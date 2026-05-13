"""💳 정산 매칭 페이지 — UI만 담당.

비즈니스 로직(매칭/대조)은 settlement_service.py, API는 naver_api.py, DB는 db_settlements 모듈.
"""
import json
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd

from db import (
    save_naver_settlements, get_naver_settlements_by_date,
    delete_naver_settlements_by_date,
    search_order_history,
)
from settlement_service import (
    match_shipped_vs_settled, shipped_orders_from_db_rows,
)
from utils import fmt

try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False
    naver_api = None


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    """💳 정산 매칭 — 전날 발송건 vs 정산내역 대조."""
    def _gs(k, default=""):
        return settings.get(k) or default

    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")

    st.header("💳 정산 매칭")
    st.caption("네이버 커머스 API로 정산 내역을 수집하고 발송건과 매칭하여 누락·차액을 확인합니다.")

    if not HAS_NAVER_API:
        st.error("naver_api.py 가 로드되지 않았습니다.")
        return
    if not api_id or not api_secret:
        st.warning("⚙️ 설정에서 네이버 커머스 API 키를 먼저 입력하세요.")
        return

    # ── 날짜 선택 + 수집 버튼 ────────────────────────────────────
    _c1, _c2, _c3, _c4 = st.columns([1.5, 1.5, 1, 1])
    with _c1:
        settle_date = st.date_input(
            "정산일", value=datetime.today() - timedelta(days=1),
            help="이 날짜의 정산 내역을 수집합니다 (네이버 기준 정산일)"
        )
        settle_date_str = settle_date.strftime("%Y-%m-%d")
    with _c2:
        ship_date = st.date_input(
            "매칭 대상 발송일", value=datetime.today() - timedelta(days=2),
            help="이 날짜의 발송건과 정산을 대조합니다 (보통 정산일 -1일)"
        )
        ship_date_str = ship_date.strftime("%Y-%m-%d")
    with _c3:
        st.write("")
        st.write("")
        fetch_clicked = st.button("📥 정산 수집", type="primary", use_container_width=True)
    with _c4:
        st.write("")
        st.write("")
        debug_clicked = st.button("🔍 응답 디버그", use_container_width=True)

    # ── 디버그: raw API 응답 ────────────────────────────────────
    if debug_clicked:
        with st.spinner("API 응답 조회 중..."):
            raw, err = naver_api.debug_settlement_response(api_id, api_secret, settle_date_str)
        if err:
            st.error(f"❌ {err}")
        else:
            st.json(raw)
        return

    # ── 정산 수집 ────────────────────────────────────────────────
    if fetch_clicked:
        with st.spinner(f"{settle_date_str} 정산 내역 조회 중..."):
            records, err = naver_api.get_settlement_history(
                api_id, api_secret, settle_date_str, settle_date_str
            )
        if err:
            st.error(f"❌ 정산 조회 실패: {err}")
            return
        if not records:
            st.info(f"{settle_date_str}에 정산된 주문이 없습니다.")
        else:
            # 기존 같은 날짜 정산 제거 후 재저장 (멱등성)
            delete_naver_settlements_by_date(USERNAME, settle_date_str)
            saved = save_naver_settlements(USERNAME, settle_date_str, records)
            st.success(f"✅ {settle_date_str} 정산 {saved}건 저장됨")

    # ── 매칭 결과 표시 ───────────────────────────────────────────
    st.divider()
    st.subheader(f"📊 매칭 결과 — 발송일 {ship_date_str} ↔ 정산일 {settle_date_str}")

    # DB에서 양쪽 데이터 로드
    settled_rows = get_naver_settlements_by_date(USERNAME, settle_date_str)
    shipped_rows = search_order_history(USERNAME, date_from=ship_date_str, date_to=ship_date_str, limit=5000)

    if not settled_rows and not shipped_rows:
        st.info("선택한 날짜에 정산 내역과 발송건이 모두 없습니다. 먼저 정산 수집을 눌러주세요.")
        return

    # 비즈니스 로직: 순수 매칭 함수 호출
    shipped_dicts = shipped_orders_from_db_rows(shipped_rows)
    # DB rows를 매칭 함수가 기대하는 키로 변환
    settled_dicts = [{
        'product_order_no': r['product_order_no'],
        'order_no':         r.get('order_no', ''),
        'settle_amount':    r.get('settle_amount', 0),
        'sales_amount':     r.get('sales_amount', 0),
        'commission':       r.get('commission', 0),
    } for r in settled_rows]

    result = match_shipped_vs_settled(shipped_dicts, settled_dicts)
    s = result['summary']

    # 요약 카드
    _m1, _m2, _m3, _m4, _m5 = st.columns(5)
    _m1.metric("발송", f"{s['shipped_n']}건")
    _m2.metric("정산", f"{s['settled_n']}건")
    _m3.metric("✅ 일치", f"{s['matched_n']}건")
    _m4.metric("⚠️ 차액", f"{s['mismatched_n']}건",
               delta=fmt(s['total_diff']) + "원" if s['total_diff'] else None)
    _m5.metric("❌ 누락", f"{s['missing_n']}건")

    # 탭으로 구분 표시
    _t1, _t2, _t3, _t4 = st.tabs([
        f"⚠️ 차액 ({s['mismatched_n']})",
        f"❌ 누락 ({s['missing_n']})",
        f"✅ 일치 ({s['matched_n']})",
        f"🔍 정산만 ({s['orphan_n']})",
    ])
    with _t1:
        if result['mismatched']:
            df = pd.DataFrame(result['mismatched'])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.success("차액 없음.")
    with _t2:
        if result['missing']:
            df = pd.DataFrame(result['missing'])
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption("💡 발송했는데 정산되지 않은 주문 — 정산일이 다를 가능성도 있으니 다른 날짜도 확인해주세요.")
        else:
            st.success("누락 없음.")
    with _t3:
        if result['matched']:
            df = pd.DataFrame(result['matched'])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.caption("일치 항목 없음.")
    with _t4:
        if result['orphan']:
            df = pd.DataFrame(result['orphan'])
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption("💡 발송 기록은 없지만 정산된 주문 — 옛 발송분이거나 발송일 범위를 확장해야 매칭됩니다.")
        else:
            st.caption("해당 없음.")
