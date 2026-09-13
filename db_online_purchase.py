"""코스트코 온라인몰 직배송 원장 — '이 주문은 매장이 아니라 온라인몰에서 샀다'는 표시.

왜 따로 두는가:
  청구의 두 기둥이 모두 매장 영수증을 전제한다.
    ① 물건값 — 그날 영수증 단가로 붙인다 (receipt_settle)
    ② 택배비 — 그날 dispatch_log 건수로 매긴다 (settle_core.daily_fees)
  코스트코 온라인몰에서 주문해 **코스트코가 고객에게 직접 보낸 건**은 둘 다 어긋난다.
    ① 매장 영수증에 없으니 단가를 못 찾고, 0원이라 to_ledger_rows에서 버려진다
       → 물건값이 통째로 청구되지 않는다
    ② 사용자가 코스트코 송장을 자기 스토어에 등록하면 dispatch_log가 생긴다
       → 관리자가 포장도 발송도 하지 않았는데 택배비가 붙는다
  즉 **물건값은 못 받고 택배비는 잘못 받는다.** 어느 쪽도 매칭 엔진을 고쳐서는
  해결되지 않는다 — 애초에 매입 경로가 다르기 때문이다. 그래서 '이 주문은
  온라인몰 건'이라는 사실 자체를 한 곳에 적어 두고, 두 기둥이 그걸 참조한다.

단가를 왜 매장가로 대신 쓰면 안 되는가:
  온라인몰가는 예외 없이 매장가보다 비싸다(실측: 7~17%, 건당 1,000~4,500원).
    KS 종이타월   매장 26,990 / 온라인 31,490  (+4,500)
    SVINTO WIPES 매장 14,990 / 온라인 17,790  (+2,800)
  매장가로 청구하면 그 차액이 그대로 손실로 남는다. 그래서 단가는 공유DB의
  online_price를 기본값으로 제시하되, 실제 결제가(할인·쿠폰)는 관리자가 확인해
  고칠 수 있게 한다 — 목록가와 결제가가 다른 날이 있기 때문이다.

UNIQUE(username, order_no):
  한 주문은 매장 건이거나 온라인몰 건이지 둘 다일 수 없다. 날짜를 키에 넣지 않는
  이유는 택배비 제외가 **월 단위로 아무 날짜나** 조회하기 때문이다(billing_page).
  주문번호만으로 답할 수 있어야 한 달치를 훑을 때 날짜를 몰라도 걸러진다.
"""
import sqlite3
from datetime import datetime

from db_core import get_auth_db


