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
        all_prods = [
            p for p in cached_merged(USERNAME)
            if (p.get('naver_product_no') and str(p['naver_product_no']).strip())
            or int(p.get('from_naver') or 0) == 1
        ]
        if not all_prods:
            st.info("네이버에 등록된 상품이 없습니다. 제품 DB 탭에서 네이버 상품을 먼저 가져오세요.")
        else:
            _rk_c1, _rk_c2 = st.columns(2)
            # 상품 검색 입력
            _prod_q = _rk_c1.text_input("상품 검색 (네이버 등록 상품)", placeholder="상품명 또는 키워드 입력", key="rk_prod_q")
            search_kw = _rk_c2.text_input("네이버 검색 키워드", placeholder="예: 코스트코 견과류", key="rk_kw")

            # 검색어로 필터링
            if _prod_q.strip():
                _q_lower = _prod_q.strip().lower()
                _filtered = [
                    p for p in all_prods
                    if _q_lower in (p.get('costco_name') or '').lower()
                    or _q_lower in (p.get('match_keyword') or '').lower()
                ]
            else:
                _filtered = []

            sel_p = None
            if _prod_q.strip() and not _filtered:
                _rk_c1.warning("검색 결과 없음")
            elif _filtered:
                _f_labels = [f"{p['costco_name']} ({p['match_keyword']})" for p in _filtered]
                _f_idx = _rk_c1.selectbox(
                    f"검색 결과 {len(_filtered)}건",
                    range(len(_filtered)),
                    format_func=lambda i: _f_labels[i],
                    key="rk_prod_sel"
                )
                sel_p = _filtered[_f_idx]
                _rk_c1.caption(f"선택됨: **{sel_p['costco_name']}**")

            store_nm = st.text_input("내 스토어명 (선택 — 있으면 매칭 정확도 향상)",
                                     placeholder="예: 코스트코핫딜", key="rk_store")
            if st.button("추적 추가", type="primary", key="rk_add",
                         disabled=(sel_p is None or not search_kw.strip())):
                naver_pno = sel_p.get('naver_product_no') or ''  # 코스트코 product_no는 Naver ID가 아님
                add_keyword_tracking(
                    USERNAME, sel_p['match_keyword'], search_kw.strip(),
                    naver_product_no=str(naver_pno), store_name=store_nm.strip()
                )
                st.success(f"✅ 추적 추가: {sel_p['costco_name']} / '{search_kw}'")
                st.rerun()

    if not trackings:
        st.info("추적 중인 키워드가 없습니다. 위에서 추가하세요.")
    else:
        # ── 전체 순위 체크 버튼 ────────────────────────────
        _rk_h1, _rk_h2 = st.columns([4, 1])
        _rk_h1.subheader("📊 현재 순위 현황")
        if HAS_NAVER_API and open_cid and open_csec:
            if _rk_h2.button("🔄 전체 체크", type="primary", use_container_width=True, key="rk_check_all"):
                _prog = st.progress(0, text="순위 체크 중...")
                _rk_errs = []
                _matched_n = 0
                _notfound_n = 0
                for _ri, _rt in enumerate(trackings):
                    _prog.progress((_ri + 1) / len(trackings),
                                   text=f"'{_rt['search_keyword']}' 확인 중... ({_ri+1}/{len(trackings)})")
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
        _act1, _act2, _act3 = st.columns([2, 2, 6])
        if _act1.button(f"🗑 선택 삭제 ({len(_sel_ids)})",
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
            _row[2].markdown(f"<small>{_t['search_keyword']}</small>", unsafe_allow_html=True)

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
