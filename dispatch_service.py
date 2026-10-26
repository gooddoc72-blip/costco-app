# -*- coding: utf-8 -*-
"""발송 파일 올리기 — 화면과 떨어진 처리 함수 (Next.js /m/receipt-settle 이 쓴다).

Streamlit dispatch_upload.render_panel이 하던 일을 같은 함수(dispatch_upload의
guess_columns·order_columns·file_dates·classify_rows·expand_combined·save_dispatch·
ship_plan·ship_for_user)를 같은 순서로 불러 한다. 새로 계산하지 않는다.

파일을 읽어 분류한 결과는 **미리보기 번호(token)**로 서버 메모리에 잠깐 둔다 — 저장·발송처리
때 파일을 다시 올리지 않아도 되게. API는 작업자 1개(uvicorn --workers 1)라 메모리로 충분하고,
서버가 다시 시작되면 사라지므로 화면은 '다시 올리세요'로 안내한다.
"""
import io
import time
import uuid

import dispatch_upload as du

_PREV = {}              # token → {'at', 'by_user', 'unknown', 'dates', ...}
_TTL = 3 * 3600
_STD = ('order_no', 'tracking_no', 'recipient', 'product_name', 'qty', 'courier',
        'dispatched_at', 'sender_code', 'sender_name')


def _gc():
    now = time.time()
    for k in [k for k, v in _PREV.items() if now - v['at'] > _TTL]:
        _PREV.pop(k, None)


def _read(name, data):
    """엑셀·CSV → DataFrame(문자열). CSV는 UTF-8이 아니면 CP949(택배사 기본)로 다시 읽는다."""
    import pandas as pd
    low = name.lower()
    if low.endswith('.csv'):
        try:
            return pd.read_csv(io.BytesIO(data), dtype=str)
        except UnicodeDecodeError:
            return pd.read_csv(io.BytesIO(data), dtype=str, encoding='cp949')
    if low.endswith(('.xlsx', '.xls')):
        return pd.read_excel(io.BytesIO(data), dtype=str)
    raise ValueError('xlsx·xls·csv만 읽습니다')


