"""수익 저장(profit_settlements)을 발송 기준으로 재정렬 — 10월분 1회 정리용.

사용:
  python migrate_profit_dispatch_basis.py <앱경로>            # 그 경로 데이터에 바로 적용
  python migrate_profit_dispatch_basis.py <앱경로> --apply    # 적용 + data/backup_profit_시각/ 에 DB 백업

⚠️ <앱경로>의 data/에 바로 쓴다. 미리보기는 DB 복사본 경로를 넘겨 돌린다.
규칙(pages_lib/profit_calc/dispatch_profit.save_dispatch_profit):
  · 발송일마다 그날 발송한 주문을 '자동'으로 저장, 다른 날짜의 자동 행은 발송일로 이동
  · 사람이 고친 저장값(수동입력·영수증 등)은 건드리지 않음
  · 발송 기록이 없는 자동 행은 취소·반품된 주문만 삭제(앱 밖 발송분은 보존)
"""
import sys, os, glob, sqlite3, shutil
from datetime import datetime
ROOT = sys.argv[1]
APPLY = '--apply' in sys.argv
os.chdir(ROOT); sys.path.insert(0, ROOT)
from pages_lib.profit_calc.dispatch_profit import save_dispatch_profit, AUTO_SOURCE
DATA = os.path.join(ROOT, 'data')
FROM, TO = '2026-10-01', '2026-10-21'

if APPLY:
    bk = os.path.join(DATA, 'backup_profit_' + datetime.now().strftime('%Y%m%d_%H%M'))
    os.makedirs(bk, exist_ok=True)

def stat(c):
    return c.execute(
        "select count(*), coalesce(sum(case when cost_price>0 then settlement_amount+shipping_fee"
        "-cost_price-delivery_cost-box_cost else 0 end),0), coalesce(sum(order_amount),0) "
        "from profit_settlements where settlement_date between ? and ?", (FROM, TO)).fetchone()

tot = {'b_rows': 0, 'b_profit': 0, 'a_rows': 0, 'a_profit': 0}
print('사용자 | 행 전→후 | 매출 전→후 | 수익 전→후 | 저장 이동 정리 수동유지 미발송자동정리')
for f in sorted(glob.glob(os.path.join(DATA, '*.db'))):
    u = os.path.basename(f)[:-3]
    if u == 'auth' or '.bak' in u or u.startswith('backup'):
        continue
    c = sqlite3.connect(f)
    try:
        has = c.execute("select 1 from sqlite_master where name='profit_settlements'").fetchone() and \
              c.execute("select 1 from sqlite_master where name='dispatch_log'").fetchone()
        if not has:
            continue
        b = stat(c)
        dates = [r[0][:10] for r in c.execute(
            "select distinct substr(dispatched_at,1,10) from dispatch_log where dispatched_at between ? and ?",
            (FROM, TO + ' 23:59'))]
    finally:
        c.close()
    if APPLY:
        sqlite3.connect(f).backup(sqlite3.connect(os.path.join(bk, os.path.basename(f))))
    agg = {'saved': 0, 'moved': 0, 'removed': 0, 'kept_manual': 0, 'skipped_other': 0}
    for d in sorted(dates):
        r = save_dispatch_profit(u, d)
        for k in agg:
            agg[k] += r.get(k, 0)
    # 발송 기록이 아예 없는 주문의 자동 행 — 취소·미발송. 나중에 발송되면 그날 저장된다.
    c = sqlite3.connect(f)
    try:
        disp_all = {r[0] for r in c.execute("select order_no from dispatch_log")}
        # 발송 기록이 없는 자동 행은 **취소·반품된 주문만** 지운다. 앱 밖(쿠팡·스토어
        # 직접)에서 보낸 주문은 발송 기록이 없어도 실제 매출이라 그대로 둔다.
        from pages_lib.profit_calc.dispatch_profit import is_cancelled_status
        _st = dict(c.execute("select order_no, status from order_history").fetchall())
        orphan = [r[0] for r in c.execute(
            "select id, order_no from profit_settlements where settlement_date between ? and ? "
            "and match_source=?", (FROM, TO, AUTO_SOURCE))
            if str(r[1]) not in disp_all and is_cancelled_status(_st.get(str(r[1])))]
        if orphan:
            c.executemany("delete from profit_settlements where id=?", [(i,) for i in orphan])
            c.commit()
        a = stat(c)
    finally:
        c.close()
    tot['b_rows'] += b[0]; tot['b_profit'] += b[1]; tot['a_rows'] += a[0]; tot['a_profit'] += a[1]
    if b != a or any(agg.values()) or orphan:
        print(f"{u} | {b[0]}→{a[0]} | {b[2]:,}→{a[2]:,} | {b[1]:,}→{a[1]:,} | "
              f"{agg['saved']} {agg['moved']} {agg['removed']} {agg['kept_manual']} {len(orphan)}")
print('TOTAL', tot)
if APPLY:
    print('backup:', bk)
