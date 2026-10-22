"""매일 자동 정산 — 택배사 발송 파일 → 발송 기록, 당일 영수증 → 저장, 둘을 매칭해 초안 저장.

관리자 화면(영수증 정산)의 2·3·4단계를 사람 없이 돈다. PC의 예약 작업이
CJ 파일과 영수증을 /api/admin/auto/* 로 보낸다(api/auto_settle.py).

사람이 하던 확인을 대신하는 규칙 — 확신이 없으면 저장하지 않고 '검토 필요'로 돌려준다:
  · 영수증은 그날 이미 저장된 것이 있으면 손대지 않는다(저장은 날짜 통째 교체라
    사람이 고친 표를 덮어쓴다).
  · 판독 자가검증 실패, 수량·금액 이상치, 파일 이름 날짜와 다른 영수증 날짜면 저장 안 함.
    (9/8 KS그릭요거트 수량 64 오독이 그대로 재고·매장가가 된 일이 있었다)
  · 매칭은 초안(settle_draft)까지만. 정산 요청(청구·원가 덮어쓰기)은 사람이 누른다.
  · 이미 정산서가 있는 날짜는 매칭하지 않는다.
"""
import io

import dispatch_upload as _du
import receipt_settle as _rs
import db_settle as _ds
import settle_core as _sc
from db import get_receipt_items_by_date

#: 같은 이름의 PDF와 사진이 함께 있으면 같은 영수증으로 본다 — PDF를 쓴다(글자가 정확하다)
_PDF, _IMG = ('.pdf',), ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif')


def upload_dispatch(records, headers):
    """택배사 파일 행 → 사용자별 dispatch_log. 화면 '2. 누락된 발송건 올리기'의 기본값과 같다
    (집화일자를 발송일로 · 잘못된 날짜 옮기기 · 같은 날짜 중복 건너뛰기)."""
    idx, _dup = _du.build_order_owner_index()
    cm = _du.guess_columns(headers)
    ranked = _du.order_columns(records, headers, idx)
    if not ranked:
        return {'ok': False, 'error': '파일의 어느 열도 코코비즈 주문번호와 맞지 않습니다'}
    cm['order_no'] = ranked[0][0]
    cm['order_no_alt'] = [c for c, _ in ranked[1:]]
    by_user, unknown = _du.classify_rows(records, cm, idx)
    external = []
    if cm.get('sender_code'):
        smap = _du.load_sender_map()
        if _du.learn_sender_map(by_user, smap):
            _du.save_sender_map(smap)
        external, unknown = _du.split_external(unknown, smap)
    dates = _du.file_dates(records, headers)
    if not dates:
        return {'ok': False, 'error': '파일에서 발송일(접수일자·집화일자) 열을 찾지 못했습니다'}
    saved, skipped, moved = _du.save_dispatch(
        by_user, max(dates), skip_existing=True, use_row_date=True, move_wrong_date=True)
    return {'ok': True, 'dates': dates, 'saved': saved, 'skipped': skipped, 'moved': moved,
            'matched': sum(len(v) for v in by_user.values()), 'external': len(external),
            'unknown': [{k: u.get(k, '') for k in ('order_no', 'recipient', 'product_name',
                                                    'sender_name', 'dispatched_at', '_why')}
                        for u in unknown[:50]],
            'unknown_n': len(unknown)}


def _as_file(name, data):
    f = io.BytesIO(data)
    f.name = name
    f.size = len(data)
    return f


def upload_receipt(day, files, username):
    """그날 영수증 파일들 → receipt_items. files: [(파일이름, bytes)].

    반환 status: saved(저장) · exists(이미 있어 그대로 둠) · review(검토 필요, 저장 안 함) · none
    """
    day = str(day)[:10]
    have = get_receipt_items_by_date(username, day)
    if have:
        return {'status': 'exists', 'items': len(have)}
    stems = {}
    for name, data in (files or []):
        _st, _, _ext = name.rpartition('.')
        _ext = '.' + _ext.lower()
        if _ext in _PDF + _IMG:
            stems.setdefault(_st, []).append((_ext in _PDF, name, data))
    pdfs, photos, used = [], [], []
    for _st, cands in sorted(stems.items()):
        _pdf, name, data = sorted(cands, key=lambda c: not c[0])[0]   # PDF 먼저
        (pdfs if _pdf else photos).append(_as_file(name, data))
        used.append(name)
    if not used:
        return {'status': 'none'}
    items, fails = _rs.parse_receipt_uploads(pdfs, photos, None)
    other = sorted({it['receipt_date'] for it in items
                    if it.get('receipt_date') and it['receipt_date'] != day})
    for it in items:
        it['receipt_date'] = day
    # 화면 표와 같은 기준 — 상품번호·실단가가 없는 줄은 매칭에 못 쓴다
    n_read = len(items)
    items = [it for it in items if _rs._s(it.get('상품번호')) and _rs._int(it.get('단가')) > 0]
    odd = _rs.receipt_outliers(items)
    why = [f"{n}: {e}" for n, e in fails]
    if other:
        why.append(f"영수증에 찍힌 날짜({', '.join(other)})가 파일 날짜({day})와 다름")
    why += [f"{o['상품명']} {o['이유']}" for o in odd]
    out = {'files': used, 'items': len(items), 'dropped': n_read - len(items),
           'qty': sum(_rs._int(it.get('수량'), 1) for it in items),
           'amount': sum(_rs._int(it.get('단가')) * _rs._int(it.get('수량'), 1) for it in items)}
    if not items:
        return dict(out, status='review', why=why or ['품목을 하나도 읽지 못했습니다'])
    if why:
        return dict(out, status='review', why=why)
    s, u, rm, pn, detail = _rs.persist_receipt(username, items)
    if detail['error']:
        return dict(out, status='review', why=['저장 실패: ' + detail['error']])
    return dict(out, status='saved', new=s, updated=u, removed=rm, priced=pn,
                price_skip=len(detail['skip']))


