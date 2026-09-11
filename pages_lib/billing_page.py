"""📮 포장 관리 (관리자) — 택배·부자재비 청구 · 포장 단가 · 주문별 배정.

청구서는 여기서 만들지 않는다. 예전에는 이 화면이 주문 구입가를 따로 훑어
'일일 청구서'를 만들었고, 그 값이 영수증 정산의 청구액과 달라 화면마다
금액이 다른 원인이 됐다. 이제 청구액은 정산 원장(db_settle) 하나에서만 나온다.

물건값과 달리 택배비·포장부자재비는 **별도로 청구**한다. 그 금액을 모아 보는
곳이 여기다.
"""
import io
from datetime import date

import streamlit as st
import pandas as pd

from db import get_all_users, get_user_db, get_all_settings, set_setting
from db_packaging import (
    KIND_LABEL, list_packaging_prices, upsert_packaging_price, delete_packaging_price,
    get_order_packaging, set_order_packaging, clear_order_packaging,
)
from utils import fmt


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def _sellers():
    return [u['username'] for u in get_all_users()]


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    if not IS_ADMIN:
        st.error("관리자 전용 기능입니다.")
        return
    st.header("📮 포장 관리")
    st.caption("포장 단가와 주문별 배정만 다룹니다. 여기서 정한 포장비·택배비는 "
               "**그날 청구액에 자동으로 실립니다** — 청구·입금은 관리자 › 정산·청구에서 봅니다.")
    t0, t1, t2, t3 = st.tabs(
        ["💰 택배·부자재비 청구", "📦 포장 단가", "📮 주문 포장 배정", "👥 사용자 택배·포장비"])
    with t0:
        _tab_fee_billing(USERNAME)
    with t1:
        _tab_prices(USERNAME)
    with t2:
        _tab_assign(USERNAME)
    with t3:
        _tab_user_fees(USERNAME)


