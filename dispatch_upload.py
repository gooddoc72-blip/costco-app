"""발송 파일 업로드 — 관리자가 전체 발송내역 파일 하나를 올리면
주문번호로 각 사용자에게 분류해 dispatch_log에 기록한다.

왜 필요한가:
  청구는 '사용자가 송장 등록해 발송처리한 것' 기준인데, 그걸 안 하는 계정이 있으면
  청구가 통째로 0원이 된다. 실측(8/31~9/6): clglobal0919는 주문 205건에 발송 0건이라
  발송 기준으로는 한 푼도 청구되지 않았다.
  관리자가 발송 파일 하나를 올려 분류하면 그 의존이 사라진다.

분류 기준은 주문번호다. 전 사용자 DB의 주문번호 4,708개 중 두 사용자에 걸친 것이
0개라 애매함이 없다(송장번호는 'nan' 오염값 하나가 겹쳤다).

기록은 log_dispatch_success를 그대로 쓴다 — dispatch_log가 채워지면 그 뒤의
발송 기준 정산·재고 차감이 손대지 않고 그대로 동작한다.
UNIQUE(order_no, dispatched_at)라 같은 파일을 두 번 올려도 중복되지 않는다.
"""
import glob
import os
import re
import sqlite3

from db_core import DATA_DIR

#: 업로드 파일에서 각 항목을 찾을 때 볼 헤더 후보 (앞에 있을수록 우선)
COLUMN_HINTS = {
    'order_no':    ('상품주문번호', '주문번호', '주문 번호', 'order_no', 'orderid', 'order id'),
    'tracking_no': ('송장번호', '운송장번호', '운송장', 'tracking', 'invoice_no'),
    'recipient':   ('수취인명', '수취인', '받는분', '수령인', 'recipient'),
    'product_name': ('상품명', '제품명', '품명', 'product', 'item'),
    'qty':         ('수량', 'qty', 'quantity'),
    # '배송사'를 후보에서 뺐다 — CJ 파일의 '배송사업담당'(담당 조직)에 부분일치해
    # 택배사 칸에 사람 조직명이 들어갔다. 택배사 열이 없는 파일이 대부분이고,
    # 틀린 값이 들어가느니 비는 편이 낫다.
    'courier':     ('택배사', 'courier', '배송사명'),
    # 택배사 파일은 '접수일자'(집화일)를 쓴다. 이 열을 못 읽으면 발송일이 오늘로
    # 잡혀 그날 택배비가 통째로 어긋난다.
    'dispatched_at': ('발송일', '출고일', '발송처리일', '접수일자', '집화일자', '접수일', '집화일', '배송일',
                      'dispatched_at'),
    # 누가 보낸 택배인가 — CJ 기업고객 파일의 '발송고객코드'(B301210319)·'송화인'.
    # 주문번호가 없거나 코코비즈 밖 업체 건을 가르는 데 쓴다(sender map).
    'sender_code': ('발송고객코드', '고객코드', 'sender_code'),
    'sender_name': ('송하인', '송화인', '보내는분', '발송고객명', 'sender_name'),
}

#: 발송고객코드 → 누구인가. 한 번 정하면 기억한다(app_settings에 JSON).
#   {code: {'kind': 'user'|'external'|'shared', 'username': .., 'name': ..}}
#   user     = 그 코코비즈 사용자의 택배(주문번호로 맞은 행에서 자동으로 배운다)
#   external = 코코비즈 사용자가 아닌 업체 — 발송 기록·영수증 매칭에서 뺀다
#   shared   = 여러 사용자가 같은 코드를 쓴다('코코마트') — 주문번호로만 가른다
SENDER_MAP_KEY = 'dispatch_sender_map'

#: 쿠팡 주문번호 앞부분 → 전체 번호들. 코코비즈에는 '주문번호-옵션번호'로 저장되는데
#   택배사 파일에는 앞부분만 있다. build_order_owner_index가 채운다.
_BASE_FULL = {}

#: 날짜로 읽을 값의 형태 — '2026-09-01', '2026/09/01', '2026-09-01 14:00:29'
_DATE_RE = re.compile(r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})')


def _norm_no(v):
    """주문번호 정규화 — 엑셀이 숫자로 읽어 '1.23e+13'이 되는 것까지 되돌린다."""
    s = str(v if v is not None else '').strip()
    if not s or s.lower() in ('nan', 'none', 'nat'):
        return ''
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]
    if 'e+' in s.lower():                 # 지수 표기로 뭉개진 긴 번호 복원
        try:
            s = '%.0f' % float(s)
        except (TypeError, ValueError):
            pass
    return s


