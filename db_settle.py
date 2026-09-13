"""정산 원장 — 청구액을 답하는 유일한 곳.

왜 새로 쓰는가:
  같은 질문("A가 9월 8일에 얼마 내야 하나")에 답하는 저장소가 넷이었다.
    receipt_settle_items · purchase_settle_snapshot · billing_ledger · daily_billing
  넷이 서로 다른 시점에 서로 다른 방법으로 채워져 화면마다 금액이 달랐고
  (9/7 oxo: 원장 780,720 vs 스냅샷 184,560), 한쪽을 고치면 다른 쪽이 틀어졌다.
  '예상→확정' 이원화도 같은 값을 두 번 저장하게 만들어 어긋남을 키웠다.

이제 둘뿐이다:
  settle_item     정산 품목 한 줄 = 청구 근거 한 줄. 무엇을 얼마에 넘겼나.
  settle_invoice  날짜×사용자 한 줄 = 청구서. 합계와 청구·입금 상태.

  invoice 금액은 언제나 item 합계에서 나온다(recompute_invoice). 사람이 손으로
  고치는 금액 필드가 없어야 둘이 어긋날 수 없다.

상태(일별 청구 · 일별 입금):
  draft   정산은 됐지만 아직 청구 전
  billed  구매일 다음날 청구함
  paid    입금 확인 완료
"""
import sqlite3
from datetime import datetime

from db_core import get_auth_db

STATUS_LABEL = {'draft': '정산완료', 'billed': '청구됨', 'paid': '입금완료'}

#: 품목 금액의 출처 — 왜 이 금액인지 화면에서 바로 읽히게 한다
SOURCE_LABEL = {
    'receipt': '영수증',      # 그날 영수증 실단가로 배치
    'stock':   '재고',        # 이전 구입분(재고 lot) 단가
    'manual':  '수동',        # 관리자가 직접 붙임
    'direct':  '직접청구',    # 주문 없이 사용자에게 청구
    'online':  '온라인몰',    # 코스트코 온라인몰 구매 → 코스트코가 고객에게 직접 발송
    'fee':     '택배·포장',   # 물건이 아닌 비용 행
}


def _conn():
    return get_auth_db(row=True)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _i(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def is_billable(username):
    """청구 대상인가 — '직접구매' 계정은 청구서를 만들지 않는다.

    직접 사는 사람은 자기 돈으로 자기가 산 것이라 청구할 것이 없다. 그래도
    품목(settle_item)은 남긴다 — 주문 구입가(수익계산)와 재고 차감의 근거이고,
    영수증 잔량을 재고로 넘길 때도 '무엇이 나갔는지'를 여기서 읽는다.

    청구서를 만들지 않아야 정산리스트·미입금자에 0원짜리가 섞이지 않는다.
    """
    try:
        from db_products import get_setting
        return str(get_setting(username, 'self_purchase') or '').strip() != '1'
    except Exception:
        return True


def _drop_invoice(settle_date, username):
    """직접구매 계정으로 바뀐 뒤 남은 청구서를 치운다(입금완료분은 남긴다)."""
    conn = _conn()
    ensure(conn)
    try:
        conn.execute(
            "DELETE FROM settle_invoice WHERE settle_date=? AND username=? AND status<>'paid'",
            (str(settle_date), str(username)))
        conn.commit()
    finally:
        conn.close()


def ensure(conn=None):
    """테이블 보장. 외부 연결을 주면 그걸 쓰고 닫지 않는다."""
    _own = conn is None
    conn = conn or _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settle_item (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            settle_date  TEXT NOT NULL,
            username     TEXT NOT NULL,
            order_no     TEXT NOT NULL DEFAULT '',
            product_no   TEXT NOT NULL DEFAULT '',
            naver_no     TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            recipient    TEXT DEFAULT '',
            qty          INTEGER DEFAULT 1,
            split_qty    INTEGER DEFAULT 1,
            pack         INTEGER DEFAULT 1,
            unit_price   INTEGER DEFAULT 0,
            amount       INTEGER DEFAULT 0,
            prev_cost    INTEGER DEFAULT 0,   -- 정산 전 구입가 (되돌리기용)
            source       TEXT DEFAULT 'receipt',
            receipt_date TEXT DEFAULT '',
            memo         TEXT DEFAULT '',
            created_by   TEXT DEFAULT '',
            created_at   TEXT DEFAULT '',
            UNIQUE(settle_date, username, order_no, product_no)
        )
    """)
    # 이미 만들어진 표에는 CREATE TABLE IF NOT EXISTS가 아무 일도 하지 않는다.
    # 나중에 늘린 컴럼은 따로 붙여야 구버전 DB에서도 돌아간다.
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(settle_item)")}
        if 'prev_cost' not in _cols:
            conn.execute("ALTER TABLE settle_item ADD COLUMN prev_cost INTEGER DEFAULT 0")
    except sqlite3.Error:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_si_date ON settle_item(settle_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_si_user ON settle_item(username, settle_date)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settle_invoice (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            settle_date  TEXT NOT NULL,
            username     TEXT NOT NULL,
            goods_amount INTEGER DEFAULT 0,
            ship_fee     INTEGER DEFAULT 0,
            pack_fee     INTEGER DEFAULT 0,
            total_amount INTEGER DEFAULT 0,
            item_count   INTEGER DEFAULT 0,
            status       TEXT DEFAULT 'draft',
            billed_at    TEXT DEFAULT '',
            paid_at      TEXT DEFAULT '',
            paid_amount  INTEGER DEFAULT 0,
            memo         TEXT DEFAULT '',
            created_by   TEXT DEFAULT '',
            created_at   TEXT DEFAULT '',
            updated_at   TEXT DEFAULT '',
            UNIQUE(settle_date, username)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sv_date ON settle_invoice(settle_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sv_user ON settle_invoice(username, settle_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sv_status ON settle_invoice(status)")
    # 매칭 초안 — 배정·수동매칭은 사람이 한 건씩 판단해 넣는 값이라 다시 만들기 비싸다.
    # 저장(붙들어 두기)과 청구(사용자에게 보내기)는 다른 결정이라 테이블도 나눈다.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settle_draft (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            settle_date TEXT NOT NULL,
            username    TEXT NOT NULL,
            order_no    TEXT NOT NULL,
            row_json    TEXT NOT NULL,
            created_by  TEXT DEFAULT '',
            updated_at  TEXT DEFAULT '',
            UNIQUE(settle_date, username, order_no)
        )
    """)
    conn.commit()
    if _own:
        conn.close()


