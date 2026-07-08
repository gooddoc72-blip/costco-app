"""네이버 API — 쇼핑 순위 체크·검색광고 키워드·자동완성·데이터랩 추이"""
import time, json, requests, bcrypt, pybase64, math
from datetime import datetime, timedelta, timezone
from .core import get_token

_last_match_info = [""]


def get_last_match_info():
    return _last_match_info[0]


def check_keyword_rank(open_client_id, open_client_secret, keyword,
                       our_product_name='', naver_product_no='',
                       store_name='', max_pages=10):
    """
    네이버 쇼핑 검색에서 원부/단독 순위 별도 추적 (최대 1000위 탐색)
    반환: (rank_wonbu, rank_solo, error)
      - rank_wonbu: 가격비교 모음(원부) 매칭 시 순위 (None=미발견)
      - rank_solo: 단독 상품 매칭 시 순위 (None=미발견)
    productType (Naver 쇼핑 검색 API):
      "1" 일반상품, "2" 가격비교 매칭 일반상품
      "3" 가격비교 비매칭 일반상품, "4" 단독상품
      가격비교 모음(원부)은 별도 productType 사용 (보통 큰 값)
    """
    try:
        from utils import ProductMatcher
    except ImportError:
        ProductMatcher = None

    _last_match_info[0] = ""
    """
    네이버 쇼핑 검색 API로 키워드 순위 확인.
    open_client_id/secret: developers.naver.com Open API 키 (Commerce API와 다름)
    반환: (rank_price_compare, rank_total, error_msg)
      - rank_price_compare: 가격비교 상품 중 순위 (None=미발견)
      - rank_total: 전체 상품 중 순위 (None=미발견)
    """
    headers = {
        "X-Naver-Client-Id": open_client_id,
        "X-Naver-Client-Secret": open_client_secret,
    }

    import re as _re
    def _clean_trigrams(s):
        s = _re.sub(r'[^\w가-힣]', '', s.lower())
        return set(s[i:i+3] for i in range(len(s)-2)) if len(s) >= 3 else set()

    def _classify(item):
        try:
            hp = int(item.get("hprice") or 0)
        except (TypeError, ValueError):
            hp = 0
        if hp > 0:
            return "원부"
        ptype = str(item.get("productType", ""))
        if ptype == "2":
            return "가격비교"
        return "단독"

    # ── 1단계: 모든 항목을 먼저 수집 (전체 통합 순위 기준) ──
    # pos = API 응답 전체에서 몇 번째인지 (광고 제외, 분류 무관)
    # 실제 네이버 웹 순위와 동일한 기준으로 계산
    collected = []
    overall_pos = 0

    for page in range(max_pages):
        start = page * 100 + 1
        params = {"query": keyword, "display": 100, "start": start, "sort": "sim"}
        try:
            resp = requests.get(
                "https://openapi.naver.com/v1/search/shop.json",
                headers=headers, params=params, timeout=15,
            )
            if resp.status_code == 401:
                return None, None, None, "인증 실패: 네이버 Open API 키를 확인해주세요"
            if resp.status_code != 200:
                err = resp.json().get("errorMessage", resp.text[:200])
                return None, None, None, f"API 오류({resp.status_code}): {err}"

            items = resp.json().get("items", [])
            if not items:
                break

            for item in items:
                overall_pos += 1
                cls = _classify(item)
                collected.append({
                    "cls": cls,
                    "pos": overall_pos,
                    "mall_pid": str(item.get("productId", "")),
                    "title": item.get("title", "").replace("<b>", "").replace("</b>", "").strip(),
                    "mall": item.get("mallName", ""),
                    "ptype": str(item.get("productType", "")),
                    "hp": item.get("hprice") or 0,
                })
        except Exception as e:
            return None, None, None, str(e)

    if not collected:
        return None, None, None, None

    # ── 2단계: 우선순위 매칭 ──
    # 우선순위: 1) PNO_EXACT (productId)  2) STORE+NAME (best sim)  3) NAME_ONLY
    rank_wonbu = rank_compare = rank_solo = None
    debug_lines = []

    def _record_match(it, reason):
        nonlocal rank_wonbu, rank_compare, rank_solo
        debug_lines.append(
            f"{keyword}: [{it['cls']}] pos={it['pos']} ptype={it['ptype']} hp={it['hp']} mall={it['mall']} | {it['title'][:45]} | {reason}"
        )
        if it["cls"] == "원부" and rank_wonbu is None:
            rank_wonbu = it["pos"]
        elif it["cls"] == "가격비교" and rank_compare is None:
            rank_compare = it["pos"]
        elif it["cls"] == "단독" and rank_solo is None:
            rank_solo = it["pos"]

    def _get_sim(t1, t2):
        if ProductMatcher:
            return ProductMatcher.get_score(t1, t2)["total"]
        _a, _b = _clean_trigrams(t1), _clean_trigrams(t2)
        return len(_a & _b) / len(_a | _b) if (_a | _b) else 0.0

    # 우선순위 1: productId 정확 일치 (가장 신뢰도 높음, 사용자가 등록 시)
    if naver_product_no:
        for it in collected:
            if it["mall_pid"] == str(naver_product_no):
                _record_match(it, f"PNO_EXACT(productId={it['mall_pid']})")
                break

    # 우선순위 2: 스토어명 매칭 + 이름 유사도 가장 높은 것 (best sim)
    if store_name and our_product_name and rank_wonbu is None and rank_compare is None and rank_solo is None:
        best_it, best_sim = None, 0.0
        for it in collected:
            if store_name in it["mall"]:
                sim = _get_sim(it["title"], our_product_name)
                if sim > best_sim:
                    best_sim, best_it = sim, it
        if best_it and best_sim >= 0.40:
            _record_match(best_it, f"STORE+NAME(sim={best_sim:.2f}, mall={best_it['mall']}, productId={best_it['mall_pid']})")

    # 우선순위 3: 이름 유사도 (스토어명 없거나 미매칭일 때 fallback)
    if our_product_name and rank_wonbu is None and rank_compare is None and rank_solo is None:
        best_it, best_sim = None, 0.0
        for it in collected:
            sim = _get_sim(it["title"], our_product_name)
            if sim > best_sim:
                best_sim, best_it = sim, it
        
        # 오매칭(타사 상품)을 원천 차단하기 위해 임계값을 0.25에서 0.60으로 대폭 상향
        # 진짜 내 상품이 묶인 카탈로그라면 ProductMatcher 보정 덕에 0.60 이상이 나옴
        if best_it and best_sim >= 0.60:
            _record_match(best_it, f"NAME_BEST(sim={best_sim:.2f}, mall={best_it['mall']})")

    if debug_lines:
        _last_match_info[0] = " || ".join(debug_lines)
    return rank_wonbu, rank_compare, rank_solo, None



