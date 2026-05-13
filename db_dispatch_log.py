"""일괄발송 성공 이력 — 정산 매칭의 기준 데이터.

송장번호 등록 → 네이버/쿠팡 일괄발송처리 API 호출 후 성공한 주문 리스트를 저장.
다음날 정산매칭에서 이 데이터의 expected_settlement 합계와 daily 정산 합계를 비교.
"""
from datetime import datetime

from db_core import get_user_db


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
