"""
통계 / 영수증 / 가격 이력 레이어
daily_orders 집계, 영수증 raw 저장, 가격 변동 이력
"""
import sqlite3
from datetime import datetime, timedelta

from db_core import get_user_db, AUTH_DB
from utils import get_week_range, get_month_range


# ── 통계 ─────────────────────────────────────────────────

# 대시보드 통계는 '정산저장'된 데이터(profit_settlements) 기준 → 수익계산 페이지와 일치.
# 고객배송비는 수수료 차감 없이 전액 정산(factor=1.0). 구입가 0(미산정) 행은 제외.
_PS_PROFIT_EXPR = (
    "CASE WHEN cost_price>0 THEN settlement_amount "
    "+ CAST(ROUND(COALESCE(shipping_fee,0)*?) AS INT) "
    "- cost_price - COALESCE(delivery_cost,0) - COALESCE(box_cost,0) ELSE 0 END"
)


def get_date_range_stats(username, start_date, end_date):
    from db_orders import _ship_settle_factor
    conn = get_user_db(username)
    factor = _ship_settle_factor(conn)
    rows = conn.execute(f"""SELECT settlement_date as order_date, COUNT(*) as cnt,
        SUM(qty) as total_qty, COALESCE(SUM(order_amount),0) as total_sales,
        COALESCE(SUM({_PS_PROFIT_EXPR}),0) as total_profit,
        COALESCE(SUM(settlement_amount),0) as total_settlement
        FROM profit_settlements WHERE settlement_date BETWEEN ? AND ?
        GROUP BY settlement_date ORDER BY settlement_date""",
        (factor, start_date, end_date)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_monthly_stats(username):
    from db_orders import _ship_settle_factor
    conn = get_user_db(username)
    factor = _ship_settle_factor(conn)
    rows = conn.execute(f"""SELECT substr(settlement_date, 1, 7) as month, COUNT(*) as cnt,
        COALESCE(SUM(order_amount),0) as total_sales,
        COALESCE(SUM({_PS_PROFIT_EXPR}),0) as total_profit
        FROM profit_settlements GROUP BY substr(settlement_date, 1, 7) ORDER BY month""",
        (factor,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product_ranking(username, year_month=None):
    from db_orders import _ship_settle_factor
    conn = get_user_db(username)
    factor = _ship_settle_factor(conn)
    where = "WHERE substr(settlement_date, 1, 7) = ?" if year_month else ""
    params = (factor,) + ((year_month,) if year_month else ())
    rows = conn.execute(f"""SELECT product_name, SUM(qty) as total_qty,
        COALESCE(SUM(order_amount),0) as total_sales,
        COALESCE(SUM({_PS_PROFIT_EXPR}),0) as total_profit
        FROM profit_settlements {where} GROUP BY product_name ORDER BY total_profit DESC LIMIT 10""",
        params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 택배 미발송일(공휴일) — 달력(_KR_HOLIDAYS)과 동일. 토·일은 strftime로 제외되므로 평일 공휴일만.
_NO_SHIP_HOLIDAYS = (
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02",
    "2026-05-05", "2026-05-25", "2026-09-24", "2026-09-25", "2026-10-05",
    "2026-10-09", "2026-12-25",
)
# 토(6)·일(0) + 평일 공휴일 제외 SQL 조각 (KPI를 달력 월합계와 일치시킴)
_NO_SHIP_SQL = (
    "AND CAST(strftime('%w', settlement_date) AS INTEGER) NOT IN (0,6) "
    "AND settlement_date NOT IN (" + ",".join("'%s'" % d for d in _NO_SHIP_HOLIDAYS) + ") "
)


def get_dashboard_kpi(username):
    today = datetime.today()
    w_start, w_end = get_week_range()
    m_start, m_end = get_month_range()
    lw_end   = (today - timedelta(days=today.weekday() + 1)).strftime("%Y-%m-%d")
    lw_start = (today - timedelta(days=today.weekday() + 7)).strftime("%Y-%m-%d")
    lm_last  = today.replace(day=1) - timedelta(days=1)
    lm_start = lm_last.replace(day=1).strftime("%Y-%m-%d")
    lm_end   = lm_last.strftime("%Y-%m-%d")
    from db_orders import _ship_settle_factor
    conn = get_user_db(username)
    factor = _ship_settle_factor(conn)
    def q(s, e):
        # 수익은 달력과 동일하게 토·일·공휴일(택배 미발송일) 제외하고 합산
        r = conn.execute(f"""SELECT COUNT(*) as cnt, COALESCE(SUM(qty),0) as qty,
            COALESCE(SUM(order_amount),0) as sales,
            COALESCE(SUM(CASE WHEN CAST(strftime('%w', settlement_date) AS INTEGER) NOT IN (0,6)
                     AND settlement_date NOT IN ({",".join("'%s'" % d for d in _NO_SHIP_HOLIDAYS)})
                     THEN ({_PS_PROFIT_EXPR}) ELSE 0 END),0) as profit
            FROM profit_settlements WHERE settlement_date BETWEEN ? AND ?""", (factor, s, e)).fetchone()
        return dict(r) if r else {'cnt': 0, 'qty': 0, 'sales': 0, 'profit': 0}
    kpi = {
        'week': q(w_start, w_end), 'month': q(m_start, m_end),
        'last_week': q(lw_start, lw_end), 'last_month': q(lm_start, lm_end),
    }
    conn.close()
    return kpi


def get_cumulative_sales(username, until_date=None):
    today = datetime.today()
    if until_date is None:
        until_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    month_start = today.replace(day=1).strftime("%Y-%m-%d")
    conn = get_user_db(username)
    r = conn.execute(
        """SELECT COALESCE(SUM(order_amount), 0) as total_sales,
                  COALESCE(COUNT(*), 0) as total_cnt
           FROM profit_settlements
           WHERE settlement_date BETWEEN ? AND ?""",
        (month_start, until_date)
    ).fetchone()
    conn.close()
    row = dict(r) if r else {'total_sales': 0, 'total_cnt': 0}
    row['until'] = until_date
    row['from']  = month_start
    return row


def get_daily_profit_trend(username, days=14):
    from db_orders import _ship_settle_factor
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    conn  = get_user_db(username)
    factor = _ship_settle_factor(conn)
    rows  = conn.execute(f"""SELECT settlement_date as order_date, COUNT(*) as cnt, SUM(qty) as total_qty,
        COALESCE(SUM(order_amount),0) as total_sales, COALESCE(SUM({_PS_PROFIT_EXPR}),0) as total_profit
        FROM profit_settlements WHERE settlement_date BETWEEN ? AND ?
        GROUP BY settlement_date ORDER BY settlement_date""", (factor, start, end)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_week_best_products(username):
    w_start, w_end = get_week_range()
    conn = get_user_db(username)
    rows = conn.execute("""SELECT product_name, SUM(qty) as total_qty,
        SUM(order_amount) as total_sales, SUM(profit) as total_profit
        FROM daily_orders WHERE order_date BETWEEN ? AND ?
        GROUP BY product_name ORDER BY total_profit DESC LIMIT 5""", (w_start, w_end)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_price_history_monthly(username):
    m_start = datetime.today().strftime("%Y-%m-01")
    conn = get_user_db(username)
    rows = conn.execute("""SELECT created_at, product_name, old_price, new_price, cost_price, reason, status
        FROM price_history WHERE created_at >= ? ORDER BY created_at DESC""", (m_start,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 가격 변동 이력 ────────────────────────────────────────

def save_price_changes_to_history(username, changes):
    if not changes:
        return
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for c in changes:
        conn.execute("""INSERT INTO price_change_history
            (costco_name, old_cost, new_cost, diff, diff_pct, product_no, shipping_fee, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (c['costco_name'], c['old_cost'], c['new_cost'], c['diff'], c['diff_pct'],
             c.get('product_no', ''), c.get('shipping_fee', 0), now))
    conn.commit()
    conn.close()


def get_price_change_history(username, limit=50):
    conn = get_user_db(username)
    rows = conn.execute(
        "SELECT * FROM price_change_history ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 영수증 raw 항목 ────────────────────────────────────────

def _receipt_conn():
    """영수증은 auth.db 한 곳에 둔다 — 관리자만 등록하는 공용 사실이다."""
    conn = sqlite3.connect(AUTH_DB, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS receipt_items (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_date TEXT NOT NULL,
        product_no   TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        qty          INTEGER DEFAULT 1,
        unit_price   INTEGER DEFAULT 0,
        discount     INTEGER DEFAULT 0,
        list_price   INTEGER DEFAULT 0,
        uploaded_by  TEXT DEFAULT '',
        created_at   TEXT DEFAULT '',
        UNIQUE(receipt_date, product_no, product_name)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ri_date ON receipt_items(receipt_date)")
    for _sql in ("ALTER TABLE receipt_items ADD COLUMN discount INTEGER DEFAULT 0",
                 "ALTER TABLE receipt_items ADD COLUMN list_price INTEGER DEFAULT 0",
                 "ALTER TABLE receipt_items ADD COLUMN uploaded_by TEXT DEFAULT ''"):
        try:
            conn.execute(_sql)
        except Exception:
            pass
    return conn


def get_receipt_items_by_date(username, receipt_date):
    """그 날짜의 영수증 품목 — 화면 표를 되살릴 때 쓴다. username은 무시(공용)."""
    conn = _receipt_conn()
    rows = conn.execute(
        "SELECT product_no, product_name, qty, unit_price, "
        "COALESCE(discount,0) AS discount, COALESCE(list_price,0) AS list_price, "
        "receipt_date FROM receipt_items WHERE receipt_date=? ORDER BY id",
        (str(receipt_date),)).fetchall()
    conn.close()
    return [{'상품번호': str(r['product_no'] or ''), '상품명': r['product_name'] or '',
             '수량': int(r['qty'] or 1), '단가': int(r['unit_price'] or 0),
             '할인': int(r['discount'] or 0),
             '정가단가': int(r['list_price'] or 0) or int(r['unit_price'] or 0),
             'receipt_date': r['receipt_date'] or ''} for r in rows]


def receipt_dates_with_items(limit=60):
    """영수증이 저장된 날짜들(최신순) — [(날짜, 품목수)]."""
    conn = _receipt_conn()
    rows = conn.execute(
        "SELECT receipt_date, COUNT(*) c FROM receipt_items WHERE receipt_date<>'' "
        "GROUP BY receipt_date ORDER BY receipt_date DESC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    return [(r['receipt_date'], r['c']) for r in rows]


def save_receipt_items(username, items, replace_dates=True):
    """영수증 품목 저장(공용). 반환: (신규, 갱신, 지운 옛 행)

    replace_dates=True — **그 날짜를 통째로 교체한다.**

    왜: 영수증을 잘못 읽었을 때 고치는 길이 재업로드밖에 없다. 그런데 예전에는
    있으면 UPDATE·없으면 INSERT만 해서, **이번에 안 읽힌 옛 행이 그대로 남았다.**
    ALLO 선식크래커가 19개로 잘못 읽힌 날, 고쳐서 다시 올려도 그 줄이 이번 판독에
    빠지면 19가 계속 재고에 남는 식이다. 상품명을 조금 다르게 읽으면 유니크 키가
    갈라져 같은 물건이 두 줄이 되기도 했다.

    이제 그 날짜 것을 지우고 이번에 올린 것으로 채운다. 재업로드 = 그날 영수증을
    다시 쓰는 일이므로 이게 사람이 기대하는 동작이다.

    ⚠️ 하루에 영수증이 여러 장이면 **한 번에 같이 올려야 한다.** 나눠서 저장하면
    나중 것이 앞 것을 지운다. (업로더는 다중 파일을 받아 합쳐 준다.)
    replace_dates=False로 부르면 옛 방식(누적)으로 동작한다.
    """
    if not items:
        return 0, 0, 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 같은 (날짜,번호,이름)이 한 배치에 두 번 오면 나중 것이 이긴다 — 옛 동작과 같다.
    rows = {}
    for it in items:
        rd = (it.get('receipt_date') or '').strip()
        name = (it.get('상품명') or '').strip()
        if not name or not rd:
            continue
        pno = str(it.get('상품번호') or '').strip()
        price = int(it.get('단가') or 0)
        rows[(rd, pno, name)] = (
            int(it.get('수량') or 1), price, int(it.get('할인') or 0),
            int(it.get('정가단가') or 0) or price)
    if not rows:
        return 0, 0, 0

    dates = sorted({rd for rd, _, _ in rows})
    conn = _receipt_conn()
    try:
        # 교체 전에 세어 둔다 — 지우고 나면 무엇이 새것이고 무엇이 고쳐진 것인지
        # 구분할 수 없게 되고, 화면에는 전부 '신규'로 보인다.
        _ph = ",".join("?" * len(dates))
        old = {(str(r['receipt_date']), str(r['product_no'] or ''), str(r['product_name'] or ''))
               for r in conn.execute(
                   "SELECT receipt_date, product_no, product_name FROM receipt_items "
                   "WHERE receipt_date IN (%s)" % _ph, dates)}
        updated = len(old & set(rows))
        saved = len(rows) - updated
        removed = len(old - set(rows)) if replace_dates else 0

        if replace_dates:
            conn.execute("DELETE FROM receipt_items WHERE receipt_date IN (%s)" % _ph, dates)
        for (rd, pno, name), (qty, price, disc, listp) in rows.items():
            conn.execute(
                "INSERT OR REPLACE INTO receipt_items (receipt_date, product_no, "
                "product_name, qty, unit_price, discount, list_price, uploaded_by, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (rd, pno, name, qty, price, disc, listp, str(username or ''), now))
        conn.commit()
    finally:
        conn.close()
    return saved, updated, removed


def get_recent_receipt_items(username, days=90):
    conn = _receipt_conn()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT receipt_date, product_no, product_name, qty, unit_price "
        "FROM receipt_items WHERE receipt_date >= ? ORDER BY receipt_date DESC, id DESC",
        (cutoff,)
    ).fetchall()
    conn.close()
    return [
        {
            '상품번호': r['product_no'] or '',
            '상품명': r['product_name'] or '',
            '수량': int(r['qty'] or 1),
            '단가': int(r['unit_price'] or 0),
            'receipt_date': r['receipt_date'] or '',
        }
        for r in rows
    ]


def delete_receipt_items_by_date(username, receipt_date):
    conn = _receipt_conn()
    cur = conn.execute("DELETE FROM receipt_items WHERE receipt_date=?", (receipt_date,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def get_receipt_dates(username):
    conn = _receipt_conn()
    rows = conn.execute(
        "SELECT DISTINCT receipt_date, COUNT(*) as cnt FROM receipt_items "
        "GROUP BY receipt_date ORDER BY receipt_date DESC"
    ).fetchall()
    conn.close()
    return [(r['receipt_date'], r['cnt']) for r in rows]
