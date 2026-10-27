# -*- coding: utf-8 -*-
"""반품·재고 장부 정리 (10/27 검증 결과) — 기본은 미리보기, 고른 번호만 적용.

    python fix_returns_cleanup.py                 # 미리보기 + 확인용 목록
    python fix_returns_cleanup.py --apply 1,2,3,4 # 고른 정리만 적용(적용 전 auth.db 백업)
    python fix_returns_cleanup.py --report        # 청구·재고 기록 없는 지난 발송건 CSV

정리마다 '지금 DB가 검증 때와 같은지'를 먼저 확인하고, 다르면 건너뛴다 —
그 사이 누가 화면에서 손댔으면 이 스크립트가 덮어쓰면 안 된다.

  1. 비첸향 반품 중복 등록 정리 — #11(중복) 삭제, #1을 '재고로 되돌림'으로, lot 31 단가 51,980→25,990·결제됨
  2. 비첸향 2개 tblue 교차판매(9/22) 청구·적립 — tblue 51,980원 청구, clglobal0919 51,980원 적립
  3. 양말 lot 53에서 잘못 깎인 1개 되돌림 — 반품#17 취소가 다른 매입분을 깎은 것(move 61)
  4. 반품 #7·#18·#19 상태 바로잡기 — 이미 재고 탭에서 매장반품·적립 끝남 → '매장 반품'으로 닫고 적립 연결
  5. (확인 후) clglobal0919 맛밤·분무기 매장반품 적립 109,920원 취소 — 그 재고(lot 40·41) 값을 낸 적이 없을 때만
"""
import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_core import AUTH_DB, DATA_DIR  # noqa: E402

BY = 'cleanup-1027'


