"""네이버 API — 쇼핑 순위 체크·검색광고 키워드·자동완성·데이터랩 추이"""
import time, json, requests, bcrypt, pybase64, math
from datetime import datetime, timedelta, timezone
from .core import get_token

_last_match_info = [""]

# ── 데이터랩 호출 경로 (2026-06 네이버 API 플랫폼 이관) ──────────
# 개발자센터(openapi.naver.com) 키와 NAVER API HUB(NCP) 키는 도메인·헤더가 서로 다르다.
# 어느 쪽 키가 등록돼 있든 동작하도록 첫 호출에서 판별 후 client_id별로 캐시한다.
_HUB_BASE = "https://naverapihub.apigw.ntruss.com"
_LEGACY_BASE = "https://openapi.naver.com"
_DATALAB_PATHS = {           # 종류: (API HUB 경로, 개발자센터 경로)
    "search":      ("/search-trend/v1/search",             "/v1/datalab/search"),
    "shop_kw":     ("/shopping/v1/category/keywords",       "/v1/datalab/shopping/category/keywords"),
    "shop_gender": ("/shopping/v1/category/keyword/gender", "/v1/datalab/shopping/category/keyword/gender"),
    "shop_age":    ("/shopping/v1/category/keyword/age",    "/v1/datalab/shopping/category/keyword/age"),
}
_api_platform = {}           # client_id -> "hub" | "legacy"


def _api_err_msg(resp):
    """네이버 API 오류 메시지 추출 — 3가지 응답 포맷 대응.
    API HUB 게이트웨이 {"error":{"message":..}} / 검색 API {"errorMessage":..} /
    데이터랩 {"errMsg":..}. 파싱 실패 시 본문 앞부분."""
    try:
        j = resp.json()
    except Exception:
        return (resp.text or "")[:200]
    if isinstance(j.get("error"), dict):
        _e = j["error"]
        return _e.get("message") or _e.get("details") or str(_e)[:200]
    return j.get("errorMessage") or j.get("errMsg") or (resp.text or "")[:200]


def _datalab_post(client_id, client_secret, kind, body, timeout=15):
    """데이터랩 계열 POST 공통 호출. 반환: (json_dict, error_msg).
    API HUB → 개발자센터 순으로 시도하고, 성공한 플랫폼을 키별로 기억한다."""
    _hub_path, _leg_path = _DATALAB_PATHS[kind]
    _cid, _csec = str(client_id), str(client_secret)
    _order = ["hub", "legacy"]
    _known = _api_platform.get(_cid)
    if _known:
        _order = [_known]                      # 판별 완료 → 한 번만 호출
    _err = None
    for _plat in _order:
        if _plat == "hub":
            _url = _HUB_BASE + _hub_path
            _hdr = {"X-NCP-APIGW-API-KEY-ID": _cid, "X-NCP-APIGW-API-KEY": _csec}
        else:
            _url = _LEGACY_BASE + _leg_path
            _hdr = {"X-Naver-Client-Id": _cid, "X-Naver-Client-Secret": _csec}
        _hdr["Content-Type"] = "application/json"
        try:
            r = requests.post(_url, headers=_hdr, data=json.dumps(body), timeout=timeout)
        except Exception as e:
            _err = str(e)
            continue
        if r.status_code == 200:
            _api_platform[_cid] = _plat        # 판별 성공 → 캐시
            try:
                return r.json(), None
            except Exception as e:
                return None, f"응답 파싱 실패: {e}"
        _err = f"[{r.status_code}] {_api_err_msg(r)}"
        if r.status_code not in (401, 403, 404):
            break                              # 인증/경로 문제가 아니면 폴백 무의미
    return None, _err


def get_last_match_info():
    return _last_match_info[0]


