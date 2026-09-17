"""고객 반품 입고 원장 — 팔려 나간 물건이 되돌아온 뒤의 행방.

왜 필요한가:
  고객이 반품하면 물건은 관리자에게 돌아오는데, 그 사실이 어디에도 안 남았다.
  남은 선택지가 둘뿐인데 둘 다 기록되지 않았다 — 멀쩡하면 **그 사용자 재고로
  되돌리고**, 아니면 **코스트코 매장에 돌려준다**. 그래서 매번 이런 질문이 남았다.
    · 지난주에 들어온 반품이 재고로 들어갔나, 매장에 갔나, 아직 창고에 있나
    · 매장 반품 기한이 얼마나 남았나 (기한을 넘기면 그대로 손실이다)
    · 이 사용자 재고가 왜 늘었나 (조정 사유만으로는 어느 반품인지 못 찾는다)

  물건 하나가 돌아와서 나가기까지를 한 줄로 남긴다. 그 줄이 곧 답이다.

이 원장이 다루지 않는 것:
  · **청구는 건드리지 않는다.** 반품분 차감은 관리자가 정산·청구 화면에서
    직접 한다. 자동으로 깎으면 이미 입금된 청구서까지 흔들려 무엇을 받은
    것인지 설명할 수 없게 된다. 여기는 '무엇이 돌아왔고 어디로 갔나'만 답한다.
  · **잘못 산 물건**(주문 없이 영수증에만 있는 것)은 db_receipt_return이다.
    거긴 애초에 팔린 적이 없어 주문도 고객도 없다. 둘을 한 표에 넣으면
    '누구 주문인가'가 절반은 비어 있게 된다.

상태:
  received        반품입고 — 물건은 받았고 아직 정리 전
  restocked       재고로 되돌림 — 그 사용자 재고(inventory_lots)로 다시 들어갔다
  store_returned  매장 반품 완료 — 코스트코에 돌려주고 환불까지 확인

  정리된 건(restocked·store_returned)은 되돌릴 수 없다. 재고를 이미 움직였거나
  물건이 매장으로 갔기 때문이다. 잘못 입력한 것은 **정리 전에** 삭제한다.
"""
import sqlite3
from datetime import datetime

from db_core import get_auth_db

STATUS_LABEL = {
    'received': '반품입고(정리 전)',
    'restocked': '재고로 되돌림',
    'store_returned': '매장 반품 완료',
}

#: 아직 창고에 있는 상태 — 매장 반품 기한이 흐르는 구간이다
OPEN = 'received'


