# -*- coding: utf-8 -*-
"""확정 금액을 실제 배치 누적액으로 바로잡는다.

전송할 때 그 미리보기 한 번의 합계로 확정해서, 하루에 정산을 여러 번 돌린 날은
마지막 회차 금액만 남았다(2026-09-07 oxo: 실제 955,510원 → 화면 184,560원).
사용자에게 실제로 청구한 금액은 그날 누적된 배치 전체다.

사용: python repair_settle_totals.py [--days 60] [--apply]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DAYS = 60
APPLY = '--apply' in sys.argv
for i, a in enumerate(sys.argv):
    if a == '--days' and i + 1 < len(sys.argv):
        DAYS = int(sys.argv[i + 1])

from datetime import date, timedelta
from db_receipt_settle import user_totals_by_date
from db_purchase_settle import get_period_rows, finalize

_to = date.today().isoformat()
_from = (date.today() - timedelta(days=DAYS)).isoformat()
print("기간 %s ~ %s · %s" % (_from, _to, "적용" if APPLY else "미리보기(--apply 로 저장)"))

acc = user_totals_by_date(_from, _to)
snap = {(r['settle_date'], r['username']): r for r in get_period_rows(_from, _to)}

_fix = _same = _new = 0
for (d, u), v in sorted(acc.items()):
    want = int(v['amount'] or 0)
    cur = snap.get((d, u))
    have = int((cur or {}).get('amount') or 0)
    if cur is None:
        mark = '신규'
        _new += 1
    elif have == want:
        _same += 1
        continue
    else:
        mark = '수정'
        _fix += 1
    print("  %s %-14s %s  %10s → %10s  (배치 %d건)"
          % (d, u, mark, format(have, ','), format(want, ','), v['count']))
    if APPLY:
        finalize(d, u, want, [], created_by='repair')

print("합계: 수정 %d · 신규 %d · 이미맞음 %d" % (_fix, _new, _same))
if not APPLY:
    print("실제로 저장하려면: python repair_settle_totals.py --days %d --apply" % DAYS)
