"""포장 가격 DB — 박스/아이스박스/아이스팩 등 포장 항목 단가(관리자 설정)와
주문별 포장 배정(발송 시 관리자가 주문마다 선택)을 관리한다.

- packaging_prices: 포장 항목 단가 카탈로그 (관리자 CRUD)
- order_packaging:   (판매자, 주문번호) → 선택한 포장 + 계산된 포장비합
                     → 수익계산의 '박스원가'로 연동된다.

모두 auth.db 에 저장 (관리자 공용).
"""
import json
import sqlite3
from datetime import datetime

from db_core import AUTH_DB

KINDS = ('box', 'icebox', 'icepack', 'etc')
KIND_LABEL = {'box': '박스', 'icebox': '아이스박스', 'icepack': '아이스팩', 'etc': '기타'}


def _conn():
    conn = sqlite3.connect(AUTH_DB, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS packaging_prices (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            kind       TEXT,
            name       TEXT,
            price      INTEGER DEFAULT 0,
            active     INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_packaging (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT,
            order_no     TEXT,
            box_id       INTEGER,
            box_name     TEXT,
            box_price    INTEGER DEFAULT 0,
            icebox_qty   INTEGER DEFAULT 0,
            icebox_price INTEGER DEFAULT 0,
            icepack_qty  INTEGER DEFAULT 0,
            icepack_price INTEGER DEFAULT 0,
            etc_json     TEXT,
            total_cost   INTEGER DEFAULT 0,
            updated_by   TEXT,
            updated_at   TEXT,
            UNIQUE(username, order_no)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_op_user ON order_packaging(username)")
    conn.commit()


# ── 포장 항목 단가 카탈로그 ──
def list_packaging_prices(kind=None, active_only=False):
    conn = _conn()
    _ensure(conn)
    q = "SELECT * FROM packaging_prices WHERE 1=1"
    p = []
    if kind:
        q += " AND kind=?"
        p.append(kind)
    if active_only:
        q += " AND active=1"
    q += " ORDER BY kind, sort_order, id"
    rows = conn.execute(q, p).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_packaging_price(name, price, kind, item_id=None, active=1, sort_order=0):
    conn = _conn()
    _ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if item_id:
        conn.execute(
            "UPDATE packaging_prices SET name=?, price=?, kind=?, active=?, sort_order=?, updated_at=? WHERE id=?",
            (name, int(price or 0), kind, int(active), int(sort_order or 0), now, item_id))
    else:
        conn.execute(
            "INSERT INTO packaging_prices (kind,name,price,active,sort_order,updated_at) VALUES (?,?,?,?,?,?)",
            (kind, name, int(price or 0), int(active), int(sort_order or 0), now))
    conn.commit()
    conn.close()


def delete_packaging_price(item_id):
    conn = _conn()
    _ensure(conn)
    conn.execute("DELETE FROM packaging_prices WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


# ── 주문별 포장 배정 ──
def get_order_packaging(username, order_no):
    conn = _conn()
    _ensure(conn)
    r = conn.execute(
        "SELECT * FROM order_packaging WHERE username=? AND order_no=?",
        (username, str(order_no))).fetchone()
    conn.close()
    return dict(r) if r else None


def get_packaging_cost_map(username, order_nos):
    """{order_no: total_cost} — 수익계산 박스원가 연동용."""
    onos = [str(o).strip() for o in (order_nos or []) if str(o).strip()]
    if not onos:
        return {}
    conn = _conn()
    _ensure(conn)
    ph = ",".join("?" * len(onos))
    rows = conn.execute(
        f"SELECT order_no, total_cost FROM order_packaging "
        f"WHERE username=? AND order_no IN ({ph})", [username, *onos]).fetchall()
    conn.close()
    return {str(r['order_no']): int(r['total_cost'] or 0) for r in rows}


def set_order_packaging(username, order_no, box_id=None, icebox_qty=0, icepack_qty=0,
                        etc_items=None, updated_by=""):
    """주문 포장 배정 저장 + 포장비합 계산. etc_items: [{id,name,price,qty}]. Returns total_cost."""
    conn = _conn()
    _ensure(conn)
    prices = {r['id']: dict(r) for r in conn.execute("SELECT * FROM packaging_prices").fetchall()}
    box_name, box_price = '', 0
    if box_id and int(box_id) in prices:
        box_name = prices[int(box_id)]['name']
        box_price = int(prices[int(box_id)]['price'] or 0)
    icebox_price = next((int(p['price'] or 0) for p in prices.values() if p['kind'] == 'icebox'), 0)
    icepack_price = next((int(p['price'] or 0) for p in prices.values() if p['kind'] == 'icepack'), 0)
    icebox_qty = int(icebox_qty or 0)
    icepack_qty = int(icepack_qty or 0)
    etc_items = etc_items or []
    etc_total = sum(int(e.get('price', 0) or 0) * int(e.get('qty', 1) or 1) for e in etc_items)
    total = box_price + icebox_qty * icebox_price + icepack_qty * icepack_price + etc_total
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO order_packaging
          (username,order_no,box_id,box_name,box_price,icebox_qty,icebox_price,
           icepack_qty,icepack_price,etc_json,total_cost,updated_by,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(username,order_no) DO UPDATE SET
          box_id=excluded.box_id, box_name=excluded.box_name, box_price=excluded.box_price,
          icebox_qty=excluded.icebox_qty, icebox_price=excluded.icebox_price,
          icepack_qty=excluded.icepack_qty, icepack_price=excluded.icepack_price,
          etc_json=excluded.etc_json, total_cost=excluded.total_cost,
          updated_by=excluded.updated_by, updated_at=excluded.updated_at
    """, (username, str(order_no), int(box_id) if box_id else None, box_name, box_price,
          icebox_qty, icebox_price, icepack_qty, icepack_price,
          json.dumps(etc_items, ensure_ascii=False), total, updated_by, now))
    conn.commit()
    conn.close()
    return total


def clear_order_packaging(username, order_no):
    conn = _conn()
    _ensure(conn)
    conn.execute("DELETE FROM order_packaging WHERE username=? AND order_no=?",
                 (username, str(order_no)))
    conn.commit()
    conn.close()
