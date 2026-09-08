# -*- coding: utf-8 -*-
"""이미 중복 저장된 정산행을 정리한다 — 주문마다 마지막 것만 남긴다.

정산을 다시 돌릴 때마다 같은 주문이 새 배치로 또 저장돼 있었다.
실측(2026-09-07): 80행인데 고유 주문 59건, 같은 배정이 최대 4번.
그대로 두면 날짜별 누적이 부풀어 과청구가 된다.

사용: python dedupe_settle_items.py [--apply]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APPLY = '--apply' in sys.argv

from db_receipt_settle import _conn, _ensure, _recompute_batches

conn = _conn()
_ensure(conn)
dups = conn.execute(
    "SELECT order_date, username, order_no, COUNT(*) c FROM receipt_settle_items "
    "GROUP BY order_date, username, order_no HAVING c > 1").fetchall()
print("중복 주문 %d건 (%s)" % (len(dups), "적용" if APPLY else "미리보기"))
if not dups:
    conn.close()
    sys.exit(0)

extra = conn.execute(
    "SELECT id, batch_id, amount FROM receipt_settle_items WHERE id NOT IN "
    "(SELECT MAX(id) FROM receipt_settle_items GROUP BY order_date, username, order_no)"
).fetchall()
print("  지울 중복 행 %d개 · 금액 %s원" % (len(extra), format(sum(int(r[2] or 0) for r in extra), ',')))
_bids = {int(r[1]) for r in extra if r[1] is not None}
if APPLY:
    conn.execute(
        "DELETE FROM receipt_settle_items WHERE id NOT IN "
        "(SELECT MAX(id) FROM receipt_settle_items GROUP BY order_date, username, order_no)")
    _recompute_batches(conn, _bids)
    conn.commit()
    print("  정리 완료 · 배치 %d개 합계 재계산" % len(_bids))
else:
    print("  실제로 지우려면: python dedupe_settle_items.py --apply")
conn.close()
