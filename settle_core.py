"""정산 파이프라인 — 매칭 결과를 청구서까지 밀어 넣는 한 줄기.

업무 흐름 그대로다:
  ① 당일 주문 수집 → 매장 구매      (order_upload_page · 장보기 목록)
  ② 관리자 택배 발송                (dispatch)
  ③ 각 사용자 송장 등록             (tracking_page)
  ④ 익일 영수증 등록 → 매칭 → 정산  (여기 finalize)
  ⑤ 정산리스트                      (settle_billing_page)
  ⑥ 사용자 일별 확인                (my_purchase_page)
  ⑦ 청구 → 입금완료 체크            (settle_billing_page)
  ⑧ 미입금자 리스트                 (settle_billing_page)

매칭 엔진 자체(receipt_settle.py)는 그대로 쓴다. 바꾼 것은 '매칭이 끝난 뒤'다.
예전에는 여기서 네 군데에 서로 다른 모양으로 저장했다. 이제 db_settle 한 곳이다.
"""
from run_memo import memo as _run_memo   # 화면 한 번 그리는 동안만 같은 조회 재사용
import db_settle as _ds

#: 매칭 경로(via) → 청구 근거(source). 화면에서 "왜 이 금액인가"에 답한다.
#  영수증에서 나온 단가면 경로가 무엇이든 'receipt'다 — 사용자에게 중요한 건
#  '어떻게 찾았나'가 아니라 '그날 산 값인가, 재고 값인가, 사람이 정한 값인가'다.
_VIA_TO_SOURCE = {
    'number': 'receipt', 'name': 'receipt', 'ai': 'receipt', 'bridge': 'receipt',
    'order': 'receipt', 'shopping': 'receipt', 'shopping-name': 'receipt',
    'carry': 'receipt',      # 이월분이지만 단가는 그날 영수증에서 나왔다
    'stock': 'stock',
    'manual': 'manual',
    'memo': 'direct',        # 주문 없이 관리자가 직접 배정한 교환·추가 발송분
    'direct': 'direct',
    'online': 'online',      # 코스트코 온라인몰에서 사서 코스트코가 직접 보낸 건
    # 직접구매자가 자기 영수증(self_receipt_items)으로 정산한 건. 관리자 창고
    # 물건이 나간 게 아니라 공용 재고 계산(build_stock_pool 등)에서 뺀다.
    'self': 'self',
}


