# -*- coding: utf-8 -*-
"""네이버 쇼핑 검색 수집기 (로그인 세션 + 프록시 기반).

배경:
  - 2026-06 네이버가 오픈API를 NAVER API HUB로 이관하며 쇼핑 검색 API(shop.json) 폐지(404 SE05)
  - 웹 쇼핑 검색도 비로그인 접근 차단(로그인 리다이렉트)
  - 내부 API 직접 호출(requests/APIRequestContext)은 418로 차단 → 페이지 렌더링 필요
  - 짧은 시간 많은 요청 / VPN IP는 "접속이 일시적으로 제한" 에러 페이지로 차단

따라서 로그인 세션 쿠키 + (선택)프록시로 검색 페이지를 실제 렌더링하고,
페이지가 호출하는 내부 API 응답을 가로채 상품 목록을 얻는다.
반환 형태는 기존 Open API 수집 결과와 동일해서 매칭 로직은 그대로 쓴다.
"""
import os
import re
import time
import random
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_PATH = os.path.join(BASE_DIR, "data", "naver_session.json")
PROFILE_DIR = os.path.join(BASE_DIR, "data", "naver_browser_profile")
SEARCH_URL = "https://search.shopping.naver.com/search/all"
PAGE_SIZE = 40                   # 내부 API 1페이지 최대치
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

ERR_NO_SESSION = ("네이버 로그인 세션이 없습니다. naver_session_setup.py 로 세션을 만들어 "
                  "data/naver_session.json 에 넣어주세요.")
ERR_EXPIRED = ("네이버 로그인 세션이 만료됐습니다. naver_session_setup.py 를 다시 실행해 "
               "세션을 갱신해주세요.")
# ⚠️ 이 차단은 IP가 아니라 **로그인 계정**에 걸리는 경우가 많다.
#   실측(2026-08-30): 같은 IP·같은 브라우저에서 세션 쿠키를 실으면 차단 페이지,
#   빼면 정상(로그인 리다이렉트)이었다. 서버 IP와 가정용 IP가 동시에 막힌 것도
#   계정 단위 제한임을 뒷받침한다. 예전 문구가 "IP 제한"이라 단정해 프록시부터
#   찾게 만들었다 — 원인 순서대로 안내한다.
#   화면은 st.code()로 그려 마크다운이 먹지 않고, 키워드마다 반복 출력되므로
#   평문으로 짧게 유지한다. 자세한 조치 순서는 설정 화면 안내에 둔다.
#   원인을 단정하지 않는다. 실측(2026-08-30)으로 배제된 것: IP(서버·가정용 동시 차단),
#   헤드리스 여부, AutomationControlled 플래그, URL 파라미터, 저장 순서,
#   storage_state vs 영구프로필. 대화형 로그인 직후의 첫 요청만 통과했다.
ERR_BLOCKED = ("네이버가 쇼핑 검색 접근을 제한했습니다(자동 수집 차단). "
               "naver_session_setup.py 로 세션을 다시 발급해 보세요.")

_BLOCK_MARKS = ("접속이 일시적으로 제한", "일시적으로 제한되었습니다", "content_error")


def session_status(session_path=None):
    """세션 파일 상태. 반환: (사용가능여부, 설명)"""
    p = session_path or SESSION_PATH
    if not os.path.exists(p):
        return False, "세션 파일 없음"
    try:
        import json
        with open(p, "r", encoding="utf-8") as f:
            cookies = json.load(f).get("cookies", [])
        names = {c.get("name") for c in cookies}
        age_d = (time.time() - os.path.getmtime(p)) / 86400.0
        if not ({"NID_AUT", "NID_SES"} <= names):
            return False, "로그인 쿠키 없음 (재로그인 필요)"
        return True, "쿠키 %d개 · %.0f일 전 저장" % (len(cookies), age_d)
    except Exception as e:
        return False, "세션 파일 손상: %s" % e


