"""재고 관리 — 대량구매 추천건(공지) → 요청/승인 → 재고 원장 → 판매 차감.

모든 테이블이 auth.db(공유)에 있다. A의 재고가 B의 판매로 차감되는 구조라
사용자별 DB(data/{username}.db)로는 교차 조회가 불가능하기 때문.

수량 단위 = 소분 단위(판매 1개 기준). 코스트코 팩 단위로 저장하면
소분 상품에서 차감이 분수가 된다(소분÷4 상품 1개 판매 = 0.25팩).
  · lot 입고량  qty_in = 팩수 × split_qty
  · 판매 차감량       = 수량 × sell_factor
  · lot 단가  unit_cost = 팩단가 ÷ split_qty
둘 다 정수로 떨어진다.

차감 순서: 판매자 본인 재고 우선 → 없으면 보유자 중 오래된 입고분(FIFO).
타인 재고에서 빠지면 보유자에게 구입가+500원(소분 1개당)을 정산한다.
"""
import sqlite3
from datetime import datetime

from db_core import AUTH_DB

# 타인 재고로 판매됐을 때 보유자가 받는 웃돈 (소분 1개당)
DEFAULT_CROSS_SURCHARGE = 500


def _conn():
    conn = sqlite3.connect(AUTH_DB, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _ensure_tables(conn):
    # ① 대량구매 추천건 — 관리자가 올리면 그대로 사용자 공지가 된다
    conn.execute("""CREATE TABLE IF NOT EXISTS bulk_deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_no TEXT DEFAULT '',
        product_name TEXT NOT NULL,
        sale_price INTEGER NOT NULL DEFAULT 0,
        normal_price INTEGER DEFAULT 0,
        split_qty INTEGER DEFAULT 1,
        total_limit INTEGER DEFAULT 0,
        deadline TEXT DEFAULT '',
        memo TEXT DEFAULT '',
        image_url TEXT DEFAULT '',
        status TEXT DEFAULT 'OPEN',
        created_by TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bdeal_status ON bulk_deals(status)")

    # ② 사용자 요청 + 관리자 승인
    conn.execute("""CREATE TABLE IF NOT EXISTS bulk_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        req_qty INTEGER NOT NULL DEFAULT 0,
        approved_qty INTEGER DEFAULT 0,
        status TEXT DEFAULT 'PENDING',
        memo TEXT DEFAULT '',
        requested_at TEXT NOT NULL,
        decided_at TEXT DEFAULT '',
        decided_by TEXT DEFAULT '',
        UNIQUE(deal_id, username)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_breq_status ON bulk_requests(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_breq_user ON bulk_requests(username)")

    # ③ 재고 원장 — lot 단위. received_at 하나로 FIFO와 30일 반품이 모두 풀린다
    conn.execute("""CREATE TABLE IF NOT EXISTS inventory_lots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_id INTEGER DEFAULT 0,
        product_no TEXT NOT NULL,
        product_name TEXT DEFAULT '',
        owner TEXT NOT NULL,
        unit_cost INTEGER NOT NULL DEFAULT 0,
        split_qty INTEGER DEFAULT 1,
        qty_in INTEGER NOT NULL DEFAULT 0,
        qty_left INTEGER NOT NULL DEFAULT 0,
        received_at TEXT NOT NULL,
        status TEXT DEFAULT 'ACTIVE',
        memo TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lot_pno ON inventory_lots(product_no, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lot_owner ON inventory_lots(owner)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lot_recv ON inventory_lots(received_at)")

    # ④ 차감 이력 + 500원 정산 장부. order_no로 멱등성 보장
    conn.execute("""CREATE TABLE IF NOT EXISTS inventory_moves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lot_id INTEGER NOT NULL,
        product_no TEXT DEFAULT '',
        owner TEXT NOT NULL,
        seller TEXT NOT NULL,
        qty INTEGER NOT NULL DEFAULT 0,
        unit_cost INTEGER DEFAULT 0,
        is_cross INTEGER DEFAULT 0,
        surcharge INTEGER DEFAULT 0,
        order_no TEXT DEFAULT '',
        dispatched_at TEXT DEFAULT '',
        platform TEXT DEFAULT '',
        settle_status TEXT DEFAULT 'PENDING',
        settled_at TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(order_no, lot_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mv_order ON inventory_moves(order_no)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mv_seller ON inventory_moves(seller)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mv_owner ON inventory_moves(owner, settle_status)")


def get_cross_surcharge() -> int:
    """타인 재고 판매 시 웃돈 — 전역 설정으로 조정 가능."""
    try:
        from db_auth import get_global_setting
        v = int(get_global_setting('inventory_cross_surcharge', '') or DEFAULT_CROSS_SURCHARGE)
        return max(0, v)
    except Exception:
        return DEFAULT_CROSS_SURCHARGE


# ── ① 추천건(공지) ────────────────────────────────────────
def create_bulk_deal(product_name: str, sale_price: int, product_no: str = '',
                     normal_price: int = 0, split_qty: int = 1, total_limit: int = 0,
                     deadline: str = '', memo: str = '', image_url: str = '',
                     created_by: str = '') -> int:
    if not (product_name or '').strip():
        return 0
    conn = _conn()
    _ensure_tables(conn)
    cur = conn.execute("""INSERT INTO bulk_deals
        (product_no, product_name, sale_price, normal_price, split_qty, total_limit,
         deadline, memo, image_url, status, created_by, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,'OPEN',?,?)""",
        (str(product_no or '').strip(), product_name.strip(), int(sale_price or 0),
         int(normal_price or 0), max(1, int(split_qty or 1)), max(0, int(total_limit or 0)),
         str(deadline or ''), str(memo or ''), str(image_url or ''),
         str(created_by or ''), _now()))
    deal_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(deal_id or 0)


def get_bulk_deals(status: str = None, limit: int = 100) -> list:
    conn = _conn()
    _ensure_tables(conn)
    if status:
        rows = conn.execute(
            "SELECT * FROM bulk_deals WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, int(limit))).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bulk_deals ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bulk_deal(deal_id: int) -> dict:
    conn = _conn()
    _ensure_tables(conn)
    r = conn.execute("SELECT * FROM bulk_deals WHERE id=?", (int(deal_id),)).fetchone()
    conn.close()
    return dict(r) if r else {}


def set_deal_status(deal_id: int, status: str) -> bool:
    conn = _conn()
    _ensure_tables(conn)
    conn.execute("UPDATE bulk_deals SET status=? WHERE id=?", (str(status), int(deal_id)))
    conn.commit()
    conn.close()
    return True


def delete_bulk_deal(deal_id: int) -> bool:
    """추천건 삭제. 이미 입고된 재고가 있으면 거부한다."""
    conn = _conn()
    _ensure_tables(conn)
    n = conn.execute("SELECT COUNT(*) c FROM inventory_lots WHERE deal_id=?",
                     (int(deal_id),)).fetchone()['c']
    if int(n) > 0:
        conn.close()
        return False
    conn.execute("DELETE FROM bulk_requests WHERE deal_id=?", (int(deal_id),))
    conn.execute("DELETE FROM bulk_deals WHERE id=?", (int(deal_id),))
    conn.commit()
    conn.close()
    return True


# ── ② 요청 / 승인 ─────────────────────────────────────────
def request_bulk_purchase(deal_id: int, username: str, req_qty: int, memo: str = '') -> bool:
    """사용자 대량구매 요청. 같은 추천건에 다시 요청하면 수량을 덮어쓴다(재요청)."""
    if int(req_qty or 0) <= 0:
        return False
    conn = _conn()
    _ensure_tables(conn)
    conn.execute("""INSERT INTO bulk_requests
        (deal_id, username, req_qty, approved_qty, status, memo, requested_at)
        VALUES (?,?,?,0,'PENDING',?,?)
        ON CONFLICT(deal_id, username) DO UPDATE SET
            req_qty=excluded.req_qty, memo=excluded.memo,
            requested_at=excluded.requested_at,
            status=CASE WHEN bulk_requests.status='APPROVED'
                        THEN bulk_requests.status ELSE 'PENDING' END""",
        (int(deal_id), str(username), int(req_qty), str(memo or ''), _now()))
    conn.commit()
    conn.close()
    return True


def get_bulk_requests(deal_id: int = None, username: str = None,
                      status: str = None) -> list:
    conn = _conn()
    _ensure_tables(conn)
    sql = """SELECT r.*, d.product_name, d.product_no, d.sale_price, d.split_qty,
                    d.deadline, d.status AS deal_status
             FROM bulk_requests r
             LEFT JOIN bulk_deals d ON r.deal_id = d.id WHERE 1=1"""
    params = []
    if deal_id is not None:
        sql += " AND r.deal_id=?"; params.append(int(deal_id))
    if username:
        sql += " AND r.username=?"; params.append(str(username))
    if status:
        sql += " AND r.status=?"; params.append(str(status))
    sql += " ORDER BY r.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def decide_bulk_request(req_id: int, approve: bool, approved_qty: int = 0,
                        decided_by: str = '') -> bool:
    """관리자 승인/거절. 승인 수량은 조정 가능(요청 20 → 승인 15)."""
    conn = _conn()
    _ensure_tables(conn)
    if approve:
        conn.execute("""UPDATE bulk_requests
            SET status='APPROVED', approved_qty=?, decided_at=?, decided_by=?
            WHERE id=?""", (max(0, int(approved_qty or 0)), _now(),
                            str(decided_by or ''), int(req_id)))
    else:
        conn.execute("""UPDATE bulk_requests
            SET status='REJECTED', approved_qty=0, decided_at=?, decided_by=?
            WHERE id=?""", (_now(), str(decided_by or ''), int(req_id)))
    conn.commit()
    conn.close()
    return True


def get_deal_request_summary(deal_id: int) -> dict:
    """추천건별 요청/승인 합계 — 한도 대비 잔여 계산용."""
    conn = _conn()
    _ensure_tables(conn)
    r = conn.execute("""SELECT
            COALESCE(SUM(req_qty),0) AS req_total,
            COALESCE(SUM(CASE WHEN status='APPROVED' THEN approved_qty ELSE 0 END),0) AS approved_total,
            COUNT(*) AS n
        FROM bulk_requests WHERE deal_id=?""", (int(deal_id),)).fetchone()
    conn.close()
    return dict(r) if r else {'req_total': 0, 'approved_total': 0, 'n': 0}


# ── ③ 재고 입고 ───────────────────────────────────────────
def receive_deal_lots(deal_id: int, received_at: str = '', memo: str = '') -> int:
    """승인된 요청들을 재고 lot으로 입고 처리. 이미 입고된 추천건이면 건너뛴다.

    수량 단위 변환: 승인 팩수 × split_qty = 소분 단위 재고
    """
    conn = _conn()
    _ensure_tables(conn)
    exists = conn.execute("SELECT COUNT(*) c FROM inventory_lots WHERE deal_id=?",
                          (int(deal_id),)).fetchone()['c']
    if int(exists) > 0:
        conn.close()
        return 0

    deal = conn.execute("SELECT * FROM bulk_deals WHERE id=?", (int(deal_id),)).fetchone()
    if not deal:
        conn.close()
        return 0
    deal = dict(deal)
    sq = max(1, int(deal.get('split_qty', 1) or 1))
    unit_cost = int(deal.get('sale_price', 0) or 0) // sq   # 소분 1개당 구입가
    recv = received_at or _today()

    reqs = conn.execute(
        "SELECT * FROM bulk_requests WHERE deal_id=? AND status='APPROVED' AND approved_qty>0",
        (int(deal_id),)).fetchall()
    made = 0
    for r in reqs:
        packs = int(r['approved_qty'] or 0)
        units = packs * sq
        if units <= 0:
            continue
        conn.execute("""INSERT INTO inventory_lots
            (deal_id, product_no, product_name, owner, unit_cost, split_qty,
             qty_in, qty_left, received_at, status, memo, created_at)
            VALUES (?,?,?,?,?,?,?,?,?, 'ACTIVE', ?, ?)""",
            (int(deal_id), str(deal.get('product_no') or ''),
             str(deal.get('product_name') or ''), str(r['username']),
             unit_cost, sq, units, units, recv, str(memo or ''), _now()))
        made += 1
    if made:
        conn.execute("UPDATE bulk_deals SET status='PURCHASED' WHERE id=?", (int(deal_id),))
    conn.commit()
    conn.close()
    return made


def add_lot(product_no: str, product_name: str, owner: str, unit_cost: int,
            qty_packs: int, split_qty: int = 1, received_at: str = '',
            deal_id: int = 0, memo: str = '') -> int:
    """추천건 없이 재고를 직접 넣는 경로(관리자 수동 입고)."""
    sq = max(1, int(split_qty or 1))
    units = int(qty_packs or 0) * sq
    if units <= 0:
        return 0
    conn = _conn()
    _ensure_tables(conn)
    cur = conn.execute("""INSERT INTO inventory_lots
        (deal_id, product_no, product_name, owner, unit_cost, split_qty,
         qty_in, qty_left, received_at, status, memo, created_at)
        VALUES (?,?,?,?,?,?,?,?,?, 'ACTIVE', ?, ?)""",
        (int(deal_id or 0), str(product_no or ''), str(product_name or ''), str(owner),
         int(unit_cost or 0) // sq, sq, units, units,
         received_at or _today(), str(memo or ''), _now()))
    lot_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(lot_id or 0)


def get_inventory_lots(owner: str = None, product_no: str = None,
                       only_active: bool = True) -> list:
    conn = _conn()
    _ensure_tables(conn)
    sql = "SELECT * FROM inventory_lots WHERE 1=1"
    params = []
    if owner:
        sql += " AND owner=?"; params.append(str(owner))
    if product_no:
        sql += " AND product_no=?"; params.append(str(product_no))
    if only_active:
        sql += " AND status='ACTIVE' AND qty_left>0"
    sql += " ORDER BY received_at, id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stock_summary(owner: str = None) -> list:
    """상품별 재고 요약 — 보유자별 잔여/입고/경과일."""
    conn = _conn()
    _ensure_tables(conn)
    sql = """SELECT product_no, product_name, owner,
                    SUM(qty_left) AS qty_left, SUM(qty_in) AS qty_in,
                    MIN(received_at) AS oldest_at,
                    CAST(julianday('now') - julianday(MIN(received_at)) AS INTEGER) AS age_days
             FROM inventory_lots
             WHERE status='ACTIVE' AND qty_left>0"""
    params = []
    if owner:
        sql += " AND owner=?"; params.append(str(owner))
    sql += " GROUP BY product_no, product_name, owner ORDER BY age_days DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── ④ 판매 차감 (핵심) ────────────────────────────────────
def consume_for_sale(seller: str, product_no: str, units: int, order_no: str,
                     dispatched_at: str = '', platform: str = '') -> dict:
    """발송된 주문 1건만큼 재고를 차감한다.

    차감 순서: 판매자 본인 재고 우선 → 없으면 보유자 중 오래된 입고분(FIFO).
    타인 재고에서 빠지면 보유자에게 구입가+500원(소분 1개당)이 붙는다.

    멱등성: 같은 order_no로 이미 차감했으면 아무것도 하지 않는다.
    log_dispatch_success가 INSERT OR REPLACE라 재실행이 가능하기 때문.

    재고가 없는 상품(대량구매 대상이 아닌 일반 상품)이면 조용히 0을 반환한다.

    반환: {'consumed': 차감된 소분 수, 'shortage': 재고 부족분,
           'surcharge': 웃돈 합계, 'moves': [...], 'skipped': bool}
    """
    out = {'consumed': 0, 'shortage': 0, 'surcharge': 0, 'moves': [], 'skipped': False}
    units = int(units or 0)
    product_no = str(product_no or '').strip()
    order_no = str(order_no or '').strip()
    if units <= 0 or not product_no or not order_no:
        return out

    conn = _conn()
    _ensure_tables(conn)

    # 멱등성 — 이미 이 주문으로 차감된 적이 있으면 재차감 금지
    dup = conn.execute("SELECT COUNT(*) c FROM inventory_moves WHERE order_no=?",
                       (order_no,)).fetchone()['c']
    if int(dup) > 0:
        conn.close()
        out['skipped'] = True
        return out

    lots = conn.execute(
        """SELECT * FROM inventory_lots
           WHERE product_no=? AND status='ACTIVE' AND qty_left>0
           ORDER BY (owner<>?) ASC, received_at ASC, id ASC""",
        (product_no, str(seller))).fetchall()
    if not lots:
        conn.close()
        return out   # 재고 관리 대상 아님 — 정상

    surcharge_unit = get_cross_surcharge()
    remain = units
    now = _now()
    for lot in lots:
        if remain <= 0:
            break
        take = min(remain, int(lot['qty_left'] or 0))
        if take <= 0:
            continue
        is_cross = 1 if str(lot['owner']) != str(seller) else 0
        sur = surcharge_unit * take if is_cross else 0
        conn.execute("""INSERT OR IGNORE INTO inventory_moves
            (lot_id, product_no, owner, seller, qty, unit_cost, is_cross, surcharge,
             order_no, dispatched_at, platform, settle_status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?, ?, ?)""",
            (int(lot['id']), product_no, str(lot['owner']), str(seller), take,
             int(lot['unit_cost'] or 0), is_cross, sur, order_no,
             str(dispatched_at or ''), str(platform or ''),
             'PENDING' if is_cross else 'NA', now))
        conn.execute("UPDATE inventory_lots SET qty_left=qty_left-? WHERE id=?",
                     (take, int(lot['id'])))
        out['moves'].append({'lot_id': int(lot['id']), 'owner': str(lot['owner']),
                             'qty': take, 'is_cross': is_cross, 'surcharge': sur})
        out['consumed'] += take
        out['surcharge'] += sur
        remain -= take

    out['shortage'] = max(0, remain)
    conn.commit()
    conn.close()
    return out


def revert_sale(order_no: str) -> int:
    """차감 취소 — 주문 취소/반품 시 재고를 되돌린다."""
    order_no = str(order_no or '').strip()
    if not order_no:
        return 0
    conn = _conn()
    _ensure_tables(conn)
    moves = conn.execute("SELECT * FROM inventory_moves WHERE order_no=?",
                         (order_no,)).fetchall()
    n = 0
    for m in moves:
        conn.execute("UPDATE inventory_lots SET qty_left=qty_left+? WHERE id=?",
                     (int(m['qty'] or 0), int(m['lot_id'])))
        n += 1
    conn.execute("DELETE FROM inventory_moves WHERE order_no=?", (order_no,))
    conn.commit()
    conn.close()
    return n


def get_surcharge_map(seller: str, order_nos: list) -> dict:
    """주문번호 → 이 판매자가 타인 재고를 써서 추가로 부담하는 웃돈 합계.

    수익계산의 구입가격에 더해진다. 자기 재고로 나간 건은 0.
    """
    order_nos = [str(o).strip() for o in (order_nos or []) if str(o).strip()]
    if not order_nos or not seller:
        return {}
    conn = _conn()
    _ensure_tables(conn)
    out = {}
    CHUNK = 900   # SQLite 변수 한도 회피 (db_dispatch_log와 동일 패턴)
    for i in range(0, len(order_nos), CHUNK):
        chunk = order_nos[i:i + CHUNK]
        ph = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""SELECT order_no, COALESCE(SUM(surcharge),0) AS sur
                FROM inventory_moves
                WHERE seller=? AND is_cross=1 AND order_no IN ({ph})
                GROUP BY order_no""",
            [str(seller)] + chunk).fetchall()
        for r in rows:
            out[str(r['order_no'])] = int(r['sur'] or 0)
    conn.close()
    return out


def get_moves(seller: str = None, owner: str = None, only_cross: bool = False,
              settle_status: str = None, limit: int = 500) -> list:
    conn = _conn()
    _ensure_tables(conn)
    sql = "SELECT * FROM inventory_moves WHERE 1=1"
    params = []
    if seller:
        sql += " AND seller=?"; params.append(str(seller))
    if owner:
        sql += " AND owner=?"; params.append(str(owner))
    if only_cross:
        sql += " AND is_cross=1"
    if settle_status:
        sql += " AND settle_status=?"; params.append(str(settle_status))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── ⑤ 500원 정산 장부 (관리자 중개) ───────────────────────
def get_cross_settlement_summary(settle_status: str = 'PENDING') -> list:
    """관리자 정산 장부 — 보유자별/판매자별 주고받을 금액.

    관리자가 B에게 받아 A에게 준다: 금액 = (구입가 + 500) × 수량
    """
    conn = _conn()
    _ensure_tables(conn)
    rows = conn.execute("""SELECT owner, seller, product_no,
                MAX(product_no) AS pno,
                SUM(qty) AS qty,
                SUM(surcharge) AS surcharge,
                SUM((unit_cost * qty) + surcharge) AS payable
            FROM inventory_moves
            WHERE is_cross=1 AND settle_status=?
            GROUP BY owner, seller, product_no
            ORDER BY owner, seller""", (str(settle_status),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_cross_settled(owner: str, seller: str, product_no: str = None) -> int:
    conn = _conn()
    _ensure_tables(conn)
    sql = ("UPDATE inventory_moves SET settle_status='SETTLED', settled_at=? "
           "WHERE is_cross=1 AND settle_status='PENDING' AND owner=? AND seller=?")
    params = [_now(), str(owner), str(seller)]
    if product_no:
        sql += " AND product_no=?"; params.append(str(product_no))
    cur = conn.execute(sql, params)
    n = cur.rowcount
    conn.commit()
    conn.close()
    return int(n or 0)


# ── ⑥ 30일 반품 대상 ──────────────────────────────────────
def get_return_due_lots(days: int = 30, owner: str = None) -> list:
    """입고 후 N일이 지나도 남아있는 재고 — 반품 권장 대상.

    상태를 자동으로 바꾸지는 않는다. 목록만 뽑아 알림으로 보낸다.
    """
    conn = _conn()
    _ensure_tables(conn)
    sql = """SELECT *, CAST(julianday('now') - julianday(received_at) AS INTEGER) AS age_days
             FROM inventory_lots
             WHERE status='ACTIVE' AND qty_left>0
               AND julianday('now') - julianday(received_at) >= ?"""
    params = [int(days)]
    if owner:
        sql += " AND owner=?"; params.append(str(owner))
    sql += " ORDER BY received_at ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_lot_status(lot_id: int, status: str) -> bool:
    conn = _conn()
    _ensure_tables(conn)
    conn.execute("UPDATE inventory_lots SET status=? WHERE id=?",
                 (str(status), int(lot_id)))
    conn.commit()
    conn.close()
    return True