def _i(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


# ── 그날 택배비·포장비 ────────────────────────────────────────
@_run_memo
def daily_fees(username, settle_date):
    """그날 발생한 택배비·포장비. 청구액에 함께 실린다.

    월말에 한 달치를 몰아 붙이던 것을 발생일로 옮겼다. 몰아 붙이면 말일
    청구서만 유독 커지고, 달 중간에 그만둔 사용자에게는 영영 청구하지 못한다.

    택배비 = 건별로 지정된 금액이 있으면 그 값, 없으면 사용자 택배비 설정
             (부피가 크면 택배 요금이 다르다 — 단일 단가로는 맞출 수 없다)
    포장비 = 그날 발송한 주문에 실제 배정된 포장비 합
             (배정이 없으면 발송건수 × 기본 박스비 — 포장은 어차피 나간다)

    **코스트코 온라인몰 직배송 건은 뺀다.** 그 건은 코스트코가 고객에게 바로
    보내므로 관리자가 포장도 발송도 하지 않았다. 그런데 사용자가 코스트코 송장을
    자기 스토어에 등록하면 dispatch_log가 생겨 여기 발송건으로 잡히고, 내지도 않은
    택배비·포장비가 청구된다. 온라인몰 원장(db_online_purchase)에 표시된 주문은
    발송건수에서 제외해야 실제 지출과 청구가 맞는다.
    """
    from db import get_all_settings
    from db_dispatch_log import get_dispatched_orders_with_details
    from db_packaging import get_packaging_cost_map, get_ship_cost_map

    try:
        rows = get_dispatched_orders_with_details(username, str(settle_date)) or []
    except Exception:
        rows = []
    onos = sorted({str(r.get('order_no') or '') for r in rows if r.get('order_no')})
    # 온라인몰 직배송 건 제외 — 관리자가 부담하지 않은 비용이다
    online_nos = set()
    try:
        import db_online_purchase as _op
        online_nos = _op.order_nos(username) or set()
    except Exception:
        online_nos = set()
    online_skipped = sorted(o for o in onos if o in online_nos)
    if online_skipped:
        onos = [o for o in onos if o not in online_nos]
    ship_count = len(onos)

    s = get_all_settings(username) or {}
    ship_unit = _i(s.get('shipping_cost')) or 2000
    box_unit = _i(s.get('box_cost')) or 300

    pkg, shp = {}, {}
    if onos:
        try:
            pkg = get_packaging_cost_map(username, onos) or {}
        except Exception:
            pkg = {}
        try:
            shp = get_ship_cost_map(username, onos) or {}
        except Exception:
            shp = {}
    # 배정이 있는 주문은 배정액, 없는 주문은 기본 박스비. 배정 화면을 안 쓰는
    # 사용자에게 포장비가 0으로 잡히면 그만큼이 그대로 손실로 남는다.
    pack_fee = sum(_i(pkg.get(o)) or box_unit for o in onos)
    # 건별 택배비가 지정된 주문은 그 값으로. 부피 큰 건이 섞인 날은 단가 × 건수로
    # 계산하면 실제 낸 택배비와 어긋난다.
    ship_fee = sum(_i(shp.get(o)) or ship_unit for o in onos)

    return {'ship_count': ship_count, 'ship_unit': ship_unit,
            'ship_fee': ship_fee, 'ship_custom': len(shp), 'pack_fee': pack_fee,
            'order_nos': onos,
            # 왜 발송건수가 dispatch_log보다 적은지 화면에서 답할 수 있어야 한다
            'online_skipped': online_skipped}


def fees_for_users(usernames, settle_date):
    """{username: {ship_fee, pack_fee, ...}} — 청구서에 실을 비용."""
    return {u: daily_fees(u, settle_date) for u in (usernames or [])}


# ── 매칭 결과 → 원장 행 ───────────────────────────────────────
def to_ledger_rows(alloc_rows, settle_date, receipt_date=''):
    """receipt_settle의 매칭 행을 settle_item 모양으로 옮긴다.

    금액이 0인 행은 버린다. 단가를 못 찾았다는 뜻인데, 0원으로 청구서에 실으면
    그 주문은 공짜로 나가고 손실이 조용히 묻힌다. 부족분으로 남겨 사람이 본다.
    """
    out, dropped = [], []
    _self_cache = {}

    def _is_self(u):
        if u not in _self_cache:
            try:
                import receipt_settle as _rs
                _self_cache[u] = _rs.is_self_purchase(u)
            except Exception:
                _self_cache[u] = False
        return _self_cache[u]

    for r in (alloc_rows or []):
        amt = _i(r.get('amount'))
        row = {
            'username': str(r.get('username') or ''),
            'order_no': str(r.get('order_no') or ''),
            'product_no': str(r.get('costco_no') or r.get('product_no') or ''),
            'naver_no': str(r.get('naver_no') or ''),
            'product_name': str(r.get('product_name') or ''),
            'recipient': str(r.get('recipient') or ''),
            'qty': _i(r.get('qty')) or 1,
            'split_qty': _i(r.get('split_qty')) or 1,
            'pack': _i(r.get('pack')) or 1,
            'unit_price': _i(r.get('unit_price')),
            'amount': amt,
            'prev_cost': _i(r.get('prev_cost')),
            'source': _VIA_TO_SOURCE.get(str(r.get('via') or ''), 'receipt'),
            'receipt_date': str(r.get('receipt_date') or receipt_date or settle_date),
            'memo': str(r.get('memo') or ''),
            # 📦 재고 출고로 고른 보유자 — 확정 때 그 사람 재고 장부에서 뺀다
            'stock_owner': str(r.get('stock_owner') or ''),
        }
        if not row['username']:
            continue
        # 대리구매 — 직접구매 계정인데 자기 영수증(source='self')이 아닌 행은
        # 관리자 영수증·재고에서 나간 것이다. 관리자 돈이 나갔으니 청구 대상이다
        # (db_settle.recompute_invoice가 직접구매 계정은 이 행만 청구한다).
        row['proxy'] = 1 if (row['source'] != 'self' and _is_self(row['username'])) else 0
        # 본인 재고(이미 낸 것)를 본인 주문으로 내보낸 행은 0원이 정상이다 —
        # 버리면 재고 장부에서 차감되지 않고(consume_stock_rows가 원장 행을 본다)
        # 그 주문이 '청구 안 된 발송건'으로 남는다.
        _own_free = (amt <= 0 and row['stock_owner']
                     and row['stock_owner'] == row['username'])
        (out if (amt > 0 or _own_free) else dropped).append(row)
    return out, dropped


# ── ④ 정산 확정 ──────────────────────────────────────────────
def finalize(settle_date, alloc_rows, created_by='', with_fees=False,
             apply_cost=True, learn=True, replace=False):
    """정산 요청 — 원장에 쓰고 청구서를 만든다. 이 함수가 유일한 확정 경로다.

    replace=True면 이번 배치에 등장한 사용자의 그 날짜 품목을 통째로 교체한다
    (다시 정산할 때 옛 값이 남아 금액이 불어나는 것을 막는다). 기본은 병합 —
    영수증을 나눠 올린 날 앞 회차가 사라지면 안 되기 때문이다.

    with_fees=False가 기본 — **청구액은 물건값만이다.**
    택배비·포장비는 별도로 청구한다. 물건값과 한 청구서에 섞으면 사용자가
    "이 금액이 왜 이런가"를 물건 내역만으로 확인할 수 없고, 단가를 고쳐 다시
    정산할 때마다 비용까지 함께 흔들린다.
    (켜면 그날 택배·포장비를 청구서에 싣는 종전 동작.)

    반환: {'saved': n, 'dropped': [...], 'totals': {user: 청구액},
           'learned': {...}, 'fees': {user: {...}}}
    """
    import receipt_settle as _rs

    rows, dropped = to_ledger_rows(alloc_rows, settle_date)

    # 각 사용자 주문의 구입가(cost_price)에 영수증 실단가를 반영 — 수익계산이 본다
    applied = 0
    if apply_cost and alloc_rows:
        # 나눠 보낸 주문은 날짜마다 따로 정산된다. 주문 구입가는 주문 한 건의 값이라
        # 오늘 금액만 쓰면 앞 날짜 몫이 지워진다(2개 주문이 1개 값이 된다) —
        # 다른 날 정산된 금액을 더해 쓴다. 한 날에만 정산된 주문은 더할 것이 없다.
        try:
            _prior = _ds.settled_qty_map(exclude_date=str(settle_date))
        except Exception:
            _prior = {}
        _cost_rows = []
        for _r in alloc_rows:
            _pa = (_prior.get((str(_r.get('username') or ''),
                               str(_r.get('order_no') or ''))) or (0, 0))[1]
            _rr = (dict(_r, amount=int(_r.get('amount') or 0) + int(_pa or 0))
                   if _pa else _r)
            # 본인 재고(결제됨) 출고는 청구액이 0이지만 물건값이 0인 것은 아니다 —
            # 수익계산이 공짜로 보지 않게 그 재고의 구입가를 주문 구입가로 쓴다.
            if _i(_rr.get('cost_basis')) and not _i(_rr.get('amount')):
                _rr = dict(_rr, amount=_i(_rr.get('cost_basis')))
            _cost_rows.append(_rr)
        applied = _rs.apply_receipt_settlement(_cost_rows)

    # 네이버번호 ↔ 코스트코번호 매핑 학습 — 다음 정산부터 번호로 바로 붙는다
    learned = {'filled': 0, 'by_user': {}}
    if learn and alloc_rows:
        try:
            learned = _rs.learn_costco_mappings(alloc_rows) or learned
        except Exception:
            pass

    fees = {}
    if with_fees:
        fees = fees_for_users({r['username'] for r in rows}, settle_date)

    totals = _ds.save_settlement(settle_date, rows, fees_by_user=fees,
                                 created_by=created_by, replace=replace)
    stock = consume_stock_rows(rows, settle_date)
    # 재고 장부에 없던 남의 물건을 빌려 보낸 건 — 빌려준 사람에게 줄 돈을 정산 장부에
    stock['loan'] = 0
    for _r in (alloc_rows or []):
        _lo = str(_r.get('loan_owner') or '').strip()
        if not _lo or _i(_r.get('amount')) <= 0:
            continue
        try:
            from db_inventory import record_loan_sale
            if record_loan_sale(str(_r.get('username') or ''), _lo,
                                str(_r.get('costco_no') or ''),
                                str(_r.get('product_name') or ''),
                                _i(_r.get('qty')) or 1, _i(_r.get('amount')),
                                str(_r.get('order_no') or ''), str(settle_date)):
                stock['loan'] += 1
        except Exception:
            pass
    # 남의 재고를 쓴 건 — 판매자에게 청구한 그 금액을 **보유자 예치금으로 적립**한다.
    #   예전엔 재고 관리 › 정산 장부에 쌓아 두고 관리자가 따로 정산했다.
    #   적립하면 그 주문의 정산 장부 건은 '정산완료'로 닫는다(두 번 주지 않게).
    stock['credited'] = credit_stock_owners(alloc_rows, settle_date, created_by)
    from run_memo import clear as _rm_clear
    _rm_clear()
    return {'saved': len(rows), 'applied': applied, 'dropped': dropped,
            'totals': totals, 'learned': learned, 'fees': fees, 'stock': stock}


def credit_stock_owners(alloc_rows, settle_date, by=''):
    """남의 재고 출고·빌린 재고 행 → 보유자 예치금 적립. 반환: 적립 건수."""
    from datetime import datetime
    n = 0
    try:
        import db_deposit as _dep
        from db_inventory import _conn as _iconn
    except Exception:
        return 0
    for r in (alloc_rows or []):
        _u = str(r.get('username') or '')
        _ow = str(r.get('stock_owner') or r.get('loan_owner') or '').strip()
        _amt = _i(r.get('amount'))
        _ono = str(r.get('order_no') or '').strip()
        if not _ow or _ow == _u or _amt <= 0 or not _ono:
            continue
        _memo = (f"재고 판매 적립 · {str(r.get('product_name') or '')[:30]} "
                 f"{_i(r.get('qty')) or 1}개 · 판매자 {_u} · {settle_date} 정산")
        try:
            if _dep.stock_credit(_ow, _amt, _ono, _memo, settle_date=str(settle_date), by=by):
                n += 1
                _c = _iconn()
                _c.execute("UPDATE inventory_moves SET settle_status='SETTLED', settled_at=? "
                           "WHERE order_no=? AND owner=? AND is_cross=1 AND settle_status='PENDING'",
                           (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), _ono, _ow))
                _c.commit()
                _c.close()
        except Exception:
            continue
    return n