def dedup_product_name(name):
    """상품명에서 같은 키워드(공백 구분 토큰)가 2회 이상 반복되지 않도록 중복 제거.

    첫 등장만 남기고 이후 동일 토큰은 삭제(순서=관련도 보존). 비교는 소문자·양끝
    문장부호 정규화(예: 'FiJi'=='fiji', '요거트,'=='요거트'). 표시 토큰은 원문 유지.
    네이버 상품명 어뷰징 필터(동일 키워드 반복) 회피 목적 — 등록/생성 공용 하드가드."""
    if not name:
        return name
    seen, out = set(), []
    for tok in str(name).split():
        norm = tok.lower().strip(".,·/|()[]{}\"'")
        if norm and norm in seen:
            continue
        if norm:
            seen.add(norm)
        out.append(tok)
    return " ".join(out)


def check_keyword_rank(open_client_id, open_client_secret, keyword,
                       our_product_name='', naver_product_no='',
                       store_name='', max_pages=10, proxy=None):
    """
    네이버 쇼핑 검색에서 원부/단독 순위 별도 추적
    반환: (rank_wonbu, rank_compare, rank_solo, error)
      - rank_wonbu: 가격비교 모음(원부) 매칭 시 순위 (None=미발견)
      - rank_compare: 가격비교 상품 매칭 시 순위 (None=미발견)
      - rank_solo: 단독 상품 매칭 시 순위 (None=미발견)

    수집 경로: 네이버가 쇼핑 검색 API를 폐지(404 SE05)하고 웹 검색도 비로그인을
    차단해, 로그인 세션 쿠키로 내부 검색 API를 호출한다(naver_shop_crawler).
    open_client_id/secret은 더 이상 쓰지 않지만 호출부 호환을 위해 인자는 유지한다.
    """
    # ── 1단계: 로그인 세션으로 검색결과 수집 (전체 통합 순위 기준) ──
    # pos = 광고 제외한 실제 노출 순위. max_pages는 100건 단위(기존 호출부 호환)
    try:
        import naver_shop_crawler
    except ImportError as e:
        return None, None, None, f"수집 모듈 로드 실패: {e}"

    collected, _cerr = naver_shop_crawler.fetch_shop_items(
        keyword, max_items=max(40, int(max_pages) * 100), proxy=proxy)
    if _cerr:
        return None, None, None, _cerr
    if not collected:
        _last_match_info[0] = ""
        return None, None, None, None

    _w, _c, _s = match_rank_in_items(collected, keyword, our_product_name,
                                     naver_product_no, store_name)
    return _w, _c, _s, None


def match_rank_in_items(collected, keyword, our_product_name='', naver_product_no='',
                        store_name=''):
    """수집된 검색결과(collected)에서 우리 상품의 순위를 찾는다.

    수집과 매칭을 분리해, 키워드 1회 수집분을 여러 계정·여러 상품 매칭에 재사용한다.
    반환: (rank_wonbu, rank_compare, rank_solo)
    """
    try:
        from utils import ProductMatcher
    except ImportError:
        ProductMatcher = None

    _last_match_info[0] = ""

    import re as _re
    def _clean_trigrams(s):
        s = _re.sub(r'[^\w가-힣]', '', s.lower())
        return set(s[i:i+3] for i in range(len(s)-2)) if len(s) >= 3 else set()

    # ── 우선순위 매칭 ──
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
    return rank_wonbu, rank_compare, rank_solo



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



def _comp_rank(r):
    """경쟁도 → 정렬용 숫자 (낮을수록 앞). 알 수 없으면 중간."""
    return {"낮음": 0, "중간": 1, "높음": 2}.get(str(r.get("경쟁도", "")), 1)


