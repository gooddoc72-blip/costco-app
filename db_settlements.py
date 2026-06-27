"""네이버 정산 매칭용 DB 레이어 — 정산 raw 레코드 저장/조회만 담당.

매칭/대조 비즈니스 로직은 settlement_service.py 에 위치.
"""
import sqlite3
from datetime import datetime

from db_core import get_user_db


def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS naver_settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_order_no TEXT NOT NULL,
        order_no TEXT DEFAULT '',
        settle_date TEXT NOT NULL,
        sales_amount INTEGER DEFAULT 0,
        commission INTEGER DEFAULT 0,
        settle_amount INTEGER DEFAULT 0,
        status TEXT DEFAULT '',
        raw_json TEXT DEFAULT '',
        fetched_at TEXT NOT NULL,
        UNIQUE(product_order_no, settle_date)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_settle_date ON naver_settlements(settle_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_settle_po ON naver_settlements(product_order_no)")
    # CSV 파싱용 추가 컬럼 (상품/배송비 분리)
    for col, ddl in [
        ('product_amount',  "INTEGER DEFAULT 0"),
        ('shipping_amount', "INTEGER DEFAULT 0"),
        ('settle_type',     "TEXT DEFAULT ''"),     # 빠른정산/공제
        ('reason',          "TEXT DEFAULT ''"),     # 배송시작/클레임요청
        ('buyer_name',      "TEXT DEFAULT ''"),
        ('product_name',    "TEXT DEFAULT ''"),
        ('pay_date',        "TEXT DEFAULT ''"),
        ('product_order_type', "TEXT DEFAULT ''"),  # PROD_ORDER / DELIVERY (배송비 정산)
    ]:
        try:
            conn.execute(f"ALTER TABLE naver_settlements ADD COLUMN {col} {ddl}")
        except Exception:
            pass


def save_naver_settlements(username: str, settle_date: str, records: list) -> int:
    """API 응답 정산 레코드 일괄 저장. UNIQUE(product_order_no, settle_date)로 멱등성."""
    if not records:
        return 0
    conn = get_user_db(username)
    _ensure_table(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    import json as _json
    saved = 0
    for r in records:
        po = str(r.get('productOrderId') or r.get('productOrderNo') or '').strip()
        if not po:
            continue
        # /case 응답: 수수료는 음수 항목들의 합 → 양수(공제액)로 저장
        _comm_case = -(int(r.get('totalPayCommissionAmount') or 0)
                       + int(r.get('sellingInterlockCommissionAmount') or 0)
                       + int(r.get('freeInstallmentCommissionAmount') or 0))
        _commission = (int(r.get('commission') or r.get('totalCommission') or 0)
                       or _comm_case)
        # paySettleAmount = 수수료 차감 전 결제정산, settleExpectAmount = 실정산액
        _pay_settle = int(r.get('paySettleAmount') or r.get('salesAmount')
                          or r.get('totalAmount') or 0)
        _settle = int(r.get('settleExpectAmount') or r.get('settleAmount')
                      or r.get('settlementAmount') or 0)
        conn.execute("""INSERT OR REPLACE INTO naver_settlements
            (product_order_no, order_no, settle_date,
             sales_amount, product_amount, commission, settle_amount,
             settle_type, product_name, buyer_name, pay_date,
             product_order_type, status, raw_json, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (po,
             str(r.get('orderId') or r.get('orderNo') or ''),
             settle_date,
             _pay_settle,
             _pay_settle,
             _commission,
             _settle,
             str(r.get('settleType') or ''),
             str(r.get('productName') or ''),
             str(r.get('purchaserName') or r.get('buyerName') or ''),
             str(r.get('payDate') or ''),
             str(r.get('productOrderType') or ''),
             str(r.get('status') or r.get('settleType') or ''),
             _json.dumps(r, ensure_ascii=False),
             now))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def save_naver_settlements_from_csv(username: str, records: list) -> int:
    """QuickSettleByCase CSV 파싱 결과 저장.
    records: naver_settlement_parser.parse_naver_quicksettle_csv 의 반환값.
    """
    if not records:
        return 0
    from datetime import datetime as _dt
    conn = get_user_db(username)
    _ensure_table(conn)
    now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    for r in records:
        po = str(r.get('product_order_no', '')).strip()
        if not po:
            continue
        settle_date = r.get('settle_complete_date') or r.get('settle_basis_date') or ''
        conn.execute("""INSERT OR REPLACE INTO naver_settlements
            (product_order_no, order_no, settle_date,
             sales_amount, commission, settle_amount,
             product_amount, shipping_amount,
             settle_type, reason, buyer_name, product_name, pay_date,
             status, raw_json, fetched_at)
            VALUES (?,?,?, ?,?,?, ?,?, ?,?,?,?,?, ?,?,?)""",
            (po,
             str(r.get('order_no', '')),
             settle_date,
             0, 0,
             int(r.get('total_amount', 0)),
             int(r.get('product_amount', 0)),
             int(r.get('shipping_amount', 0)),
             str(r.get('settle_type', '')),
             str(r.get('reason', '')),
             str(r.get('buyer_name', '')),
             str(r.get('product_name', '')),
             str(r.get('pay_date', '')),
             str(r.get('settle_type', '')),
             '',
             now))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_naver_settlements_by_date(username: str, settle_date: str) -> list:
    conn = get_user_db(username)
    _ensure_table(conn)
    rows = conn.execute(
        """SELECT product_order_no, order_no, settle_date,
                  sales_amount, product_amount, shipping_amount,
                  commission, settle_amount, settle_type, reason,
                  product_name, buyer_name, pay_date, product_order_type,
                  status, fetched_at
           FROM naver_settlements WHERE settle_date=?
           ORDER BY product_order_no""",
        (settle_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_naver_settlements_in_range(username: str, start_date: str, end_date: str) -> list:
    conn = get_user_db(username)
    _ensure_table(conn)
    rows = conn.execute(
        """SELECT product_order_no, order_no, settle_date,
                  sales_amount, commission, settle_amount, status
           FROM naver_settlements WHERE settle_date BETWEEN ? AND ?
           ORDER BY settle_date, product_order_no""",
        (start_date, end_date)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _ensure_coupang_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS coupang_settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT NOT NULL,
        vendor_item_id TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        settle_date TEXT NOT NULL,
        recognition_date TEXT DEFAULT '',
        sale_amount INTEGER DEFAULT 0,
        service_fee INTEGER DEFAULT 0,
        settlement_amount INTEGER DEFAULT 0,
        delivery_settlement INTEGER DEFAULT 0,
        quantity INTEGER DEFAULT 1,
        fetched_at TEXT NOT NULL,
        UNIQUE(order_id, vendor_item_id, settle_date)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cps_settle ON coupang_settlements(settle_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cps_order ON coupang_settlements(order_id)")
    try:  # 2차 지급일(30%) — 주정산 분할 반영용
        conn.execute("ALTER TABLE coupang_settlements ADD COLUMN final_settle_date TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()


def save_coupang_settlements(username: str, records: list) -> int:
    """쿠팡 revenue-history 레코드 저장 (order_id+vendor_item_id+settle_date UNIQUE)."""
    if not records:
        return 0
    conn = get_user_db(username)
    _ensure_coupang_table(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    for r in records:
        oid = str(r.get('order_id', '') or '').strip()
        if not oid:
            continue
        conn.execute("""INSERT OR REPLACE INTO coupang_settlements
            (order_id, vendor_item_id, product_name, settle_date, recognition_date,
             sale_amount, service_fee, settlement_amount, delivery_settlement, quantity,
             final_settle_date, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, str(r.get('vendor_item_id', '') or ''), str(r.get('product_name', '') or ''),
             str(r.get('settlement_date', '') or ''), str(r.get('recognition_date', '') or ''),
             int(r.get('sale_amount', 0) or 0), int(r.get('service_fee', 0) or 0),
             int(r.get('settlement_amount', 0) or 0), int(r.get('delivery_settlement', 0) or 0),
             int(r.get('quantity', 1) or 1), str(r.get('final_settlement_date', '') or ''), now))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_coupang_settlements_by_date(username: str, settle_date: str) -> list:
    conn = get_user_db(username)
    _ensure_coupang_table(conn)
    rows = conn.execute(
        "SELECT * FROM coupang_settlements WHERE settle_date=? ORDER BY order_id",
        (settle_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_coupang_settled_map(username: str) -> dict:
    """orderId별 정산 집계 — {order_id: {settlement, service_fee, delivery, settle_date}}.
    한 주문에 여러 item이면 합산. 판매-정산 대사(누락 추적)용."""
    conn = get_user_db(username)
    _ensure_coupang_table(conn)
    rows = conn.execute(
        "SELECT order_id, SUM(settlement_amount) s, SUM(service_fee) f, "
        "SUM(delivery_settlement) d, MAX(settle_date) sd, MAX(final_settle_date) fsd "
        "FROM coupang_settlements GROUP BY order_id"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        s = int(r['s'] or 0)
        sd = r['sd'] or ''
        fsd = r['fsd'] or ''
        # 2차 지급일이 없거나 1차와 같으면 월정산(100%), 다르면 주정산(70/30)
        monthly = (not fsd) or (fsd == sd)
        if monthly:
            first_amt, second_amt = s, 0
        else:
            first_amt = round(s * 0.7)
            second_amt = s - first_amt
        out[str(r['order_id'])] = {
            'settlement': s, 'service_fee': int(r['f'] or 0), 'delivery': int(r['d'] or 0),
            'settle_date': sd, 'final_settle_date': fsd,
            'cycle': '월정산' if monthly else '주정산',
            'first_amt': first_amt, 'second_amt': second_amt,
        }
    return out


def get_coupang_deposit_map(username: str, date_from: str, date_to: str) -> dict:
    """쿠팡 일별 실제 입금액 — {입금일: 금액}. 주정산은 1차일 70%·2차일 30%로 분배,
    월정산은 1차일 100%. 홈 달력의 '그날 입금' 합산용."""
    conn = get_user_db(username)
    _ensure_coupang_table(conn)
    rows = conn.execute(
        "SELECT settlement_amount s, settle_date sd, final_settle_date fsd FROM coupang_settlements"
    ).fetchall()
    conn.close()
    dep = {}
    for r in rows:
        s = int(r['s'] or 0)
        sd = r['sd'] or ''
        fsd = r['fsd'] or ''
        if not s or not sd:
            continue
        if not fsd or fsd == sd:  # 월정산: 1차일에 100%
            dep[sd] = dep.get(sd, 0) + s
        else:  # 주정산: 1차일 70% / 2차일 30%
            first = round(s * 0.7)
            dep[sd] = dep.get(sd, 0) + first
            dep[fsd] = dep.get(fsd, 0) + (s - first)
    return {d: v for d, v in dep.items() if date_from <= d <= date_to}


def get_coupang_settle_dates(username: str, limit: int = 60) -> list:
    conn = get_user_db(username)
    _ensure_coupang_table(conn)
    rows = conn.execute(
        "SELECT DISTINCT settle_date FROM coupang_settlements ORDER BY settle_date DESC LIMIT ?",
        (int(limit),)
    ).fetchall()
    conn.close()
    return [r['settle_date'] for r in rows]


def _ensure_match_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS settlement_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        settle_date TEXT NOT NULL,
        product_order_no TEXT NOT NULL,
        ship_date TEXT DEFAULT '',
        expected INTEGER DEFAULT 0,
        actual INTEGER DEFAULT 0,
        commission INTEGER DEFAULT 0,
        diff INTEGER DEFAULT 0,
        diff_reason TEXT DEFAULT '',
        settle_type TEXT DEFAULT '',
        match_status TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        buyer_name TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(settle_date, product_order_no)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_smatch_po ON settlement_matches(product_order_no)")
    conn.commit()


def save_settlement_matches(username: str, settle_date: str, rows: list) -> int:
    """역추적 매칭 결과 저장 (settle_date 단위로 덮어씀). rows: match_settled_to_dispatch의
    matched/mismatched/no_dispatch 항목 dict 리스트 (match_status 포함)."""
    conn = get_user_db(username)
    _ensure_match_table(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM settlement_matches WHERE settle_date=?", (settle_date,))
    saved = 0
    for r in rows:
        po = str(r.get('product_order_no', '') or '').strip()
        if not po:
            continue
        conn.execute("""INSERT OR REPLACE INTO settlement_matches
            (settle_date, product_order_no, ship_date, expected, actual, commission,
             diff, diff_reason, settle_type, match_status, product_name, buyer_name, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (settle_date, po, str(r.get('ship_date', '') or ''),
             int(r.get('expected', 0) or 0), int(r.get('actual', 0) or 0),
             int(r.get('commission', 0) or 0), int(r.get('diff', 0) or 0),
             str(r.get('diff_reason', '') or ''), str(r.get('settle_type', '') or ''),
             str(r.get('match_status', '') or ''), str(r.get('product_name', '') or ''),
             str(r.get('buyer_name', '') or ''), now))
        saved += 1
    conn.commit()
    conn.close()
    return saved


def get_settlement_matches(username: str, settle_date: str) -> list:
    conn = get_user_db(username)
    _ensure_match_table(conn)
    rows = conn.execute(
        "SELECT * FROM settlement_matches WHERE settle_date=? ORDER BY match_status, product_order_no",
        (settle_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_actual_settlements_map(username: str, order_nos=None) -> dict:
    """상품주문번호 → 저장된 실제 정산액 매핑 (수익계산 실정산 반영용).
    같은 주문번호 여러 정산일이면 최신 정산일 우선. order_nos 미지정 시 전체."""
    conn = get_user_db(username)
    _ensure_match_table(conn)
    sql = ("SELECT product_order_no, actual, commission, settle_date, settle_type "
           "FROM settlement_matches WHERE actual > 0 ORDER BY settle_date")
    rows = conn.execute(sql).fetchall()
    conn.close()
    want = set(str(o) for o in order_nos) if order_nos else None
    out = {}
    for r in rows:  # 오래된→최신 순회, 최신이 덮어씀
        po = str(r['product_order_no'])
        if want is not None and po not in want:
            continue
        out[po] = {'actual': int(r['actual'] or 0), 'commission': int(r['commission'] or 0),
                   'settle_date': r['settle_date'], 'settle_type': r['settle_type']}
    return out


def get_settled_product_order_nos(username: str) -> set:
    """정산된 상품주문번호 집합 (전체 날짜, 배송비 라인 제외).
    미정산 추적용 — 발송건이 이 집합에 없으면 아직 정산 안 됨.
    """
    conn = get_user_db(username)
    _ensure_table(conn)
    try:
        rows = conn.execute(
            "SELECT DISTINCT product_order_no FROM naver_settlements "
            "WHERE COALESCE(product_order_type,'') != 'DELIVERY'"
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT DISTINCT product_order_no FROM naver_settlements"
        ).fetchall()
    conn.close()
    return {str(r['product_order_no']) for r in rows if r['product_order_no']}


def delete_naver_settlements_by_date(username: str, settle_date: str) -> int:
    conn = get_user_db(username)
    _ensure_table(conn)
    cur = conn.execute(
        "DELETE FROM naver_settlements WHERE settle_date=?", (settle_date,)
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n
