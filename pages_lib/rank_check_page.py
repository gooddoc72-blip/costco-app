"""📈 순위 체크 페이지 — pages_lib 자동 추출."""
import os
import io
import sys
import json
import subprocess
import sqlite3
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
try:
    import plotly.express as px
except ImportError:
    px = None

from db import (
    init_auth_db, hash_pw, check_login, get_global_setting, set_global_setting,
    register_user, get_pending_users, approve_user, reject_user, get_all_users,
    add_user, delete_user, change_password, get_user_info,
    create_session, get_session_user, delete_session,
    get_shared_products, upsert_shared_product, delete_shared_product, upsert_shared_store_price,
    get_user_db, init_user_db, get_setting, set_setting, get_all_settings, get_all_products,
    upsert_user_private, get_all_products_merged, upsert_product,
    get_product_detail,
    save_daily_orders, get_daily_orders, save_order_history, search_order_history,
    save_receipt_items, get_recent_receipt_items, delete_receipt_items_by_date, get_receipt_dates,
    get_date_range_stats, get_monthly_stats, get_product_ranking, get_saved_dates,
    get_dashboard_kpi, get_daily_profit_trend, get_week_best_products,
    get_price_history_monthly, save_price_changes_to_history, get_price_change_history,
    add_keyword_tracking, get_keyword_trackings, delete_keyword_tracking,
    update_keyword_tracking,
    save_rank_result, get_rank_history, get_latest_ranks,
    get_daily_ranks_in_month, get_yearly_rank_history, delete_trackings_bulk,
    get_rank_drops,
    AUTH_DB,
)
from services import (
    match_product_to_db, match_shared_product,
    update_product_info_from_orders, update_product_shipping_fees, update_product_sale_price,
    detect_price_changes, build_price_alert_msg,
    parse_costco_receipt_pdf, match_receipt_to_orders,
    match_receipt_to_naver_products, apply_receipt_pno_updates,
    decrypt_excel, read_excel_auto,
    _token_score,
)
from utils import (
    fmt, to_id_str, extract_pack_qty, clean_name, has_meaningful_char,
    get_ngrams, calc_match_score, MIN_MATCH_SCORE, get_week_range, get_month_range,
)
from ui_theme import (
    COLORS, CHART_COLORS, hero_section, section_header,
    kpi_card, chart_card_open, chart_card_close, quick_action_buttons,
)

try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False
    naver_api = None

# app.py 라우터에서 주입되는 cached wrapper들
cached_shared_products = None
cached_user_products = None
cached_merged = None
invalidate_data_cache = None


