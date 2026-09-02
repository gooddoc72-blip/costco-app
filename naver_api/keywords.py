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
    # keyword가 리스트면 hintKeywords 다중 조회(네이버 허용 최대 5개).
    # 상품명 한 덩어리보다 '핵심어 여러 개'가 연관어를 훨씬 많이 준다.
    if isinstance(keyword, (list, tuple, set)):
        _hint = ",".join([str(k).replace(" ", "") for k in keyword if str(k).strip()][:5])
    else:
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


# ── 상품명 → 검색용 키워드 추출 ────────────────────────────────
# 네이버 키워드도구(keywordstool)의 hintKeywords는 '검색어'를 기대한다.
# 상품명 전체를 그대로 던지면 자기 자신 1건만 돌아오고 연관검색어가 하나도 안 나온다.
#   '본비 호두아몬드율무차 18g x 120스틱' → 1건('본비호두아몬드율무차18GX120스틱', 20회)
#   '호두아몬드율무차'                    → 118건
# 그동안 상품명 전체를 seed로 던져서, 연관키워드가 없으니 원본 상품명이 그대로
# 등록됐다(= 쇼핑성 키워드가 하나도 안 붙었다). 규격·용량·괄호를 걷어낸
# '핵심 검색어'를 만들어 던진다.
_SPEC_UNITS_LATIN = ('kg', 'mg', 'ml', 'cm', 'mm', 'lb', 'oz', 'ct', 'pcs', 'ea', 'g', 'l', 'p', 't')
_SPEC_UNITS_KR = ('개입', '개', '매', '입', '스틱', '포', '정', '장', '봉', '팩',
                  '병', '캔', '구', '인분', '미', '과', '롤', '단')

#: 검색어로서 의미가 없는 상품명 토큰 (브랜드 수식어·판매문구)
_SEED_NOISE = {
    '시그니처', 'signature', '그레이드', 'grade', '정품', '무료배송', '당일발송',
    '특가', '세트', 'set', '신상', 'best', '대용량', '행사', '할인',
}


def clean_search_seed(name):
    """상품명 → 검색용 핵심어. 용량·규격·괄호·판매문구를 걷어낸다.

    '커클랜드 시그니처 냉동 딸기 그레이드 A 2.72kg (6LB)' → '커클랜드 냉동 딸기'
    '본비 호두아몬드율무차 18g x 120스틱'                → '본비 호두아몬드율무차'
    """
    import re as _re
    _t = str(name or '')
    _t = _re.sub(r'\([^)]*\)', ' ', _t)
    _t = _re.sub(r'\[[^\]]*\]', ' ', _t)
    _t = _t.replace('×', ' ').replace('*', ' ').replace('/', ' ')
    # 숫자+단위 — 라틴 단위는 뒤에 글자가 안 붙을 때만(‘6LB’는 제거, ‘1등급’은 유지)
    _t = _re.sub(r'(?i)\d+(?:\.\d+)?\s*(?:' + '|'.join(_SPEC_UNITS_LATIN) + r')(?![a-z0-9가-힣])',
                 ' ', _t)
    _t = _re.sub(r'\d+(?:\.\d+)?\s*(?:' + '|'.join(_SPEC_UNITS_KR) + r')', ' ', _t)
    _t = _re.sub(r'(?i)(?<![a-z0-9])x(?![a-z0-9])', ' ', _t)      # 곱셈 x
    _t = _re.sub(r'\d+(?:\.\d+)?', ' ', _t)                        # 남은 숫자
    _t = _re.sub(r'(?i)(?<![a-z])[a-z](?![a-z])', ' ', _t)         # 단독 알파벳('그레이드 A')
    _toks = [w for w in _t.split() if w and w.lower() not in _SEED_NOISE]
    _out, _seen = [], set()
    for _w in _toks:                       # 같은 낱말 반복 제거('건전지 … 건전지')
        _n = _w.lower()
        if _n in _seen:
            continue
        _seen.add(_n)
        _out.append(_w)
    return ' '.join(_out).strip()


