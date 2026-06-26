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
