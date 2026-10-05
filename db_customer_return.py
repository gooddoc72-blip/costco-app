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
  · **청구서는 건드리지 않는다.** 자동으로 깎으면 이미 입금된 청구서까지
    흔들려 무엇을 받은 것인지 설명할 수 없게 된다. 대신 매장 반품으로 돌려받은
    돈은 **예치금에 적립**한다(credit_deposit) — 청구서와 별개의 원장이라
    받은 돈과 청구액이 어긋나지 않는다.
  · **잘못 산 물건**(주문 없이 영수증에만 있는 것)은 db_receipt_return이다.
    거긴 애초에 팔린 적이 없어 주문도 고객도 없다. 둘을 한 표에 넣으면
    '누구 주문인가'가 절반은 비어 있게 된다.

상태:
  received        반품입고 — 물건은 받았고 아직 정리 전
  restocked       재고로 되돌림 — 그 사용자 재고(inventory_lots)로 다시 들어갔다
  store_returned  매장 반품 완료 — 코스트코에 돌려주고 환불까지 확인

  정리된 건(restocked·store_returned)은 바로 고치지 않고 **처리 취소**(cancel)로
  정리 대기로 되돌린 뒤 고친다 — 취소가 넣었던 재고를 빼고 적립한 예치금을
  (−)로 되돌려, 움직인 것을 장부에서 함께 되감는다. 잘못 입력한 것은 **정리
  전에** 수정(update)하거나 삭제한다.
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
    # 사용자 매장 반품 요청 — 상태가 아니라 표시다. 물건은 여전히 창고(received)에
    # 있고, 실제 매장 반품은 관리자가 store_return으로 끝낸다. 상태로 만들면
    # '창고에 있는 건'을 고르는 모든 조회(OPEN)를 둘로 나눠 고쳐야 한다.
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(customer_return)")}
        # deposit_id: 매장반품 환불을 예치금에 적립한 원장 행(0=미적립)
        for _c, _t in (('store_req', 'INTEGER DEFAULT 0'),
                       ('store_req_at', "TEXT DEFAULT ''"),
                       ('store_req_by', "TEXT DEFAULT ''"),
                       ('deposit_id', 'INTEGER DEFAULT 0'),
                       ('deposit_amount', 'INTEGER DEFAULT 0'),
                       ('deposit_at', "TEXT DEFAULT ''")):
            if _c not in _cols:
                conn.execute(f"ALTER TABLE customer_return ADD COLUMN {_c} {_t}")
    except sqlite3.Error:
        pass
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


def purchase_total(r):
    """그 반품 건의 구입가 합계 — 개당 구입가 × 수량 (소분이면 나눈다)."""
    _sq = max(1, _i(r.get('split_qty')) or 1)
    return int(round(_i(r.get('unit_cost')) / _sq * _i(r.get('qty'))))


def store_return(ids, refund_amount=0, by='', memo='', amounts=None, credit=True):
    """매장 반품 완료 — 코스트코에 돌려주고 환불까지 확인했다.

    amounts={id: 환불액} 를 주면 **건별 환불액**으로 적고, credit=True면 그
    금액을 그 주문 판매자의 예치금에 바로 적립한다(credit_deposit).
    amounts 없이 refund_amount만 주면 예전 방식 — 합계를 첫 건에만 적고
    적립하지 않는다(누구 몫인지 나눌 수 없어서).

    반환: {'ok': n, 'skipped': n, 'credited': [{id, username, amount}], 'errors': []}
    """
    ids = [_i(i) for i in (ids or []) if _i(i) > 0]
    out = {'ok': 0, 'skipped': 0, 'credited': [], 'errors': []}
    _amt = _i(refund_amount)
    for _id in ids:
        _this = _i((amounts or {}).get(_id)) if amounts is not None else _amt
        if _finish(_id, 'store_returned', by=by, refund_amount=_this, memo=memo):
            out['ok'] += 1
            if amounts is None:
                _amt = 0                  # 합계 방식 — 환불금액은 한 번만
            elif credit and _this > 0:
                _c = credit_deposit(_id, by=by)
                if _c['ok']:
                    out['credited'].append(_c)
                else:
                    out['errors'].append(f"#{_id} {_c['msg']}")
        else:
            out['skipped'] += 1
    return out


