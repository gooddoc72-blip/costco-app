"""수익계산 DB 레이어
- profit_settlements : 수익계산 결과 (날짜별 저장)
- settlement_overrides: 정산매칭 오버라이드 (키워드/단가 영구 저장)
"""
from datetime import datetime
from db_core import get_user_db


# ── 테이블 초기화 ──────────────────────────────────────────

_PS_TABLE_SQL = """CREATE TABLE {name} (
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
        UNIQUE(settlement_date, order_no)
    )"""


def _ensure_tables(conn):
    conn.execute(_PS_TABLE_SQL.format(name="IF NOT EXISTS profit_settlements"))
    # ── 마이그레이션: 옛 UNIQUE(settlement_date, recipient, product_name)
    #    → (settlement_date, order_no). 각 주문을 상품주문번호로 개별 보존. ──
    _r = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='profit_settlements'").fetchone()
    _s = (_r[0] if _r else '') or ''
    if 'recipient, product_name' in _s and 'settlement_date, order_no' not in _s:
        # order_no 비어있는 옛 행 → 합성키(_r{id})로 충돌 방지 (개별 보존)
        conn.execute("UPDATE profit_settlements SET order_no='_r'||id WHERE COALESCE(order_no,'')=''")
        # 같은 (날짜, order_no) 중복은 최신 1건만 유지
        conn.execute("DELETE FROM profit_settlements WHERE id NOT IN "
                     "(SELECT MAX(id) FROM profit_settlements GROUP BY settlement_date, order_no)")
        conn.execute("ALTER TABLE profit_settlements RENAME TO _ps_old")
        conn.execute(_PS_TABLE_SQL.format(name="profit_settlements"))
        conn.execute("INSERT INTO profit_settlements SELECT * FROM _ps_old")
        conn.execute("DROP TABLE _ps_old")
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
        # order_no(상품주문번호)를 키로 각 주문 개별 저장. 비어있으면 수취인|상품으로 합성(구버전 호환).
        _ono = (str(r.get('order_no', '') or '').strip()
                or f"{str(r.get('recipient','') or '')}|{str(r.get('product_name','') or '')}")
        conn.execute(
            """INSERT INTO profit_settlements
               (settlement_date, order_no, recipient, product_name, product_no, option_info,
                qty, order_amount, shipping_fee, extra_shipping, settlement_amount,
                cost_price, delivery_cost, box_cost, profit,
                matched_keyword, matched_product_no, match_source,
                split_qty, sell_factor, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(settlement_date, order_no) DO UPDATE SET
                   recipient          = excluded.recipient,
                   product_name       = excluded.product_name,
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
             _ono,
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


def calc_row_profit(r: dict) -> int:
    """저장 행 → 수익. 일별 정산저장(save.py)·통계(db_stats)와 같은 공식.
    수익 = (정산예정 + 고객배송비 전액) − (구입가 + 택배비 + 박스비)."""
    return (int(r.get('settlement_amount') or 0) + int(r.get('shipping_fee') or 0)
            - int(r.get('cost_price') or 0) - int(r.get('delivery_cost') or 0)
            - int(r.get('box_cost') or 0))


def get_month_settlements(username: str, year_month: str) -> list:
    """한 달(YYYY-MM) 정산저장 행 전부 — 월별 분석·점검용."""
    conn = get_user_db(username)
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM profit_settlements WHERE substr(settlement_date,1,7)=? "
        "ORDER BY settlement_date, product_name, recipient", (str(year_month),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_profit_months(username: str) -> list:
    """정산저장 데이터가 있는 달(최신순)."""
    conn = get_user_db(username)
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT DISTINCT substr(settlement_date,1,7) m FROM profit_settlements "
        "WHERE settlement_date!='' ORDER BY m DESC").fetchall()
    conn.close()
    return [r['m'] for r in rows]


#: 월별 화면에서 고칠 수 있는 칸 — 주문 자체(주문번호·수량·매출)는 고치지 않는다.
#  매출·수량은 네이버에서 온 사실이고, 틀렸다면 그날을 다시 수집해야 한다.
EDITABLE_PS_FIELDS = ('cost_price', 'delivery_cost', 'box_cost', 'settlement_amount')


def update_profit_rows(username: str, edits: dict) -> int:
    """월별 화면 수정분 반영. edits: {id: {필드: 값}}. 수익은 다시 계산해 저장한다.

    수익을 같이 고치지 않으면 저장값(profit)과 원가가 어긋난다 — 다른 화면은
    재계산하지만 저장값을 읽는 곳도 있어 둘이 달라지면 화면마다 수익이 다르다.
    """
    if not edits:
        return 0
    conn = get_user_db(username)
    _ensure_tables(conn)
    n = 0
    for _id, ch in edits.items():
        ch = {k: max(0, int(v or 0)) for k, v in (ch or {}).items()
              if k in EDITABLE_PS_FIELDS}
        if not ch:
            continue
        row = conn.execute("SELECT * FROM profit_settlements WHERE id=?", (int(_id),)).fetchone()
        if not row:
            continue
        r = dict(row)
        r.update(ch)
        _sets = ", ".join(f"{k}=?" for k in ch)
        conn.execute(f"UPDATE profit_settlements SET {_sets}, profit=? WHERE id=?",
                     list(ch.values()) + [calc_row_profit(r), int(_id)])
        n += 1
    conn.commit()
    conn.close()
    return n


def get_settlements_by_order_nos(username: str, order_nos: list) -> list:
    """주문번호들의 정산저장 행 전부(날짜 무관) — 달을 넘긴 중복(9/30·10/1)까지 보려고."""
    order_nos = [str(o) for o in (order_nos or []) if str(o or '').strip()]
    if not order_nos:
        return []
    conn = get_user_db(username)
    _ensure_tables(conn)
    out = []
    for i in range(0, len(order_nos), 900):          # SQLite 변수 한도
        ch = order_nos[i:i + 900]
        out += [dict(r) for r in conn.execute(
            "SELECT * FROM profit_settlements WHERE order_no IN (%s)" % ",".join("?" * len(ch)),
            ch).fetchall()]
    conn.close()
    return out


def delete_profit_rows(username: str, ids: list) -> int:
    """정산저장 행을 id로 지운다 — 같은 주문이 두 날짜에 저장된 중복 정리용."""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return 0
    conn = get_user_db(username)
    _ensure_tables(conn)
    cur = conn.execute("DELETE FROM profit_settlements WHERE id IN (%s)"
                       % ",".join("?" * len(ids)), ids)
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


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
