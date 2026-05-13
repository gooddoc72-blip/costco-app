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

    # ── 디버그: 모든 후보 path 동시 probe ─────────────────────────
    if debug_clicked:
        with st.spinner("정산 API 후보 경로들 probe 중..."):
            probes, err = naver_api.debug_settlement_response(
                api_id, api_secret, settle_date_str
            )
        if err:
            st.error(f"❌ {err}")
            return
        # 200 응답을 먼저 표시
        _hits = [(p, r) for p, r in (probes or {}).items() if r.get('status') == 200]
        _miss = [(p, r) for p, r in (probes or {}).items() if r.get('status') != 200]
        if _hits:
            st.success(f"✅ {len(_hits)}개 path가 200 응답 — 이 path를 정답으로 사용 가능")
            for p, r in _hits:
                with st.expander(f"✅ 200 — {p}", expanded=True):
                    st.json(r.get('body'))
        else:
            st.error("⚠️ 어떤 path도 200을 주지 않음. API 권한(정산 조회) 또는 path 자체 문제.")
        if _miss:
            with st.expander(f"❌ 실패한 path ({len(_miss)}개)", expanded=not _hits):
                for p, r in _miss:
                    st.text(f"[{r.get('status')}] {p} — {r.get('msg', '')}")
        return

    # ── 정산 수집 ────────────────────────────────────────────────
    if fetch_clicked:
        with st.spinner(f"{settle_date_str} 정산 내역 조회 중..."):
            records, err, used, attempts = naver_api.get_settlement_history(
                api_id, api_secret, settle_date_str, settle_date_str
            )
        if used:
            st.caption(f"🔗 사용된 endpoint: `{used}`")
        # 모든 시도 로그를 expander에 표시 (디버그 없이도 /case 실패 메시지 즉시 확인)
        if attempts:
            with st.expander(f"🔍 시도 로그 ({len(attempts)}건) — /case 400 본문 확인용", expanded=bool(err)):
                for line in attempts:
                    if line.startswith("✅"):
                        st.success(line)
                    else:
                        st.text(line)
        if err:
            st.error(f"❌ {err}")
            return
        if not records:
            st.info(f"{settle_date_str}에 정산된 주문이 없습니다. (응답 비어있음)")
        else:
            with st.expander(f"📋 응답 샘플 (첫 레코드) — 필드 확인용", expanded=False):
                st.json(records[0])
            delete_naver_settlements_by_date(USERNAME, settle_date_str)
            saved = save_naver_settlements(USERNAME, settle_date_str, records)
            if saved == 0 and len(records) > 0:
                st.warning(
                    f"⚠️ API에서 {len(records)}건 받았으나 저장 0건 — productOrderId 필드가 없는 응답일 가능성 "
                    f"(예: /daily 일별 합계). /case 응답이 필요합니다."
                )
            else:
                st.success(f"✅ {settle_date_str} 정산 {saved}건 저장됨 (API 응답 {len(records)}건)")

    # ── 매칭 결과 표시 ───────────────────────────────────────────
    st.divider()
    st.subheader(f"📊 매칭 결과 — 발송일 {ship_date_str} ↔ 정산일 {settle_date_str}")

    # DB에서 양쪽 데이터 로드
    settled_rows = get_naver_settlements_by_date(USERNAME, settle_date_str)
    _all_shipped = search_order_history(USERNAME, date_from=ship_date_str, date_to=ship_date_str, limit=5000)
    # 네이버 정산 매칭이므로 네이버 주문만 필터 (쿠팡 order_no는 '-' 포함 형식)
    shipped_rows = [r for r in _all_shipped if '-' not in str(r.get('order_no', ''))]
    _coupang_count = len(_all_shipped) - len(shipped_rows)
    if _coupang_count > 0:
        st.caption(f"💡 발송 {len(_all_shipped)}건 중 쿠팡 {_coupang_count}건 제외 → 네이버 {len(shipped_rows)}건 매칭 대상")

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