def credit_deposit(_id, by=''):
    """매장반품 환불액을 그 주문 판매자의 예치금에 적립한다 — 반품 1건당 한 번.

    판매자에게 넣는 이유: 그 물건값은 판매자에게 청구됐다. 매장에서 돌려받은
    돈은 그 청구분을 되돌려 주는 것이다.
    적립 사유(예치금 메모)에 반품번호·상품·수량·구입가·환불액·사유·주문번호를
    남긴다 — 예치금 내역만 보고도 무엇이 반품돼 들어온 돈인지 알 수 있어야 한다.

    반환: {'ok', 'msg', 'id', 'username', 'amount', 'deposit_id'}
    """
    import db_deposit as _dep
    r = get(_id)
    if not r:
        return {'ok': False, 'msg': '없는 반품 건입니다.'}
    if str(r.get('status')) != 'store_returned':
        return {'ok': False, 'msg': '매장 반품 완료된 건만 적립합니다.'}
    if _i(r.get('deposit_id')):
        return {'ok': False, 'msg': '이미 예치금에 적립됐습니다.'}
    amt = _i(r.get('refund_amount'))
    if amt <= 0:
        return {'ok': False, 'msg': '환불금액이 0원입니다.'}
    un = _s(r.get('username'))
    _memo = (f"매장반품 환불 적립 · 반품#{_i(_id)} · {_s(r.get('product_name'))[:40]} "
             f"{_i(r.get('qty'))}개 · 구입가 {purchase_total(r):,}원 · 환불 {amt:,}원"
             + (f" · 사유 {_s(r.get('reason'))}" if r.get('reason') else '')
             + (f" · 주문 {_s(r.get('order_no'))}" if r.get('order_no') else ''))
    # 먼저 반품 건을 '적립 중'으로 잡는다 — 버튼을 두 번 눌러도 두 줄이 안 생기게.
    conn = _conn()
    ensure(conn)
    try:
        cur = conn.execute("UPDATE customer_return SET deposit_id=-1 "
                           "WHERE id=? AND COALESCE(deposit_id,0)=0", (_i(_id),))
        conn.commit()
        if cur.rowcount != 1:
            return {'ok': False, 'msg': '이미 예치금에 적립됐습니다.'}
    finally:
        conn.close()
    try:
        _did = _dep.return_credit(un, amt, _memo, by=by)
    except Exception as e:
        _did = 0
        _err = str(e)
    else:
        _err = ''
    conn = _conn()
    try:
        conn.execute("UPDATE customer_return SET deposit_id=?, deposit_amount=?, "
                     "deposit_at=? WHERE id=?",
                     (_did, amt if _did else 0, _now() if _did else '', _i(_id)))
        conn.commit()
    finally:
        conn.close()
    if not _did:
        return {'ok': False, 'msg': f'예치금 적립 실패 {_err}'.strip()}
    return {'ok': True, 'msg': '적립', 'id': _i(_id), 'username': un,
            'amount': amt, 'deposit_id': _did}


