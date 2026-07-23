"""
네이버 스마트스토어 자동 등록 공용 코어 (UI/헤드리스 공용, Streamlit 비의존)

코스트코에서 크롤링해 shared_products에 저장된 상품을 네이버에 등록하는
핵심 로직을 한 곳에 모은다. naver_register_page.py(UI)와 auto_task.py(무인)가
같은 함수를 호출해 중복을 없앤다.

- compute_sale_price : 코스트코가 → 마진·수수료 반영 판매가
- resolve_category   : 상품 → 네이버 리프카테고리ID (저장값→매핑→AI 순)
- register_one       : 단일 상품 등록 (이미지 업로드 + 상세 + register_product)
- auto_register      : 미등록 상품 일괄 자동 등록 (카테고리 미해결은 스킵 → 장바구니 회수)
"""
import os
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


def _noop(*_a, **_k):
    pass


# ─── 판매가 계산 ──────────────────────────────────────────────────
def compute_sale_price(product: dict, margin: float) -> int:
    """코스트코가(온라인가 우선) → 네이버 판매가.
    공식: 원가 ×(1+마진%) ÷0.945 (네이버 수수료 5.5% 그로스업) → 10원 단위 반올림.
    원가 없으면 0 반환."""
    cost = (
        int(product.get("online_price") or 0)
        or int(product.get("unit_price") or 0)
        or int(product.get("sale_price") or 0)
    )
    if cost <= 0:
        return 0
    return int(round(cost * (1 + margin / 100.0) / 0.945 / 10) * 10)


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

    _catf = opts.get("cat_full", "")

    # 상세설명: AI가 코스트코 내용 분석해 새로 작성(opt) — 지저분한 원본HTML 대신 깔끔한 문장
    _desc_block = ""
    if _ai and opts.get("ai_desc"):
        try:
            import ai_service, html as _h2
            _d, _ = ai_service.generate_description_from_costco(_ai, name, _raw_detail, _catf)
            if _d:
                _desc_block = ('<div style="font-size:17px;line-height:1.9;text-align:center;'
                               'padding:4px 16px 8px;color:#333">'
                               + _h2.escape(_d).replace("\n", "<br>") + '</div>')
        except Exception:
            _desc_block = ""

    # 한글표시사항: 코스트코 스펙 → '제품 상세정보' 표 + 제조자
    _spec_table = ""
    _manufacturer = ""
    if opts.get("with_spec"):
        try:
            import costco_crawler as _cc
            _spec = _cc.fetch_costco_spec(str(product.get("product_no") or "").strip())
        except Exception:
            _spec = {}
        if _spec:
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
                  log=None):
    """미등록 코스트코 상품을 자동 등록.
    - merged에서 naver_product_no 빈 상품만 대상.
    - 가격/이미지 없는 건은 비용 없이 스킵.
    - 카테고리 미해결 건은 등록하지 않고 스킵(미등록 유지 → UI 장바구니로 회수).
    - max_count: 회당 '처리(카테고리해결+등록)' 상한 (AI 호출·라이브 등록 폭주 방지).
    반환: dict{ok, fail, skipped_no_category, skipped_no_price, skipped_no_image, processed, results[]}."""
    log = log or _noop
    cat_map = cat_map or {}

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

    out = {
        "ok": 0, "fail": 0,
        "skipped_no_category": 0, "skipped_no_price": 0, "skipped_no_image": 0,
        "skipped_soldout": 0,
        "processed": 0, "results": [],
    }
    processed = 0
    _skip_dirty = False

    for p in unreg:
        name = (p.get("costco_name") or "")[:40]

        # ── 저비용 사전 스킵 (예산 미소모) ──
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
