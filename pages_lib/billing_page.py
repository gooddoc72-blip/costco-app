"""📮 포장 관리 (관리자) — 포장 단가 · 주문별 배정 · 사용자 택배·포장비.

청구서는 여기서 만들지 않는다. 예전에는 이 화면이 주문 구입가를 따로 훑어
'일일 청구서'를 만들었고, 그 값이 영수증 정산의 청구액과 달라 화면마다
금액이 다른 원인이 됐다. 이제 청구액은 정산 원장(db_settle) 하나에서만 나온다.
"""
from datetime import date

import streamlit as st

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
    t1, t2, t3 = st.tabs(
        ["📦 포장 단가", "📮 주문 포장 배정", "👥 사용자 택배·포장비"])
    with t1:
        _tab_prices(USERNAME)
    with t2:
        _tab_assign(USERNAME)
    with t3:
        _tab_user_fees(USERNAME)


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
            cur_ship = int(s.get('shipping_cost') or 1800)
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