def build_hint_keywords(name, category='', brand='', model_name='', max_hints=5):
    """키워드도구에 던질 hint 목록(최대 5개). 앞쪽이 우선순위가 높다.

    상품 본체(뒤쪽 토큰)를 먼저 넣는다 — 한국어 상품명은 '브랜드 … 제품'
    순이라 뒤쪽이 검색어에 가깝다('커클랜드 냉동 딸기' → '냉동딸기').
    """
    _core = clean_search_seed(name)
    _toks = _core.split()
    _brand = str(brand or '').strip()
    _prod = [w for w in _toks if w.lower() != _brand.lower()] or _toks
    # 규격을 떼고 남은 한 글자 찌꺼기('D형'→'형')는 검색어가 못 된다.
    # 단 '쌀'처럼 한 글자가 상품 본체인 경우가 있어, 대안이 있을 때만 버린다.
    _hp = [w for w in _prod if len(w) >= 2] or _prod
    _hints = []

    def _add(_x):
        _x = str(_x or '').strip().replace(' ', '')
        if not _x or _x in _hints or len(_hints) >= max_hints:
            return
        if len(_x) < 2 and not ('가' <= _x <= '힣'):
            return          # 한 글자는 한글일 때만 허용(라틴 한 글자는 규격 찌꺼기)
        _hints.append(_x)

    _order = []
    if len(_hp) >= 2:
        _order.append(''.join(_hp[-2:]))            # 뒤 2토큰 = 보통 상품 본체
    _order.append(_hp[-1] if _hp else '')           # 마지막 토큰
    # 남은 자리는 긴 토큰부터 — 합성명사('골드키위','알카라인')가 실제 검색어일 확률이 높다
    _order += sorted([w for w in _hp if len(w) >= 2], key=lambda w: -len(w))
    _order.append(clean_search_seed(model_name))
    if str(category or '').strip():
        _order.append(str(category).replace('>', ' ').split()[-1])
    if _brand and _hp:
        _order.append(_brand + _hp[-1])
    for _x in _order:
        _add(_x)
    return _hints


def relevance_anchors(name, category='', extra=()):
    """관련성 판정용 앵커 집합 (소문자·공백제거).

    키워드도구는 '연관'이라는 이름과 달리 무관한 고검색어를 잔뜩 준다
    (율무차 → 레모네이드·우렁강된장, 냉동딸기 → 복숭아·쌀10KG).
    그걸 그대로 상품명 앞단에 박으면 상품명이 망가진다.
    상품명·카테고리에서 뽑은 조각과 겹치는 후보만 통과시킨다.
    합성어('호두아몬드율무차')는 통짜로는 안 걸리므로 2~4글자 조각도 앵커로 쓴다.
    """
    _words = clean_search_seed(name).split()
    _words += [w for w in str(category or '').replace('>', ' ').split()]
    _words += [str(x) for x in (extra or [])]
    _out = set()
    for _w in _words:
        _w = str(_w).strip().lower().replace(' ', '')
        if len(_w) < 2 or _w in _SEED_NOISE:
            continue
        _out.add(_w)
        if len(_w) >= 4:
            for _n in (2, 3, 4):
                for _i in range(len(_w) - _n + 1):
                    _out.add(_w[_i:_i + _n])
    return _out


def is_relevant_keyword(kw, anchors):
    """후보 키워드가 상품과 관련 있는지 (앵커 조각을 하나라도 포함하는지)."""
    _k = str(kw or '').lower().replace(' ', '')
    if not _k or not anchors:
        return False
    return any(_a in _k for _a in anchors)