def match_day(day, username):
    """그날 발송건 × 그날 영수증 — 화면 '4. 정산 매칭'(발송건 기준 + AI)과 같은 순서로
    배치하고 초안으로 저장한다. 사람이 손댄 초안 행(_edited)은 그대로 살린다."""
    day = str(day)[:10]
    if any(str(r.get('settle_date')) == day for r in (_ds.settled_dates(limit=400) or [])):
        return {'status': 'settled'}
    receipt_items = get_receipt_items_by_date(username, day)
    if not receipt_items:
        return {'status': 'no_receipt'}
    ship_n = sum(int((_sc.daily_fees(u, day) or {}).get('ship_count') or 0)
                 for u in _rs.settle_users(day))
    if not ship_n:
        return {'status': 'no_dispatch'}

    import db_online_purchase as _op
    pool = _rs.build_stock_pool(day, exclude_dates=[day])
    settled = _ds.settled_order_keys(exclude_date=day) | (_op.keys_all() or set())
    alloc = _rs.allocate_dispatched_to_receipt(
        receipt_items, day, stock_pool=pool, exclude_orders=settled, defer_stock=True,
        settled_qty=_ds.settled_qty_map(exclude_date=day))
    # 이전 날짜에 발송됐는데 아직 정산 안 된 건 — 화면과 같이 오늘 미매칭에 올린다
    have = {(r.get('username'), r.get('order_no'))
            for r in (alloc.get('rows') or []) + (alloc.get('unmatched_orders') or [])}
    carried = _rs.carryover_unsettled(day, days=14, exclude=have)
    alloc['unmatched_orders'] = list(alloc.get('unmatched_orders') or []) + carried
    draft = _ds.get_draft(day) or []
    edited = {(r.get('username'), r.get('order_no')): r for r in draft if r.get('_edited')}
    for r in alloc['rows']:
        k = (r.get('username'), r.get('order_no'))
        if k in edited:
            r.update(edited[k])
    have = {(r.get('username'), r.get('order_no')) for r in alloc['rows']}
    _rs.merge_matches(alloc, [r for r in draft
                              if (r.get('username'), r.get('order_no')) not in have], [])
    have = {(r.get('username'), r.get('order_no')) for r in alloc['rows']}
    _rs.merge_matches(alloc, _rs.build_online_rows(
        [o for o in (_op.get_by_date(day) or [])
         if (o.get('username'), o.get('order_no')) not in have], day), [])
    ai_n, ai_err = 0, ''
    if alloc.get('unmatched_orders') and alloc.get('unmatched_receipt'):
        import ai_service as _ais
        ak, gk = _ais.get_ai_keys()
        if ak or gk:
            picks, ai_err = _rs.ai_match_receipt_orders(
                alloc['unmatched_receipt'], alloc['unmatched_orders'],
                anthropic_key=ak, gemini_key=gk)
            if picks:
                new = _rs.build_manual_rows([
                    {'order': alloc['unmatched_orders'][x['order_index']],
                     'costco_no': x['costco_no'], 'unit_price': x['unit_price'], 'via': 'ai'}
                    for x in picks])
                ai_n = len(_rs.merge_matches(alloc, new, [x['order_index'] for x in picks]))
    carry = _rs.apply_stock_carry(alloc, pool) if pool else 0
    rows = alloc['rows']
    n = _ds.save_draft(day, rows, created_by=f'{username}(자동)')
    return {'status': 'saved', 'rows': n, 'ship_count': ship_n, 'ai': ai_n,
            'ai_error': ai_err or '', 'stock_carry': carry or 0, 'carried': len(carried),
            'amount': sum(_rs._int(r.get('amount')) for r in rows),
            'users': {u: s['amount'] for u, s in (alloc.get('user_summary') or {}).items()},
            'unmatched_orders': len(alloc.get('unmatched_orders') or []),
            'unmatched_receipt': len(alloc.get('unmatched_receipt') or [])}