def order_seo_keywords(rows, front_max=200, n_front=3):
    """검색량 rows → 상품명용 키워드 배치 (순수 함수, API 불필요 = 단위테스트 가능).

    롱테일 전략: 저검색(총검색량 ≤ front_max) 키워드를 '앞단'에 둔다.
    신규 상품은 대표어로 상위노출이 어려우니, 경쟁 낮은 저검색 키워드를 앞에 배치해
    롱테일 검색 1페이지를 노린다. 대표어(최고검색량)는 뒤에 보조로 붙인다.
    n_front = 앞단에 넣을 연관검색 키워드 수 (기본 3개).

    각 row에 band 라벨을 달아 반환:
      'front' = 저검색(≤front_max) 후보  ·  'rep' = 대표어  ·  'mid' = 그 외
    반환: {'front': [kw...], 'rep': kw|None, 'ordered_kw': [앞단..., 대표어],
           'candidates': [{키워드,총검색량,경쟁도,band}...]}
    """
    rows = [r for r in (rows or []) if str(r.get("키워드", "")).strip()]
    if not rows:
        return {"front": [], "rep": None, "ordered_kw": [], "candidates": []}
    by_vol = sorted(rows, key=lambda r: int(r.get("총검색량", 0) or 0), reverse=True)
    rep = by_vol[0]                                   # 대표어 = 최고검색량
    rep_kw = str(rep.get("키워드", "")).strip()

    # 저검색 후보: 1 ≤ 총검색량 ≤ front_max, 경쟁 낮은 순 → 같은 경쟁이면 검색량 높은 순
    front_pool = [r for r in by_vol
                  if 1 <= int(r.get("총검색량", 0) or 0) <= front_max
                  and str(r.get("키워드", "")).strip() != rep_kw]
    front_pool.sort(key=lambda r: (_comp_rank(r), -int(r.get("총검색량", 0) or 0)))
    front = [str(r.get("키워드", "")).strip() for r in front_pool[:max(0, n_front)]]

    ordered = []
    for k in front + [rep_kw]:
        n = k.lower().replace(" ", "")
        if k and n not in [x.lower().replace(" ", "") for x in ordered]:
            ordered.append(k)

    front_set = {r["키워드"] for r in front_pool[:max(0, n_front)]}
    candidates = []
    for r in by_vol:
        kw = str(r.get("키워드", "")).strip()
        band = "rep" if kw == rep_kw else ("front" if kw in front_set else "mid")
        candidates.append({"키워드": kw, "총검색량": int(r.get("총검색량", 0) or 0),
                           "경쟁도": r.get("경쟁도", ""), "band": band})
    return {"front": front, "rep": rep_kw, "ordered_kw": ordered, "candidates": candidates}