def consume_stock_rows(rows, settle_date):
    """📦 재고 출고 행(stock_owner 있음)만큼 그 보유자 재고 장부에서 뺀다.

    재고가 둘로 갈라지지 않게 하는 자리다. 예전엔 정산이 '재고에서 나갔다'고
    청구만 하고 장부(inventory_lots)는 그대로 둬서, 📊 전체 재고에는 안 줄어든
    채 남았고, 그 입고는 '한 번도 안 쓰인 입고'로 보여 초기화 때 통째로 지워졌다.

    같은 주문이 발송 때 이미 차감됐으면(consume_for_sale 멱등) 다시 빼지 않는다.
    수량 = 주문수량 × 묶음배수(발송 차감과 같은 해석).
    반환: {'consumed': 소분 수, 'already': 주문 수, 'short': [{order_no, name, owner, short}]}
    """
    out = {'consumed': 0, 'already': 0, 'short': []}
    _todo = [r for r in (rows or []) if str(r.get('stock_owner') or '').strip()
             and str(r.get('order_no') or '').strip()
             and str(r.get('product_no') or '').strip()]
    if not _todo:
        return out
    try:
        from db_inventory import consume_for_sale
        from db_dispatch_log import _pack_factor
        from db_core import get_user_db
    except Exception:
        return out
    _pk = {}
    for r in _todo:
        u, pno = str(r['username']), str(r['product_no'])
        try:
            if (u, pno) not in _pk:
                uc = get_user_db(u)
                try:
                    _pk[(u, pno)] = max(1, int(_pack_factor(uc, pno, r.get('product_name') or '') or 1))
                finally:
                    uc.close()
        except Exception:
            _pk[(u, pno)] = 1
        units = max(1, _i(r.get('qty'))) * _pk[(u, pno)]
        try:
            res = consume_for_sale(u, pno, units, str(r['order_no']),
                                   dispatched_at=str(settle_date), platform='settle',
                                   owner=str(r['stock_owner']))
        except Exception:
            continue
        if res.get('skipped'):
            out['already'] += 1
            continue
        out['consumed'] += _i(res.get('consumed'))
        if _i(res.get('shortage')):
            out['short'].append({'order_no': str(r['order_no']),
                                 'name': str(r.get('product_name') or '')[:24],
                                 'owner': str(r['stock_owner']),
                                 'short': _i(res.get('shortage'))})
    return out


