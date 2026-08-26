# -*- coding: utf-8 -*-
"""사용자 제품DB의 빈 코스트코 상품번호를 공유 카탈로그에서 채운다.

왜 필요한가:
  판매자 상품코드 입력도, 영수증 매칭도 전부 '코스트코 번호를 아느냐'에 걸린다.
  그런데 실측상 4,024개 상품 중 번호가 있는 건 176개(4.4%)뿐이다.
  공유 카탈로그에는 3,361개가 있으니, 이름으로 이어붙이면 대부분 채울 수 있다.

세 가지를 반드시 지킨다:
  1) **매장 상품만** (store_price>0). 코스트코는 같은 상품이라도 매장과 온라인의
     번호가 다르다(Bouchard 초콜릿: 매장 673870 vs 온라인 696900). 영수증에 찍히는
     건 매장 번호이므로, 온라인 번호를 채우면 영수증과 영영 안 맞는다.
  2) **4~7자리 검증** (is_costco_pno). 공유 카탈로그 자체에 네이버 ID가 섞여
     들어간 오염분이 87건 있다.
  3) **높은 확신만** (기본 0.9). 틀린 번호는 빈칸보다 나쁘다 — 매입원가와
     판매자 상품코드로 그대로 흘러가기 때문이다.

매입가(unit_price)는 **비어 있을 때만** 채운다. 사용자가 자기 영수증으로 넣은
값이 있으면 그게 권위다.
"""
from datetime import datetime

from db import get_user_db, get_all_products, get_shared_products
from services import match_shared_product, is_costco_pno

#: 자동 채움 기준 점수. 0.9 미만은 사람이 봐야 한다.
DEFAULT_MIN_SCORE = 0.9


def build_fill_plan(username: str, min_score: float = DEFAULT_MIN_SCORE,
                    store_only: bool = True) -> tuple[list, dict]:
    """채울 목록과 집계를 만든다. DB를 바꾸지 않는다.

    Returns: (plan, stats)
      plan: [{id, name, costco_no, score, price, shared_name}]
    """
    prods = get_all_products(username) or []
    shared = get_shared_products() or []
    plan = []
    st = {"total": len(prods), "already": 0, "no_match": 0,
          "low_score": 0, "online_only": 0, "bad_pno": 0, "ready": 0}

    for p in prods:
        if is_costco_pno(p.get("product_no")):
            st["already"] += 1
            continue
        name = (p.get("store_product_name") or p.get("costco_name") or "").strip()
        if not name:
            st["no_match"] += 1
            continue
        m, score = match_shared_product(name, return_score=True, _shared_prods=shared)
        if not m:
            st["no_match"] += 1
            continue
        if score < min_score:
            st["low_score"] += 1
            continue
        store_price = int(m.get("store_price") or 0)
        if store_only and store_price <= 0:
            # 온라인 전용 — 번호 체계가 매장과 달라 영수증과 안 맞는다
            st["online_only"] += 1
            continue
        cno = str(m.get("product_no") or "").strip()
        if not is_costco_pno(cno):
            st["bad_pno"] += 1
            continue
        plan.append({
            "id": p.get("id"),
            "name": name[:46],
            "costco_no": cno,
            "score": round(float(score), 3),
            "price": store_price,
            "shared_name": str(m.get("costco_name") or "")[:34],
            "cur_unit_price": int(p.get("unit_price") or 0),
        })
    st["ready"] = len(plan)
    return plan, st


def fill_costco_numbers(username: str, min_score: float = DEFAULT_MIN_SCORE,
                        store_only: bool = True, dry_run: bool = False,
                        limit: int = None, progress=None) -> dict:
    """계획을 세우고 products.product_no를 채운다.

    매입가는 현재 0일 때만 매장 실단가로 함께 채운다.
    반환: {filled, price_filled, stats, plan}
    """
    def _log(m):
        if progress:
            progress(m)

    plan, st = build_fill_plan(username, min_score=min_score, store_only=store_only)
    _log(f"{username} — 전체 {st['total']} / 이미보유 {st['already']} / 매칭없음 {st['no_match']} / "
         f"점수미달 {st['low_score']} / 온라인전용 {st['online_only']} / 번호오염 {st['bad_pno']} "
         f"→ 채울 대상 {st['ready']}")

    if dry_run:
        return {"filled": 0, "price_filled": 0, "stats": st, "plan": plan, "dry_run": True}
    if limit:
        plan = plan[:int(limit)]
    if not plan:
        return {"filled": 0, "price_filled": 0, "stats": st, "plan": []}

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_user_db(username)
    filled = price_filled = 0
    try:
        for x in plan:
            if x["cur_unit_price"] <= 0 and x["price"] > 0:
                conn.execute(
                    "UPDATE products SET product_no=?, unit_price=?, updated_at=? WHERE id=?",
                    (x["costco_no"], x["price"], now, x["id"]))
                price_filled += 1
            else:
                conn.execute(
                    "UPDATE products SET product_no=?, updated_at=? WHERE id=?",
                    (x["costco_no"], now, x["id"]))
            filled += 1
        conn.commit()
    finally:
        conn.close()

    _log(f"  ✅ 번호 채움 {filled}건 (그 중 매입가 동시 반영 {price_filled}건)")
    return {"filled": filled, "price_filled": price_filled, "stats": st, "plan": plan}