def preview(files):
    """files: [(이름, bytes)] → 분류 결과(미리보기 번호 포함). 저장하지 않는다."""
    import pandas as pd
    _gc()
    frames, bad, notes = [], [], []
    for name, data in files or []:
        try:
            d = _read(name, data)
        except Exception as e:
            bad.append({'file': name, 'error': str(e)[:80]})
            continue
        if d is None or d.empty:
            bad.append({'file': name, 'error': '빈 파일'})
            continue
        frames.append((name, d.fillna('')))
    if not frames:
        return {'token': '', 'files': [], 'bad': bad, 'error': '읽은 파일이 없습니다'}
    same = len({tuple(str(c) for c in d.columns) for _, d in frames}) == 1
    if same:
        df = pd.concat([d for _, d in frames], ignore_index=True)
    else:
        norm = []
        for n, d in frames:
            g = du.guess_columns(list(d.columns))
            if not g.get('order_no'):
                bad.append({'file': n, 'error': '주문번호 열을 찾지 못해 제외'})
                continue
            norm.append(pd.DataFrame({k: (d[g[k]] if g.get(k) in d.columns else '') for k in _STD}))
        if not norm:
            return {'token': '', 'files': [], 'bad': bad, 'error': '주문번호 열이 있는 파일이 없습니다'}
        df = pd.concat(norm, ignore_index=True).fillna('')
        notes.append('파일마다 열이 달라 각각 자동 인식했습니다')
    cols = list(df.columns)
    guess = du.guess_columns(cols)
    idx, dup = du.build_order_owner_index()
    recs = df.to_dict('records')
    # 주문번호 열은 이름이 아니라 값으로 고른다(택배사가 자기 관리번호에 '주문번호'를 붙인다)
    ranked = du.order_columns(recs, cols, idx)
    alt = []
    if ranked:
        best, hit = ranked[0]
        alt = [c for c, _ in ranked[1:]]
        if guess.get('order_no') != best:
            notes.append(f"주문번호 열을 '{best}'로 잡았습니다 — 실제 주문과 {hit}건 일치")
        guess['order_no'] = best
    elif idx:
        notes.append('⚠️ 어느 열도 우리 주문번호와 맞지 않습니다 — 상품주문번호 열인지, 주문 수집이 됐는지 확인하세요')
    if not guess.get('order_no'):
        return {'token': '', 'files': [{'file': n, 'rows': len(d)} for n, d in frames], 'bad': bad,
                'notes': notes, 'error': '주문번호 열을 찾지 못했습니다 — 기존 화면의 ⚙️ 열 매핑에서 지정하세요'}
    cm = {k: guess.get(k) for k in _STD}
    cm['order_no_alt'] = alt
    dates = du.file_dates(recs, cols)
    by_user, unknown = du.classify_rows(recs, cm, idx)
    smap = du.load_sender_map() if cm.get('sender_code') else {}
    external = []
    if cm.get('sender_code'):
        if du.learn_sender_map(by_user, smap):
            try:
                du.save_sender_map(smap)
            except Exception:
                pass
        external, unknown = du.split_external(unknown, smap)
    # 파일 맨 아래 합계 줄은 발송건이 아니다
    unknown = [u for u in unknown if (str(u.get('recipient') or '').strip()
                                      or str(u.get('product_name') or '').strip()
                                      or du._trk_key(u.get('tracking_no')))]
    # 미분류 중 이미 누군가의 발송 기록에 있는 송장 → 그 사용자 몫으로 분류 결과에 넣는다
    saved_trk = du.saved_trackings([u.get('tracking_no') for u in unknown])
    n_saved = 0
    if saved_trk:
        keep = []
        for u in unknown:
            k = du._trk_key(u.get('tracking_no'))
            if k in saved_trk:
                su, so = saved_trk[k]
                by_user.setdefault(su, []).append(dict(u, order_no=so or u.get('order_no'), _saved=True))
                n_saved += 1
            else:
                keep.append(u)
        unknown = keep
    by_user, comb = du.expand_combined(by_user)
    for i, u in enumerate(unknown):
        u['_idx'] = i
        m = smap.get(str(u.get('sender_code') or '')) or {}
        u['_pre'] = m.get('username', '') if m.get('kind') == 'user' else ''
    token = uuid.uuid4().hex[:12]
    default_date = max(dates.items(), key=lambda kv: kv[1])[0] if dates else ''
    _PREV[token] = {'at': time.time(), 'by_user': by_user, 'unknown': unknown, 'dates': dates}
    return {
        'token': token, 'files': [{'file': n, 'rows': len(d)} for n, d in frames], 'bad': bad, 'notes': notes,
        'columns': {k: cm.get(k) or '' for k in ('order_no', 'tracking_no', 'recipient', 'courier', 'sender_code')},
        'dates': [{'date': str(k), 'count': v} for k, v in sorted(dates.items())], 'default_date': str(default_date),
        'dup_owner': len(dup or []), 'external': len(external), 'already_saved': n_saved,
        'combined': [{'username': x['username'], 'with': x['_combined_with'], 'order_no': x['order_no'],
                      'recipient': x.get('recipient', ''), 'product_name': str(x.get('product_name') or '')[:30],
                      'tracking_no': x.get('tracking_no', '')} for x in comb],
        'unknown': [{'idx': u['_idx'], 'row': u.get('_row'), 'order_no': u.get('order_no', ''),
                     'recipient': u.get('recipient', ''), 'product_name': str(u.get('product_name') or '')[:34],
                     'qty': int(u.get('qty') or 1), 'tracking_no': u.get('tracking_no', ''),
                     'dispatched_at': u.get('dispatched_at', ''), 'sender': u.get('sender_name', ''),
                     'why': u.get('_why', ''), 'pre': u['_pre']} for u in unknown[:300]],
    }


def _get(token):
    p = _PREV.get(str(token or ''))
    if not p:
        raise ValueError('미리보기가 없습니다(서버가 다시 시작됐거나 3시간이 지났습니다) — 파일을 다시 올리세요.')
    return p


def summary(token, the_date):
    """사용자별 — 건수·이 날짜에 이미 있음·다른 날짜에 기록됨·새로 저장될 건 + 스토어 발송 대상."""
    p = _get(token)
    rows, other = [], {}
    for u, rs in sorted(p['by_user'].items(), key=lambda kv: -len(kv[1])):
        ex = du.existing_dispatch(u, [r['order_no'] for r in rs])
        same = sum(1 for d in ex.values() if str(d) == str(the_date))
        for o, d in ex.items():
            if str(d) != str(the_date):
                other[str(d)] = other.get(str(d), 0) + 1
        rows.append({'username': u, 'count': len(rs), 'same': same, 'other': len(ex) - same,
                     'new': len(rs) - same})
    plan = du.ship_plan(p['by_user'])
    ship = []
    for u, x in plan.items():
        st = []
        if x['naver'] and not x['keys'][0]:
            st.append('네이버 키 없음')
        if x['coupang'] and not x['keys'][1]:
            st.append('쿠팡 키 없음')
        ship.append({'username': u, 'naver': len(x['naver']), 'coupang': len(x['coupang']), 'done': x['done'],
                     'no_trk': x['no_trk'], 'naver_key': x['keys'][0], 'coupang_key': x['keys'][1],
                     'state': ' · '.join(st) or ('보낼 수 있음' if (x['naver'] or x['coupang']) else '보낼 건 없음'),
                     'sendable': len(x['naver']) * x['keys'][0] + len(x['coupang']) * x['keys'][1]})
    ship.sort(key=lambda s: -(s['naver'] + s['coupang']))
    return {'users': rows, 'other_dates': [{'date': k, 'count': v} for k, v in sorted(other.items())],
            'total': sum(r['count'] for r in rows), 'new_total': sum(r['new'] for r in rows), 'ship': ship}


