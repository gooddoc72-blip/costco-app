"""쿠팡 판매가 일괄 재가격 — 네이버와 분리된 쿠팡 전용 모듈.

플랫폼 구분(분리):
  · 네이버 = naver_register_service.reprice_registered
      우리가 등록 → 전체 리스팅+상품번호 보유 → get_product_list로 현재가 일괄조회.
  · 쿠팡   = coupang_reprice.reprice_coupang  (이 모듈)
      주문/발송/정산만 수집 → 주문번호 'orderId-vendorItemId'의 vendorItemId 기반.
      현재가는 vendorItem별로 쿠팡 API(get_coupang_item_price) 조회.

공통(공유): 가격 공식 pricing.compute_sale_price / 단품원가 pricing.unit_cost.
쿠팡 API: coupang_api.get_coupang_item_price(현재가), coupang_api.update_coupang_price(변경).
안전 가드는 네이버와 동일(인상만·인상비율 상한·split_qty 반영) — 카톤가 이상치 참사 방지.
"""
import coupang_api
from pricing import compute_sale_price, unit_cost, DEFAULT_IMPORT_SHIPPING
from db import get_all_products, get_shared_products, get_user_db, get_setting
from services import match_product_to_db


def _noop(*_a, **_k):
    pass


def coupang_products_from_orders(username):
    """사용자 주문에서 쿠팡 상품 추출.
    주문번호 'orderId-vendorItemId'(대시 포함=쿠팡) → {vendorItemId: {'name': 상품명}}.
    최근(rowid DESC) 우선, vendorItemId 기준 중복 제거."""
    conn = get_user_db(username)
    out = {}
    for tbl in ("dispatch_log", "order_history", "daily_orders"):
        try:
            rows = conn.execute(
                "SELECT order_no, product_name FROM %s ORDER BY rowid DESC" % tbl
            ).fetchall()
        except Exception:
            rows = []
        for r in rows:
            ono = str(r[0] or "")
            if "-" not in ono:
                continue
            vi = ono.split("-", 1)[1].strip()
            if not (vi.isdigit() and len(vi) >= 6):   # vendorItemId = 긴 숫자
                continue
            if vi not in out:
                out[vi] = {"name": str(r[1] or "")}
    conn.close()
    return out


def reprice_coupang(username, *, margin=10, dry_run=True, max_ratio=2.0,
                    max_count=0, log=None):
    """쿠팡 등록상품(주문 이력의 vendorItemId)을 '단품원가+택배3000+마진'으로 재가격.

    안전 가드(네이버 reprice_registered와 동일):
      · unit_cost(split_qty 반영), 인상만(신규>현재), 인상비율 상한(신규≤현재×max_ratio),
        현재가 조회 실패/0·상품 매칭 실패 스킵. over_cap(상한초과)=수동확인으로 분리(미적용).
    현재가는 vendorItem별 쿠팡 API 조회. 적용은 update_coupang_price(forceSalePriceUpdate).

    dry_run=True: 실제 변경 없이 변경안만.  max_count: 실제변경 상한(0=무제한).
    반환 dict{targeted, updated, failed, ok_or_lower, over_cap, no_match, no_cost,
             no_price, query_fail, results[], over_cap_samples[]}."""
    log = log or _noop
    ak = (get_setting(username, "coupang_access_key") or "").strip()
    sk = (get_setting(username, "coupang_secret_key") or "").strip()
    vd = (get_setting(username, "coupang_vendor_id") or "").strip()
    if not (ak and sk and vd):
        return {"error": "쿠팡 API 키(access/secret/vendor) 미설정",
                "targeted": 0, "updated": 0, "failed": 0}

    products = coupang_products_from_orders(username)
    uprods = get_all_products(username)
    sprods = get_shared_products()
    _memo = {}

    def _match(nm):
        if nm not in _memo:
            _memo[nm] = match_product_to_db(username, nm, product_no="",
                                            _user_prods=uprods, _shared_prods=sprods)
        return _memo[nm]

    out = {"targeted": 0, "updated": 0, "failed": 0, "ok_or_lower": 0, "over_cap": 0,
           "no_match": 0, "no_cost": 0, "no_price": 0, "query_fail": 0,
           "results": [], "over_cap_samples": [], "candidates": len(products)}
    _n = 0
    for vi, meta in products.items():
        name = meta.get("name", "")
        p = _match(name)
        if not p:
            out["no_match"] += 1
            continue
        ucost = unit_cost(p)
        if ucost <= 0:
            out["no_cost"] += 1
            continue
        new_price = compute_sale_price({"online_price": ucost}, margin)
        info, err = coupang_api.get_coupang_item_price(ak, sk, vd, vi)
        if err or not info:
            out["query_fail"] += 1
            continue
        cur_price = int(info.get("salePrice") or 0)
        if cur_price <= 0:
            out["no_price"] += 1
            continue
        if new_price <= cur_price:
            out["ok_or_lower"] += 1
            continue
        rec = {"name": name[:40], "vendor_item_id": vi, "unit_cost": ucost,
               "current": cur_price, "new": new_price, "diff": new_price - cur_price,
               "ratio": round(new_price / cur_price, 2)}
        if new_price > cur_price * max_ratio:   # 카톤가/이상치 의심 → 수동확인, 미적용
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
        ok, err = coupang_api.update_coupang_price(ak, sk, vd, vi, new_price)
        if ok:
            out["updated"] += 1
            log(f"  ✅ {name[:30]}: {cur_price:,} → {new_price:,}")
        else:
            out["failed"] += 1
            rec["err"] = str(err)[:80]
            out["results"].append(rec)
            log(f"  ❌ {name[:30]}: {str(err)[:60]}")
    return out