def ai_shopping_keywords(seed, category='', ai_key=None, gemini_key=None, n=5):
    """AI가 뽑는 쇼핑성 검색 키워드 후보. 키워드도구 결과가 부실할 때 쓴다.

    키워드도구는 hint가 희귀어면 연관어를 거의 안 준다. 그때 상품명만 남으면
    쇼핑성 키워드가 통째로 빠지므로, AI 후보를 뽑아 다시 키워드도구로
    검색량을 확인한다(지어낸 키워드를 그대로 쓰지 않기 위해).
    반환: [키워드, ...]
    """
    _seed = str(seed or '').strip()
    if not _seed:
        return []
    try:
        import ai_service
        _gk = ai_service.resolve_gemini_key(gemini_key)
    except Exception:
        return []
    if not (ai_key or _gk):
        return []
    _sys = ("너는 네이버 쇼핑 검색 키워드 전문가다. 주어진 상품을 살 사람이 "
            "네이버 쇼핑에서 실제로 검색할 만한 키워드만 고른다. "
            "규칙: 상품 자체를 가리키는 키워드만(용도·대상·형태 포함 가능). "
            "브랜드명·용량·수량·과장어·중복 금지. 2~10자. "
            "쉼표로 구분해 %d개만 출력하고 다른 말은 하지 마라." % int(n))
    _msg = "상품명: %s\n카테고리: %s" % (_seed, category or '미상')
    try:
        import ai_service
        _txt, _e, _ = ai_service.ai_complete(
            _sys, _msg, gemini_key=_gk, anthropic_key=ai_key or '', max_tokens=120,
            claude_model=getattr(ai_service, 'VISION_MODEL', None))
    except Exception:
        return []
    if not _txt:
        return []
    _out = []
    for _p in str(_txt).replace('\n', ',').split(','):
        _p = _p.strip().strip('"').strip("'").lstrip('-').strip()
        if 2 <= len(_p) <= 12 and _p not in _out:
            _out.append(_p)
    return _out[:int(n)]


#: 상품이 아니라 '정보'를 찾는 검색어 — 상품명에 넣으면 노출도 전환도 안 된다.
#  (율무차 후보에 '율무가루만드는법', '율무효능'이 그대로 올라왔다)
_INFO_WORDS = (
    '만드는법', '만들기', '만드는', '하는법', '레시피', '효능', '효과', '부작용',
    '증상', '유래', '뜻', '차이', '비교', '후기', '블로그', '카페', '종류',
    '먹는법', '보관법', '사용법', '고르는법', '손질', '요리', '칼로리', '성분',
)


def is_info_keyword(kw):
    """상품 검색어가 아니라 정보성 검색어인지."""
    _k = str(kw or '').replace(' ', '')
    return any(_w in _k for _w in _INFO_WORDS)