# ── 탭0: 택배·부자재비 청구 (월별) ──
def _tab_fee_billing(USERNAME):
    """물건값과 따로 청구하는 택배비·포장부자재비를 월 단위로 모아 본다.

    청구서(settle_invoice)에는 물건값만 싣는다. 그래서 택배·부자재비는 어디에도
    합계가 없어 매달 손으로 세야 했다. 직접구매 계정처럼 청구서 자체가 안 만들어지는
    사용자는 볼 화면조차 없었다.

    택배비는 발송 기록에서 자동으로 나온다(발송건수 × 사용자 택배비).
    포장부자재비는 관리자가 직접 적는다 — 실제로 쓰는 부자재는 주문마다 다르고
    한 번에 사 두었다 나눠 쓰는 것이라, 건별 배정보다 "이번 달 이 사람 몫은 얼마"를
    사람이 정하는 편이 현실에 맞는다.
    """
    import calendar as _cal
    from datetime import date as _date
    import settle_core as _sc
    from db_packaging import get_packaging_fees, set_packaging_fee

    st.subheader("💰 택배·부자재비 청구")
    st.caption("물건값 청구서와 **별도로** 청구하는 금액입니다. "
               "택배비는 발송 기록에서 자동 계산되고, **포장부자재비는 직접 입력**합니다.")

    _today = _date.today()
    _yms = []
    _y, _m = _today.year, _today.month
    for _ in range(12):
        _yms.append('%04d-%02d' % (_y, _m))
        _m -= 1
        if _m == 0:
            _y, _m = _y - 1, 12
    c1, c2 = st.columns([1, 3])
    _ym = c1.selectbox("청구 월", _yms, key="fb_ym")
    _last = _cal.monthrange(int(_ym[:4]), int(_ym[5:7]))[1]

    dmap = _disp_map()
    _saved = get_packaging_fees(_ym)

    with st.spinner(f"{_ym} 발송 기록을 모으는 중..."):
        _rows = []
        for u in _sellers():
            _cnt = _ship = 0
            _unit = 0
            for _d in range(1, _last + 1):
                _f = _sc.daily_fees(u, '%s-%02d' % (_ym, _d))
                _cnt += int(_f.get('ship_count') or 0)
                _ship += int(_f.get('ship_fee') or 0)
                _unit = int(_f.get('ship_unit') or 0) or _unit
            _pf = _saved.get(u) or {}
            if not _cnt and not int(_pf.get('amount') or 0):
                continue          # 그달 아무 일도 없던 사용자는 줄을 만들지 않는다
            _rows.append({
                '판매자': dmap.get(u, u),
                '발송건수': _cnt,
                '택배단가': _unit,
                '택배비': _ship,
                '포장부자재비': int(_pf.get('amount') or 0),
                '합계': _ship + int(_pf.get('amount') or 0),
                '메모': str(_pf.get('memo') or ''),
                '_u': u,
            })

    if not _rows:
        st.info(f"{_ym} 발송 기록도 입력된 부자재비도 없습니다.")
        return

    _rows.sort(key=lambda r: -r['합계'])
    _ed = st.data_editor(
        pd.DataFrame(_rows), use_container_width=True, hide_index=True,
        key=f"fb_ed_{_ym}",
        disabled=['판매자', '발송건수', '택배단가', '택배비', '합계', '_u'],
        column_config={
            '포장부자재비': st.column_config.NumberColumn(
                '포장부자재비', format='%d', min_value=0, step=1000,
                help='관리자가 직접 적습니다. 저장해야 반영됩니다.'),
            '메모': st.column_config.TextColumn('메모', help='청구 근거 메모 (선택)'),
            '_u': None,
            **{_k: st.column_config.NumberColumn(_k, format='%d')
               for _k in ('발송건수', '택배단가', '택배비', '합계')},
        })

    _recs = _ed.to_dict('records')
    _t_ship = sum(int(r['택배비'] or 0) for r in _recs)
    _t_pack = sum(int(r['포장부자재비'] or 0) for r in _recs)
    st.markdown(f"**{_ym} 합계** — 택배비 **{fmt(_t_ship)}원** + 부자재비 "
                f"**{fmt(_t_pack)}원** = **{fmt(_t_ship + _t_pack)}원** "
                f"({len(_recs)}명)")
    st.caption("합계 칸은 저장 후 다시 계산됩니다 — 부자재비를 고치면 저장을 누르세요.")

    b1, b2 = st.columns([1, 3])
    if b1.button("💾 부자재비 저장", type="primary", key=f"fb_save_{_ym}"):
        _n = 0
        for r in _recs:
            _old = int((_saved.get(str(r['_u'])) or {}).get('amount') or 0)
            _oldm = str((_saved.get(str(r['_u'])) or {}).get('memo') or '')
            if int(r['포장부자재비'] or 0) != _old or str(r['메모'] or '') != _oldm:
                set_packaging_fee(str(r['_u']), _ym, int(r['포장부자재비'] or 0),
                                  memo=str(r['메모'] or ''), updated_by=USERNAME)
                _n += 1
        st.success(f"✅ {_n}명 부자재비 저장" if _n else "변경된 내용이 없습니다.")
        st.rerun()

    try:
        _buf = io.BytesIO()
        _out = pd.DataFrame([{k: v for k, v in r.items() if k != '_u'} for r in _recs])
        with pd.ExcelWriter(_buf, engine='openpyxl') as _xw:
            _out.to_excel(_xw, index=False, sheet_name='택배부자재비')
        _buf.seek(0)
        b2.download_button(f"📥 {_ym} 택배·부자재비 엑셀", data=_buf.getvalue(),
                           file_name=f"fee_{_ym}.xlsx",
                           mime=("application/vnd.openxmlformats-officedocument"
                                 ".spreadsheetml.sheet"), key=f"fb_xls_{_ym}")
    except Exception as _e:
        b2.caption(f"엑셀 생성 실패: {_e}")

    with st.expander("📅 날짜별 발송 내역 (청구 근거)", expanded=False):
        _u2 = st.selectbox("판매자", [r['_u'] for r in _recs],
                           format_func=lambda u: dmap.get(u, u), key=f"fb_det_{_ym}")
        _dd = []
        for _d in range(1, _last + 1):
            _ds2 = '%s-%02d' % (_ym, _d)
            _f = _sc.daily_fees(_u2, _ds2)
            if _f.get('ship_count'):
                _dd.append({'날짜': _ds2, '발송건수': int(_f['ship_count']),
                            '택배비': int(_f['ship_fee']),
                            '건별지정': int(_f.get('ship_custom') or 0)})
        if _dd:
            st.dataframe(pd.DataFrame(_dd), use_container_width=True, hide_index=True,
                         column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                                        for _k in ('발송건수', '택배비', '건별지정')})
        else:
            st.caption("그달 발송 기록이 없습니다.")

    _tab_ship_per_order(USERNAME, dmap, _ym, _last, [r['_u'] for r in _recs])


