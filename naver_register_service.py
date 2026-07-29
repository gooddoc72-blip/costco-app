"""
네이버 스마트스토어 자동 등록 공용 코어 (UI/헤드리스 공용, Streamlit 비의존)

코스트코에서 크롤링해 shared_products에 저장된 상품을 네이버에 등록하는
핵심 로직을 한 곳에 모은다. naver_register_page.py(UI)와 auto_task.py(무인)가
같은 함수를 호출해 중복을 없앤다.

- compute_sale_price : 코스트코가 → 마진·수수료 반영 판매가
- resolve_category   : 상품 → 네이버 리프카테고리ID (저장값→매핑→AI 순)
- exclude_reason     : 규칙 기반 등록 제외 판정 (키워드·카테고리·원가·카톤)
- register_one       : 단일 상품 등록 (이미지 업로드 + 상세 + register_product)
- auto_register      : 미등록 상품 일괄 자동 등록 (카테고리 미해결은 스킵 → 장바구니 회수)
"""
import os
import re
import json
import sqlite3

import naver_api
from db import (
    get_all_products_merged,
    get_product_detail,
    upsert_user_private,
    get_setting,
    set_setting,
    AUTH_DB,
)
# 판매가 공식은 플랫폼 공통(pricing.py) — 쿠팡(coupang_reprice)과 동일 공식 공유.
from pricing import DEFAULT_IMPORT_SHIPPING, compute_sale_price, unit_cost as _unit_cost


def _noop(*_a, **_k):
    pass


def _fallback_desc_body(name, raw_detail):
    """비-AI 상세설명 본문(HTML 안전) — AI 미사용/실패/크레딧부족 시.

    코스트코 크롤 상세 텍스트(raw_detail)를 태그 제거·정제해 그대로 쓴다(실제 제품 설명).
    쓸 만한 텍스트가 없으면 빈 문자열 반환(제품 상세정보 표가 정보를 대신 제공).
    상품명 반복은 무의미(상단 제목과 중복)하므로 넣지 않는다."""
    import re as _re, html as _h
    _txt = _re.sub(r"<[^>]+>", " ", str(raw_detail or ""))
    _txt = _re.sub(r"\s+", " ", _txt).strip()
    if len(_txt) >= 30:
        return _h.escape(_txt[:1500])
    return ""


# ─── 규칙 기반 등록 제외 ──────────────────────────────────────────
#   '조건에 맞는 상품군'을 통째로 거른다. (개별 상품 영구 스킵은 auto_register_skip 담당)
#   자동 등록(auto_register)과 수동 후보 목록(naver_register_page)이 같은 규칙을 쓰도록
#   판정 로직을 여기 한 곳에 둔다. 설정은 사용자별(자동화 탭 Task 7).
EXCLUDE_KEYS = (
    "auto_register_exclude_keywords",     # 상품명 포함 키워드 (콤마/개행 구분)
    "auto_register_exclude_categories",   # 코스트코 카테고리 (콤마/개행 구분)
    "auto_register_exclude_cost_min",     # 단품원가 하한 (미만이면 제외, 0=미사용)
    "auto_register_exclude_cost_max",     # 단품원가 상한 (초과면 제외, 0=미사용)
    "auto_register_exclude_carton",       # '1'이면 카톤 표기 + 고액 원가 상품 제외
    "auto_register_carton_min",           # 카톤 판정 최소 수량 (기본 30)
    "auto_register_carton_cost_min",      # 카톤 판정 최소 원가 (기본 20만원, 0=원가조건 끔)
)

CARTON_MIN_DEFAULT = 30
# 카톤 판정에 요구하는 최소 단품원가.
#   실데이터(미등록 2,027건) 검증: 이름의 'xN'만으로 판정하면 137건이 걸리는데
#   그중 실제 카톤가는 19건뿐이고 118건은 '초콜릿 1.3kg x 200'(내용물 개수)처럼
#   원가 1~3만원인 정상 단품이었다 → 이름 패턴 단독은 오탐 86%.
#   카톤가로 저장된 건은 예외 없이 고액(수십만~수천만원)이므로 원가 조건을 AND로 건다.
CARTON_COST_MIN_DEFAULT = 200_000

