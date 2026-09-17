"""반품요청 원장 — '이건 잘못 산 물건이라 코스트코로 되돌린다'는 표시.

왜 따로 두는가:
  영수증에 찍혔지만 주문에 안 붙은 물건을 처리할 길이 둘뿐이었다.
    ① 사용자에게 **청구** — 그 사람이 받은 물건일 때
    ② 사용자 **재고로 입고** — 안 팔려서 창고에 남은 물건일 때
  그런데 **잘못 산 것**은 둘 다 아니다. 청구하면 안 산 사람에게 물건값을 물리고,
  재고로 넣으면 창고에 없는 물건이 장부에 생겨 다음 날 그 유령 재고로 남의
  주문을 메꿨다고 계산한다(build_stock_pool). 그래서 실제로는 **아무것도 안 하고**
  배정 대기에 계속 쌓였고, 매일 같은 품목을 다시 판단해야 했다.

  반품요청은 세 번째 결말이다 — 청구도 재고도 아니고 **매장으로 되돌린다**.
  표시해 두면 배정 대기 목록에서 빠지고, 가용 재고에서도 빠진다.

수량은 팩 단위(qty)와 소분 단위(units) 둘 다 적는다:
  화면은 영수증에 찍힌 그대로 팩으로 읽어야 하고, 재고 차감은 소분 단위라
  둘 중 하나만 두면 부르는 쪽마다 곱하고 나누다 어긋난다(units = qty × split_qty).

상태:
  requested  반품요청 — 아직 매장에 안 가져감
  done       반품완료 — 환불까지 확인

  둘 다 재고에서는 똑같이 빠진다. 요청한 순간 '파는 물건'이 아니기 때문이다.
  잘못 표시한 것은 상태를 바꾸는 게 아니라 **삭제(cancel)** 한다 — 그래야
  배정 대기와 가용 재고로 되돌아온다.
"""
import sqlite3
from datetime import datetime

from db_core import get_auth_db

