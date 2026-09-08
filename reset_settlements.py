# -*- coding: utf-8 -*-
"""영수증 정산내역을 전부 지우고 주문 구입가를 정산 전 값으로 되돌린다.

정산 로직을 여러 번 고치는 동안 중복 저장·잘못된 매칭·틀린 금액이 섞여
쌓였다. 그 위에 다시 정산하면 어디까지가 옛 결과인지 알 수 없으므로
한 번 비우고 새 로직으로 다시 쌓는 편이 낫다.

지우는 것:  정산 배치·배치행·부족분·재고분·스냅샷·매칭 초안
되돌리는 것: 주문의 구입가(order_history / daily_orders / profit_settlements)
남기는 것:  영수증 품목(receipt_items) · 공유상품 · 코스트코번호 매핑
            — 영수증과 매핑은 정산의 '입력'이지 결과가 아니다.

사용: python reset_settlements.py [--apply]
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APPLY = '--apply' in sys.argv

from db_receipt_settle import _conn, _ensure
from db_core import get_user_db

conn = _conn()
_ensure(conn)
conn.row_factory = sqlite3.Row

items = [dict(r) for r in conn.execute(
    "SELECT username, order_no, amount, prev_cost FROM receipt_settle_items")]
counts = {}
for t in ('receipt_settle_batches', 'receipt_settle_items', 'receipt_settle_shortages',
          'receipt_settle_leftovers', 'purchase_settle_snapshot', 'receipt_match_draft'):
    try:
        counts[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    except Exception:
        counts[t] = 0
print("삭제 대상 · %s" % ("적용" if APPLY else "미리보기"))
for t, n in counts.items():
    print("  %-28s %d행" % (t, n))
print("구입가 복원 대상 주문 %d건" % len(items))

if not APPLY:
    conn.close()
    print("실제로 지우려면: python reset_settlements.py --apply")
    sys.exit(0)

# ① 주문 구입가를 정산 전 값으로
by_user = {}
for r in items:
    if r['order_no']:
        by_user.setdefault(r['username'], []).append(r)
restored = 0
for uname, rows in by_user.items():
    try:
        uc = get_user_db(uname)
    except Exception:
        continue
    for r in rows:
        _p = int(r['prev_cost'] or 0)
        for _t in ('order_history', 'daily_orders', 'profit_settlements'):
            try:
                uc.execute("UPDATE %s SET cost_price=? WHERE order_no=?" % _t,
                           (_p, str(r['order_no'])))
            except Exception:
                pass
        restored += 1
    uc.commit()
    uc.close()

# ② 정산 결과 비우기
for t in ('receipt_settle_items', 'receipt_settle_shortages',
          'receipt_settle_leftovers', 'receipt_settle_batches',
          'purchase_settle_snapshot', 'receipt_match_draft'):
    try:
        conn.execute("DELETE FROM %s" % t)
    except Exception:
        pass
conn.commit()
conn.close()
print("완료 — 구입가 %d건 복원 · 정산 결과 전부 삭제" % restored)
print("영수증 품목·공유상품·코스트코번호 매핑은 그대로 남아 있습니다.")