# 카톤/대량 표기: 'x240', '×432', '*100' 처럼 2자리 이상 수량이 붙은 것.
#   'x24개'·'x90캡슐'(내용물 개수)·'x250ml'(용량)는 부정 전방탐색으로 배제.
#   치수 표기('200x300', '200 x 300')는 앞이 숫자이므로 부정 후방탐색으로 배제.
#   ct/ea는 배제하지 않는다 — 'X 50CT'(카톤 수)와 'x 170ct'(내용물 수)가 섞여 쓰이는데,
#   여러 개 매칭 시 최댓값을 쓰므로 카톤 수가 선택되고, 저가 오탐은 원가 조건이 막는다.
#   ※ 'x 84ea x 3'처럼 카톤 배수가 1자리면 84가 잡힌다(수량 기준만 넘으면 무방).
#   영문자 뒤 X는 모델명('아이나비 VX2000')이므로 배제 — 실제 수량 표기는 앞이
#   공백이거나 한글 단위('180개x 120')다.
_CARTON_RE = re.compile(
    r"(?<![\d.])(?<!\d )(?<![A-Za-z])[xX×*]\s*(\d{2,})(?!\d)"
    r"(?!\s*(?:개|입|매|장|알|정|포|봉|컵|롤|팩|스틱|캡슐|티백|조각|인분|세트"
    r"|ml|mL|L|g|kg|G|KG|mm|cm|m\b|인치))"
)


def _split_terms(raw):
    """콤마/개행 구분 문자열 → 소문자 term 리스트 (공백 항목 제거)."""
    out = []
    for t in str(raw or "").replace("\n", ",").split(","):
        t = t.strip().lower()
        if t:
            out.append(t)
    return out


def carton_qty(name):
    """상품명에서 카톤/대량 표기 수량 추정 (없으면 0). 여러 개면 최댓값."""
    try:
        nums = [int(m) for m in _CARTON_RE.findall(str(name or ""))]
    except Exception:
        return 0
    return max(nums) if nums else 0


def parse_exclude_rules(raw):
    """설정 dict(문자열 값) → 규칙 dict. DB 비의존 순수함수(UI·무인 공용)."""
    raw = raw or {}

    def _int(k):
        try:
            return int(float(str(raw.get(k) or 0)))
        except Exception:
            return 0

    # 카톤 원가조건: 미저장(빈값)이면 기본값, 명시적 0이면 '원가조건 끔'(이름 패턴만).
    _ccm_raw = str(raw.get("auto_register_carton_cost_min") or "").strip()
    return {
        "keywords":   _split_terms(raw.get("auto_register_exclude_keywords")),
        "categories": _split_terms(raw.get("auto_register_exclude_categories")),
        "cost_min":   max(0, _int("auto_register_exclude_cost_min")),
        "cost_max":   max(0, _int("auto_register_exclude_cost_max")),
        "carton":     str(raw.get("auto_register_exclude_carton") or "") == "1",
        "carton_min": _int("auto_register_carton_min") or CARTON_MIN_DEFAULT,
        "carton_cost_min": (max(0, _int("auto_register_carton_cost_min"))
                            if _ccm_raw else CARTON_COST_MIN_DEFAULT),
    }


def has_exclude_rules(rules):
    """설정된 규칙이 하나라도 있는지."""
    r = rules or {}
    return bool(r.get("keywords") or r.get("categories")
                or r.get("cost_min") or r.get("cost_max") or r.get("carton"))


def load_exclude_rules(username):
    """사용자 설정에서 제외 규칙 로드."""
    return parse_exclude_rules({k: (get_setting(username, k) or "") for k in EXCLUDE_KEYS})


