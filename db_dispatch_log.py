"""일괄발송 성공 이력 — 정산 매칭의 기준 데이터.

송장번호 등록 → 네이버/쿠팡 일괄발송처리 API 호출 후 성공한 주문 리스트를 저장.
다음날 정산매칭에서 이 데이터의 expected_settlement 합계와 daily 정산 합계를 비교.

재고 차감도 여기에 걸려 있다 — 발송 = 실제 판매 확정 시점.
"""
from datetime import datetime

from db_core import get_user_db


def _pack_factor(conn, product_no: str, product_name: str) -> int:
    """1주문이 소비하는 개수. services.resolve_pack_factor와 동일 해석.

    상품명의 "x N개"는 상품마다 뜻이 달라(내용물 설명 vs 진짜 묶음) 상품명만으로는
    판정할 수 없다 → products.pack_multiplier 지정값을 우선한다.
    """
    from services import resolve_pack_factor
    prod = None
    try:
        r = conn.execute(
            "SELECT COALESCE(pack_multiplier,0) AS pack_multiplier FROM products "
            "WHERE product_no=? ORDER BY updated_at DESC LIMIT 1",
            (str(product_no or ''),)).fetchone()
        if r:
            prod = {'pack_multiplier': int(r['pack_multiplier'] or 0)}
    except Exception:
        prod = None
    return resolve_pack_factor(prod, product_name)


def _resolve_order_items(conn, order_nos: list) -> dict:
    """order_no → {product_no, qty, product_name}.

    dispatch_log에는 상품번호도 수량도 없다(product_name 자유 텍스트뿐).
    order_history를 JOIN해야 상품을 특정할 수 있다.
    """
    out = {}
    order_nos = [str(o).strip() for o in (order_nos or []) if str(o).strip()]
    if not order_nos:
        return out
    CHUNK = 900   # SQLite 변수 한도 회피
    for i in range(0, len(order_nos), CHUNK):
        chunk = order_nos[i:i + CHUNK]
        ph = ",".join("?" * len(chunk))
        try:
            rows = conn.execute(
                f"""SELECT order_no, COALESCE(product_no,'') AS product_no,
                           COALESCE(qty,1) AS qty, COALESCE(product_name,'') AS product_name
                    FROM order_history WHERE order_no IN ({ph})""", chunk).fetchall()
        except Exception:
            return out   # order_history 미존재 — 재고 차감 생략
        for r in rows:
            out[str(r['order_no'])] = dict(r)
    return out


def _consume_inventory(conn, username: str, orders: list, dispatched_at: str,
                       platform: str) -> dict:
    """발송된 주문만큼 재고를 차감. 재고 관리 대상이 아니면 조용히 넘어간다.

    차감 수량 = 주문수량 × sell_factor (소분 단위).
    재고 실패가 발송 기록을 막으면 안 되므로 전체를 예외로 감싼다.
    """
    res = {'consumed': 0, 'shortage': 0, 'cross': 0}
    try:
        from db_inventory import consume_for_sale
    except Exception:
        return res

    order_nos = [str(o.get('order_no') or o.get('상품주문번호') or '').strip()
                 for o in orders]
    items = _resolve_order_items(conn, order_nos)
    if not items:
        return res

    for o in orders:
        ono = str(o.get('order_no') or o.get('상품주문번호') or '').strip()
        if not ono:
            continue
        it = items.get(ono)
        if not it:
            continue
        pno = str(it.get('product_no') or '').strip()
        if not pno:
            continue   # 코스트코 번호 없는 주문 — 재고 매칭 불가
        try:
            qty = max(1, int(it.get('qty') or 1))
        except (TypeError, ValueError):
            qty = 1
        units = qty * _pack_factor(conn, pno,
                                   it.get('product_name') or o.get('product_name') or '')
        try:
            r = consume_for_sale(username, pno, units, ono,
                                 dispatched_at=dispatched_at, platform=platform)
            res['consumed'] += int(r.get('consumed') or 0)
            res['shortage'] += int(r.get('shortage') or 0)
            res['cross'] += sum(1 for m in (r.get('moves') or []) if m.get('is_cross'))
        except Exception:
            continue
    return res