def update(_id, by='', **fields):
    """정리 대기(received) 건 수정 — 수량·개당 구입가·코스트코번호·사유·메모.
    정리된 건은 고치지 않는다(재고·예치금이 이미 움직였다) — 먼저 취소한다.
    반환: {'ok', 'msg'}"""
    r = get(_id)
    if not r:
        return {'ok': False, 'msg': '없는 반품 건입니다.'}
    if str(r.get('status')) != OPEN:
        return {'ok': False, 'msg': '정리된 건은 수정할 수 없습니다 — 먼저 처리 취소하세요.'}
    _set, _args = [], []
    if 'qty' in fields:
        if _i(fields['qty']) <= 0:
            return {'ok': False, 'msg': '수량은 1 이상이어야 합니다.'}
        _set.append('qty=?'); _args.append(_i(fields['qty']))
    if 'unit_cost' in fields:
        _set.append('unit_cost=?'); _args.append(max(0, _i(fields['unit_cost'])))
    for _k in ('costco_no', 'reason', 'memo'):
        if _k in fields:
            _set.append(f'{_k}=?'); _args.append(_s(fields[_k]))
    if not _set:
        return {'ok': False, 'msg': '바꿀 내용이 없습니다.'}
    conn = _conn()
    try:
        conn.execute(f"UPDATE customer_return SET {', '.join(_set)} "
                     "WHERE id=? AND status=?", (*_args, _i(_id), OPEN))
        conn.commit()
    finally:
        conn.close()
    return {'ok': True, 'msg': '수정했습니다.'}


def update_store_returned(_id, by='', unit_cost=None, refund_amount=None):
    """매장 반품 완료 건의 개당 구입가·환불액 수정 — **예치금 적립 전**에만.

    적립된 뒤 환불액을 고치면 예치금과 어긋난다. 그때는 처리 취소 → 다시 처리.
    반환: {'ok', 'msg'}"""
    r = get(_id)
    if not r:
        return {'ok': False, 'msg': '없는 반품 건입니다.'}
    if str(r.get('status')) != 'store_returned':
        return {'ok': False, 'msg': '매장 반품 완료 건이 아닙니다.'}
    if _i(r.get('deposit_id')):
        return {'ok': False, 'msg': '이미 예치금에 적립된 건입니다 — 처리 취소 후 고치세요.'}
    _set, _args = [], []
    if unit_cost is not None:
        _set.append('unit_cost=?'); _args.append(max(0, _i(unit_cost)))
    if refund_amount is not None:
        _set.append('refund_amount=?'); _args.append(max(0, _i(refund_amount)))
    if not _set:
        return {'ok': False, 'msg': '바꿀 내용이 없습니다.'}
    _note = f"{_now()} {by} 수정(구입가 {_i(r.get('unit_cost')):,}→" \
            f"{_i(unit_cost if unit_cost is not None else r.get('unit_cost')):,} · 환불 " \
            f"{_i(r.get('refund_amount')):,}→" \
            f"{_i(refund_amount if refund_amount is not None else r.get('refund_amount')):,})"
    conn = _conn()
    try:
        conn.execute(
            f"UPDATE customer_return SET {', '.join(_set)}, "
            "memo=CASE WHEN COALESCE(memo,'')='' THEN ? ELSE memo || ' / ' || ? END "
            "WHERE id=? AND status='store_returned' AND COALESCE(deposit_id,0)=0",
            (*_args, _note, _note, _i(_id)))
        conn.commit()
    finally:
        conn.close()
    return {'ok': True, 'msg': '수정했습니다.'}