def parse_proxy(proxy_url):
    """'http://user:pass@host:port' → playwright proxy dict. 빈 값이면 None."""
    s = str(proxy_url or "").strip()
    if not s:
        return None
    if "://" not in s:
        s = "http://" + s
    u = urllib.parse.urlparse(s)
    if not u.hostname:
        return None
    out = {"server": "%s://%s%s" % (u.scheme, u.hostname, ":%d" % u.port if u.port else "")}
    if u.username:
        out["username"] = urllib.parse.unquote(u.username)
    if u.password:
        out["password"] = urllib.parse.unquote(u.password)
    return out


def _classify(p):
    """원부(가격비교 모음) / 가격비교 / 단독 — 기존 Open API 분류와 같은 의미."""
    try:
        hp = int(p.get("hprice") or 0)
    except (TypeError, ValueError):
        hp = 0
    if hp > 0:
        return "원부"
    if str(p.get("productType", "")) == "2":
        return "가격비교"
    return "단독"


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def _extract_products(payload):
    """내부 API 응답(JSON dict)에서 상품 배열 추출 — 응답 구조 변화에 대비해 여러 경로 시도."""
    if not isinstance(payload, dict):
        return []
    for path in (("shoppingResult", "products"), ("products",), ("data", "products")):
        cur = payload
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
            if cur is None:
                break
        if isinstance(cur, list) and cur:
            return cur
    return []


def _to_item(pr, pos):
    return {
        "cls": _classify(pr),
        "pos": pos,
        "mall_pid": str(pr.get("id") or pr.get("productId") or pr.get("nvMid") or ""),
        "title": _strip_tags(pr.get("productTitle") or pr.get("productName") or pr.get("title")),
        "mall": pr.get("mallName") or pr.get("mall") or "",
        "ptype": str(pr.get("productType", "")),
        "hp": pr.get("hprice") or 0,
    }