def keyword_tool(ad_api_key, ad_secret, customer_id, keyword):
    """네이버 검색광고 keywordstool 조회.
    입력 키워드의 월간 검색량(PC/모바일) + 연관검색어 목록을 반환.
    반환: (rows, error). rows=[{키워드, PC검색량, 모바일검색량, 총검색량, 경쟁도}], 총검색량 내림차순.
    검색광고 API 키는 광고주센터(searchad.naver.com) > 도구 > API 관리에서 발급 (Open API와 별개).
    """
    import hmac as _hmac, hashlib as _hashlib, base64 as _b64
    if not (ad_api_key and ad_secret and customer_id):
        return [], "검색광고 API 키(API_KEY/SECRET/고객ID) 미설정"
    _ts = str(int(time.time() * 1000))
    _method, _uri = "GET", "/keywordstool"
    _msg = f"{_ts}.{_method}.{_uri}"
    _sig = _b64.b64encode(
        _hmac.new(str(ad_secret).encode("utf-8"), _msg.encode("utf-8"), _hashlib.sha256).digest()
    ).decode("utf-8")
    _headers = {
        "X-Timestamp": _ts,
        "X-API-KEY": str(ad_api_key),
        "X-Customer": str(customer_id),
        "X-Signature": _sig,
    }
    _hint = str(keyword or "").replace(" ", "")
    if not _hint:
        return [], "키워드를 입력하세요."

    def _num(v):
        if isinstance(v, str):
            v = v.replace("<", "").replace(",", "").strip()
        try:
            return int(v)
        except Exception:
            return 0

    try:
        r = requests.get(
            "https://api.searchad.naver.com/keywordstool",
            headers=_headers,
            params={"hintKeywords": _hint, "showDetail": "1"},
            timeout=15,
        )
        if r.status_code != 200:
            try:
                _em = r.json()
                _msg2 = _em.get("title") or _em.get("message") or r.text[:200]
            except Exception:
                _msg2 = r.text[:200]
            return [], f"[{r.status_code}] {_msg2}"
        _list = r.json().get("keywordList", []) or []
        out = []
        for it in _list:
            _pc = _num(it.get("monthlyPcQcCnt", 0))
            _mo = _num(it.get("monthlyMobileQcCnt", 0))
            out.append({
                "키워드": it.get("relKeyword", ""),
                "PC검색량": _pc,
                "모바일검색량": _mo,
                "총검색량": _pc + _mo,
                "경쟁도": it.get("compIdx", "") or "",
            })
        out.sort(key=lambda x: x["총검색량"], reverse=True)
        return out, None
    except Exception as e:
        return [], str(e)