def ai_pick_keywords(seed, candidates, category='', ai_key=None, gemini_key=None, n=5):
    """후보 중 '이 상품을 살 사람이 실제로 검색할' 키워드만 고른다.

    문자 기반 필터는 낱말이 겹치기만 하면 통과시킨다. 그래서 냉동딸기 상품에
    '대관령딸기'(생딸기 산지 검색어)가, 율무차에 '생율무가루팩'이 남는다.
    형태·상태(냉동/생, 가루/차/즙)가 같은지는 의미 판단이 있어야 걸러진다.

    candidates: [{'키워드','총검색량',...}] 또는 [키워드, ...]
    반환: 선택된 키워드 리스트 (AI 불가·실패 시 빈 리스트 → 호출부가 원래 후보 사용)
    """
    _seed = str(seed or '').strip()
    _cands = []
    for _c in (candidates or []):
        _k = (_c.get('키워드') if isinstance(_c, dict) else _c)
        _k = str(_k or '').strip()
        if _k and _k not in _cands:
            _cands.append(_k)
    if not (_seed and _cands):
        return []
    try:
        import ai_service
        _gk = ai_service.resolve_gemini_key(gemini_key)
    except Exception:
        return []
    if not (ai_key or _gk):
        return []

    _sys = ("너는 네이버 쇼핑 SEO 전문가다. 후보 검색어 중 **이 상품 자체를 사려는 사람이 "
            "검색할 것만** 고른다.\n"
            "반드시 제외:\n"
            "- 상품의 형태·상태·가공이 다른 것 (냉동식품에 '생/산지직송', 차 제품에 '가루/원물')\n"
            "- 다른 상품 (딸기 상품에 '블루베리')\n"
            "- 정보성 검색어 (효능·만드는법·레시피·후기)\n"
            "- 다른 브랜드명\n"
            "고른 것만 쉼표로 구분해 출력한다. 없으면 '없음'만 출력. 다른 말 금지.\n"
            "최대 %d개." % int(n))
    _msg = ("상품명: %s\n카테고리: %s\n후보: %s"
            % (_seed, category or '미상', ', '.join(_cands[:40])))
    try:
        import ai_service
        _txt, _e, _ = ai_service.ai_complete(
            _sys, _msg, gemini_key=_gk, anthropic_key=ai_key or '', max_tokens=200,
            claude_model=getattr(ai_service, 'VISION_MODEL', None))
    except Exception:
        return []
    if not _txt or '없음' in str(_txt)[:6]:
        return []
    _norm = {_k.lower().replace(' ', ''): _k for _k in _cands}
    _out = []
    for _p in str(_txt).replace('\n', ',').split(','):
        _p = _p.strip().strip('"').strip("'").lstrip('-').strip()
        _n = _p.lower().replace(' ', '')
        if _n in _norm and _norm[_n] not in _out:      # 후보에 없던 말은 버린다(환각 방지)
            _out.append(_norm[_n])
    return _out[:int(n)]