# ── 미매칭 품목 → 사용자 재고 입고 ────────────────────────────
def owner_hints(settle_date):
    """{코스트코번호: username} — 그날 장보기 목록에서 그 상품을 요청한 사람.

    미매칭 잔량을 누구 재고로 넣을지의 근거다. '누가 사 달라고 했나'가
    '누가 그 물건의 주인인가'에 가장 가까운 답이다. 두 사람이 같은 상품을
    요청했으면 수량을 많이 적은 쪽을 고른다.
    """
    hints, best = {}, {}
    try:
        import receipt_settle as _rs
        bridge = _rs.build_shopping_bridge(str(settle_date)) or {}
    except Exception:
        return hints
    for uname, info in bridge.items():
        for it in (info or {}).get('items', []):
            cno = str(it.get('costco') or '')
            if not cno:
                continue
            q = _i(it.get('qty'))
            if q >= best.get(cno, -1):
                best[cno] = q
                hints[cno] = uname
    return hints


def leftovers(receipt_items, alloc_rows, settle_date, users=None):
    """구입내역 중 어느 사용자 주문에도 안 붙은 잔량 — 재고로 정리할 대상.

    영수증에 있는데 주문에 못 붙은 물건은 실물이 창고에 남아 있다는 뜻이다.
    그냥 두면 다음 날 '재고가 있는 줄 모르고' 또 사고, 장부에도 안 잡힌다.
    누구 것인지(장보기 요청자)를 붙여 돌려주면 그대로 입고할 수 있다.

    반환: [{costco_no, name, unit_price, split_qty, units_left, packs_left,
            amount, owner}]
    """
    import receipt_settle as _rs

    rows = alloc_rows or []
    # 발송은 됐는데 정산에 못 붙은 건도 실물은 나갔다 — 재고에서 빼야 한다.
    # 안 빼면 있지도 않은 재고가 잡히고, 다음 날 그 재고로 다른 주문을 메꿨다고
    # 계산해 같은 물건이 두 번 쓰인다.
    extra = {}
    try:
        extra, _ = _rs.dispatch_consumption(
            str(settle_date), [x.get('상품번호') for x in (receipt_items or [])],
            matched_keys={(r.get('username'), r.get('order_no')) for r in rows},
            users=users)
    except Exception:
        extra = {}

    hints = owner_hints(settle_date)
    out = []
    for l in (_rs.compute_leftovers(receipt_items, rows, extra_used=extra) or []):
        sq = max(1, _i(l.get('split_qty')) or 1)
        out.append({
            'costco_no': l['costco_no'], 'name': l['name'],
            'unit_price': _i(l['unit_price']), 'split_qty': sq,
            'qty_receipt': _i(l['qty_receipt']),
            'units_used': _i(l['units_used']), 'units_left': _i(l['units_left']),
            'packs_left': round(l['packs_left'], 2),
            'amount': int(_i(l['unit_price']) / sq * _i(l['units_left'])),
            'owner': hints.get(l['costco_no'], ''),
        })
    return out