# ── 쓰기 ────────────────────────────────────────────────────
def _write_items(conn, settle_date, username, rows, created_by, now):
    """품목 INSERT만 — 무엇을 먼저 지울지는 부르는 쪽이 정한다."""
    n = 0
    for r in (rows or []):
        conn.execute(
            """INSERT OR REPLACE INTO settle_item
               (settle_date, username, order_no, product_no, naver_no, product_name,
                recipient, qty, split_qty, pack, unit_price, amount, prev_cost,
                source, receipt_date, memo, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(settle_date), str(username), str(r.get('order_no') or ''),
             str(r.get('product_no') or ''), str(r.get('naver_no') or ''),
             str(r.get('product_name') or ''), str(r.get('recipient') or ''),
             _i(r.get('qty')) or 1, _i(r.get('split_qty')) or 1, _i(r.get('pack')) or 1,
             _i(r.get('unit_price')), _i(r.get('amount')), _i(r.get('prev_cost')),
             str(r.get('source') or 'receipt'), str(r.get('receipt_date') or ''),
             str(r.get('memo') or ''), str(created_by or ''), now))
        n += 1
    return n


def merge_items(settle_date, username, rows, created_by=''):
    """이번 회차에 매칭된 주문만 갱신하고 나머지는 그대로 둔다 — 평상시 정산 경로.

    영수증을 나눠 올리는 날이 있다. 오전분을 먼저 정산하고 오후에 나머지를
    올리는데, 두 번째 회차는 첫 영수증의 상품을 몰라 그 주문들이 미매칭으로
    떨어진다. 통째로 교체하면 오전에 정산한 것이 통째로 사라진다.

    같은 주문이 다시 오면 새 값으로 덮어쓴다 — 잘못 붙은 매칭은 다시 돌려 고치면
    된다. 행을 없애려면 정산 취소(delete_settlement)를 쓴다.

    rows: [{order_no, product_no, product_name, qty, split_qty, pack,
            unit_price, amount, source, receipt_date, naver_no, recipient, memo}]
    반환: 쓴 행 수
    """
    onos = sorted({str(r.get('order_no') or '') for r in (rows or [])})
    conn = _conn()
    ensure(conn)
    now = _now()
    try:
        CHUNK = 900                       # SQLite 변수 한도
        for i in range(0, len(onos), CHUNK):
            part = onos[i:i + CHUNK]
            conn.execute(
                "DELETE FROM settle_item WHERE settle_date=? AND username=? "
                "AND order_no IN (%s)" % ",".join("?" * len(part)),
                [str(settle_date), str(username)] + part)
        n = _write_items(conn, settle_date, username, rows, created_by, now)
        conn.commit()
    finally:
        conn.close()
    recompute_invoice(settle_date, username, created_by=created_by)
    return n


def replace_items(settle_date, username, rows, created_by=''):
    """그 날짜·사용자의 품목을 통째로 교체한다 — '이 날짜는 이게 전부'라고 단언할 때만.

    평상시 정산은 merge_items를 쓴다. 여기 있는 것 말고 다 지우는 동작이라
    나눠 올린 영수증의 앞 회차를 날려 버릴 수 있다.
    """
    conn = _conn()
    ensure(conn)
    now = _now()
    try:
        conn.execute("DELETE FROM settle_item WHERE settle_date=? AND username=?",
                     (str(settle_date), str(username)))
        n = _write_items(conn, settle_date, username, rows, created_by, now)
        conn.commit()
    finally:
        conn.close()
    recompute_invoice(settle_date, username, created_by=created_by)
    return n


def save_settlement(settle_date, rows, fees_by_user=None, created_by=''):
    """정산 확정 — 사용자별로 품목을 교체하고 청구서를 만든다.

    rows에 등장하지 않는 사용자는 건드리지 않는다. 그날 정산 대상이 아닌
    사람의 기존 청구서를 0원으로 지워 버리면 안 되기 때문이다.
    같은 이유로 품목도 병합한다(merge_items) — 이번 회차에 안 나온 주문은
    앞서 정산한 값을 그대로 둔다.
    fees_by_user: {username: {'ship_fee': int, 'pack_fee': int}}
    반환: {username: 청구액}
    """
    by_user = {}
    for r in (rows or []):
        u = str(r.get('username') or '')
        if u:
            by_user.setdefault(u, []).append(r)
    out = {}
    for u, urows in by_user.items():
        merge_items(settle_date, u, urows, created_by=created_by)
        f = (fees_by_user or {}).get(u) or {}
        if f:
            set_fees(settle_date, u, _i(f.get('ship_fee')), _i(f.get('pack_fee')))
        inv = get_invoice(settle_date, u)
        out[u] = _i((inv or {}).get('total_amount'))
    return out


def set_fees(settle_date, username, ship_fee, pack_fee):
    """그날 택배비·포장비를 청구서에 싣고 합계를 다시 계산한다.

    월말에 한 달치를 몰아 붙이면 그달 마지막 날 청구서만 유독 커지고, 중간에
    그만둔 사용자에게는 영영 못 받는다. 발생한 날에 그날 것만 싣는다.

    '직접구매' 계정은 청구서를 만들지 않으므로 비용도 싣지 않는다 — 여기서
    막지 않으면 INSERT가 0원 청구서를 되살린다.
    """
    if not is_billable(username):
        _drop_invoice(settle_date, username)
        return 0
    conn = _conn()
    ensure(conn)
    now = _now()
    try:
        conn.execute(
            """INSERT INTO settle_invoice (settle_date, username, ship_fee, pack_fee,
                                           created_at, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(settle_date, username) DO UPDATE SET
                 ship_fee=excluded.ship_fee, pack_fee=excluded.pack_fee,
                 updated_at=excluded.updated_at""",
            (str(settle_date), str(username), _i(ship_fee), _i(pack_fee), now, now))
        conn.commit()
    finally:
        conn.close()
    return recompute_invoice(settle_date, username)


def recompute_invoice(settle_date, username, created_by=''):
    """청구서 금액을 품목 합계에서 다시 만든다 — 금액의 유일한 계산 지점.

    이미 입금 완료(paid)된 청구서는 금액을 건드리지 않는다. 받은 돈과 청구액이
    달라지면 무엇을 받은 것인지 설명할 수 없게 된다.

    '직접구매' 계정은 청구서를 만들지 않는다 — 품목만 남기고 0을 돌려준다.
    """
    if not is_billable(username):
        _drop_invoice(settle_date, username)
        return 0
    conn = _conn()
    ensure(conn)
    try:
        r = conn.execute(
            "SELECT COALESCE(SUM(amount),0) amt, COUNT(*) n FROM settle_item "
            "WHERE settle_date=? AND username=?",
            (str(settle_date), str(username))).fetchone()
        goods, cnt = _i(r['amt']), _i(r['n'])
        cur = conn.execute(
            "SELECT * FROM settle_invoice WHERE settle_date=? AND username=?",
            (str(settle_date), str(username))).fetchone()
        now = _now()
        if cur is None:
            conn.execute(
                """INSERT INTO settle_invoice
                   (settle_date, username, goods_amount, ship_fee, pack_fee, total_amount,
                    item_count, status, created_by, created_at, updated_at)
                   VALUES (?,?,?,0,0,?,?, 'draft', ?,?,?)""",
                (str(settle_date), str(username), goods, goods, cnt,
                 str(created_by or ''), now, now))
            total = goods
        elif str(cur['status']) == 'paid':
            total = _i(cur['total_amount'])
        else:
            total = goods + _i(cur['ship_fee']) + _i(cur['pack_fee'])
            conn.execute(
                "UPDATE settle_invoice SET goods_amount=?, total_amount=?, item_count=?, "
                "updated_at=? WHERE settle_date=? AND username=?",
                (goods, total, cnt, now, str(settle_date), str(username)))
        conn.commit()
    finally:
        conn.close()
    return total


def mark_billed(settle_date, usernames=None, by=''):
    """청구 — draft를 billed로. 이미 청구·입금된 건은 그대로 둔다."""
    conn = _conn()
    ensure(conn)
    now = _now()
    try:
        sql = ("UPDATE settle_invoice SET status='billed', billed_at=?, updated_at=? "
               "WHERE settle_date=? AND status='draft' AND total_amount>0")
        args = [now, now, str(settle_date)]
        if usernames:
            sql += " AND username IN (%s)" % ",".join("?" * len(usernames))
            args += [str(u) for u in usernames]
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_paid(settle_date, username, paid_amount=None, paid_at='', memo=''):
    """입금완료 체크. paid_amount를 생략하면 청구액 전액 입금으로 본다."""
    conn = _conn()
    ensure(conn)
    try:
        r = conn.execute(
            "SELECT total_amount FROM settle_invoice WHERE settle_date=? AND username=?",
            (str(settle_date), str(username))).fetchone()
        if r is None:
            return False
        amt = _i(paid_amount) if paid_amount is not None else _i(r['total_amount'])
        conn.execute(
            "UPDATE settle_invoice SET status='paid', paid_at=?, paid_amount=?, "
            "memo=COALESCE(NULLIF(?,''), memo), updated_at=? "
            "WHERE settle_date=? AND username=?",
            (str(paid_at or _now()), amt, str(memo or ''), _now(),
             str(settle_date), str(username)))
        conn.commit()
        return True
    finally:
        conn.close()


def unmark_paid(settle_date, username):
    """입금완료 취소 — 잘못 체크했을 때. billed로 되돌리고 금액을 다시 계산한다."""
    conn = _conn()
    ensure(conn)
    try:
        conn.execute(
            "UPDATE settle_invoice SET status='billed', paid_at='', paid_amount=0, "
            "updated_at=? WHERE settle_date=? AND username=?",
            (_now(), str(settle_date), str(username)))
        conn.commit()
    finally:
        conn.close()
    return recompute_invoice(settle_date, username)


def delete_settlement(settle_date, username=None):
    """정산 취소 — 품목과 청구서를 지운다. 입금 완료분은 남긴다.

    반환: (지운 사용자 수, 입금돼서 건너뛴 사용자 목록)
    """
    conn = _conn()
    ensure(conn)
    try:
        args = [str(settle_date)]
        _w = "settle_date=?"
        if username:
            _w += " AND username=?"
            args.append(str(username))
        paid = {str(r['username']) for r in conn.execute(
            "SELECT username FROM settle_invoice WHERE %s AND status='paid'" % _w, args)}
        targets = [str(r['username']) for r in conn.execute(
            "SELECT DISTINCT username FROM settle_invoice WHERE %s" % _w, args)
            if str(r['username']) not in paid]
        for u in targets:
            conn.execute("DELETE FROM settle_item WHERE settle_date=? AND username=?",
                         (str(settle_date), u))
            conn.execute("DELETE FROM settle_invoice WHERE settle_date=? AND username=?",
                         (str(settle_date), u))
        conn.commit()
        return len(targets), sorted(paid)
    finally:
        conn.close()


def reset_all(include_paid=False, restore_cost=True, drop_lots=False):
    """정산을 처음부터 다시 쌓기 위해 원장을 비운다.

    지우는 것:  settle_item · settle_invoice · settle_draft
    되돌리는 것: 주문의 구입가(prev_cost) — 정산이 덮어쓴 값을 원래대로
                 예치금 차감 — 지워지는 청구서에 딸린 차감분을 사용자에게 돌려준다
    남기는 것:  영수증 품목 · 공유상품 · 코스트코번호 매핑
                — 영수증과 매핑은 정산의 '입력'이지 결과가 아니다.

    입금완료된 청구서는 기본적으로 남긴다. 받은 돈의 근거를 지우면 그 입금이
    무엇에 대한 것이었는지 설명할 수 없게 된다.

    예치금 차감건은 status='paid'라 기본 경로에서는 지워지지 않는다. include_paid로
    지울 때는 차감도 함께 되돌린다 — 청구서만 없애면 사용자 잔액에서는 돈이 빠진
    채로 무엇 때문에 빠졌는지 가리킬 곳이 사라진다.

    drop_lots: 영수증 정산으로 넣은 재고 입고도 같이 되돌린다(판매에 안 쓰인 것만).
    반환: {'items','invoices','restored','lots','kept_paid','deposit_returned'}
    """
    from db_core import get_user_db

    conn = _conn()
    ensure(conn)
    res = {'items': 0, 'invoices': 0, 'restored': 0, 'lots': 0, 'kept_paid': [],
           'deposit_returned': 0}
    try:
        paid = {(str(r['settle_date']), str(r['username'])) for r in conn.execute(
            "SELECT settle_date, username FROM settle_invoice WHERE status='paid'")}
        if include_paid:
            paid = set()
        res['kept_paid'] = sorted(paid)

        rows = [dict(r) for r in conn.execute(
            "SELECT settle_date, username, order_no, prev_cost FROM settle_item")]
        rows = [r for r in rows
                if (str(r['settle_date']), str(r['username'])) not in paid]

        # 지워질 청구서 — 예치금 차감을 되돌릴 대상을 지우기 전에 잡아 둔다
        gone = [(str(r['settle_date']), str(r['username'])) for r in conn.execute(
            "SELECT settle_date, username FROM settle_invoice"
            + ("" if include_paid else " WHERE status<>'paid'"))]

        if include_paid:
            res['items'] = conn.execute("DELETE FROM settle_item").rowcount
            res['invoices'] = conn.execute("DELETE FROM settle_invoice").rowcount
        else:
            res['items'] = conn.execute(
                "DELETE FROM settle_item WHERE (settle_date, username) NOT IN "
                "(SELECT settle_date, username FROM settle_invoice WHERE status='paid')"
            ).rowcount
            res['invoices'] = conn.execute(
                "DELETE FROM settle_invoice WHERE status<>'paid'").rowcount
        conn.execute("DELETE FROM settle_draft")
        conn.commit()
    finally:
        conn.close()

    # 없어진 청구서에 딸려 있던 예치금 차감을 사용자에게 돌려준다
    import db_deposit as _dep
    for _d, _u in gone:
        if _dep.deducted(_d, _u):
            _dep.undo_deduct(_d, _u, by='reset', memo="%s 정산 초기화로 차감 취소" % _d)
            res['deposit_returned'] += 1

    # 주문 구입가를 정산 전 값으로 — 청구만 지우고 구입가를 두면
    # 수익계산이 계속 틀린 값을 본다.
    if restore_cost:
        by_user = {}
        for r in rows:
            if str(r['order_no'] or ''):
                by_user.setdefault(str(r['username']), []).append(r)
        for uname, urows in by_user.items():
            try:
                uc = get_user_db(uname)
            except Exception:
                continue
            try:
                for r in urows:
                    for _t in ('order_history', 'daily_orders', 'profit_settlements'):
                        try:
                            uc.execute("UPDATE %s SET cost_price=? WHERE order_no=?" % _t,
                                       (_i(r['prev_cost']), str(r['order_no'])))
                        except sqlite3.Error:
                            pass
                    res['restored'] += 1
                uc.commit()
            finally:
                uc.close()

    if drop_lots:
        try:
            from db_inventory import find_receipt_lots, delete_lots
            _ids = [int(l['id']) for l in (find_receipt_lots() or [])]
            res['lots'] = (delete_lots(_ids) or {}).get('deleted', 0)
        except Exception:
            res['lots'] = 0
    return res


# ── 읽기 ────────────────────────────────────────────────────
def get_items(settle_date, username=None):
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM settle_item WHERE settle_date=?"
        args = [str(settle_date)]
        if username:
            sql += " AND username=?"
            args.append(str(username))
        return [dict(r) for r in conn.execute(sql + " ORDER BY username, order_no", args)]
    finally:
        conn.close()


def get_items_range(date_from, date_to, username=None):
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM settle_item WHERE settle_date BETWEEN ? AND ?"
        args = [str(date_from), str(date_to)]
        if username:
            sql += " AND username=?"
            args.append(str(username))
        return [dict(r) for r in conn.execute(
            sql + " ORDER BY settle_date, username, order_no", args)]
    finally:
        conn.close()


def get_invoice(settle_date, username):
    conn = _conn()
    ensure(conn)
    try:
        r = conn.execute("SELECT * FROM settle_invoice WHERE settle_date=? AND username=?",
                         (str(settle_date), str(username))).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def list_invoices(date_from, date_to=None, username=None, status=None):
    """기간 청구서 — 정산리스트·미입금자 리스트가 모두 이 한 함수를 쓴다."""
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM settle_invoice WHERE settle_date BETWEEN ? AND ?"
        args = [str(date_from), str(date_to or date_from)]
        if username:
            sql += " AND username=?"
            args.append(str(username))
        if status:
            _s = [status] if isinstance(status, str) else list(status)
            sql += " AND status IN (%s)" % ",".join("?" * len(_s))
            args += _s
        return [dict(r) for r in conn.execute(
            sql + " ORDER BY settle_date DESC, total_amount DESC", args)]
    finally:
        conn.close()


def unpaid_invoices(date_to=None, date_from='2000-01-01'):
    """미입금 청구서 — 청구했는데 아직 안 들어온 것.

    draft(아직 청구 전)는 미입금이 아니다. 청구하지 않은 돈을 안 냈다고
    독촉할 수는 없다.
    """
    return list_invoices(date_from, date_to or datetime.now().strftime("%Y-%m-%d"),
                         status='billed')


def unpaid_by_user(date_to=None):
    """미입금자별 누적 — [{username, days, amount, oldest, newest}] 금액 큰 순."""
    out = {}
    for inv in unpaid_invoices(date_to=date_to):
        u = str(inv['username'])
        e = out.setdefault(u, {'username': u, 'days': 0, 'amount': 0,
                               'oldest': '', 'newest': '', 'dates': []})
        e['days'] += 1
        e['amount'] += _i(inv['total_amount'])
        e['dates'].append(str(inv['settle_date']))
    for e in out.values():
        e['dates'].sort()
        e['oldest'], e['newest'] = e['dates'][0], e['dates'][-1]
    return sorted(out.values(), key=lambda e: -e['amount'])


def daily_summary(date_from, date_to):
    """일별 정리 — {rows, by_date, by_user, total, paid, unpaid}."""
    rows = list_invoices(date_from, date_to)
    by_date, by_user = {}, {}
    paid = unpaid = draft = 0
    for r in rows:
        d, u, a = r['settle_date'], r['username'], _i(r['total_amount'])
        by_date.setdefault(d, {})[u] = by_date.setdefault(d, {}).get(u, 0) + a
        by_user[u] = by_user.get(u, 0) + a
        if r['status'] == 'paid':
            paid += _i(r['paid_amount'])
        elif r['status'] == 'billed':
            unpaid += a
        else:
            draft += a
    return {'rows': rows, 'by_date': by_date, 'by_user': by_user,
            'total': sum(by_user.values()), 'paid': paid, 'unpaid': unpaid, 'draft': draft}


def monthly_summary(year_month):
    """월별 사용자 정리 — {username: {goods, fees, total, paid, unpaid, days}}."""
    import calendar
    ym = str(year_month)[:7]
    last = calendar.monthrange(int(ym[:4]), int(ym[5:7]))[1]
    out = {}
    for r in list_invoices('%s-01' % ym, '%s-%02d' % (ym, last)):
        e = out.setdefault(str(r['username']), {
            'goods': 0, 'fees': 0, 'total': 0, 'paid': 0, 'unpaid': 0, 'days': 0})
        e['goods'] += _i(r['goods_amount'])
        e['fees'] += _i(r['ship_fee']) + _i(r['pack_fee'])
        e['total'] += _i(r['total_amount'])
        e['days'] += 1
        if r['status'] == 'paid':
            e['paid'] += _i(r['paid_amount'])
        elif r['status'] == 'billed':
            e['unpaid'] += _i(r['total_amount'])
    return out


def settled_dates(limit=60):
    """정산이 있는 날짜 — [{settle_date, users, total, billed, paid}]."""
    conn = _conn()
    ensure(conn)
    try:
        rows = conn.execute(
            "SELECT settle_date, COUNT(*) users, COALESCE(SUM(total_amount),0) total, "
            "SUM(status='billed') billed, SUM(status='paid') paid "
            "FROM settle_invoice GROUP BY settle_date ORDER BY settle_date DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def settled_order_keys(exclude_date=None):
    """이미 정산에 잡힌 (사용자, 주문번호) — 같은 주문을 두 번 청구하지 않기 위해.

    exclude_date를 주면 그 날짜는 빼고 본다(그날 정산을 다시 돌리는 중이므로
    자기 자신을 '이미 청구됨'으로 보면 아무것도 매칭되지 않는다).
    """
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT username, order_no FROM settle_item WHERE order_no<>''"
        args = []
        if exclude_date:
            sql += " AND settle_date<>?"
            args.append(str(exclude_date))
        return {(str(r['username']), str(r['order_no'])) for r in conn.execute(sql, args)}
    finally:
        conn.close()


def remove_items(username, order_nos):
    """주문이 삭제됐을 때 그 청구 근거도 함께 지운다.

    안 지우면 존재하지 않는 주문이 청구서에 남아, 사용자는 무엇에 대한 돈인지
    확인할 방법이 없는 금액을 청구받는다. 지운 뒤 청구서를 다시 계산한다.
    입금완료된 날은 금액을 건드리지 않는다(recompute_invoice가 지킨다).
    """
    onos = [str(o) for o in (order_nos or []) if str(o or '').strip()]
    if not onos:
        return 0
    conn = _conn()
    ensure(conn)
    try:
        removed, dates = 0, set()
        CHUNK = 900                       # SQLite 변수 한도
        for i in range(0, len(onos), CHUNK):
            part = onos[i:i + CHUNK]
            ph = ",".join("?" * len(part))
            for r in conn.execute(
                    "SELECT DISTINCT settle_date FROM settle_item "
                    "WHERE username=? AND order_no IN (%s)" % ph, [str(username)] + part):
                dates.add(str(r['settle_date']))
            cur = conn.execute(
                "DELETE FROM settle_item WHERE username=? AND order_no IN (%s)" % ph,
                [str(username)] + part)
            removed += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    for d in dates:
        recompute_invoice(d, username)
    return removed


def all_item_orders():
    """[(id, settle_date, username, order_no)] — 고아 정리용 전수 조회."""
    conn = _conn()
    ensure(conn)
    try:
        return [(int(r['id']), str(r['settle_date']), str(r['username']),
                 str(r['order_no'])) for r in conn.execute(
            "SELECT id, settle_date, username, order_no FROM settle_item WHERE order_no<>''")]
    finally:
        conn.close()


def delete_items_by_id(item_ids, restore_cost=True):
    """오매칭 품목을 지운다 — 지운 주문은 다시 매칭 대상이 된다.

    같은 주문을 두 번 청구하지 않으려고 settled_order_keys()가 settle_item을
    읽어 '이미 정산된 주문'을 매칭에서 빼는데, 그 판단 근거가 바로 이 표다.
    그래서 행을 지우면 그 주문은 **영수증 정산에서 자동으로 다시 후보가 된다.**
    별도 표시를 남길 필요가 없다.

    restore_cost: 정산이 주문에 덮어쓴 구입가를 정산 전 값(prev_cost)으로 되돌린다.
      안 되돌리면 청구는 사라졌는데 수익계산은 계속 그 단가를 본다.

    품목이 다 빠지고 비용도 없으면 빈 청구서를 남기지 않는다 — 0원 청구서는
    화면에서 "뭔가 있는 것"처럼 보이지만 청구할 것이 없다.
    반환: 지운 행 수
    """
    ids = [int(i) for i in (item_ids or [])]
    if not ids:
        return 0
    conn = _conn()
    ensure(conn)
    try:
        pairs, gone, removed = set(), [], 0
        CHUNK = 900
        for i in range(0, len(ids), CHUNK):
            part = ids[i:i + CHUNK]
            ph = ",".join("?" * len(part))
            for r in conn.execute(
                    "SELECT settle_date, username, order_no, prev_cost FROM settle_item "
                    "WHERE id IN (%s)" % ph, part):
                pairs.add((str(r['settle_date']), str(r['username'])))
                if str(r['order_no'] or ''):
                    gone.append((str(r['username']), str(r['order_no']), _i(r['prev_cost'])))
            removed += conn.execute(
                "DELETE FROM settle_item WHERE id IN (%s)" % ph, part).rowcount
        conn.commit()
    finally:
        conn.close()

    if restore_cost and gone:
        from db_core import get_user_db
        by_user = {}
        for uname, ono, prev in gone:
            by_user.setdefault(uname, []).append((ono, prev))
        for uname, rows in by_user.items():
            try:
                uc = get_user_db(uname)
            except Exception:
                continue
            try:
                for ono, prev in rows:
                    for _t in ('order_history', 'daily_orders', 'profit_settlements'):
                        try:
                            uc.execute("UPDATE %s SET cost_price=? WHERE order_no=?" % _t,
                                       (prev, ono))
                        except sqlite3.Error:
                            pass
                uc.commit()
            finally:
                uc.close()

    for d, u in pairs:
        recompute_invoice(d, u)
        # 남은 품목도 비용도 없으면 청구서를 치운다
        conn = _conn()
        try:
            r = conn.execute(
                "SELECT item_count, ship_fee, pack_fee, status FROM settle_invoice "
                "WHERE settle_date=? AND username=?", (d, u)).fetchone()
            if (r is not None and str(r['status']) != 'paid'
                    and _i(r['item_count']) == 0
                    and _i(r['ship_fee']) == 0 and _i(r['pack_fee']) == 0):
                conn.execute("DELETE FROM settle_invoice WHERE settle_date=? AND username=?",
                             (d, u))
                conn.commit()
        finally:
            conn.close()
    return removed


# ── 매칭 초안 ────────────────────────────────────────────────
def save_draft(settle_date, rows, created_by=''):
    """그 날짜 초안을 통째로 교체. 반환: 저장한 행 수."""
    import json
    conn = _conn()
    ensure(conn)
    now = _now()
    try:
        conn.execute("DELETE FROM settle_draft WHERE settle_date=?", (str(settle_date),))
        n = 0
        for r in (rows or []):
            u, o = str(r.get('username') or ''), str(r.get('order_no') or '')
            if not (u and o):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO settle_draft "
                "(settle_date, username, order_no, row_json, created_by, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (str(settle_date), u, o, json.dumps(r, ensure_ascii=False),
                 str(created_by or ''), now))
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def get_draft(settle_date):
    import json
    conn = _conn()
    ensure(conn)
    try:
        out = []
        for r in conn.execute("SELECT row_json FROM settle_draft WHERE settle_date=? ORDER BY id",
                              (str(settle_date),)):
            try:
                out.append(json.loads(r['row_json']))
            except (ValueError, TypeError):
                continue
        return out
    finally:
        conn.close()


def draft_dates(limit=30):
    conn = _conn()
    ensure(conn)
    try:
        return [(str(r['settle_date']), _i(r['c'])) for r in conn.execute(
            "SELECT settle_date, COUNT(*) c FROM settle_draft "
            "GROUP BY settle_date ORDER BY settle_date DESC LIMIT ?", (int(limit),))]
    finally:
        conn.close()


def clear_draft(settle_date):
    conn = _conn()
    ensure(conn)
    try:
        conn.execute("DELETE FROM settle_draft WHERE settle_date=?", (str(settle_date),))
        conn.commit()
        return True
    finally:
        conn.close()


# ── 옛 저장소 → 새 원장 이관 ──────────────────────────────────
def migrate_legacy(dry_run=False):
    """receipt_settle_items · purchase_settle_snapshot 을 새 원장으로 옮긴다.

    옛 테이블은 지우지 않는다 — 옮긴 값이 이상하면 대조할 원본이 있어야 한다.
    같은 (날짜, 사용자, 주문, 상품)이 옛 배치에 여러 번 있으면 가장 최근 행만
    쓴다. 하루에 정산을 여러 번 돌린 날은 회차마다 행이 쌓여 있다.
    반환: {'items': n, 'invoices': n, 'skipped_existing': n}
    """
    conn = _conn()
    ensure(conn)
    res = {'items': 0, 'invoices': 0, 'skipped_existing': 0}
    try:
        _has = {r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        already = {(str(r['settle_date']), str(r['username'])) for r in conn.execute(
            "SELECT DISTINCT settle_date, username FROM settle_invoice")}

        touched = set()
        if 'receipt_settle_items' in _has:
            cols = {r['name'] for r in conn.execute("PRAGMA table_info(receipt_settle_items)")}

            def _c(name, default="''"):
                return name if name in cols else default

            rows = conn.execute(
                "SELECT order_date, username, order_no, %s costco_no, %s product_name, "
                "       %s qty, %s split_qty, %s unit_price, amount "
                "FROM receipt_settle_items WHERE id IN "
                "  (SELECT MAX(id) FROM receipt_settle_items "
                "   GROUP BY order_date, username, order_no) "
                "ORDER BY order_date, username"
                % (_c('costco_no'), _c('product_name'), _c('qty', '1'),
                   _c('split_qty', '1'), _c('unit_price', '0'))).fetchall()
            now = _now()
            for r in rows:
                key = (str(r['order_date']), str(r['username']))
                if key in already:
                    res['skipped_existing'] += 1
                    continue
                if dry_run:
                    res['items'] += 1
                    touched.add(key)
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO settle_item
                       (settle_date, username, order_no, product_no, product_name,
                        qty, split_qty, pack, unit_price, amount, source, created_by, created_at)
                       VALUES (?,?,?,?,?,?,?,1,?,?, 'receipt', 'migrate', ?)""",
                    (key[0], key[1], str(r['order_no'] or ''), str(r['costco_no'] or ''),
                     str(r['product_name'] or ''), _i(r['qty']) or 1,
                     _i(r['split_qty']) or 1, _i(r['unit_price']), _i(r['amount']), now))
                res['items'] += 1
                touched.add(key)
            if not dry_run:
                conn.commit()

        # 옛 스냅샷의 택배·포장비(월말분)는 그날 청구서의 fee로 옮긴다
        fees = {}
        if 'purchase_settle_snapshot' in _has:
            for r in conn.execute(
                    "SELECT settle_date, username, fees_total FROM purchase_settle_snapshot "
                    "WHERE COALESCE(fees_total,0)>0"):
                fees[(str(r['settle_date']), str(r['username']))] = _i(r['fees_total'])
    finally:
        conn.close()

    if not dry_run:
        for (d, u) in sorted(touched):
            set_fees(d, u, 0, fees.get((d, u), 0))
            res['invoices'] += 1
    else:
        res['invoices'] = len(touched)
    return res
