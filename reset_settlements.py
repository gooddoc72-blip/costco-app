# -*- coding: utf-8 -*-
"""정산 원장을 비우고 주문 구입가를 정산 전 값으로 되돌린다.

정산을 처음부터 다시 쌓고 싶을 때 쓴다. 옛 결과 위에 다시 정산하면 어디까지가
옛 값인지 알 수 없다.

지우는 것:  settle_item · settle_invoice · settle_draft
되돌리는 것: 주문의 구입가(order_history / daily_orders / profit_settlements)
남기는 것:  영수증 품목(receipt_items) · 공유상품 · 코스트코번호 매핑 · 재고 lot
            — 영수증과 매핑은 정산의 '입력'이지 결과가 아니다.

입금완료된 청구서는 기본적으로 남긴다. 받은 돈의 근거를 지우면 그 입금이
무엇에 대한 것이었는지 설명할 수 없게 된다. 정말 전부 지우려면 --include-paid.

사용: python reset_settlements.py [--apply] [--include-paid] [--user <계정>]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

APPLY = '--apply' in sys.argv
INCLUDE_PAID = '--include-paid' in sys.argv
USER = ''
for _i, _a in enumerate(sys.argv):
    if _a == '--user' and _i + 1 < len(sys.argv):
        USER = sys.argv[_i + 1]

import db_settle as ds
from db_core import get_user_db

conn = ds._conn()
ds.ensure(conn)

_w, _args = "1=1", []
if USER:
    _w, _args = "username=?", [USER]

paid_keys = {(str(r['settle_date']), str(r['username'])) for r in conn.execute(
    "SELECT settle_date, username FROM settle_invoice WHERE %s AND status='paid'" % _w,
    _args)}
items = [dict(r) for r in conn.execute(
    "SELECT settle_date, username, order_no, prev_cost FROM settle_item WHERE %s" % _w,
    _args)]
if not INCLUDE_PAID:
    items = [r for r in items
             if (str(r['settle_date']), str(r['username'])) not in paid_keys]

n_inv = conn.execute(
    "SELECT COUNT(*) FROM settle_invoice WHERE %s" % _w, _args).fetchone()[0]
n_draft = conn.execute("SELECT COUNT(*) FROM settle_draft").fetchone()[0]

print("삭제 대상 · %s%s" % ("적용" if APPLY else "미리보기",
                          (" · 계정 %s" % USER) if USER else ""))
print("  settle_item      %d행 (구입가 복원 대상)" % len(items))
print("  settle_invoice   %d행" % n_inv)
print("  settle_draft     %d행" % n_draft)
if paid_keys:
    print("  입금완료 %d건 — %s" % (
        len(paid_keys), "함께 지웁니다(--include-paid)" if INCLUDE_PAID else "남깁니다"))

if not APPLY:
    conn.close()
    print("\n실제로 지우려면: python reset_settlements.py --apply")
    sys.exit(0)

# ① 주문 구입가를 정산 전 값으로
by_user = {}
for r in items:
    if r['order_no']:
        by_user.setdefault(str(r['username']), []).append(r)
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

# ② 정산 결과 비우기 — 입금완료분은 조건에 따라 남긴다
_del_w = _w
if not INCLUDE_PAID:
    _del_w += " AND status<>'paid'"
keep = [(d, u) for (d, u) in paid_keys] if not INCLUDE_PAID else []
conn.execute("DELETE FROM settle_invoice WHERE %s" % _del_w, _args)
if keep:
    _ph = " AND NOT (%s)" % " OR ".join(["(settle_date=? AND username=?)"] * len(keep))
    conn.execute("DELETE FROM settle_item WHERE %s%s" % (_w, _ph),
                 _args + [x for pair in keep for x in pair])
else:
    conn.execute("DELETE FROM settle_item WHERE %s" % _w, _args)
if not USER:
    conn.execute("DELETE FROM settle_draft")
conn.commit()
conn.close()
print("\n완료 — 구입가 %d건 복원 · 정산 원장 삭제" % restored)
print("영수증 품목·공유상품·코스트코번호 매핑·재고는 그대로 남아 있습니다.")
