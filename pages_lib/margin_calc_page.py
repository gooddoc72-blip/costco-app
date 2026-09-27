"""🧮 마진계산기 (단독 메뉴) — 판매금액·고객배송비를 넣으면 마진금액·마진율이 나온다.

계산 방향은 한쪽뿐이다: 입력(원가·판매가) → 결과(마진).
네이버 등록 화면의 계산기는 마진율을 입력으로 받아 판매가를 역산해 칸에 다시
채우는데, 그래서 택배비만 고쳐도 판매가가 바뀌었다. 여기서는 판매금액을 사람이
정하고, 그 값을 절대 덮어쓰지 않는다. 판매금액·고객배송비를 움직이며 마진을 맞춘다.

공식은 pricing.margin_breakdown 하나 — 등록 화면·수익계산과 같은 수수료율을 쓴다.
"""
import streamlit as st

import pricing
from utils import fmt


def _i(v):
    try:
        return max(0, int(float(v or 0)))
    except (TypeError, ValueError):
        return 0


def render(USERNAME, IS_ADMIN, settings):
    st.header("🧮 마진계산기")
    st.caption("원가(매입금액·택배비·포장비)를 넣고, **판매금액·고객배송비**를 바꿔 가며 "
               "마진금액과 마진율을 맞추세요. 어떤 칸을 고쳐도 판매금액은 바뀌지 않습니다.")

    _pct = pricing.NAVER_FEE_RATE * 100
    _spct = pricing.NAVER_SHIP_FEE_RATE * 100

    with st.container(border=True):
        st.markdown("**① 원가 — 나가는 돈**")
        c1, c2, c3 = st.columns(3)
        cost = c1.number_input("매입금액", min_value=0, step=100, value=0, key="mcp_cost",
                               help="1개를 파는 데 드는 매입가입니다(소분이면 나눈 값).")
        ship = c2.number_input("택배비", min_value=0, step=100,
                               value=_i(settings.get('shipping_cost')), key="mcp_ship",
                               help="택배사에 내는 원가입니다. 기본값은 관리자가 정한 내 택배비입니다.")
        box = c3.number_input("포장비", min_value=0, step=50,
                              value=_i(settings.get('box_cost')), key="mcp_box",
                              help="기본값은 관리자가 정한 내 박스비입니다.")

        st.markdown("**② 판매 — 들어오는 돈**")
        d1, d2 = st.columns(2)
        sale = d1.number_input("판매금액", min_value=0, step=100, value=0, key="mcp_sale",
                               help="네이버 판매가입니다. 직접 정하세요 — 자동으로 바뀌지 않습니다.")
        cship = d2.number_input("고객배송비", min_value=0, step=500, value=0, key="mcp_cship",
                                help="구매자가 내는 배송비입니다. 무료배송이면 0. "
                                     f"네이버는 이 금액에도 {_spct:g}% 수수료를 뗍니다.")

    b = pricing.margin_breakdown(sale, cost, ship, box, customer_ship=cship)

    # ── ③ 결과 ──
    with st.container(border=True):
        st.markdown("**③ 결과**")
        if not sale:
            st.info("판매금액을 넣으면 마진이 계산됩니다.")
        else:
            _col = "#d32f2f" if b['margin'] < 0 else "#2e7d32"
            r1, r2 = st.columns(2)
            r1.markdown(
                "마진금액<br>"
                f"<span style='color:{_col};font-size:2em;font-weight:800'>"
                f"{fmt(b['margin'])}원</span>", unsafe_allow_html=True)
            r2.markdown(
                "마진율 (판매금액 대비)<br>"
                f"<span style='color:{_col};font-size:2em;font-weight:800'>"
                f"{b['margin_rate']:.1f}%</span>", unsafe_allow_html=True)
            if cost:
                st.caption(f"매입금액 대비 수익률 {b['margin'] / cost * 100:.1f}%")

            s1, s2 = st.columns(2)
            s1.metric(f"판매 정산금액 (수수료 {_pct:g}% 제외)", f"{fmt(sale - b['fee'])}원",
                      delta=f"-{fmt(b['fee'])}원", delta_color="inverse")
            s2.metric(f"고객배송비 정산금액 (수수료 {_spct:g}% 제외)",
                      f"{fmt(cship - b['ship_fee'])}원",
                      delta=(f"-{fmt(b['ship_fee'])}원" if cship else "무료배송"),
                      delta_color="inverse")

            _cs = f"+ 고객배송비 {fmt(cship)} " if cship else ""
            _csf = f"− 배송비수수료({_spct:g}%) {fmt(b['ship_fee'])} " if cship else ""
            st.caption(f"판매금액 {fmt(sale)} {_cs}− 판매수수료({_pct:g}%) {fmt(b['fee'])} "
                       f"{_csf}− 택배비 {fmt(ship)} − 포장비 {fmt(box)} − 매입금액 {fmt(cost)} "
                       f"= **{fmt(b['margin'])}원**")
            if not cost:
                st.caption("⚠️ 매입금액이 0입니다 — 넣어야 실제 마진이 나옵니다.")
            elif b['margin'] < 0:
                st.error("🔻 역마진입니다 — 이 가격이면 판매 건마다 손해입니다. "
                         "판매금액이나 고객배송비를 올려 보세요.")