def _conn():
    return get_auth_db(row=True)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _i(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _s(v):
    return str(v if v is not None else '').strip()


def ensure(conn=None):
    """테이블 보장. 외부 연결을 주면 그걸 쓰고 닫지 않는다."""
    _own = conn is None
    conn = conn or _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS online_purchase (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            settle_date  TEXT NOT NULL,
            username     TEXT NOT NULL,
            order_no     TEXT NOT NULL,
            costco_no    TEXT DEFAULT '',
            naver_no     TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            recipient    TEXT DEFAULT '',
            qty          INTEGER DEFAULT 1,
            unit_price   INTEGER DEFAULT 0,
            amount       INTEGER DEFAULT 0,
            prev_cost    INTEGER DEFAULT 0,   -- 정산 전 구입가 (되돌리기용)
            memo         TEXT DEFAULT '',
            created_by   TEXT DEFAULT '',
            created_at   TEXT DEFAULT '',
            UNIQUE(username, order_no)
        )
    """)
    # 이미 만들어진 표에는 CREATE TABLE IF NOT EXISTS가 아무 일도 하지 않는다.
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(online_purchase)")}
        if 'prev_cost' not in _cols:
            conn.execute("ALTER TABLE online_purchase ADD COLUMN prev_cost INTEGER DEFAULT 0")
        if 'naver_no' not in _cols:
            conn.execute("ALTER TABLE online_purchase ADD COLUMN naver_no TEXT DEFAULT ''")
    except sqlite3.Error:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_op_date ON online_purchase(settle_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_op_user ON online_purchase(username, settle_date)")
    conn.commit()
    if _own:
        conn.close()


# ── 쓰기 ──────────────────────────────────────────────────────
def mark(rows, created_by=''):
    """온라인몰 직배송 건으로 표시한다. 같은 주문을 다시 표시하면 덮어쓴다.

    rows: [{username, order_no, settle_date, costco_no, product_name,
            recipient, qty, unit_price, memo}]

    덮어쓰기를 허용하는 이유: 단가를 잘못 넣었을 때 표시를 지웠다 다시 넣게 하면
    그 사이에 정산을 돌린 경우 매장 영수증 쪽으로 붙어 버린다. 고쳐 쓰는 편이 안전하다.
    단 prev_cost만은 덮어쓰지 않는다 — 처음 표시할 때의 값이 '정산 전 구입가'이고,
    정산을 한 번 돌린 뒤 다시 표시하면 이미 덮어써진 값을 원본으로 잡게 된다.

    반환: 저장된 건수
    """
    rows = [r for r in (rows or []) if _s(r.get('username')) and _s(r.get('order_no'))]
    if not rows:
        return 0
    conn = _conn()
    ensure(conn)
    now = _now()
    n = 0
    try:
        for r in rows:
            qty = max(1, _i(r.get('qty')) or 1)
            up = _i(r.get('unit_price'))
            conn.execute("""
                INSERT INTO online_purchase
                    (settle_date, username, order_no, costco_no, naver_no,
                     product_name, recipient, qty, unit_price, amount, prev_cost,
                     memo, created_by, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(username, order_no) DO UPDATE SET
                    settle_date=excluded.settle_date,
                    costco_no=excluded.costco_no,
                    naver_no=excluded.naver_no,
                    product_name=excluded.product_name,
                    recipient=excluded.recipient,
                    qty=excluded.qty,
                    unit_price=excluded.unit_price,
                    amount=excluded.amount,
                    memo=excluded.memo,
                    created_by=excluded.created_by,
                    created_at=excluded.created_at
            """, (_s(r.get('settle_date')), _s(r.get('username')), _s(r.get('order_no')),
                  _s(r.get('costco_no')), _s(r.get('naver_no')),
                  _s(r.get('product_name')), _s(r.get('recipient')),
                  qty, up, up * qty, _i(r.get('prev_cost')), _s(r.get('memo')),
                  _s(created_by), now))
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def unmark(username, order_nos):
    """표시를 지운다 — 알고 보니 매장에서 산 건이었을 때.

    지우면 그 주문은 다음 정산부터 다시 매장 영수증 매칭 대상이 되고,
    택배비도 다시 붙는다.
    """
    onos = [_s(o) for o in (order_nos or []) if _s(o)]
    if not (_s(username) and onos):
        return 0
    conn = _conn()
    ensure(conn)
    try:
        cur = conn.execute(
            "DELETE FROM online_purchase WHERE username=? AND order_no IN (%s)"
            % ",".join("?" * len(onos)), [_s(username)] + onos)
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


# ── 읽기 ──────────────────────────────────────────────────────
def order_nos(username):
    """{order_no} — 그 사용자의 온라인몰 건 주문번호 전부.

    택배비 제외가 이걸 쓴다. 날짜를 받지 않는 이유는 월별 청구가 하루씩 60번
    daily_fees를 부르기 때문이다 — 매번 날짜로 조회하면 같은 질문을 60번 한다.
    """
    if not _s(username):
        return set()
    conn = _conn()
    ensure(conn)
    try:
        return {str(r['order_no']) for r in conn.execute(
            "SELECT order_no FROM online_purchase WHERE username=?", (_s(username),))}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def keys_all():
    """{(username, order_no)} — 전 사용자. 영수증 매칭에서 제외할 때 쓴다."""
    conn = _conn()
    ensure(conn)
    try:
        return {(str(r['username']), str(r['order_no'])) for r in conn.execute(
            "SELECT username, order_no FROM online_purchase")}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def get_by_date(settle_date, username=None):
    """그날 표시된 온라인몰 건. [{...}] — 정산 미리보기에서 되살릴 때 쓴다."""
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM online_purchase WHERE settle_date=?"
        args = [str(settle_date)]
        if username:
            sql += " AND username=?"
            args.append(_s(username))
        return [dict(r) for r in conn.execute(sql + " ORDER BY username, order_no", args)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def list_range(date_from, date_to=None, username=None):
    """기간 조회 — 관리자 화면에서 '언제 무엇을 온라인으로 샀나'를 본다."""
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM online_purchase WHERE settle_date>=? AND settle_date<=?"
        args = [str(date_from), str(date_to or date_from)]
        if username:
            sql += " AND username=?"
            args.append(_s(username))
        return [dict(r) for r in conn.execute(
            sql + " ORDER BY settle_date DESC, username, order_no", args)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def summary_by_date(date_from, date_to=None):
    """{settle_date: {count, amount}} — 얼마나 쓰이고 있는지 한눈에."""
    out = {}
    for r in list_range(date_from, date_to):
        d = str(r.get('settle_date') or '')
        e = out.setdefault(d, {'count': 0, 'amount': 0})
        e['count'] += 1
        e['amount'] += _i(r.get('amount'))
    return out
