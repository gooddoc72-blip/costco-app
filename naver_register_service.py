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

    # 추가 이미지 + 상세HTML (크롤링 저장분)
    extra_cdn = []
    detail_html = ""
    sid = product.get("shared_id")
    if sid:
        xraw, dhtml = get_product_detail(sid)
        detail_html = dhtml or ""
        if xraw:
            try:
                xlist = json.loads(xraw)
            except Exception:
                xlist = []
            if xlist:
                extra_cdn, _ = naver_api.upload_images_batch(api_id, api_secret, xlist)

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
    unreg = [p for p in merged if not str(p.get("naver_product_no") or "").strip()]
    log(f"미등록 후보 {len(unreg)}개 (전체 {len(merged)}개)")

    out = {
        "ok": 0, "fail": 0,
        "skipped_no_category": 0, "skipped_no_price": 0, "skipped_no_image": 0,
        "processed": 0, "results": [],
    }
    processed = 0

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
            opts={"sale_price": sale, "as_tel": as_tel, "stock": stock})
        if err or not origin_no:
            out["fail"] += 1
            out["results"].append({"상품명": name, "결과": "fail", "내용": str(err)[:80]})
            log(f"  ❌ {name}: {str(err)[:80]}")
        else:
            out["ok"] += 1
            out["results"].append({
                "상품명": name, "카테고리": (cat_full or "")[:20],
                "판매가": sale, "결과": "ok", "내용": f"#{origin_no} ({source})",
            })
            log(f"  ✅ {name} → #{origin_no} / {sale}원 / {cat_full or cat_id} ({source})")

    out["processed"] = processed
    return out
