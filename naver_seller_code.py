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


def is_store_costco_pno(costco_no: str) -> tuple[bool, str]:
    """이 번호가 공유DB에서 **매장 상품**으로 확인되는가.

    네이버 판매자 상품코드에는 코스트코 **매장** 번호가 들어가야 한다. 온라인몰
    번호는 카톤 단위라 매입원가와도, 영수증과도 맞지 않는다.
    공유DB 기준으로 store_price>0 이면 매장 상품으로 본다.
    반환: (ok, 사유)
    """
    from db import get_shared_products
    for sp in (get_shared_products() or []):
        if str(sp.get("product_no") or "").strip() == str(costco_no).strip():
            if int(sp.get("store_price") or 0) > 0:
                return True, f"매장 {int(sp['store_price']):,}원 ({sp.get('costco_name', '')[:20]})"
            return False, f"공유DB에 온라인 전용({sp.get('price_type')}) — 매장가 없음"
    return False, "공유DB에 없는 번호"


def read_remote_seller_code(client_id: str, client_secret: str, naver_no: str) -> tuple[str, str]:
    """네이버에 현재 들어있는 판매자 상품코드를 읽는다.

    반환: (code, err) — code는 없으면 ''. err가 있으면 조회 실패.
    밀어넣기 전 반드시 확인해야 한다. 우리 DB의 코스트코 번호가 틀린 경우가 있고
    (실측 35건 중 8건), 그대로 밀면 네이버에 있던 올바른 코드를 덮어쓴다.
    """
    import requests
    from naver_api import get_token
    from naver_api.products import resolve_origin_product_no

    token, err = get_token(client_id, client_secret)
    if not token:
        return "", (err or "토큰 발급 실패")
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://api.commerce.naver.com/external/v2/products/origin-products/"
    try:
        r = requests.get(url + str(naver_no), headers=headers, timeout=15)
        if r.status_code in (403, 404):
            origin, _e = resolve_origin_product_no(client_id, client_secret, naver_no)
            if origin:
                r = requests.get(url + str(origin), headers=headers, timeout=15)
        if r.status_code != 200:
            return "", f"조회 실패({r.status_code})"
        origin_product = r.json().get("originProduct") or {}
        detail = origin_product.get("detailAttribute") or {}
        code = (detail.get("sellerCodeInfo") or {}).get("sellerManagementCode") or ""
        return str(code).strip(), ""
    except Exception as e:
        return "", f"조회 예외: {e}"


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
                      only_missing: bool = True,
                      on_conflict: str = "skip") -> dict:
    """코스트코 번호를 네이버 판매자 상품코드에 밀어넣는다.

    limit: 한 번에 처리할 최대 건수 (API 부하·중단 대비). None이면 전량.
    dry_run: 호출하지 않고 대상만 집계.
    on_conflict: 네이버에 이미 **다른** 코드가 있을 때
        'skip'      기본 — 건드리지 않고 충돌로 보고한다. 우리 DB 번호가
                    틀린 사례가 실측 확인됐고, 덮어쓰면 맞는 코드가 사라진다.
        'overwrite_if_store' 우리 DB 번호가 공유DB에서 매장 상품으로 확인될 때만
                    덮어쓴다. 네이버에 온라인 번호가 들어간 걸 매장 번호로 바로잡는
                    안전한 경로 — DB 번호가 틀린 건(공유DB에 없거나 온라인)은 보류한다.
        'overwrite' 검증 없이 우리 DB 값으로 덮어쓴다.
    반환: {ok, failed, skipped, same, conflicts[], stats, errors[], done[]}
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
        return {"ok": 0, "failed": 0, "skipped": 0, "same": 0, "conflicts": [],
                "stats": st, "errors": [], "done": [], "dry_run": True, "targets": targets}

    if not (client_id and client_secret):
        return {"ok": 0, "failed": 0, "skipped": len(targets), "same": 0, "conflicts": [],
                "stats": st, "errors": ["네이버 커머스 API 키가 없습니다 (설정 탭에서 등록)."],
                "done": []}

    if limit:
        targets = targets[:int(limit)]

    ok = failed = same = 0
    errors, done, conflicts = [], [], []
    for i, t in enumerate(targets, 1):
        # 안전망 — build_targets가 이미 걸렀지만 값이 흘러들어오는 경로가 늘 수 있다
        if not is_costco_pno(t["costco_no"]):
            failed += 1
            errors.append(f"{t['naver_no']}: 코스트코번호 형식 아님({t['costco_no']})")
            continue
        # ── 사전 확인: 네이버 현재 값을 먼저 읽는다 ──
        cur, rerr = read_remote_seller_code(client_id, client_secret, t["naver_no"])
        if rerr:
            failed += 1
            errors.append(f"{t['naver_no']}: {rerr}")
            _log(f"  [{i}/{len(targets)}] ⚠️ {t['naver_no']} — {rerr}")
            if i < len(targets) and delay:
                time.sleep(delay)
            continue
        if cur == t["costco_no"]:
            # 이미 맞는 값 → PUT 없이 기록만 (불필요한 상품 수정 방지)
            _mark_synced(username, t["id"], t["costco_no"])
            same += 1
            _log(f"  [{i}/{len(targets)}] ⏭ {t['naver_no']} 이미 {cur} — 건너뜀")
            if i < len(targets) and delay:
                time.sleep(delay)
            continue
        if cur and on_conflict != "overwrite":
            allow, why = (False, "")
            if on_conflict == "overwrite_if_store":
                allow, why = is_store_costco_pno(t["costco_no"])
            if not allow:
                conflicts.append({**t, "remote": cur, "reason": why})
                _log(f"  [{i}/{len(targets)}] ⚠️ {t['naver_no']} 충돌 — "
                     f"네이버 {cur} vs DB {t['costco_no']} → 보류"
                     + (f" ({why})" if why else ""))
                if i < len(targets) and delay:
                    time.sleep(delay)
                continue
            _log(f"  [{i}/{len(targets)}] 🔁 {t['naver_no']} 교체 — "
                 f"네이버 {cur}(온라인) → DB {t['costco_no']} [{why}]")
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

    _log(f"완료 — 입력 {ok} / 이미맞음 {same} / 충돌 {len(conflicts)} / 실패 {failed} "
         f"(남은 대상 {max(0, st['ready'] - len(targets))})")
    return {"ok": ok, "failed": failed, "skipped": 0, "same": same,
            "conflicts": conflicts, "stats": st, "errors": errors, "done": done,
            "remaining": max(0, st["ready"] - len(targets))}