def _tab_ship_per_order(USERNAME, dmap, ym, last_day, users):
    """건별 택배비 — 부피가 크면 요금이 다르다.

    사용자 단일 단가(발송건수 × shipping_cost)로는 맞출 수가 없다. 큰 박스 하나가
    섞인 날은 실제 낸 택배비와 청구액이 어긋나고, 그 차액은 그대로 손실이다.
    여기서 지정한 건은 그 금액으로 계산되고, 비워 두면 기본 단가를 쓴다.
    """
    from datetime import date as _date
    import settle_core as _sc
    from db_packaging import get_ship_cost_map, set_order_ship_cost
    from db import get_dispatched_orders_with_details, get_all_settings

    with st.expander("🚚 건별 택배비 수정 (부피에 따라 다를 때)", expanded=False):
        if not users:
            st.caption("대상 판매자가 없습니다.")
            return
        c1, c2 = st.columns([2, 1.4])
        _u = c1.selectbox("판매자", users, format_func=lambda u: dmap.get(u, u),
                          key=f"fbso_u_{ym}")
        _days = []
        for _d in range(1, last_day + 1):
            _ds = '%s-%02d' % (ym, _d)
            if _sc.daily_fees(_u, _ds).get('ship_count'):
                _days.append(_ds)
        if not _days:
            st.caption("그달 발송 기록이 없습니다.")
            return
        _day = c2.selectbox("발송일", _days, index=len(_days) - 1, key=f"fbso_d_{ym}")

        _unit = int((get_all_settings(_u) or {}).get('shipping_cost') or 2000)
        try:
            _rows0 = get_dispatched_orders_with_details(_u, _day) or []
        except Exception as _e:
            st.error(f"발송 내역 조회 실패: {_e}")
            return
        _onos = sorted({str(r.get('order_no') or '') for r in _rows0 if r.get('order_no')})
        _cur = get_ship_cost_map(_u, _onos) or {}
        _by_ono = {}
        for r in _rows0:
            _by_ono.setdefault(str(r.get('order_no') or ''), r)

        st.caption(f"기본 단가 **{fmt(_unit)}원** — 비워 두면(0) 기본 단가로 계산됩니다. "
                   "부피가 커서 요금이 다른 건만 적으세요.")
        _rows = [{
            '수취인': str(_by_ono.get(o, {}).get('recipient') or '')[:10],
            '상품명': str(_by_ono.get(o, {}).get('product_name') or '')[:34],
            '택배비': int(_cur.get(o, 0)),
            '적용액': int(_cur.get(o) or _unit),
            '주문번호': o,
        } for o in _onos]
        _ed = st.data_editor(
            pd.DataFrame(_rows), use_container_width=True, hide_index=True,
            key=f"fbso_ed_{ym}_{_u}_{_day}",
            disabled=['수취인', '상품명', '적용액', '주문번호'],
            column_config={
                '택배비': st.column_config.NumberColumn(
                    '택배비(건별)', format='%d', min_value=0, step=100,
                    help='0이면 기본 단가를 씁니다. 부피 큰 건만 실제 낸 금액을 적으세요.'),
                '적용액': st.column_config.NumberColumn(
                    '현재 적용액', format='%d', help='저장 후 다시 계산됩니다.'),
            })
        _recs2 = _ed.to_dict('records')
        _sum = sum(int(r['택배비'] or 0) or _unit for r in _recs2)
        st.markdown(f"**{_day}** 택배비 합계 **{fmt(_sum)}원** ({len(_recs2)}건) "
                    f"· 건별 지정 {sum(1 for r in _recs2 if int(r['택배비'] or 0) > 0)}건")
        if st.button("💾 건별 택배비 저장", type="primary", key=f"fbso_save_{ym}_{_u}_{_day}"):
            _n = 0
            for r in _recs2:
                _o = str(r['주문번호'])
                if int(r['택배비'] or 0) != int(_cur.get(_o, 0)):
                    set_order_ship_cost(_u, _o, int(r['택배비'] or 0), updated_by=USERNAME)
                    _n += 1
            st.success(f"✅ {_n}건 저장" if _n else "변경된 내용이 없습니다.")
            st.rerun()