STATUS_LABEL = {'requested': '반품요청', 'done': '반품완료'}


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
        CREATE TABLE IF NOT EXISTS receipt_return (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            settle_date  TEXT NOT NULL,          -- 그 물건을 산 영수증의 정산일
            costco_no    TEXT NOT NULL,
            product_name TEXT DEFAULT '',
            qty          INTEGER DEFAULT 1,      -- 팩 수 (영수증에 찍힌 단위)
            split_qty    INTEGER DEFAULT 1,
            units        INTEGER DEFAULT 0,      -- 소분 단위 = qty × split_qty
            unit_price   INTEGER DEFAULT 0,      -- 팩단가 (할인 후 실지불)
            list_price   INTEGER DEFAULT 0,      -- 정가 (할인 전)
            amount       INTEGER DEFAULT 0,      -- unit_price × qty
            reason       TEXT DEFAULT '',        -- 왜 잘못 샀나
            status       TEXT DEFAULT 'requested',
            created_by   TEXT DEFAULT '',
            created_at   TEXT DEFAULT '',
            done_by      TEXT DEFAULT '',
            done_at      TEXT DEFAULT ''
        )
    """)
    # 이미 만들어진 표에는 CREATE TABLE IF NOT EXISTS가 아무 일도 하지 않는다.
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(receipt_return)")}
        if 'done_by' not in _cols:
            conn.execute("ALTER TABLE receipt_return ADD COLUMN done_by TEXT DEFAULT ''")
    except sqlite3.Error:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rr_date ON receipt_return(settle_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rr_no ON receipt_return(costco_no)")
    conn.commit()
    if _own:
        conn.close()


# ── 쓰기 ──────────────────────────────────────────────────────
def add(settle_date, picks, created_by=''):
    """반품요청으로 표시한다.

    picks: [{costco_no, name, qty, split_qty, unit_price, list_price, reason}]
      qty는 팩 수다. 소분 단위로만 아는 쪽(재고 현황 화면은 units로 센다)은
      units·amount를 직접 넣을 수 있다 — 넣지 않으면 팩 수에서 계산한다.
      자투리(1팩을 4소분해 2개만 남은 것)도 돌려보낼 수 있어야 해서,
      units를 팩 수로 되계산하게 두면 안 된다.

    같은 날 같은 상품을 막지 않는다 — 3팩 중 1팩만 잘못 산 날이 있고, 나중에
    1팩을 더 돌려보내는 일도 있다. 이중 표시는 부르는 쪽이 '남은 수량'에서
    이미 요청한 만큼을 뺀 목록을 주는 것으로 막는다(_build_assign_rows).

    반환: {'ok': n, 'skipped': n}
    """
    res = {'ok': 0, 'skipped': 0}
    rows = picks or []
    if not rows:
        return res
    conn = _conn()
    ensure(conn)
    now = _now()
    try:
        for p in rows:
            cno = _s(p.get('costco_no'))
            qty = max(0, _i(p.get('qty')))
            sq = max(1, _i(p.get('split_qty')) or 1)
            up = _i(p.get('unit_price'))
            units = _i(p.get('units')) or qty * sq
            amount = _i(p.get('amount')) or up * qty
            if not cno or units <= 0:
                res['skipped'] += 1
                continue
            conn.execute("""
                INSERT INTO receipt_return
                    (settle_date, costco_no, product_name, qty, split_qty, units,
                     unit_price, list_price, amount, reason, status,
                     created_by, created_at, done_by, done_at)
                VALUES (?,?,?,?,?,?,?,?,?,?, 'requested', ?,?,'','')
            """, (_s(settle_date), cno, _s(p.get('name')), qty, sq, units,
                  up, _i(p.get('list_price')), amount, _s(p.get('reason')),
                  _s(created_by), now))
            res['ok'] += 1
        conn.commit()
    finally:
        conn.close()
    return res


def mark_done(ids, by=''):
    """반품완료 — 매장에 돌려주고 환불까지 확인했다."""
    ids = [_i(i) for i in (ids or []) if _i(i) > 0]
    if not ids:
        return 0
    conn = _conn()
    ensure(conn)
    try:
        # created_by는 건드리지 않는다 — 요청한 사람과 돌려준 사람은 다를 수 있고,
        # 덮어쓰면 '누가 이걸 반품하기로 했나'를 다시 물을 수 없다.
        cur = conn.execute(
            "UPDATE receipt_return SET status='done', done_at=?, done_by=? "
            "WHERE id IN (%s)" % ",".join("?" * len(ids)),
            [_now(), _s(by)] + ids)
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def cancel(ids):
    """표시를 지운다 — 알고 보니 반품할 물건이 아니었을 때.

    지우면 그 수량은 배정 대기와 가용 재고로 되돌아온다. 상태를 따로 두지 않는
    이유는, '취소된 반품요청'이 남아 있으면 재고에서 빼야 하는지 아닌지를
    조회하는 쪽마다 다시 판단해야 하기 때문이다.
    """
    ids = [_i(i) for i in (ids or []) if _i(i) > 0]
    if not ids:
        return 0
    conn = _conn()
    ensure(conn)
    try:
        cur = conn.execute(
            "DELETE FROM receipt_return WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


# ── 읽기 ──────────────────────────────────────────────────────
def by_date(settle_date):
    """그날 표시한 반품요청 전부. [{...}]"""
    conn = _conn()
    ensure(conn)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM receipt_return WHERE settle_date=? "
            "ORDER BY status, id", (_s(settle_date),))]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def units_by_date(settle_date):
    """{코스트코번호: units} — 그날 반품요청한 소분 단위 수량.

    배정 대기 목록에서 빼는 데 쓴다. 안 빼면 반품하기로 한 물건이 목록에
    계속 남아 매일 다시 판단하게 된다.
    """
    out = {}
    for r in by_date(settle_date):
        pn = _s(r.get('costco_no'))
        if pn:
            out[pn] = out.get(pn, 0) + _i(r.get('units'))
    return out


def units_upto(date_upto, start=''):
    """{코스트코번호: units} — 기간 내 반품요청 누계.

    가용 재고(build_stock_pool)에서 뺀다. 요청한 순간 그 물건은 파는 물건이
    아니라 매장으로 돌아갈 물건이다 — 남겨 두면 다음 날 그 수량으로 남의
    주문을 메꿨다고 계산한다.
    """
    conn = _conn()
    ensure(conn)
    try:
        q = ("SELECT costco_no, SUM(units) FROM receipt_return WHERE settle_date <= ?"
             + (" AND settle_date >= ?" if start else "") + " GROUP BY costco_no")
        args = (_s(date_upto), _s(start)) if start else (_s(date_upto),)
        out = {}
        for pn, units in conn.execute(q, args):
            pn = _s(pn)
            if pn:
                out[pn] = out.get(pn, 0) + _i(units)
        return out
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def day_total(settle_date):
    """{'count': 종수, 'qty': 팩수, 'amount': 금액} — 대조 화면이 쓴다."""
    rows = by_date(settle_date)
    return {'count': len(rows),
            'qty': sum(_i(r.get('qty')) for r in rows),
            'amount': sum(_i(r.get('amount')) for r in rows)}


def list_range(date_from, date_to=None, status=None):
    """기간 조회 — '언제 무엇을 왜 돌려보냈나'를 본다."""
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM receipt_return WHERE settle_date>=? AND settle_date<=?"
        args = [_s(date_from), _s(date_to or date_from)]
        if status:
            _st = [status] if isinstance(status, str) else list(status)
            sql += " AND status IN (%s)" % ",".join("?" * len(_st))
            args += _st
        return [dict(r) for r in conn.execute(
            sql + " ORDER BY settle_date DESC, id DESC", args)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def pending(date_upto=None):
    """아직 매장에 안 가져간 반품요청 — 기한을 넘기면 돈을 못 돌려받는다."""
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM receipt_return WHERE status='requested'"
        args = []
        if date_upto:
            sql += " AND settle_date <= ?"
            args.append(_s(date_upto))
        return [dict(r) for r in conn.execute(
            sql + " ORDER BY settle_date, id", args)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
