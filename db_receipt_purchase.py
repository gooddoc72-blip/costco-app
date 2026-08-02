"""매입 영수증 장부 — 휴대폰으로 찍은 코스트코/트레이더스 영수증 사진의 파싱 결과 저장.

재고관리용 매입 원장. 사용자 DB(data/{username}.db)에 저장한다.
  receipt_purchases      — 영수증 1장 = 1행 (구매일자·매장·수량·금액·할인·카드·현금영수증)
  receipt_purchase_items — 영수증의 품목 내역 (재고 입고 수량 산출용)

⚠️ 상품 매입가 공유DB 반영은 기존 receipt_items/upsert_shared_store_price 경로가 담당한다.
   이 모듈은 '영수증 장부(매입 이력)'만 담는다.
"""
import sqlite3
from datetime import datetime

from db_core import get_user_db

STORE_TYPES = ["코스트코", "트레이더스"]


def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_purchases (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date   TEXT,              -- 구매일자 YYYY-MM-DD
            purchase_time   TEXT DEFAULT '',   -- 구매시각 HH:MM (있으면)
            store_type      TEXT DEFAULT '',   -- 코스트코 | 트레이더스
            store_name      TEXT DEFAULT '',   -- 지점명 포함 원문 (예: 코스트코 상봉점)
            total_qty       INTEGER DEFAULT 0, -- 구매수량(총 수량)
            item_kinds      INTEGER DEFAULT 0, -- 품목 종수
            total_amount    INTEGER DEFAULT 0, -- 구매금액(결제금액)
            discount_amount INTEGER DEFAULT 0, -- 할인금액
            card_last4      TEXT DEFAULT '',   -- 사용카드 끝 4자리
            cash_receipt_no TEXT DEFAULT '',   -- 현금영수증 승인번호
            memo            TEXT DEFAULT '',
            source          TEXT DEFAULT 'photo',
            created_at      TEXT,
            updated_at      TEXT,
            UNIQUE(purchase_date, store_name, total_amount, card_last4, cash_receipt_no)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipt_purchase_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id  INTEGER,
            product_no   TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            qty          INTEGER DEFAULT 0,
            unit_price   INTEGER DEFAULT 0,
            amount       INTEGER DEFAULT 0,
            discount     INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_date ON receipt_purchases(purchase_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rpi_pid ON receipt_purchase_items(purchase_id)")
    conn.commit()


def _i(v) -> int:
    """'12,345원' / None / 실수 → int (실패 시 0)."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(',', '').replace('원', '').strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def save_purchase(username: str, header: dict, items: list = None) -> tuple:
    """영수증 1장 저장(같은 영수증 재업로드면 갱신). 반환: (purchase_id, is_new).

    중복 키: (구매일자, 매장명, 구매금액, 카드끝4자리, 현금영수증승인번호)
    """
    _date = (header.get('purchase_date') or '').strip()
    if not _date:
        return 0, False
    conn = get_user_db(username)
    _ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _store_name = (header.get('store_name') or '').strip()
    _amount = _i(header.get('total_amount'))
    _card = (header.get('card_last4') or '').strip()
    _cash = (header.get('cash_receipt_no') or '').strip()
    _vals = (
        (header.get('purchase_time') or '').strip(),
        (header.get('store_type') or '').strip(),
        _i(header.get('total_qty')),
        _i(header.get('item_kinds')) or len(items or []),
        _i(header.get('discount_amount')),
        (header.get('memo') or '').strip(),
        (header.get('source') or 'photo').strip(),
        now,
    )
    row = conn.execute(
        "SELECT id FROM receipt_purchases WHERE purchase_date=? AND store_name=? "
        "AND total_amount=? AND card_last4=? AND cash_receipt_no=?",
        (_date, _store_name, _amount, _card, _cash)
    ).fetchone()
    if row:
        pid, is_new = row['id'], False
        conn.execute(
            "UPDATE receipt_purchases SET purchase_time=?, store_type=?, total_qty=?, "
            "item_kinds=?, discount_amount=?, memo=?, source=?, updated_at=? WHERE id=?",
            _vals + (pid,)
        )
        conn.execute("DELETE FROM receipt_purchase_items WHERE purchase_id=?", (pid,))
    else:
        cur = conn.execute(
            "INSERT INTO receipt_purchases (purchase_date, store_name, total_amount, "
            "card_last4, cash_receipt_no, purchase_time, store_type, total_qty, item_kinds, "
            "discount_amount, memo, source, updated_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_date, _store_name, _amount, _card, _cash) + _vals + (now,)
        )
        pid, is_new = cur.lastrowid, True
    for it in (items or []):
        _name = (it.get('상품명') or it.get('product_name') or '').strip()
        if not _name:
            continue
        _qty = _i(it.get('수량') or it.get('qty')) or 1
        _price = _i(it.get('단가') or it.get('unit_price'))
        conn.execute(
            "INSERT INTO receipt_purchase_items (purchase_id, product_no, product_name, "
            "qty, unit_price, amount, discount) VALUES (?,?,?,?,?,?,?)",
            (pid, str(it.get('상품번호') or it.get('product_no') or '').strip(),
             _name, _qty, _price,
             _i(it.get('금액') or it.get('amount')) or _qty * _price,
             _i(it.get('할인') or it.get('discount')))
        )
    conn.commit()
    conn.close()
    return pid, is_new


def list_purchases(username: str, date_from: str = '', date_to: str = '',
                   limit: int = 500) -> list:
    """기간 내 매입 영수증 목록 (최신순). 반환: [dict]"""
    conn = get_user_db(username)
    _ensure(conn)
    sql = "SELECT * FROM receipt_purchases"
    where, params = [], []
    if date_from:
        where.append("purchase_date >= ?"); params.append(date_from)
    if date_to:
        where.append("purchase_date <= ?"); params.append(date_to)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY purchase_date DESC, id DESC LIMIT ?"
    params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_purchase_items(username: str, purchase_id: int) -> list:
    """영수증 1장의 품목 내역."""
    conn = get_user_db(username)
    _ensure(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM receipt_purchase_items WHERE purchase_id=? ORDER BY id",
        (int(purchase_id),)).fetchall()]
    conn.close()
    return rows


def get_purchase_items_range(username: str, date_from: str = '', date_to: str = '') -> list:
    """기간 내 전 영수증의 품목 내역 (재고 입고 집계용). 영수증 헤더 정보 조인."""
    conn = get_user_db(username)
    _ensure(conn)
    sql = ("SELECT p.purchase_date, p.store_type, p.store_name, i.* "
           "FROM receipt_purchase_items i JOIN receipt_purchases p ON p.id = i.purchase_id")
    where, params = [], []
    if date_from:
        where.append("p.purchase_date >= ?"); params.append(date_from)
    if date_to:
        where.append("p.purchase_date <= ?"); params.append(date_to)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY p.purchase_date DESC, i.id"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def delete_purchase(username: str, purchase_id: int) -> bool:
    """영수증 1장 삭제 (품목 내역 포함)."""
    conn = get_user_db(username)
    _ensure(conn)
    conn.execute("DELETE FROM receipt_purchase_items WHERE purchase_id=?", (int(purchase_id),))
    cur = conn.execute("DELETE FROM receipt_purchases WHERE id=?", (int(purchase_id),))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n > 0