# ── 탭1: 포장 단가 설정 ──
def _tab_prices(USERNAME):
    st.subheader("📦 포장 항목 단가")
    st.caption("박스(사이즈별 여러 개)·아이스박스·아이스팩·기타 항목의 단가를 등록합니다. 주문 포장 배정에서 사용됩니다.")

    with st.form("pkg_add", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1.2, 2, 1.2, 0.8])
        kind = c1.selectbox("유형", options=list(KIND_LABEL.keys()),
                            format_func=lambda k: KIND_LABEL[k], key="pkg_kind")
        name = c2.text_input("이름 (예: 박스 M, 아이스팩 소)", key="pkg_name")
        price = c3.number_input("단가(원)", min_value=0, step=100, key="pkg_price")
        c4.markdown("<br>", unsafe_allow_html=True)
        if c4.form_submit_button("➕ 추가", use_container_width=True):
            if name.strip():
                upsert_packaging_price(name.strip(), int(price), kind)
                st.success(f"✅ {KIND_LABEL[kind]} '{name}' 추가")
                st.rerun()
            else:
                st.warning("이름을 입력하세요.")

    rows = list_packaging_prices()
    if not rows:
        st.info("등록된 포장 항목이 없습니다. 위에서 추가하세요.")
        return
    for kind in KIND_LABEL:
        krows = [r for r in rows if r['kind'] == kind]
        if not krows:
            continue
        st.markdown(f"**{KIND_LABEL[kind]}**")
        for r in krows:
            c1, c2, c3, c4 = st.columns([2.5, 1.5, 1, 1])
            c1.write(r['name'])
            _newp = c2.number_input("단가", min_value=0, step=100, value=int(r['price'] or 0),
                                    key=f"pkg_p_{r['id']}", label_visibility="collapsed")
            if c3.button("💾", key=f"pkg_s_{r['id']}", help="단가 저장"):
                upsert_packaging_price(r['name'], int(_newp), r['kind'], item_id=r['id'],
                                       active=r['active'], sort_order=r['sort_order'])
                st.rerun()
            if c4.button("🗑", key=f"pkg_d_{r['id']}", help="삭제"):
                delete_packaging_price(r['id'])
                st.rerun()


