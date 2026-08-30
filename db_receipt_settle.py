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
    # 매칭 출처 — number(코스트코번호 정확) / name(상품명 유사도) / manual(수동·AI)
    #   청구 근거가 '영수증 확정'인지 '이름으로 추정'인지 구분하려면 반드시 남아야 한다.
    #   계산은 되고 있었는데 저장에서 빠져 있어, 청구서가 근거를 구분할 수 없었다.
    try:
        conn.execute("ALTER TABLE receipt_settle_items ADD COLUMN via TEXT DEFAULT ''")
    except Exception:
        pass

    # 부족분 — 그날 주문은 있는데 영수증에서 못 찾은 건 (안 샀거나 매칭 실패)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_settle_shortages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id     INTEGER,
            username     TEXT,
            order_no     TEXT,
            order_date   TEXT,
            recipient    TEXT,
            naver_no     TEXT,
            product_name TEXT,
            qty          INTEGER DEFAULT 0,
            prev_cost    INTEGER DEFAULT 0,
            created_at   TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rss_batch ON receipt_settle_shortages(batch_id)")

    # 재고분 — 영수증으로 산 수량 중 그날 주문에 쓰이지 않고 남은 것
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_settle_leftovers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id     INTEGER,
            costco_no    TEXT,
            name         TEXT,
            unit_price   INTEGER DEFAULT 0,
            qty_receipt  INTEGER DEFAULT 0,
            split_qty    INTEGER DEFAULT 1,
            units_in     INTEGER DEFAULT 0,
            units_used   INTEGER DEFAULT 0,
            units_left   INTEGER DEFAULT 0,
            created_at   TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rsl_batch ON receipt_settle_leftovers(batch_id)")
    conn.commit()


def save_settlement_batch(label, date_from, date_to, receipt_dates,
                          rows, created_by, shortages=None, leftovers=None):
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
            " qty,unit_price,amount,prev_cost,via,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, r.get('username', ''), r.get('order_no', ''), r.get('order_date', ''),
             r.get('costco_no', ''), r.get('naver_no', ''), r.get('product_name', ''),
             int(r.get('qty', 0) or 0), int(r.get('unit_price', 0) or 0),
             int(r.get('amount', 0) or 0), int(r.get('prev_cost', 0) or 0),
             str(r.get('via', '') or ''), now),
        )
    for sh in (shortages or []):
        conn.execute(
            "INSERT INTO receipt_settle_shortages "
            "(batch_id,username,order_no,order_date,recipient,naver_no,product_name,"
            " qty,prev_cost,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (bid, sh.get('username', ''), sh.get('order_no', ''), sh.get('order_date', ''),
             sh.get('recipient', ''), sh.get('naver_no', ''), sh.get('product_name', ''),
             int(sh.get('qty', 0) or 0), int(sh.get('prev_cost', 0) or 0), now),
        )
    for lf in (leftovers or []):
        conn.execute(
            "INSERT INTO receipt_settle_leftovers "
            "(batch_id,costco_no,name,unit_price,qty_receipt,split_qty,"
            " units_in,units_used,units_left,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (bid, lf.get('costco_no', ''), lf.get('name', ''),
             int(lf.get('unit_price', 0) or 0), int(lf.get('qty_receipt', 0) or 0),
             int(lf.get('split_qty', 1) or 1), int(lf.get('units_in', 0) or 0),
             int(lf.get('units_used', 0) or 0), int(lf.get('units_left', 0) or 0), now),
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


def get_settlement_shortages(batch_id):
    """부족분 — 주문은 있는데 영수증에서 못 찾은 건."""
    conn = _conn(); _ensure(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM receipt_settle_shortages WHERE batch_id=? ORDER BY username, order_no",
        (batch_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_settlement_leftovers(batch_id):
    """재고분 — 영수증 구매 수량 중 그날 주문에 안 쓰이고 남은 것."""
    conn = _conn(); _ensure(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM receipt_settle_leftovers WHERE batch_id=? ORDER BY units_left DESC",
        (batch_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_billing_basis(batch_id):
    """사용자별 청구 근거 분해 — 확정(번호)/추정(이름)/수동 금액을 나눠 집계.

    청구액만 보여주면 그 돈이 영수증 실단가인지 이름으로 추측한 값인지 알 수 없다.
    분쟁을 막으려면 근거를 갈라 보여줘야 한다.
    """
    conn = _conn(); _ensure(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT username, COALESCE(via,'') via, COUNT(*) n, SUM(amount) amt "
        "FROM receipt_settle_items WHERE batch_id=? GROUP BY username, via",
        (batch_id,)).fetchall()
    conn.close()
    out = {}
    for r in rows:
        u = out.setdefault(r["username"], {"확정": [0, 0], "추정": [0, 0], "수동": [0, 0]})
        key = {"number": "확정", "name": "추정"}.get(r["via"], "수동")
        u[key][0] += int(r["n"] or 0)
        u[key][1] += int(r["amt"] or 0)
    return out


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


def _ensure_billing(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_billing (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_date   TEXT,
            username    TEXT,
            order_count INTEGER DEFAULT 0,
            amount      INTEGER DEFAULT 0,
            created_by  TEXT,
            created_at  TEXT,
            UNIQUE(bill_date, username)
        )
    """)
    conn.commit()


def save_daily_billing(bill_date, rows, created_by):
    """일일 판매자 청구서 저장 (덮어쓰기). rows: [{username, order_count, amount}]."""
    conn = _conn()
    _ensure_billing(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 같은 날짜 기존 저장분 제거 후 재저장
    conn.execute("DELETE FROM daily_billing WHERE bill_date=?", (str(bill_date),))
    for r in rows:
        conn.execute(
            "INSERT INTO daily_billing (bill_date,username,order_count,amount,created_by,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (str(bill_date), r.get('username', ''), int(r.get('order_count', 0) or 0),
             int(r.get('amount', 0) or 0), created_by, now))
    conn.commit()
    conn.close()


def get_daily_billing(bill_date):
    conn = _conn()
    _ensure_billing(conn)
    rows = conn.execute(
        "SELECT * FROM daily_billing WHERE bill_date=? ORDER BY amount DESC",
        (str(bill_date),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_billing_dates(limit=60):
    conn = _conn()
    _ensure_billing(conn)
    rows = conn.execute(
        "SELECT bill_date, COUNT(*) sellers, SUM(amount) total, MAX(created_at) at "
        "FROM daily_billing GROUP BY bill_date ORDER BY bill_date DESC LIMIT ?",
        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