def _memo_tag(settle_date):
    return f"영수증정산 {settle_date}"


STOCKIN_PREFIX = 'STOCKIN-'


def bill_stock_intake(settle_date, owner, items, created_by=''):
    """재고 입고분의 구매비용을 보유자에게 청구한다 — '관리자가 대신 사서 재고로 보관'.

    items: [{lot_id, costco_no, name, units, split_qty, pack_price}]
    주문번호는 STOCKIN-<lot_id> — 입고 1건에 청구 1줄. 같은 lot을 다시 청구해도
    덮어쓸 뿐 두 번 청구되지 않는다. 입고를 취소하면 unbill_stock_intake가 지운다.

    product_no는 비워 둔다. 채우면 공용 재고 계산(build_stock_pool)이 이 행을
    '배치된 수량'으로 한 번, 재고 lot으로 또 한 번 빼서 남은 영수증 품목이 사라진다.
    상품번호는 메모에 남긴다.
    반환: 청구액 합계
    """
    try:
        import receipt_settle as _rs
        _proxy = 1 if _rs.is_self_purchase(owner) else 0
    except Exception:
        _proxy = 0
    rows, tot = [], 0
    for it in (items or []):
        lid = _i(it.get('lot_id'))
        units = _i(it.get('units'))
        sq = max(1, _i(it.get('split_qty')) or 1)
        pp = _i(it.get('pack_price'))
        if not (lid and units > 0 and pp > 0):
            continue
        amt = int(pp / sq * units)
        tot += amt
        _packs = units / sq
        rows.append({
            'order_no': f"{STOCKIN_PREFIX}{lid}", 'product_no': '',
            'product_name': f"재고 구입 · {str(it.get('name') or '')[:40]}",
            'qty': max(1, int(round(_packs))) if _packs >= 1 else 1,
            'split_qty': sq, 'pack': 1, 'unit_price': pp, 'amount': amt,
            'source': 'direct', 'receipt_date': str(settle_date),
            'memo': f"재고 입고 청구 · {it.get('costco_no') or ''} · {units}개"
                    + (f"({_packs:g}팩)" if sq > 1 else ''),
            'proxy': _proxy})
    if rows:
        _ds.merge_items(str(settle_date), str(owner), rows, created_by=created_by)
    return tot