def build_order_owner_index():
    """주문번호 → 사용자. daily_orders와 order_history를 모두 훑는다.

    daily_orders에만 있는 주문이 많아(clglobal0919 980건) 한쪽만 보면 놓친다.
    반환: (index, dup) — dup은 두 사용자에 걸린 주문번호(정상이면 비어 있다)
    """
    idx, seen = {}, {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
        u = os.path.basename(path)[:-3]
        if u == 'auth' or '.bak' in u or '.backup' in u:
            continue
        try:
            conn = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
        except Exception:
            continue
        for tbl in ('daily_orders', 'order_history'):
            try:
                rows = conn.execute(
                    "SELECT order_no FROM %s WHERE COALESCE(order_no,'') <> ''" % tbl)
            except Exception:
                continue
            for (o,) in rows:
                o = _norm_no(o)
                if not o:
                    continue
                seen.setdefault(o, set()).add(u)
                idx[o] = u
        conn.close()
    # 쿠팡 '주문번호-옵션번호' — 택배사 파일에는 앞부분만 있다. 앞부분으로도 찾게 한다.
    _BASE_FULL.clear()
    for o, us in list(seen.items()):
        if '-' not in o or len(us) != 1:
            continue
        b = o.split('-')[0]
        if not b or b in seen:
            continue
        _BASE_FULL.setdefault(b, set()).add(o)
        idx.setdefault(b, next(iter(us)))
    dup = {o: sorted(us) for o, us in seen.items() if len(us) > 1}
    return idx, dup


def load_sender_map():
    import json
    try:
        from db_auth import get_global_setting
        return json.loads(get_global_setting(SENDER_MAP_KEY, '') or '{}') or {}
    except Exception:
        return {}


def save_sender_map(m):
    import json
    from db_auth import set_global_setting
    set_global_setting(SENDER_MAP_KEY, json.dumps(m or {}, ensure_ascii=False))


def learn_sender_map(by_user, smap):
    """주문번호로 맞은 행에서 '발송고객코드 → 사용자'를 배운다. 반환: 바뀐 수.

    관리자가 정한 값(external, fixed user)은 덮지 않는다. 한 코드가 두 사용자에
    걸리면 'shared'로 남겨 주문번호로만 가르게 한다(코코마트처럼 여럿이 같은 이름).
    """
    seen = {}
    for u, rows in (by_user or {}).items():
        for r in rows:
            c = str(r.get('sender_code') or '').strip()
            if c:
                seen.setdefault(c, {}).setdefault(u, str(r.get('sender_name') or ''))
    n = 0
    for c, us in seen.items():
        cur = smap.get(c) or {}
        if cur.get('kind') == 'external' or cur.get('fixed'):
            continue
        name = next(iter(us.values()), '')
        if len(us) == 1:
            u = next(iter(us))
            if cur.get('kind') == 'user' and cur.get('username') == u:
                continue
            if cur.get('kind') in ('user', 'shared'):
                if cur.get('kind') == 'shared':
                    continue
                smap[c] = {'kind': 'shared', 'name': name}
            else:
                smap[c] = {'kind': 'user', 'username': u, 'name': name}
            n += 1
        elif cur.get('kind') != 'shared':
            smap[c] = {'kind': 'shared', 'name': name}
            n += 1
    return n


def split_external(unknown, smap):
    """미분류 행 → (외부 업체 행, 남은 미분류). 외부 업체는 기록·매칭에서 뺀다."""
    ext, rest = [], []
    for r in (unknown or []):
        c = str(r.get('sender_code') or '').strip()
        if c and (smap.get(c) or {}).get('kind') == 'external':
            ext.append(r)
        else:
            rest.append(r)
    return ext, rest


def guess_columns(headers):
    """업로드 파일 헤더 → {항목: 열이름}. 못 찾으면 그 항목은 빠진다."""
    _h = [str(h) for h in (headers or [])]
    _low = {h: str(h).strip().lower().replace(' ', '') for h in _h}
    out = {}
    for key, hints in COLUMN_HINTS.items():
        _hs = [hint.lower().replace(' ', '') for hint in hints]
        # 모든 후보의 **완전일치**를 먼저 본다. 후보별로 완전→부분을 돌리면
        # 앞 후보의 부분일치가 뒤 후보의 완전일치를 이겨 엉뚱한 열이 잡힌다.
        hit = next((h for _hi in _hs for h in _h if _low[h] == _hi), None) \
            or next((h for _hi in _hs for h in _h if _hi in _low[h]), None)
        if hit:
            out[key] = hit
    return out


def file_dates(records, headers):
    """파일에서 읽은 발송일 → {'YYYY-MM-DD': 행수}. 못 읽으면 빈 dict.

    발송일을 사람이 매번 고르게 두면 기본값(오늘)으로 저장하는 사고가 난다.
    파일이 이미 날짜를 들고 있으므로 그걸 기본값으로 올린다.
    """
    _g = guess_columns(headers)
    col = _g.get('dispatched_at')
    if not col:
        return {}
    out = {}
    for r in (records or []):
        m = _DATE_RE.search(str(r.get(col) or ''))
        if not m:
            continue
        d = '%s-%02d-%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))
        out[d] = out.get(d, 0) + 1
    return out


def order_columns(records, headers, owner_index, sample=400, min_hit=1):
    """값이 실제 주문번호와 맞는 열들을 적중순으로 돌려준다. [(열, 적중수)]

    헤더 이름으로 고르면 택배사 관리번호 열을 '주문번호'로 잡는다. 우리는 이미
    전 사용자의 주문번호를 아니까 열마다 몇 개나 들어맞는지 세는 편이 확실하다.
    스토어(네이버·쿠팡)가 섞이면 번호 열도 여럿일 수 있어 하나만 고르지 않는다.
    """
    _rows = list(records or [])[:int(sample)]
    if not _rows or not owner_index:
        return []
    out = []
    for h in (headers or []):
        hit = 0
        for r in _rows:
            v = _norm_no(r.get(h))
            if v and v in owner_index:
                hit += 1
        if hit >= int(min_hit):
            out.append((h, hit))
    out.sort(key=lambda x: -x[1])
    return out


def classify_rows(records, colmap, owner_index):
    """파일 행 → (분류된 것, 못 찾은 것).

    주문번호만으로 가른다. 이름·수취인 유사도로 억지로 붙이지 않는다 —
    영수증 매칭에서 그렇게 붙였다가 엉뚱한 사용자에게 청구될 뻔했다.
    반환: (by_user, unknown)
      by_user = {username: [{order_no, recipient, product_name, qty,
                             tracking_no, courier}, ...]}
      unknown = [{..., '_row': 원본행번호}]
    """
    _c = colmap or {}
    # 주문번호는 여러 열에 흩어질 수 있다(네이버 상품주문번호 / 쿠팡 주문번호).
    # 지정된 열을 먼저 보고, 안 맞으면 나머지 후보 열을 차례로 본다.
    _ocols = [_c.get('order_no')] + list(_c.get('order_no_alt') or [])
    _ocols = [c for c in _ocols if c]
    by_user, unknown = {}, []
    for i, rec in enumerate(records or []):
        def _g(key):
            col = _c.get(key)
            return rec.get(col) if col else None

        ono = ''
        for _oc in _ocols:
            _v = _norm_no(rec.get(_oc))
            if _v and (not owner_index or _v in owner_index):
                ono = _v
                break
        if not ono and _ocols:
            ono = _norm_no(rec.get(_ocols[0]))
        _dm = _DATE_RE.search(str(_g('dispatched_at') or ''))
        item = {
            'order_no': ono,
            'recipient': str(_g('recipient') or '').strip(),
            'product_name': str(_g('product_name') or '').strip(),
            'tracking_no': _norm_no(_g('tracking_no')),
            'courier': str(_g('courier') or '').strip(),
            # 집화일자 — 택배사가 물건을 받아 간 날. 발송일의 진짜 근거다.
            'dispatched_at': ('%s-%02d-%02d' % (_dm.group(1), int(_dm.group(2)),
                                                int(_dm.group(3))) if _dm else ''),
            'sender_code': str(_g('sender_code') or '').strip(),
            'sender_name': str(_g('sender_name') or '').strip(),
            '_row': i + 1,
        }
        try:
            item['qty'] = max(1, int(float(_g('qty') or 1)))
        except (TypeError, ValueError):
            item['qty'] = 1
        u = owner_index.get(ono) if ono else None
        if u and ono in _BASE_FULL:
            # 쿠팡 주문번호 앞부분 — 그 주문의 옵션 줄마다 같은 송장으로 기록한다
            for _full in sorted(_BASE_FULL[ono]):
                by_user.setdefault(u, []).append(dict(item, order_no=_full))
        elif u:
            by_user.setdefault(u, []).append(item)
        else:
            item['_why'] = '주문번호 없음' if not ono else '어느 사용자에도 없는 주문번호'
            unknown.append(item)
    return by_user, unknown


def existing_dispatch(username, order_nos):
    """이미 발송 기록이 있는 주문번호 → 발송일. 중복 업로드를 알리기 위한 것."""
    out = {}
    _ons = [str(o).strip() for o in (order_nos or []) if str(o).strip()]
    if not (username and _ons):
        return out
    from db import get_user_db
    try:
        conn = get_user_db(username)
    except Exception:
        return out
    CHUNK = 900                            # SQLite 변수 한도
    for i in range(0, len(_ons), CHUNK):
        part = _ons[i:i + CHUNK]
        ph = ','.join('?' * len(part))
        try:
            for o, d in conn.execute(
                    "SELECT order_no, dispatched_at FROM dispatch_log "
                    "WHERE order_no IN (%s)" % ph, part):
                out[str(o)] = str(d)
        except Exception:
            break
    conn.close()
    return out


def _move_wrong_date(username, rows, target_date):
    """같은 주문이 **다른 날짜**에 같은 송장으로 기록돼 있으면 그 행을 지운다.

    앱은 일괄발송 버튼을 누른 시각을 발송일로 쓴다. 자정을 넘겨 처리하면 다음
    날로 기록된다(실측: 9/11 발송 8건이 9/12 00:20에 처리돼 9/12로 저장됐다).
    택배사 파일의 집화일자가 진짜 발송일이므로 그 날짜로 옮긴다. 새로 만들기만
    하면 같은 택배가 두 날에 잡혀 택배비가 두 번 청구된다.

    송장번호가 같을 때만 옮긴다 — 송장이 다르면 재발송이라 둘 다 남아야 한다.
    반환: 옮긴(지운) 행 수
    """
    _t = [(str(r.get('order_no') or '').strip(), str(r.get('tracking_no') or '').strip())
          for r in (rows or [])]
    _t = [(o, t) for o, t in _t if o and t]
    if not _t:
        return 0
    from db import get_user_db
    try:
        conn = get_user_db(username)
    except Exception:
        return 0
    moved = 0
    try:
        for _o, _tn in _t:
            cur = conn.execute(
                "DELETE FROM dispatch_log WHERE order_no=? AND dispatched_at<>? "
                "  AND REPLACE(COALESCE(tracking_no,''),'-','')=REPLACE(?,'-','')",
                (_o, str(target_date), _tn))
            moved += cur.rowcount
        conn.commit()
    except Exception:
        moved = 0
    finally:
        conn.close()
    return moved


def _cell_int(v, default=0):
    """표(data_editor) 칸 값 → int. 칸을 비우면 None이 아니라 NaN이 들어와
    `int(v or 0)`이 'cannot convert float NaN to integer'로 화면 전체를 죽인다."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if f != f else int(f)


def save_dispatch(by_user, dispatched_at, platform='upload', skip_existing=True,
                  use_row_date=False, move_wrong_date=False, detail=None):
    """분류 결과를 dispatch_log에 기록. 반환: {username: 저장건수}, 건너뛴 수.

    건너뛰기는 **같은 날짜**에 이미 있는 주문만 뺀다. 다른 날짜에 기록이 있다고
    빼면 안 된다 — 파일이 "이 날짜에 이 건이 나갔다"고 말하는데 저장을 막는
    셈이고, 실제로 그것 때문에 9/1 파일 9건 중 1건만 저장된 일이 있었다.
    dispatch_log는 UNIQUE(order_no, dispatched_at)이라 같은 날짜 중복만 막으면
    되고, 다른 날짜 건은 행이 하나 더 생긴다(재발송이면 그게 맞다).
    """
    from db import log_dispatch_success
    saved, skipped, moved = {}, 0, 0
    for uname, rows in (by_user or {}).items():
        # 행마다 집화일자가 다를 수 있다(여러 날이 한 파일에 섞인 경우).
        groups = {}
        for r in rows:
            _d = (str(r.get('dispatched_at') or '').strip()
                  if use_row_date else '') or str(dispatched_at)
            groups.setdefault(_d, []).append(r)
        for _d, _grp in groups.items():
            _rows = _grp
            if move_wrong_date:
                moved += _move_wrong_date(uname, _grp, _d)
            _same = set()
            if skip_existing:
                _ex = existing_dispatch(uname, [r['order_no'] for r in _grp])
                _same = {o for o, dd in _ex.items() if str(dd) == str(_d)}
                _rows = [r for r in _grp if r['order_no'] not in _same]
                skipped += len(_grp) - len(_rows)
            n = 0
            if _rows:
                try:
                    n = log_dispatch_success(uname, _rows, str(_d), platform=platform)
                except Exception:
                    n = 0
            # 몇 건인지만 알려 주면 어느 건인지 알 수 없다 — 줄마다 결과를 남긴다
            if detail is not None:
                for r in _grp:
                    _o = str(r.get('order_no') or '')
                    detail.append({
                        '결과': ('⏭ 이미 있음' if _o in _same
                                 else ('✅ 저장' if n else '❌ 저장 실패')),
                        '사용자': uname, '발송일': str(_d),
                        '주문번호': '' if _o.startswith('NO-') else _o,
                        '수취인': str(r.get('recipient') or ''),
                        '상품명': str(r.get('product_name') or '')[:30],
                        '송장번호': str(r.get('tracking_no') or '')})
            if not _rows:
                continue
            if n:
                saved[uname] = saved.get(uname, 0) + n
    return saved, skipped, moved




# ── 관리자 대신 스토어 발송처리 ─────────────────────────────
#   사용자가 발송처리를 늦게 해서 관리자가 17시에 CJ 파일로 정리한다. 기록만
#   넣으면 네이버·쿠팡 주문은 '발송 대기'로 남아 사용자가 또 처리해야 했다.
#   각 사용자 설정의 스토어 API 키로 관리자가 골라서 대신 송장을 등록한다.
_NV_COURIER = {"CJ대한통운": "CJGLS", "롯데택배": "HYUNDAI", "한진택배": "HANJIN",
               "우체국택배": "EPOST", "로젠택배": "KGB"}
_CP_COURIER = {"CJ대한통운": "CJGLS", "롯데택배": "HDEXP", "한진택배": "HANJIN",
               "우체국택배": "EPOST", "로젠택배": "KGB", "경동택배": "KDEXP",
               "대신택배": "DAESIN"}


def _store_keys(username):
    try:
        from db import get_all_settings
        s = get_all_settings(username) or {}
    except Exception:
        s = {}
    nv = (s.get('api_client_id') or '', s.get('api_client_secret') or '')
    cp = (s.get('coupang_access_key') or '', s.get('coupang_secret_key') or '',
          s.get('coupang_vendor_id') or '')
    return (nv if all(nv) else None), (cp if all(cp) else None)


def store_dispatched(username, order_nos):
    """이미 스토어(네이버·쿠팡)로 발송처리된 주문번호. 관리자 업로드 기록(upload)은 아니다."""
    from db import get_user_db
    out = set()
    _nos = [str(o) for o in order_nos if str(o)]
    if not _nos:
        return out
    try:
        conn = get_user_db(username)
        for i in range(0, len(_nos), 900):
            _ch = _nos[i:i + 900]
            for (o,) in conn.execute(
                    "SELECT order_no FROM dispatch_log WHERE platform IN ('naver','coupang') "
                    "AND order_no IN (%s)" % ",".join("?" * len(_ch)), _ch):
                out.add(str(o))
        conn.close()
    except Exception:
        pass
    return out


def ship_plan(by_user):
    """사용자별 스토어 발송 대상 — {username: {'naver': [...], 'coupang': [...],
    'done': n, 'no_trk': n, 'keys': (네이버키?, 쿠팡키?)}}"""
    plan = {}
    for u, rows in (by_user or {}).items():
        _done = store_dispatched(u, [r.get('order_no') for r in rows])
        nv, cp, done, no_trk, seen = [], [], 0, 0, set()
        for r in rows:
            o = str(r.get('order_no') or '').strip()
            if not o or o.startswith(('NO-', 'STOCKIN-')) or o in seen:
                continue
            seen.add(o)
            if o in _done:
                done += 1
                continue
            if not str(r.get('tracking_no') or '').strip():
                no_trk += 1
                continue
            (cp if '-' in o else nv).append(r)
        _k = _store_keys(u)
        plan[u] = {'naver': nv, 'coupang': cp, 'done': done, 'no_trk': no_trk,
                   'keys': (bool(_k[0]), bool(_k[1]))}
    return plan


def _fill_settlement(username, rows):
    """dispatch_log에 정산예정금액·배송비를 채운다 — 수익계산이 이걸 읽는다."""
    from db import get_user_db
    try:
        conn = get_user_db(username)
        for r in rows:
            h = conn.execute("SELECT settlement, shipping_fee, recipient, product_name "
                             "FROM order_history WHERE order_no=?", (r['order_no'],)).fetchone()
            if h:
                r['expected_settlement'] = int(h[0] or 0)
                r['customer_shipping_fee'] = int(h[1] or 0)
                r['recipient'] = r.get('recipient') or h[2] or ''
                r['product_name'] = r.get('product_name') or h[3] or ''
        conn.close()
    except Exception:
        pass
    return rows


def ship_for_user(username, plan_u, courier, dispatched_at):
    """한 사용자의 대상건을 그 사람 스토어에 발송처리하고 dispatch_log에 남긴다.
    반환: {'naver': (성공, 실패), 'coupang': (...), 'errors': [...], 'fails': [...]}"""
    from db import log_dispatch_success
    out = {'naver': (0, 0), 'coupang': (0, 0), 'errors': [], 'fails': []}
    nvk, cpk = _store_keys(username)
    try:
        from pages_lib.tracking_page import _log_dispatch_attempt as _logatt
    except Exception:
        _logatt = None

    def _log_rows(src, ok_ids, plat):
        ok = {str(x).strip() for x in ok_ids or []}
        rows = []
        for r in src:
            if str(r['order_no']) in ok:
                rows.append({'order_no': r['order_no'], 'recipient': r.get('recipient', ''),
                             'product_name': r.get('product_name', ''),
                             'tracking_no': str(r.get('tracking_no') or '').replace('-', ''),
                             'courier': r.get('courier') or courier,
                             '_d': str(r.get('dispatched_at') or '') or str(dispatched_at)})
        _fill_settlement(username, rows)
        by_d = {}
        for r in rows:
            by_d.setdefault(r.pop('_d'), []).append(r)
        for d, rs in by_d.items():
            log_dispatch_success(username, rs, d, platform=plat)

    # 네이버
    if plan_u.get('naver'):
        if not nvk:
            out['errors'].append(f"네이버 API 키 없음 — {len(plan_u['naver'])}건 못 보냄")
        else:
            try:
                import naver_api
                _raw = plan_u['naver']
                # 파일에 '주문번호'가 들어온 경우 상품주문번호로 바꾼다(송장 등록 화면과 같다)
                _map, _unres, _rerr = naver_api.resolve_product_order_ids(
                    nvk[0], nvk[1], [r['order_no'] for r in _raw])
                if _rerr and not _map:
                    _map, _unres = {r['order_no']: [r['order_no']] for r in _raw}, []
                _src = [dict(r, order_no=po) for r in _raw for po in _map.get(r['order_no'], [])]
                if _unres:
                    out['errors'].append(f"네이버 이 스토어 주문 아님 {len(_unres)}건: "
                                         + ", ".join(_unres[:3]))
                if _src:
                    _code = _NV_COURIER.get(courier, courier)
                    _items = [{"productOrderId": r['order_no'], "택배사": _code,
                               "trackingNumber": str(r.get('tracking_no') or '').replace('-', '')}
                              for r in _src]
                    res, err = naver_api.ship_orders(nvk[0], nvk[1], _items)
                    if _logatt:
                        _logatt(username, 'naver', len(_items), res, err, excluded=_unres,
                                note='관리자 대신 발송')
                    if err:
                        out['errors'].append(f"네이버: {err}")
                    res = res or {}
                    out['naver'] = (int(res.get('success') or 0), int(res.get('fail') or 0))
                    out['fails'] += [dict(f, 스토어='네이버') for f in res.get('fail_items') or []]
                    _ok = res.get('success_order_ids') or []
                    if _ok:
                        try:   # 수익계산이 order_history를 읽는다 — 상세를 먼저 채운다
                            from db import save_order_history
                            import pandas as _pd
                            _det, _ = naver_api.fetch_order_details_by_ids(nvk[0], nvk[1], list(_ok))
                            if _det:
                                save_order_history(username, _pd.DataFrame(_det))
                        except Exception:
                            pass
                        _log_rows(_src, _ok, 'naver')
            except Exception as e:
                out['errors'].append(f"네이버 처리 오류: {e}")
    # 쿠팡
    if plan_u.get('coupang'):
        if not cpk:
            out['errors'].append(f"쿠팡 API 키 없음 — {len(plan_u['coupang'])}건 못 보냄")
        else:
            try:
                import coupang_api
                _src = plan_u['coupang']
                _code = _CP_COURIER.get(courier, courier)
                _items = [{"productOrderId": r['order_no'], "courierCode": _code,
                           "trackingNumber": str(r.get('tracking_no') or '').replace('-', '')}
                          for r in _src]
                res, err = coupang_api.dispatch_orders(cpk[0], cpk[1], cpk[2], _items)
                if _logatt:
                    _logatt(username, 'coupang', len(_items), res, err, note='관리자 대신 발송')
                if err:
                    out['errors'].append(f"쿠팡: {err}")
                res = res or {}
                out['coupang'] = (int(res.get('success') or 0), int(res.get('fail') or 0))
                out['fails'] += [dict(f, 스토어='쿠팡') for f in res.get('fail_items') or []]
                if res.get('success_order_ids'):
                    _log_rows(_src, res['success_order_ids'], 'coupang')
            except Exception as e:
                out['errors'].append(f"쿠팡 처리 오류: {e}")
    return out


def _render_store_ship(st, pd, dmap, by_user, d_day):
    """사용자를 골라 그 사람 스토어에 대신 발송처리."""
    st.markdown("#### 🚀 스토어 발송처리 — 관리자가 대신")
    st.caption("파일의 송장번호를 **각 사용자의 네이버·쿠팡 스토어에 등록**합니다. "
               "사용자가 따로 발송처리하지 않아도 됩니다. 이미 스토어로 발송처리된 주문은 "
               "빼고 보냅니다. **고른 사용자만** 처리됩니다.")
    _res = st.session_state.pop('_du_ship_res', None)
    if _res:
        _rt = [{'사용자': dmap.get(u, u),
                '네이버 성공': r['naver'][0], '네이버 실패': r['naver'][1],
                '쿠팡 성공': r['coupang'][0], '쿠팡 실패': r['coupang'][1],
                '메모': ' / '.join(r['errors'])[:200]} for u, r in _res.items()]
        _okn = sum(x['네이버 성공'] + x['쿠팡 성공'] for x in _rt)
        _ngn = sum(x['네이버 실패'] + x['쿠팡 실패'] for x in _rt)
        (st.success if _okn and not _ngn else st.warning)(
            f"🚀 발송처리 결과 — 성공 {_okn}건 · 실패 {_ngn}건")
        st.dataframe(pd.DataFrame(_rt), use_container_width=True, hide_index=True)
        _fl = [dict(f, 사용자=dmap.get(u, u)) for u, r in _res.items() for f in r['fails']]
        if _fl:
            with st.expander(f"📋 실패 {len(_fl)}건 상세", expanded=True):
                st.dataframe(pd.DataFrame(_fl), use_container_width=True, hide_index=True)
    plan = ship_plan(by_user)
    if not plan:
        return
    _us = sorted(plan, key=lambda u: -(len(plan[u]['naver']) + len(plan[u]['coupang'])))

    def _state(p):
        _m = []
        if p['naver'] and not p['keys'][0]:
            _m.append('⚠️ 네이버 키 없음')
        if p['coupang'] and not p['keys'][1]:
            _m.append('⚠️ 쿠팡 키 없음')
        return ' · '.join(_m) or ('✅ 보낼 수 있음' if (p['naver'] or p['coupang'])
                                  else '보낼 건 없음')
    _rows = [{'선택': False,
              '사용자': (f"{dmap.get(u, u)} ({u})" if dmap.get(u, u) != u else u),
              '네이버': len(plan[u]['naver']), '쿠팡': len(plan[u]['coupang']),
              '이미 발송처리됨': plan[u]['done'], '송장 없음': plan[u]['no_trk'],
              '상태': _state(plan[u])} for u in _us]
    _ed = st.data_editor(pd.DataFrame(_rows), use_container_width=True, hide_index=True,
                         key=f"du_ship_pick_{d_day}",
                         disabled=['사용자', '네이버', '쿠팡', '이미 발송처리됨', '송장 없음', '상태'],
                         column_config={'선택': st.column_config.CheckboxColumn('선택')})
    _pick = [_us[i] for i, r in enumerate(_ed.to_dict('records')) if r.get('선택')]
    _n = sum(len(plan[u]['naver']) * plan[u]['keys'][0]
             + len(plan[u]['coupang']) * plan[u]['keys'][1] for u in _pick)
    _c1, _c2 = st.columns([1, 2])
    _cour = _c1.selectbox("택배사", list(_CP_COURIER), index=0, key="du_ship_courier")
    _agree = _c2.checkbox("스토어 주문이 **배송중으로 바뀌고 고객에게 발송 알림**이 갑니다 — "
                          "되돌릴 수 없음을 확인했습니다", key="du_ship_ok")
    if st.button(f"🚀 선택한 {len(_pick)}명 · {_n}건 스토어 발송처리", type="primary",
                 key="du_ship_go", disabled=not (_pick and _n and _agree)):
        _out = {}
        _bar = st.progress(0.0)
        for i, u in enumerate(_pick):
            _bar.progress(i / len(_pick), text=f"{dmap.get(u, u)} 발송처리 중…")
            _out[u] = ship_for_user(u, plan[u], _cour, str(d_day))
        _bar.empty()
        st.session_state['_du_ship_res'] = _out
        st.session_state['_rs_rematch'] = True
        st.session_state.pop('rs_alloc', None)
        st.session_state.pop('du_ship_ok', None)
        st.rerun()



# ── 합배송 ────────────────────────────────────────────────
#   한 상자에 여러 주문을 담아 보내면 택배 송장은 하나라 CJ 파일에는 주문번호가
#   한 줄(대표 주문)만 찍힌다. 그대로 보내면 네이버에는 대표 주문만 발송처리되고
#   같이 나간 나머지 주문은 '발송 대기'로 남는다. 같은 상자로 나간 주문을 찾아
#   같은 송장으로 함께 넣는다.
#   같은 상자 = 같은 주문번호(order_group_no)이거나, 수취인·연락처·주소가 모두 같은
#   **아직 발송 안 된** 네이버 주문. 이름만 같은 건 붙이지 않는다(동명이인).
def _addr_key(raw):
    import json as _json, re as _re
    try:
        d = _json.loads(raw or '{}')
    except Exception:
        return None
    _nm = str(d.get('수취인명') or '').strip()
    _ph = _re.sub(r'\D', '', str(d.get('수취인연락처1') or ''))
    _ad = _re.sub(r'\s', '', str(d.get('통합배송지') or d.get('기본배송지') or ''))
    if not (_nm and _ph and _ad):
        return None
    return (_nm, _ph, _ad)


def combined_siblings(username, order_nos):
    """{대표 주문번호: [같은 상자로 나간 다른 주문 dict, ...]} — 네이버 주문만."""
    from db import get_user_db
    from db_orders import ACTIVE_ORDER_STATUSES
    _nos = [str(o) for o in order_nos if str(o) and '-' not in str(o)
            and not str(o).startswith(('NO-', 'STOCKIN-'))]
    if not _nos:
        return {}
    try:
        conn = get_user_db(username)
        conn.row_factory = __import__('sqlite3').Row
        _ph = ",".join("?" * len(ACTIVE_ORDER_STATUSES))
        _active = [dict(r) for r in conn.execute(
            f"SELECT order_no, order_group_no, recipient, product_name, qty, raw_json "
            f"FROM order_history WHERE status IN ({_ph}) AND COALESCE(tracking_no,'')=''",
            ACTIVE_ORDER_STATUSES)]
        _done = {str(r[0]) for r in conn.execute("SELECT order_no FROM dispatch_log")}
        _src = {}
        for i in range(0, len(_nos), 900):
            _ch = _nos[i:i + 900]
            for r in conn.execute(
                    "SELECT order_no, order_group_no, raw_json FROM order_history "
                    "WHERE order_no IN (%s)" % ",".join("?" * len(_ch)), _ch):
                _src[str(r['order_no'])] = (str(r['order_group_no'] or ''),
                                            _addr_key(r['raw_json']))
        conn.close()
    except Exception:
        return {}
    _in = set(_nos)
    _by_grp, _by_addr = {}, {}
    for a in _active:
        _o = str(a['order_no'])
        if _o in _in or _o in _done:
            continue
        if a.get('order_group_no'):
            _by_grp.setdefault(str(a['order_group_no']), []).append(a)
        _k = _addr_key(a.get('raw_json'))
        if _k:
            _by_addr.setdefault(_k, []).append(a)
    out, _used = {}, set()
    for o in _nos:
        _g, _k = _src.get(o, ('', None))
        for a in (_by_grp.get(_g, []) if _g else []) + (_by_addr.get(_k, []) if _k else []):
            _ao = str(a['order_no'])
            if _ao in _used:
                continue
            _used.add(_ao)
            out.setdefault(o, []).append(a)
    return out


def expand_combined(by_user):
    """분류 결과에 합배송 주문을 같은 송장으로 덧붙인다. 반환: (by_user, 추가 목록)."""
    added = []
    for u, rows in (by_user or {}).items():
        _sib = combined_siblings(u, [r.get('order_no') for r in rows if r.get('tracking_no')])
        if not _sib:
            continue
        _extra = []
        for r in rows:
            for a in _sib.get(str(r.get('order_no')), []):
                _x = dict(r, order_no=str(a['order_no']),
                          recipient=str(a.get('recipient') or r.get('recipient') or ''),
                          product_name=str(a.get('product_name') or ''),
                          qty=int(a.get('qty') or 1), _combined_with=str(r.get('order_no')))
                _extra.append(_x)
                added.append(dict(_x, username=u))
        rows.extend(_extra)
    return by_user, added



def _trk_key(v):
    return ''.join(ch for ch in str(v or '') if ch.isdigit())


def saved_trackings(trackings):
    """{송장(숫자만): 사용자} — 이 송장들 중 이미 누군가의 발송 기록에 있는 것."""
    import glob as _g, os as _o, sqlite3 as _sq
    from db_core import DATA_DIR
    want = {_trk_key(t) for t in (trackings or []) if _trk_key(t)}
    out = {}
    if not want:
        return out
    for f in _g.glob(_o.path.join(DATA_DIR, '*.db')):
        u = _o.path.basename(f)[:-3]
        if u == 'auth' or '.' in u:
            continue
        try:
            c = _sq.connect(f'file:{f}?mode=ro', uri=True)
            for (t,) in c.execute("SELECT tracking_no FROM dispatch_log WHERE COALESCE(tracking_no,'')<>''"):
                k = _trk_key(t)
                if k in want:
                    out.setdefault(k, u)
            c.close()
        except Exception:
            continue
    return out

def _render_sender_panel(st, pd, dmap, smap, unknown, external):
    """🏢 보낸 업체 구분 — 코코비즈 사용자가 아닌 업체 건을 빼고, 처음 보는 코드를 정한다."""
    if external:
        _by = {}
        for r in external:
            _k = r.get('sender_name') or r.get('sender_code')
            _by[_k] = _by.get(_k, 0) + 1
        st.info(f"🏢 **코코비즈 밖 업체 {len(external)}건은 제외합니다** — "
                + " · ".join(f"{k} {v}건" for k, v in
                             sorted(_by.items(), key=lambda kv: -kv[1]))
                + ". 발송 기록·영수증 매칭에 들어가지 않습니다.")
    # 처음 보는 코드 — 미분류 행 중 구분이 정해지지 않은 코드
    _new = {}
    for r in (unknown or []):
        c = str(r.get('sender_code') or '').strip()
        if c and c not in smap:
            _e = _new.setdefault(c, {'name': r.get('sender_name') or '', 'n': 0})
            _e['n'] += 1
    with st.expander(f"🏢 보낸 업체 구분 — 코드 {len(smap)}개 기억 중"
                     + (f" · 처음 보는 코드 {len(_new)}개" if _new else ""),
                     expanded=bool(_new)):
        st.caption("CJ 파일의 **발송고객코드**로 보낸 업체를 구분합니다. 주문번호로 맞은 "
                   "행에서 '코드 → 사용자'를 자동으로 배우고, 처음 보는 코드는 여기서 한 번 "
                   "정하면 다음부터 자동 분류됩니다. **외부 업체**로 정한 코드는 발송 "
                   "기록과 영수증 매칭에서 빠집니다. 여러 사용자가 함께 쓰는 코드는 "
                   "미지정으로 두세요 — 주문번호로만 가릅니다.")
        _EXT, _NONE = '🏢 외부 업체(제외)', '— 미지정(주문번호로만) —'
        _l2u = {f"👤 {dmap.get(u, u)} ({u})": u for u in dmap}
        _opts = [_NONE, _EXT] + sorted(_l2u)

        def _lab(m):
            if (m or {}).get('kind') == 'external':
                return _EXT
            if (m or {}).get('kind') == 'user' and m.get('username') in dmap:
                return f"👤 {dmap.get(m['username'], m['username'])} ({m['username']})"
            return _NONE
        _rows = [{'코드': c, '송하인': v['name'], '이번 파일': v['n'], '구분': _NONE}
                 for c, v in sorted(_new.items(), key=lambda kv: -kv[1]['n'])]
        _rows += [{'코드': c, '송하인': m.get('name', ''), '이번 파일': 0, '구분': _lab(m)}
                  for c, m in sorted(smap.items())]
        if not _rows:
            st.caption("아직 기억한 코드가 없습니다.")
            return
        _ed = st.data_editor(
            pd.DataFrame(_rows), use_container_width=True, hide_index=True,
            key="du_sender_ed", disabled=['코드', '송하인', '이번 파일'],
            column_config={'구분': st.column_config.SelectboxColumn(
                '구분', options=_opts, required=True)})
        _new_map, _chg = dict(smap), 0
        for r in _ed.to_dict('records'):
            c, v = str(r['코드']), str(r['구분'])
            cur = smap.get(c) or {}
            if v == _lab(cur) and (c in smap or v == _NONE):
                continue
            if v == _EXT:
                _new_map[c] = {'kind': 'external', 'name': r['송하인']}
            elif v in _l2u:
                _new_map[c] = {'kind': 'user', 'username': _l2u[v], 'name': r['송하인'],
                               'fixed': True}
            else:
                _new_map[c] = {'kind': 'shared', 'name': r['송하인'], 'fixed': True}
            _chg += 1
        if st.button(f"💾 업체 구분 저장 ({_chg}건 변경)", key="du_sender_save",
                     disabled=not _chg):
            save_sender_map(_new_map)
            st.session_state['_du_msg'] = (f"🏢 보낸 업체 구분 {_chg}건 저장 — "
                                           "다시 분류했습니다.")
            st.rerun()


# ── 화면 (Streamlit) ───────────────────────
def render_panel(dmap, USERNAME):
    """발송 파일 업로드 화면. 영수증 정산에서 호출한다.

    영수증과 발송건을 함께 올려야 각 사용자의 주문건이 정리된다.
    구매내역 정산은 그 결과를 확인·검수만 한다.
    """
    import streamlit as st
    import pandas as pd
    from datetime import date
    from utils import fmt
    """발송 파일 업로드 — 주문번호로 사용자를 갈라 dispatch_log에 기록.

    청구는 발송 기준인데 송장 등록을 안 하는 계정이 있으면 청구가 0원이 된다
    (clglobal0919는 8/31~9/6 주문 205건에 발송 0건이었다).
    관리자가 전체 발송 파일 하나를 올려 분류하면 그 의존이 사라진다.
    """

    # 저장하고 나면 결과를 봐야 한다 — 접혀 있으면 무엇이 저장됐는지 안 보였다
    with st.expander("🚚 발송 파일 업로드 — 주문번호로 사용자 분류",
                     expanded=bool(st.session_state.get('_du_msg')
                                   or st.session_state.get('_du_ship_res'))):
        _msg = st.session_state.pop('_du_msg', None)
        if _msg:
            st.success(_msg)
            _det = st.session_state.pop('_du_detail', None)
            if _det:
                for _x in _det:
                    _x['사용자'] = dmap.get(_x['사용자'], _x['사용자'])
                _det.sort(key=lambda x: (x['결과'] != '✅ 저장', x['사용자']))
                st.caption("어떤 건이 저장·건너뛰기 됐는지 — 같은 날짜에 이미 있던 건은 "
                           "**⏭ 이미 있음**(다시 넣지 않음)")
                st.dataframe(pd.DataFrame(_det), use_container_width=True, hide_index=True)
        st.caption(
            "전체 발송내역 파일(엑셀·CSV)을 올리면 **주문번호**로 각 사용자에게 나눠 "
            "발송 기록에 넣습니다. 이후 영수증 정산이 그 발송건에 매입가를 채웁니다. "
            "**여러 개를 한 번에** 올릴 수 있고, 같은 파일을 두 번 올려도 "
            "중복 저장되지 않습니다.")
        # ── ✍️ 발송건 직접 입력 ────────────────────────────────────
        #   파일이 없는 경우가 있다. 이전 주문이 미발송으로 남아 있다가 나중에
        #   손으로 보내는 건이 대표적이다 — 택배사 파일에도 안 잡히고, 그날 주문이
        #   아니라 미발송 목록에도 안 뜬다. 그러면 그 발송건은 어디에도 기록되지
        #   않아 청구에서 통째로 빠진다. 관리자가 직접 넣을 길을 둔다.
        with st.expander("✍️ 발송건 직접 입력 — 파일 없이 손으로 넣기", expanded=False):
            st.caption(
                "택배사 파일에 없는 발송건을 직접 넣습니다. **이전 주문을 뒤늦게 "
                "보낸 건**처럼 파일·목록 어디에도 안 잡히는 경우에 쓰세요. "
                "주문번호를 넣으면 영수증 매칭까지 되고, 없으면 발송건수·택배비에만 "
                "잡힙니다.")
            _mu_opts = [dmap.get(_u, _u) for _u in sorted(dmap)]
            _mu_l2u = {str(dmap.get(_u, _u)).strip(): _u for _u in dmap}
            if not _mu_opts:
                st.caption("사용자가 없습니다.")
            else:
                _mdate = st.date_input("발송일 (집화일 기준)", value=date.today(),
                                       key="du_man_date",
                                       help="택배가 실제로 나간 날입니다. 그날 청구에 "
                                            "잡힙니다.")
                _mrows = [{'사용자': _mu_opts[0], '주문번호': '', '수취인': '',
                           '상품명': '', '수량': 1, '송장번호': '', '택배사': ''}
                          for _ in range(5)]
                _med = st.data_editor(
                    pd.DataFrame(_mrows), use_container_width=True, hide_index=True,
                    num_rows="dynamic", key=f"du_man_ed_{_mdate}",
                    column_config={
                        '사용자': st.column_config.SelectboxColumn(
                            '사용자', options=_mu_opts, required=True),
                        '주문번호': st.column_config.TextColumn(
                            '주문번호',
                            help='상품주문번호. 넣으면 영수증 매칭까지 됩니다. '
                                 '모르면 비워도 됩니다.'),
                        '수량': st.column_config.NumberColumn('수량', format='%d',
                                                            min_value=1, step=1),
                        '송장번호': st.column_config.TextColumn(
                            '송장번호', help='주문번호가 없을 때 이 값으로 키를 만듭니다.'),
                    })
                _mpick, _mbad = {}, 0
                for _i, _r in enumerate(_med.to_dict('records')):
                    _ono = str(_r.get('주문번호') or '').strip()
                    _trk = str(_r.get('송장번호') or '').strip()
                    _nm = str(_r.get('상품명') or '').strip()
                    _rc = str(_r.get('수취인') or '').strip()
                    if not (_ono or _trk or _nm or _rc):
                        continue                      # 빈 줄은 넘어간다
                    _u2 = _mu_l2u.get(str(_r.get('사용자') or '').strip(), '')
                    if not _u2 or not (_ono or _trk):
                        _mbad += 1                    # 주문번호도 송장도 없으면 못 넣는다
                        continue
                    _mpick.setdefault(_u2, []).append({
                        'order_no': _ono or ('NO-%s-%s' % (_mdate, _trk)),
                        'recipient': _rc, 'product_name': _nm,
                        'qty': max(1, _cell_int(_r.get('수량'), 1)),
                        'tracking_no': _trk,
                        'courier': str(_r.get('택배사') or '').strip(),
                    })
                if _mbad:
                    st.warning(f"⚠️ {_mbad}줄은 **주문번호와 송장번호가 모두 비어** "
                               "넣을 수 없습니다 — 둘 중 하나는 있어야 기록됩니다.")
                _mn = sum(len(v) for v in _mpick.values())
                if _mn:
                    st.caption("넣을 건 — " + " · ".join(
                        f"{dmap.get(_u, _u)} {len(_v)}건" for _u, _v in _mpick.items()))
                if st.button(f"💾 {_mn}건 발송 기록 저장", key="du_man_save",
                             type="primary", disabled=not _mn):
                    _det = []
                    st.session_state['_du_detail'] = _det
                    _ms, _mk, _mm = save_dispatch(_mpick, str(_mdate), detail=_det,
                                                  skip_existing=True)
                    _mtot = sum(_ms.values())
                    st.session_state['_rs_rematch'] = True   # 발송건이 늘었다 — 매칭을 다시 돌린다
                    st.session_state.pop('rs_alloc', None)
                    st.session_state['_du_msg'] = (
                        (f"✍️ 발송 기록 {_mtot}건 직접 입력 ({_mdate}) — "
                         + " · ".join(f"{dmap.get(u, u)} {c}건" for u, c in _ms.items()))
                        if _mtot else
                        "ℹ️ 저장된 건이 없습니다 — 그 날짜에 이미 기록돼 있습니다."
                    ) + (f"  ·  ⏭ 건너뜀 {_mk}건" if _mk else "")
                    st.rerun()

        _fs = st.file_uploader("발송 파일 (xlsx · xls · csv · 여러 개 가능)",
                               type=['xlsx', 'xls', 'csv'], key="du_file",
                               accept_multiple_files=True)
        if not _fs:
            return

        # 택배사·몰마다 양식이 달라 파일마다 헤더가 다를 수 있다.
        # 같으면 그대로 합치고, 다르면 파일별로 열을 추정해 표준 열로 맞춘 뒤 합친다.
        _frames, _bad = [], []
        for _f in _fs:
            try:
                if _f.name.lower().endswith('.csv'):
                    _d = pd.read_csv(_f, dtype=str)
                else:
                    _d = pd.read_excel(_f, dtype=str)
            except Exception as _e:
                _bad.append((_f.name, str(_e)[:60]))
                continue
            if _d is None or _d.empty:
                _bad.append((_f.name, '빈 파일'))
                continue
            _frames.append((_f.name, _d.fillna('')))
        for _n, _e in _bad:
            st.error(f"⚠️ {_n} — {_e}")
        if not _frames:
            return

        _same = len({tuple(str(c) for c in _d.columns) for _, _d in _frames}) == 1
        st.caption("📄 " + " · ".join(f"{_n} {len(_d)}행" for _n, _d in _frames)
                   + (f"  ·  합계 {sum(len(_d) for _, _d in _frames)}행"
                      if len(_frames) > 1 else "")
                   + ("" if _same else "  ·  ⚠️ 파일마다 열이 달라 각각 자동 인식합니다"))

        _STD = ('order_no', 'tracking_no', 'recipient', 'product_name', 'qty', 'courier',
                'dispatched_at', 'sender_code', 'sender_name')
        if _same:
            _df = pd.concat([_d for _, _d in _frames], ignore_index=True)
        else:
            _norm = []
            for _n, _d in _frames:
                _g = guess_columns(list(_d.columns))
                if not _g.get('order_no'):
                    st.error(f"⚠️ {_n} — 주문번호 열을 찾지 못해 제외합니다.")
                    continue
                _one = pd.DataFrame(
                    {_k: (_d[_g[_k]] if _g.get(_k) in _d.columns else '')
                     for _k in _STD})
                _norm.append(_one)
            if not _norm:
                return
            _df = pd.concat(_norm, ignore_index=True).fillna('')

        _cols = list(_df.columns)
        _guess = guess_columns(_cols)
        # 주문번호 열은 이름이 아니라 **값**으로 고른다. 택배사 파일은
        # '주문번호'라는 이름을 자기 관리번호에 붙여 놓는 일이 흔하고,
        # 스토어(네이버·쿠팡)가 섞이면 번호 열이 여럿일 수도 있다.
        _idx0, _dup0 = build_order_owner_index()
        _recs0 = _df.to_dict('records')
        _ranked = order_columns(_recs0, _cols, _idx0)
        _alt = []
        if _ranked:
            _best, _bh = _ranked[0]
            _alt = [c for c, _ in _ranked[1:]]
            if _guess.get('order_no') != _best:
                st.info(f"🔎 주문번호 열을 **{_best}** 로 잡았습니다 — "
                        f"그 열의 값이 실제 주문과 {_bh}건 일치합니다."
                        + (f" (헤더 이름으로는 '{_guess.get('order_no')}'가 잡혔는데 "
                           "그 열에는 맞는 번호가 없습니다.)"
                           if _guess.get('order_no') else ""))
            _guess['order_no'] = _best
            if _alt:
                st.caption("🛒 스토어가 섞여 있어 보조 열도 함께 봅니다 — "
                           + " · ".join(f"{c}({h}건)" for c, h in _ranked[1:4]))
        elif _idx0:
            st.warning("⚠️ 이 파일의 어느 열도 우리 주문번호와 맞지 않습니다 — "
                       "**상품주문번호**가 들어 있는 열인지, 그리고 그 주문들이 "
                       "일일 주문 수집으로 들어와 있는지 확인하세요.")
        # 열 매핑은 접어 둔다 — 주문번호 열은 값으로 찾으므로 사람이 확인할 일이
        # 거의 없다. 매번 6개 드롭다운을 보여 주면 진짜 확인할 것(날짜·건수)이 묻힌다.
        _opts = ['(없음)'] + [str(c) for c in _cols]
        _need_fix = not _guess.get('order_no')
        with st.expander("⚙️ 열 매핑 — 자동 인식 결과 (고칠 일이 있을 때만 여세요)",
                         expanded=_need_fix):
            _m1, _m2, _m3 = st.columns(3)

            def _pick(col, label, key, need=False):
                _d = _guess.get(key)
                _i = _opts.index(str(_d)) if _d and str(_d) in _opts else 0
                _v = col.selectbox(label + (" *" if need else ""), _opts, index=_i,
                                   key=f"du_col_{key}")
                return None if _v == '(없음)' else _v

            _cm = {
                'order_no':     _pick(_m1, "주문번호", 'order_no', need=True),
                'tracking_no':  _pick(_m2, "송장번호", 'tracking_no'),
                'recipient':    _pick(_m3, "수취인", 'recipient'),
                'product_name': _pick(_m1, "상품명", 'product_name'),
                'qty':          _pick(_m2, "수량", 'qty'),
                'courier':      _pick(_m3, "택배사", 'courier'),
                'dispatched_at': _guess.get('dispatched_at'),
                'sender_code':  _pick(_m1, "발송고객코드", 'sender_code'),
                'sender_name':  _pick(_m2, "송하인(보낸 업체)", 'sender_name'),
                'order_no_alt': _alt,      # 스토어가 섞였을 때 함께 볼 열
            }
        if not _cm.get('order_no'):
            st.error("⚠️ **주문번호** 열을 지정해야 분류할 수 있습니다. "
                     "위 **⚙️ 열 매핑**을 열어 지정하세요.")
            return
        st.caption("🔗 인식된 열 — "
                   + " · ".join(f"{_k}={_cm.get(_v) or '없음'}" for _k, _v in
                                (('주문번호', 'order_no'), ('송장', 'tracking_no'),
                                 ('수취인', 'recipient'), ('택배사', 'courier'))))

        # 발송일은 파일에서 읽는다. 기본값을 오늘로 두면 9/1 파일을 올리고도
        # 오늘 발송으로 저장돼 그날 택배비가 통째로 어긋난다(실제로 그럴 뻔했다).
        _dates = file_dates(_recs0, _cols)
        if _dates:
            _top = max(_dates.items(), key=lambda kv: kv[1])[0]
            _dv = _top
            if len(_dates) == 1:
                st.success(f"📅 파일에서 발송일 **{_top}** 을 읽었습니다 "
                           f"({_dates[_top]}건).")
            else:
                st.warning(
                    "📅 파일에 날짜가 여러 개입니다 — "
                    + " · ".join(f"{_k} {_v}건" for _k, _v in sorted(_dates.items()))
                    + ". 아래 날짜 하나로 **전부** 저장되므로, 날짜별로 나눠 올리는 "
                      "편이 정확합니다.")
        else:
            _dv = date.today()
            st.caption("📅 파일에서 발송일 열을 찾지 못했습니다 — 아래에서 직접 고르세요.")
        _dd = st.date_input("발송일 (청구 귀속일)", value=_dv, key="du_date",
                            help="이 날짜로 발송 기록이 남고, 그날 청구에 잡힙니다. "
                                 "파일에 날짜가 있으면 그 값이 기본으로 들어옵니다.")

        _idx, _dup = _idx0, _dup0
        if _dup:
            st.warning(f"⚠️ 두 사용자에 걸친 주문번호 {len(_dup)}건이 있습니다 — "
                       "그 건은 분류가 부정확할 수 있습니다.")
        _by_user, _unknown = classify_rows(_recs0, _cm, _idx)
        # 보낸 업체(발송고객코드)로 코코비즈 밖 업체 건을 가른다 — 기록·매칭에서 뺀다
        _smap = load_sender_map() if _cm.get('sender_code') else {}
        if _cm.get('sender_code'):
            if learn_sender_map(_by_user, _smap):
                try:
                    save_sender_map(_smap)
                except Exception:
                    pass
            _external, _unknown = split_external(_unknown, _smap)
            _render_sender_panel(st, pd, dmap, _smap, _unknown, _external)

        # 합배송 — 송장 하나에 담겨 나간 다른 주문도 같은 송장으로 함께 넣는다
        _by_user, _comb = expand_combined(_by_user)
        if _comb:
            with st.expander(f"📦 합배송 {len(_comb)}건 추가 — 대표 주문과 같은 송장으로 "
                             "함께 기록·발송처리합니다", expanded=False):
                st.caption("CJ 파일에는 상자마다 대표 주문 하나만 찍힙니다. 같은 주문번호이거나 "
                           "**수취인·연락처·주소가 모두 같은 미발송 주문**을 찾아 붙였습니다.")
                st.dataframe(pd.DataFrame([{
                    '사용자': dmap.get(x['username'], x['username']),
                    '대표 주문': x['_combined_with'], '함께 나간 주문': x['order_no'],
                    '수취인': x.get('recipient', ''), '상품명': str(x.get('product_name') or '')[:30],
                    '송장번호': x.get('tracking_no', '')} for x in _comb]),
                    use_container_width=True, hide_index=True)
        _tot = sum(len(v) for v in _by_user.values())
        st.markdown(f"### 분류 결과 — {_tot}건 매칭 · {len(_unknown)}건 미분류")
        _new_total, _other_dates = 0, {}
        if _by_user:
            _sum = []
            for _u, _rows in sorted(_by_user.items(), key=lambda kv: -len(kv[1])):
                _ex = existing_dispatch(_u, [r['order_no'] for r in _rows])
                _same = sum(1 for _d in _ex.values() if str(_d) == str(_dd))
                _diff = len(_ex) - _same
                for _o, _d in _ex.items():
                    if str(_d) != str(_dd):
                        _other_dates[str(_d)] = _other_dates.get(str(_d), 0) + 1
                # 건너뛰기는 같은 날짜만 뺀다 → 다른 날짜 건은 저장 대상이다
                _new = len(_rows) - _same
                _new_total += _new
                _sum.append({'사용자': dmap.get(_u, _u), '건수': len(_rows),
                             '이 날짜에 이미 있음': _same,
                             '다른 날짜에 기록됨': _diff,
                             '새로 저장될 건': _new})
            st.dataframe(pd.DataFrame(_sum), use_container_width=True, hide_index=True)
        # 다른 날짜에 이미 있는 건은 건너뛰기를 켜 두면 조용히 빠진다. 왜 0건
        # 저장됐는지 화면이 답해야 한다.
        if _other_dates:
            st.warning(
                "⚠️ 파일의 주문 중 **다른 날짜로 이미 발송된 건**이 있습니다 — "
                + " · ".join(f"{_k} {_v}건" for _k, _v in sorted(_other_dates.items()))
                + ". 송장번호가 다르면 **재발송**(반품 후 재배송 등)이라 택배가 두 번 "
                  "나간 것이므로 두 기록이 모두 있어야 맞습니다. "
                  "그런데 건너뛰기는 **주문번호만** 보므로, 켜 두면 이 건들이 "
                  "저장되지 않습니다. 재발송이 맞다면 건너뛰기를 **끄고** 저장하세요 — "
                  "기존 기록은 그대로 두고 이 날짜로 **한 건 더** 쌓입니다"
                  "(기존 날짜를 덮어쓰지 않습니다). 그만큼 택배비도 더 청구됩니다.")
        # 미분류 중 **이미 저장된 건**(같은 송장이 누군가의 발송 기록에 있음)은 뺀다 —
        #   표는 파일에서 매번 다시 만들어져 저장한 뒤에도 그대로 남아 '저장이 안 된다'로 보였다.
        # 파일 맨 아래 합계 줄(수취인·상품·송장 모두 빈 줄)은 발송건이 아니다
        _unknown = [u for u in _unknown if (str(u.get('recipient') or '').strip()
                                            or str(u.get('product_name') or '').strip()
                                            or _trk_key(u.get('tracking_no')))]
        _saved_trk = saved_trackings([u.get('tracking_no') for u in _unknown])
        if _saved_trk:
            _done_u = [u for u in _unknown
                       if _trk_key(u.get('tracking_no')) in _saved_trk]
            _unknown = [u for u in _unknown
                        if _trk_key(u.get('tracking_no')) not in _saved_trk]
            if _done_u:
                st.caption(f"✅ 미분류 중 **이미 저장된 {len(_done_u)}건**은 목록에서 뺐습니다 — "
                           + " · ".join(f"{dmap.get(_saved_trk[_trk_key(u.get('tracking_no'))], '')} "
                                        f"{u.get('recipient', '')}" for u in _done_u[:6])
                           + (" …" if len(_done_u) > 6 else ""))
        if _unknown:
            with st.expander(f"❓ 미분류 {len(_unknown)}건 — 사용자를 지정해 넣기",
                             expanded=False):
                st.caption(
                    "주문번호가 비었거나, 어느 사용자 DB에도 없는 번호입니다. "
                    "주문 수집이 안 된 건일 수 있습니다. **누구 것인지 아시면 "
                    "사용자를 골라 바로 넣으세요** — 그대로 두면 그 발송건은 "
                    "청구에 잡히지 않습니다.")
                _NOU = '— 지정 안 함 —'
                _uopts = [_NOU] + [dmap.get(_u, _u) for _u in sorted(dmap)]
                _ul2u = {str(dmap.get(_u, _u)).strip(): _u for _u in dmap}
                def _pre(u):
                    _m = _smap.get(str(u.get('sender_code') or '')) or {}
                    if _m.get('kind') == 'user' and _m.get('username') in dmap:
                        return dmap.get(_m['username'], _m['username'])
                    return _NOU
                _urows = [{
                    '사용자 지정': _pre(u),
                    '송하인': u.get('sender_name', ''),
                    '행': u['_row'], '주문번호': u['order_no'],
                    '수취인': u['recipient'], '상품명': u['product_name'][:34],
                    '수량': int(u.get('qty') or 1),
                    '송장번호': u.get('tracking_no', ''),
                    '집화일자': u.get('dispatched_at', ''),
                    '사유': u.get('_why', ''),
                } for u in _unknown[:200]]
                _ued = st.data_editor(
                    pd.DataFrame(_urows), use_container_width=True, hide_index=True,
                    key=f"du_unknown_{_dd}",
                    disabled=['행', '송하인', '주문번호', '수취인', '상품명', '송장번호',
                              '집화일자', '사유'],
                    column_config={
                        '사용자 지정': st.column_config.SelectboxColumn(
                            '사용자 지정', options=_uopts, required=True,
                            help='이 발송건이 누구 것인지 고르세요'),
                        '수량': st.column_config.NumberColumn('수량', format='%d',
                                                            min_value=1, step=1),
                    })
                _upick = {}
                for _i, _r in enumerate(_ued.to_dict('records')):
                    _un2 = _ul2u.get(str(_r.get('사용자 지정') or '').strip(), '')
                    if not _un2:
                        continue
                    _src = dict(_unknown[_i])
                    _src['qty'] = max(1, _cell_int(_r.get('수량'), 1))
                    if not str(_src.get('order_no') or '').strip():
                        # 주문번호가 없으면 dispatch_log의 키가 안 잡힌다.
                        # 송장번호로 대체 키를 만들어 최소한 발송건수에는 잡히게 한다.
                        _src['order_no'] = ('NO-%s-%s'
                                            % (_dd, _src.get('tracking_no') or _src['_row']))
                    _upick.setdefault(_un2, []).append(_src)
                _un_n = sum(len(v) for v in _upick.values())
                if _un_n:
                    st.caption("넣을 건 — " + " · ".join(
                        f"{dmap.get(_u, _u)} {len(_v)}건" for _u, _v in _upick.items()))
                if st.button(f"👤 {_un_n}건을 지정한 사용자로 저장",
                             key=f"du_unknown_save_{_dd}", disabled=not _un_n):
                    # 아래 옵션 체크박스는 이 블록보다 뒤에 그려지므로, 첫 렌더에는
                    # session_state에 없다. 그때는 체크박스와 같은 기본값을 쓴다.
                    _det = []
                    st.session_state['_du_detail'] = _det
                    _s2, _k2, _m2 = save_dispatch(
                        _upick, str(_dd), detail=_det,
                        skip_existing=bool(st.session_state.get('du_skip', True)),
                        use_row_date=bool(st.session_state.get('du_rowdate',
                                                              bool(_dates))),
                        move_wrong_date=bool(st.session_state.get('du_fixdate',
                                                                 bool(_dates))))
                    _n2 = sum(_s2.values())
                    st.session_state['_rs_rematch'] = True   # 발송건이 늘었다 — 매칭을 다시 돌린다
                    st.session_state.pop('rs_alloc', None)
                    st.session_state['_du_msg'] = (
                        f"👤 미분류 {_n2}건을 지정한 사용자로 저장 — "
                        + " · ".join(f"{dmap.get(u, u)} {c}건" for u, c in _s2.items())
                        if _n2 else "ℹ️ 저장된 건이 없습니다(이미 기록돼 있습니다).")
                    st.rerun()

        if _by_user:
            with st.container(border=True):
                _render_store_ship(st, pd, dmap, _by_user, _dd)
        # 집화일자 = 택배사가 물건을 받아 간 날. 발송일의 진짜 근거다.
        #   앱의 일괄발송은 '버튼 누른 시각'을 쓰므로 자정을 넘기면 다음 날로
        #   기록된다(실측: 9/11 발송 8건이 9/12 00:20 처리 → 9/12로 저장).
        _c1, _c2 = st.columns(2)
        _use_row = _c1.checkbox("파일의 집화일자를 발송일로 쓰기", value=bool(_dates),
                                key="du_rowdate", disabled=not _dates,
                                help="행마다 파일에 적힌 집화일자로 저장합니다. "
                                     "여러 날짜가 섞인 파일도 날짜별로 나뉘어 들어갑니다. "
                                     "끄면 아래에서 고른 날짜 하나로 전부 저장합니다.")
        _fix = _c2.checkbox("다른 날짜로 잘못 기록된 건 옮기기", value=bool(_dates),
                            key="du_fixdate",
                            help="같은 주문이 **같은 송장번호**로 다른 날짜에 기록돼 "
                                 "있으면 그 기록을 지우고 이 날짜로 옮깁니다. "
                                 "송장이 다르면 재발송이라 그대로 둡니다. "
                                 "끄면 그 날짜에도 남아 한 택배가 두 날에 잡힙니다.")
        _skip = st.checkbox("같은 날짜에 이미 있는 주문은 건너뛰기", value=True,
                            key="du_skip",
                            help="그 날짜에 이미 기록된 주문만 뺍니다.")
        _will = _new_total if _skip else _tot
        st.caption(f"저장 버튼을 누르면 최대 **{_will}건**이 기록됩니다"
                   + ("  ·  날짜는 **파일의 집화일자**를 씁니다"
                      if (_use_row and _dates) else
                      f"  ·  날짜는 **{_dd}** 하나로 저장합니다"))
        if st.button(f"💾 {_will}건 발송 기록 저장", key="du_save", type="primary",
                     disabled=not _by_user or _will <= 0):
            _det = []
            st.session_state['_du_detail'] = _det
            _saved, _skipped, _moved = save_dispatch(
                _by_user, str(_dd), detail=_det, skip_existing=bool(_skip),
                use_row_date=bool(_use_row and _dates),
                move_wrong_date=bool(_fix))
            _n = sum(_saved.values())
            st.session_state['_rs_rematch'] = True   # 발송건이 늘었다 — 매칭을 다시 돌린다
            st.session_state.pop('rs_alloc', None)
            st.session_state['_du_msg'] = (
                (f"✅ 발송 기록 {_n}건 저장 — "
                 + " · ".join(f"{dmap.get(u, u)} {c}건" for u, c in _saved.items()))
                if _n else
                "ℹ️ 새로 저장된 건이 없습니다 — 파일의 주문이 그 날짜에 모두 이미 "
                "기록돼 있습니다."
            ) + (f"  ·  🔁 다른 날짜에서 옮긴 건 {_moved}건" if _moved else "")               + (f"  ·  ⏭ 이미 있어 건너뜀 {_skipped}건" if _skipped else "")
            st.rerun()