def naver_autocomplete(keyword):
    """네이버 자동완성 키워드 목록 (ac.search.naver.com). 실패 시 []."""
    try:
        r = requests.get(
            "https://ac.search.naver.com/nx/ac",
            params={"q": keyword, "st": 111, "r_format": "json", "frm": "nv", "ans": 2},
            timeout=10,
        )
        j = r.json()
        seen, out = set(), []
        for grp in (j.get("items") or []):
            for item in (grp or []):
                if item and item[0] and item[0] not in seen:
                    seen.add(item[0]); out.append(item[0])
        return out
    except Exception:
        return []



def _norm_kw(s):
    return str(s or "").replace(" ", "").upper()



def keyword_volumes(ad_api_key, ad_secret, customer_id, keywords):
    """키워드 리스트의 월간 검색량 조회 (keywordstool 5개씩 배치). {정규화키: (pc, mo, comp)}"""
    vol = {}
    kws = [k for k in keywords if k]
    for i in range(0, len(kws), 5):
        rows, err = keyword_tool(ad_api_key, ad_secret, customer_id, ",".join(kws[i:i + 5]))
        if err or not rows:
            continue
        for r in rows:
            vol[_norm_kw(r["키워드"])] = (r["PC검색량"], r["모바일검색량"], r.get("경쟁도", ""))
    return vol



def keyword_research(ad_api_key, ad_secret, customer_id, keyword):
    """키워드 통합 리서치. 반환 rows에 '구분'(연관검색어/함께찾는/자동완성) 포함.
    - 연관검색어: 검색어 자신(현재)
    - 함께찾는:   keywordstool 연관 키워드(월간 검색량 有)
    - 자동완성:   네이버 자동완성(검색량은 keywordstool로 보완)
    """
    rel_rows, err = keyword_tool(ad_api_key, ad_secret, customer_id, keyword)
    if err:
        return [], err
    _qn = _norm_kw(keyword)
    out, seen = [], set()
    for r in (rel_rows or []):
        _n = _norm_kw(r["키워드"])
        if _n in seen:
            continue
        seen.add(_n)
        out.append({**r, "구분": ("연관검색어" if _n == _qn else "함께찾는")})
    _ac_new = [k for k in naver_autocomplete(keyword) if _norm_kw(k) not in seen]
    if _ac_new:
        _vol = keyword_volumes(ad_api_key, ad_secret, customer_id, _ac_new)
        for k in _ac_new:
            _n = _norm_kw(k)
            if _n in seen:
                continue
            seen.add(_n)
            _pc, _mo, _cp = _vol.get(_n, (0, 0, ""))
            out.append({"키워드": k, "PC검색량": _pc, "모바일검색량": _mo,
                        "총검색량": _pc + _mo, "경쟁도": _cp, "구분": "자동완성"})
    # 현재 검색어 최상단 → 이후 총검색량 내림차순
    out.sort(key=lambda x: (0 if x["구분"] == "연관검색어" else 1, -x["총검색량"]))
    return out, None



