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
        conn.execute("""INSERT OR REPLACE INTO naver_settlements
            (product_order_no, order_no, settle_date,
             sales_amount, commission, settle_amount, status, raw_json, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (po,
             str(r.get('orderId') or r.get('orderNo') or ''),
             settle_date,
             int(r.get('salesAmount') or r.get('totalAmount') or 0),
             int(r.get('commission') or r.get('totalCommission') or 0),
             int(r.get('settleAmount') or r.get('settlementAmount') or 0),
             str(r.get('status') or r.get('settleStatus') or ''),
             _json.dumps(r, ensure_ascii=False),
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
                  sales_amount, commission, settle_amount, status, fetched_at
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
