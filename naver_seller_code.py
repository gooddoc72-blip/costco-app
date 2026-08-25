# -*- coding: utf-8 -*-
"""네이버 판매자 상품코드에 코스트코 상품번호 일괄 입력.

왜 필요한가:
  주문 API는 '판매자 상품코드'(sellerManagementCode)를 그대로 돌려준다.
  여기에 코스트코 번호가 들어 있으면 주문 → 코스트코 번호가 **매칭 없이** 확정된다.
  지금은 이 필드가 전 주문에서 공란이라, 상품명 유사도로 추측하고 있고 그게
  영수증 매칭 오류의 근원이다.

신규 등록은 이미 seller_code를 넣고 있다(naver_register_service).
이 모듈은 **이미 등록된 상품**에 소급 적용하는 실행부다.

안전 원칙:
  · 코스트코 번호는 4~7자리만 인정 (services.is_costco_pno). 네이버 ID(10~11자리)를
    판매자 상품코드에 넣으면 아무 의미가 없고 나중에 구분도 안 된다.
  · 같은 값을 두 번 밀지 않는다 (products.seller_code_synced 기록).
  · update_product_full은 GET→필드교체→PUT이라 나머지 상품 정보를 보존한다.
  · 상품당 GET+PUT 2회 호출이라 반드시 간격을 둔다. 실패는 건너뛰고 계속한다.
"""
from datetime import datetime

from db import get_user_db, get_all_products
from services import costco_pno_of, is_costco_pno

#: 네이버 커머스 API 호출 간격(초). 상품당 GET+PUT 2회라 너무 빠르면 429가 난다.
DEFAULT_DELAY = 0.6


def _naver_no(p: dict) -> str:
    """수정 대상 네이버 번호 — origin 우선, 없으면 channel.

    update_product_full이 channel번호를 받으면 origin으로 변환해 재시도하므로
    둘 중 아무거나 있으면 된다.
    """
    for k in ("naver_origin_pno", "naver_channel_pno"):
        v = str(p.get(k) or "").strip()
        if v:
            return v
    return ""


def build_targets(username: str, only_missing: bool = True) -> tuple[list, dict]:
    """밀어넣을 대상 목록을 만든다.

    Returns: (targets, stats)
      targets: [{id, naver_no, costco_no, name}]
      stats:   {total, no_naver, no_costco, already, ready}
    """
    prods = get_all_products(username) or []
    targets = []
    st = {"total": len(prods), "no_naver": 0, "no_costco": 0, "already": 0, "ready": 0}
    for p in prods:
        nv = _naver_no(p)
        if not nv:
            st["no_naver"] += 1
            continue
        cno = costco_pno_of(p)
        if not cno:
            st["no_costco"] += 1
            continue
        if only_missing and str(p.get("seller_code_synced") or "").strip() == cno:
            st["already"] += 1
            continue
        targets.append({
            "id": p.get("id"),
            "naver_no": nv,
            "costco_no": cno,
            "name": str(p.get("costco_name") or p.get("store_product_name") or "")[:40],
        })
    st["ready"] = len(targets)
    return targets, st


def _mark_synced(username: str, product_id, costco_no: str):
    conn = get_user_db(username)
    try:
        conn.execute(
            "UPDATE products SET seller_code_synced=?, seller_code_synced_at=? WHERE id=?",
            (costco_no, datetime.now().strftime("%Y-%m-%d %H:%M"), product_id))
        conn.commit()
    finally:
        conn.close()


def push_seller_codes(username: str, client_id: str, client_secret: str,
                      limit: int = 50, dry_run: bool = False,
                      delay: float = DEFAULT_DELAY, progress=None,
                      only_missing: bool = True) -> dict:
    """코스트코 번호를 네이버 판매자 상품코드에 밀어넣는다.

    limit: 한 번에 처리할 최대 건수 (API 부하·중단 대비). None이면 전량.
    dry_run: 호출하지 않고 대상만 집계.
    반환: {ok, failed, skipped, stats, errors[], done[]}
    """
    import time
    from naver_api.products import update_product_full

    def _log(m):
        if progress:
            progress(m)

    targets, st = build_targets(username, only_missing=only_missing)
    _log(f"대상 집계 — 전체 {st['total']}개 / 네이버번호 없음 {st['no_naver']} / "
         f"코스트코번호 없음 {st['no_costco']} / 이미 반영 {st['already']} → 처리대상 {st['ready']}")

    if dry_run:
        return {"ok": 0, "failed": 0, "skipped": 0, "stats": st,
                "errors": [], "done": [], "dry_run": True, "targets": targets}

    if not (client_id and client_secret):
        return {"ok": 0, "failed": 0, "skipped": len(targets), "stats": st,
                "errors": ["네이버 커머스 API 키가 없습니다 (설정 탭에서 등록)."], "done": []}

    if limit:
        targets = targets[:int(limit)]

    ok = failed = 0
    errors, done = [], []
    for i, t in enumerate(targets, 1):
        # 안전망 — build_targets가 이미 걸렀지만 값이 흘러들어오는 경로가 늘 수 있다
        if not is_costco_pno(t["costco_no"]):
            failed += 1
            errors.append(f"{t['naver_no']}: 코스트코번호 형식 아님({t['costco_no']})")
            continue
        try:
            success, err, used = update_product_full(
                client_id, client_secret, t["naver_no"], {"seller_code": t["costco_no"]})
        except Exception as e:
            success, err, used = False, f"예외: {e}", None
        if success:
            _mark_synced(username, t["id"], t["costco_no"])
            ok += 1
            done.append({**t, "origin_no": used})
            _log(f"  [{i}/{len(targets)}] ✅ {t['naver_no']} → {t['costco_no']}  {t['name']}")
        else:
            failed += 1
            errors.append(f"{t['naver_no']}({t['costco_no']}): {err}")
            _log(f"  [{i}/{len(targets)}] ❌ {t['naver_no']} — {err}")
        if i < len(targets) and delay:
            time.sleep(delay)

    _log(f"완료 — 성공 {ok} / 실패 {failed} (남은 대상 {max(0, st['ready'] - len(targets))})")
    return {"ok": ok, "failed": failed, "skipped": 0, "stats": st,
            "errors": errors, "done": done, "remaining": max(0, st["ready"] - len(targets))}