def exclude_reason(product, rules):
    """상품이 제외 규칙에 걸리면 사유 문자열, 아니면 None.
    원가 규칙은 원가가 0(미수집)인 건에는 적용하지 않는다 — '가격 없음'으로 따로 스킵되기 때문."""
    if not has_exclude_rules(rules):
        return None
    name = str(product.get("costco_name") or "")
    low = name.lower()
    for kw in rules.get("keywords") or []:
        if kw in low:
            return f"키워드 '{kw}'"

    cat = str(product.get("category") or "").strip().lower()
    if cat:
        for c in rules.get("categories") or []:
            if c in cat:
                return f"카테고리 '{c}'"

    cmin = rules.get("cost_min") or 0
    cmax = rules.get("cost_max") or 0
    if cmin or cmax:
        cost = _unit_cost(product)
        if cost > 0:
            if cmin and cost < cmin:
                return f"원가 {cost:,}원 < 하한 {cmin:,}원"
            if cmax and cost > cmax:
                return f"원가 {cost:,}원 > 상한 {cmax:,}원"

    if rules.get("carton"):
        q = carton_qty(name)
        if q >= (rules.get("carton_min") or CARTON_MIN_DEFAULT):
            # 이름 패턴만으로는 '1.3kg x 200'(내용물 개수) 정상 단품을 오탐한다.
            # 카톤가로 저장된 건은 고액이므로 원가 하한을 AND 조건으로 요구한다.
            ccm = rules.get("carton_cost_min", CARTON_COST_MIN_DEFAULT)
            if not ccm:
                return f"카톤 표기 x{q}"
            uc = _unit_cost(product)
            if uc >= ccm:
                return f"카톤 표기 x{q} · 원가 {uc:,}원"
    return None


def filter_excluded(products, rules):
    """(통과 목록, [(상품, 사유), ...]) — UI 미리보기·후보 목록 필터 공용."""
    if not has_exclude_rules(rules):
        return list(products), []
    kept, dropped = [], []
    for p in products:
        r = exclude_reason(p, rules)
        (dropped.append((p, r)) if r else kept.append(p))
    return kept, dropped


# ─── 등록상품 일괄 재가격 (가드 적용) ─────────────────────────────
#   판매가 공식(compute_sale_price)·단품원가(_unit_cost)·택배비 상수(DEFAULT_IMPORT_SHIPPING)는
#   플랫폼 공통이라 pricing.py에서 import(위). 쿠팡은 coupang_reprice가 같은 공식을 공유한다.
def reprice_registered(username, api_id, api_secret, *, margin=10, dry_run=True,
                       max_ratio=2.0, max_count=0, log=None):
    """등록된(naver_product_no 있는) 상품을 '단품원가 + 택배비 3000 + 마진'으로 재가격.

    등록 당시 값으로 굳어 코스트코 가격 인상·택배비 미포함으로 역마진이 된 리스팅을 바로잡는다.
    get_product_list로 현재 판매가를 한 번에 조회 → 목표가 산출 → 가드 통과분만 PUT.

    ⚠️ 안전 가드 (돈 계산 — 카톤가가 원가로 저장된 이상치로 인한 참사 방지):
      · split_qty 반영(_unit_cost) — 소분 단품 원가로 환산
      · 인상만: 신규가 > 현재가일 때만 (이미 충분하면 건너뜀)
      · 인상비율 상한: 신규가 ≤ 현재가 × max_ratio (초과 시 '수동확인'으로 분류, 미적용)
        → 카톤가(x240 등)로 인한 수십~수백 배 폭등을 자동 제외
      · 라이브 목록에 없거나 현재가 0이면 건너뜀

    dry_run=True: 실제 수정 없이 변경안만.  max_count: 실제수정 상한(0=무제한).
    반환 dict{targeted, updated, failed, ok_or_lower, over_cap, not_listed, no_price,
             no_cost, results[], over_cap_samples[]}."""
    log = log or _noop
    listed, lerr = naver_api.get_product_list(api_id, api_secret)
    if listed is None:
        return {"error": lerr or "네이버 상품목록 조회 실패", "targeted": 0,
                "updated": 0, "failed": 0}
    cur_by_no = {}
    for _it in listed:
        for _k in ("originProductNo", "channelProductNo"):
            _v = str(_it.get(_k) or "").strip()
            if _v:
                cur_by_no[_v] = _it

    merged = get_all_products_merged(username)
    out = {"targeted": 0, "updated": 0, "failed": 0, "ok_or_lower": 0,
           "over_cap": 0, "not_listed": 0, "no_price": 0, "no_cost": 0,
           "results": [], "over_cap_samples": []}
    _n = 0
    for p in merged:
        nno = str(p.get("naver_product_no") or "").strip()
        if not nno:
            continue
        ucost = _unit_cost(p)
        if ucost <= 0:
            out["no_cost"] += 1
            continue
        new_price = compute_sale_price({"online_price": ucost}, margin)  # 택배비 3000 포함
        cur = cur_by_no.get(nno)
        if cur is None:
            out["not_listed"] += 1               # 라이브 목록에 없음(판매종료 등) — 손 안 댐
            continue
        cur_price = int(cur.get("salePrice") or 0)
        name = (p.get("costco_name") or "")[:40]
        if cur_price <= 0:
            out["no_price"] += 1
            continue
        if new_price <= cur_price:
            out["ok_or_lower"] += 1              # 이미 충분(인상 불필요)
            continue
        # PUT 대상 번호: 라이브 목록의 originProductNo 우선(저장 naver_no가 채널번호면
        # origin-products GET이 404 → 변환 실패하던 문제 방지). 없으면 저장 naver_no.
        put_no = str(cur.get("originProductNo") or "").strip() or nno
        rec = {"name": name, "naver_no": put_no, "unit_cost": ucost, "current": cur_price,
               "new": new_price, "diff": new_price - cur_price,
               "ratio": round(new_price / cur_price, 2)}
        # 상한 초과 = 카톤가/이상치 의심 → 적용 안 하고 수동확인 목록으로
        if new_price > cur_price * max_ratio:
            out["over_cap"] += 1
            if len(out["over_cap_samples"]) < 30:
                out["over_cap_samples"].append(rec)
            continue
        out["targeted"] += 1
        if dry_run:
            out["results"].append(rec)
            continue
        if max_count and _n >= max_count:
            rec["skipped"] = "상한 도달"
            out["results"].append(rec)
            continue
        _n += 1
        ok, err, _used = naver_api.update_product_price(api_id, api_secret, put_no, new_price)
        if ok:
            out["updated"] += 1
            log(f"  ✅ {name}: {cur_price:,} → {new_price:,}")
        else:
            out["failed"] += 1
            rec["err"] = str(err)[:80]
            out["results"].append(rec)
            log(f"  ❌ {name}: {str(err)[:60]}")
    return out


