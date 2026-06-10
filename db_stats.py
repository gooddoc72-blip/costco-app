"""
통계 / 영수증 / 가격 이력 레이어
daily_orders 집계, 영수증 raw 저장, 가격 변동 이력
"""
from datetime import datetime, timedelta

from db_core import get_user_db
from utils import get_week_range, get_month_range


# ── 통계 ─────────────────────────────────────────────────

# 대시보드 통계는 '정산저장'된 데이터(profit_settlements) 기준 → 수익계산 페이지와 일치.
# 수익은 실정산배송비(고객배송비×(1-네이버수수료율))로 계산하고 구입가 0(미산정) 행은 제외.
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
        r = conn.execute(f"""SELECT COUNT(*) as cnt, COALESCE(SUM(qty),0) as qty,
            COALESCE(SUM(order_amount),0) as sales, COALESCE(SUM({_PS_PROFIT_EXPR}),0) as profit
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

def save_receipt_items(username, items):
    if not items:
        return 0, 0
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    updated = 0
    for it in items:
        rd = (it.get('receipt_date') or '').strip()
        pno = str(it.get('상품번호') or '').strip()
        name = (it.get('상품명') or '').strip()
        qty = int(it.get('수량') or 1)
        price = int(it.get('단가') or 0)
        if not name or not rd:
            continue
        existing = conn.execute(
            "SELECT id FROM receipt_items WHERE receipt_date=? AND product_no=? AND product_name=?",
            (rd, pno, name)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE receipt_items SET qty=?, unit_price=?, created_at=? WHERE id=?",
                (qty, price, now, existing['id'])
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO receipt_items (receipt_date, product_no, product_name, qty, unit_price, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (rd, pno, name, qty, price, now)
            )
            saved += 1
    conn.commit()
    conn.close()
    return saved, updated


def get_recent_receipt_items(username, days=90):
    conn = get_user_db(username)
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
    conn = get_user_db(username)
    cur = conn.execute("DELETE FROM receipt_items WHERE receipt_date=?", (receipt_date,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def get_receipt_dates(username):
    conn = get_user_db(username)
    rows = conn.execute(
        "SELECT DISTINCT receipt_date, COUNT(*) as cnt FROM receipt_items "
        "GROUP BY receipt_date ORDER BY receipt_date DESC"
    ).fetchall()
    conn.close()
    return [(r['receipt_date'], r['cnt']) for r in rows]