def keyword_seo_name(ad_api_key, ad_secret, customer_id, seed, ai_key=None,
                     category="", front_max=200, n_front=3, manual_kw=None,
                     gemini_key=None):
    """검색량 분석 기반 SEO 상품명 + 후보 키워드. (메인 네이버 등록용)

    저검색(≤front_max) 키워드를 상품명 '앞단'에 배치한다.
    manual_kw가 주어지면(사용자 수동선택) 그 순서 그대로 앞단에 쓴다.
    ai_key 있으면 AI가 자연스러운 상품명으로 조합(앞단 순서 유지), 없으면 이어붙임.

    반환: (result, err)
      result = {'name', 'front'[], 'rep', 'candidates'[{키워드,총검색량,경쟁도,band}]}
    """
    _seed = str(seed or "").strip()
    rows, err = keyword_tool(ad_api_key, ad_secret, customer_id, _seed)
    if err or not rows:
        return {"name": _seed, "front": [], "rep": None, "candidates": []}, (err or "연관키워드 없음")

    plan = order_seo_keywords(rows, front_max=front_max, n_front=n_front)
    # 수동 선택이 있으면 그 키워드를 앞단으로 (순서 보존)
    if manual_kw:
        front = [str(k).strip() for k in manual_kw if str(k).strip()]
        ordered = []
        for k in front + ([plan["rep"]] if plan["rep"] else []):
            n = k.lower().replace(" ", "")
            if k and n not in [x.lower().replace(" ", "") for x in ordered]:
                ordered.append(k)
    else:
        front, ordered = plan["front"], plan["ordered_kw"]

    result = {"name": _seed, "front": front, "rep": plan["rep"],
              "candidates": plan["candidates"]}
    if not ordered:
        return result, None

    try:
        import ai_service
        _gk = ai_service.resolve_gemini_key(gemini_key)
    except Exception:
        _gk = ''
    if ai_key or _gk:
        try:
            import ai_service
            _sys = ("너는 네이버 스마트스토어 SEO 상품명 작성 전문가다. 주어진 키워드를 "
                    "'제시된 순서대로' 자연스럽게 포함하는 한국어 상품명을 한 줄로 만든다. "
                    "특히 앞쪽 키워드가 상품명 앞부분에 오도록 배치한다. "
                    "**주어진 키워드는 하나도 빠뜨리지 말 것** (연관검색 키워드 3개 + 대표어). "
                    "중복·과장·특수문자·이모지 금지, 최대 50자. 상품명만 출력.")
            _msg = (f"원본 상품명: {_seed}\n카테고리: {category or '미상'}\n"
                    f"앞단부터 순서대로 포함할 키워드: {', '.join(ordered)}\n"
                    f"상품명 한 줄만 출력.")
            # Gemini 우선 → Claude 폴백 (gemini_key=None → 설정에서 자동 해석)
            _txt, _e, _ = ai_service.ai_complete(
                _sys, _msg, gemini_key=_gk, anthropic_key=ai_key or '', max_tokens=120,
                claude_model=getattr(ai_service, "VISION_MODEL", None))
            if _txt:
                _name = " ".join(str(_txt).split()).strip().strip('"').strip()
                if len(_name) >= 4:
                    result["name"] = dedup_product_name(_name)[:100]
                    return result, None
        except Exception as _ex:
            err = str(_ex)
    # AI 실패/미사용 → 앞단 키워드 + 원본명 이어붙임 (중복 키워드 제거)
    result["name"] = dedup_product_name((" ".join(ordered) + " " + _seed).strip())[:100]
    return result, err


def keyword_optimized_name(ad_api_key, ad_secret, customer_id, seed,
                           ai_key=None, category="", low=100, high=300,
                           gemini_key=None):
    """연관키워드 조회수 기반 상품명 생성.
    저경쟁(총검색량 low~high, 경쟁도 낮은 순) 1~2개 + 대표어(최고 검색량) 1개를 조합.
    ai_key 있으면 AI가 자연스러운 상품명으로 조합, 없으면 단순 이어붙임.
    반환: (name, info).  info = {rep, low[], err}.  키/조회 실패 시 (seed, info).
    """
    _seed = str(seed or "").strip()
    info = {"rep": None, "low": [], "err": None}
    rows, err = keyword_tool(ad_api_key, ad_secret, customer_id, _seed)
    if err or not rows:
        info["err"] = err or "연관키워드 없음"
        return _seed, info
    rep = rows[0]                                    # keyword_tool = 총검색량 내림차순 → 대표어
    band = [r for r in rows if low <= int(r.get("총검색량", 0)) <= high]

    def _comp_rank(r):
        return {"낮음": 0, "중간": 1, "높음": 2}.get(str(r.get("경쟁도", "")), 1)

    band.sort(key=lambda r: (_comp_rank(r), -int(r.get("총검색량", 0))))
    lows = band[:2]
    info["rep"] = rep.get("키워드"); info["low"] = [r.get("키워드") for r in lows]

    kws = []
    for k in [rep.get("키워드")] + [r.get("키워드") for r in lows]:
        k = str(k or "").strip()
        _norm = k.lower().replace(" ", "")
        if k and _norm not in [x.lower().replace(" ", "") for x in kws]:
            kws.append(k)
    if not kws:
        return _seed, info

    try:
        import ai_service
        _gk = ai_service.resolve_gemini_key(gemini_key)
    except Exception:
        _gk = ''
    if ai_key or _gk:
        try:
            import ai_service
            _sys = ("너는 네이버 스마트스토어 SEO 상품명 작성 전문가다. 주어진 핵심 키워드를 "
                    "모두 자연스럽게 포함하는 한국어 상품명을 한 줄로 만든다. "
                    "중복·과장·특수문자·이모지 금지, 최대 40자. 상품명만 출력.")
            _msg = (f"원본 상품명: {_seed}\n카테고리: {category or '미상'}\n"
                    f"반드시 포함할 키워드: {', '.join(kws)}\n상품명 한 줄만 출력.")
            _txt, _e, _ = ai_service.ai_complete(
                _sys, _msg, gemini_key=_gk, anthropic_key=ai_key or '', max_tokens=120,
                claude_model=getattr(ai_service, "VISION_MODEL", None))
            if _e:
                info["err"] = _e
            if _txt:
                _name = " ".join(str(_txt).split()).strip().strip('"').strip()
                if len(_name) >= 4:
                    return dedup_product_name(_name)[:100], info
        except Exception as _ex:
            info["err"] = str(_ex)
    return dedup_product_name(" ".join(kws))[:100] or _seed, info


