"""🧾 내 구매내역 정산 — 각 사용자가 자기 일별 구매금액(청구액)과 변경 고지를 확인.

관리자가 '구매내역 정산'에서 확정하면, 영수증 실단가 반영으로 바뀐 금액이 여기 배지로 뜬다.
매일=구매가만. (월말 택배·포장 추가는 예정.)
"""
from datetime import date

import streamlit as st
import pandas as pd

from db_purchase_settle import (
    compute_daily_purchase, get_snapshot, get_user_badge, month_fees_if_last_day,
)
from utils import fmt


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    st.header("🧾 내 구매내역 정산")
    st.caption("코스트코 구매대행 **구매금액(청구액)**입니다. 매일은 구매가 기준이며, "
               "관리자가 영수증을 반영해 확정하면 실단가로 갱신되고 변경이 아래에 표시됩니다.")

    d = st.date_input("날짜", value=date.today())
    ds = str(d)

    items, cur_total = compute_daily_purchase(USERNAME, ds)
    snap = get_snapshot(ds, USERNAME)
    badge = get_user_badge(ds, USERNAME)
    is_final = bool(snap and snap.get('status') == 'final')

    if not items and not snap:
        st.info(f"{ds} 구매내역이 없습니다.")
        return

    # 구매가: 확정되면 확정액, 아니면 현재(예상) 계산액
    goods_total = int(snap['final_total']) if is_final else cur_total
    status_txt = "✅ 확정 (영수증 반영)" if is_final else ("🕐 예상 (영수증 반영 전)" if snap else "🕐 예상")

    # 월말(말일)이면 그달 1일~말일 택배·포장 누적 추가
    fees = month_fees_if_last_day(USERNAME, ds)
    charge_total = goods_total + (fees['fees_total'] if fees else 0)

    c1, c2 = st.columns([1.3, 3])
    c1.metric("청구액", f"{fmt(charge_total)}원")
    c2.caption(f"상태: **{status_txt}**  ·  구매 {len(items)}건  ·  {ds}"
               + ("  ·  📦 **말일 정산(택배·포장 포함)**" if fees else "  ·  매일=구매가만"))

    if fees:
        st.info(
            f"📦 **말일 정산** — 구매가 {fmt(goods_total)}원 "
            f"+ 그달 택배·포장 {fmt(fees['fees_total'])}원 "
            f"(택배 {fees['ship_count']}건 × {fmt(fees['ship_fee'])} = {fmt(fees['ship_total'])} · "
            f"포장 실배정 {fmt(fees['pkg_total'])})  =  **청구액 {fmt(charge_total)}원**")

    # ── 변경 고지 배지 (확정 & 예상과 다를 때) ──
    if badge:
        _diff = badge['diff']
        _arrow = "🔺" if _diff > 0 else "🔻"
        st.warning(
            f"{_arrow} **실단가 반영으로 금액이 변경되었습니다** — "
            f"확정 {fmt(badge['final_total'])}원 "
            f"(예상 {fmt(badge['est_total'])}원 대비 **{_diff:+,}원**), 변경 {len(badge['changed'])}건")
        if badge['changed']:
            st.dataframe(pd.DataFrame([{
                '상품': c['product_name'], '예상': fmt(c['prev']),
                '확정': fmt(c['now']), '차액': f"{c['diff']:+,}",
            } for c in badge['changed']]), use_container_width=True, hide_index=True)

    st.divider()
    # ── 구매내역 상세 ──
    st.markdown("#### 구매 상세")
    _df = pd.DataFrame([{
        '수취인': it['recipient'],
        '상품명': it['product_name'],
        '수량': it['qty'],
        '구매단가': fmt(it['unit_price']),
        '구매금액': fmt(it['amount']),
    } for it in items])
    st.dataframe(_df, use_container_width=True, hide_index=True)

    _matched = sum(1 for it in items if it['amount'] > 0)
    if _matched < len(items):
        st.caption(f"ℹ️ 구매가 미등록 {len(items) - _matched}건은 0원으로 표시됩니다 "
                   "(관리자가 구매가/영수증 반영 시 갱신).")
