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
import sqlite3

from db_core import DATA_DIR

#: 업로드 파일에서 각 항목을 찾을 때 볼 헤더 후보 (앞에 있을수록 우선)
COLUMN_HINTS = {
    'order_no':    ('상품주문번호', '주문번호', '주문 번호', 'order_no', 'orderid', 'order id'),
    'tracking_no': ('송장번호', '운송장번호', '운송장', 'tracking', 'invoice_no'),
    'recipient':   ('수취인명', '수취인', '받는분', '수령인', 'recipient'),
    'product_name': ('상품명', '제품명', 'product', 'item'),
    'qty':         ('수량', 'qty', 'quantity'),
    'courier':     ('택배사', '배송사', 'courier'),
    'dispatched_at': ('발송일', '출고일', '발송처리일', '배송일'),
}


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
    dup = {o: sorted(us) for o, us in seen.items() if len(us) > 1}
    return idx, dup


def guess_columns(headers):
    """업로드 파일 헤더 → {항목: 열이름}. 못 찾으면 그 항목은 빠진다."""
    _h = [str(h) for h in (headers or [])]
    _low = {h: str(h).strip().lower().replace(' ', '') for h in _h}
    out = {}
    for key, hints in COLUMN_HINTS.items():
        for hint in hints:
            _hi = hint.lower().replace(' ', '')
            # 완전일치 우선, 없으면 부분일치
            hit = next((h for h in _h if _low[h] == _hi), None) \
                or next((h for h in _h if _hi in _low[h]), None)
            if hit:
                out[key] = hit
                break
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
    by_user, unknown = {}, []
    for i, rec in enumerate(records or []):
        def _g(key):
            col = _c.get(key)
            return rec.get(col) if col else None

        ono = _norm_no(_g('order_no'))
        item = {
            'order_no': ono,
            'recipient': str(_g('recipient') or '').strip(),
            'product_name': str(_g('product_name') or '').strip(),
            'tracking_no': _norm_no(_g('tracking_no')),
            'courier': str(_g('courier') or '').strip(),
            '_row': i + 1,
        }
        try:
            item['qty'] = max(1, int(float(_g('qty') or 1)))
        except (TypeError, ValueError):
            item['qty'] = 1
        u = owner_index.get(ono) if ono else None
        if u:
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


def save_dispatch(by_user, dispatched_at, platform='upload', skip_existing=True):
    """분류 결과를 dispatch_log에 기록. 반환: {username: 저장건수}, 건너뛴 수."""
    from db import log_dispatch_success
    saved, skipped = {}, 0
    for uname, rows in (by_user or {}).items():
        _rows = rows
        if skip_existing:
            _ex = existing_dispatch(uname, [r['order_no'] for r in rows])
            _rows = [r for r in rows if r['order_no'] not in _ex]
            skipped += len(rows) - len(_rows)
        if not _rows:
            continue
        try:
            n = log_dispatch_success(uname, _rows, str(dispatched_at), platform=platform)
        except Exception:
            n = 0
        if n:
            saved[uname] = n
    return saved, skipped


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

    with st.expander("🚚 발송 파일 업로드 — 주문번호로 사용자 분류", expanded=False):
        st.caption(
            "전체 발송내역 파일(엑셀·CSV)을 올리면 **주문번호**로 각 사용자에게 나눠 "
            "발송 기록에 넣습니다. 이후 영수증 정산이 그 발송건에 매입가를 채웁니다. "
            "**여러 개를 한 번에** 올릴 수 있고, 같은 파일을 두 번 올려도 "
            "중복 저장되지 않습니다.")
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

        _STD = ('order_no', 'tracking_no', 'recipient', 'product_name', 'qty', 'courier')
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
        st.markdown("**열 매핑** — 자동으로 찾은 값이 맞는지 확인하세요")
        _m1, _m2, _m3 = st.columns(3)
        _opts = ['(없음)'] + [str(c) for c in _cols]

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
        }
        if not _cm.get('order_no'):
            st.error("⚠️ **주문번호** 열을 지정해야 분류할 수 있습니다.")
            return

        _dd = st.date_input("발송일 (청구 귀속일)", value=date.today(), key="du_date",
                            help="이 날짜로 발송 기록이 남고, 그날 청구에 잡힙니다.")

        _idx, _dup = build_order_owner_index()
        if _dup:
            st.warning(f"⚠️ 두 사용자에 걸친 주문번호 {len(_dup)}건이 있습니다 — "
                       "그 건은 분류가 부정확할 수 있습니다.")
        _by_user, _unknown = classify_rows(_df.to_dict('records'), _cm, _idx)

        _tot = sum(len(v) for v in _by_user.values())
        st.markdown(f"### 분류 결과 — {_tot}건 매칭 · {len(_unknown)}건 미분류")
        if _by_user:
            _sum = []
            for _u, _rows in sorted(_by_user.items(), key=lambda kv: -len(kv[1])):
                _ex = existing_dispatch(_u, [r['order_no'] for r in _rows])
                _sum.append({'사용자': dmap.get(_u, _u), '건수': len(_rows),
                             '이미 발송기록 있음': len(_ex),
                             '새로 저장될 건': len(_rows) - len(_ex)})
            st.dataframe(pd.DataFrame(_sum), use_container_width=True, hide_index=True)
        if _unknown:
            with st.expander(f"❓ 미분류 {len(_unknown)}건 — 어느 사용자 것인지 못 찾음",
                             expanded=False):
                st.caption("주문번호가 비었거나, 어느 사용자 DB에도 없는 번호입니다. "
                           "주문 수집이 안 된 건일 수 있습니다.")
                st.dataframe(pd.DataFrame([{
                    '행': u['_row'], '주문번호': u['order_no'], '수취인': u['recipient'],
                    '상품명': u['product_name'][:34], '사유': u.get('_why', '')}
                    for u in _unknown[:200]]), use_container_width=True, hide_index=True)

        _skip = st.checkbox("이미 발송 기록이 있는 주문은 건너뛰기", value=True,
                            key="du_skip",
                            help="끄면 같은 주문의 발송일을 이 날짜로 덮어씁니다.")
        if st.button(f"💾 {_tot}건 발송 기록 저장", key="du_save", type="primary",
                     disabled=not _by_user):
            _saved, _skipped = save_dispatch(_by_user, str(_dd),
                                                skip_existing=bool(_skip))
            _n = sum(_saved.values())
            st.session_state['_du_msg'] = (
                f"✅ 발송 기록 {_n}건 저장 — "
                + " · ".join(f"{dmap.get(u, u)} {c}건" for u, c in _saved.items())
                + (f"  ·  ⏭ 이미 있어 건너뜀 {_skipped}건" if _skipped else ""))
            st.rerun()