def naver_shopping_search(open_client_id, open_client_secret, query, display=10):
    """네이버 쇼핑 검색 (카테고리·시세 파악용). 반환: (items, err).
    item = {title, category1~4, lprice(최저가)}
    """
    import re as _re
    if not (open_client_id and open_client_secret):
        return None, "네이버 Open API 키 미설정"
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/shop.json",
            headers={"X-Naver-Client-Id": str(open_client_id),
                     "X-Naver-Client-Secret": str(open_client_secret)},
            params={"query": str(query), "display": min(20, max(1, display)), "sort": "sim"},
            timeout=10,
        )
        if r.status_code != 200:
            return None, f"[{r.status_code}] {r.text[:150]}"
        items = []
        for it in r.json().get("items", []) or []:
            items.append({
                "title": _re.sub(r"<[^>]+>", "", it.get("title", "") or ""),
                "category1": it.get("category1", ""), "category2": it.get("category2", ""),
                "category3": it.get("category3", ""), "category4": it.get("category4", ""),
                "lprice": int(it.get("lprice", 0) or 0),
            })
        return items, None
    except Exception as e:
        return None, str(e)


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

    def _fetch(device):
        body = {
            "startDate": _start.strftime("%Y-%m-%d"),
            "endDate": _end.strftime("%Y-%m-%d"),
            "timeUnit": "month",
            "keywordGroups": [{"groupName": _kw, "keywords": [_kw]}],
        }
        if device:
            body["device"] = device
        _j, _err = _datalab_post(client_id, client_secret, "search", body)
        if _err:
            return None, _err
        _res = _j.get("results") or []
        _data = _res[0].get("data", []) if _res else []
        return {d["period"][:7]: float(d.get("ratio") or 0) for d in _data}, None

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


# ── 데이터랩 쇼핑인사이트 — 키워드 성별/연령 비율 ─────────────
# 쇼핑 최상위 카테고리 ID (datalab shopping insight 필수 파라미터)
_SHOP_CAT_IDS = {
    "패션의류": "50000000", "패션잡화": "50000001", "화장품/미용": "50000002",
    "디지털/가전": "50000003", "가구/인테리어": "50000004", "출산/육아": "50000005",
    "식품": "50000006", "스포츠/레저": "50000007", "생활/건강": "50000008",
    "여가/생활편의": "50000009", "면세점": "50000010", "도서": "50005542",
}


_cat_cache = {}      # keyword -> (cat_id, cat_name)


