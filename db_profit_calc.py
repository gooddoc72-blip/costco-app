"""수익계산 DB 레이어
- profit_settlements : 수익계산 결과 (날짜별 저장)
- settlement_overrides: 정산매칭 오버라이드 (키워드/단가 영구 저장)
"""
from datetime import datetime
from db_core import get_user_db


# ── 테이블 초기화 ──────────────────────────────────────────

def _ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS profit_settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        settlement_date TEXT NOT NULL,
        order_no TEXT DEFAULT '',
        recipient TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        product_no TEXT DEFAULT '',
        option_info TEXT DEFAULT '',
        qty INTEGER DEFAULT 1,
        order_amount INTEGER DEFAULT 0,
        shipping_fee INTEGER DEFAULT 0,
        extra_shipping INTEGER DEFAULT 0,
        settlement_amount INTEGER DEFAULT 0,
        cost_price INTEGER DEFAULT 0,
        delivery_cost INTEGER DEFAULT 0,
        box_cost INTEGER DEFAULT 0,
        profit INTEGER DEFAULT 0,
        matched_keyword TEXT DEFAULT '',
        matched_product_no TEXT DEFAULT '',
        match_source TEXT DEFAULT '',
        split_qty INTEGER DEFAULT 1,
        sell_factor INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        UNIQUE(settlement_date, recipient, product_name)
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ps_date ON profit_settlements(settlement_date)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS settlement_overrides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        override_keyword TEXT DEFAULT '',
        override_cost INTEGER DEFAULT 0,
        updated_at TEXT NOT NULL,
        UNIQUE(recipient, product_name)
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_so_pname ON settlement_overrides(product_name)"
    )


# ── profit_settlements CRUD ────────────────────────────────

