# -*- coding: utf-8 -*-
"""직접구매 계정에 잘못 잡힌 청구를 지운다.

대행 대상이 아닌 계정(본인이 매장에서 사는 계정)이 정산에 섞여 들어가면
사지도 않은 물건이 청구된다. 그 기록을 지우고, 주문에 덮어썼던 구입가도
원래 값으로 되돌린다 — 청구만 지우고 구입가를 두면 수익계산이 계속 틀린다.

사용: python remove_user_charges.py --user tblue [--apply]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

APPLY = '--apply' in sys.argv
USER = ''
for i, a in enumerate(sys.argv):
    if a == '--user' and i + 1 < len(sys.argv):
        USER = sys.argv[i + 1]
if not USER:
    print("사용: python remove_user_charges.py --user <계정> [--apply]")
    sys.exit(1)

import sqlite3
from db_receipt_settle import _conn, _ensure, _recompute_batches
from db_core import get_user_db

conn = _conn()
_ensure(conn)
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute(
    "SELECT * FROM receipt_settle_items WHERE username=?", (USER,))]
print("%s · 정산행 %d건 · 합계 %s원 · %s" % (
    USER, len(rows), format(sum(int(r['amount'] or 0) for r in rows), ','),
    "적용" if APPLY else "미리보기"))
for r in rows[:20]:
    print("  %s  %-34s %8s원  (기존구입가 %s)" % (
        r['order_date'], str(r['product_name'])[:34],
        format(int(r['amount'] or 0), ','), format(int(r['prev_cost'] or 0), ',')))

snaps = [dict(r) for r in conn.execute(
    "SELECT settle_date, est_total, final_total, status FROM purchase_settle_snapshot "
    "WHERE username=?", (USER,))]
print("  스냅샷 %d건: %s" % (len(snaps), [(s['settle_date'], s['final_total']) for s in snaps]))

if not APPLY:
    conn.close()
    print("실제로 지우려면: python remove_user_charges.py --user %s --apply" % USER)
    sys.exit(0)

# ① 주문의 구입가를 원래 값으로 되돌린다
restored = 0
if rows:
    try:
        uc = get_user_db(USER)
        for r in rows:
            _o = str(r['order_no'] or '')
            _p = int(r['prev_cost'] or 0)
            if not _o:
                continue
            for _t in ('order_history', 'daily_orders', 'profit_settlements'):
                try:
                    uc.execute("UPDATE %s SET cost_price=? WHERE order_no=?" % _t, (_p, _o))
                except Exception:
                    pass
            restored += 1
        uc.commit()
        uc.close()
    except Exception as e:
        print("  ⚠️ 구입가 복원 실패: %s" % e)

# ② 정산행·부족분·스냅샷 제거
_bids = {int(r['batch_id']) for r in rows if r['batch_id'] is not None}
conn.execute("DELETE FROM receipt_settle_items WHERE username=?", (USER,))
conn.execute("DELETE FROM receipt_settle_shortages WHERE username=?", (USER,))
conn.execute("DELETE FROM purchase_settle_snapshot WHERE username=?", (USER,))
if _bids:
    _recompute_batches(conn, _bids)
conn.commit()
conn.close()
print("완료 — 정산행 %d건 삭제 · 구입가 %d건 복원 · 스냅샷 %d건 삭제 · 배치 %d개 재계산"
      % (len(rows), restored, len(snaps), len(_bids)))