def _detect_shop_category(client_id, client_secret, keyword):
    """키워드가 속한 쇼핑 최상위 카테고리 탐지.

    기존에는 쇼핑검색 API(shop.json) 결과의 category1 다수결을 썼으나 해당 API가
    폐지(404 SE05)돼, 데이터랩 쇼핑인사이트에 12개 카테고리를 순차 조회해
    데이터가 잡히는 카테고리(데이터 포인트 수 → 비율합 순)로 판정한다."""
    _kw = str(keyword or "").strip()
    if not _kw:
        return None, None
    if _kw in _cat_cache:
        return _cat_cache[_kw]
    from datetime import date as _d, timedelta as _t
    _end = _d.today().replace(day=1) - _t(days=1)
    _sy, _sm = _end.year, _end.month - 2
    while _sm <= 0:
        _sm += 12; _sy -= 1
    _base = {"startDate": _d(_sy, _sm, 1).strftime("%Y-%m-%d"),
             "endDate": _end.strftime("%Y-%m-%d"), "timeUnit": "month"}
    _best = None                          # (점수 pts, 비율합, cat_id, cat_name)
    for _name, _cid2 in _SHOP_CAT_IDS.items():
        _body = dict(_base, category=_cid2, keyword=[{"name": _kw, "param": [_kw]}])
        _j, _err = _datalab_post(client_id, client_secret, "shop_kw", _body, timeout=10)
        if _err or not _j:
            continue
        _res = _j.get("results") or []
        _data = _res[0].get("data", []) if _res else []
        if not _data:
            continue
        _sum = sum(float(d.get("ratio") or 0) for d in _data)
        _cand = (len(_data), _sum, _cid2, _name)
        if _best is None or _cand[:2] > _best[:2]:
            _best = _cand
    _out = (_best[2], _best[3]) if _best else (None, None)
    _cat_cache[_kw] = _out
    return _out


def datalab_keyword_gender_age(client_id, client_secret, keyword):
    """키워드의 성별/연령별 검색 비율 (데이터랩 쇼핑인사이트, 최근 12개월 합산).
    카테고리는 쇼핑검색 상위 결과에서 자동 탐지.
    반환: (dict{'gender':{'여성','남성'}, 'ages':{'10대'..'50대+'}, 'category'}, error)
    """
    import json as _json
    from datetime import date as _date, timedelta as _td
    if not (client_id and client_secret):
        return None, "네이버 Open API 키 미설정"
    _kw = str(keyword or "").strip()
    if not _kw:
        return None, "키워드를 입력하세요."
    _cat, _cat_name = _detect_shop_category(client_id, client_secret, _kw)
    if not _cat:
        return None, "쇼핑 카테고리 탐지 실패 (쇼핑 검색결과 없음)"
    _end = _date.today().replace(day=1) - _td(days=1)
    _sy, _sm = _end.year, _end.month - 11
    while _sm <= 0:
        _sm += 12; _sy -= 1
    _body = {"startDate": _date(_sy, _sm, 1).strftime("%Y-%m-%d"),
             "endDate": _end.strftime("%Y-%m-%d"),
             "timeUnit": "month", "category": _cat, "keyword": _kw}

    def _fetch(kind):
        _j, _err = _datalab_post(client_id, client_secret, kind, _body)
        if _err:
            return None, _err
        _res = _j.get("results") or []
        _sum = {}
        for d in (_res[0].get("data", []) if _res else []):
            g = str(d.get("group") or "")
            _sum[g] = _sum.get(g, 0.0) + float(d.get("ratio") or 0)
        return _sum, None

    _g, _e1 = _fetch("shop_gender")
    if _e1:
        return None, _e1
    _a, _e2 = _fetch("shop_age")
    if _e2:
        return None, _e2
    _gt = sum(_g.values()) or 1.0
    gender = {"여성": round(_g.get("f", 0) / _gt * 100, 1),
              "남성": round(_g.get("m", 0) / _gt * 100, 1)}
    # 연령: 50대+60대 합산 → '50대+' (이미지 표기와 동일)
    _a50 = _a.get("50", 0) + _a.get("60", 0)
    _at = (sum(_a.values()) or 1.0)
    ages = {"10대": round(_a.get("10", 0) / _at * 100, 1),
            "20대": round(_a.get("20", 0) / _at * 100, 1),
            "30대": round(_a.get("30", 0) / _at * 100, 1),
            "40대": round(_a.get("40", 0) / _at * 100, 1),
            "50대+": round(_a50 / _at * 100, 1)}
    return {"gender": gender, "ages": ages, "category": _cat_name}, None