def save_profit_settlements(username: str, date: str, rows: list) -> int:
    """수익계산 결과 일괄 저장 (UPSERT by settlement_date+recipient+product_name)."""
    if not rows:
        return 0
    conn = get_user_db(username)
    _ensure_tables(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    for r in rows:
        conn.execute(
            """INSERT INTO profit_settlements
               (settlement_date, order_no, recipient, product_name, product_no, option_info,
                qty, order_amount, shipping_fee, extra_shipping, settlement_amount,
                cost_price, delivery_cost, box_cost, profit,
                matched_keyword, matched_product_no, match_source,
                split_qty, sell_factor, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(settlement_date, recipient, product_name) DO UPDATE SET
                   order_no           = excluded.order_no,
                   product_no         = excluded.product_no,
                   qty                = excluded.qty,
                   order_amount       = excluded.order_amount,
                   shipping_fee       = excluded.shipping_fee,
                   extra_shipping     = excluded.extra_shipping,
                   settlement_amount  = excluded.settlement_amount,
                   cost_price         = excluded.cost_price,
                   delivery_cost      = excluded.delivery_cost,
                   box_cost           = excluded.box_cost,
                   profit             = excluded.profit,
                   matched_keyword    = excluded.matched_keyword,
                   matched_product_no = excluded.matched_product_no,
                   match_source       = excluded.match_source,
                   split_qty          = excluded.split_qty,
                   sell_factor        = excluded.sell_factor,
                   created_at         = excluded.created_at""",
            (date,
             str(r.get('order_no', '') or ''),
             str(r.get('recipient', '') or ''),
             str(r.get('product_name', '') or ''),
             str(r.get('product_no', '') or ''),
             str(r.get('option_info', '') or ''),
             int(r.get('qty', 1) or 1),
             int(r.get('order_amount', 0) or 0),
             int(r.get('shipping_fee', 0) or 0),
             int(r.get('extra_shipping', 0) or 0),
             int(r.get('settlement_amount', 0) or 0),
             int(r.get('cost_price', 0) or 0),
             int(r.get('delivery_cost', 0) or 0),
             int(r.get('box_cost', 0) or 0),
             int(r.get('profit', 0) or 0),
             str(r.get('matched_keyword', '') or ''),
             str(r.get('matched_product_no', '') or ''),
             str(r.get('match_source', '') or ''),
             int(r.get('split_qty', 1) or 1),
             int(r.get('sell_factor', 1) or 1),
             now))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def apply_actual_settlements_to_profit(username: str, actuals: dict) -> int:
    """정산매칭의 실제 정산액을 profit_settlements.settlement_amount에 반영 (order_no 기준).
    → 달력/대시보드/통계의 정산·수익이 예상 대신 실제 정산액을 반영하게 됨.
    actuals: {order_no(상품주문번호): {'actual': int, ...}}
    """
    if not actuals:
        return 0
    conn = get_user_db(username)
    _ensure_tables(conn)
    n = 0
    for po, info in actuals.items():
        actual = int((info or {}).get('actual') or 0)
        if actual <= 0:
            continue
        cur = conn.execute(
            "UPDATE profit_settlements SET settlement_amount=? WHERE order_no=?",
            (actual, str(po))
        )
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n


def get_profit_settlements(username: str, date: str) -> list:
    """날짜별 수익계산 결과 조회."""
    conn = get_user_db(username)
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM profit_settlements WHERE settlement_date=? ORDER BY product_name, recipient",
        (date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_saved_profit_dates(username: str) -> list:
    """저장된 수익계산 날짜 목록."""
    conn = get_user_db(username)
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT DISTINCT settlement_date FROM profit_settlements ORDER BY settlement_date DESC"
    ).fetchall()
    conn.close()
    return [r['settlement_date'] for r in rows]


def delete_profit_settlements(username: str, date: str) -> int:
    conn = get_user_db(username)
    _ensure_tables(conn)
    cur = conn.execute(
        "DELETE FROM profit_settlements WHERE settlement_date=?", (date,)
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def get_profit_history(username: str, date_from: str = '', date_to: str = '',
                       product_name: str = '', limit: int = 500) -> list:
    """수익 이력 조회 (날짜 범위 + 상품명 필터)."""
    conn = get_user_db(username)
    _ensure_tables(conn)
    query = "SELECT * FROM profit_settlements WHERE 1=1"
    params = []
    if date_from:
        query += " AND settlement_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND settlement_date <= ?"
        params.append(date_to)
    if product_name:
        query += " AND product_name LIKE ?"
        params.append(f'%{product_name}%')
    query += " ORDER BY settlement_date DESC, product_name LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── settlement_overrides CRUD ──────────────────────────────

def save_settlement_override(username: str, recipient: str, product_name: str,
                              keyword: str = '', cost: int = 0) -> None:
    """정산매칭 오버라이드 영구 저장 (keyword 또는 cost 중 유효한 값만 갱신)."""
    if not keyword and not cost:
        return
    conn = get_user_db(username)
    _ensure_tables(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO settlement_overrides
           (recipient, product_name, override_keyword, override_cost, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(recipient, product_name) DO UPDATE SET
               override_keyword = CASE WHEN excluded.override_keyword != ''
                                       THEN excluded.override_keyword
                                       ELSE settlement_overrides.override_keyword END,
               override_cost    = CASE WHEN excluded.override_cost > 0
                                       THEN excluded.override_cost
                                       ELSE settlement_overrides.override_cost END,
               updated_at       = excluded.updated_at""",
        (str(recipient), str(product_name), str(keyword or ''), int(cost or 0), now)
    )
    conn.commit()
    conn.close()


def get_settlement_overrides_map(username: str) -> dict:
    """전체 정산매칭 오버라이드 맵: {(recipient, product_name): dict}."""
    conn = get_user_db(username)
    _ensure_tables(conn)
    rows = conn.execute("SELECT * FROM settlement_overrides").fetchall()
    conn.close()
    return {(r['recipient'], r['product_name']): dict(r) for r in rows}


def delete_settlement_override(username: str, recipient: str, product_name: str) -> int:
    conn = get_user_db(username)
    _ensure_tables(conn)
    cur = conn.execute(
        "DELETE FROM settlement_overrides WHERE recipient=? AND product_name=?",
        (recipient, product_name)
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n