def cancel(_id, by=''):
    """처리 취소 — 정리된 건을 정리 대기(received)로 되돌린다.

    restocked      : 그때 넣은 재고를 같은 보유자 재고에서 다시 뺀다. 이미
                     팔려 남은 재고가 모자라면 취소하지 않는다(장부만 되돌리면
                     실물 없는 재고가 남는다).
    store_returned : 예치금에 적립했으면 같은 금액을 (−)로 되돌린다. 행을
                     지우지 않아 "적립했다가 취소했다"가 예치금 내역에 남는다.
    반환: {'ok', 'msg'}
    """
    r = get(_id)
    if not r:
        return {'ok': False, 'msg': '없는 반품 건입니다.'}
    st_ = str(r.get('status'))
    _tag = f"#{_i(_id)} {_s(r.get('product_name'))[:30]} {_i(r.get('qty'))}개"
    if st_ == 'restocked':
        try:
            from db_inventory import adjust_stock
            res = adjust_stock(
                owner=_s(r.get('restock_owner')) or _s(r.get('username')),
                product_no=_s(r.get('costco_no')), units=-_i(r.get('qty')),
                reason=f"고객 반품 재입고 취소 #{_i(_id)}", by=by)
        except Exception as e:
            return {'ok': False, 'msg': f'재고 차감 실패 — {e}'}
        if not res.get('ok'):
            return {'ok': False, 'msg': f"재고에서 뺄 수 없습니다 — {res.get('msg')}"}
        _msg = f"{_tag} 재고에서 다시 뺐습니다."
    elif st_ == 'store_returned':
        _msg = f"{_tag} 매장 반품을 취소했습니다."
        _did, _amt = _i(r.get('deposit_id')), _i(r.get('deposit_amount'))
        if _did < 0:
            return {'ok': False, 'msg': '예치금 적립이 진행 중입니다. 잠시 뒤 다시 하세요.'}
        if _did > 0 and _amt > 0:
            import db_deposit as _dep
            _cid = _dep.return_credit_cancel(
                _s(r.get('username')), _amt,
                f"매장반품 적립 취소 · 반품#{_i(_id)} · {_s(r.get('product_name'))[:40]} "
                f"{_i(r.get('qty'))}개 · −{_amt:,}원", by=by)
            if not _cid:
                return {'ok': False, 'msg': '예치금 되돌림 실패'}
            _msg += f" 예치금 {_amt:,}원을 되돌렸습니다."
    else:
        return {'ok': False, 'msg': '정리 대기 건은 취소할 것이 없습니다.'}
    conn = _conn()
    try:
        conn.execute(
            "UPDATE customer_return SET status=?, restock_owner='', refund_amount=0, "
            "deposit_id=0, deposit_amount=0, deposit_at='', done_by='', done_at='', "
            "memo=CASE WHEN COALESCE(memo,'')='' THEN ? ELSE memo || ' / ' || ? END "
            "WHERE id=?",
            (OPEN, f"{_now()} {by} 처리취소({STATUS_LABEL.get(st_, st_)})",
             f"{_now()} {by} 처리취소({STATUS_LABEL.get(st_, st_)})", _i(_id)))
        conn.commit()
    finally:
        conn.close()
    return {'ok': True, 'msg': _msg}


def uncredited_store_returns():
    """매장 반품 완료 + 환불액 있음 + 아직 예치금 미적립 — 소급 적립 대상."""
    return [r for r in list_returns(status='store_returned', limit=5000)
            if _i(r.get('refund_amount')) > 0 and not _i(r.get('deposit_id'))]


def set_store_request(ids, username, on=True, by=''):
    """사용자가 자기 반품 건에 '매장 반품 요청'을 걸거나 푼다.

    **정리 전(received)이고 그 사용자 건**만 바꾼다. 이미 재고로 되돌렸거나
    매장에 간 건, 남의 건은 건드리지 않는다(화면 밖에서 번호만 바꿔 보내도).
    반환: {'ok': n, 'skipped': n}
    """
    ids = [_i(i) for i in (ids or []) if _i(i) > 0]
    out = {'ok': 0, 'skipped': 0}
    if not ids or not _s(username):
        return out
    conn = _conn()
    ensure(conn)
    try:
        for _id in ids:
            cur = conn.execute(
                "UPDATE customer_return SET store_req=?, store_req_at=?, store_req_by=? "
                "WHERE id=? AND username=? AND status=?",
                (1 if on else 0, _now() if on else '', _s(by) if on else '',
                 _id, _s(username), OPEN))
            if cur.rowcount:
                out['ok'] += 1
            else:
                out['skipped'] += 1
        conn.commit()
    finally:
        conn.close()
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