def keyword_seo_name(ad_api_key, ad_secret, customer_id, seed, ai_key=None,
                     category="", front_max=200, n_front=3, manual_kw=None,
                     gemini_key=None, brand="", model_name=""):
    """검색량 분석 기반 SEO 상품명 + 후보 키워드. (메인 네이버 등록용)

    파이프라인:
      1) 상품명 → 핵심 검색어(hint) 추출        clean_search_seed / build_hint_keywords
      2) 키워드도구 조회 (hint 최대 5개 동시)
      3) 상품과 무관한 연관어 제거              relevance_anchors / is_relevant_keyword
      4) 후보가 부족하면 AI 쇼핑 키워드 → 검색량 재조회로 보강
      5) 저검색(≤front_max) 앞단 + 대표어 배치  order_seo_keywords
      6) AI가 자연스러운 한 줄 상품명으로 조합 (실패 시 이어붙임)

    manual_kw가 주어지면(사용자 수동선택) 그 순서 그대로 앞단에 쓴다.
    반환: (result, err)
      result = {'name', 'front'[], 'rep', 'candidates'[{키워드,총검색량,경쟁도,band}],
                'hints'[], 'dropped'(관련성 필터로 제외한 수)}
    """
    _seed = str(seed or "").strip()
    _hints = build_hint_keywords(_seed, category=category, brand=brand,
                                 model_name=model_name)
    rows, err = keyword_tool(ad_api_key, ad_secret, customer_id, _hints or _seed)

    _anchors = relevance_anchors(_seed, category)
    _seed_norm = _seed.lower().replace(" ", "")

    def _keep(_r):
        _k = str(_r.get("키워드", "")).strip()
        if not _k:
            return False
        if _k.lower().replace(" ", "") == _seed_norm:   # 상품명 통짜 에코
            return False
        return is_relevant_keyword(_k, _anchors)

    _rel = [r for r in (rows or []) if _keep(r)]
    _dropped = len(rows or []) - len(_rel)

    # 후보가 부족하면 AI 쇼핑 키워드로 보강 — 단, 검색량은 키워드도구로 재확인한다
    if len(_rel) < max(1, int(n_front)):
        _aikw = ai_shopping_keywords(_seed, category=category, ai_key=ai_key,
                                     gemini_key=gemini_key, n=5)
        if _aikw:
            _rows2, _e2 = keyword_tool(ad_api_key, ad_secret, customer_id, _aikw[:5])
            _have = {str(r.get("키워드", "")).lower().replace(" ", "") for r in _rel}
            for _r in (_rows2 or []):
                _k = str(_r.get("키워드", "")).strip()
                if not _k or _k.lower().replace(" ", "") in _have:
                    continue
                if _k.lower().replace(" ", "") == _seed_norm:
                    continue
                if is_relevant_keyword(_k, _anchors) or _k in _aikw:
                    _rel.append(_r)
                    _have.add(_k.lower().replace(" ", ""))

    # 정보성 검색어 제거 — '율무효능', '율무가루만드는법'은 상품 검색어가 아니다
    _info = [r for r in _rel if is_info_keyword(r.get("키워드"))]
    if len(_info) < len(_rel):
        _rel = [r for r in _rel if r not in _info]
        _dropped += len(_info)

    if not _rel:
        return ({"name": _seed, "front": [], "rep": None, "candidates": [],
                 "hints": _hints, "dropped": _dropped},
                err or "관련 연관키워드 없음")

    # 의미 선별 — 낱말만 겹치는 '대관령딸기'(생딸기)를 냉동딸기 상품에서 빼려면
    # 형태·상태를 이해해야 한다. 후보에 없던 말은 버려 환각을 막는다.
    if not manual_kw:
        _pick = ai_pick_keywords(_seed, _rel, category=category, ai_key=ai_key,
                                 gemini_key=gemini_key, n=max(2, int(n_front) + 2))
        if _pick:
            _pn = {k.lower().replace(" ", "") for k in _pick}
            _sel = [r for r in _rel
                    if str(r.get("키워드", "")).lower().replace(" ", "") in _pn]
            if _sel:
                _dropped += len(_rel) - len(_sel)
                _rel = _sel

    plan = order_seo_keywords(_rel, front_max=front_max, n_front=n_front)
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
              "candidates": plan["candidates"], "hints": _hints, "dropped": _dropped}
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
            _sys = ("너는 네이버 스마트스토어 SEO 상품명 작성 전문가다.\n"
                    "출력: 상품명 한 줄만. 설명·따옴표·이모지·특수문자 금지.\n"
                    "규칙:\n"
                    "1) 주어진 검색 키워드를 제시된 순서대로 모두 포함하고, "
                    "앞쪽 키워드를 상품명 맨 앞에 둔다.\n"
                    "2) 원본 상품명의 브랜드·제품명·용량/수량 표기는 그대로 유지해 뒤에 붙인다. "
                    "임의로 바꾸거나 빼지 않는다.\n"
                    "3) 같은 낱말을 두 번 쓰지 않는다. 키워드가 원본 상품명에 이미 있으면 "
                    "그 자리를 그대로 두고 앞에 또 적지 않는다.\n"
                    "4) 40자 이내. 낱말은 띄어쓰기로 구분한다.")
            _msg = (f"원본 상품명: {_seed}\n카테고리: {category or '미상'}\n"
                    f"앞단부터 순서대로 포함할 검색 키워드: {', '.join(ordered)}\n"
                    f"상품명 한 줄만 출력.")
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
    # 상품명 통짜를 던지면 연관어가 안 나온다 — keyword_seo_name과 같은 정제를 쓴다.
    _hints = build_hint_keywords(_seed, category=category)
    rows, err = keyword_tool(ad_api_key, ad_secret, customer_id, _hints or _seed)
    if rows:
        _anc = relevance_anchors(_seed, category)
        _sn = _seed.lower().replace(" ", "")
        rows = [r for r in rows
                if str(r.get("키워드", "")).lower().replace(" ", "") != _sn
                and is_relevant_keyword(r.get("키워드"), _anc)]
    if err or not rows:
        info["err"] = err or "관련 연관키워드 없음"
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