def _set_cache_helpers(shared_fn, user_fn, merged_fn, invalidate_fn, **kwargs):
    global cached_shared_products, cached_user_products, cached_merged, invalidate_data_cache
    cached_shared_products = shared_fn
    cached_user_products = user_fn
    cached_merged = merged_fn
    invalidate_data_cache = invalidate_fn


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    """📈 순위 체크 탭 렌더링."""
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    st.header("📈 키워드 순위 체크")
    st.caption("네이버 쇼핑에서 우리 상품이 특정 키워드 검색 결과 몇 위에 노출되는지 매일 추적합니다.")
    st.info(
        "ℹ️ **순위는 네이버 쇼핑 검색 API의 '정확도순' 기준**입니다. "
        "실제 검색 화면 기본값인 **'랭킹순'(인기·리뷰·광고 반영)과는 순위가 다를 수 있습니다.** "
        "비교하실 땐 네이버쇼핑에서 정렬을 **'정확도순'으로 바꿔** 확인하시면 가장 비슷합니다. "
        "(절대 순위보다 **매일 변동 추세** 파악용으로 보세요)"
    )

    # ── 🔍 키워드 검색 — 월간 검색량·연관검색어 (네이버 검색광고 API) ──
    _ad_key  = _gs('naver_ad_api_key')
    _ad_sec  = _gs('naver_ad_secret')
    _ad_cust = _gs('naver_ad_customer_id')
    with st.expander("🔍 키워드 검색 — 월간 검색량 · 연관검색어", expanded=False):
        if not (_ad_key and _ad_sec and _ad_cust):
            st.warning("⚙️ 설정 탭 > **네이버 검색광고 API**에 API_KEY·SECRET·고객ID를 등록하면 사용할 수 있습니다.")
            st.caption("발급: [searchad.naver.com](https://searchad.naver.com) 로그인 → 도구 → **API 사용 관리** → 발급 "
                       "(네이버 Open API·커머스 API와 별개).")
        else:
            _kc1, _kc2 = st.columns([4, 1])
            _kw_q = _kc1.text_input("검색 키워드", placeholder="예: 검은콩, 그릭요거트",
                                    key="kw_tool_q", label_visibility="collapsed")
            _kw_go = _kc2.button("🔎 검색", key="kw_tool_go", use_container_width=True, type="primary")
            # 검색 결과를 리스트에 누적 (같은 키워드 재검색 시 최신으로 갱신, 최근 검색이 위)
            _kw_hist = st.session_state.setdefault('_kw_tool_hist', [])
            if _kw_go and _kw_q.strip():
                _q = _kw_q.strip()
                with st.spinner("네이버 검색광고·자동완성 조회 중..."):
                    _rows, _kerr = naver_api.keyword_research(_ad_key, _ad_sec, _ad_cust, _q)
                # 📈 최근 12개월 검색량 추이 (데이터랩 — 순위체크용 Open API 키 재사용)
                _trend, _terr = None, None
                _open_cid  = _gs('naver_open_client_id')
                _open_csec = _gs('naver_open_client_secret')
                if not _kerr and _open_cid and _open_csec:
                    # 검색어 자신(연관검색어 행)의 현재월 PC/모바일 → 절대치 앵커
                    _selfrow = next((r for r in (_rows or [])
                                     if r.get('구분') == '연관검색어'),
                                    (_rows or [{}])[0] if _rows else {})
                    _pc_now = int(_selfrow.get('PC검색량', 0) or 0)
                    _mo_now = int(_selfrow.get('모바일검색량', 0) or 0)
                    with st.spinner("데이터랩 12개월 추이 조회 중..."):
                        _trend, _terr = naver_api.datalab_search_trend(
                            _open_cid, _open_csec, _q, _pc_now, _mo_now)
                # 👫 성별·연령 검색 비율 (데이터랩 쇼핑인사이트 — 카테고리 자동탐지)
                _ga, _gaerr = None, None
                if not _kerr and _open_cid and _open_csec:
                    with st.spinner("성별·연령 비율 조회 중..."):
                        _ga, _gaerr = naver_api.datalab_keyword_gender_age(
                            _open_cid, _open_csec, _q)
                _kw_hist[:] = [h for h in _kw_hist if h['q'] != _q]   # 중복 제거
                _kw_hist.insert(0, {'q': _q, 'rows': _rows, 'err': _kerr,
                                    'trend': _trend, 'terr': _terr,
                                    'ga': _ga, 'gaerr': _gaerr})
                st.rerun()

            if not _kw_hist:
                st.caption("키워드를 입력하고 검색하면 아래에 누적됩니다. (연관검색어 · 함께찾는 · 자동완성)")
            _TAG = {'연관검색어': '🟡 연관검색어', '함께찾는': '🟢 함께찾는', '자동완성': '⚪ 자동완성'}
            import pandas as _pd
            for _hi, _h in enumerate(list(_kw_hist)):
                _hc1, _hc2 = st.columns([6, 1])
                _n = 0 if _h.get('err') else len(_h.get('rows') or [])
                _hc1.markdown(f"**🔎 {_h['q']}** — {_n}개")
                if _hc2.button("🗑 삭제", key=f"kw_del_{_hi}_{_h['q']}", use_container_width=True):
                    _kw_hist[:] = [x for x in _kw_hist if x['q'] != _h['q']]
                    st.rerun()
                if _h.get('err'):
                    st.error(f"❌ 조회 실패: {_h['err']}")
                elif not _h.get('rows'):
                    st.info("연관 키워드가 없습니다.")
                else:
                    # 좌: 키워드 표 / 우: 성별·연령·12개월 추이 패널
                    _lc, _rc = st.columns([3, 2])
                    with _lc:
                        _df_kw = _pd.DataFrame(_h['rows'])
                        if '구분' in _df_kw.columns:
                            _df_kw['구분'] = _df_kw['구분'].map(lambda x: _TAG.get(x, x))
                            _df_kw = _df_kw[['구분', '키워드', 'PC검색량', '모바일검색량', '총검색량', '경쟁도']]
                        st.dataframe(
                            _df_kw.head(200).style.format(
                                {'PC검색량': '{:,}', '모바일검색량': '{:,}', '총검색량': '{:,}'}),
                            use_container_width=True, hide_index=True, height=640,
                        )
                    with _rc:
                        # ⚠️ render() 하단의 지역 `import plotly... as go`가 go를 지역변수로 만들어
                        #    모듈 상단 import를 가림 → 여기서도 사용 전 import 필요 (UnboundLocalError 방지)
                        import plotly.graph_objects as go
                        _ga = _h.get('ga')
                        if _ga and _ga.get('gender'):
                            _gf = _ga['gender'].get('여성', 0)
                            _gm = _ga['gender'].get('남성', 0)
                            st.markdown(f"**👫 성별 검색 비율** <span style='color:#999;font-size:12px'>"
                                        f"(쇼핑 {_ga.get('category','')} 기준)</span>", unsafe_allow_html=True)
                            _figg = go.Figure(go.Pie(
                                labels=['여성', '남성'], values=[_gf, _gm], hole=0.55,
                                marker=dict(colors=['#F2637B', '#5470F2']),
                                texttemplate='%{label} %{value}%', textposition='outside'))
                            _figg.update_layout(height=190, margin=dict(l=10, r=10, t=6, b=6),
                                                showlegend=False)
                            st.plotly_chart(_figg, use_container_width=True,
                                            key=f"kwga_g_{_hi}_{_h['q']}")
                            st.markdown("**👥 연령별 검색 비율**")
                            _ages = _ga.get('ages', {})
                            _figa = go.Figure(go.Bar(
                                x=list(_ages.keys()), y=list(_ages.values()),
                                text=[f"{v}%" for v in _ages.values()], textposition='outside',
                                marker_color='#7B8CF5'))
                            _figa.update_layout(height=200, margin=dict(l=10, r=10, t=14, b=6),
                                                yaxis=dict(visible=False))
                            st.plotly_chart(_figa, use_container_width=True,
                                            key=f"kwga_a_{_hi}_{_h['q']}")
                        elif _h.get('gaerr'):
                            st.caption(f"👫 성별·연령 조회 실패: {_h['gaerr']}")
                        # 📈 최근 12개월 검색량 추이 (Total / PC / Mobile)
                        _tr = _h.get('trend')
                        if _tr and _tr.get('months'):
                            st.markdown("**📈 최근 1년간 월별 검색량 추이**")
                            _figt = go.Figure()
                            for _nm2, _ys, _cl in (('Total', _tr['total'], '#E74C3C'),
                                                   ('PC', _tr['pc'], '#9AC1F5'),
                                                   ('Mobile', _tr['mo'], '#2E6FDB')):
                                _figt.add_trace(go.Scatter(
                                    x=_tr['months'], y=_ys, name=_nm2, mode='lines',
                                    line=dict(color=_cl, width=2)))
                            _figt.update_layout(
                                height=230, margin=dict(l=10, r=10, t=6, b=6),
                                legend=dict(orientation='h', yanchor='top', y=-0.25, x=0.2))
                            st.plotly_chart(_figt, use_container_width=True,
                                            key=f"kwga_t_{_hi}_{_h['q']}")
                            st.caption("💡 데이터랩 상대추이 × 검색광고 현재월 검색량 환산 **추정치**"
                                       if _tr.get('anchored') else
                                       "💡 현재월 절대 검색량이 없어 **상대지수(0~100)** 표시")
                        elif _h.get('terr'):
                            st.caption(f"📈 추이 조회 실패: {_h['terr']}")
                st.divider()
            if _kw_hist:
                st.caption("💡 구분: 🟡연관검색어(현재) · 🟢함께찾는(연관어) · ⚪자동완성 · "
                           "총검색량=PC+모바일 월간 검색수 · '< 10'은 10 미만")

    open_cid  = _gs('naver_open_client_id')
    open_csec = _gs('naver_open_client_secret')

    if not open_cid or not open_csec:
        st.warning("⚙️ 설정 탭 > 네이버 Open API에서 키워드 순위 체크용 API 키를 먼저 등록해주세요.")
        with st.expander("📋 발급 방법"):
            st.markdown("""
    1. [developers.naver.com](https://developers.naver.com) → 로그인
    2. **Application** → **애플리케이션 등록**
    3. 사용 API에서 **검색 > 쇼핑** 체크
    4. Client ID / Client Secret 복사 → 설정 탭에 입력
    > ⚠️ 네이버 커머스 API 키와 **별개**입니다. 새로 발급 필요.
    """)
    else:
        # API 키 정보 + 테스트 버튼
        _t1, _t2 = st.columns([3, 1])
        _t1.caption(f"🔑 Open API: ID `{open_cid[:8]}...` / Secret 길이 {len(open_csec)}자")
        if _t2.button("🧪 API 키 테스트", key="rk_test_api"):
            import requests as _rq
            try:
                _r = _rq.get(
                    "https://openapi.naver.com/v1/search/shop.json",
                    headers={"X-Naver-Client-Id": open_cid, "X-Naver-Client-Secret": open_csec},
                    params={"query": "테스트", "display": 1},
                    timeout=10
                )
                if _r.status_code == 200:
                    _items = _r.json().get('items', [])
                    st.success(f"✅ API 키 정상 (테스트 검색 결과 {len(_items)}건)")
                else:
                    _msg = _r.json().get('errorMessage', _r.text[:200]) if _r.text else ''
                    st.error(f"❌ API 오류 [{_r.status_code}]: {_msg}")
            except Exception as e:
                st.error(f"❌ 호출 실패: {e}")

    trackings = get_latest_ranks(USERNAME)

    # ── 새 키워드 추가 ──────────────────────────────────────
    with st.expander("➕ 새 키워드 추적 추가", expanded=not trackings):
        # 네이버 등록 상품 = 상품번호 있거나 from_naver=1
        _merged_all = cached_merged(USERNAME) or []
        all_prods = [
            p for p in _merged_all
            if (p.get('naver_product_no') and str(p['naver_product_no']).strip())
            or int(p.get('from_naver') or 0) == 1
        ]
        if not all_prods:   # 네이버번호 없는 상품만 있으면 전체 상품으로 폴백(선택 가능하게)
            all_prods = _merged_all
        if not all_prods:
            st.info("등록된 상품이 없습니다. 제품 DB 탭에서 상품을 먼저 가져오세요.")
        else:
            _rk_c1, _rk_c2 = st.columns(2)
            # 상품 검색 입력
            _prod_q = _rk_c1.text_input("상품 검색 (네이버 등록 상품)", placeholder="상품명 또는 키워드 입력", key="rk_prod_q")
            search_kw = _rk_c2.text_input("네이버 검색 키워드", placeholder="예: 코스트코 견과류", key="rk_kw")

            # 검색어로 필터링 — 있으면 부분/토큰 일치, 없으면 전체 목록.
            # 매칭이 0건이어도 전체 목록을 보여줘 항상 선택 가능하게 한다.
            _no_hit = False
            if _prod_q.strip():
                _q_lower = _prod_q.strip().lower()
                _q_tokens = [t for t in _q_lower.split() if len(t) >= 2]

                def _rk_score(p):
                    _txt = ((p.get('costco_name') or '') + ' '
                            + (p.get('match_keyword') or '')).lower()
                    if _q_lower in _txt:
                        return 1000  # 통째 부분일치 최우선
                    return sum(1 for t in _q_tokens if t in _txt)

                _scored = sorted(
                    ((p, _rk_score(p)) for p in all_prods),
                    key=lambda x: -x[1]
                )
                _hits = [p for p, s in _scored if s > 0][:30]   # 한 단어라도 겹치면 노출
                _filtered = _hits if _hits else all_prods[:50]
                _no_hit = not _hits
            else:
                _filtered = all_prods[:50]

            sel_p = None
            if _filtered:
                if _no_hit:
                    _rk_c1.caption("🔍 검색 일치 없음 — 아래 전체 목록에서 선택하세요.")
                _f_labels = [f"{p['costco_name']} ({p.get('match_keyword', '')})" for p in _filtered]
                _f_idx = _rk_c1.selectbox(
                    f"상품 선택 ({len(_filtered)}건)",
                    range(len(_filtered)),
                    format_func=lambda i: _f_labels[i],
                    key="rk_prod_sel"
                )
                sel_p = _filtered[_f_idx]
                _rk_c1.caption(f"선택됨: **{sel_p['costco_name']}**")

            # 스토어명: 저장된 값 자동 입력 (한 번 입력하면 고정)
            _saved_store = _gs('rank_store_name')
            store_nm = st.text_input("내 스토어명 (한 번 입력하면 자동 저장됨 · 매칭 정확도 향상)",
                                     value=_saved_store,
                                     placeholder="예: 코스트코핫딜", key="rk_store")
            if st.button("추적 추가", type="primary", key="rk_add",
                         disabled=(sel_p is None or not search_kw.strip())):
                # 추적 제목 = 네이버 등록 상품명 우선(naver_name) → costco_name → match_keyword
                _track_name = (sel_p.get('naver_name') or sel_p.get('costco_name')
                               or sel_p.get('match_keyword') or '').strip()
                # 중복 검사: 같은 (search_keyword + product_keyword) 추적이 이미 있으면 차단
                _dup = next((t for t in trackings
                             if t.get('search_keyword', '') == search_kw.strip()
                             and t.get('product_keyword', '') == _track_name), None)
                if _dup:
                    st.warning(f"⚠️ 이미 등록됨: '{search_kw}' / {_track_name} (id={_dup.get('id')})")
                else:
                    # 원상품번호(naver_origin_pno) 우선 — 수정 API가 직접 받는 번호
                    naver_pno = (sel_p.get('naver_origin_pno') or sel_p.get('naver_channel_pno')
                                 or sel_p.get('naver_product_no') or '')
                    # 스토어명 저장 (다음부터 자동 입력)
                    if store_nm.strip() and store_nm.strip() != _saved_store:
                        set_setting(USERNAME, 'rank_store_name', store_nm.strip())
                    add_keyword_tracking(
                        USERNAME, _track_name, search_kw.strip(),
                        naver_product_no=str(naver_pno), store_name=store_nm.strip()
                    )
                    st.success(f"✅ 추적 추가: {_track_name} / '{search_kw}'")
                    st.rerun()

    if not trackings:
        st.info("추적 중인 키워드가 없습니다. 위에서 추가하세요.")
    else:
        # ── 순위 체크 실행 헬퍼 (전체/선택 공용) ──────────────
        _rk_api_ready = HAS_NAVER_API and open_cid and open_csec

        def _run_rank_check(_targets):
            _targets = [t for t in (_targets or []) if t]
            if not _targets:
                st.warning("체크할 대상이 없습니다.")
                return
            _prog = st.progress(0, text="순위 체크 중...")
            _rk_errs = []
            _matched_n = 0
            _notfound_n = 0
            for _ri, _rt in enumerate(_targets):
                _prog.progress((_ri + 1) / len(_targets),
                               text=f"'{_rt['search_keyword']}' 확인 중... ({_ri+1}/{len(_targets)})")
                _r_wonbu, _r_compare, _r_solo, _rerr = naver_api.check_keyword_rank(
                    open_cid, open_csec, _rt['search_keyword'],
                    our_product_name=_rt['product_keyword'],
                    naver_product_no=_rt.get('naver_product_no', ''),
                    store_name=_rt.get('store_name', ''),
                )
                save_rank_result(USERNAME, _rt['id'], _r_wonbu, _r_solo, _r_compare)
                if _rerr:
                    _rk_errs.append(f"'{_rt['search_keyword']}': {_rerr}")
                elif _r_wonbu is not None or _r_compare is not None or _r_solo is not None:
                    _matched_n += 1
                    _info = naver_api.get_last_match_info()
                    if _info:
                        _rk_errs.append(f"ℹ️ {_info}")
                else:
                    _notfound_n += 1
            _prog.empty()
            _summary = f"✅ 체크 완료 — 매칭 성공 {_matched_n}건 / 미발견 {_notfound_n}건"
            if _rk_errs:
                st.session_state['_rk_check_log'] = _summary + "\n\n" + "\n".join(_rk_errs)
            else:
                st.session_state['_rk_check_log'] = _summary
            st.rerun()

        # ── 전체 순위 체크 버튼 ────────────────────────────
        _rk_h1, _rk_h2 = st.columns([4, 1])
        _rk_h1.subheader("📊 현재 순위 현황")
        if _rk_api_ready:
            if _rk_h2.button("🔄 전체 순위조회", type="primary", use_container_width=True, key="rk_check_all",
                             help="등록된 모든 추적 항목의 순위를 지금 조회"):
                _run_rank_check(trackings)
        elif not open_cid:
            _rk_h2.caption("API 키 미등록")

        # 체크 결과 로그 (직전 체크 디버그 정보)
        _rk_log = st.session_state.pop('_rk_check_log', None)
        if _rk_log:
            with st.expander("📋 직전 체크 결과/매칭 디버그", expanded=True):
                st.code(_rk_log)

        # ── 현재 월 일별 순위 표 ────────────────────────────
        import calendar as _cal
        from datetime import datetime as _dt
        _now = _dt.now()
        _yy, _mm = _now.year, _now.month
        _days_in_month = _cal.monthrange(_yy, _mm)[1]

        # 선택된 항목 집계 (체크박스)
        _sel_ids = [t['id'] for t in trackings if st.session_state.get(f"sel_t_{t['id']}", False)]

        # 상단 액션 바
        _act0, _act1, _act2, _act3 = st.columns([2, 2, 2, 4])
        _all_sel_now = bool(trackings) and all(
            st.session_state.get(f"sel_t_{t['id']}", False) for t in trackings)
        if _act0.button("◻️ 전체해제" if _all_sel_now else "☑️ 전체선택",
                        key="rk_sel_all_toggle", use_container_width=True,
                        help="모든 항목의 체크박스를 선택/해제"):
            for _tt in trackings:
                st.session_state[f"sel_t_{_tt['id']}"] = not _all_sel_now
            st.rerun()
        if _act1.button(f"✅ 선택 체크 ({len(_sel_ids)})",
                         disabled=not _sel_ids or not _rk_api_ready, key="rk_check_sel",
                         type="primary", use_container_width=True,
                         help="체크한 항목만 순위 확인"):
            _run_rank_check([t for t in trackings if t['id'] in _sel_ids])
        if _act2.button(f"🗑 선택 삭제 ({len(_sel_ids)})",
                         disabled=not _sel_ids, key="rk_bulk_del",
                         use_container_width=True):
            delete_trackings_bulk(USERNAME, _sel_ids)
            for _k in list(st.session_state.keys()):
                if _k.startswith('sel_t_'):
                    st.session_state.pop(_k, None)
            st.success(f"✅ {len(_sel_ids)}개 추적 삭제됨")
            st.rerun()

        st.markdown(f"##### 📅 {_yy}년 {_mm}월 일별 순위 (1~{_days_in_month}일)")
        st.caption("🟢 TOP10  🟠 TOP30  ⬜ 30위 초과  🔴 전일 대비 순위 하락  ☐ 미체크 — 상품명 클릭 시 1년 변동 추이 그래프")

        # 표 헤더
        _hdr = st.columns([0.4, 3.5, 1.8, 6.0, 1.2])
        _hdr[0].markdown("<b style='font-size:15px'>☐</b>", unsafe_allow_html=True)
        _hdr[1].markdown("<b style='font-size:15px'>상품</b>", unsafe_allow_html=True)
        _hdr[2].markdown("<b style='font-size:15px'>검색 KW</b>", unsafe_allow_html=True)
        _hdr[3].markdown(f"<b style='font-size:15px'>일별 순위 (1~{_days_in_month})</b>", unsafe_allow_html=True)
        _hdr[4].markdown("<b style='font-size:15px'>최신</b>", unsafe_allow_html=True)
        st.markdown("<hr style='margin:4px 0 8px 0;border-color:#ddd'>", unsafe_allow_html=True)

        # 저장된 네이버번호(채널/원 무엇이든) → 원상품번호(naver_origin_pno) 매핑
        # 기존 추적이 채널번호로 저장돼 있어도 수정 API가 받는 원번호로 변환
        _origin_by_no = {}
        for _mp in (cached_merged(USERNAME) or []):
            _op = str(_mp.get('naver_origin_pno') or '').strip()
            if not _op:
                continue
            for _mk in ('naver_origin_pno', 'naver_channel_pno', 'naver_product_no'):
                _mv = str(_mp.get(_mk) or '').strip()
                if _mv:
                    _origin_by_no[_mv] = _op

        for _t in trackings:
            _daily = get_daily_ranks_in_month(USERNAME, _t['id'], _yy, _mm)
            _row = st.columns([0.4, 3.5, 1.8, 6.0, 1.2])
            _row[0].checkbox("", key=f"sel_t_{_t['id']}", label_visibility="collapsed")
            # 상품명 버튼 (클릭하면 그래프)
            if _row[1].button(_t['product_keyword'],
                               key=f"name_btn_{_t['id']}",
                               help="클릭하면 1년 변동 추이 그래프 표시",
                               use_container_width=True):
                if st.session_state.get('rk_graph_tid') == _t['id']:
                    st.session_state.pop('rk_graph_tid', None)
                else:
                    st.session_state['rk_graph_tid'] = _t['id']
                st.rerun()
            if _row[2].button(f"✏️ {_t['search_keyword']}", key=f"kw_edit_btn_{_t['id']}",
                               help="클릭하면 검색 키워드/상품/스토어명을 수정합니다",
                               use_container_width=True):
                if st.session_state.get('_rk_edit_tid') == _t['id']:
                    st.session_state.pop('_rk_edit_tid', None)
                else:
                    st.session_state['_rk_edit_tid'] = _t['id']
                st.rerun()

            # 일별 순위 셀 (HTML 한 줄). 전일 대비 순위 하락 시 적색.
            _cells = []
            _prev_rank = None
            for _d in range(1, _days_in_month + 1):
                _info = _daily.get(_d)
                if _info is None:
                    _cells.append(
                        f'<span style="display:inline-flex;align-items:center;justify-content:center;'
                        f'width:30px;height:30px;color:#bbb;font-size:14px;'
                        f'background:#fafafa;border:1px solid #eee;'
                        f'border-radius:5px;margin:2px 2px">{_d}</span>'
                    )
                    # 미체크 일자에는 prev_rank 갱신하지 않음
                else:
                    _r = _info['best']
                    # 전일(데이터 있는 직전) 대비 순위 하락 (숫자 증가) 판정
                    _is_drop = _prev_rank is not None and _r > _prev_rank
                    _diff = (_r - _prev_rank) if _prev_rank is not None else 0
                    if _is_drop:
                        # 적색 — 순위 하락
                        _color = "#fff"; _bg = "#e74c3c"; _border = "#c0392b"
                        _tip_extra = f" ↓{_diff} (전일 {_prev_rank}위)"
                    elif _r <= 10:
                        _color = "#fff"; _bg = "#27ae60"; _border = "#229954"
                        _tip_extra = ""
                    elif _r <= 30:
                        _color = "#fff"; _bg = "#f39c12"; _border = "#d68910"
                        _tip_extra = ""
                    else:
                        _color = "#444"; _bg = "#e8e8e8"; _border = "#ccc"
                        _tip_extra = ""
                    _cells.append(
                        f'<span title="{_d}일: {_info["best_type"]} {_r}위{_tip_extra}" '
                        f'style="display:inline-flex;align-items:center;justify-content:center;'
                        f'width:30px;height:30px;color:{_color};font-size:14px;font-weight:700;'
                        f'background:{_bg};border:1px solid {_border};'
                        f'border-radius:5px;margin:2px 2px;'
                        f'box-shadow:0 1px 2px rgba(0,0,0,0.08)">{_r}</span>'
                    )
                    _prev_rank = _r
            _row[3].markdown(''.join(_cells), unsafe_allow_html=True)

            # 최신 순위 — 일별 집계(MIN) 대신 실제 최신 체크 값 사용
            # (일별 최선 순위가 아닌 가장 최근 체크 결과 → 알림과 일치)
            _lr_map = {
                'wonbu':   _t.get('rank_price_compare'),
                'compare': _t.get('rank_compare'),
                'solo':    _t.get('rank_total'),
            }
            _lr_vals = {k: v for k, v in _lr_map.items() if v is not None}
            if _lr_vals:
                _lr_type = min(_lr_vals, key=_lr_vals.get)
                _lr_best = _lr_vals[_lr_type]
                _row[4].markdown(
                    f"<b>{_lr_best}위</b><br><small>({_lr_type})</small>",
                    unsafe_allow_html=True
                )
            else:
                _row[4].markdown("<small>-</small>", unsafe_allow_html=True)

            # ── ✏️ 추적 항목 수정 (검색 KW 클릭 시) ──
            if st.session_state.get('_rk_edit_tid') == _t['id']:
                st.markdown(
                    "<div style='background:#fff8e1;border:1px solid #ffe082;border-radius:6px;"
                    "padding:8px 12px;margin:2px 0'>", unsafe_allow_html=True)
                _ec = st.columns([2.6, 2.6, 2.6, 1.0, 1.0])
                _e_kw = _ec[0].text_input("네이버 검색 키워드", value=_t['search_keyword'],
                                          key=f"e_kw_{_t['id']}")
                _e_prod = _ec[1].text_input("상품 키워드(매칭명)", value=_t['product_keyword'],
                                            key=f"e_prod_{_t['id']}")
                _e_store = _ec[2].text_input("내 스토어명", value=_t.get('store_name', '') or '',
                                             key=f"e_store_{_t['id']}")
                _ec[3].write(""); _ec[3].write("")
                if _ec[3].button("💾 저장", key=f"e_save_{_t['id']}", type="primary",
                                 use_container_width=True):
                    if _e_kw.strip() and _e_prod.strip():
                        update_keyword_tracking(
                            USERNAME, _t['id'],
                            search_keyword=_e_kw.strip(),
                            product_keyword=_e_prod.strip(),
                            store_name=_e_store.strip())
                        # 자동 닫힘 제거 — 저장 후 '네이버 상품명 변경'을 이어서 누를 수 있도록 패널 유지
                        st.toast("✅ 저장 완료 — 이어서 '네이버 상품명 변경' 가능", icon="✏️")
                        st.rerun()
                    else:
                        _ec[3].error("검색 키워드·상품 키워드는 비울 수 없습니다.")
                _ec[4].write(""); _ec[4].write("")
                if _ec[4].button("✖ 닫기", key=f"e_cancel_{_t['id']}", use_container_width=True):
                    st.session_state.pop('_rk_edit_tid', None)
                    st.rerun()

                # ── 🏪 네이버 스토어 상품명 실제 변경 (별도 버튼 — 로컬 저장과 독립) ──
                _npno_raw = str(_t.get('naver_product_no', '') or '').strip()
                _npno_edit = _origin_by_no.get(_npno_raw, _npno_raw)   # 채널→원상품번호 변환
                _has_keys = bool(api_id and api_secret)
                st.caption("🏪 아래 버튼은 위 '상품 키워드(매칭명)' 값으로 **네이버 스토어의 실제 상품명**을 "
                           "변경합니다. (로컬 저장과 별개 · 라이브 반영 · 검색 노출/순위에 영향 가능)")
                _sc1, _sc2 = st.columns([2, 4])
                if not _npno_edit:
                    _sc1.button("🏪 네이버 상품명 변경", key=f"e_napi_{_t['id']}",
                                disabled=True, use_container_width=True,
                                help="이 항목에 네이버 상품번호가 없어 스토어 수정 불가 (이름 매칭으로 추가된 항목)")
                    _sc2.caption("⚠️ 네이버 상품번호가 없는 항목입니다. '새 키워드 추적 추가'에서 상품을 선택해 등록하면 번호가 저장됩니다.")
                elif not _has_keys:
                    _sc1.button("🏪 네이버 상품명 변경", key=f"e_napi_{_t['id']}",
                                disabled=True, use_container_width=True,
                                help="설정 탭 > 네이버 커머스 API 키 등록 필요")
                    _sc2.caption("⚠️ 설정 탭 > 네이버 커머스 API 키(api_client_id/secret)를 먼저 등록하세요.")
                else:
                    if _sc1.button("🏪 네이버 상품명 변경", key=f"e_napi_{_t['id']}",
                                   type="secondary", use_container_width=True,
                                   help=f"스토어 상품(#{_npno_edit})명을 위 입력값으로 실제 변경"):
                        _new_nm = _e_prod.strip()
                        if not _new_nm:
                            _sc2.error("상품 키워드(매칭명)를 먼저 입력하세요.")
                        else:
                            with st.spinner("네이버 스토어 상품명 변경 중..."):
                                _okn, _errn, _usedno = naver_api.update_product_name(
                                    api_id, api_secret, _npno_edit, _new_nm)
                            if _okn:
                                # 로컬 라벨도 동기화 (스토어와 일치) — 패널은 유지, '닫기'로만 닫음
                                update_keyword_tracking(USERNAME, _t['id'], product_keyword=_new_nm)
                                st.session_state[f'_rk_napi_done_{_t["id"]}'] = f"✅ 네이버 스토어 상품명 변경 완료 (#{_usedno})"
                                st.rerun()
                            else:
                                _sc2.error(f"변경 실패: {_errn}")
                # 변경 완료 메시지 (rerun 후에도 패널에 표시 — 자동 닫힘 없음)
                _napi_msg = st.session_state.pop(f'_rk_napi_done_{_t["id"]}', None)
                if _napi_msg:
                    st.success(_napi_msg + " · '✖ 닫기'로 패널을 닫으세요.")

                # ── 🏷 연관태그 수정 (제품등록 태그 기능 재사용 · 기존 상품 태그 교체) ──
                if _npno_edit and _has_keys:
                    st.markdown("<hr style='margin:6px 0;border:none;border-top:1px dashed #ffd54f'>",
                                unsafe_allow_html=True)
                    st.caption("🏷 이 상품의 **네이버 연관태그(검색 노출용)**를 새로 생성/수정합니다. "
                               "(사전 등록 태그만 검색 반영 · 라이브 반영)")
                    _aikey_rk = _gs('anthropic_api_key')
                    _adc_rk = (_gs('naver_ad_api_key'), _gs('naver_ad_secret'), _gs('naver_ad_customer_id'))
                    _tgc1, _tgc2 = st.columns([2, 4])
                    if _tgc1.button("🤖 AI 태그 생성", key=f"rk_taggen_{_t['id']}",
                                    use_container_width=True, disabled=not _aikey_rk):
                        with st.spinner("AI 후보 → 태그사전 검증 → 제한태그 제거..."):
                            _tags_rk, _tinfo_rk = naver_api.build_seller_tags(
                                api_id, api_secret, _aikey_rk,
                                _e_prod.strip() or _t['product_keyword'], '', _e_kw.strip(),
                                ad_creds=_adc_rk if all(_adc_rk) else None)
                        st.session_state[f'_rk_tags_{_t["id"]}'] = _tags_rk
                        st.session_state[f'_rk_tinfo_{_t["id"]}'] = _tinfo_rk
                        if not _tags_rk:
                            st.warning(f"검증된 사전 태그 없음 (후보 {_tinfo_rk.get('candidates', 0)}개)")
                        st.rerun()
                    if not _aikey_rk:
                        _tgc2.caption("⚠️ 설정 탭 > 🤖 AI 설정에 Anthropic 키 필요")
                    elif not all(_adc_rk):
                        _tgc2.caption("💡 검색광고 키 넣으면 검색량순 정렬 (지금은 관련도순)")

                    _rk_tags = st.session_state.get(f'_rk_tags_{_t["id"]}')
                    if _rk_tags:
                        import pandas as _pdrk
                        _volmap_rk = (st.session_state.get(f'_rk_tinfo_{_t["id"]}', {})
                                      or {}).get('volumes', {}) or {}
                        _tdf_rk = _pdrk.DataFrame([
                            {"사용": True, "태그": _tg["text"],
                             "월검색량": int(_volmap_rk.get(_tg["text"], 0)), "태그ID": _tg.get("code")}
                            for _tg in _rk_tags])
                        _ed_rk = st.data_editor(
                            _tdf_rk, key=f"rk_tag_ed_{_t['id']}", hide_index=True,
                            use_container_width=True, num_rows="dynamic",
                            column_config={
                                "사용": st.column_config.CheckboxColumn("사용", default=True),
                                "태그": st.column_config.TextColumn("태그", required=True),
                                "월검색량": st.column_config.NumberColumn("월검색량", disabled=True),
                                "태그ID": st.column_config.NumberColumn(
                                    "태그ID", disabled=True,
                                    help="사전 등록 태그 ID(숫자 有 = 검색 반영). 빈 값 = 직접입력 태그."),
                            })
                        _sel_rk = []
                        try:
                            for _r in _ed_rk.to_dict("records"):
                                _tx = str(_r.get("태그") or "").strip()
                                if _r.get("사용") and _tx:
                                    _e = {"text": _tx}
                                    _cd = _r.get("태그ID")
                                    if _cd not in (None, "", 0) and not _pdrk.isna(_cd):
                                        _e["code"] = int(_cd)
                                    _sel_rk.append(_e)
                        except Exception:
                            _sel_rk = [{"code": _tg.get("code"), "text": _tg["text"]} for _tg in _rk_tags]
                        st.caption("체크 해제=제외 · 행 추가=직접입력 태그 · 적용 시 체크된 것만 반영.")
                        _tap1, _tap2 = st.columns([2, 4])
                        if _tap1.button(f"🏷 태그 적용 ({len(_sel_rk)}개)", key=f"rk_tagapply_{_t['id']}",
                                        type="primary", use_container_width=True):
                            with st.spinner("네이버 스토어 태그 변경 중..."):
                                _okt, _errt, _ut = naver_api.update_product_tags(
                                    api_id, api_secret, _npno_edit, _sel_rk)
                            if _okt:
                                st.session_state[f'_rk_tagdone_{_t["id"]}'] = (
                                    f"✅ 연관태그 {len(_sel_rk)}개 적용 완료 (#{_ut})")
                                st.session_state.pop(f'_rk_tags_{_t["id"]}', None)
                                st.session_state.pop(f'_rk_tinfo_{_t["id"]}', None)
                                st.rerun()
                            else:
                                _tap2.error(f"태그 변경 실패: {_errt}")
                    _tagdone = st.session_state.pop(f'_rk_tagdone_{_t["id"]}', None)
                    if _tagdone:
                        st.success(_tagdone + " · '✖ 닫기'로 패널을 닫으세요.")

                st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("<hr style='margin:6px 0;border:none;border-top:1px solid #f0f0f0'>", unsafe_allow_html=True)

        # ── 1년 그래프 (상품명 클릭 시 표시) ──────────────────
        _gtid = st.session_state.get('rk_graph_tid')
        if _gtid:
            _sel_t = next((t for t in trackings if t['id'] == _gtid), None)
            if _sel_t:
                st.divider()
                _gh1, _gh2 = st.columns([5, 1])
                _gh1.subheader(f"📈 {_sel_t['product_keyword']} — 1년 변동 추이")
                _gh1.caption(f"검색 키워드: {_sel_t['search_keyword']}")
                if _gh2.button("✖ 닫기", key="rk_graph_close"):
                    st.session_state.pop('rk_graph_tid', None)
                    st.rerun()

                _yh = get_yearly_rank_history(USERNAME, _gtid)
                if not _yh:
                    st.info("순위 이력이 없습니다. 자동 체크 또는 '전체 체크'를 실행해주세요.")
                else:
                    _hdf = pd.DataFrame(_yh)
                    _hdf['checked_at'] = pd.to_datetime(_hdf['checked_at'])
                    try:
                        import plotly.graph_objects as go
                        _fig = go.Figure()
                        if _hdf['rank_price_compare'].notna().any():
                            _fig.add_trace(go.Scatter(
                                x=_hdf['checked_at'], y=_hdf['rank_price_compare'],
                                mode='lines+markers', name='원부 순위',
                                line=dict(color='#e74c3c', width=2),
                                connectgaps=False,
                            ))
                        if 'rank_compare' in _hdf.columns and _hdf['rank_compare'].notna().any():
                            _fig.add_trace(go.Scatter(
                                x=_hdf['checked_at'], y=_hdf['rank_compare'],
                                mode='lines+markers', name='가격비교 순위',
                                line=dict(color='#ff7f0e', width=2),
                                connectgaps=False,
                            ))
                        if _hdf['rank_total'].notna().any():
                            _fig.add_trace(go.Scatter(
                                x=_hdf['checked_at'], y=_hdf['rank_total'],
                                mode='lines+markers', name='단독 상품 순위',
                                line=dict(color='#1f77b4', width=2),
                                connectgaps=False,
                            ))
                        _fig.update_layout(
                            yaxis=dict(autorange="reversed", title="순위 (낮을수록 좋음)"),
                            xaxis_title="날짜",
                            height=400,
                            legend=dict(orientation="h", yanchor="bottom", y=1.02),
                            margin=dict(l=40, r=20, t=20, b=40),
                        )
                        _fig.add_hline(y=10, line_dash="dot", line_color="green",
                                       annotation_text="TOP 10", annotation_position="right")
                        st.plotly_chart(_fig, use_container_width=True)
                    except ImportError:
                        _cols = [c for c in ['rank_price_compare', 'rank_compare', 'rank_total'] if c in _hdf.columns]
                        st.line_chart(_hdf.set_index('checked_at')[_cols])

                    with st.expander("📋 원본 데이터 (최근 1년)"):
                        _disp = _hdf.copy()
                        _disp['checked_at'] = _disp['checked_at'].dt.strftime('%Y-%m-%d %H:%M')
                        _disp = _disp.rename(columns={
                            'rank_price_compare': '원부',
                            'rank_compare': '가격비교',
                            'rank_total': '단독',
                            'checked_at': '체크 시각',
                        })
                        st.dataframe(_disp, use_container_width=True, hide_index=True)


    # ── 자동 체크 시간 설정 ──────────────────────────────
    st.divider()
    with st.expander("⏰ 자동 체크 시간 설정", expanded=False):
        st.caption("매일 지정된 시간에 Windows 작업 스케줄러가 순위 체크를 자동 실행합니다.")

        _rank_en = _gs('auto_rank_enabled') == '1'
        _rank_time_str = _gs('auto_rank_time') or '12:00'
        _rth, _rtm = [int(x) for x in _rank_time_str.split(':')]
        _PYTHON_PATH = sys.executable
        _SCRIPT_PATH = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', 'auto_task.py'
        )
        _TASK4_NAME = f"CostcoRank_{USERNAME}"

        def _rk_sc_run(args):
            try:
                r = subprocess.run(
                    ['schtasks'] + args, capture_output=True,
                    text=True, encoding='cp949', errors='replace'
                )
                return r.returncode == 0, (r.stdout + r.stderr).strip()
            except Exception as _e:
                return False, str(_e)

        _c1, _c2 = st.columns([1, 2])
        _new_en = _c1.checkbox("자동 체크 활성화", value=_rank_en, key="rk_auto_en")
        _new_time = _c2.time_input(
            "실행 시간",
            value=datetime.strptime(_rank_time_str, '%H:%M').time(),
            key="rk_auto_time"
        )

        _rc1, _rc2 = st.columns([2, 3])
        if _rc1.button("💾 저장 & 등록", key="rk_save_auto", type="primary", use_container_width=True):
            _t_str = _new_time.strftime('%H:%M')
            set_setting(USERNAME, 'auto_rank_enabled', '1' if _new_en else '0')
            set_setting(USERNAME, 'auto_rank_time', _t_str)
            if _new_en:
                _cmd4 = f'"{_PYTHON_PATH}" "{_SCRIPT_PATH}" --task rank --user {USERNAME}'
                _ok, _out = _rk_sc_run(['/create', '/tn', _TASK4_NAME, '/tr', _cmd4,
                                        '/sc', 'daily', '/st', _t_str, '/f'])
                if _ok:
                    st.success(f"✅ 매일 {_t_str}에 자동 순위 체크 등록 완료")
                else:
                    st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{_out}")
            else:
                _rk_sc_run(['/delete', '/tn', _TASK4_NAME, '/f'])
                st.info("자동 체크 비활성화 — 스케줄 삭제됨")
            st.rerun()

        # 현재 등록 상태 표시
        _t4_ok, _t4_out = _rk_sc_run(['/query', '/tn', _TASK4_NAME, '/fo', 'LIST'])
        if _t4_ok:
            st.success(f"✅ 현재 등록됨 — 매일 {_rank_time_str} 자동 실행")
        else:
            st.warning("⚠️ 미등록 — 저장 & 등록 버튼을 눌러 활성화하세요")

    # ═══════════════════════════════════════