def datalab_search_trend(client_id, client_secret, keyword, pc_now=0, mo_now=0):
    """네이버 데이터랩 검색어트렌드 → 최근 12개월 월별 검색량 추이(PC/모바일/합계).
    데이터랩은 '상대비율(0~100)'만 주므로, keywordstool 현재월 PC/모바일 절대치로 앵커링해
    추정 절대값으로 환산한다. Open API 키(developers.naver.com)는 순위체크용과 동일.
    반환: (dict{'months','total','pc','mo'}, error).
    """
    import json as _json
    from datetime import date as _date, timedelta as _td
    if not (client_id and client_secret):
        return None, "네이버 Open API 키 미설정"
    _kw = str(keyword or "").strip()
    if not _kw:
        return None, "키워드를 입력하세요."
    # 완결월(지난달 말일) 기준 최근 12개월
    _end = _date.today().replace(day=1) - _td(days=1)
    _sy, _sm = _end.year, _end.month - 11
    while _sm <= 0:
        _sm += 12; _sy -= 1
    _start = _date(_sy, _sm, 1)
    _hdr = {
        "X-Naver-Client-Id": str(client_id),
        "X-Naver-Client-Secret": str(client_secret),
        "Content-Type": "application/json",
    }

    def _fetch(device):
        body = {
            "startDate": _start.strftime("%Y-%m-%d"),
            "endDate": _end.strftime("%Y-%m-%d"),
            "timeUnit": "month",
            "keywordGroups": [{"groupName": _kw, "keywords": [_kw]}],
        }
        if device:
            body["device"] = device
        try:
            r = requests.post("https://openapi.naver.com/v1/datalab/search",
                              headers=_hdr, data=_json.dumps(body), timeout=15)
            if r.status_code != 200:
                try:
                    _m2 = r.json().get("errorMessage") or r.text[:200]
                except Exception:
                    _m2 = r.text[:200]
                return None, f"[{r.status_code}] {_m2}"
            _res = r.json().get("results") or []
            _data = _res[0].get("data", []) if _res else []
            return {d["period"][:7]: float(d.get("ratio") or 0) for d in _data}, None
        except Exception as e:
            return None, str(e)

    _pc_r, _e1 = _fetch("pc")
    if _e1:
        return None, _e1
    _mo_r, _e2 = _fetch("mo")
    if _e2:
        return None, _e2
    _months = sorted(set(_pc_r) | set(_mo_r))
    if not _months:
        return None, "데이터랩 결과 없음"
    # 앵커: 마지막(최근) 월 비율 → 현재월 절대치(pc_now/mo_now)
    _pc_last = _pc_r.get(_months[-1], 0) or 0
    _mo_last = _mo_r.get(_months[-1], 0) or 0
    _pc_scale = (float(pc_now) / _pc_last) if (_pc_last > 0 and pc_now > 0) else 0
    _mo_scale = (float(mo_now) / _mo_last) if (_mo_last > 0 and mo_now > 0) else 0
    _anchored = bool(_pc_scale or _mo_scale)
    out = {"months": [], "total": [], "pc": [], "mo": [], "anchored": _anchored}
    for mth in _months:
        _pr = _pc_r.get(mth, 0) or 0
        _mr = _mo_r.get(mth, 0) or 0
        if _anchored:
            _pcv = int(round(_pr * _pc_scale))
            _mov = int(round(_mr * _mo_scale))
        else:
            # 앵커 불가(현재월 절대치 없음) → 상대지수 그대로 표시
            _pcv, _mov = int(round(_pr)), int(round(_mr))
        out["months"].append(mth[2:])   # 'YY-MM'
        out["pc"].append(_pcv)
        out["mo"].append(_mov)
        out["total"].append(_pcv + _mov)
    return out, None
