# -*- coding: utf-8 -*-
"""daily_orders.costco_no 채우기 — 정산의 단일 축을 실제로 만든다.

주문·발송·영수증이 서로 붙는 유일한 열쇠가 코스트코 상품번호인데, 컬럼이
수집 시점에만 만들어져 기존 주문에는 아예 없었다. 화면마다 매번 다시 매칭하니
같은 주문이 화면마다 다르게 붙었다. 한 번 확정해 행에 굳혀 둔다.

사용: python backfill_costco_no.py [--days 60] [--apply]
"""
import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DAYS = 60
APPLY = '--apply' in sys.argv
for i, a in enumerate(sys.argv):
    if a == '--days' and i + 1 < len(sys.argv):
        DAYS = int(sys.argv[i + 1])

from datetime import date, timedelta
from db_products import (get_all_products, get_shared_products, resolve_costco_no)

SINCE = (date.today() - timedelta(days=DAYS)).isoformat()
print("기준일 %s 이후 · %s" % (SINCE, "적용" if APPLY else "미리보기(--apply 로 실제 저장)"))

_shared = get_shared_products()
_t_col = _t_row = _t_fix = 0

for f in sorted(glob.glob(os.path.join('data', '*.db'))):
    name = os.path.basename(f)[:-3]
    if name in ('auth', 'shared'):
        continue
    conn = sqlite3.connect(f)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_orders)")}
    except Exception:
        conn.close()
        continue
    if not cols:
        conn.close()
        continue
    if 'costco_no' not in cols:
        if APPLY:
            conn.execute("ALTER TABLE daily_orders ADD COLUMN costco_no TEXT DEFAULT ''")
            conn.commit()
            cols.add('costco_no')
        _t_col += 1
        print("  %-14s 컬럼 생성%s" % (name, "" if APPLY else "(예정)"))
        if not APPLY:
            conn.close()
            continue

    _pno = 'product_no' if 'product_no' in cols else None
    _org = 'naver_origin_pno' if 'naver_origin_pno' in cols else None
    _sel = "id, product_name, COALESCE(costco_no,'') AS cno"
    _sel += ", COALESCE(%s,'') AS pno" % _pno if _pno else ", '' AS pno"
    _sel += ", COALESCE(%s,'') AS org" % _org if _org else ", '' AS org"
    rows = conn.execute(
        "SELECT %s FROM daily_orders WHERE order_date>=? "
        "AND TRIM(COALESCE(costco_no,''))=''" % _sel, (SINCE,)).fetchall()
    if not rows:
        conn.close()
        continue

    _uprods = get_all_products(name)
    fixed = 0
    for r in rows:
        cno = resolve_costco_no(name, naver_no=r['pno'], naver_origin_no=r['org'],
                                product_name=r['product_name'] or '',
                                _user_prods=_uprods, _shared_prods=_shared)
        if not cno:
            continue
        if APPLY:
            conn.execute("UPDATE daily_orders SET costco_no=? WHERE id=?", (cno, r['id']))
        fixed += 1
    if APPLY:
        conn.commit()
    conn.close()
    _t_row += len(rows)
    _t_fix += fixed
    print("  %-14s 빈 주문 %4d건 중 %4d건 확정 (%2.0f%%)"
          % (name, len(rows), fixed, 100.0 * fixed / max(1, len(rows))))

print("합계: 컬럼 %d개 · 빈 주문 %d건 중 %d건 확정 (%.0f%%)"
      % (_t_col, _t_row, _t_fix, 100.0 * _t_fix / max(1, _t_row)))
if not APPLY:
    print("실제로 저장하려면: python backfill_costco_no.py --days %d --apply" % DAYS)