def unbill_stock_intake(lot_ids):
    """입고를 취소했으면 그 입고 청구(STOCKIN-<lot_id>)도 지운다. 입금완료분은 남긴다.

    반환: {'removed': n, 'kept_paid': [lot_id...]}
    """
    ids = [int(x) for x in (lot_ids or []) if str(x).strip()]
    out = {'removed': 0, 'kept_paid': []}
    if not ids:
        return out
    conn = _ds._conn()
    _ds.ensure(conn)
    touched = set()
    try:
        for lid in ids:
            ono = f"{STOCKIN_PREFIX}{lid}"
            for r in conn.execute("SELECT settle_date, username FROM settle_item "
                                  "WHERE order_no=?", (ono,)).fetchall():
                sd, u = str(r[0]), str(r[1])
                inv = conn.execute("SELECT status FROM settle_invoice WHERE settle_date=? "
                                   "AND username=?", (sd, u)).fetchone()
                if inv and str(inv[0]) == 'paid':
                    out['kept_paid'].append(lid)
                    continue
                conn.execute("DELETE FROM settle_item WHERE order_no=? AND settle_date=? "
                             "AND username=?", (ono, sd, u))
                out['removed'] += 1
                touched.add((sd, u))
        conn.commit()
    finally:
        conn.close()
    for sd, u in touched:
        _ds.recompute_invoice(sd, u)
    return out


