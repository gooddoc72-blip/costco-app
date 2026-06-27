"""💳 정산 매칭 페이지 — UI만 담당.

비즈니스 로직(매칭/대조)은 settlement_service.py, API는 naver_api.py, DB는 db_settlements 모듈.
"""
import json
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd

from db import (
    save_naver_settlements, save_naver_settlements_from_csv,
    get_naver_settlements_by_date, delete_naver_settlements_by_date,
    search_order_history,
    get_dispatch_log_by_date, get_dispatch_dates, get_dispatch_by_order_nos,
    get_settled_product_order_nos, save_settlement_matches,
    apply_actual_settlements_to_profit,
    save_coupang_settlements, get_coupang_settlements_by_date, get_coupang_settle_dates,
    get_coupang_settled_map, get_dispatch_by_order_id, get_orders_by_order_ids,
)
try:
    import coupang_api
    HAS_COUPANG_API = True
except ImportError:
    HAS_COUPANG_API = False
    coupang_api = None
from settlement_service import (
    match_shipped_vs_settled, shipped_orders_from_db_rows,
    match_daily_total, analyze_shipping_commission,
    match_settled_to_dispatch, find_unsettled_dispatches,
    infer_purchase_decision,
)
from naver_settlement_parser import parse_naver_quicksettle_csv
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
    st.caption("네이버 정산 CSV 업로드 또는 API로 정산 내역을 수집하고 발송건과 매칭합니다.")

    # ══════════════════════════════════════════════════════════════════
    # ⭐ 네이버 정산 CSV 업로드 (QuickSettleByCase) — 가장 확실한 정답
    # ══════════════════════════════════════════════════════════════════
    with st.expander("📥 네이버 정산 CSV 업로드 (스마트스토어 → 정산관리 → 빠른정산 건별 다운로드)",
                     expanded=True):
        _csv_file = st.file_uploader(
            "QuickSettleByCase.csv (EUC-KR)", type=['csv'], key='csv_settle'
        )
        if _csv_file is not None:
            try:
                _parsed = parse_naver_quicksettle_csv(_csv_file.read())
            except Exception as _pe:
                st.error(f"❌ 파싱 실패: {_pe}")
                _parsed = []
            if _parsed:
                _saved = save_naver_settlements_from_csv(USERNAME, _parsed)
                # 요약 카드
                _cnt_quick = sum(1 for r in _parsed if r.get('settle_type') == '빠른정산')
                _cnt_claim = sum(1 for r in _parsed if r.get('settle_type') == '공제')
                _sum_prod  = sum(int(r.get('product_amount',0))  for r in _parsed)
                _sum_ship  = sum(int(r.get('shipping_amount',0)) for r in _parsed)
                _sum_tot   = sum(int(r.get('total_amount',0))    for r in _parsed)
                _u1, _u2, _u3, _u4 = st.columns(4)
                _u1.metric("주문 수", f"{len(_parsed)}건",
                           delta=f"빠른정산 {_cnt_quick} / 공제 {_cnt_claim}")
                _u2.metric("상품 정산", f"{fmt(_sum_prod)}원")
                _u3.metric("배송비 정산", f"{fmt(_sum_ship)}원")
                _u4.metric("총 정산", f"{fmt(_sum_tot)}원")
                st.success(f"✅ CSV 파싱 완료 — {_saved}건 DB 저장")
                with st.expander("📋 파싱 샘플 (첫 5건)", expanded=False):
                    st.dataframe(pd.DataFrame(_parsed[:5]),
                                 use_container_width=True, hide_index=True)

    if not HAS_NAVER_API:
        st.error("naver_api.py 가 로드되지 않았습니다.")
        return
    if not api_id or not api_secret:
        st.warning("⚙️ 설정에서 네이버 커머스 API 키를 먼저 입력하세요.")
        return

    # ── 날짜 선택 + 수집 버튼 ────────────────────────────────────
    _c1, _c3, _c4 = st.columns([2, 1, 1])
    with _c1:
        settle_date = st.date_input(
            "정산일 (정산예정일=입금일)", value=datetime.today(),
            help="네이버 '정산예정일' 기준 — 이 날짜에 입금되는 정산내역을 조회합니다. "
                 "오늘 입금분을 보려면 오늘 날짜로 두세요."
        )
        settle_date_str = settle_date.strftime("%Y-%m-%d")
    # 발송일은 역추적(정산일 기준)에선 불필요 → 합계/미정산 보조용 기본값(정산일 전날)
    ship_date_str = (settle_date - timedelta(days=1)).strftime("%Y-%m-%d")
    with _c3:
        st.write("")
        st.write("")
        fetch_clicked = st.button("📥 정산 수집·저장", type="primary", use_container_width=True,
                                  help="API로 건별 정산을 수집해 DB에 저장합니다 (별도 저장 불필요).")
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

    # ══════════════════════════════════════════════════════════════
    # 🔁 정산일 역추적 매칭 (정산건 → 상품주문번호로 발송건 역조회) — 메인
    # ══════════════════════════════════════════════════════════════
    st.divider()
    st.subheader(f"💰 {settle_date_str} 정산금액 (네이버 입금 기준)")
    _daily, _derr = naver_api.get_daily_settlement(api_id, api_secret, settle_date_str)
    if _daily:
        _d1, _d2, _d3, _d4 = st.columns(4)
        _d1.metric("💰 정산금액(입금)", f"{fmt(_daily.get('settleAmount', 0))}원")
        _d2.metric("정산기준금액", f"{fmt(_daily.get('paySettleAmount', 0))}원")
        _d3.metric("수수료합계", f"{fmt(_daily.get('commissionSettleAmount', 0))}원")
        _d4.metric("혜택정산", f"{fmt(_daily.get('benefitSettleAmount', 0))}원")
        _e1, _e2, _e3 = st.columns(3)
        _e1.metric("일반정산금액", f"{fmt(_daily.get('normalSettleAmount', 0))}원")
        _e2.metric("빠른정산금액", f"{fmt(_daily.get('quickSettleAmount', 0))}원")
        _dcomplete = _daily.get('settleCompleteDate') or _daily.get('settleExpectDate') or ''
        _e3.metric("입금(완료)일", _dcomplete)
        st.caption("📌 네이버 정산내역 화면과 동일한 순액입니다 (정산기준금액 − 수수료 − 혜택 = 정산금액).")
    elif _derr:
        st.warning(f"일별 정산 합계 조회 실패: {_derr}")
    else:
        st.info(f"{settle_date_str}에 입금 예정 정산이 없습니다.")

    st.divider()
    st.subheader(f"🔁 정산일 역추적 매칭 — {settle_date_str} 정산건 → 발송건")
    st.caption("이 정산일에 들어온 정산건을 상품주문번호로 역추적해 원래 발송건과 대조합니다. "
               "(정산 = 확정된 사실 기준 / 발송→정산 소요일도 표시)")

    _settled_rt = get_naver_settlements_by_date(USERNAME, settle_date_str)
    if not _settled_rt:
        st.info(f"❓ {settle_date_str} 정산 내역이 없습니다. 위에서 📥 정산 수집 또는 CSV 업로드를 먼저 하세요.")
    else:
        _po_list = [str(r.get('product_order_no', '')) for r in _settled_rt]
        _disp_by_po = get_dispatch_by_order_nos(USERNAME, _po_list, platform='naver')
        _rt = match_settled_to_dispatch(_settled_rt, _disp_by_po)
        _s = _rt['summary']
        # ── 건별 정산 합계 (상품 / 배송비) — /case 기준, 혜택정산 차감 전 ──
        _g1, _g2, _g3 = st.columns(3)
        _g1.metric("상품 정산 합계(건별)", f"{fmt(_s['settle_total'])}원",
                   delta=f"{_s['settled_n']}건")
        _g2.metric("배송비 정산 합계(건별)", f"{fmt(_s['delivery_total'])}원",
                   delta=f"{_s['delivery_n']}건")
        _g3.metric("건별 합계(상품+배송비)", f"{fmt(_s['grand_total'])}원",
                   help="건별(/case) 합계입니다. 위 '정산금액(입금)'은 여기서 혜택정산을 더 차감한 순액입니다.")
        # 건별 합계 ≠ 입금액 차이 설명 (혜택정산 등 건별 응답에 없는 차감)
        if _daily:
            _settle_in = int(_daily.get('settleAmount', 0) or 0)
            _ben = int(_daily.get('benefitSettleAmount', 0) or 0)
            _gap = _s['grand_total'] - _settle_in
            if _gap != 0:
                st.caption(
                    f"💡 **건별 합계 {fmt(_s['grand_total'])}원 ≠ 입금액 {fmt(_settle_in)}원** (차이 {fmt(_gap)}원) — "
                    f"건별(/case) 응답엔 **혜택정산({fmt(_ben)}원)** 등 건별 외 차감이 안 들어옵니다. "
                    f"건별 합계 + 혜택정산 = 입금액. 실제 입금액은 위 '정산금액(입금)' 기준이 정확합니다."
                )
        # ── 매칭 현황 ──
        _r1, _r2, _r3, _r4 = st.columns(4)
        _r1.metric("정산건(상품)", f"{_s['settled_n']}건")
        _r2.metric("✅ 일치", f"{_s['matched_n']}건")
        _r3.metric("⚠️ 차액", f"{_s['mismatched_n']}건",
                   delta=fmt(_s['total_diff']) + "원" if _s['total_diff'] else None)
        _r4.metric("🔍 발송기록 없음", f"{_s['no_dispatch_n']}건")

        # ── 💾 정산매칭 저장 (매칭 결과 기록 + 실정산 확정 → 수익계산 반영) ──
        if st.button("💾 정산매칭 저장 (실정산 확정 → 수익계산 반영)",
                     key=f"save_match_{settle_date_str}", type="primary",
                     help="이 매칭 결과를 저장하고, 매칭된 실제 정산액을 수익계산에서 상품주문번호로 반영합니다."):
            _save_rows = (
                [{**r, 'match_status': 'matched'} for r in _rt['matched']]
                + [{**r, 'match_status': 'mismatched'} for r in _rt['mismatched']]
                + [{**r, 'match_status': 'no_dispatch'} for r in _rt['no_dispatch']]
            )
            _n = save_settlement_matches(USERNAME, settle_date_str, _save_rows)
            # 실정산 확정 → profit_settlements 반영 (달력/대시보드/통계까지 적용)
            _actuals = {r['product_order_no']: {'actual': int(r.get('actual') or 0)}
                        for r in _save_rows if int(r.get('actual') or 0) > 0}
            _upd = apply_actual_settlements_to_profit(USERNAME, _actuals)
            st.success(f"✅ {settle_date_str} 정산매칭 {_n}건 저장 완료 — "
                       f"수익계산 실정산 반영, 저장된 정산표 {_upd}건 갱신(달력·대시보드 반영).")
        st.caption("💡 저장하면 매칭 결과가 기록되고, 매칭된 실제 정산액이 수익계산에 반영됩니다 "
                   "(예상 → 실제 정산).")

        def _render_rt(rows):
            _cols = ['product_order_no', 'buyer_name', 'product_name', 'ship_date',
                     'settle_date', 'lag_days', 'expected', 'actual', 'diff',
                     'commission', 'settle_type', 'diff_reason']
            _names = ['상품주문번호', '구매자', '상품명', '발송일', '정산일',
                      '소요일', '예상정산', '실제정산', '차액', '수수료', '정산유형', '차액원인']
            _df = pd.DataFrame(rows)
            for _c in _cols:
                if _c not in _df.columns:
                    _df[_c] = ''
            _df = _df[_cols]
            _df.columns = _names
            st.dataframe(_df, use_container_width=True, hide_index=True)

        _rt1, _rt2, _rt3 = st.tabs([
            f"✅ 발송일 일치 ({_s['matched_n']})",
            f"🔍 미일치·발송기록 없음 ({_s['no_dispatch_n']})",
            f"⚠️ 차액 ({_s['mismatched_n']})",
        ])
        with _rt1:
            if _rt['matched']:
                st.caption(f"발송건과 매칭되고 예상=실제가 일치한 정산 {_s['matched_n']}건 (발송일·소요일 표시)")
                _render_rt(_rt['matched'])
            else:
                st.caption("발송일 일치 항목이 없습니다. (정산 수집·발송처리 누적 시 늘어남)")
        with _rt2:
            if _rt['no_dispatch']:
                st.caption(f"발송 기록과 매칭 안 된 정산 {_s['no_dispatch_n']}건 — dispatch_log 범위 밖(옛 발송분) 또는 수동 발송분")
                _dec_key = f"_pdec_{settle_date_str}"
                if st.button("🤖 구매확정 유형 확인 (자동/수동 추정)",
                             key=f"btn_pdec_{settle_date_str}",
                             help="미일치 정산건의 상품주문번호로 구매확정 일시를 조회해 추정합니다 (새벽 확정=자동)"):
                    _po_nd = [r['product_order_no'] for r in _rt['no_dispatch']]
                    with st.spinner("구매확정 일시 조회 중..."):
                        st.session_state[_dec_key] = naver_api.get_purchase_decisions(
                            api_id, api_secret, _po_nd)
                _decmap = st.session_state.get(_dec_key)
                if _decmap is not None:
                    _nd_rows = []
                    for r in _rt['no_dispatch']:
                        _d = _decmap.get(r['product_order_no'], {})
                        _label, _dt = infer_purchase_decision(_d.get('decision_date', ''))
                        _nd_rows.append({**r, '_confirm': _label or '—', '_confirm_at': _dt})
                    _cols = ['product_order_no', 'buyer_name', 'product_name', 'settle_date',
                             'actual', '_confirm', '_confirm_at']
                    _names = ['상품주문번호', '구매자', '상품명', '정산일',
                              '실제정산', '구매확정(추정)', '구매확정일시']
                    _df = pd.DataFrame(_nd_rows)
                    for _c in _cols:
                        if _c not in _df.columns:
                            _df[_c] = ''
                    _df = _df[_cols]
                    _df.columns = _names
                    st.dataframe(_df, use_container_width=True, hide_index=True)
                    _auto_n = sum(1 for x in _nd_rows if '자동' in (x['_confirm'] or ''))
                    _manual_n = sum(1 for x in _nd_rows if '수동' in (x['_confirm'] or ''))
                    st.caption(f"추정: 🤖 자동확정 {_auto_n}건 / 👤 수동확정 {_manual_n}건 "
                               "— 자동확정된 옛 주문은 발송기록이 없을 수 있습니다. (시각 기반 추정)")
                else:
                    _render_rt(_rt['no_dispatch'])
            else:
                st.success("모든 정산건이 발송건과 연결됨.")
        with _rt3:
            if _rt['mismatched']:
                st.caption("💡 차액원인 열로 수수료/배송비/공제 차감을 확인하세요.")
                _render_rt(_rt['mismatched'])
            else:
                st.success("차액 없음 — 예상 = 실제 정산.")

    # ══════════════════════════════════════════════════════════════
    # 📦 미정산 추적 (발송일 기준 순방향) — 발송했는데 아직 정산 안 된 건
    # ══════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📦 미정산 추적 — 발송건 중 정산 안 된 건")
    _us_c1, _us_c2 = st.columns([1.3, 4])
    _us_ship = _us_c1.date_input("발송일 선택", value=datetime.today() - timedelta(days=2),
                                 key="us_ship_date",
                                 help="이 발송일의 주문 중 아직 정산 안 된 건을 찾습니다.")
    _us_ship_str = _us_ship.strftime("%Y-%m-%d")
    _us_c2.caption("발송했는데 아직 정산되지 않은 건을 찾습니다. 발송 후 오래된 건은 '누락 의심'으로 표시됩니다. "
                   "(정산 수집을 충분히 해둬야 정확합니다)")

    _disp_us = get_dispatch_log_by_date(USERNAME, _us_ship_str, platform='naver')
    if not _disp_us:
        st.info(f"❓ {_us_ship_str}에 발송 이력이 없습니다. (송장 페이지에서 발송처리 시 자동 기록)")
    else:
        _settled_set = get_settled_product_order_nos(USERNAME)
        _today = datetime.today().strftime("%Y-%m-%d")
        _us = find_unsettled_dispatches(_disp_us, _settled_set, _today, delay_threshold=10)
        _us_s = _us['summary']
        _u1, _u2, _u3, _u4 = st.columns(4)
        _u1.metric("발송", f"{_us_s['dispatch_n']}건")
        _u2.metric("✅ 정산완료", f"{_us_s['settled_n']}건")
        _u3.metric("⏳ 정산지연(대기)", f"{_us_s['pending_n']}건")
        _u4.metric("🔴 누락 의심", f"{_us_s['suspect_n']}건",
                   delta=fmt(_us_s['unsettled_amount']) + "원" if _us_s['unsettled_amount'] else None)
        if _us['unsettled']:
            _df_us = pd.DataFrame(_us['unsettled'])[
                ['product_order_no', 'recipient', 'product_name', 'ship_date',
                 'elapsed_days', 'expected_settlement', 'status']]
            _df_us.columns = ['상품주문번호', '수취인', '상품명', '발송일',
                              '경과일', '정산예정금액', '상태']
            st.dataframe(_df_us, use_container_width=True, hide_index=True)
            st.caption("💡 '누락 의심'(발송 10일 초과 미정산)은 네이버 정산관리에서 직접 확인을 권장합니다. "
                       "'정산지연'은 구매확정 전이거나 정산예정일 미도래로 정상입니다.")
        else:
            st.success("✅ 이 발송일의 모든 주문이 정산 완료되었습니다.")

    # ══════════════════════════════════════════════════════════════
    # ⭐ 일괄발송 vs 일일정산 합계 매칭 (per-order /case 없이 합계로 검증)
    # ══════════════════════════════════════════════════════════════
    st.divider()
    st.subheader(f"🎯 일괄발송 합계 vs 일일정산 합계 — {ship_date_str} 발송 / {settle_date_str} 정산")
    st.caption("📌 어제 일괄발송 성공한 주문의 정산예정 합계와 오늘 네이버 일일정산 합계를 비교합니다.")

    _dispatch_rows = get_dispatch_log_by_date(USERNAME, ship_date_str, platform='naver')
    if not _dispatch_rows:
        _avail = get_dispatch_dates(USERNAME, limit=10)
        st.info(
            f"❓ {ship_date_str}에 저장된 일괄발송 이력이 없습니다. "
            f"송장번호 페이지에서 발송처리를 완료해야 자동 저장됩니다."
            + (f"\n\n사용 가능한 발송일: {', '.join(_avail[:7])}" if _avail else "")
        )
    else:
        # 일일 정산 합계 — /daily API 직접 호출 (이미 동작 확인됨)
        with st.spinner(f"네이버 일일정산 합계 조회 중..."):
            _records, _err, _used, _attempts = naver_api.get_settlement_history(
                api_id, api_secret, settle_date_str, settle_date_str
            )
        _daily_total = 0
        if _records:
            # /case: settleExpectAmount(건별) / /daily: settleAmount(합계)
            for _rec in _records:
                _daily_total += int(_rec.get('settleExpectAmount')
                                    or _rec.get('settleAmount')
                                    or _rec.get('settle_amount') or 0)
        if _err:
            st.error(f"❌ 일일정산 조회 실패: {_err}")
        else:
            _m = match_daily_total(_dispatch_rows, _daily_total)
            _c1, _c2, _c3, _c4 = st.columns(4)
            _c1.metric("일괄발송 성공", f"{_m['dispatch_count']}건")
            _c2.metric("예상 정산합계", f"{fmt(_m['expected_total'])}원")
            _c3.metric("실제 정산합계", f"{fmt(_m['actual_total'])}원",
                       delta=f"{fmt(_m['diff'])}원" if _m['diff'] else None)
            _c4.metric("일치율",
                       f"{_m['rate']:.1f}%",
                       delta="✅ 일치" if _m['match'] == 'OK' else "⚠️ 불일치")

            if _m['match'] == 'OK':
                st.success(f"✅ 합계 일치 — 차액 {fmt(_m['diff'])}원 (허용 오차 내)")
            else:
                _color = "🔴" if _m['diff'] < 0 else "🔵"
                st.error(
                    f"⚠️ 불일치 {_color} 차액 {fmt(_m['diff'])}원 — "
                    + ("정산 누락 가능성" if _m['diff'] < 0 else "정산 추가 입금 (이전 발송분일 가능성)")
                )
            with st.expander(f"📋 {ship_date_str} 발송 성공 목록 ({len(_dispatch_rows)}건)", expanded=False):
                st.dataframe(pd.DataFrame(_dispatch_rows)[
                    ['order_no', 'recipient', 'product_name', 'expected_settlement', 'tracking_no', 'courier']
                ], use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════
    # 📊 건별 매칭 (CSV 데이터 기반 — dispatch_log ↔ naver_settlements)
    # ══════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader(f"📊 건별 매칭 — 발송일 {ship_date_str} ↔ 정산일 {settle_date_str}")

    # DB에서 양쪽 데이터 로드 — dispatch_log를 우선, 없으면 order_history fallback
    settled_rows = get_naver_settlements_by_date(USERNAME, settle_date_str)
    _dispatch_for_match = get_dispatch_log_by_date(USERNAME, ship_date_str, platform='naver')

    if _dispatch_for_match:
        # dispatch_log 사용 (정확한 발송 성공건만)
        shipped_dicts = [{
            'product_order_no':    str(d.get('order_no', '')),
            'recipient':           d.get('recipient', ''),
            'product_name':        d.get('product_name', ''),
            'expected_settlement': int(d.get('expected_settlement') or 0),
        } for d in _dispatch_for_match]
        st.caption(f"📌 매칭 소스: dispatch_log {len(_dispatch_for_match)}건 (일괄발송 성공건)")
    else:
        # fallback: order_history (네이버만)
        _all_shipped = search_order_history(USERNAME, date_from=ship_date_str, date_to=ship_date_str, limit=5000)
        shipped_rows_fb = [r for r in _all_shipped if '-' not in str(r.get('order_no', ''))]
        shipped_dicts = shipped_orders_from_db_rows(shipped_rows_fb)
        if shipped_dicts:
            st.caption(f"📌 매칭 소스: order_history (네이버만) {len(shipped_dicts)}건 — dispatch_log 비어있음")

    settled_dicts = [{
        'product_order_no': r['product_order_no'],
        'order_no':         r.get('order_no', ''),
        'settle_amount':    r.get('settle_amount', 0),
        'sales_amount':     r.get('sales_amount', 0),
        'product_amount':   r.get('product_amount', 0),
        'shipping_amount':  r.get('shipping_amount', 0),
        'commission':       r.get('commission', 0),
        'settle_type':      r.get('settle_type', ''),
        'reason':           r.get('reason', ''),
    } for r in settled_rows]

    if not settled_rows and not shipped_dicts:
        st.info("선택한 날짜에 정산 내역과 발송건이 모두 없습니다. 먼저 CSV 업로드 또는 정산 수집을 눌러주세요.")
        return

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
    def _render_match_df(rows):
        """매칭 행을 한글 컬럼 + 차액 원인으로 표시."""
        _cols = ['product_order_no', 'recipient', 'product_name',
                 'expected', 'actual', 'diff', 'commission', 'shipping_amount',
                 'settle_type', 'diff_reason']
        _names = ['상품주문번호', '수취인', '상품명',
                  '예상정산', '실제정산', '차액', '수수료', '정산배송비',
                  '정산유형', '차액원인']
        _df = pd.DataFrame(rows)
        for _c in _cols:
            if _c not in _df.columns:
                _df[_c] = ''
        _df = _df[_cols]
        _df.columns = _names
        st.dataframe(_df, use_container_width=True, hide_index=True)

    with _t1:
        if result['mismatched']:
            _render_match_df(result['mismatched'])
            st.caption("💡 **차액원인** 열로 수수료/배송비/공제/광고비 차감을 확인하세요. "
                       "정산유형 '공제'는 클레임·반품 등으로 차감된 건입니다.")
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
            _render_match_df(result['matched'])
        else:
            st.caption("일치 항목 없음.")
    with _t4:
        if result['orphan']:
            df = pd.DataFrame(result['orphan'])
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption("💡 발송 기록은 없지만 정산된 주문 — 옛 발송분이거나 발송일 범위를 확장해야 매칭됩니다.")
        else:
            st.caption("해당 없음.")

    # ══════════════════════════════════════════════════════════════════
    # 🚚 배송비 수수료 분석 (고객결제 배송비 vs 정산받은 배송비)
    # ══════════════════════════════════════════════════════════════════
    if _dispatch_for_match and settled_rows:
        st.divider()
        st.subheader("🚚 배송비 수수료 분석")
        st.caption("고객이 결제한 배송비와 네이버가 정산해준 배송비의 차이 = 네이버가 떼는 수수료")

        _ship_analysis = analyze_shipping_commission(_dispatch_for_match, settled_rows)
        _sa1, _sa2, _sa3, _sa4 = st.columns(4)
        _sa1.metric("매칭 건수", f"{len(_ship_analysis['rows'])}건")
        _sa2.metric("고객 결제 배송비 합계", f"{fmt(_ship_analysis['total_customer_shipping'])}원")
        _sa3.metric("정산받은 배송비 합계", f"{fmt(_ship_analysis['total_settled_shipping'])}원")
        _sa4.metric("배송비 수수료 합계",
                    f"{fmt(_ship_analysis['total_commission'])}원",
                    delta=f"평균 {_ship_analysis['avg_commission_rate']:.1f}%")

        if _ship_analysis['rows']:
            with st.expander(f"📋 행별 상세 ({len(_ship_analysis['rows'])}건)", expanded=False):
                _df_ship = pd.DataFrame(_ship_analysis['rows'])
                _df_ship.columns = ['상품주문번호', '수취인', '고객결제배송비', '정산배송비', '수수료', '수수료율(%)']
                st.dataframe(_df_ship, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════
    # 🛒 쿠팡 정산 매칭 (Wing revenue-history)
    # ══════════════════════════════════════════════════════════════════
    st.divider()
    st.header("🛒 쿠팡 정산 매칭")
    _cp_ak, _cp_sk, _cp_vid = _gs("coupang_access_key"), _gs("coupang_secret_key"), _gs("coupang_vendor_id")
    if not (HAS_COUPANG_API and _cp_ak and _cp_sk and _cp_vid):
        st.info("⚙️ 설정에서 쿠팡 Wing API 키(Access/Secret/Vendor ID)를 입력하면 쿠팡 정산이 활성화됩니다.")
    else:
        # ── 📊 판매-정산 대사 (판매건 중 정산 누락 없는지 + 실제 정산금) ──
        st.subheader("📊 판매–정산 대사 (누락 확인 · 실제 정산금)")
        _rc1, _rc2, _rc3 = st.columns([1.3, 1.3, 3])
        _rc_from = _rc1.date_input("판매(주문)일 From", value=datetime.today() - timedelta(days=45),
                                   key="cp_rc_from")
        _rc_to = _rc2.date_input("판매(주문)일 To", value=datetime.today(), key="cp_rc_to")
        _rc3.caption("이 기간 판매한 쿠팡 주문이 정산·입금됐는지 대조합니다. "
                     "정산이 안 된 건(누락/대기)을 찾아 실제 수익을 확인하세요. "
                     "(정산은 보통 판매 수주 후 — 최근 판매는 '정산대기'가 정상)")
        _cp_orders = [o for o in search_order_history(
            USERNAME, date_from=_rc_from.strftime("%Y-%m-%d"),
            date_to=_rc_to.strftime("%Y-%m-%d"), limit=5000)
            if '-' in str(o.get('order_no', ''))]  # 쿠팡 주문(order_no='{orderId}-..')
        if not _cp_orders:
            st.caption("해당 기간 쿠팡 판매 주문이 없습니다. (주문 수집을 먼저 하세요)")
        else:
            _settled = get_coupang_settled_map(USERNAME)  # {orderId: {settlement, first_amt, ...}}
            _rc_rows = []
            _n_settled = _n_missing = _sum_settle = _sum_sale = 0
            _sum_1st = _sum_2nd = 0
            _cycles = set()
            _today_d = datetime.today().date()
            for o in _cp_orders:
                _oid = str(o.get('order_no', '')).split('-')[0]
                _sale = int(o.get('order_amount', 0) or 0)
                _sum_sale += _sale
                _sv = _settled.get(_oid)
                if _sv:
                    _n_settled += 1
                    _sum_settle += _sv['settlement']
                    _sum_1st += _sv['first_amt']; _sum_2nd += _sv['second_amt']
                    _cycles.add(_sv['cycle'])
                    _rc_rows.append({
                        '주문번호': _oid, '상품명': (o.get('product_name', '') or '')[:24],
                        '판매일': str(o.get('order_date', ''))[:10], '판매액': _sale,
                        '정산금(전액)': _sv['settlement'], '주기': _sv['cycle'],
                        '1차(70%)': _sv['first_amt'], '1차일': _sv['settle_date'],
                        '2차(30%)': _sv['second_amt'] or '', '2차일': _sv['final_settle_date'] or '',
                        '상태': '✅ 정산완료',
                    })
                else:
                    _n_missing += 1
                    try:
                        _od = datetime.strptime(str(o.get('order_date', ''))[:10], "%Y-%m-%d").date()
                        _age = (_today_d - _od).days
                    except Exception:
                        _age = 0
                    _rc_rows.append({
                        '주문번호': _oid, '상품명': (o.get('product_name', '') or '')[:24],
                        '판매일': str(o.get('order_date', ''))[:10], '판매액': _sale,
                        '정산금(전액)': '', '주기': '', '1차(70%)': '', '1차일': '',
                        '2차(30%)': '', '2차일': '',
                        '상태': '🔴 누락 의심' if _age > 30 else '⏳ 정산대기',
                    })
            _q1, _q2, _q3, _q4 = st.columns(4)
            _q1.metric("판매 건수", f"{len(_cp_orders)}건")
            _q2.metric("✅ 정산완료", f"{_n_settled}건", delta=f"{fmt(_sum_settle)}원")
            _q3.metric("⏳ 미정산(대기+누락)", f"{_n_missing}건")
            _q4.metric("판매액 합계", f"{fmt(_sum_sale)}원")
            _cyc_label = " / ".join(sorted(_cycles)) if _cycles else "-"
            st.caption(
                f"💰 **실제 받을 정산금(전액) = {fmt(_sum_settle)}원** · 정산주기: **{_cyc_label}** | "
                f"주정산 분할 → **1차(70%) {fmt(_sum_1st)}원 + 2차(30%) {fmt(_sum_2nd)}원**. "
                f"(70/30은 정책 기준 계산값 · 월정산은 1차에 100%) 🔴누락의심(판매 30일 초과 미정산) 확인."
            )
            st.dataframe(pd.DataFrame(_rc_rows), use_container_width=True, hide_index=True)
            st.divider()
        # ── (참고) 정산일 기준 입금 상세 ──
        _cpc1, _cpc2, _cpc3 = st.columns([1.4, 1.4, 1.2])
        _cp_from = _cpc1.date_input("매출인식일 From", value=datetime.today() - timedelta(days=45),
                                    key="cp_from", help="이 기간의 쿠팡 매출/정산을 수집합니다(매출인식일 기준).")
        _cp_to = _cpc2.date_input("매출인식일 To", value=datetime.today(), key="cp_to")
        _cpc3.write(""); _cpc3.write("")
        if _cpc3.button("📥 쿠팡 정산 수집·저장", type="primary", use_container_width=True, key="cp_fetch"):
            with st.spinner("쿠팡 매출/정산 조회 중..."):
                _cp_recs, _cp_err = coupang_api.get_revenue_history(
                    _cp_ak, _cp_sk, _cp_vid,
                    _cp_from.strftime("%Y-%m-%d"), _cp_to.strftime("%Y-%m-%d"))
            if _cp_err:
                st.error(f"❌ {_cp_err}")
            else:
                _cp_n = save_coupang_settlements(USERNAME, _cp_recs)
                st.success(f"✅ 쿠팡 정산 {_cp_n}건 저장 (조회 {len(_cp_recs)}건)")

        _cp_dates = get_coupang_settle_dates(USERNAME, limit=60)
        if not _cp_dates:
            st.caption("아직 저장된 쿠팡 정산이 없습니다. 위에서 수집·저장하세요.")
        else:
            _cp_sel = st.selectbox("정산일(지급일) 선택", _cp_dates, key="cp_settle_date")
            _cp_rows = get_coupang_settlements_by_date(USERNAME, _cp_sel)
            _cp_settle = sum(int(r.get('settlement_amount') or 0) for r in _cp_rows)
            _cp_fee = sum(int(r.get('service_fee') or 0) for r in _cp_rows)
            _cp_dlv = sum(int(r.get('delivery_settlement') or 0) for r in _cp_rows)
            _cp_sale = sum(int(r.get('sale_amount') or 0) for r in _cp_rows)
            _m1, _m2, _m3, _m4 = st.columns(4)
            _m1.metric("💰 정산금(상품)", f"{fmt(_cp_settle)}원", delta=f"{len(_cp_rows)}건")
            _m2.metric("수수료 합계", f"{fmt(_cp_fee)}원")
            _m3.metric("🚚 배송비 정산", f"{fmt(_cp_dlv)}원")
            _m4.metric("판매액 합계", f"{fmt(_cp_sale)}원")
            st.caption(f"💡 정산금 = 판매액 − 수수료 (쿠팡). **광고비는 revenue-history에 없어 별도**입니다. "
                       f"정산금+배송비정산 = {fmt(_cp_settle + _cp_dlv)}원")

            # 역매칭: orderId → 발송(dispatch_log) 우선, 없으면 주문수집(order_history) 폴백
            _cp_oids = list({str(r.get('order_id')) for r in _cp_rows if r.get('order_id')})
            _cp_disp = get_dispatch_by_order_id(USERNAME, _cp_oids, platform='coupang')
            _cp_ord = get_orders_by_order_ids(USERNAME, [o for o in _cp_oids if o not in _cp_disp])
            _cp_hit = {o for o in _cp_oids if o in _cp_disp or o in _cp_ord}
            _cp_matched = len(_cp_hit)
            _mm1, _mm2 = st.columns(2)
            _mm1.metric("✅ 주문/발송 매칭", f"{_cp_matched}/{len(_cp_oids)}건")
            _mm2.metric("🔍 매칭 없음", f"{len(_cp_oids) - _cp_matched}건")

            def _cp_src(oid):
                if oid in _cp_disp:
                    return '🚚 발송'
                if oid in _cp_ord:
                    return '📋 주문'
                return '—'
            _cp_df = pd.DataFrame([{
                '주문번호': r.get('order_id'), '상품명': (r.get('product_name') or '')[:30],
                '판매액': r.get('sale_amount'), '수수료': r.get('service_fee'),
                '정산금': r.get('settlement_amount'), '배송비정산': r.get('delivery_settlement'),
                '매칭': _cp_src(str(r.get('order_id'))),
            } for r in _cp_rows])
            st.dataframe(_cp_df, use_container_width=True, hide_index=True)
            st.caption("💡 매칭: 🚚발송(발송처리됨) / 📋주문(주문수집됨, 발송처리 전) / —(우리 주문 아님·범위 밖)")
