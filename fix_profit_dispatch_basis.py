"""수익 저장(profit_settlements)을 '송장 등록으로 발송처리된 건 + 온라인몰 직배송 건' 기준으로 정리 (10/26).

수익계산은 무조건 발송건 기준이다. 그런데 저장된 수익에는
  · 예전 '주문 수집일 기준' 자동 저장이 남긴 행(발송일과 다른 날짜, 발송 기록 없는 주문)
  · 화면이 결제일로 대신 보여 줄 때 정산저장한 행
  · 발송됐는데 수익이 저장되지 않은 주문('주문 자동 수집' 작업이 없는 사용자)
이 섞여 홈 달력·월별 수익이 화면과 달랐다(10/26 실측: 다른 날짜 92·발송 없음 186·저장 없음 493).

하는 일 (사용자마다, --from 날짜부터 오늘까지):
  그 기간의 발송일·온라인몰 정산일·저장 날짜 전부에 대해 save_dispatch_profit을 두 번 돈다.
    · 그날 발송·온라인몰 건의 수익을 저장(사람이 고친 값은 유지, 다른 날짜에 있던 값은 발송일로 옮김)
    · 그날 대상이 아닌 행은 발송일로 옮기거나, 발송 기록이 없으면 지운다
사용:
  python fix_profit_dispatch_basis.py <앱경로> [--from 2026-10-01]          # 미리보기(복사본 경로로)
  python fix_profit_dispatch_basis.py /opt/costco-app --apply [--from ...]  # 적용 + data/backup_profit2_시각/ 백업
"""
import sys, os, glob, sqlite3
from datetime import datetime, date

ROOT = sys.argv[1]
APPLY = '--apply' in sys.argv
FROM = sys.argv[sys.argv.index('--from') + 1] if '--from' in sys.argv else '2026-10-01'
TO = date.today().isoformat()
if not APPLY and os.path.realpath(ROOT) == '/opt/costco-app':
    sys.exit("운영 경로는 --apply(백업 포함)로만 실행합니다. 미리보기는 DB 복사본 경로로 돌리세요.")
os.chdir(ROOT); sys.path.insert(0, ROOT)
from pages_lib.profit_calc.dispatch_profit import save_dispatch_profit, _online_dates
DATA = os.path.join(ROOT, 'data')


def stat(c):
    """(행 수, 매출 합, 수익 합(구입가 있는 행), 발송 아닌 행 수)"""
    r = c.execute(
        "select count(*), coalesce(sum(order_amount),0), coalesce(sum(case when cost_price>0 then "
        "settlement_amount+shipping_fee-cost_price-delivery_cost-box_cost else 0 end),0) "
        "from profit_settlements where settlement_date between ? and ?", (FROM, TO)).fetchone()
    return r


def off_basis(c, online):
    """발송일(또는 온라인몰 정산일)과 다른 날짜에 있는 행 수, 발송된 주문인데 저장이 없는 수."""
    dl = {}
    for o, d in c.execute("select order_no, min(substr(dispatched_at,1,10)) from dispatch_log group by order_no"):
        dl[str(o)] = d
    bad = 0
    keys = set()
    for d, o in c.execute("select settlement_date, order_no from profit_settlements where settlement_date between ? and ?",
                          (FROM, TO)):
        o = str(o)
        keys.add(o)
        bd = dl.get(o) or online.get(o)
        if bd != d:
            bad += 1
    miss = sum(1 for o, d in dl.items() if FROM <= (d or '') <= TO and o not in keys)
    miss += sum(1 for o, d in online.items() if FROM <= d <= TO and o not in keys and o not in dl)
    return bad, miss


if APPLY:
    bk = os.path.join(DATA, 'backup_profit2_' + datetime.now().strftime('%Y%m%d_%H%M'))
    os.makedirs(bk, exist_ok=True)

tot = [0, 0, 0, 0, 0, 0, 0, 0]
print(f'기간 {FROM} ~ {TO}')
print('사용자 | 행 전→후 | 매출 전→후 | 수익 전→후 | 기준 어긋난 행 전→후 | 발송됐는데 저장 없음 전→후')
for f in sorted(glob.glob(os.path.join(DATA, '*.db'))):
    u = os.path.basename(f)[:-3]
    if u in ('auth', 'admin') or '.bak' in u or u.startswith('backup') or '.' in u:
        continue
    c = sqlite3.connect(f)
    try:
        if not (c.execute("select 1 from sqlite_master where name='profit_settlements'").fetchone()
                and c.execute("select 1 from sqlite_master where name='dispatch_log'").fetchone()):
            continue
        online = _online_dates(u)
        b = stat(c)
        b_off = off_basis(c, online)
        dates = {r[0] for r in c.execute(
            "select distinct substr(dispatched_at,1,10) from dispatch_log where substr(dispatched_at,1,10) between ? and ?",
            (FROM, TO))}
        dates |= {r[0] for r in c.execute(
            "select distinct settlement_date from profit_settlements where settlement_date between ? and ?", (FROM, TO))}
        dates |= {d for d in online.values() if FROM <= d <= TO}
    finally:
        c.close()
    if APPLY:
        s = sqlite3.connect(f); d_ = sqlite3.connect(os.path.join(bk, os.path.basename(f)))
        s.backup(d_); s.close(); d_.close()
    for _pass in (1, 2):           # 옮긴 행이 앞 날짜로 가는 경우까지 정리되게 두 번
        for d in sorted(dates):
            save_dispatch_profit(u, d)
    c = sqlite3.connect(f)
    try:
        a = stat(c)
        a_off = off_basis(c, online)
    finally:
        c.close()
    if b != a or b_off != a_off:
        print(f"{u} | {b[0]}→{a[0]} | {b[1]:,}→{a[1]:,} | {b[2]:,}→{a[2]:,} | {b_off[0]}→{a_off[0]} | {b_off[1]}→{a_off[1]}")
    for i, v in enumerate((b[0], a[0], b[1], a[1], b[2], a[2], b_off[0] + b_off[1], a_off[0] + a_off[1])):
        tot[i] += v
print(f"합계 | 행 {tot[0]}→{tot[1]} | 매출 {tot[2]:,}→{tot[3]:,} | 수익 {tot[4]:,}→{tot[5]:,} | 어긋남+누락 {tot[6]}→{tot[7]}")
if APPLY:
    print('백업:', bk)