# ─── 카테고리 해결 ────────────────────────────────────────────────
def _resolve_leaf(api_id, api_secret, path):
    """카테고리 경로(A>B>C>D) → (leaf_id, full_name).
    leaf명으로 검색 후 경로 최다일치 후보 선택.
    (naver_register_page._nr_resolve_leaf 이식)"""
    leaf = str(path).split(">")[-1].strip()
    if not leaf:
        return None, None
    cr, _ = naver_api.search_naver_categories(api_id, api_secret, leaf)
    if not cr:
        return None, None
    pt = set(str(path).replace(">", " ").replace("/", " ").split())
    best, bs = None, -1
    for c in cr:
        ct = set(str(c.get("full_name", "")).replace(">", " ").replace("/", " ").split())
        s = len(pt & ct)
        if s > bs:
            bs, best = s, c
    return (best.get("id"), best.get("full_name")) if best else (None, None)


def resolve_category(api_id, api_secret, product, cat_map=None, ai_key="", open_creds=None):
    """상품 → 네이버 리프카테고리ID.
    우선순위: ① 상품에 저장된 naver_category_id → ② 코스트코 카테고리 매핑
             → ③ AI 자동(쇼핑검색→suggest_naver_category→leaf resolve).
    반환: (cat_id, cat_full, source)  source ∈ {'product','mapping','ai', None}.
    셋 다 실패 시 (None, None, None)."""
    # ① 상품에 이미 저장된 네이버 카테고리
    cid = str(product.get("naver_category_id") or "").strip()
    if cid:
        return cid, "", "product"

    # ② 코스트코 카테고리 → 네이버 기본 매핑
    cat_map = cat_map or {}
    ccat = str(product.get("category") or "").strip()
    if ccat:
        m = cat_map.get(ccat)
        mid = ""
        if isinstance(m, dict):
            mid = str(m.get("id") or "").strip()
        elif m:
            mid = str(m).strip()
        if mid:
            return mid, "", "mapping"

    # ③ AI 자동 (쇼핑검색 → 카테고리 후보 → suggest → leaf resolve)
    #    open_creds 없으면 쇼핑검색 불가 → 미해결.
    if open_creds and all(open_creds):
        try:
            import ai_service
        except Exception:
            ai_service = None
        name = product.get("costco_name") or ""
        items, _ = naver_api.naver_shopping_search(open_creds[0], open_creds[1], name)
        paths = [
            ">".join([x for x in (it.get("category1"), it.get("category2"),
                                  it.get("category3"), it.get("category4")) if x])
            for it in (items or [])
        ]
        paths = [p for p in paths if p]
        if paths:
            chosen = paths[0]
            if ai_service is not None:
                # ai_key 비어도 suggest_naver_category는 다수결 경로 반환(무AI 폴백)
                _c, _ = ai_service.suggest_naver_category(ai_key, name, paths)
                chosen = _c or paths[0]
            cat_id, cat_full = _resolve_leaf(api_id, api_secret, chosen)
            if cat_id:
                return cat_id, cat_full, "ai"

    return None, None, None