def fetch_shop_items(keyword, max_items=400, session_path=None, sort="rel",
                     proxy=None, min_delay=2.5, max_delay=5.0, headless=True, log=None):
    """쇼핑 검색 결과 수집. 반환: (items, error)

    item = {cls, pos, mall_pid, title, mall, ptype, hp}  (pos = 광고 제외 노출 순위)
    proxy: 'http://user:pass@host:port' 또는 None
    """
    p = session_path or SESSION_PATH
    if not os.path.exists(p):
        return None, ERR_NO_SESSION
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright 미설치 (pip install playwright && playwright install chromium)"

    def _log(m):
        if log:
            try:
                log(m)
            except Exception:
                pass

    q = urllib.parse.quote(str(keyword))
    pages = max(1, (int(max_items) + PAGE_SIZE - 1) // PAGE_SIZE)
    proxy_cfg = parse_proxy(proxy)
    items, pos = [], 0

    try:
        with sync_playwright() as pw:
            # ⭐ 영구 프로필 우선.
            #   storage_state(쿠키+localStorage)로 복원한 컨텍스트는 쇼핑 검색에서
            #   항상 차단 페이지를 받았다. 같은 계정·같은 IP·같은 옵션인데 로그인
            #   직후의 라이브 컨텍스트만 통과했다 — storage_state가 sessionStorage를
            #   저장하지 않기 때문으로 보인다. 실제 사용자 프로필 디렉터리를 그대로
            #   재사용하면 그 상태가 통째로 남는다.
            _args = ["--no-sandbox", "--disable-dev-shm-usage",
                     "--disable-blink-features=AutomationControlled"]
            _ctx_kw = dict(locale="ko-KR", user_agent=UA,
                           viewport={"width": 1400, "height": 900},
                           extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"})
            _use_profile = os.path.isdir(PROFILE_DIR)
            br = None
            if _use_profile:
                ctx = pw.chromium.launch_persistent_context(
                    PROFILE_DIR, headless=headless, proxy=proxy_cfg,
                    args=_args, **_ctx_kw)
                # 프로필은 브라우저를 닫을 때 **세션 쿠키(NID_AUT/NID_SES)를 버린다**
                #   → 프로필만으로는 로그아웃 상태가 된다. 저장해 둔 쿠키를 주입해
                #   로그인 상태를 되살린다. 프로필은 sessionStorage 등 나머지 상태를,
                #   쿠키 파일은 로그인 자격을 담당하는 조합이다.
                try:
                    import json as _json
                    with open(p, encoding="utf-8") as _f:
                        _ck = (_json.load(_f) or {}).get("cookies") or []
                    if _ck:
                        ctx.add_cookies(_ck)
                except Exception:
                    pass
            else:
                br = pw.chromium.launch(headless=headless, proxy=proxy_cfg, args=_args)
            try:
                if not _use_profile:
                    ctx = br.new_context(storage_state=p, **_ctx_kw)
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
                page = ctx.new_page()

                captured = []
                page.on("response", lambda r: captured.append(r)
                        if ("/api/search/" in r.url and r.request.resource_type in ("xhr", "fetch"))
                        else None)

                for idx in range(1, pages + 1):
                    url = "%s?query=%s&pagingIndex=%d&pagingSize=%d&sort=%s&productSet=total" % (
                        SEARCH_URL, q, idx, PAGE_SIZE, sort)
                    captured.clear()
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    except Exception as e:
                        if "nidlogin" in str(e):
                            return None, ERR_EXPIRED
                        return None, "페이지 로드 실패: %s" % str(e)[:120]
                    page.wait_for_timeout(2500)

                    if "nidlogin" in page.url:
                        return None, ERR_EXPIRED
                    html = page.content()
                    if len(html) < 20000 and any(m in html for m in _BLOCK_MARKS):
                        return None, ERR_BLOCKED

                    # 1) 페이지가 호출한 내부 API 응답 가로채기
                    prods = []
                    for r in captured:
                        try:
                            prods = _extract_products(r.json())
                        except Exception:
                            prods = []
                        if prods:
                            break
                    # 2) 폴백: SSR 데이터(__NEXT_DATA__ 등) 안의 상품 배열
                    if not prods:
                        m = re.search(r'id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>', html, re.S)
                        if m:
                            try:
                                import json as _json
                                prods = _find_products_deep(_json.loads(m.group(1)))
                            except Exception:
                                prods = []
                    if not prods:
                        if idx == 1:
                            return None, ("검색 결과를 읽지 못했습니다(페이지 구조 변경 가능). "
                                          "HTML %d바이트" % len(html))
                        break

                    for pr in prods:
                        if pr.get("adId"):          # 광고 제외 — 기존 API 기준과 동일
                            continue
                        pos += 1
                        items.append(_to_item(pr, pos))
                        if len(items) >= max_items:
                            break
                    _log("  %s %d페이지 누적 %d건" % (keyword, idx, len(items)))
                    if len(items) >= max_items:
                        break
                    if idx < pages:
                        time.sleep(random.uniform(min_delay, max_delay))
            finally:
                # 영구 프로필이면 br이 없다(launch_persistent_context가 컨텍스트를 직접 반환)
                if br is not None:
                    br.close()
                else:
                    try:
                        ctx.close()
                    except Exception:
                        pass
    except Exception as e:
        return None, str(e)[:200]

    return items, None


def _find_products_deep(obj, depth=0):
    """SSR JSON 안에서 상품 배열로 보이는 리스트를 찾는다."""
    if depth > 8:
        return []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "products" and isinstance(v, list) and v and isinstance(v[0], dict):
                return v
            found = _find_products_deep(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and (
                "productTitle" in obj[0] or "productName" in obj[0]):
            return obj
        for v in obj[:20]:
            found = _find_products_deep(v, depth + 1)
            if found:
                return found
    return []
