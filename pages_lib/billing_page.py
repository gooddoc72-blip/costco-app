"""📮 포장·청구 (관리자) — 포장 단가 설정 / 주문별 포장 배정 / 일일 판매자 청구서."""
from datetime import date

import streamlit as st
import pandas as pd

from db import get_all_users, get_user_db, get_all_settings, set_setting
from db_packaging import (
    KIND_LABEL, list_packaging_prices, upsert_packaging_price, delete_packaging_price,
    get_order_packaging, set_order_packaging, clear_order_packaging,
)
from db_receipt_settle import save_daily_billing, get_daily_billing, list_billing_dates
from utils import fmt


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def _sellers():
    return [u['username'] for u in get_all_users()]


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    if not IS_ADMIN:
        st.error("관리자 전용 기능입니다.")
        return
    st.header("📮 포장 · 청구")
    t1, t2, t3, t4 = st.tabs(
        ["📦 포장 단가", "📮 주문 포장 배정", "🧾 일일 청구서", "👥 사용자 택배·포장비"])
    with t1:
        _tab_prices(USERNAME)
    with t2:
        _tab_assign(USERNAME)
    with t3:
        _tab_billing(USERNAME)
    with t4:
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


# ── 탭3: 일일 판매자 청구서 ──
def _tab_billing(USERNAME):
    st.subheader("🧾 일일 판매자 청구서")
    st.caption("선택한 날짜의 각 판매자 주문 구매가(cost_price)를 합산해 청구액을 냅니다. (구매가만 — 결정하신 정책)")
    dmap = _disp_map()
    d = st.date_input("청구 날짜 (주문일 기준)", value=date.today(), key="bill_date")

    all_rows = []
    for u in _sellers():
        try:
            conn = get_user_db(u)
            ords = conn.execute(
                "SELECT order_no, recipient, product_name, qty, cost_price "
                "FROM order_history WHERE order_date=?", (str(d),)).fetchall()
            conn.close()
        except Exception:
            ords = []
        for o in ords:
            all_rows.append({'username': u, 'order_no': str(o['order_no']),
                             'recipient': o['recipient'], 'product_name': o['product_name'],
                             'qty': int(o['qty'] or 1), 'cost': int(o['cost_price'] or 0)})
    if not all_rows:
        st.info(f"{d} 주문이 없습니다.")
        return

    summary = {}
    for r in all_rows:
        s = summary.setdefault(r['username'], {'count': 0, 'amount': 0})
        s['count'] += 1
        s['amount'] += r['cost']

    st.markdown("### 판매자별 청구 요약")
    st.dataframe(pd.DataFrame([
        {'판매자': dmap.get(u, u), '주문수': s['count'], '청구액(구매가)': fmt(s['amount'])}
        for u, s in sorted(summary.items(), key=lambda kv: -kv[1]['amount'])
    ]), use_container_width=True, hide_index=True)
    _tot = sum(s['amount'] for s in summary.values())
    st.markdown(f"### 총 청구액: **{fmt(_tot)}원**  ·  판매자 {len(summary)}명  ·  주문 {len(all_rows)}건")

    # 저장 + 저장 상태
    _saved = {r['username']: r for r in get_daily_billing(str(d))}
    _sc1, _sc2 = st.columns([1.4, 3])
    if _sc1.button("💾 이 날짜 청구서 저장", type="primary", key="bill_save"):
        save_daily_billing(str(d), [
            {'username': u, 'order_count': s['count'], 'amount': s['amount']}
            for u, s in summary.items()], created_by=USERNAME)
        st.success(f"✅ {d} 청구서 저장 완료 (판매자 {len(summary)}명)")
        st.rerun()
    if _saved:
        _sc2.caption(f"💾 저장됨: {_saved[list(_saved)[0]]['created_at']} · 판매자 {len(_saved)}명")

    # 판매자별 상세 + 인쇄/엑셀
    for u, s in sorted(summary.items(), key=lambda kv: -kv[1]['amount']):
        urows = [r for r in all_rows if r['username'] == u]
        with st.expander(f"🧾 {dmap.get(u, u)} — 청구액 {fmt(s['amount'])}원 ({s['count']}건)", expanded=False):
            _df = pd.DataFrame([
                {'수취인': r['recipient'], '상품명': r['product_name'], '수량': r['qty'],
                 '구매가': r['cost']} for r in urows
            ])
            st.dataframe(_df.assign(구매가=_df['구매가'].map(fmt)),
                         use_container_width=True, hide_index=True)
            import io
            _buf = io.BytesIO()
            try:
                with pd.ExcelWriter(_buf, engine='openpyxl') as _xw:
                    _df.to_excel(_xw, index=False, sheet_name='청구서')
                _buf.seek(0)
                st.download_button("📥 엑셀 다운로드", data=_buf.getvalue(),
                                   file_name=f"청구서_{dmap.get(u, u)}_{d}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   key=f"bill_dl_{u}")
            except Exception:
                pass

    # ── 저장된 청구서 조회 ──
    st.divider()
    st.subheader("📚 저장된 청구서 조회")
    saved_dates = list_billing_dates(limit=60)
    if not saved_dates:
        st.caption("저장된 청구서가 없습니다. 위에서 '이 날짜 청구서 저장'을 눌러 보관하세요.")
        return
    for b in saved_dates:
        with st.expander(f"📅 {b['bill_date']} — 총 {fmt(b['total'])}원 · 판매자 {b['sellers']}명 · 저장 {b['at']}",
                         expanded=False):
            det = get_daily_billing(b['bill_date'])
            st.dataframe(pd.DataFrame([
                {'판매자': dmap.get(x['username'], x['username']),
                 '주문수': x['order_count'], '청구액': fmt(x['amount'])} for x in det
            ]), use_container_width=True, hide_index=True)