def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS dispatch_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT NOT NULL,
        dispatched_at TEXT NOT NULL,
        recipient TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        expected_settlement INTEGER DEFAULT 0,
        tracking_no TEXT DEFAULT '',
        courier TEXT DEFAULT '',
        platform TEXT DEFAULT 'naver',
        created_at TEXT NOT NULL,
        UNIQUE(order_no, dispatched_at)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_date ON dispatch_log(dispatched_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_platform ON dispatch_log(platform)")
    # 고객 결제 배송비 — 정산 매칭 시 배송비 수수료 분석에 사용
    try:
        conn.execute("ALTER TABLE dispatch_log ADD COLUMN customer_shipping_fee INTEGER DEFAULT 0")
    except Exception:
        pass


def log_dispatch_success(username: str, orders: list, dispatched_at: str,
                         platform: str = 'naver') -> int:
    """일괄발송 성공한 주문들을 dispatch_log에 저장 (idempotent: UNIQUE on order_no+date).

    orders: [{'order_no'|'상품주문번호', 'recipient'|'수취인명',
              'product_name'|'상품명', 'expected_settlement'|'정산예정금액',
              'tracking_no', 'courier'}, ...]

    재고 차감도 함께 수행한다 (order_no 기준 멱등 — 재실행해도 중복 차감 없음).
    """
    if not orders:
        return 0
    conn = get_user_db(username)
    _ensure_table(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    for o in orders:
        order_no = str(o.get('order_no') or o.get('상품주문번호') or '').strip()
        if not order_no:
            continue
        conn.execute("""INSERT OR REPLACE INTO dispatch_log
            (order_no, dispatched_at, recipient, product_name,
             expected_settlement, tracking_no, courier, platform,
             customer_shipping_fee, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (order_no, dispatched_at,
             str(o.get('recipient') or o.get('수취인명') or ''),
             str(o.get('product_name') or o.get('상품명') or ''),
             int(o.get('expected_settlement') or o.get('정산예정금액') or 0),
             str(o.get('tracking_no') or o.get('송장번호') or ''),
             str(o.get('courier') or o.get('택배사') or ''),
             platform,
             int(o.get('customer_shipping_fee') or o.get('배송비 합계') or 0),
             now))
        saved += 1
    conn.commit()
    # 재고 차감 — 발송 확정 = 판매 확정. 실패해도 발송 기록은 유지한다.
    try:
        _consume_inventory(conn, username, orders, dispatched_at, platform)
    except Exception:
        pass
    conn.close()
    return saved


def get_dispatch_log_by_date(username: str, date: str, platform: str = None) -> list:
    """특정 날짜의 일괄발송 성공 이력 조회. platform 지정 시 필터."""
    conn = get_user_db(username)
    _ensure_table(conn)
    if platform:
        rows = conn.execute(
            "SELECT * FROM dispatch_log WHERE dispatched_at=? AND platform=? ORDER BY id",
            (date, platform)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM dispatch_log WHERE dispatched_at=? ORDER BY id",
            (date,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_dispatch_by_order_nos(username: str, order_nos: list,
                              platform: str = None) -> dict:
    """상품주문번호 리스트로 dispatch_log 역조회 (날짜 무관).

    정산일 기준 역추적 매칭용 — 정산건의 상품주문번호로 원래 발송건을 찾는다.
    Returns: {order_no: dispatch_row_dict}. 같은 order_no 중복 시 최신 발송 1건.
    """
    order_nos = [str(o).strip() for o in (order_nos or []) if str(o).strip()]
    if not order_nos:
        return {}
    conn = get_user_db(username)
    _ensure_table(conn)
    out = {}
    CHUNK = 900  # SQLite 변수 한도 회피
    for i in range(0, len(order_nos), CHUNK):
        chunk = order_nos[i:i + CHUNK]
        ph = ",".join("?" * len(chunk))
        sql = (f"SELECT * FROM dispatch_log WHERE order_no IN ({ph})"
               + (" AND platform=?" if platform else "")
               + " ORDER BY dispatched_at")  # 오래된 것부터 → 최신이 덮어씀
        params = chunk + ([platform] if platform else [])
        for r in conn.execute(sql, params).fetchall():
            out[str(r['order_no'])] = dict(r)
    conn.close()
    return out


def get_dispatch_by_order_id(username: str, order_ids: list,
                             platform: str = 'coupang') -> dict:
    """쿠팡 orderId 리스트로 dispatch_log 역조회.
    쿠팡 발송 order_no는 '{orderId}-{orderItemId}' 형식 → orderId로 매칭.
    Returns: {order_id: dispatch_row}."""
    order_ids = [str(o).strip() for o in (order_ids or []) if str(o).strip()]
    if not order_ids:
        return {}
    conn = get_user_db(username)
    _ensure_table(conn)
    out = {}
    for oid in order_ids:
        r = conn.execute(
            "SELECT * FROM dispatch_log WHERE platform=? AND (order_no=? OR order_no LIKE ?) "
            "ORDER BY dispatched_at LIMIT 1",
            (platform, oid, oid + '-%')
        ).fetchone()
        if r:
            out[oid] = dict(r)
    conn.close()
    return out


def get_dispatched_orders_with_details(username: str, dispatched_at: str,
                                       platform: str = None) -> list:
    """일괄발송 성공건 + order_history 상세 정보 JOIN 조회.

    수익계산 페이지의 새 데이터 소스 (daily_orders 대체).
    "이 날짜에 발송된 주문" = "이 날짜의 수익계산 대상" 으로 일치 보장.

    Returns: dict 리스트. 키:
        order_no, dispatched_at, platform, tracking_no, courier,
        recipient, product_name, option_info, product_no, qty,
        order_amount, shipping_fee, settlement, cost_price, profit
    """
    conn = get_user_db(username)
    _ensure_table(conn)
    # order_history 테이블이 존재해야 JOIN 가능 (db_products.init_user_db 에서 생성)
    base_sql = """
        SELECT
            dl.order_no                                            AS order_no,
            dl.dispatched_at                                       AS dispatched_at,
            dl.platform                                            AS platform,
            dl.tracking_no                                         AS tracking_no,
            dl.courier                                             AS courier,
            COALESCE(oh.recipient,    dl.recipient)                AS recipient,
            COALESCE(oh.product_name, dl.product_name)             AS product_name,
            COALESCE(oh.option_info, '')                            AS option_info,
            COALESCE(oh.product_no, '')                             AS product_no,
            COALESCE(oh.qty, 1)                                     AS qty,
            COALESCE(oh.order_amount, 0)                            AS order_amount,
            COALESCE(oh.shipping_fee, dl.customer_shipping_fee, 0)  AS shipping_fee,
            COALESCE(oh.settlement,   dl.expected_settlement, 0)    AS settlement,
            COALESCE(oh.cost_price, 0)                              AS cost_price,
            COALESCE(oh.profit, 0)                                  AS profit
        FROM dispatch_log dl
        LEFT JOIN order_history oh ON dl.order_no = oh.order_no
        WHERE dl.dispatched_at = ?
    """
    params = [dispatched_at]
    if platform:
        base_sql += " AND dl.platform = ?"
        params.append(platform)
    base_sql += " ORDER BY COALESCE(oh.product_name, dl.product_name), dl.id"

    try:
        rows = conn.execute(base_sql, params).fetchall()
    except Exception:
        # order_history 테이블 미존재 등 예외 — dispatch_log만 반환
        rows = conn.execute(
            "SELECT * FROM dispatch_log WHERE dispatched_at=? ORDER BY id",
            (dispatched_at,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_dispatch_counts(username: str, date_from: str, date_to: str) -> dict:
    """기간 내 일별 발송건수 — {dispatched_at: count}. (달력 표시용)"""
    conn = get_user_db(username)
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT dispatched_at, COUNT(*) c FROM dispatch_log "
        "WHERE dispatched_at BETWEEN ? AND ? GROUP BY dispatched_at",
        (date_from, date_to)
    ).fetchall()
    conn.close()
    return {r['dispatched_at']: int(r['c']) for r in rows}


def get_dispatch_dates(username: str, limit: int = 30) -> list:
    """일괄발송 이력이 있는 최근 날짜 목록."""
    conn = get_user_db(username)
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT DISTINCT dispatched_at FROM dispatch_log ORDER BY dispatched_at DESC LIMIT ?",
        (int(limit),)
    ).fetchall()
    conn.close()
    return [r['dispatched_at'] for r in rows]
