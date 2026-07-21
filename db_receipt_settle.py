"""영수증 정산 — 관리자가 코스트코 영수증을 각 사용자 주문에 배치하고
사용자별 구매금액을 정산한 '배치(batch)'를 auth.db에 기록한다.

- receipt_settle_batches: 정산 1회분(영수증 업로드→적용) 요약
- receipt_settle_items:   배치 내 개별 배치행 (사용자·주문·품목·금액)

주문 자체의 cost_price 갱신은 services.apply_receipt_settlement 가 각 사용자
DB(order_history/profit_settlements)에서 수행한다. 이 모듈은 '정산 이력'만 담는다.
"""
import sqlite3
from datetime import datetime

from db_core import AUTH_DB


def _conn():
    conn = sqlite3.connect(AUTH_DB, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_settle_batches (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            label        TEXT,
            date_from    TEXT,
            date_to      TEXT,
            receipt_dates TEXT,
            order_count  INTEGER DEFAULT 0,
            total_amount INTEGER DEFAULT 0,
            created_by   TEXT,
            created_at   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_settle_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id     INTEGER,
            username     TEXT,
            order_no     TEXT,
            order_date   TEXT,
            costco_no    TEXT,
            naver_no     TEXT,
            product_name TEXT,
            qty          INTEGER DEFAULT 0,
            unit_price   INTEGER DEFAULT 0,
            amount       INTEGER DEFAULT 0,
            prev_cost    INTEGER DEFAULT 0,
            created_at   TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rsi_batch ON receipt_settle_items(batch_id)")
    conn.commit()


def save_settlement_batch(label, date_from, date_to, receipt_dates,
                          rows, created_by):
    """정산 배치와 배치행 저장 → batch_id 반환.

    rows: [{username, order_no, order_date, costco_no, naver_no,
            product_name, qty, unit_price, amount, prev_cost}, ...]
    """
    conn = _conn()
    _ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = sum(int(r.get('amount', 0) or 0) for r in rows)
    cur = conn.execute(
        "INSERT INTO receipt_settle_batches "
        "(label,date_from,date_to,receipt_dates,order_count,total_amount,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (label, date_from, date_to, receipt_dates, len(rows), total, created_by, now),
    )
    bid = cur.lastrowid
    for r in rows:
        conn.execute(
            "INSERT INTO receipt_settle_items "
            "(batch_id,username,order_no,order_date,costco_no,naver_no,product_name,"
            " qty,unit_price,amount,prev_cost,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, r.get('username', ''), r.get('order_no', ''), r.get('order_date', ''),
             r.get('costco_no', ''), r.get('naver_no', ''), r.get('product_name', ''),
             int(r.get('qty', 0) or 0), int(r.get('unit_price', 0) or 0),
             int(r.get('amount', 0) or 0), int(r.get('prev_cost', 0) or 0), now),
        )
    conn.commit()
    conn.close()
    return bid


def list_settlement_batches(limit=50):
    conn = _conn()
    _ensure(conn)
    rows = conn.execute(
        "SELECT * FROM receipt_settle_batches ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_settlement_items(batch_id):
    conn = _conn()
    _ensure(conn)
    rows = conn.execute(
        "SELECT * FROM receipt_settle_items WHERE batch_id=? ORDER BY username, product_name",
        (batch_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_settlement_summary(batch_id):
    """배치별 사용자 합계 → [{username, item_count, qty, amount}]."""
    conn = _conn()
    _ensure(conn)
    rows = conn.execute(
        "SELECT username, COUNT(*) item_count, COALESCE(SUM(qty),0) qty, "
        "       COALESCE(SUM(amount),0) amount "
        "FROM receipt_settle_items WHERE batch_id=? GROUP BY username ORDER BY amount DESC",
        (batch_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_settlement_batch(batch_id):
    conn = _conn()
    _ensure(conn)
    conn.execute("DELETE FROM receipt_settle_items WHERE batch_id=?", (batch_id,))
    conn.execute("DELETE FROM receipt_settle_batches WHERE id=?", (batch_id,))
    conn.commit()
    conn.close()


def _recompute_batches(conn, batch_ids):
    """배치별 order_count/total_amount 재집계. 항목 0개면 배치도 삭제."""
    for bid in set(batch_ids):
        row = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(amount),0) s "
            "FROM receipt_settle_items WHERE batch_id=?", (bid,)
        ).fetchone()
        cnt, amt = int(row[0]), int(row[1])
        if cnt == 0:
            conn.execute("DELETE FROM receipt_settle_batches WHERE id=?", (bid,))
        else:
            conn.execute(
                "UPDATE receipt_settle_batches SET order_count=?, total_amount=? WHERE id=?",
                (cnt, amt, bid)
            )


def remove_settlement_items(username, order_nos):
    """특정 사용자의 주문번호들을 모든 정산 배치에서 제거하고 배치 합계 재집계.
    주문 삭제 시 호출 → 구매 정산 내역 정합성 유지. Returns: 제거된 항목 수."""
    onos = [str(o).strip() for o in (order_nos or []) if str(o).strip()]
    if not onos:
        return 0
    conn = _conn()
    _ensure(conn)
    ph = ",".join("?" * len(onos))
    affected = [r[0] for r in conn.execute(
        f"SELECT DISTINCT batch_id FROM receipt_settle_items "
        f"WHERE username=? AND order_no IN ({ph})", [username, *onos]
    ).fetchall()]
    if not affected:
        conn.close()
        return 0
    removed = conn.execute(
        f"DELETE FROM receipt_settle_items WHERE username=? AND order_no IN ({ph})",
        [username, *onos]
    ).rowcount
    _recompute_batches(conn, affected)
    conn.commit()
    conn.close()
    return removed


def iter_all_settlement_item_orders():
    """모든 정산 항목의 (id, username, order_no, batch_id) — orphan 청소용."""
    conn = _conn()
    _ensure(conn)
    rows = conn.execute(
        "SELECT id, username, order_no, batch_id FROM receipt_settle_items"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_settlement_items_by_id(item_ids, batch_ids):
    """항목 id로 직접 삭제 후 관련 배치 재집계. Returns: 삭제 수."""
    ids = [int(i) for i in (item_ids or [])]
    if not ids:
        return 0
    conn = _conn()
    _ensure(conn)
    ph = ",".join("?" * len(ids))
    removed = conn.execute(
        f"DELETE FROM receipt_settle_items WHERE id IN ({ph})", ids
    ).rowcount
    _recompute_batches(conn, batch_ids or [])
    conn.commit()
    conn.close()
    return removed