def _conn():
    return get_auth_db(row=True)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


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
        CREATE TABLE IF NOT EXISTS customer_return (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            returned_at   TEXT NOT NULL,          -- 반품입고일 (물건을 받은 날)
            username      TEXT NOT NULL,          -- 그 주문을 판 사용자
            order_no      TEXT DEFAULT '',        -- 상품주문번호
            order_date    TEXT DEFAULT '',
            recipient     TEXT DEFAULT '',
            product_name  TEXT DEFAULT '',
            naver_no      TEXT DEFAULT '',
            costco_no     TEXT DEFAULT '',        -- 재고로 되돌릴 때 필요하다
            qty           INTEGER DEFAULT 1,      -- 소분 단위(판매 1개 기준)
            split_qty     INTEGER DEFAULT 1,
            unit_cost     INTEGER DEFAULT 0,      -- 팩 구입가 (재고 단가의 근거)
            reason        TEXT DEFAULT '',        -- 고객 반품 사유
            status        TEXT DEFAULT 'received',
            restock_owner TEXT DEFAULT '',        -- 재고로 되돌린 대상 사용자
            refund_amount INTEGER DEFAULT 0,      -- 매장에서 돌려받은 금액
            memo          TEXT DEFAULT '',
            created_by    TEXT DEFAULT '',
            created_at    TEXT DEFAULT '',
            done_by       TEXT DEFAULT '',
            done_at       TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_status ON customer_return(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_user ON customer_return(username, returned_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cr_order ON customer_return(order_no)")
    conn.commit()
    if _own:
        conn.close()


# ── 쓰기 ──────────────────────────────────────────────────────
def add(rows, created_by=''):
    """반품입고 등록.

    rows: [{returned_at, username, order_no, order_date, recipient, product_name,
            naver_no, costco_no, qty, split_qty, unit_cost, reason, memo}]

    같은 주문번호를 두 번 넣는 것을 막지 않는다. 한 주문에서 2개 중 1개만
    반품되고 며칠 뒤 나머지가 오는 일이 있다. 대신 화면이 '이 주문에 이미
    들어온 반품'을 함께 보여 준다(by_order).

    반환: {'ok': n, 'skipped': n}
    """
    res = {'ok': 0, 'skipped': 0}
    if not rows:
        return res
    conn = _conn()
    ensure(conn)
    now = _now()
    try:
        for r in rows:
            un = _s(r.get('username'))
            qty = max(0, _i(r.get('qty')))
            if not un or qty <= 0:
                res['skipped'] += 1
                continue
            conn.execute("""
                INSERT INTO customer_return
                    (returned_at, username, order_no, order_date, recipient,
                     product_name, naver_no, costco_no, qty, split_qty, unit_cost,
                     reason, status, restock_owner, refund_amount, memo,
                     created_by, created_at, done_by, done_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'received', '',0, ?,?,?,'','')
            """, (_s(r.get('returned_at')) or _today(), un, _s(r.get('order_no')),
                  _s(r.get('order_date')), _s(r.get('recipient')),
                  _s(r.get('product_name')), _s(r.get('naver_no')),
                  _s(r.get('costco_no')), qty, max(1, _i(r.get('split_qty')) or 1),
                  _i(r.get('unit_cost')), _s(r.get('reason')), _s(r.get('memo')),
                  _s(created_by), now))
            res['ok'] += 1
        conn.commit()
    finally:
        conn.close()
    return res


def delete(ids):
    """잘못 입력한 반품입고를 지운다 — **정리 전(received)** 인 것만.

    재고로 되돌렸거나 매장에 보낸 건은 지우지 않는다. 지워도 움직인 재고와
    매장에 간 물건은 돌아오지 않아, 장부만 실물과 어긋나게 된다.

    반환: {'deleted': n, 'skipped': [id...]}
    """
    ids = [_i(i) for i in (ids or []) if _i(i) > 0]
    out = {'deleted': 0, 'skipped': []}
    if not ids:
        return out
    conn = _conn()
    ensure(conn)
    try:
        for _id in ids:
            row = conn.execute("SELECT status FROM customer_return WHERE id=?",
                               (_id,)).fetchone()
            if row is None or str(row['status']) != OPEN:
                out['skipped'].append(_id)
                continue
            conn.execute("DELETE FROM customer_return WHERE id=?", (_id,))
            out['deleted'] += 1
        conn.commit()
    finally:
        conn.close()
    return out


def _finish(_id, status, by='', **fields):
    """정리 완료 표시 — 재고 복귀·매장 반품이 같은 경로를 쓰도록 한 곳에 모았다.

    이미 정리된 건은 다시 바꾸지 않는다. 재고로 되돌린 물건을 매장 반품으로
    덮어쓰면 재고에는 남아 있는데 장부에는 매장에 간 것으로 적힌다.
    """
    conn = _conn()
    ensure(conn)
    try:
        row = conn.execute("SELECT status FROM customer_return WHERE id=?",
                           (_i(_id),)).fetchone()
        if row is None or str(row['status']) != OPEN:
            return False
        conn.execute(
            "UPDATE customer_return SET status=?, restock_owner=?, "
            "refund_amount=?, memo=CASE WHEN ?<>'' THEN ? ELSE memo END, "
            "done_by=?, done_at=? WHERE id=?",
            (status, _s(fields.get('restock_owner')),
             _i(fields.get('refund_amount')), _s(fields.get('memo')),
             _s(fields.get('memo')), _s(by), _now(), _i(_id)))
        conn.commit()
        return True
    finally:
        conn.close()


def restock(_id, owner='', by='', memo=''):
    """재고로 되돌린다 — 상태가 멀쩡해 다시 팔 수 있는 건.

    실제 입고는 db_inventory.adjust_stock이 한다. 재고 원장을 여기서 직접
    건드리지 않는 이유는, 입고 경로가 둘이 되면 '이 lot이 어디서 왔나'를
    두 곳에서 따로 설명하게 되기 때문이다. adjust_stock은 lot 번호를 돌려주지
    않으므로, 대신 **사유에 반품 번호를 박는다** — 재고 조정 이력만 보고도
    어느 반품에서 온 물건인지 되짚을 수 있다.

    owner를 안 주면 원래 주문의 사용자에게 되돌린다.
    반환: {'ok': bool, 'msg': str}
    """
    r = get(_id)
    if not r:
        return {'ok': False, 'msg': '없는 반품 건입니다.'}
    if str(r.get('status')) != OPEN:
        return {'ok': False, 'msg': '이미 정리된 건입니다.'}
    cno = _s(r.get('costco_no'))
    if not cno:
        return {'ok': False, 'msg': '코스트코 상품번호가 없어 재고로 넣을 수 없습니다 — '
                                   '반품 건을 지우고 번호를 넣어 다시 등록하세요.'}
    owner = _s(owner) or _s(r.get('username'))
    try:
        from db_inventory import adjust_stock
        res = adjust_stock(
            owner=owner, product_no=cno, units=_i(r.get('qty')),
            reason=f"고객 반품 재입고 #{_i(_id)}"
                   + (f" · {_s(r.get('order_no'))}" if r.get('order_no') else ''),
            by=by, product_name=_s(r.get('product_name')),
            unit_cost=_i(r.get('unit_cost')),
            split_qty=max(1, _i(r.get('split_qty')) or 1))
    except Exception as e:
        return {'ok': False, 'msg': f"재고 입고 실패 — {e}"}
    if not res.get('ok'):
        return {'ok': False, 'msg': res.get('msg') or '재고 입고 실패'}
    _finish(_id, 'restocked', by=by, restock_owner=owner, memo=memo)
    return {'ok': True, 'msg': f"{owner} 재고로 {_i(r.get('qty'))}개 되돌렸습니다."}


def store_return(ids, refund_amount=0, by='', memo=''):
    """매장 반품 완료 — 코스트코에 돌려주고 환불까지 확인했다.

    환불금액은 여러 건을 한 번에 처리하면 **첫 건에만** 적는다. 건마다 쪼개
    넣으면 실제로 받은 총액과 장부 합계가 어긋난다(영수증은 한 장이다).

    반환: {'ok': n, 'skipped': n}
    """
    ids = [_i(i) for i in (ids or []) if _i(i) > 0]
    out = {'ok': 0, 'skipped': 0}
    _amt = _i(refund_amount)
    for _id in ids:
        if _finish(_id, 'store_returned', by=by, refund_amount=_amt, memo=memo):
            out['ok'] += 1
            _amt = 0                      # 환불금액은 한 번만
        else:
            out['skipped'] += 1
    return out


# ── 읽기 ──────────────────────────────────────────────────────
def get(_id):
    conn = _conn()
    ensure(conn)
    try:
        row = conn.execute("SELECT * FROM customer_return WHERE id=?",
                           (_i(_id),)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def list_returns(status=None, username='', date_from='', date_to='', limit=500):
    """조건 조회. status는 문자열 하나 또는 목록."""
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM customer_return WHERE 1=1"
        args = []
        if status:
            _st = [status] if isinstance(status, str) else list(status)
            sql += " AND status IN (%s)" % ",".join("?" * len(_st))
            args += _st
        if username:
            sql += " AND username=?"
            args.append(_s(username))
        if date_from:
            sql += " AND returned_at >= ?"
            args.append(_s(date_from))
        if date_to:
            sql += " AND returned_at <= ?"
            args.append(_s(date_to))
        sql += " ORDER BY returned_at DESC, id DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def open_returns(username=''):
    """아직 정리 안 된 반품입고 — 매장 반품 기한이 흐르는 건들."""
    return list_returns(status=OPEN, username=username)


def by_order(username, order_no):
    """그 주문에 이미 들어온 반품. 같은 주문을 두 번 넣기 전에 보여 준다."""
    if not (_s(username) and _s(order_no)):
        return []
    conn = _conn()
    ensure(conn)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM customer_return WHERE username=? AND order_no=? "
            "ORDER BY id", (_s(username), _s(order_no)))]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def summary(username=''):
    """{status: {count, qty, amount}} — 화면 맨 위 숫자 세 개."""
    out = {}
    for r in list_returns(username=username, limit=5000):
        e = out.setdefault(str(r.get('status') or ''),
                           {'count': 0, 'qty': 0, 'amount': 0})
        e['count'] += 1
        e['qty'] += _i(r.get('qty'))
        # 금액은 구입가 기준 — 얼마짜리 물건이 묶여 있나에 답한다
        _sq = max(1, _i(r.get('split_qty')) or 1)
        e['amount'] += int(_i(r.get('unit_cost')) / _sq * _i(r.get('qty')))
    return out