# ─── 단일 상품 등록 ───────────────────────────────────────────────
def register_one(username, api_id, api_secret, product, cat_id, opts=None):
    """크롤링 상품 1건을 네이버에 등록.
    대표+추가이미지 CDN 업로드 → 상세HTML → register_product.
    성공 시 등록완료 표시(upsert_user_private) + shared_products.naver_category_id 갱신.
    opts: sale_price(필수), as_tel, stock.
    반환: (origin_product_no, err)."""
    opts = opts or {}
    name = (product.get("costco_name") or "").strip()
    if not name:
        return None, "상품명 없음"

    sale = int(opts.get("sale_price") or 0)
    if sale <= 0:
        return None, "판매가 없음"

    # AI 상품명 최적화 (opt-in) — 실패 시 원본 유지
    _ai = str(opts.get("ai_key") or "")
    if _ai and opts.get("optimize_name"):
        try:
            import ai_service
            _opt, _ = ai_service.optimize_product_name(_ai, name, opts.get("cat_full", ""))
            if _opt and _opt.strip():
                name = _opt.strip()[:100]
        except Exception:
            pass

    # 대표 이미지 선택: 로컬 파일은 이 머신에 실제 존재할 때만 사용,
    # 아니면 원본 http URL(코스트코 CDN)로 폴백.
    # (local_image가 타 PC의 절대경로(F:\...)면 서버엔 없으므로 image_url 사용)
    _li = str(product.get("local_image") or "").strip()
    _iu = str(product.get("image_url") or "").strip()
    if _li.startswith("http"):
        rep = _li
    elif _li and os.path.exists(_li):
        rep = _li
    elif _iu:
        rep = _iu
    else:
        rep = _li
    if not rep:
        return None, "대표이미지 없음"

    # 대표이미지가 목록API 저해상도(160px) 썸네일이면 상세API 1200px(superZoom)로 교체.
    #   저해상도를 네이버 1000×1000으로 확대하면 뭉개져 '깨진' 것처럼 보이던 문제 해결.
    #   상세API 실패 시 기존 rep 유지(무회귀). hi_extra는 저장 추가이미지 없을 때만 보완.
    _hi_extra = []
    _pno_img = str(product.get("product_no") or "").strip()
    if _pno_img:
        try:
            from costco_crawler import fetch_hires_images as _fhi
            _hi_main, _hi_extra = _fhi(_pno_img)
            if _hi_main:
                rep = _hi_main
        except Exception:
            _hi_extra = []

    # 대표 이미지 → 네이버 CDN (1000×1000 자동변환)
    rep_cdn, e1 = naver_api.upload_product_image(api_id, api_secret, rep)
    if not rep_cdn:
        return None, f"이미지 업로드 실패: {e1}"

    # 추가 이미지 → 네이버 CDN + 크롤링 상세(원본) 로드
    extra_cdn = []
    _raw_detail = ""
    sid = product.get("shared_id")
    if sid:
        xraw, dhtml = get_product_detail(sid)
        _raw_detail = dhtml or ""
        if xraw:
            try:
                xlist = json.loads(xraw)
            except Exception:
                xlist = []
            if xlist:
                extra_cdn, _ = naver_api.upload_images_batch(api_id, api_secret, xlist)
    # 저장된 추가이미지가 없으면 상세API 하이레스 갤러리로 보완 (순수 추가, 무회귀)
    if not extra_cdn and _hi_extra:
        extra_cdn, _ = naver_api.upload_images_batch(api_id, api_secret, _hi_extra)

    _catf = opts.get("cat_full", "")

    # 코스트코 스펙 1회 조회 (상세설명 폴백 + '제품 상세정보' 표 공용)
    import costco_crawler as _cc
    _spec = {}
    if opts.get("with_spec") or opts.get("ai_desc"):
        try:
            _spec = _cc.fetch_costco_spec(str(product.get("product_no") or "").strip()) or {}
        except Exception:
            _spec = {}

    # 상세설명: ① AI가 코스트코 내용 분석해 새로 작성(크레딧 필요)
    #           ② AI 미사용/실패/크레딧부족 → 비-AI 폴백(코스트코 크롤 상세 텍스트 정제)
    import html as _h2
    _desc_block = ""
    if opts.get("ai_desc"):
        _d = None
        if _ai:
            try:
                import ai_service
                _d, _ = ai_service.generate_description_from_costco(_ai, name, _raw_detail, _catf)
            except Exception:
                _d = None
        _body = (_h2.escape(_d).replace("\n", "<br>") if _d
                 else _fallback_desc_body(name, _raw_detail))
        if _body:
            _desc_block = ('<div style="font-size:17px;line-height:1.9;text-align:center;'
                           'padding:4px 16px 8px;color:#333">' + _body + '</div>')

    # 한글표시사항: 코스트코 스펙 → '제품 상세정보' 표 + 제조자
    _spec_table = ""
    _manufacturer = ""
    if opts.get("with_spec") and _spec:
        _spec_table = _cc.build_spec_table_html(_spec)
        for _k in ("제조자/수입자", "제조원/수입원", "제조원", "수입원", "제조사"):
            if _spec.get(_k):
                _manufacturer = str(_spec[_k]).strip()
                break

    # 상세페이지: [공통상단] + 상품명 + (2줄 여백) + 상품설명 + 이미지들 + 표시사항표 + [공통하단]
    _top = str(get_setting(username, "naver_detail_top_img") or "").strip()
    _bot = str(get_setting(username, "naver_detail_bottom_img") or "").strip()
    _all_cdn = [rep_cdn] + [u for u in extra_cdn if u]
    _nm = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    _dp = ['<div style="text-align:center">']
    if _top:
        _dp.append('<img src="%s" style="max-width:100%%;display:block;margin:0 auto">' % _top)
    _dp.append('<div style="font-size:30px;font-weight:800;padding:20px 12px 0;'
               'line-height:1.4">%s</div>' % _nm)
    _dp.append('<div style="height:44px"></div>')   # 상품명 아래 2줄 여백
    if _desc_block:
        _dp.append(_desc_block)
    for _u in _all_cdn:
        _dp.append('<img src="%s" style="max-width:100%%;display:block;'
                   'margin:16px auto 0;border:1px solid #cccccc">' % _u)
    if _spec_table:
        _dp.append(_spec_table)
    if _bot:
        _dp.append('<img src="%s" style="max-width:100%%;display:block;margin:24px auto 0">' % _bot)
    _dp.append('</div>')
    detail_html = "\n".join(_dp)

    # AI 연관태그 (opt-in) — 네이버 태그사전 검증된 것만 (최대 10개)
    seller_tags = []
    if _ai and opts.get("gen_tags"):
        try:
            seller_tags, _ = naver_api.build_seller_tags(
                api_id, api_secret, _ai, name, opts.get("cat_full", ""), name)
        except Exception:
            seller_tags = []

    res, e2 = naver_api.register_product(api_id, api_secret, {
        "name":              name,
        "sale_price":        sale,
        "image_url":         rep_cdn,
        "category_id":       cat_id,
        "stock":             int(opts.get("stock", 100)),
        "shipping_fee":      int(product.get("shipping_fee") or 0),
        "after_service_tel": opts.get("as_tel") or "1588-1234",
        "extra_image_urls":  extra_cdn,
        "detail_html":       detail_html,
        "seller_code":       str(product.get("product_no") or "").strip(),
        "seller_tags":       seller_tags,
        "manufacturer":      _manufacturer,
    })
    if e2 or not res:
        return None, e2 or "등록 실패"

    origin_no = res.get("origin_product_no", "")

    # 등록 완료 표시 (merged view에서 등록됨으로 잡혀 재등록 방지) + 카테고리 저장
    try:
        upsert_user_private(username, product.get("match_keyword"), name,
                            naver_product_no=origin_no)
    except Exception:
        pass
    if cat_id and sid:
        try:
            ca = sqlite3.connect(AUTH_DB)
            ca.execute("UPDATE shared_products SET naver_category_id=? WHERE id=?",
                       (str(cat_id), sid))
            ca.commit()
            ca.close()
        except Exception:
            pass

    return origin_no, None