def receive_leftovers(settle_date, picks, bill=False, created_by=''):
    """잔량을 각 사용자 재고로 입고한다.

    bill=True: 입고와 함께 구매비용을 보유자에게 청구하고 재고를 '결제됨'으로 둔다
      (관리자가 대신 사서 그 사람 재고로 보관 — 나중에 본인 주문으로 나갈 때는
      다시 청구하지 않는다). False면 예전처럼 청구 없이 넣고 나갈 때 청구한다.

    picks: [{costco_no, name, unit_price, split_qty, units_left, owner}]

    자동 입고하지 않는 이유는 그대로다 — 영수증 수량 인식이 틀리거나 매칭이 덜
    되면 있지도 않은 재고가 생기고, 그 유령 재고가 나중에 남의 판매에서 차감되며
    교차정산 웃돈까지 발생시킨다. 되돌리기 어려운 방향이라 사람이 확인한 것만 넣는다.

    같은 날짜·같은 상품이라고 막지는 않는다. 한 상품을 여러 사람에게 나눠
    넣는 것이 정상 흐름이기 때문이다. 이중 입고는 부르는 쪽이 '남은 수량'에서
    이미 입고한 만큼을 뺀 목록을 주는 것으로 막는다(_build_assign_rows).

    같은 날·같은 사람·같은 상품에 **이미 영수증 배정으로 넣은 lot**이 있으면
    그 사실을 돌려준다(dupe). 막지는 않는다 — 나눠 배정하다 더 넣는 일이 정상
    흐름이기 때문이다. 다만 화면이 말해 주지 않으면, 버튼을 두 번 누른 것과
    더 넣은 것을 구분할 수 없어 재고가 조용히 불어난다.

    반환: {'ok': n, 'skipped': n, 'failed': [msg...], 'dupe': [{name, owner, have}]}
    """
    from db_inventory import add_lot_units

    # 그날 영수증 배정으로 이미 들어간 양 — (상품번호, 보유자): units
    _have = {}
    try:
        from db_inventory import find_lots
        for _l in (find_lots(date_from=str(settle_date),
                             date_to=str(settle_date)) or []):
            if not str(_l.get('memo') or '').startswith('영수증정산'):
                continue
            _k = (str(_l.get('product_no') or ''), str(_l.get('owner') or ''))
            _have[_k] = _have.get(_k, 0) + _i(_l.get('qty_in'))
    except Exception:
        _have = {}

    res = {'ok': 0, 'skipped': 0, 'failed': [], 'dupe': [], 'billed': 0}
    _to_bill = {}
    for p in (picks or []):
        cno = str(p.get('costco_no') or '')
        owner = str(p.get('owner') or '')
        units = _i(p.get('units_left'))
        if not (cno and owner and units > 0):
            res['skipped'] += 1
            continue
        _k = (cno, owner)
        if _have.get(_k):
            res['dupe'].append({'name': str(p.get('name') or '')[:24],
                                'owner': owner, 'have': int(_have[_k])})
        try:
            lid = add_lot_units(
                product_no=cno, product_name=str(p.get('name') or ''), owner=owner,
                pack_unit_cost=_i(p.get('unit_price')), qty_units=units,
                split_qty=max(1, _i(p.get('split_qty')) or 1),
                received_at=str(settle_date),
                memo=_memo_tag(settle_date) + " · 미배정 잔량"
                     + (" · 입고 청구" if bill else ""),
                # 정가(할인 전) — 얼마짜리를 얼마에 샀는지 재고에도 남긴다
                pack_list_price=_i(p.get('list_price')),
                paid=1 if bill else 0)
            if lid:
                res['ok'] += 1
                if bill:
                    _to_bill.setdefault(owner, []).append({
                        'lot_id': lid, 'costco_no': cno, 'name': p.get('name'),
                        'units': units, 'split_qty': p.get('split_qty'),
                        'pack_price': _i(p.get('unit_price'))})
            else:
                res['failed'].append(f"{str(p.get('name'))[:20]} (수량 0)")
        except Exception as e:
            res['failed'].append(f"{str(p.get('name'))[:20]} — {str(e)[:60]}")
    for _ow, _its in _to_bill.items():
        try:
            res['billed'] += bill_stock_intake(settle_date, _ow, _its,
                                               created_by=created_by)
        except Exception as e:
            res['failed'].append(f"{_ow} 입고 청구 실패 — {str(e)[:60]}")
    return res