# ── 탭2: 주문별 포장 배정 ──
def _tab_assign(USERNAME):
    st.subheader("📮 주문 포장 배정")
    st.caption("발송할 주문마다 박스·아이스 옵션을 선택하면 포장비가 계산되어, 해당 판매자 수익계산의 '박스원가'로 반영됩니다.")

    boxes = list_packaging_prices(kind='box', active_only=True)
    if not boxes:
        st.warning("먼저 '포장 단가' 탭에서 박스 항목을 등록하세요.")
        return
    prices = list_packaging_prices(active_only=True)
    _icebox_price = next((int(p['price'] or 0) for p in prices if p['kind'] == 'icebox'), 0)
    _icepack_price = next((int(p['price'] or 0) for p in prices if p['kind'] == 'icepack'), 0)

    dmap = _disp_map()
    c1, c2 = st.columns(2)
    seller = c1.selectbox("판매자", options=_sellers(), format_func=lambda u: dmap.get(u, u), key="asg_seller")
    d = c2.date_input("주문일", value=date.today(), key="asg_date")

    try:
        conn = get_user_db(seller)
        ords = conn.execute(
            "SELECT order_no, recipient, product_name, qty FROM order_history "
            "WHERE order_date=? ORDER BY recipient", (str(d),)).fetchall()
        conn.close()
    except Exception as e:
        st.error(f"주문 조회 오류: {e}")
        return
    if not ords:
        st.info(f"{dmap.get(seller, seller)} · {d} 주문이 없습니다.")
        return

    _box_opts = {b['id']: f"{b['name']} ({fmt(b['price'])}원)" for b in boxes}
    _box_ids = [0] + [b['id'] for b in boxes]   # 0 = 미지정
    st.caption(f"📦 {dmap.get(seller, seller)} · {d} · 주문 {len(ords)}건  |  아이스박스 {fmt(_icebox_price)}원 · 아이스팩 {fmt(_icepack_price)}원")

    for o in ords:
        ono = str(o['order_no'])
        cur = get_order_packaging(seller, ono) or {}
        with st.container():
            c0, c1, c2, c3, c4 = st.columns([3, 1.6, 1, 1, 1])
            c0.markdown(f"**{o['recipient']}** · {str(o['product_name'])[:26]} ×{o['qty']}")
            _cur_box = cur.get('box_id') or 0
            _bsel = c1.selectbox("박스", options=_box_ids,
                                 format_func=lambda i: "미지정" if i == 0 else _box_opts.get(i, str(i)),
                                 index=_box_ids.index(_cur_box) if _cur_box in _box_ids else 0,
                                 key=f"asg_box_{ono}", label_visibility="collapsed")
            _ibx = c2.number_input("아이스박스", min_value=0, step=1, value=int(cur.get('icebox_qty') or 0),
                                   key=f"asg_ibx_{ono}", label_visibility="collapsed")
            _ipk = c3.number_input("아이스팩", min_value=0, step=1, value=int(cur.get('icepack_qty') or 0),
                                   key=f"asg_ipk_{ono}", label_visibility="collapsed")
            _bp = next((int(b['price'] or 0) for b in boxes if b['id'] == _bsel), 0)
            _tot = _bp + int(_ibx) * _icebox_price + int(_ipk) * _icepack_price
            c4.markdown(f"= **{fmt(_tot)}**")
        # 변경 즉시 저장 (선택값이 저장값과 다르면)
        if (_bsel or 0) != (cur.get('box_id') or 0) or int(_ibx) != int(cur.get('icebox_qty') or 0) \
                or int(_ipk) != int(cur.get('icepack_qty') or 0):
            if _bsel or _ibx or _ipk:
                set_order_packaging(seller, ono, box_id=_bsel or None,
                                    icebox_qty=int(_ibx), icepack_qty=int(_ipk), updated_by=USERNAME)
            else:
                clear_order_packaging(seller, ono)


# ── 탭4: 사용자 택배비·포장비 (관리자 지정) ──
def _tab_user_fees(USERNAME):
    st.subheader("👥 사용자 택배비·포장비 (관리자 지정)")
    st.caption("각 판매자의 기본 택배비·포장비(박스비)를 관리자가 지정합니다. "
               "지정한 값이 그 판매자 수익계산의 기본 택배원가·박스원가로 반영됩니다. "
               "(주문별 포장 배정이 있으면 박스원가는 그 값이 우선)")
    dmap = _disp_map()
    h0, h1, h2, h3 = st.columns([2, 1.3, 1.3, 0.8])
    h0.caption("판매자")
    h1.caption("택배비(원)")
    h2.caption("포장비(원)")
    h3.caption("저장")
    for u in _sellers():
        s = get_all_settings(u) or {}
        try:
            cur_ship = int(s.get('shipping_cost') or 2000)
        except (TypeError, ValueError):
            cur_ship = 1800
        try:
            cur_box = int(s.get('box_cost') or 300)
        except (TypeError, ValueError):
            cur_box = 300
        c0, c1, c2, c3 = st.columns([2, 1.3, 1.3, 0.8])
        c0.markdown(f"**{dmap.get(u, u)}**  ·  `{u}`")
        ship = c1.number_input("택배비", min_value=0, step=100, value=cur_ship,
                               key=f"fee_ship_{u}", label_visibility="collapsed")
        box = c2.number_input("포장비", min_value=0, step=100, value=cur_box,
                              key=f"fee_box_{u}", label_visibility="collapsed")
        if c3.button("💾", key=f"fee_save_{u}", help="이 판매자 택배·포장비 저장"):
            set_setting(u, 'shipping_cost', int(ship))
            set_setting(u, 'box_cost', int(box))
            st.success(f"✅ {dmap.get(u, u)} 저장 — 택배 {fmt(int(ship))} · 포장 {fmt(int(box))}")
            st.rerun()