# ─── 미등록 상품 일괄 자동 등록 ───────────────────────────────────
def auto_register(username, api_id, api_secret, *, margin=10, max_count=20,
                  open_creds=None, ai_key="", cat_map=None, as_tel="", stock=100,
                  gen_tags=True, optimize_name=True, ai_desc=True, with_spec=True,
                  exclude_rules=None, log=None):
    """미등록 코스트코 상품을 자동 등록.
    - merged에서 naver_product_no 빈 상품만 대상.
    - 제외 규칙(키워드·카테고리·원가·카톤)에 걸린 건은 비용 없이 스킵.
    - 가격/이미지 없는 건은 비용 없이 스킵.
    - 카테고리 미해결 건은 등록하지 않고 스킵(미등록 유지 → UI 장바구니로 회수).
    - max_count: 회당 '처리(카테고리해결+등록)' 상한 (AI 호출·라이브 등록 폭주 방지).
    - exclude_rules: None이면 사용자 설정에서 로드. {}를 넘기면 규칙 미적용.
    반환: dict{ok, fail, skipped_no_category, skipped_no_price, skipped_no_image,
             skipped_excluded, processed, results[]}."""
    log = log or _noop
    cat_map = cat_map or {}
    if exclude_rules is None:
        exclude_rules = load_exclude_rules(username)

    merged = get_all_products_merged(username)

    # 지난 실행에서 400(권한·인증 등 구조적 거부)으로 실패한 상품은 스킵 목록에 기록되어
    # 매번 같은 항목에 막히지 않도록 제외한다. (권한·인증 없는 카테고리는 재시도해도 실패)
    try:
        _skip = set(json.loads(get_setting(username, "auto_register_skip") or "[]"))
    except Exception:
        _skip = set()

    def _skey(pp):
        return str(pp.get("product_no") or pp.get("match_keyword") or "").strip()

    unreg = [p for p in merged
             if not str(p.get("naver_product_no") or "").strip()
             and _skey(p) not in _skip]
    log(f"미등록 후보 {len(unreg)}개 (전체 {len(merged)}개, 스킵 {len(_skip)}개 제외)")
    if has_exclude_rules(exclude_rules):
        _rd = []
        if exclude_rules.get("keywords"):
            _rd.append(f"키워드 {len(exclude_rules['keywords'])}개")
        if exclude_rules.get("categories"):
            _rd.append(f"카테고리 {len(exclude_rules['categories'])}개")
        if exclude_rules.get("cost_min"):
            _rd.append(f"원가 {exclude_rules['cost_min']:,}원 미만")
        if exclude_rules.get("cost_max"):
            _rd.append(f"원가 {exclude_rules['cost_max']:,}원 초과")
        if exclude_rules.get("carton"):
            _ccm = exclude_rules.get("carton_cost_min") or 0
            _rd.append(f"카톤 x{exclude_rules['carton_min']}+"
                       + (f" & 원가 {_ccm:,}원+" if _ccm else " (원가무관)"))
        log("제외 규칙 적용: " + " · ".join(_rd))

    out = {
        "ok": 0, "fail": 0,
        "skipped_no_category": 0, "skipped_no_price": 0, "skipped_no_image": 0,
        "skipped_soldout": 0, "skipped_excluded": 0,
        "processed": 0, "results": [],
    }
    processed = 0
    _skip_dirty = False

    for p in unreg:
        name = (p.get("costco_name") or "")[:40]

        # ── 저비용 사전 스킵 (예산 미소모) ──
        # 규칙 기반 제외가 최우선 — 크롤 조회·AI·등록 어느 것도 태우지 않는다.
        _xr = exclude_reason(p, exclude_rules)
        if _xr:
            out["skipped_excluded"] += 1
            out["results"].append({"상품명": name, "결과": "skip", "내용": f"제외규칙 — {_xr}"})
            continue

        sale = compute_sale_price(p, margin)
        if sale <= 0:
            out["skipped_no_price"] += 1
            out["results"].append({"상품명": name, "결과": "skip", "내용": "가격 없음"})
            continue
        if not (p.get("local_image") or p.get("image_url")):
            out["skipped_no_image"] += 1
            out["results"].append({"상품명": name, "결과": "skip", "내용": "이미지 없음"})
            continue

        # ── 코스트코 판매종료/품절 스킵 (죽은 상품 등록 방지) ──
        #   AI·등록 비용 소모 전에 확인. 판매종료(exists=False)=영구 스킵,
        #   품절(available=False)=이번 회차만(재입고 가능하므로 영구 제외 안 함).
        _pno = str(p.get("product_no") or "").strip()
        if _pno:
            try:
                from costco_crawler import fetch_costco_status as _fcs
                _cs = _fcs(_pno)
            except Exception:
                _cs = {}
            _ended = _cs.get("exists") is False
            _oos = _cs.get("available") is False
            if _ended or _oos:
                out["skipped_soldout"] += 1
                _rsn = _cs.get("reason") or ("판매종료" if _ended else "품절")
                out["results"].append({"상품명": name, "결과": "skip", "내용": f"코스트코 {_rsn}"})
                log(f"  ⏭ {name}: 코스트코 {_rsn} — 등록 안 함")
                if _ended:
                    _skip.add(_skey(p))
                    _skip_dirty = True
                continue

        # ── 여기부터 고비용(AI 카테고리·등록) → 회당 상한 적용 ──
        if processed >= max_count:
            log(f"회당 상한 {max_count} 도달 — 나머지는 다음 실행 또는 장바구니로")
            break
        processed += 1

        cat_id, cat_full, source = resolve_category(
            api_id, api_secret, p, cat_map=cat_map, ai_key=ai_key, open_creds=open_creds)
        if not cat_id:
            out["skipped_no_category"] += 1
            out["results"].append({"상품명": name, "결과": "skip", "내용": "카테고리 미해결 → 장바구니"})
            log(f"  ⏭ {name}: 카테고리 미해결 (수동 확인 필요)")
            continue

        origin_no, err = register_one(
            username, api_id, api_secret, p, cat_id,
            opts={"sale_price": sale, "as_tel": as_tel, "stock": stock,
                  "ai_key": ai_key if (gen_tags or optimize_name or ai_desc) else "",
                  "cat_full": cat_full or "",
                  "gen_tags": gen_tags, "optimize_name": optimize_name,
                  "ai_desc": ai_desc, "with_spec": with_spec})
        if err or not origin_no:
            out["fail"] += 1
            out["results"].append({"상품명": name, "결과": "fail", "내용": str(err)[:80]})
            log(f"  ❌ {name}: {str(err)[:80]}")
            # 구조적 거부(400: 권한·인증·유효성)는 재시도해도 실패 → 스킵 목록 등록
            _es = str(err or "")
            if any(t in _es for t in ("400", "권한", "인증", "유효하지")):
                _k = _skey(p)
                if _k:
                    _skip.add(_k)
                    _skip_dirty = True
        else:
            out["ok"] += 1
            out["results"].append({
                "상품명": name, "카테고리": (cat_full or "")[:20],
                "판매가": sale, "결과": "ok", "내용": f"#{origin_no} ({source})",
            })
            log(f"  ✅ {name} → #{origin_no} / {sale}원 / {cat_full or cat_id} ({source})")

    if _skip_dirty:
        try:
            set_setting(username, "auto_register_skip",
                        json.dumps(sorted(_skip), ensure_ascii=False))
            log(f"스킵 목록 갱신: {len(_skip)}개 (다음 실행부터 제외)")
        except Exception:
            pass

    out["processed"] = processed
    out["skip_total"] = len(_skip)
    return out