def _c():
    c = sqlite3.connect(AUTH_DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _one(c, sql, args=()):
    r = c.execute(sql, args).fetchone()
    return dict(r) if r else None


def _today():
    return datetime.now().strftime('%Y-%m-%d')


# ── 정리 ─────────────────────────────────────────────────────
def fix1(apply):
    """비첸향 반품 #1/#11 — 같은 주문 2개를 두 번 등록. 실제 물건은 #1의 lot 31로 9/22 tblue에 나갔다."""
    c = _c()
    try:
        r1 = _one(c, "SELECT * FROM customer_return WHERE id=1")
        r11 = _one(c, "SELECT * FROM customer_return WHERE id=11")
        l31 = _one(c, "SELECT * FROM inventory_lots WHERE id=31")
        l46 = _one(c, "SELECT * FROM inventory_lots WHERE id=46")
        m11 = _one(c, "SELECT * FROM inventory_moves WHERE id=11")
        ok = (r1 and r11 and l31 and l46 and m11
              and r1['order_no'] == r11['order_no'] and r1['status'] == 'received'
              and r11['status'] == 'restocked' and int(l31['unit_cost']) == 51980
              and int(l46['qty_left']) == 0 and int(m11['lot_id']) == 31)
        print("[1] 비첸향 반품 중복(#1·#11) 정리")
        if not ok:
            print("    ⏭ 검증 때와 달라 건너뜀")
            return False
        print("    · 반품 #11 삭제(중복 등록 — 그 재고 lot 46은 이미 0)")
        print("    · 반품 #1 → '재고로 되돌림'(lot 31, 9/22 tblue 주문으로 나감) · 구입가 51,980→25,990")
        print("    · lot 31 단가 51,980→25,990, 결제됨 / 차감 move 11 단가 25,990")
        if apply:
            c.execute("DELETE FROM customer_return WHERE id=11")
            c.execute("UPDATE customer_return SET status='restocked', restock_owner='clglobal0919', "
                      "restock_lot_id=31, unit_cost=25990, done_by=?, done_at=?, "
                      "memo=COALESCE(memo,'') || ' / 10/27 정리: #11 중복 삭제, 단가 정정' WHERE id=1",
                      (BY, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            c.execute("UPDATE inventory_lots SET unit_cost=25990, paid=1, "
                      "memo=memo || ' / 10/27 단가 정정(51,980은 2개 값)' WHERE id=31")
            c.execute("UPDATE inventory_moves SET unit_cost=25990 WHERE id=11")
            c.commit()
            print("    ✅ 적용")
        return True
    finally:
        c.close()


def fix2(apply, settle_date):
    """9/22 tblue 주문 2026092013437421 — clglobal 재고(lot 31) 2개로 나갔는데 청구·적립이 없다."""
    ono = '2026092013437421'
    c = _c()
    try:
        m = _one(c, "SELECT * FROM inventory_moves WHERE id=11")
        billed = _one(c, "SELECT COUNT(*) n FROM settle_item WHERE order_no=?", (ono,))['n']
        credited = _one(c, "SELECT COALESCE(SUM(amount),0) s FROM deposit_ledger WHERE kind='stock_credit' "
                           "AND memo LIKE ?", (f"%[주문 {ono}]%",))['s']
    finally:
        c.close()
    print(f"[2] 비첸향 2개 tblue 교차판매(9/22) 청구·적립 — {settle_date} 청구서")
    if not (m and m['order_no'] == ono and m['settle_status'] == 'PENDING' and not billed and not credited
            and int(m['qty']) == 2):
        print("    ⏭ 검증 때와 달라 건너뜀(이미 정산됐거나 바뀜)")
        return False
    amt = 25990 * 2      # 정상가 재고(정가 기록 없음) — 지금 규칙으로 웃돈 없음
    print(f"    · tblue 청구 {amt:,}원(25,990×2) · clglobal0919 예치금 적립 {amt:,}원 · 정산 장부 건 닫음")
    if apply:
        import db_settle as ds
        import db_deposit as dep
        ds.merge_items(settle_date, 'tblue', [{
            'order_no': ono, 'product_no': '679151', 'product_name': '비첸향 골든코인 선물세트 (9/22 발송분)',
            'qty': 2, 'split_qty': 1, 'pack': 1, 'unit_price': amt, 'amount': amt,
            'source': 'stock', 'receipt_date': '2026-09-22', 'stock_owner': 'clglobal0919',
            'memo': '9/22 clglobal0919 재고(고객반품분)로 나간 건 — 10/27 정리로 청구'}], created_by=BY)
        dep.stock_credit('clglobal0919', amt, ono,
                         '재고 판매 적립 · 비첸향 골든코인 2개 · 판매자 tblue · 9/22 발송분(10/27 정리)',
                         settle_date=settle_date, by=BY)
        c = _c()
        c.execute("UPDATE inventory_moves SET surcharge=0, settle_status='SETTLED', settled_at=? WHERE id=11",
                  (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
        c.commit()
        c.close()
        print("    ✅ 적용")
    return True


def fix3(apply):
    """반품 #17 취소(10/23)가 반품 재고 lot 49 대신 다른 매입분 lot 53(양말)에서 1개를 깎았다."""
    c = _c()
    try:
        m = _one(c, "SELECT * FROM inventory_moves WHERE id=61")
        print("[3] 양말 lot 53에서 잘못 깎인 1개 되돌림(move 61)")
        if not (m and int(m['lot_id']) == 53 and m['platform'] == 'adjust' and int(m['qty']) == 1):
            print("    ⏭ 검증 때와 달라 건너뜀")
            return False
        l53 = _one(c, "SELECT qty_left FROM inventory_lots WHERE id=53")
        print(f"    · lot 53 남은 수량 {l53['qty_left']} → {int(l53['qty_left']) + 1}, 차감 기록 move 61 삭제")
        if apply:
            c.execute("UPDATE inventory_lots SET qty_left=qty_left+1 WHERE id=53")
            c.execute("DELETE FROM inventory_moves WHERE id=61")
            c.commit()
            print("    ✅ 적용")
        return True
    finally:
        c.close()


def fix4(apply):
    """#7·#18·#19 — 재고 탭에서 매장반품·적립까지 끝났는데 반품 장부엔 '재고로 되돌림'."""
    plan = [(7, 47, [111, 112, 113]), (18, 48, [110]), (19, 50, [109])]
    c = _c()
    print("[4] 반품 #7·#18·#19 → '매장 반품'으로 닫고 적립 연결")
    try:
        todo = []
        for rid, lot, deps in plan:
            r = _one(c, "SELECT * FROM customer_return WHERE id=?", (rid,))
            l = _one(c, "SELECT qty_left FROM inventory_lots WHERE id=?", (lot,))
            d = [x for x in (_one(c, "SELECT * FROM deposit_ledger WHERE id=?", (i,)) for i in deps) if x]
            if not (r and r['status'] == 'restocked' and l and int(l['qty_left']) == 0
                    and len(d) == len(deps) and all(x['kind'] == 'return_credit' for x in d)):
                print(f"    ⏭ #{rid} 검증 때와 달라 건너뜀")
                continue
            amt = sum(int(x['amount']) for x in d)
            print(f"    · #{rid} {r['product_name'][:24]} → 매장 반품 · 적립 {amt:,}원(원장 {deps}) · lot {lot}")
            todo.append((rid, lot, deps[0], amt))
        if apply and todo:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for rid, lot, did, amt in todo:
                c.execute("UPDATE customer_return SET status='store_returned', restock_lot_id=?, refund_amount=?, "
                          "deposit_id=?, deposit_amount=?, deposit_at=?, done_by=?, done_at=?, "
                          "memo=COALESCE(memo,'') || ' / 10/27 정리: 재고 탭 매장반품과 연결' WHERE id=?",
                          (lot, amt, did, amt, now, BY, now, rid))
            c.commit()
            print("    ✅ 적용")
        return bool(todo)
    finally:
        c.close()


def fix5(apply):
    """lot 40·41(맛밤 6·분무기 5)은 결제 안 된 관리자 수동 입고분인데 매장반품하며 clglobal에 적립됐다."""
    c = _c()
    try:
        d = [_one(c, "SELECT * FROM deposit_ledger WHERE id=?", (i,)) for i in (81, 82, 83, 84)]
        bills = _one(c, "SELECT COUNT(*) n FROM settle_item WHERE order_no IN ('STOCKIN-40','STOCKIN-41')")['n']
        undone = _one(c, "SELECT COUNT(*) n FROM deposit_ledger WHERE username='clglobal0919' AND memo LIKE '%10/27 정리: 맛밤·분무기%'")['n']
    finally:
        c.close()
    print("[5] (확인 후) clglobal0919 맛밤·분무기 매장반품 적립 109,920원 취소")
    if not (all(d) and sum(int(x['amount']) for x in d) == 109920 and not bills and not undone):
        print("    ⏭ 검증 때와 달라 건너뜀(이미 취소했거나 입고 청구가 있음)")
        return False
    print("    · 그 재고는 입고 청구(STOCKIN)도 판매 청구도 없었다 — clglobal0919가 따로 값을 냈다면 적용하지 마세요")
    print("    · 적용하면 예치금에 −109,920원 줄(적립 취소)을 남깁니다(원래 줄은 지우지 않음)")
    if apply:
        import db_deposit as dep
        dep.return_credit_cancel('clglobal0919', 109920,
                                 '매장반품 적립 취소 · 10/27 정리: 맛밤·분무기(lot 40·41)는 결제 안 된 재고 — '
                                 '적립 81~84 되돌림', by=BY)
        print("    ✅ 적용")
    return True


def notes():
    """사람이 확인할 것 — 스크립트가 고치지 않는다."""
    c = _c()
    try:
        print("\n── 확인만 하세요(자동으로 고치지 않음) ──")
        print("· 반품 #4 청정원 선물세트 — 구입가 0원·상품번호 없음인데 39,980원 적립. 실제 청구액 확인 필요")
        print("· 반품 #12 가르시니아 — 11,490원 적립. 그 주문 청구가 8,990원이었으면 2,500원 과다")
        print("· 반품 #16 쿠키 — 구입가·상품번호 비어 있음(적립 없음)")
        for r in c.execute("SELECT * FROM receipt_return WHERE status='requested'"):
            print(f"· 영수증 반품요청 #{r['id']} {r['product_name']} {int(r['amount']):,}원 — {r['created_at'][:10]}부터 '요청' 상태")
        rows = list(c.execute("SELECT id, owner, product_name, unit_cost, qty_left, received_at, memo FROM inventory_lots "
                              "WHERE status='ACTIVE' AND qty_left>0 AND COALESCE(paid,0)=0"))
        if rows:
            print(f"· 결제 안 된 재고 {len(rows)}건 — 나갈 때 청구되는 예전 방식. 이미 냈으면 결제됨으로 바꿔야 이중 청구가 없음:")
            for r in rows:
                print(f"    lot {r['id']} {r['owner']} {r['product_name'][:20]} {r['qty_left']}개 × {int(r['unit_cost']):,} ({r['received_at']}) {r['memo'][:30]}")
    finally:
        c.close()


def report():
    """지난 14일 발송 중 청구도 재고 차감 기록도 없는 주문 → CSV."""
    import io
    import contextlib
    import receipt_settle as rs
    with contextlib.redirect_stdout(io.StringIO()):
        cand = rs.carryover_unsettled(_today(), days=14)
    c = _c()
    out = []
    try:
        for o in cand:
            if c.execute("SELECT 1 FROM inventory_moves WHERE order_no=?", (o['order_no'],)).fetchone():
                continue
            own = c.execute("SELECT COALESCE(SUM(qty_left),0) FROM inventory_lots WHERE owner=? AND product_no=? "
                            "AND status='ACTIVE'", (o['username'], str(o.get('costco_no') or ''))).fetchone()[0]
            out.append({'판매자': o['username'], '발송일': o.get('carried_from', ''), '주문번호': o['order_no'],
                        '상품명': o.get('product_name', ''), '수량': o.get('qty', 1),
                        '코스트코번호': o.get('costco_no', ''), '본인 재고 장부 잔량': own})
    finally:
        c.close()
    path = os.path.join(DATA_DIR, f"report_unbilled_{_today()}.csv")
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()) if out else ['판매자'])
        w.writeheader()
        w.writerows(out)
    by = {}
    for r in out:
        by[r['판매자']] = by.get(r['판매자'], 0) + 1
    print(f"청구·재고 기록 없는 지난 발송 {len(out)}건 → {path}")
    for u, n in sorted(by.items(), key=lambda x: -x[1]):
        print(f"  {u}: {n}건")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', default='', help='적용할 번호, 예: 1,2,3,4')
    ap.add_argument('--date', default=_today(), help='2번 청구를 넣을 청구서 날짜(기본 오늘)')
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    if a.report:
        report()
        return
    pick = {int(x) for x in a.apply.split(',') if x.strip().isdigit()}
    if pick:
        bak = os.path.join(DATA_DIR, f"backup_auth_returns_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        s = sqlite3.connect(AUTH_DB)
        d = sqlite3.connect(bak)
        s.backup(d)
        d.close()
        s.close()
        print(f"백업: {bak}\n")
    import db_customer_return as _cr
    _cr.ensure()               # restock_lot_id 칸
    fix1(1 in pick)
    fix2(2 in pick, a.date)
    fix3(3 in pick)
    fix4(4 in pick)
    fix5(5 in pick)
    notes()
    if not pick:
        print("\n미리보기입니다. 적용: python fix_returns_cleanup.py --apply 1,2,3,4  (5는 확인 후)")


if __name__ == '__main__':
    main()