def save(token, the_date, skip_existing=True, use_row_date=True, move_wrong_date=True):
    """분류된 발송건을 발송 기록에 저장. 반환: 사용자별 저장 수·건너뜀·옮김·줄별 결과."""
    p = _get(token)
    det = []
    use_row = bool(use_row_date and p['dates'])
    saved, skipped, moved = du.save_dispatch(p['by_user'], str(the_date), detail=det,
                                             skip_existing=bool(skip_existing), use_row_date=use_row,
                                             move_wrong_date=bool(move_wrong_date))
    return {'saved': saved, 'total': sum(saved.values()), 'skipped': skipped, 'moved': moved, 'detail': det}


def save_unknown(token, the_date, assign, skip_existing=True, use_row_date=True, move_wrong_date=True):
    """미분류 건을 사람이 고른 사용자로 저장. assign: [{idx, username, qty?}]"""
    p = _get(token)
    pick = {}
    for a in assign or []:
        i, u = a.get('idx'), str(a.get('username') or '')
        if not u or not isinstance(i, int) or not 0 <= i < len(p['unknown']):
            continue
        src = dict(p['unknown'][i])
        src['qty'] = max(1, int(a.get('qty') or src.get('qty') or 1))
        if not str(src.get('order_no') or '').strip():
            # 주문번호가 없으면 송장번호로 대체 키 — 최소한 발송건수·택배비에는 잡히게
            src['order_no'] = 'NO-%s-%s' % (the_date, src.get('tracking_no') or src.get('_row'))
        pick.setdefault(u, []).append(src)
    if not pick:
        raise ValueError('사용자를 지정한 건이 없습니다.')
    det = []
    saved, skipped, moved = du.save_dispatch(pick, str(the_date), detail=det, skip_existing=bool(skip_existing),
                                             use_row_date=bool(use_row_date and p['dates']),
                                             move_wrong_date=bool(move_wrong_date))
    done = {a['idx'] for a in assign or [] if a.get('username')}
    p['unknown'] = [u for u in p['unknown'] if u.get('_idx') not in done]
    return {'saved': saved, 'total': sum(saved.values()), 'skipped': skipped, 'detail': det}


def manual(the_date, rows):
    """파일 없이 손으로 넣는 발송건. rows: [{username, order_no?, tracking_no?, recipient, product_name, qty, courier}]
    주문번호도 송장번호도 없으면 넣을 수 없다."""
    pick, bad = {}, 0
    for r in rows or []:
        ono, trk = str(r.get('order_no') or '').strip(), str(r.get('tracking_no') or '').strip()
        nm, rc, u = str(r.get('product_name') or '').strip(), str(r.get('recipient') or '').strip(), str(r.get('username') or '')
        if not (ono or trk or nm or rc):
            continue
        if not u or not (ono or trk):
            bad += 1
            continue
        pick.setdefault(u, []).append({'order_no': ono or ('NO-%s-%s' % (the_date, trk)), 'recipient': rc,
                                       'product_name': nm, 'qty': max(1, int(r.get('qty') or 1)),
                                       'tracking_no': trk, 'courier': str(r.get('courier') or '').strip()})
    if not pick:
        raise ValueError('넣을 건이 없습니다 — 사용자와, 주문번호·송장번호 중 하나는 있어야 합니다.')
    det = []
    saved, skipped, _ = du.save_dispatch(pick, str(the_date), detail=det, skip_existing=True)
    return {'saved': saved, 'total': sum(saved.values()), 'skipped': skipped, 'bad': bad, 'detail': det}


COURIERS = list(du._CP_COURIER)


def ship(token, users, courier, the_date, log=None):
    """고른 사용자만 그 사람 스토어(네이버·쿠팡)에 대신 발송처리 — 고객에게 발송 알림이 간다."""
    p = _get(token)
    if courier not in du._CP_COURIER:
        raise ValueError('택배사를 고르세요.')
    plan = du.ship_plan(p['by_user'])
    out = {}
    for i, u in enumerate(users or [], 1):
        if u not in plan:
            continue
        if log:
            log(f'{i}/{len(users)} {u} 발송처리 중')
        r = du.ship_for_user(u, plan[u], courier, str(the_date))
        out[u] = {'naver_ok': r['naver'][0], 'naver_fail': r['naver'][1],
                  'coupang_ok': r['coupang'][0], 'coupang_fail': r['coupang'][1],
                  'errors': r['errors'], 'fails': r['fails'][:50]}
    return out
