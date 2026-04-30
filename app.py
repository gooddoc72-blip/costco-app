"""
코스트코핫딜 주문 수익 관리 시스템 v3 (Multi-user Web Edition)
- 로그인 / 멀티유저 / 엑셀 비밀번호 자동해제 / 네이버 커머스API 연동
- 배포: Streamlit Cloud or self-hosted
"""
import streamlit as st
import pandas as pd
import sqlite3
import os
import re
import io
import json
import math
import hashlib
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, time as dtime
import plotly.graph_objects as go

# 네이버 커머스 API (선택)
try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False

# ─── 기본 설정 ───
APP_TITLE = "📦 코스트코핫딜 주문 수익 관리"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUTH_DB = os.path.join(DATA_DIR, "auth.db")
os.makedirs(DATA_DIR, exist_ok=True)

st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide")

st.markdown("""
<style>
/* 제품 목록 행 간격 축소 */
div[data-testid="stHorizontalBlock"] {
    margin-bottom: -0.4rem;
}
</style>
""", unsafe_allow_html=True)

EXTRACT_COLS = ['수취인명','상품명','옵션정보','수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']

from utils import (
    fmt, to_id_str, extract_pack_qty, clean_name, has_meaningful_char,
    get_ngrams, calc_match_score, MIN_MATCH_SCORE, get_week_range, get_month_range,
)
from db import (
    init_auth_db, hash_pw, check_login, get_global_setting, set_global_setting,
    register_user, get_pending_users, approve_user, reject_user, get_all_users,
    add_user, delete_user, change_password, get_user_info,
    create_session, get_session_user, delete_session,
    get_shared_products, upsert_shared_product, delete_shared_product,
    get_user_db, init_user_db, get_setting, set_setting, get_all_products,
    upsert_user_private, get_all_products_merged, upsert_product,
    save_daily_orders, get_daily_orders, save_order_history, search_order_history,
    get_date_range_stats, get_monthly_stats, get_product_ranking, get_saved_dates,
    get_dashboard_kpi, get_daily_profit_trend, get_week_best_products,
    get_price_history_monthly, save_price_changes_to_history, get_price_change_history,
)
from services import (
    match_product_to_db, match_shared_product,
    update_product_info_from_orders, update_product_shipping_fees, update_product_sale_price,
    detect_price_changes, build_price_alert_msg,
    parse_costco_receipt_pdf, match_receipt_to_orders,
    decrypt_excel, read_excel_auto,
)


def _get_qparam(key, default=''):
    try:
        return st.query_params.get(key, default)
    except Exception:
        return st.experimental_get_query_params().get(key, [default])[0]

def _set_qparam(key, value):
    try:
        st.query_params[key] = value
    except Exception:
        st.experimental_set_query_params(**{key: value})

def _clear_qparams():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

init_auth_db()

# ═══════════════════════════════════════
# 로그인 화면
# ═══════════════════════════════════════
if 'user' not in st.session_state:
    st.session_state['user'] = None

if st.session_state['user'] is None:
    # URL 쿼리 파라미터로 저장된 세션 토큰 확인 → 자동 로그인
    _sid = _get_qparam('sid')
    if _sid:
        _auto_username = get_session_user(_sid)
        if _auto_username:
            _auto_user = get_user_info(_auto_username)
            if _auto_user:
                st.session_state['user'] = _auto_user
                st.session_state['_sid'] = _sid
                init_user_db(_auto_username)
                st.rerun()
        else:
            _clear_qparams()

    st.markdown("<h1 style='text-align:center;margin-top:60px'>📦 코스트코핫딜</h1>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align:center;color:gray'>주문 수익 관리 시스템</h3>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.6, 1])
    with col2:
        allow_signup = get_global_setting('allow_signup', '1')
        tab_labels = ["🔑 로그인", "📝 회원가입"] if allow_signup == '1' else ["🔑 로그인"]
        tabs = st.tabs(tab_labels)

        # ── 로그인 탭 ──
        with tabs[0]:
            with st.form("login_form"):
                username = st.text_input("아이디")
                password = st.text_input("비밀번호", type="password")
                remember_me = st.checkbox("자동 로그인 (30일간 유지)", value=True)
                submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")

                if submitted:
                    result = check_login(username, password)
                    if result == "pending":
                        st.warning("⏳ 관리자 승인 대기 중입니다. 잠시 후 다시 시도해주세요.")
                    elif result == "rejected":
                        st.error("❌ 가입 신청이 거절되었습니다. 관리자에게 문의하세요.")
                    elif result:
                        st.session_state['user'] = result
                        init_user_db(result['username'])
                        if remember_me:
                            _token = create_session(result['username'], days=30)
                            st.session_state['_sid'] = _token
                            _set_qparam('sid', _token)
                        st.rerun()
                    else:
                        st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

        # ── 회원가입 탭 ──
        if allow_signup == '1' and len(tabs) > 1:
            with tabs[1]:
                require_approval = get_global_setting('require_approval', '1')
                if require_approval == '1':
                    st.info("📋 가입 신청 후 관리자 승인이 완료되면 로그인 가능합니다.")
                else:
                    st.info("✅ 가입 즉시 로그인 가능합니다.")

                with st.form("signup_form"):
                    reg_id   = st.text_input("아이디 (영문·숫자, 4자 이상)")
                    reg_name = st.text_input("이름 / 업체명")
                    reg_pw   = st.text_input("비밀번호 (6자 이상)", type="password")
                    reg_pw2  = st.text_input("비밀번호 확인", type="password")
                    reg_ok   = st.form_submit_button("회원가입 신청", use_container_width=True, type="primary")

                    if reg_ok:
                        reg_id = reg_id.strip()
                        reg_name = reg_name.strip()
                        if len(reg_id) < 4 or not reg_id.isalnum():
                            st.error("아이디는 영문·숫자 4자 이상이어야 합니다.")
                        elif len(reg_pw) < 6:
                            st.error("비밀번호는 6자 이상이어야 합니다.")
                        elif reg_pw != reg_pw2:
                            st.error("비밀번호가 일치하지 않습니다.")
                        elif not reg_name:
                            st.error("이름/업체명을 입력해주세요.")
                        else:
                            ok, status = register_user(reg_id, reg_pw, reg_name)
                            if ok:
                                if status == 'active':
                                    st.success("✅ 가입 완료! 로그인 탭에서 로그인하세요.")
                                else:
                                    st.success("✅ 가입 신청 완료! 관리자 승인 후 로그인 가능합니다.")
                                init_user_db(reg_id)
                            else:
                                st.error("이미 사용 중인 아이디입니다.")

    st.stop()


# ═══════════════════════════════════════
# 로그인 후 메인 화면
# ═══════════════════════════════════════
user = st.session_state['user']
USERNAME = user['username']
IS_ADMIN = user['is_admin']
excel_pw = get_setting(USERNAME, 'excel_password')
api_id = get_setting(USERNAME, 'api_client_id')
api_secret = get_setting(USERNAME, 'api_client_secret')

# 사이드바
with st.sidebar:
    st.title(APP_TITLE)
    st.caption(f"👤 {user['display_name']} ({USERNAME})")
    st.divider()

    menus = ["🏠 홈", "📋 주문 업로드", "📮 송장번호 등록", "🧾 영수증 등록", "💰 수익 계산", "📊 대시보드", "📦 제품 DB", "🛍 네이버 등록", "⚙️ 설정", "🤖 자동화"]
    if IS_ADMIN:
        menus.append("👑 관리자")
    tab_choice = st.radio("메뉴", menus, label_visibility="collapsed", key="main_tab")

    st.divider()
    ship = get_setting(USERNAME, 'shipping_cost')
    box = get_setting(USERNAME, 'box_cost')
    st.caption(f"택배비: {fmt(int(ship) if ship else 0)}원 | 박스비: {fmt(int(box) if box else 0)}원")

    if st.button("🚪 로그아웃", use_container_width=True):
        _sid_to_del = st.session_state.get('_sid')
        if _sid_to_del:
            delete_session(_sid_to_del)
        _clear_qparams()
        st.session_state.clear()
        st.rerun()


# ═══════════════════════════════════════
# 홈 대시보드
# ═══════════════════════════════════════
if tab_choice == "🏠 홈":
    today = datetime.today()
    w_start, w_end = get_week_range()
    m_start, m_end = get_month_range()

    kpi = get_dashboard_kpi(USERNAME)
    wk = kpi['week']
    mk = kpi['month']
    lwk = kpi['last_week']
    lmk = kpi['last_month']

    # ── KPI 카드 4개 ──
    c1, c2, c3, c4 = st.columns(4)
    w_delta = f"{((wk['profit']-lwk['profit'])/abs(lwk['profit'])*100):+.1f}%" if lwk['profit'] != 0 else None
    m_delta = f"{((mk['profit']-lmk['profit'])/abs(lmk['profit'])*100):+.1f}%" if lmk['profit'] != 0 else None
    c1.metric("📅 이번 주 수익", f"{fmt(wk['profit'])}원", delta=w_delta, help=f"{w_start} ~ {w_end}")
    c2.metric("📆 이번 달 수익", f"{fmt(mk['profit'])}원", delta=m_delta, help=f"{m_start} ~ {m_end}")
    c3.metric("📦 주간 주문건수", f"{wk['cnt']}건", delta=f"전주 {lwk['cnt']}건" if lwk['cnt'] else None, delta_color="off")
    c4.metric("📦 월간 주문건수", f"{mk['cnt']}건", delta=f"전달 {lmk['cnt']}건" if lmk['cnt'] else None, delta_color="off")

    st.divider()

    # ── 일별 수익 추이 (최근 14일) ──
    st.subheader("📈 일별 수익 추이 (최근 14일)")
    daily = get_daily_profit_trend(USERNAME, days=14)
    if daily:
        all_dates = pd.date_range(
            start=(today - timedelta(days=13)).strftime("%Y-%m-%d"),
            end=today.strftime("%Y-%m-%d"), freq='D'
        )
        ddf = pd.DataFrame(daily)
        ddf['order_date'] = pd.to_datetime(ddf['order_date'])
        ddf = ddf.set_index('order_date').reindex(all_dates, fill_value=0).reset_index()
        ddf.rename(columns={'index': 'date'}, inplace=True)
        bar_colors = ['#E74C3C' if v < 0 else '#1D9E75' for v in ddf['total_profit']]
        fig_daily = go.Figure()
        fig_daily.add_trace(go.Bar(
            x=ddf['date'], y=ddf['total_profit'],
            name='순수익', marker_color=bar_colors,
            text=ddf['total_profit'].apply(lambda x: f"{x:,.0f}" if x != 0 else ''),
            textposition='outside', textfont=dict(size=10),
        ))
        fig_daily.add_trace(go.Scatter(
            x=ddf['date'], y=ddf['cnt'], name='주문건수', yaxis='y2',
            mode='lines+markers',
            line=dict(color='#7F77DD', width=2, dash='dot'),
            marker=dict(size=6),
        ))
        fig_daily.update_layout(
            height=380, margin=dict(l=10, r=10, t=20, b=40),
            plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            yaxis=dict(title='순수익 (원)', tickformat=',', gridcolor='rgba(0,0,0,0.06)'),
            yaxis2=dict(title='주문건수', overlaying='y', side='right', showgrid=False),
            xaxis=dict(tickformat='%m/%d', dtick='D1'), bargap=0.3,
        )
        st.plotly_chart(fig_daily, use_container_width=True)
    else:
        st.info("📋 저장된 데이터가 없습니다. 주문 업로드 → 수익 계산 → 저장 순으로 진행하세요.")

    # ── 주간 베스트 / 월간 수익 추이 ──
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader(f"🏆 주간 베스트 상품 ({w_start[5:]} ~ {w_end[5:]})")
        best = get_week_best_products(USERNAME)
        if best:
            bdf = pd.DataFrame(best)
            short_names = [n[:22] + ('…' if len(n) > 22 else '') for n in bdf['product_name']]
            fig_best = go.Figure(go.Bar(
                y=short_names, x=bdf['total_profit'], orientation='h',
                marker_color=['#1D9E75' if v >= 0 else '#E74C3C' for v in bdf['total_profit']],
                text=bdf['total_profit'].apply(lambda x: f"{x:,.0f}원"),
                textposition='inside', textfont=dict(color='white', size=11),
            ))
            fig_best.update_layout(
                height=240, margin=dict(l=10, r=10, t=10, b=10),
                plot_bgcolor='rgba(0,0,0,0)',
                yaxis=dict(autorange='reversed'),
                xaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)'),
            )
            st.plotly_chart(fig_best, use_container_width=True)
            # 상세 테이블 (HTML, 상품명 전체 표시)
            rows_b = []
            for rank, row in enumerate(best, 1):
                pc = '#1D9E75' if row['total_profit'] >= 0 else '#E74C3C'
                rows_b.append(
                    f'<tr style="border-bottom:1px solid #f0f0f0">'
                    f'<td style="padding:5px 8px;text-align:center;font-weight:bold;color:#888">{rank}</td>'
                    f'<td style="padding:5px 8px;white-space:normal;word-break:break-word">{row["product_name"]}</td>'
                    f'<td style="padding:5px 8px;text-align:right">{int(row["total_qty"]):,}개</td>'
                    f'<td style="padding:5px 8px;text-align:right;font-weight:bold;color:{pc}">{int(row["total_profit"]):,}원</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
                f'<thead><tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6">'
                f'<th style="padding:6px 8px;width:28px">#</th>'
                f'<th style="padding:6px 8px;text-align:left">상품명</th>'
                f'<th style="padding:6px 8px;text-align:right">수량</th>'
                f'<th style="padding:6px 8px;text-align:right">수익</th></tr></thead>'
                f'<tbody>{"".join(rows_b)}</tbody></table>',
                unsafe_allow_html=True
            )
        else:
            st.info("이번 주 데이터가 없습니다.")

    with col_right:
        st.subheader("📊 월간 수익 추이 (최근 6개월)")
        monthly = get_monthly_stats(USERNAME)
        if monthly:
            mdf = pd.DataFrame(monthly).tail(6)
            m_colors = ['#E74C3C' if v < 0 else '#7F77DD' for v in mdf['total_profit']]
            fig_month = go.Figure()
            fig_month.add_trace(go.Bar(
                x=mdf['month'], y=mdf['total_profit'], name='월 수익',
                marker_color=m_colors,
                text=mdf['total_profit'].apply(lambda x: f"{x/10000:.1f}만"),
                textposition='outside',
            ))
            fig_month.add_trace(go.Scatter(
                x=mdf['month'], y=mdf['cnt'], name='주문건수', yaxis='y2',
                mode='lines+markers',
                line=dict(color='#FF7F0E', width=2), marker=dict(size=8),
            ))
            fig_month.update_layout(
                height=300, margin=dict(l=10, r=10, t=10, b=40),
                plot_bgcolor='rgba(0,0,0,0)',
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                yaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)'),
                yaxis2=dict(overlaying='y', side='right', showgrid=False), bargap=0.35,
            )
            st.plotly_chart(fig_month, use_container_width=True)

            # 월별 합계 테이블
            mdf_disp = mdf[['month','cnt','total_sales','total_profit']].copy()
            mdf_disp.columns = ['월','주문건','매출','순수익']
            rows_m = []
            for _, row in mdf_disp.iterrows():
                pc = '#1D9E75' if row['순수익'] >= 0 else '#E74C3C'
                rows_m.append(
                    f'<tr style="border-bottom:1px solid #f0f0f0">'
                    f'<td style="padding:5px 10px">{row["월"]}</td>'
                    f'<td style="padding:5px 10px;text-align:right">{int(row["주문건"]):,}건</td>'
                    f'<td style="padding:5px 10px;text-align:right">{int(row["매출"]):,}원</td>'
                    f'<td style="padding:5px 10px;text-align:right;font-weight:bold;color:{pc}">{int(row["순수익"]):,}원</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
                f'<thead><tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6">'
                f'<th style="padding:6px 10px;text-align:left">월</th>'
                f'<th style="padding:6px 10px;text-align:right">주문건</th>'
                f'<th style="padding:6px 10px;text-align:right">매출</th>'
                f'<th style="padding:6px 10px;text-align:right">순수익</th></tr></thead>'
                f'<tbody>{"".join(rows_m)}</tbody></table>',
                unsafe_allow_html=True
            )
        else:
            st.info("월별 데이터가 없습니다.")

    # ── 이번 달 가격 변동 이력 ──
    st.divider()
    st.subheader(f"💹 이번 달 가격 변동 이력 ({today.strftime('%Y년 %m월')})")
    ph = get_price_history_monthly(USERNAME)
    if ph:
        rows_ph = []
        for row in ph:
            old_p, new_p = int(row['old_price']), int(row['new_price'])
            is_up = new_p > old_p
            arrow = '▲' if is_up else '▼'
            color = '#1D9E75' if is_up else '#E74C3C'
            pct = f"{((new_p - old_p) / old_p * 100):+.1f}%" if old_p > 0 else '-'
            rows_ph.append(
                f'<tr style="border-bottom:1px solid #f0f0f0">'
                f'<td style="padding:6px 10px;white-space:nowrap;font-size:12px;color:#888">{row["created_at"]}</td>'
                f'<td style="padding:6px 10px;white-space:normal;word-break:break-word">{row["product_name"]}</td>'
                f'<td style="padding:6px 10px;text-align:right">{old_p:,}원</td>'
                f'<td style="padding:6px 10px;text-align:right;font-weight:bold">{new_p:,}원</td>'
                f'<td style="padding:6px 10px;text-align:center;font-weight:bold;color:{color}">{arrow} {pct}</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#666">{row["reason"]}</td>'
                f'</tr>'
            )
        ths = ['일시', '상품명', '변경 전', '변경 후', '변동폭', '사유']
        thead = ''.join(
            f'<th style="padding:7px 10px;background:#f8f9fa;text-align:{"right" if h in ("변경 전","변경 후") else "center" if h=="변동폭" else "left"};'
            f'font-weight:600;white-space:nowrap;border-bottom:2px solid #dee2e6">{h}</th>'
            for h in ths
        )
        st.markdown(
            f'<div style="overflow-x:auto;border:1px solid #dee2e6;border-radius:4px">'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'<thead><tr>{thead}</tr></thead>'
            f'<tbody>{"".join(rows_ph)}</tbody></table></div>',
            unsafe_allow_html=True
        )
        # 가격변동 막대 차트 (최근 변동 top10)
        if len(ph) >= 2:
            ph_chart = ph[:10]
            names_ph = [r['product_name'][:18] + ('…' if len(r['product_name']) > 18 else '') for r in ph_chart]
            diffs = [int(r['new_price']) - int(r['old_price']) for r in ph_chart]
            fig_ph = go.Figure(go.Bar(
                y=names_ph, x=diffs, orientation='h',
                marker_color=['#1D9E75' if d > 0 else '#E74C3C' for d in diffs],
                text=[f"{d:+,}원" for d in diffs],
                textposition='inside', textfont=dict(color='white', size=11),
            ))
            fig_ph.update_layout(
                height=max(200, len(ph_chart) * 36),
                margin=dict(l=10, r=10, t=20, b=10),
                plot_bgcolor='rgba(0,0,0,0)',
                title=dict(text='가격 변동액 (최근 10건)', font=dict(size=13)),
                yaxis=dict(autorange='reversed'),
                xaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)', title='변동액 (원)'),
            )
            st.plotly_chart(fig_ph, use_container_width=True)
    else:
        st.info("이번 달 가격 변동 이력이 없습니다.")


# ═══════════════════════════════════════
# 탭 1: 주문 업로드
# ═══════════════════════════════════════
elif tab_choice == "📋 주문 업로드":
    st.header("📋 주문 파일 업로드")

    # ── API 자동 조회 ──
    if HAS_NAVER_API and api_id and api_secret:
        c_api1, c_api2 = st.columns([2, 1])
        with c_api1:
            status_options = {"배송준비 (발주확인)": "READY", "결제완료 (신규주문)": "PAYED", "전체 (신규+배송준비)": "ALL"}
            status_label = st.selectbox("주문 상태", list(status_options.keys()), index=0)
            status_type = status_options[status_label]
        with c_api2:
            st.write("")
            st.write("")
            fetch_btn = st.button("🔄 API로 주문 자동 조회", type="primary", key="api_fetch")
        hours = 48  # 항상 최근 48시간 조회
        if fetch_btn:
            all_orders = []
            types_to_query = ["READY", "PAYED"] if status_type == "ALL" else [status_type]
            
            with st.spinner("네이버 커머스 API에서 주문을 조회 중..."):
                for st_type in types_to_query:
                    orders, err = naver_api.get_new_orders(api_id, api_secret, hours_back=hours, status_type=st_type)
                    if orders:
                        all_orders.extend(orders)
                    elif err:
                        if err.startswith("DEBUG_RESP:"):
                            st.caption(f"🔍 API 응답: {err[11:]}")
                        else:
                            st.warning(f"{st_type} 조회: {err}")
            
            if not all_orders:
                st.info("조회된 주문이 없습니다.")
            else:
                # 1. 원본 데이터 생성 및 중복 제거
                raw_df = pd.DataFrame(all_orders)
                raw_df = raw_df.drop_duplicates(subset=['상품주문번호'], keep='last')
                
                # 2. 송장등록/엑셀다운로드용 원본 저장 (1회만)
                st.session_state['order_full'] = raw_df.copy()
                
                # 3. 화면 출력용 df
                df = raw_df.copy()
                for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
                df = df.sort_values('상품명').reset_index(drop=True)
                
                # 4. 상품번호 기반 매칭 + 구입가격 계산
                costs = []
                for _, r in df.iterrows():
                    p_no = str(r.get('상품번호', '')) if r.get('상품번호') else ''
                    p = match_product_to_db(USERNAME, r['상품명'], product_no=p_no)
                    costs.append(p['unit_price'] * r['수량'] if p else 0)
                    # 상품번호가 있고 DB에 매칭된 제품이 있으면 상품번호 연결
                    if p_no and p:
                        upsert_product(USERNAME, p['costco_name'], p['match_keyword'], p['unit_price'], product_no=p_no)
                df['구입가격'] = costs

                st.session_state['orders'] = df
                st.session_state['order_date'] = datetime.today().strftime("%Y-%m-%d")
                
                s_cost = int(get_setting(USERNAME, 'shipping_cost') or 1800)
                b_cost = int(get_setting(USERNAME, 'box_cost') or 300)
                save_daily_orders(USERNAME, st.session_state['order_date'], df, s_cost, b_cost)
                # 주문 이력 누적 저장
                hist_saved = save_order_history(USERNAME, raw_df, cost_df=df)
                # 제품DB 배송비·판매가 자동 업데이트 (한 번에)
                fee_upd, sale_upd = update_product_info_from_orders(USERNAME, raw_df)
                notes = []
                if hist_saved: notes.append(f"이력 {hist_saved}건 저장")
                if fee_upd:    notes.append(f"배송비 {fee_upd}건 업데이트")
                if sale_upd:   notes.append(f"판매가 {sale_upd}건 업데이트")
                if notes:
                    st.caption(f"💡 제품 DB: {' / '.join(notes)}")

                st.success(f"✅ API에서 {len(df)}건 주문 조회 완료!")
                st.rerun()
        st.divider()
    elif not HAS_NAVER_API:
        st.caption("💡 naver_api.py 파일과 bcrypt, pybase64 패키지를 설치하면 API 자동 조회를 사용할 수 있습니다.")
    elif not api_id:
        st.caption("💡 설정에서 API 키를 등록하면 자동 주문 조회를 사용할 수 있습니다.")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        uploaded = st.file_uploader("네이버 스마트스토어 발주발송관리 xlsx 파일", type=['xlsx', 'xls'], key="order_upload")
    with col2:
        order_date = st.date_input("주문 날짜", value=datetime.today())
    with col3:
        input_pw = st.text_input("엑셀 비밀번호", value=excel_pw, type="password", key="upload_pw")

    if uploaded:
        use_pw = input_pw or excel_pw
        df, err = read_excel_auto(uploaded, use_pw)
        if df is None:
            st.error(f"❌ {err}")
            if "비밀번호" in str(err):
                st.info("비밀번호를 확인하고 오른쪽 입력란에 다시 입력해주세요.")
        else:
            missing = [c for c in EXTRACT_COLS if c not in df.columns]
            if missing:
                st.error(f"필요한 컬럼이 없습니다: {missing}")
            else:
                # 송장번호 등록용 전체 데이터 저장 (상품주문번호 포함)
                if '상품주문번호' in df.columns:
                    st.session_state['order_full'] = df.copy()

                df = df[EXTRACT_COLS].copy()
                for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
                df = df.sort_values('상품명').reset_index(drop=True)
                costs = []
                for _, r in df.iterrows():
                    p_no = str(r.get('상품번호', '')) if '상품번호' in df.columns and r.get('상품번호') else ''
                    p = match_product_to_db(USERNAME, r['상품명'], product_no=p_no)
                    costs.append(p['unit_price'] * r['수량'] if p else 0)
                df['구입가격'] = costs

                st.session_state['orders'] = df
                st.session_state['order_date'] = order_date.strftime("%Y-%m-%d")

                s_cost = int(get_setting(USERNAME, 'shipping_cost') or 1800)
                b_cost = int(get_setting(USERNAME, 'box_cost') or 300)
                save_daily_orders(USERNAME, st.session_state['order_date'], df, s_cost, b_cost)
                # 주문 이력 누적 저장 (order_full 우선, 없으면 df 사용)
                full_src = st.session_state.get('order_full')
                src = full_src if full_src is not None else df
                hist_saved = save_order_history(USERNAME, src, cost_df=df)
                fee_upd, sale_upd = update_product_info_from_orders(USERNAME, src)
                notes = []
                if hist_saved: notes.append(f"이력 {hist_saved}건 저장")
                if fee_upd:    notes.append(f"배송비 {fee_upd}건 업데이트")
                if sale_upd:   notes.append(f"판매가 {sale_upd}건 업데이트")
                if notes:
                    st.caption(f"💡 제품 DB: {' / '.join(notes)}")

    if 'orders' in st.session_state and st.session_state['orders'] is not None:
        df = st.session_state['orders']
        order_date_str = st.session_state.get('order_date', datetime.today().strftime("%Y-%m-%d"))

        st.subheader(f"📦 주문 목록 ({len(df)}건)")
        
        if 'order_full' in st.session_state and st.session_state['order_full'] is not None:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                st.session_state['order_full'].to_excel(writer, index=False)
            output.seek(0)
            st.download_button(
                label="📥 배송준비건 엑셀 다운로드 (비밀번호 없음)",
                data=output,
                file_name=f"발주발송관리_{order_date_str}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="secondary"
            )

        st.dataframe(df[['수취인명','상품명','옵션정보','수량','최종 상품별 총 주문금액','배송비 합계','정산예정금액']],
                   use_container_width=True, hide_index=True)

        st.subheader("🛒 코스트코 장보기 목록")
        shop_cols = ['상품번호', '상품명', '옵션정보', '수량']
        available_cols = [c for c in shop_cols if c in df.columns]
        shopping = df[available_cols].copy()
        shopping['옵션정보'] = shopping['옵션정보'].fillna('') if '옵션정보' in shopping.columns else ''

        # ── 집계: 상품번호·상품명·옵션정보가 모두 같아야 한 묶음 ──
        group_cols = [c for c in ['상품번호', '상품명', '옵션정보'] if c in shopping.columns]
        shopping = shopping.groupby(group_cols, sort=True, dropna=False).agg({'수량': 'sum'}).reset_index()
        shopping.columns = group_cols + ['주문수량']

        # ── 묶음수량 추출 (옵션/상품명 기반) ──
        shopping['묶음수량'] = shopping.apply(
            lambda r: extract_pack_qty(r.get('옵션정보', ''), r['상품명']), axis=1)

        # ── DB 단가 + 분리수량 조회 ──
        db_prices, db_splits = [], []
        for _, r in shopping.iterrows():
            p = match_product_to_db(USERNAME, r['상품명'], product_no=r.get('상품번호', ''))
            if p:
                sq = max(1, int(p.get('split_qty', 1) or 1))
                db_prices.append(p['unit_price'])
                db_splits.append(sq)
            else:
                db_prices.append(None)
                db_splits.append(1)
        shopping['팩단가'] = db_prices      # 코스트코 팩 전체 가격
        shopping['분리수량'] = db_splits    # 팩 1개 → 몇 개 분리 판매

        # ── 코스트코 구매수량 계산 ──
        # 분리판매(split_qty>1): ceil(주문수량 / 분리수량) 팩
        # 묶음판매(pack_qty>1) : 주문수량 × 묶음수량 개
        # 일반            : 주문수량 개
        def _costco_qty(row):
            sq = int(row['분리수량'])
            pq = int(row['묶음수량'])
            if sq > 1:
                return math.ceil(int(row['주문수량']) / sq)
            return int(row['주문수량']) * pq
        shopping['코스트코구매수량'] = shopping.apply(_costco_qty, axis=1)

        # ── 예상금액 계산 ──
        # 분리판매: 코스트코팩수 × 팩단가
        # 묶음/일반: 코스트코구매수량 × (팩단가/분리수량=1)
        def _expected_cost(row):
            if pd.isna(row['팩단가']) or not row['팩단가']:
                return None
            sq = int(row['분리수량'])
            return int(row['코스트코구매수량']) * int(row['팩단가'])
        shopping['예상금액'] = shopping.apply(_expected_cost, axis=1)

        # ── 표시 컬럼 구성 ──
        has_split = (shopping['분리수량'] > 1).any()
        has_multi = (shopping['묶음수량'] > 1).any()
        disp_cols = [c for c in ['상품번호', '상품명', '옵션정보'] if c in shopping.columns]
        disp_cols += ['주문수량']
        if has_split:
            disp_cols += ['분리수량']
        if has_multi:
            disp_cols += ['묶음수량']
        if has_split or has_multi:
            disp_cols += ['코스트코구매수량']
        disp_cols += ['팩단가', '예상금액']

        # ── HTML 테이블로 렌더링 ──
        num_cols = {'주문수량', '분리수량', '묶음수량', '코스트코구매수량', '팩단가', '예상금액'}
        # 분리 행: 하늘색, 묶음 행: 노란색
        def _row_bg(row):
            if int(row.get('분리수량', 1)) > 1:
                return '#d6eaf8'  # 분리판매 → 하늘색
            if int(row.get('묶음수량', 1)) > 1:
                return '#fff3cd'  # 묶음판매 → 노란색
            return 'white'

        # 코스트코구매수량 헤더: 분리 시 "팩구매수", 묶음 시 "코스트코구매수량"
        col_labels = {}
        if has_split:
            col_labels['코스트코구매수량'] = '코스트코팩구매'
        if has_split:
            col_labels['팩단가'] = '팩단가'

        th_cells = ''.join(
            f'<th style="background:#f8f9fa;padding:7px 12px;border-bottom:2px solid #dee2e6;'
            f'font-weight:600;white-space:nowrap;text-align:{"right" if c in num_cols else "left"}">'
            f'{col_labels.get(c, c)}</th>'
            for c in disp_cols
        )
        row_htmls = []
        for _, row in shopping[disp_cols].iterrows():
            bg = _row_bg(row)
            sq = int(row.get('분리수량', 1))
            tds = []
            for c in disp_cols:
                v = row[c]
                is_num = c in num_cols
                if pd.isna(v) or v == '' or v is None:
                    display = '-'
                elif is_num:
                    try:
                        iv = int(v)
                        # 팩구매수에 단위 표시
                        if c == '코스트코구매수량' and sq > 1:
                            display = f'{iv:,}팩'
                        else:
                            display = f'{iv:,}'
                    except Exception:
                        display = str(v)
                else:
                    display = str(v)
                align = 'right' if is_num else 'left'
                tds.append(
                    f'<td style="background:{bg};padding:6px 12px;border-bottom:1px solid #e9ecef;'
                    f'white-space:normal;word-break:break-word;text-align:{align}">{display}</td>'
                )
            row_htmls.append(f'<tr>{"".join(tds)}</tr>')

        st.markdown(
            f'<div style="overflow-x:auto;border:1px solid #dee2e6;border-radius:4px;margin-bottom:8px">'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'<thead><tr>{th_cells}</tr></thead>'
            f'<tbody>{"".join(row_htmls)}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True
        )

        captions = []
        if has_split:
            captions.append("🔵 파란색 행 = 소분판매 (코스트코팩구매 = ceil(주문수량 ÷ 소분수량))")
        if has_multi:
            captions.append("🟡 노란색 행 = 묶음상품 (코스트코구매수량 = 주문수량 × 묶음수량)")
        for cap in captions:
            st.caption(cap)

        c1, c2 = st.columns(2)
        c1.metric("예상 구매 총액", f"{fmt(shopping['예상금액'].dropna().sum())}원")
        c2.metric("단가 미등록 상품", f"{shopping['팩단가'].isna().sum()}종")

        # 휴대폰으로 장보기 목록 전송
        kakao_token = get_setting(USERNAME, 'kakao_access_token')
        tg_token = get_setting(USERNAME, 'telegram_token')
        tg_chat = get_setting(USERNAME, 'telegram_chat_id')

        if st.button("📱 장보기 목록 휴대폰 전송", key="send_shopping"):
            order_date_obj = datetime.strptime(order_date_str, "%Y-%m-%d")
            lines = [f"🛒 코스트코 장보기 목록 ({order_date_obj.strftime('%m/%d')})", ""]
            for _, r in shopping.iterrows():
                opt = f"({r['옵션정보']})" if r.get('옵션정보') else ""
                sq = int(r.get('분리수량', 1))
                pq = int(r.get('묶음수량', 1))
                buy_qty = int(r['코스트코구매수량'])
                order_qty = int(r['주문수량'])
                if sq > 1:
                    qty_str = f"{buy_qty}팩 (주문{order_qty}건÷{sq}소분)"
                elif pq > 1:
                    qty_str = f"{buy_qty}개 (주문{order_qty}건×{pq}구)"
                else:
                    qty_str = f"{buy_qty}개"
                name_part = " ".join(p for p in [r['상품명'][:22], opt] if p)
                lines.append(f"▪ {name_part} × {qty_str}")
            lines.append(f"\n💰 예상 총액: {fmt(shopping['예상금액'].dropna().sum())}원")
            lines.append(f"📦 총 {len(df)}건")
            msg = "\n".join(lines)
            
            sent_ok = False
            if kakao_token:
                kakao_api_key = get_setting(USERNAME, 'kakao_api_key')
                kakao_refresh = get_setting(USERNAME, 'kakao_refresh_token')
                ok, kerr = naver_api.send_kakao(kakao_token, msg, rest_api_key=kakao_api_key, refresh_token=kakao_refresh)
                if ok:
                    sent_ok = True
                    if kerr and "__TOKEN_REFRESHED__" in str(kerr):
                        parts = str(kerr).replace("__TOKEN_REFRESHED__", "").split("||")
                        set_setting(USERNAME, 'kakao_access_token', parts[0])
                        if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                else:
                    st.error(f"❌ 카카오톡 실패: {kerr}")
            
            if not sent_ok and tg_token and tg_chat:
                ok, terr = naver_api.send_telegram(tg_token, tg_chat, msg)
                if ok: sent_ok = True
                else: st.error(f"❌ 텔레그램 실패: {terr}")
                
            if sent_ok:
                st.success("✅ 휴대폰으로 전송 완료!")
            elif not kakao_token and not tg_token:
                st.warning("💡 설정에서 카카오톡 또는 텔레그램을 설정해주세요.")

    # ── 주문 이력 검색 ──────────────────────────────────────────
    st.divider()
    st.subheader("🔍 주문 이력 검색")

    with st.form("order_search_form"):
        sc1, sc2, sc3 = st.columns([2, 1, 1])
        kw_input      = sc1.text_input("수취인 / 구매자 / 주문번호", placeholder="홍길동, 주문번호 입력")
        prod_input    = sc1.text_input("상품명", placeholder="상품명 일부 입력")
        date_from_in  = sc2.date_input("시작일", value=datetime.today() - timedelta(days=30))
        date_to_in    = sc3.date_input("종료일", value=datetime.today())
        search_btn    = st.form_submit_button("🔍 검색", use_container_width=True, type="primary")

    if search_btn or st.session_state.get('order_search_triggered'):
        st.session_state['order_search_triggered'] = True
        results = search_order_history(
            USERNAME,
            keyword=kw_input,
            product_name=prod_input,
            date_from=date_from_in.strftime("%Y-%m-%d"),
            date_to=date_to_in.strftime("%Y-%m-%d"),
        )
        if results:
            rdf = pd.DataFrame(results)
            show_cols = {
                'order_date': '주문일', 'recipient': '수취인', 'buyer': '구매자',
                'product_name': '상품명', 'option_info': '옵션',
                'qty': '수량', 'unit_price': '판매단가', 'shipping_fee': '배송비',
                'order_amount': '주문금액', 'settlement': '정산예정',
                'status': '주문상태', 'tracking_no': '송장번호',
                'cost_price': '구입가', 'profit': '수익',
            }
            disp = rdf[[c for c in show_cols if c in rdf.columns]].rename(columns=show_cols)
            for col in ['판매단가', '배송비', '주문금액', '정산예정', '구입가', '수익']:
                if col in disp.columns:
                    disp[col] = disp[col].apply(lambda x: f"{int(x):,}" if pd.notna(x) and x != 0 else ("-" if x == 0 else ""))
            st.caption(f"검색 결과 {len(results)}건")
            st.dataframe(disp, use_container_width=True, hide_index=True)

            # 다운로드
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine='openpyxl') as w:
                rdf.to_excel(w, index=False, sheet_name='주문이력')
            out.seek(0)
            st.download_button(
                "📥 검색 결과 엑셀 다운로드",
                data=out, file_name=f"주문이력_{date_from_in}_{date_to_in}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("조건에 맞는 주문이 없습니다.")


# ═══════════════════════════════════════
# 탭 1.5: 송장번호 등록
# ═══════════════════════════════════════
elif tab_choice == "📮 송장번호 등록":
    st.header("📮 송장번호 일괄 등록")
    st.caption("택배사 PIDPIC 파일을 업로드하면 네이버 스마트스토어 일괄 송장 등록 파일을 생성하거나 API로 자동 발송처리합니다.")

    pidpic_file = st.file_uploader("택배사 PIDPIC 파일 업로드 (주문번호 + 운송장번호)", type=['xlsx', 'xls'], key="track_pidpic")
    courier = st.selectbox("택배사", ["롯데택배", "한진택배", "CJ대한통운", "우체국택배", "로젠택배"])

    if pidpic_file:
        pidpic_df, err2 = read_excel_auto(pidpic_file)

        if pidpic_df is None:
            st.error(f"PIDPIC 파일 읽기 실패: {err2}")
        else:
            # 컬럼명 후보 탐색 (택배사마다 컬럼명 다를 수 있음)
            col_order = next((c for c in pidpic_df.columns if '주문번호' in str(c)), None)
            col_track = next((c for c in pidpic_df.columns if '운송장' in str(c) or '송장' in str(c)), None)

            if not col_order or not col_track:
                st.error(f"PIDPIC 파일에서 주문번호/운송장번호 컬럼을 찾을 수 없습니다.")
                st.write("파일의 컬럼 목록:", list(pidpic_df.columns))
            else:
                pidpic_df['_주문번호'] = pidpic_df[col_order].apply(to_id_str)
                pidpic_df['_운송장번호'] = pidpic_df[col_track].apply(to_id_str)

                valid = pidpic_df[
                    (pidpic_df['_주문번호'].str.len() > 5) &
                    (pidpic_df['_운송장번호'].str.len() > 5) &
                    (pidpic_df['_운송장번호'] != 'nan')
                ].copy()

                if valid.empty:
                    st.warning("유효한 주문번호/운송장번호 데이터가 없습니다.")
                else:
                    result_df = pd.DataFrame({
                        '상품주문번호': valid['_주문번호'].values,
                        '배송방법': '택배,등기,소포',
                        '택배사': courier,
                        '송장번호': valid['_운송장번호'].values,
                    })

                    st.metric("처리 가능 건수", f"{len(result_df)}건")
                    st.dataframe(result_df, use_container_width=True, hide_index=True)
                    st.divider()

                    # ── 반자동: XLS 다운로드 ──────────────────────────
                    st.subheader("📥 반자동 — 파일 다운로드 후 스마트스토어에 직접 업로드")
                    output = io.BytesIO()
                    import xlwt
                    wb = xlwt.Workbook(encoding='utf-8')
                    ws = wb.add_sheet('발송처리')
                    headers = ['상품주문번호', '배송방법', '택배사', '송장번호']
                    for ci, h in enumerate(headers):
                        ws.write(0, ci, h)
                    for ri, (_, row) in enumerate(result_df.iterrows(), 1):
                        ws.write(ri, 0, str(row['상품주문번호']))
                        ws.write(ri, 1, str(row['배송방법']))
                        ws.write(ri, 2, str(row['택배사']))
                        ws.write(ri, 3, str(row['송장번호']))
                    wb.save(output)
                    output.seek(0)

                    st.download_button(
                        label=f"📥 송장번호_일괄_등록.xls 다운로드 ({len(result_df)}건)",
                        data=output,
                        file_name=f"송장번호_일괄_등록_{datetime.today().strftime('%Y%m%d')}.xls",
                        mime="application/vnd.ms-excel",
                        use_container_width=True,
                    )

                    # ── 자동: 네이버 API 직접 전송 ───────────────────
                    st.divider()
                    st.subheader("🚀 자동 — 네이버 스마트스토어 API로 즉시 발송처리")

                    if not HAS_NAVER_API:
                        st.warning("naver_api.py 파일이 없습니다. 관리자에게 문의하세요.")
                    elif not api_id or not api_secret:
                        st.warning("⚙️ 설정 탭에서 네이버 API 키를 먼저 입력해주세요.")
                    else:
                        st.caption(f"API 연동 완료 · {len(result_df)}건 발송처리 준비됨")
                        if st.button(f"🚀 {len(result_df)}건 일괄 발송처리 (API 자동)", type="primary", key="api_ship", use_container_width=True):
                            ship_data = []
                            for _, row in result_df.iterrows():
                                p_id = str(row['상품주문번호']).split('.')[0].strip()
                                t_num = str(row['송장번호']).replace('-', '').strip()
                                ship_data.append({
                                    "productOrderId": p_id,
                                    "택배사": courier,
                                    "trackingNumber": t_num,
                                })
                            with st.spinner(f"네이버 스마트스토어에 {len(ship_data)}건 발송처리 중..."):
                                result, ship_err = naver_api.ship_orders(api_id, api_secret, ship_data)
                            if ship_err:
                                st.error(f"❌ 오류: {ship_err}")
                            elif result:
                                st.success(f"✅ 완료! 성공: {result.get('success', 0)}건 / 실패: {result.get('fail', 0)}건")
                                if result.get('fail', 0) > 0:
                                    with st.expander("실패 상세 사유"):
                                        for detail in result.get('fail_details', []):
                                            st.write(detail)

# ═══════════════════════════════════════
# 탭 2: 영수증 등록
# ═══════════════════════════════════════
elif tab_choice == "🧾 영수증 등록":
    st.header("🧾 코스트코 영수증 등록")

    st.subheader("📄 영수증 PDF 업로드 (여러 파일 동시 등록 가능)")
    receipt_files = st.file_uploader(
        "코스트코 영수증 PDF (여러 파일 선택 가능)",
        type=['pdf'], key="receipt_pdf", accept_multiple_files=True
    )

    if receipt_files:
        all_parsed = []
        fail_files = []   # [(filename, error_msg)]
        for rf in receipt_files:
            items, err = parse_costco_receipt_pdf(rf)
            if items:
                for p in items:
                    p['_file'] = rf.name
                all_parsed.extend(items)
            else:
                fail_files.append((rf.name, err))

        if all_parsed:
            # 같은 상품번호/상품명이면 최신 단가로 덮어쓰기 (중복 제거)
            merged = {}
            for p in all_parsed:
                key = p.get('상품번호') or p['상품명']
                merged[key] = p
            deduped = list(merged.values())

            st.success(f"✅ {len(receipt_files) - len(fail_files)}개 파일 / {len(deduped)}종 상품 인식")
            if fail_files:
                for fname, emsg in fail_files:
                    with st.expander(f"⚠️ 인식 실패: {fname}", expanded=False):
                        st.warning(emsg)

            # 파일별 탭으로 결과 표시
            if len(receipt_files) > 1:
                file_names = sorted(set(p['_file'] for p in all_parsed))
                tabs = st.tabs([f"📄 {n}" for n in file_names] + ["📋 전체 합산"])
                for ti, fname in enumerate(file_names):
                    with tabs[ti]:
                        file_items = [p for p in all_parsed if p['_file'] == fname]
                        st.dataframe(
                            pd.DataFrame(file_items)[['상품번호', '상품명', '수량', '단가']],
                            use_container_width=True, hide_index=True
                        )
                with tabs[-1]:
                    st.dataframe(
                        pd.DataFrame(deduped)[['상품번호', '상품명', '수량', '단가']],
                        use_container_width=True, hide_index=True
                    )
            else:
                st.dataframe(
                    pd.DataFrame(deduped)[['상품번호', '상품명', '수량', '단가']],
                    use_container_width=True, hide_index=True
                )

            st.session_state['receipt_items'] = [
                {"상품명": p['상품명'], "수량": p['수량'], "단가": p['단가'], "상품번호": p.get('상품번호', '')}
                for p in deduped
            ]

            # ── 가격 변동 감지 ──────────────────────────────────────
            price_changes = detect_price_changes(USERNAME, deduped)

            if price_changes:
                st.divider()
                up_cnt = sum(1 for c in price_changes if c['diff'] > 0)
                dn_cnt = sum(1 for c in price_changes if c['diff'] < 0)
                st.warning(f"⚠️ 가격 변동 감지: 🔺인상 {up_cnt}건 / 🔻인하 {dn_cnt}건")

                # 변동 내역 테이블
                def _fee_str(f):
                    return "무료" if f == 0 else f"{int(f):,}원"

                change_rows = []
                for c in price_changes:
                    arrow = "🔺" if c['diff'] > 0 else "🔻"
                    change_rows.append({
                        "": arrow,
                        "코스트코 상품명": c['costco_name'],
                        "기존 매입가": f"{c['old_cost']:,}원",
                        "새 매입가": f"{c['new_cost']:,}원",
                        "변동": f"{'+' if c['diff']>0 else ''}{c['diff']:,}원 ({'+' if c['diff']>0 else ''}{c['diff_pct']}%)",
                        "고객 배송비": _fee_str(c['shipping_fee']),
                    })
                st.dataframe(pd.DataFrame(change_rows), use_container_width=True, hide_index=True)

                # ── 카카오/텔레그램 알림 ──
                kakao_token = get_setting(USERNAME, 'kakao_access_token')
                tg_token = get_setting(USERNAME, 'telegram_token')
                tg_chat = get_setting(USERNAME, 'telegram_chat_id')

                col_notif, col_save = st.columns([1, 1])
                if col_notif.button("📲 가격변동 알림 카톡/텔레그램 발송", key="send_price_alert", use_container_width=True):
                    alert_msg = build_price_alert_msg(price_changes)
                    sent_ok = False
                    if HAS_NAVER_API and kakao_token:
                        kakao_key = get_setting(USERNAME, 'kakao_api_key')
                        kakao_refresh = get_setting(USERNAME, 'kakao_refresh_token')
                        ok, kerr = naver_api.send_kakao(kakao_token, alert_msg, rest_api_key=kakao_key, refresh_token=kakao_refresh)
                        if ok:
                            sent_ok = True
                            if kerr and "__TOKEN_REFRESHED__" in str(kerr):
                                parts = str(kerr).replace("__TOKEN_REFRESHED__", "").split("||")
                                set_setting(USERNAME, 'kakao_access_token', parts[0])
                                if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                        else:
                            st.error(f"카카오 실패: {kerr}")
                    if not sent_ok and HAS_NAVER_API and tg_token and tg_chat:
                        ok, terr = naver_api.send_telegram(tg_token, tg_chat, alert_msg)
                        if ok:
                            sent_ok = True
                        else:
                            st.error(f"텔레그램 실패: {terr}")
                    if sent_ok:
                        # 알림 발송 이력 저장
                        save_price_changes_to_history(USERNAME, price_changes)
                        st.success("✅ 가격 변동 알림 발송 완료!")
                    elif not kakao_token and not tg_token:
                        st.warning("설정에서 카카오톡 또는 텔레그램을 먼저 설정해주세요.")

                # ── 네이버 가격 자동 적용 ──
                st.divider()
                st.subheader("🛒 네이버 판매가 검토 및 적용")
                st.caption("새 매입가를 기준으로 판매가를 조정합니다. 적용할 상품을 선택하고 새 판매가를 입력 후 적용하세요.")

                api_id = get_setting(USERNAME, 'api_client_id')
                api_secret = get_setting(USERNAME, 'api_client_secret')
                shipping_cost_set = int(get_setting(USERNAME, 'shipping_cost') or 1800)
                box_cost_set = int(get_setting(USERNAME, 'box_cost') or 300)
                margin_rate = int(get_setting(USERNAME, 'target_margin') or 10) / 100

                if not api_id:
                    st.info("💡 설정 탭에서 네이버 커머스 API 키를 등록하면 자동 가격 적용이 가능합니다.")

                apply_targets = []
                for idx, c in enumerate(price_changes):
                    sq = max(1, c.get('split_qty', 1))
                    unit_cost = c['new_cost'] // sq
                    cust_fee = int(c.get('shipping_fee', 0) or 0)
                    # 권장 판매가: 원가 + 택배비 + 박스비 + 마진, 네이버 수수료 5.5% 고려
                    suggested = int(
                        (unit_cost + shipping_cost_set + box_cost_set) * (1 + margin_rate) / 0.945 / 100
                    ) * 100

                    with st.expander(
                        f"{'🔺' if c['diff']>0 else '🔻'} {c['costco_name']}  "
                        f"{c['old_cost']:,} → {c['new_cost']:,}원  |  고객배송비 {_fee_str(cust_fee)}",
                        expanded=True
                    ):
                        col_a, col_b, col_c = st.columns([1, 2, 1])
                        do_apply = col_a.checkbox("적용", value=True, key=f"chk_{idx}")
                        new_sale_price = col_b.number_input(
                            "새 네이버 판매가 (원)",
                            value=suggested, min_value=100, step=100,
                            key=f"nsp_{idx}", label_visibility="collapsed"
                        )
                        col_c.caption(f"권장가\n**{suggested:,}원**")
                        p_no_input = st.text_input(
                            "네이버 원상품번호 (originProductNo)",
                            value=c.get('product_no', ''),
                            key=f"pno_{idx}",
                            placeholder="미입력 시 API 적용 불가"
                        )
                        if do_apply:
                            apply_targets.append({
                                **c,
                                'product_no': p_no_input,
                                'new_sale_price': new_sale_price,
                            })

                if st.button("✅ 선택 상품 네이버 판매가 적용", type="primary", key="apply_naver_price", use_container_width=True):
                    if not api_id or not api_secret:
                        st.error("네이버 API 키가 설정되지 않았습니다. 설정 탭에서 입력해주세요.")
                    elif not HAS_NAVER_API:
                        st.error("naver_api.py 모듈이 없습니다.")
                    else:
                        ok_list, fail_list = [], []
                        for t in apply_targets:
                            if not t['product_no']:
                                fail_list.append(f"{t['costco_name']}: 상품번호 미입력")
                                continue
                            ok, err = naver_api.update_product_price(
                                api_id, api_secret, t['product_no'], t['new_sale_price']
                            )
                            if ok:
                                ok_list.append(t['costco_name'])
                                # 가격 변동 이력 저장 (네이버 적용 완료 표시)
                                conn = get_user_db(USERNAME)
                                conn.execute("""INSERT INTO price_change_history
                                    (costco_name, old_cost, new_cost, diff, diff_pct,
                                     product_no, shipping_fee, naver_updated, created_at)
                                    VALUES (?,?,?,?,?,?,?,1,?)""",
                                    (t['costco_name'], t['old_cost'], t['new_cost'],
                                     t['diff'], t['diff_pct'], t['product_no'],
                                     t.get('shipping_fee', 0),
                                     datetime.now().strftime("%Y-%m-%d %H:%M")))
                                conn.commit()
                                conn.close()
                            else:
                                fail_list.append(f"{t['costco_name']}: {err}")

                        if ok_list:
                            st.success(f"✅ 네이버 가격 적용 완료: {', '.join(ok_list)}")
                            # 완료 알림 카톡 발송
                            if HAS_NAVER_API and (kakao_token or (tg_token and tg_chat)):
                                done_msg = (
                                    f"[가격 적용 완료] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                                    + "\n".join(
                                        f"✅ {t['costco_name']}: {t['new_sale_price']:,}원으로 변경"
                                        for t in apply_targets if t['costco_name'] in ok_list
                                    )
                                )
                                if kakao_token:
                                    kakao_key = get_setting(USERNAME, 'kakao_api_key')
                                    naver_api.send_kakao(kakao_token, done_msg, rest_api_key=kakao_key)
                                elif tg_token and tg_chat:
                                    naver_api.send_telegram(tg_token, tg_chat, done_msg)
                        if fail_list:
                            for f in fail_list:
                                st.error(f"❌ {f}")

            else:
                st.info("✅ 가격 변동 없음 — DB에 저장된 가격과 동일합니다.")

            st.divider()
            if st.button("💾 공유 DB 저장 (전체 판매자 매입가 업데이트)", type="primary", key="save_parsed"):
                cnt = 0
                for p in deduped:
                    upsert_shared_product(
                        costco_name=p['상품명'],
                        keyword=p['상품명'],
                        price=p['단가'],
                        product_no=p.get('상품번호', ''),
                        updated_by=USERNAME,
                    )
                    cnt += 1
                st.success(f"✅ {cnt}종 공유 DB 저장 완료! 모든 판매자에게 반영됩니다.")
        else:
            st.warning("업로드한 파일 모두 인식 실패. 아래에서 직접 입력해주세요.")
            for fname, emsg in fail_files:
                with st.expander(f"⚠️ {fname} — 실패 원인", expanded=True):
                    st.code(emsg, language=None)

    st.divider()
    st.subheader("✏️ 수동 입력")
    if 'manual_items' not in st.session_state:
        st.session_state['manual_items'] = [{"상품명": "", "단가": 0}]

    m_items = st.session_state['manual_items']
    to_delete = None
    for i, item in enumerate(m_items):
        cols = st.columns([4, 2, 1])
        m_items[i]['상품명'] = cols[0].text_input(f"mn_{i}", value=item['상품명'], label_visibility="collapsed", key=f"mn_{i}", placeholder="코스트코 상품명")
        m_items[i]['단가'] = cols[1].number_input(f"mp_{i}", value=item['단가'], min_value=0, step=100, label_visibility="collapsed", key=f"mp_{i}")
        if cols[2].button("🗑", key=f"md_{i}") and len(m_items) > 1:
            to_delete = i
    if to_delete is not None:
        m_items.pop(to_delete)
        st.rerun()

    c1, c2 = st.columns([1, 3])
    if c1.button("➕ 행 추가"):
        m_items.append({"상품명": "", "단가": 0})
        st.rerun()
    if c2.button("💾 수동 입력 저장 (공유 DB)"):
        cnt = 0
        for item in m_items:
            name = item['상품명'].strip()
            if name and item['단가'] > 0:
                upsert_shared_product(name, name, item['단가'], updated_by=USERNAME)
                cnt += 1
        if cnt: st.success(f"✅ {cnt}건 공유 DB 저장!")

    st.divider()
    st.subheader("📦 저장된 제품 가격 DB")
    products = get_all_products(USERNAME)
    if products:
        pdf = pd.DataFrame(products)[['product_no','costco_name','match_keyword','unit_price','updated_at']]
        pdf.columns = ['코스트코 상품번호', '코스트코 상품명', '매칭키', '단가', '최종 업데이트']
        st.dataframe(pdf, use_container_width=True, hide_index=True)
    else:
        st.info("등록된 제품이 없습니다.")


# ═══════════════════════════════════════
# 탭 3: 수익 계산
# ═══════════════════════════════════════
elif tab_choice == "💰 수익 계산":
    st.header("💰 수익 계산")
    shipping_cost = int(get_setting(USERNAME, 'shipping_cost') or 1800)
    box_cost = int(get_setting(USERNAME, 'box_cost') or 300)

    st.info(f"📐 수익 = (정산예정 + 고객택배비) - (구입가 + 택배비 {fmt(shipping_cost)} + 박스비 {fmt(box_cost)})")

    col_date, _ = st.columns([1, 3])
    with col_date:
        calc_date = st.date_input("계산할 주문 날짜 선택", value=datetime.today())
        calc_date_str = calc_date.strftime("%Y-%m-%d")

    # 기존 DB에서 데이터 불러오기
    saved_rows = get_daily_orders(USERNAME, calc_date_str)
    if saved_rows:
        df = pd.DataFrame(saved_rows)
        # DB 컬럼명을 UI용 컬럼명으로 매핑
        rename_map = {
            'recipient': '수취인명',
            'product_name': '상품명',
            'option_info': '옵션정보',
            'qty': '수량',
            'order_amount': '최종 상품별 총 주문금액',
            'shipping_fee': '배송비 합계',
            'settlement': '정산예정금액',
            'cost_price': '구입가격'
        }
        df = df.rename(columns=rename_map)
    else:
        df = None

    if df is not None and not df.empty:
        receipt_items = st.session_state.get('receipt_items', [])
        unique_products = df['상품명'].unique().tolist()
        receipt_matches = match_receipt_to_orders(receipt_items, unique_products) if receipt_items else {}

        costs, match_sources, matched_names = [], [], []
        for _, r in df.iterrows():
            product, qty = r['상품명'], r['수량']
            saved_cost = int(r.get('구입가격', 0) or 0)
            if product in receipt_matches:
                item = receipt_matches[product]
                costs.append(item['단가'] * qty)
                match_sources.append("영수증")
                matched_names.append(item['상품명'])
            else:
                p_no = str(r.get('product_no', '')) if 'product_no' in r.index else ''
                p = match_product_to_db(USERNAME, product, product_no=p_no)
                if p:
                    sq = max(1, int(p.get('split_qty', 1) or 1))
                    unit_cost = p['unit_price'] // sq  # 분리판매 시 1개 원가
                    costs.append(unit_cost * qty)
                    match_sources.append("DB")
                    matched_names.append(p['costco_name'])
                elif saved_cost > 0:
                    # 제품DB 미매칭이지만 이전에 저장된 구입가 사용
                    costs.append(saved_cost)
                    match_sources.append("DB")
                    matched_names.append(product)
                else:
                    costs.append(0)
                    match_sources.append("미매칭")
                    matched_names.append("")

        df['구입가격'] = costs
        df['매칭출처'] = match_sources
        df['매칭제품'] = matched_names

        if 'cost_overrides' not in st.session_state:
            st.session_state['cost_overrides'] = {}
        for idx in df.index:
            key = f"{df.loc[idx,'수취인명']}_{df.loc[idx,'상품명']}_{idx}_{calc_date_str}"
            if key in st.session_state['cost_overrides']:
                df.loc[idx, '구입가격'] = st.session_state['cost_overrides'][key]
                if st.session_state['cost_overrides'][key] > 0 and df.loc[idx, '매칭출처'] == '미매칭':
                    df.loc[idx, '매칭출처'] = '수동입력'

        df['수입'] = df.apply(
            lambda r: (r['정산예정금액'] + r['배송비 합계']) - (r['구입가격'] + shipping_cost + box_cost) if r['구입가격'] > 0 else None, axis=1)

        st.caption(f"📅 {calc_date_str}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🟢 영수증", f"{len(df[df['매칭출처']=='영수증'])}건")
        c2.metric("🔵 DB", f"{len(df[df['매칭출처']=='DB'])}건")
        c3.metric("✏️ 수동", f"{len(df[df['매칭출처']=='수동입력'])}건")
        c4.metric("🟡 미매칭", f"{len(df[df['매칭출처']=='미매칭'])}건")

        st.subheader("📊 일별 정산표")
        st.caption("🟢=영수증 | 🔵=DB | ⬜=수동 | 🟡=미매칭")

        hcols = st.columns([1.5, 3.5, 0.7, 1.5, 1.5, 1.5, 2.3, 0.5, 1.5])
        for h, c in zip(['수취인','상품명','수량','정산예정','고객택배비','구입가격✏️','매칭키워드✏️','','수입'], hcols):
            c.markdown(f"**{h}**")

        for idx, r in df.iterrows():
            key = f"{r['수취인명']}_{r['상품명']}_{idx}_{calc_date_str}"
            bg = "#fff3cd" if r['매칭출처']=='미매칭' else "#d4edda" if r['매칭출처']=='영수증' else "#d6eaf8" if r['매칭출처']=='DB' else "#ffffff"
            cols = st.columns([1.5, 3.5, 0.7, 1.5, 1.5, 1.5, 2.3, 0.5, 1.5])
            cols[0].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;font-size:12px'>{r['수취인명']}</div>", unsafe_allow_html=True)
            cols[1].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;font-size:11px'>{r['상품명'][:40]}</div>", unsafe_allow_html=True)
            cols[2].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;text-align:center'>{int(r['수량'])}</div>", unsafe_allow_html=True)
            cols[3].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;text-align:right;font-size:12px'>{fmt(r['정산예정금액'])}</div>", unsafe_allow_html=True)
            cols[4].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;text-align:right;font-size:12px'>{fmt(r['배송비 합계'])}</div>", unsafe_allow_html=True)

            current_cost = int(r['구입가격'])
            new_cost = cols[5].number_input(f"c_{idx}", value=current_cost, min_value=0, step=100, label_visibility="collapsed", key=f"c_{idx}")
            if new_cost != current_cost:
                st.session_state['cost_overrides'][key] = new_cost

            current_kw = r['매칭제품'] if r['매칭제품'] else ""
            new_kw = cols[6].text_input(f"k_{idx}", value=current_kw, label_visibility="collapsed", key=f"k_{idx}", placeholder="매칭키워드")
            if cols[7].button("💾", key=f"s_{idx}"):
                kw = new_kw.strip()
                price = new_cost if new_cost > 0 else current_cost
                unit_price = price // int(r['수량']) if int(r['수량']) > 1 else price
                if kw and unit_price > 0:
                    upsert_product(USERNAME, kw, kw, unit_price)
                    st.success(f"✅ '{kw}' → {fmt(unit_price)}원 저장!")
                    st.session_state['cost_overrides'] = {}
                    st.rerun()

            pv = r['수입']
            cols[8].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;text-align:right'>{fmt(pv) if pd.notna(pv) else '-'}</div>", unsafe_allow_html=True)

        if st.button("🔄 수정사항 반영", key="recalc"):
            st.session_state['cost_overrides'] = {}
            st.rerun()

        # 합계
        st.subheader("📋 합계")
        matched_df = df[df['구입가격'] > 0]
        total_settlement = matched_df['정산예정금액'].sum()
        total_cust_ship = matched_df['배송비 합계'].sum()
        total_cost = matched_df['구입가격'].sum()
        total_ship = len(matched_df) * shipping_cost
        total_box = len(matched_df) * box_cost
        total_profit = matched_df['수입'].sum() if len(matched_df) > 0 else 0

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**수입**")
            st.write(f"정산예정: {fmt(total_settlement)}원 + 고객택배비: {fmt(total_cust_ship)}원 = **{fmt(total_settlement + total_cust_ship)}원**")
        with c2:
            st.markdown("**지출**")
            st.write(f"구입가: {fmt(total_cost)}원 + 택배: {fmt(total_ship)}원 + 박스: {fmt(total_box)}원 = **{fmt(total_cost + total_ship + total_box)}원**")
        st.markdown(f"### 순수익: {'🟢' if total_profit >= 0 else '🔴'} {fmt(total_profit)}원")

        st.divider()
        if st.button("💾 정산 데이터 저장", type="primary"):
            save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
            st.success(f"✅ {calc_date_str} 저장 완료!")

        # ── 적자 상품 가격 자동 조정 ──
        loss_df = df[(df['구입가격'] > 0) & (df['수입'] < 0)].copy()
        if len(loss_df) > 0:
            st.divider()
            st.subheader("🔴 적자 상품 가격 조정")

            target_margin = int(get_setting(USERNAME, 'target_margin') or 10)
            max_increase = int(get_setting(USERNAME, 'max_increase_pct') or 20)
            tg_token = get_setting(USERNAME, 'telegram_token')
            tg_chat = get_setting(USERNAME, 'telegram_chat_id')

            # 적자 상품별로 권장 가격 계산
            loss_products = loss_df.drop_duplicates(subset='상품명')[['상품명','구입가격','수량','정산예정금액','수입']].reset_index(drop=True)

            st.caption(f"목표 마진: {target_margin}% | 최대 인상폭: {max_increase}%")

            adjust_list = []
            for i, r in loss_products.iterrows():
                unit_cost = int(r['구입가격'] / r['수량']) if r['수량'] > 1 else int(r['구입가격'])
                current_price = int(r['정산예정금액'] / r['수량']) if r['수량'] > 1 else int(r['정산예정금액'])

                if HAS_NAVER_API:
                    min_price = naver_api.calc_min_price(unit_cost, shipping_cost, box_cost, target_margin / 100)
                else:
                    min_price = int((unit_cost + shipping_cost + box_cost) * (1 + target_margin / 100) / 0.945 / 100) * 100

                increase_pct = ((min_price - current_price) / current_price * 100) if current_price > 0 else 0
                over_limit = increase_pct > max_increase

                cols = st.columns([3, 1.5, 1.5, 1.5, 1.5, 1])
                cols[0].markdown(f"**{r['상품명'][:30]}**")
                cols[1].markdown(f"원가: {fmt(unit_cost)}")
                cols[2].markdown(f"현재가: {fmt(current_price)}")
                cols[3].markdown(f"권장가: **{fmt(min_price)}**")

                if over_limit:
                    cols[4].markdown(f"🔴 +{increase_pct:.0f}% (상한초과)")
                else:
                    cols[4].markdown(f"🟡 +{increase_pct:.0f}%")

                adjust_list.append({
                    '상품명': r['상품명'],
                    '원가': unit_cost,
                    '현재가': current_price,
                    '권장가': min_price,
                    '인상률': increase_pct,
                    '상한초과': over_limit,
                    '손익': int(r['수입']),
                })

            if adjust_list and HAS_NAVER_API and api_id and api_secret:
                st.divider()
                safe_list = [a for a in adjust_list if not a['상한초과'] and a['권장가'] > a['현재가']]
                over_list = [a for a in adjust_list if a['상한초과']]

                if safe_list:
                    st.markdown(f"**✅ 자동 조정 가능: {len(safe_list)}개** (인상폭 {max_increase}% 이내)")
                    if st.button(f"💰 {len(safe_list)}개 상품 가격 자동 조정", type="primary", key="auto_price"):
                        # 텔레그램 알림
                        if tg_token and tg_chat:
                            msg_lines = ["🔴 적자 상품 가격 조정 알림\n"]
                            for a in safe_list:
                                msg_lines.append(f"▪ {a['상품명'][:20]}")
                                msg_lines.append(f"  {fmt(a['현재가'])} → {fmt(a['권장가'])} (+{a['인상률']:.0f}%)")
                            msg_lines.append(f"\n⏰ 10분 내 '취소' 입력 시 취소됩니다.")
                            naver_api.send_telegram(tg_token, tg_chat, "\n".join(msg_lines))
                            st.info("📱 텔레그램 알림 전송 완료. 10분 내 '취소' 응답이 없으면 자동 적용됩니다.")

                        # 상품 목록 조회 → 매칭 → 가격 변경
                        with st.spinner("스마트스토어 상품 조회 중..."):
                            store_products, err = naver_api.get_product_list(api_id, api_secret)

                        if err:
                            st.error(f"❌ 상품 조회 실패: {err}")
                        elif store_products:
                            success_cnt, fail_cnt = 0, 0
                            conn = get_user_db(USERNAME)
                            now = datetime.now().strftime("%Y-%m-%d %H:%M")

                            for adj in safe_list:
                                # 스토어 상품 매칭 (이름 유사도)
                                matched_product = None
                                for sp in store_products:
                                    score = calc_match_score(adj['상품명'], sp['productName'])
                                    if score >= MIN_MATCH_SCORE:
                                        matched_product = sp
                                        break

                                if matched_product:
                                    ok, err = naver_api.update_product_price(
                                        api_id, api_secret,
                                        matched_product['originProductNo'],
                                        adj['권장가']
                                    )
                                    if ok:
                                        success_cnt += 1
                                        conn.execute("""INSERT INTO price_history
                                            (product_name, origin_product_no, old_price, new_price, cost_price, reason, status, created_at)
                                            VALUES (?,?,?,?,?,?,?,?)""",
                                            (adj['상품명'], str(matched_product['originProductNo']),
                                             adj['현재가'], adj['권장가'], adj['원가'],
                                             f"적자 자동조정 (+{adj['인상률']:.0f}%)", "applied", now))
                                    else:
                                        fail_cnt += 1
                                        st.warning(f"⚠️ {adj['상품명'][:20]}: {err}")
                                else:
                                    fail_cnt += 1
                                    st.warning(f"⚠️ {adj['상품명'][:20]}: 스토어 상품 매칭 실패")

                            conn.commit()
                            conn.close()

                            st.success(f"✅ 가격 조정 완료! 성공: {success_cnt}건, 실패: {fail_cnt}건")

                            # 결과 텔레그램 전송
                            if tg_token and tg_chat:
                                naver_api.send_telegram(tg_token, tg_chat,
                                    f"✅ 가격 조정 완료\n성공: {success_cnt}건, 실패: {fail_cnt}건")
                        else:
                            st.error("스토어에 판매중인 상품이 없습니다.")

                if over_list:
                    st.warning(f"⚠️ 수동 확인 필요: {len(over_list)}개 (인상폭 {max_increase}% 초과)")
                    for a in over_list:
                        st.caption(f"  {a['상품명'][:30]}: {fmt(a['현재가'])} → {fmt(a['권장가'])} (+{a['인상률']:.0f}%)")
            elif not HAS_NAVER_API:
                st.caption("💡 naver_api.py와 API 키가 설정되면 자동 가격 조정이 가능합니다.")
    else:
        st.info("📋 '주문 업로드' 탭에서 먼저 주문 파일을 업로드해주세요.")

    # ── 정산 이력 (항상 표시) ──
    st.divider()
    st.subheader("📅 정산 이력")
    saved_dates_list = get_saved_dates(USERNAME)
    if saved_dates_list:
        col_d, col_del = st.columns([2, 1])
        sel_date = col_d.selectbox("날짜 선택", saved_dates_list, key="profit_hist_date")
        if sel_date:
            hist_orders = get_daily_orders(USERNAME, sel_date)
            if hist_orders:
                hodf = pd.DataFrame(hist_orders)[['recipient','product_name','option_info','qty','settlement','shipping_fee','cost_price','profit']]
                hodf.columns = ['수취인','상품명','옵션','수량','정산예정','고객택배비','구입가','수입']
                st.dataframe(hodf, use_container_width=True, hide_index=True)
                hc1, hc2, hc3 = st.columns(3)
                hc1.metric("당일 순수익", f"{fmt(sum(o['profit'] for o in hist_orders))}원")
                hc2.metric("주문건수", f"{len(hist_orders)}건")
                hc3.metric("정산 합계", f"{fmt(sum(o['settlement'] for o in hist_orders))}원")
        if col_del.button(f"🗑 {sel_date} 삭제", key="del_hist_date", use_container_width=True):
            conn = get_user_db(USERNAME)
            conn.execute("DELETE FROM daily_orders WHERE order_date=?", (sel_date,))
            conn.commit(); conn.close()
            st.rerun()
    else:
        st.info("저장된 정산 이력이 없습니다.")


# ═══════════════════════════════════════
# 탭 4: 대시보드
# ═══════════════════════════════════════
elif tab_choice == "📊 대시보드":
    st.header("📊 대시보드")
    today = datetime.today()

    period = st.radio("기간", ["최근 7일", "최근 14일", "최근 30일"], horizontal=True, label_visibility="collapsed")
    days = {"최근 7일": 7, "최근 14일": 14, "최근 30일": 30}[period]
    start = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    stats = get_date_range_stats(USERNAME, start, end)

    if not stats:
        st.info("저장된 데이터가 없습니다. 주문 업로드 → 수익 계산 → 저장 순서로 진행하세요.")
    else:
        today_str = today.strftime("%Y-%m-%d")
        today_stat = next((s for s in stats if s['order_date'] == today_str), None)

        c1, c2, c3 = st.columns(3)
        c1.metric("오늘 주문", f"{today_stat['cnt']}건" if today_stat else "0건")
        c2.metric("오늘 매출", f"{fmt(today_stat['total_sales'])}원" if today_stat else "0원")
        c3.metric("오늘 순수익", f"{fmt(today_stat['total_profit'])}원" if today_stat else "0원")

        st.subheader("일별 수익 추이")
        chart_df = pd.DataFrame(stats)
        chart_df['order_date'] = pd.to_datetime(chart_df['order_date'])
        fig = go.Figure()
        fig.add_trace(go.Bar(x=chart_df['order_date'], y=chart_df['total_profit'],
            name='순수익', marker_color='#1D9E75',
            text=chart_df['total_profit'].apply(lambda x: f"{x:,.0f}"), textposition='outside'))
        fig.update_layout(height=350, margin=dict(l=20,r=20,t=20,b=40), yaxis_tickformat=",",
                         xaxis_dtick="D1", xaxis_tickformat="%m/%d", plot_bgcolor='rgba(0,0,0,0)')
        fig.update_yaxes(gridcolor='rgba(0,0,0,0.05)')
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("주간 요약")
        tw_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        lw_start = (today - timedelta(days=today.weekday()+7)).strftime("%Y-%m-%d")
        lw_end = (today - timedelta(days=today.weekday()+1)).strftime("%Y-%m-%d")
        tw = get_date_range_stats(USERNAME, tw_start, end)
        lw = get_date_range_stats(USERNAME, lw_start, lw_end)
        tw_profit = sum(s['total_profit'] for s in tw)
        lw_profit = sum(s['total_profit'] for s in lw)
        c1, c2 = st.columns(2)
        c1.metric("이번 주 순수익", f"{fmt(tw_profit)}원",
                  delta=f"{((tw_profit/lw_profit-1)*100):.1f}%" if lw_profit > 0 else None)
        c2.metric("이번 주 주문건수", f"{sum(s['cnt'] for s in tw)}건")

        st.subheader("월별 수익 추이")
        monthly = get_monthly_stats(USERNAME)
        if monthly:
            mdf = pd.DataFrame(monthly)
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=mdf['month'], y=mdf['total_profit'], mode='lines+markers+text',
                line=dict(color='#7F77DD', width=3), marker=dict(size=10),
                text=mdf['total_profit'].apply(lambda x: f"{x/10000:.1f}만"), textposition='top center'))
            fig2.update_layout(height=300, margin=dict(l=20,r=20,t=20,b=40), yaxis_tickformat=",", plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig2, use_container_width=True)

        st.subheader("상품별 수익 순위")
        ranking = get_product_ranking(USERNAME, today.strftime("%Y-%m"))
        if ranking:
            rdf = pd.DataFrame(ranking)
            rdf.columns = ['상품명', '판매수량', '매출', '순수익']
            rdf.index = range(1, len(rdf) + 1)
            st.dataframe(rdf.style.format({'매출': '{:,.0f}', '순수익': '{:,.0f}'}), use_container_width=True)


# ═══════════════════════════════════════
# 탭 5: 설정
# ═══════════════════════════════════════
elif tab_choice == "⚙️ 설정":
    st.header("⚙️ 설정")

    st.subheader("🔓 엑셀 비밀번호")
    st.caption("네이버 스마트스토어에서 다운받은 엑셀 파일의 비밀번호를 저장하면 자동으로 해제됩니다.")
    current_pw = get_setting(USERNAME, 'excel_password')
    new_pw = st.text_input("엑셀 비밀번호", value=current_pw, type="password", key="excel_pw_input")
    if st.button("비밀번호 저장", key="save_pw"):
        set_setting(USERNAME, 'excel_password', new_pw)
        st.success("✅ 엑셀 비밀번호 저장 완료!")

    st.divider()
    st.subheader("🔗 네이버 커머스 API")
    st.caption("커머스API센터에서 발급받은 키를 입력하면 주문 자동 조회 + 발송 자동 처리가 가능합니다.")
    api_id_val = get_setting(USERNAME, 'api_client_id')
    api_secret_val = get_setting(USERNAME, 'api_client_secret')
    c1, c2 = st.columns(2)
    new_api_id = c1.text_input("애플리케이션 ID", value=api_id_val, key="api_id_input")
    new_api_secret = c2.text_input("애플리케이션 시크릿", value=api_secret_val, type="password", key="api_secret_input")
    if st.button("API 키 저장", key="save_api"):
        set_setting(USERNAME, 'api_client_id', new_api_id)
        set_setting(USERNAME, 'api_client_secret', new_api_secret)
        st.success("✅ API 키 저장 완료!")
        if HAS_NAVER_API and new_api_id and new_api_secret:
            with st.spinner("API 연결 테스트 중..."):
                token, err = naver_api.get_token(new_api_id, new_api_secret)
            if token:
                st.success("✅ API 연결 성공!")
            else:
                st.error(f"❌ API 연결 실패: {err}")
    if not HAS_NAVER_API:
        st.warning("naver_api.py 파일이 프로그램 폴더에 없습니다. 관리자에게 문의하세요.")

    st.divider()
    st.subheader("🛍 네이버 상품 등록 기본값")
    st.caption("제품 DB에서 '🛍등록' 버튼 클릭 시 자동 입력되는 기본값입니다.")
    _nc1, _nc2 = st.columns(2)
    _def_cat = _nc1.text_input("기본 카테고리 ID",
                                value=get_setting(USERNAME, 'naver_default_category'),
                                placeholder="예: 50000803",
                                key="set_naver_cat")
    _def_as  = _nc2.text_input("A/S 전화번호",
                                value=get_setting(USERNAME, 'naver_as_tel'),
                                placeholder="010-0000-0000",
                                key="set_naver_as")
    if st.button("상품 등록 기본값 저장", key="save_naver_reg_defaults"):
        set_setting(USERNAME, 'naver_default_category', _def_cat.strip())
        set_setting(USERNAME, 'naver_as_tel', _def_as.strip())
        st.success("✅ 저장 완료!")

    st.divider()
    st.subheader("📱 카카오톡 알림")
    st.caption("장보기 목록을 카카오톡(나에게 보내기)으로 전송합니다.")
    
    kakao_api_key = get_setting(USERNAME, 'kakao_api_key')
    kakao_token = get_setting(USERNAME, 'kakao_access_token')
    kakao_refresh = get_setting(USERNAME, 'kakao_refresh_token')
    
    new_kakao_api_key = st.text_input("REST API 키", value=kakao_api_key, key="kakao_api_key_input",
                                       help="카카오 개발자 콘솔 > 플랫폼 키에서 확인")
    
    if st.button("REST API 키 저장", key="save_kakao_api_key"):
        set_setting(USERNAME, 'kakao_api_key', new_kakao_api_key)
        st.success("✅ REST API 키 저장!")
        kakao_api_key = new_kakao_api_key
    
    if kakao_api_key:
        # 인가 코드 발급 링크
        auth_url = f"https://kauth.kakao.com/oauth/authorize?client_id={kakao_api_key}&redirect_uri=http://localhost&response_type=code&scope=talk_message"
        st.markdown(f"**1단계:** [여기를 클릭하여 카카오 로그인]({auth_url}) → 동의 후 브라우저 주소창에서 `code=` 뒤의 값을 복사하세요.")
        st.caption("예: http://localhost?code=**abc123xyz** → `abc123xyz` 부분을 아래에 붙여넣기")
        
        auth_code = st.text_input("2단계: 인가 코드 붙여넣기", key="kakao_auth_code", placeholder="인가 코드를 여기에 붙여넣으세요")
        if st.button("🔑 토큰 발급받기", key="kakao_get_token"):
            if auth_code and HAS_NAVER_API:
                with st.spinner("토큰 발급 중..."):
                    access, refresh, err = naver_api.get_kakao_token_by_code(kakao_api_key, auth_code)
                if access:
                    set_setting(USERNAME, 'kakao_access_token', access)
                    set_setting(USERNAME, 'kakao_refresh_token', refresh or '')
                    st.success(f"✅ 토큰 발급 성공! (길이: {len(access)}자)")
                    kakao_token = access
                    kakao_refresh = refresh or ''
                else:
                    st.error(f"❌ {err}")
            else:
                st.warning("인가 코드를 입력해주세요.")
    
    # 현재 토큰 상태 표시
    if kakao_token:
        st.info(f"✅ 액세스 토큰 설정됨 (길이: {len(kakao_token)}자)")
    else:
        st.warning("⚠️ 아직 토큰이 없습니다. 위의 과정을 진행해주세요.")
    
    if st.button("🔔 카카오톡 테스트 전송", key="test_kakao"):
        if kakao_token and HAS_NAVER_API:
            ok, err = naver_api.send_kakao(kakao_token, "✅ 코스트코핫딜 알림 테스트 성공!", 
                                            rest_api_key=kakao_api_key, refresh_token=kakao_refresh)
            if ok:
                if err and "__TOKEN_REFRESHED__" in str(err):
                    parts = str(err).replace("__TOKEN_REFRESHED__", "").split("||")
                    set_setting(USERNAME, 'kakao_access_token', parts[0])
                    if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                    st.success("✅ 카카오톡 전송 성공! (토큰 자동 갱신됨)")
                else:
                    st.success("✅ 카카오톡 전송 성공!")
            else:
                st.error(f"❌ {err}")
        else:
            st.warning("토큰을 먼저 발급받아 주세요.")

    st.divider()
    st.subheader("🚛 택배사 설정")
    current_courier = get_setting(USERNAME, 'default_courier') or 'CJGLS'
    courier_options = {"CJ대한통운": "CJGLS", "롯데택배": "HYUNDAI"}
    sel_courier = st.selectbox("기본 택배사", list(courier_options.keys()), index=0 if current_courier == 'CJGLS' else 1)
    
    st.caption("CJ대한통운 API 접수 설정 (자동 송장 발급용)")
    cj_id = get_setting(USERNAME, 'cj_api_id')
    cj_pw = get_setting(USERNAME, 'cj_api_pw')
    cj_acc = get_setting(USERNAME, 'cj_account_no')
    col1, col2, col3 = st.columns(3)
    new_cj_id = col1.text_input("CJ ID", value=cj_id)
    new_cj_pw = col2.text_input("CJ PW", value=cj_pw, type="password")
    new_cj_acc = col3.text_input("고객번호", value=cj_acc)
    
    if st.button("택배사 설정 저장", key="save_courier"):
        set_setting(USERNAME, 'default_courier', courier_options[sel_courier])
        set_setting(USERNAME, 'cj_api_id', new_cj_id)
        set_setting(USERNAME, 'cj_api_pw', new_cj_pw)
        set_setting(USERNAME, 'cj_account_no', new_cj_acc)
        st.success(f"✅ 택배사 설정 저장 완료! (기본: {sel_courier})")

    st.divider()
    st.subheader("📱 텔레그램 알림 (백업)")
    tg_token = get_setting(USERNAME, 'telegram_token')
    tg_chat = get_setting(USERNAME, 'telegram_chat_id')
    c1, c2 = st.columns(2)
    new_tg_token = c1.text_input("봇 토큰", value=tg_token, type="password", key="tg_token_input")
    new_tg_chat = c2.text_input("Chat ID", value=tg_chat, key="tg_chat_input")
    if st.button("텔레그램 저장", key="save_tg"):
        set_setting(USERNAME, 'telegram_token', new_tg_token)
        set_setting(USERNAME, 'telegram_chat_id', new_tg_chat)
        st.success("✅ 텔레그램 설정 저장!")

    st.divider()
    st.subheader("📦 고정 비용")
    c1, c2 = st.columns(2)
    new_ship = c1.number_input("택배비 (원)", value=int(get_setting(USERNAME, 'shipping_cost') or 1800), step=100)
    new_box = c2.number_input("박스비 (원)", value=int(get_setting(USERNAME, 'box_cost') or 300), step=50)
    if st.button("비용 저장", key="save_cost"):
        set_setting(USERNAME, 'shipping_cost', new_ship)
        set_setting(USERNAME, 'box_cost', new_box)
        st.success(f"✅ 택배비 {fmt(new_ship)}원, 박스비 {fmt(new_box)}원 저장")

    st.divider()
    st.subheader("💰 가격 자동 조정")
    st.caption("적자 상품 감지 시 스마트스토어 판매가를 자동으로 조정합니다.")
    c1, c2 = st.columns(2)
    new_margin = c1.number_input("목표 마진율 (%)", value=int(get_setting(USERNAME, 'target_margin') or 10), min_value=1, max_value=50, step=1)
    new_max_inc = c2.number_input("최대 인상폭 (%)", value=int(get_setting(USERNAME, 'max_increase_pct') or 20), min_value=5, max_value=50, step=5)
    st.caption(f"예시: 원가 10,000원 + 택배비 {fmt(new_ship)}원 + 박스비 {fmt(new_box)}원 → 최소 판매가 약 {fmt(int((10000+new_ship+new_box) * (1+new_margin/100) / 0.945 / 100) * 100)}원")
    if st.button("마진 설정 저장", key="save_margin"):
        set_setting(USERNAME, 'target_margin', new_margin)
        set_setting(USERNAME, 'max_increase_pct', new_max_inc)
        st.success(f"✅ 목표 마진 {new_margin}%, 최대 인상폭 {new_max_inc}% 저장")

    # 가격 변경 이력
    conn = get_user_db(USERNAME)
    history = conn.execute("SELECT * FROM price_history ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()
    if history:
        st.divider()
        st.subheader("📋 가격 변경 이력")
        hdf = pd.DataFrame([dict(h) for h in history])[['created_at','product_name','old_price','new_price','cost_price','reason','status']]
        hdf.columns = ['일시','상품명','변경전','변경후','원가','사유','상태']
        st.dataframe(hdf, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🔑 비밀번호 변경")
    new_login_pw = st.text_input("새 로그인 비밀번호", type="password", key="new_login_pw")
    new_login_pw2 = st.text_input("비밀번호 확인", type="password", key="new_login_pw2")
    if st.button("비밀번호 변경", key="change_pw"):
        if new_login_pw and new_login_pw == new_login_pw2:
            change_password(USERNAME, new_login_pw)
            st.success("✅ 비밀번호 변경 완료!")
        elif new_login_pw != new_login_pw2:
            st.error("비밀번호가 일치하지 않습니다.")



# ═══════════════════════════════════════
# 제품 DB 탭
# ═══════════════════════════════════════
elif tab_choice == "📦 제품 DB":
    st.header("📦 제품 가격 DB 관리")
    st.caption("🔗 공유 필드(매입가·상품명)는 읽기전용 — 영수증 업로드 또는 관리자 탭에서 수정 | ✏️ 판매가·배송비는 개인별 수정 가능")

    # ── 네이버 상품 등록 폼 ─────────────────────────────────────────
    _nreg_sp_id = st.session_state.get('naver_reg_sp_id')
    if _nreg_sp_id is not None:
        _nreg_kw = st.session_state.get('naver_reg_kw', '')
        conn_auth = sqlite3.connect(AUTH_DB)
        conn_auth.row_factory = sqlite3.Row
        _sp = conn_auth.execute("SELECT * FROM shared_products WHERE id=?", (_nreg_sp_id,)).fetchone()
        conn_auth.close()

        if _sp:
            _sp = dict(_sp)
            with st.expander(f"🛍 네이버 스마트스토어 상품 등록 — {_sp['costco_name']}", expanded=True):
                if not HAS_NAVER_API:
                    st.error("naver_api.py 없음")
                elif not api_id or not api_secret:
                    st.warning("⚙️ 설정 탭에서 네이버 API 키를 먼저 입력하세요.")
                else:
                    _saved_cat = _sp.get('naver_category_id') or get_setting(USERNAME, 'naver_default_category') or ''
                    _saved_as  = get_setting(USERNAME, 'naver_as_tel') or ''

                    _reg_name = st.text_input("상품명", value=_sp['costco_name'][:100], key="nreg_name")

                    # ── 카테고리 검색 UI ──────────────────────────────────
                    st.markdown("**네이버 카테고리**")
                    _cat_col1, _cat_col2 = st.columns([4, 1])
                    _cat_kw = _cat_col1.text_input(
                        "카테고리 키워드 검색",
                        placeholder="예: 냉동, 건강식품, 피자",
                        key="nreg_cat_kw",
                        label_visibility="collapsed",
                    )
                    _cat_search_btn = _cat_col2.button("🔍 검색", key="nreg_cat_search")

                    if _cat_search_btn and _cat_kw.strip():
                        with st.spinner("카테고리 검색 중..."):
                            _cat_results, _cat_err = naver_api.search_naver_categories(
                                api_id, api_secret, _cat_kw.strip()
                            )
                        if _cat_err:
                            st.warning(f"카테고리 검색 오류: {_cat_err}")
                            st.session_state['nreg_cat_results'] = []
                        elif not _cat_results:
                            st.info("검색 결과 없음")
                            st.session_state['nreg_cat_results'] = []
                        else:
                            st.session_state['nreg_cat_results'] = _cat_results

                    _cat_results_now = st.session_state.get('nreg_cat_results', [])
                    if _cat_results_now:
                        _cat_options = [f"{c['id']} — {c['full_name']}" for c in _cat_results_now]
                        _cat_sel_idx = 0
                        if _saved_cat:
                            _match = next((i for i, c in enumerate(_cat_results_now) if c['id'] == _saved_cat), None)
                            if _match is not None:
                                _cat_sel_idx = _match
                        _cat_chosen = st.selectbox(
                            "카테고리 선택",
                            options=_cat_options,
                            index=_cat_sel_idx,
                            key="nreg_cat_select",
                        )
                        _reg_cat = _cat_chosen.split(" — ")[0].strip() if _cat_chosen else _saved_cat
                        st.caption(f"선택된 카테고리 ID: `{_reg_cat}`")
                    else:
                        _reg_cat = st.text_input(
                            "카테고리 ID 직접 입력",
                            value=_saved_cat,
                            placeholder="예: 50000803",
                            key="nreg_cat",
                            label_visibility="collapsed",
                        )
                        if _saved_cat:
                            st.caption(f"저장된 카테고리 ID: `{_saved_cat}`")
                        else:
                            st.caption("키워드 검색 후 선택하거나 ID를 직접 입력하세요.")

                    _cat_refresh_col1, _cat_refresh_col2 = st.columns([6, 1])
                    if _cat_refresh_col2.button("🔄 카테고리 갱신", key="nreg_cat_refresh", help="네이버 카테고리 캐시를 강제 갱신합니다"):
                        with st.spinner("카테고리 목록 갱신 중..."):
                            _rf_cats, _rf_err = naver_api.load_naver_category_cache(api_id, api_secret, force_refresh=True)
                        if _rf_err:
                            st.error(f"갱신 실패: {_rf_err}")
                        else:
                            st.success(f"✅ {len(_rf_cats):,}개 카테고리 갱신 완료")
                    # ─────────────────────────────────────────────────────

                    rc3, rc4, rc5 = st.columns(3)
                    _up = next((x for x in get_all_products_merged(USERNAME) if x.get('shared_id') == _nreg_sp_id), {})
                    _def_price = int(_up.get('sale_price') or 0) or int(_sp.get('unit_price') or 0)
                    _def_fee   = int(_up.get('shipping_fee') or 0)
                    _reg_price = rc3.number_input("판매가 (원)", value=_def_price, step=100, key="nreg_price")
                    _reg_fee   = rc4.number_input("배송비 (0=무료)", value=_def_fee, step=500, key="nreg_fee")
                    _reg_stock = rc5.number_input("재고 수량", value=100, step=10, key="nreg_stock")

                    rc6, rc7 = st.columns(2)
                    _reg_as   = rc6.text_input("A/S 전화번호", value=_saved_as, placeholder="010-0000-0000", key="nreg_as")
                    _reg_orig = rc7.selectbox("원산지", ["국내산 (03)", "해외산 (04)"], key="nreg_orig")
                    _orig_code = "03" if "03" in _reg_orig else "04"

                    _image_src = _sp.get('local_image') or _sp.get('image_url') or ''
                    if _image_src:
                        st.image(_image_src, width=80, caption="등록 이미지")
                    else:
                        st.warning("이미지 없음 — 크롤링 후 재시도 권장")

                    btn_c1, btn_c2 = st.columns([1, 4])
                    if btn_c2.button("✖ 취소", key="nreg_cancel"):
                        st.session_state.pop('naver_reg_sp_id', None)
                        st.session_state.pop('naver_reg_kw', None)
                        st.rerun()

                    if btn_c1.button("🛍 네이버 등록", key="nreg_submit", type="primary"):
                        if not _reg_cat.strip():
                            st.error("카테고리 ID를 입력하세요.")
                        elif not _reg_price:
                            st.error("판매가를 입력하세요.")
                        elif not _image_src:
                            st.error("이미지가 없습니다. 먼저 크롤링을 실행하세요.")
                        else:
                            with st.spinner("이미지 업로드 중..."):
                                _cdn_url, _err = naver_api.upload_product_image(api_id, api_secret, _image_src)
                            if _err or not _cdn_url:
                                st.error(f"이미지 업로드 실패: {_err}")
                            else:
                                with st.spinner("네이버 상품 등록 중..."):
                                    _result, _err2 = naver_api.register_product(api_id, api_secret, {
                                        "name": _reg_name,
                                        "sale_price": _reg_price,
                                        "image_url": _cdn_url,
                                        "category_id": _reg_cat.strip(),
                                        "stock": _reg_stock,
                                        "shipping_fee": _reg_fee,
                                        "after_service_tel": _reg_as,
                                        "origin_code": _orig_code,
                                    })
                                if _err2 or not _result:
                                    st.error(f"상품 등록 실패: {_err2}")
                                else:
                                    _npno = _result.get("origin_product_no", "")
                                    # 사용자 products 테이블에 네이버 상품번호 저장
                                    upsert_user_private(USERNAME, _nreg_kw,
                                                        _sp['costco_name'],
                                                        naver_product_no=_npno)
                                    # 카테고리 ID를 shared_products 및 사용자 기본값으로 저장
                                    try:
                                        _ca = sqlite3.connect(AUTH_DB)
                                        _ca.execute("UPDATE shared_products SET naver_category_id=? WHERE id=?",
                                                    (_reg_cat.strip(), _nreg_sp_id))
                                        _ca.commit(); _ca.close()
                                    except Exception:
                                        pass
                                    set_setting(USERNAME, 'naver_default_category', _reg_cat.strip())
                                    set_setting(USERNAME, 'naver_as_tel', _reg_as)
                                    st.success(f"✅ 등록 완료! 네이버 상품번호: {_npno}")
                                    st.session_state.pop('naver_reg_sp_id', None)
                                    st.session_state.pop('naver_reg_kw', None)
                                    st.rerun()
        else:
            st.session_state.pop('naver_reg_sp_id', None)
            st.session_state.pop('naver_reg_kw', None)

    products = get_all_products_merged(USERNAME)
    if products:
        # ── 카테고리 버튼 ──
        _all_cats = sorted({p.get('category', '') for p in products if p.get('category', '')})
        if 'product_cat_filter' not in st.session_state:
            st.session_state['product_cat_filter'] = '전체'
        _cat_filter = st.session_state['product_cat_filter']

        _cat_buttons = ['전체'] + _all_cats
        if _all_cats:
            _btn_cols = st.columns(min(len(_cat_buttons), 8))
            for _ci, _cat in enumerate(_cat_buttons):
                _active = (_cat == _cat_filter)
                _style = "primary" if _active else "secondary"
                if _btn_cols[_ci % 8].button(_cat, key=f"cat_btn_{_ci}",
                                              type=_style, use_container_width=True):
                    st.session_state['product_cat_filter'] = _cat
                    st.session_state['product_page'] = 1
                    st.rerun()

        # ── 검색 ──
        s_col, _ = st.columns([2, 3])
        search_q = s_col.text_input("🔍 검색", placeholder="상품명 또는 상품번호", key="product_search")

        filtered_products = products
        if _cat_filter != '전체':
            filtered_products = [p for p in filtered_products if p.get('category', '') == _cat_filter]
        if search_q:
            sq_low = search_q.strip().lower()
            filtered_products = [p for p in filtered_products if
                sq_low in p.get('costco_name', '').lower() or
                sq_low in str(p.get('product_no', ''))]

        total_count = len(filtered_products)
        per_page = 30
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        # query_params로 페이지 이동 처리
        try:
            _qp = st.query_params.get("product_page")
            if _qp:
                _qp_v = max(1, min(int(_qp), total_pages))
                st.session_state['product_page'] = _qp_v
                del st.query_params["product_page"]
        except Exception:
            pass

        if 'product_page' not in st.session_state:
            st.session_state['product_page'] = 1
        if st.session_state['product_page'] > total_pages:
            st.session_state['product_page'] = 1
        page = st.session_state['product_page']

        start_idx = (page - 1) * per_page
        end_idx = min(start_idx + per_page, total_count)
        page_products = filtered_products[start_idx:end_idx]

        st.caption(f"총 {total_count}개 제품 (페이지 {page}/{total_pages})")

        # ── 테이블 헤더 (매칭키 제거) ──
        HDR = [0.9, 4.6, 1.05, 1.05, 0.6, 1.2, 1.1, 1.0, 0.6, 0.6, 0.55]
        HDR_LABELS = ['상품번호', '코스트코 상품명', '매장가🔒', '온라인가🔒', '소분🔒', '판매가(네이버)✏️', '고객배송비✏️', '업데이트', '수정', '🛍등록', '삭제']
        hdr_cols = st.columns(HDR)
        for lbl, col in zip(HDR_LABELS, hdr_cols):
            col.markdown(f"<span style='font-size:15px;font-weight:600;color:#555'>{lbl}</span>",
                         unsafe_allow_html=True)
        st.markdown("<hr style='margin:4px 0 2px 0;border-color:#dee2e6'>", unsafe_allow_html=True)

        editing_kw = st.session_state.get('editing_product_kw')

        for p in page_products:
            kw        = p['match_keyword']
            is_shared = p.get('shared_id') is not None
            sq_val    = int(p.get('split_qty', 1) or 1)
            fee_val   = int(p.get('shipping_fee', 0) or 0)
            sale_val  = int(p.get('sale_price', 0) or 0)

            if editing_kw == kw:
                st.markdown(
                    "<div style='background:#eaf4fb;border:1px solid #aed6f1;border-radius:6px;"
                    "padding:10px 12px;margin:4px 0'>",
                    unsafe_allow_html=True
                )
                if is_shared:
                    # 공유 제품: 판매가·배송비만 수정 가능
                    st.caption(f"🔗 공유 제품 — 매입가·상품명은 읽기전용 (관리자 탭에서 수정)")
                    fc = st.columns([3.0, 1.5, 1.5, 1.2, 1.0])
                    fc[0].markdown(
                        f"**{p['costco_name']}**  "
                        f"<span style='color:#888;font-size:14px'>({p.get('product_no','') or '-'})</span><br>"
                        f"<span style='color:#555;font-size:14px'>매입가: {fmt(p.get('unit_price',0))}원  |  소분: {sq_val}</span>",
                        unsafe_allow_html=True
                    )
                    e_sale = fc[1].number_input("판매가(네이버)", value=sale_val, min_value=0, step=100,
                                                key=f"e_sale_{kw}", label_visibility="visible")
                    e_fee  = fc[2].number_input("고객배송비 (0=무료)", value=fee_val, min_value=0, step=100,
                                                key=f"e_fee_{kw}", label_visibility="visible")
                    if fc[3].button("✅ 저장", key=f"e_save_{kw}", use_container_width=True, type="primary"):
                        upsert_user_private(USERNAME, kw, p['costco_name'],
                                            sale_price=e_sale, shipping_fee=e_fee)
                        st.session_state.pop('editing_product_kw', None)
                        st.rerun()
                    if fc[4].button("✖ 취소", key=f"e_cancel_{kw}", use_container_width=True):
                        st.session_state.pop('editing_product_kw', None)
                        st.rerun()
                else:
                    # 레거시 개인 제품: 모든 필드 수정 가능
                    fc = st.columns([0.9, 4.6, 1.3, 0.8, 1.2, 1.1, 1.0, 0.8])
                    pid_legacy = p.get('private_id')
                    e_pno  = fc[0].text_input("상품번호", value=p.get('product_no', ''), key=f"e_pno_{kw}",
                                              label_visibility="collapsed", placeholder="상품번호")
                    e_name = fc[1].text_input("상품명",   value=p['costco_name'],        key=f"e_name_{kw}",
                                              label_visibility="collapsed")
                    e_price= fc[2].number_input("매입가", value=int(p.get('unit_price', 0) or 0),
                                                step=100, key=f"e_price_{kw}", label_visibility="collapsed")
                    e_sq   = fc[3].number_input("소분", value=sq_val, min_value=1, max_value=20,
                                                key=f"e_sq_{kw}", label_visibility="collapsed")
                    e_sale = fc[4].number_input("판매가", value=sale_val, min_value=0, step=100,
                                                key=f"e_sale2_{kw}", label_visibility="collapsed")
                    e_fee  = fc[5].number_input("배송비", value=fee_val, min_value=0, step=100,
                                                key=f"e_fee2_{kw}", label_visibility="collapsed")
                    if fc[6].button("✅ 저장", key=f"e_save2_{kw}", use_container_width=True, type="primary"):
                        if pid_legacy:
                            conn_u = get_user_db(USERNAME)
                            conn_u.execute(
                                "UPDATE products SET product_no=?, costco_name=?, match_keyword=?, "
                                "unit_price=?, split_qty=?, sale_price=?, shipping_fee=?, updated_at=? WHERE id=?",
                                (e_pno, e_name, kw, e_price, e_sq, e_sale, e_fee,
                                 datetime.now().strftime("%Y-%m-%d %H:%M"), pid_legacy)
                            )
                            conn_u.commit(); conn_u.close()
                        st.session_state.pop('editing_product_kw', None)
                        st.rerun()
                    if fc[7].button("✖", key=f"e_cancel2_{kw}", use_container_width=True):
                        st.session_state.pop('editing_product_kw', None)
                        st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

            else:
                # ── 일반 표시 행 ──
                row_cols = st.columns(HDR)
                pt_cur    = p.get('price_type') or '매장'
                price_fmt = f"{fmt(p.get('unit_price', 0))}원"
                if pt_cur == '온라인':
                    store_disp  = "<span style='font-size:17px;color:#ccc'>-</span>"
                    online_disp = f"<span style='font-size:17px;font-weight:600;color:#1565c0'>🌐 {price_fmt}</span>"
                else:
                    store_disp  = f"<span style='font-size:17px;font-weight:600;color:#2e7d32'>{price_fmt}</span>"
                    online_disp = "<span style='font-size:17px;color:#ccc'>-</span>"
                sq_label   = f"÷{sq_val}" if sq_val > 1 else "-"
                sq_color   = "color:#1565C0;font-weight:bold" if sq_val > 1 else "color:#888"
                fee_label  = "무료" if fee_val == 0 else f"{fee_val:,}원"
                fee_color  = "color:#2e7d32;font-weight:600" if fee_val == 0 else "color:#555"
                sale_label = f"{sale_val:,}원" if sale_val > 0 else "-"
                sale_color = "color:#1a237e;font-weight:600" if sale_val > 0 else "color:#ccc"
                updated_str = (p.get('shared_updated_at') or '')[:10]
                row_cols[0].markdown(
                    f"<span style='font-size:16px;color:#888'>{p.get('product_no','') or '-'}</span>",
                    unsafe_allow_html=True)
                _thumb = p.get('image_url', '')
                _img_html = (
                    f"<img src='{_thumb}' width='57' height='57' "
                    f"style='object-fit:cover;border-radius:6px;"
                    f"vertical-align:middle;margin-right:8px;border:1px solid #eee'>"
                    if _thumb else ""
                )
                row_cols[1].markdown(
                    f"{_img_html}<span style='font-size:17px'>{p['costco_name']}</span>",
                    unsafe_allow_html=True)
                row_cols[2].markdown(store_disp, unsafe_allow_html=True)
                row_cols[3].markdown(online_disp, unsafe_allow_html=True)
                row_cols[4].markdown(
                    f"<span style='font-size:17px;{sq_color}'>{sq_label}</span>",
                    unsafe_allow_html=True)
                row_cols[5].markdown(
                    f"<span style='font-size:17px;{sale_color}'>{sale_label}</span>",
                    unsafe_allow_html=True)
                row_cols[6].markdown(
                    f"<span style='font-size:17px;{fee_color}'>{fee_label}</span>",
                    unsafe_allow_html=True)
                row_cols[7].markdown(
                    f"<span style='font-size:15px;color:#888'>{updated_str}</span>",
                    unsafe_allow_html=True)
                if row_cols[8].button("✏️", key=f"edit_btn_{kw}", use_container_width=True):
                    st.session_state['editing_product_kw'] = kw
                    st.rerun()
                _n_registered = bool(p.get('naver_product_no'))
                _n_label = "✅" if _n_registered else "🛍"
                if row_cols[9].button(_n_label, key=f"nreg_btn_{kw}", use_container_width=True,
                                      help="네이버 스마트스토어 등록" if not _n_registered else f"등록됨 ({p.get('naver_product_no')})"):
                    st.session_state['naver_reg_sp_id'] = p.get('shared_id')
                    st.session_state['naver_reg_kw'] = kw
                    st.rerun()
                if row_cols[10].button("🗑", key=f"del_btn_{kw}", use_container_width=True):
                    pid_del = p.get('private_id')
                    if pid_del:
                        conn_u = get_user_db(USERNAME)
                        conn_u.execute("DELETE FROM products WHERE id=?", (pid_del,))
                        conn_u.commit(); conn_u.close()
                    st.session_state.pop('editing_product_kw', None)
                    st.rerun()

            st.markdown("<hr style='margin:-4px 0 -6px 0;border-color:#f0f0f0'>", unsafe_allow_html=True)

        # ── 페이지 번호 — 하단 중앙 (HTML 링크 스타일) ──
        if total_pages > 1:
            _s = max(1, page - 5)
            _e = min(total_pages, _s + 9)
            if _e - _s < 9: _s = max(1, _e - 9)
            _a = "color:#555;text-decoration:none;padding:4px 10px;font-size:14px"
            _n = "color:#333;text-decoration:none;padding:4px 10px;font-size:14px"
            _cur = ("border:1px solid #e74c3c;color:#e74c3c;padding:3px 9px;"
                    "border-radius:3px;font-size:14px;font-weight:700")
            _pg_parts = []
            if page > 1:
                _pg_parts.append(f'<a href="?product_page={page-1}" style="{_a}">&lt; 이전</a>')
            for _p in range(_s, _e + 1):
                if _p == page:
                    _pg_parts.append(f'<span style="{_cur}">{_p}</span>')
                else:
                    _pg_parts.append(f'<a href="?product_page={_p}" style="{_n}">{_p}</a>')
            if page < total_pages:
                _pg_parts.append(f'<a href="?product_page={page+1}" style="{_a}">다음 &gt;</a>')
            st.markdown(
                '<div style="display:flex;justify-content:center;align-items:center;'
                'gap:2px;padding:14px 0">' + ''.join(_pg_parts) + '</div>',
                unsafe_allow_html=True
            )
    else:
        st.info("등록된 제품이 없습니다. 영수증 등록 메뉴에서 추가하세요.")

    # ── 가격 변동 이력 ──────────────────────────────────────────
    st.divider()
    st.subheader("📈 코스트코 가격 변동 이력")
    ph_rows = get_price_change_history(USERNAME, limit=100)
    if ph_rows:
        ph_df = pd.DataFrame(ph_rows)
        ph_df['변동'] = ph_df.apply(
            lambda r: f"{'🔺' if r['diff'] > 0 else '🔻'} {int(r['diff']):+,}원 ({r['diff_pct']:+.1f}%)", axis=1
        )
        ph_df['고객배송비'] = ph_df['shipping_fee'].apply(lambda f: "무료" if int(f or 0) == 0 else f"{int(f):,}원")
        ph_df['알림'] = ph_df['notified'].apply(lambda x: "✅" if x else "-")
        ph_df['네이버적용'] = ph_df['naver_updated'].apply(lambda x: "✅" if x else "-")
        display_cols = {
            'created_at': '일시', 'costco_name': '상품명',
            'old_cost': '이전가', 'new_cost': '새 매입가',
            '변동': '변동', '고객배송비': '고객배송비', '알림': '알림발송', '네이버적용': '네이버적용'
        }
        ph_show = ph_df[[c for c in display_cols.keys() if c in ph_df.columns] + ['변동', '고객배송비', '알림', '네이버적용']].copy()
        ph_show = ph_show.rename(columns=display_cols)
        if '이전가' in ph_show.columns:
            ph_show['이전가'] = ph_show['이전가'].apply(lambda x: f"{int(x):,}원")
        if '새 매입가' in ph_show.columns:
            ph_show['새 매입가'] = ph_show['새 매입가'].apply(lambda x: f"{int(x):,}원")
        st.dataframe(ph_show, use_container_width=True, hide_index=True)

        if st.button("🗑 이력 전체 삭제", key="del_price_history"):
            conn = get_user_db(USERNAME)
            conn.execute("DELETE FROM price_change_history")
            conn.commit(); conn.close()
            st.success("이력 삭제 완료")
            st.rerun()
    else:
        st.info("아직 가격 변동 이력이 없습니다. 영수증을 업로드하면 자동으로 감지됩니다.")


# ═══════════════════════════════════════
# 탭 6: 관리자
# ═══════════════════════════════════════
elif tab_choice == "👑 관리자" and IS_ADMIN:
    st.header("👑 관리자 페이지")

    # ── 회원가입 승인 대기 ──────────────────────────────────────────
    pending_users = get_pending_users()
    if pending_users:
        st.subheader(f"⏳ 승인 대기 ({len(pending_users)}명)")
        st.warning("아래 신청자를 승인하거나 거절하세요.")
        for u in pending_users:
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.markdown(f"**{u['display_name']}** (`{u['username']}`) — {u['created_at']}")
            if c2.button("✅ 승인", key=f"approve_{u['username']}", use_container_width=True, type="primary"):
                approve_user(u['username'])
                st.success(f"✅ '{u['display_name']}' 승인 완료!")
                st.rerun()
            if c3.button("❌ 거절", key=f"reject_{u['username']}", use_container_width=True):
                reject_user(u['username'])
                st.warning(f"'{u['display_name']}' 거절됨")
                st.rerun()
        st.divider()

    # ── 회원가입 설정 ───────────────────────────────────────────────
    st.subheader("⚙️ 회원가입 설정")
    cur_allow   = get_global_setting('allow_signup', '1')
    cur_approve = get_global_setting('require_approval', '1')
    c1, c2 = st.columns(2)
    new_allow   = c1.toggle("회원가입 허용", value=(cur_allow == '1'), key="toggle_allow_signup")
    new_approve = c2.toggle("신규 가입 시 관리자 승인 필요", value=(cur_approve == '1'), key="toggle_require_approval")
    if st.button("설정 저장", key="save_signup_settings"):
        set_global_setting('allow_signup', '1' if new_allow else '0')
        set_global_setting('require_approval', '1' if new_approve else '0')
        st.success("✅ 설정 저장 완료!")
        st.rerun()

    st.divider()
    st.subheader("👥 사용자 목록")
    users = get_all_users()
    status_labels = {'active': '✅ 활성', 'pending': '⏳ 대기', 'rejected': '❌ 거절'}
    for u in users:
        role = "👑 관리자" if u['is_admin'] else "👤 일반"
        status_txt = status_labels.get(u.get('status', 'active'), '✅')
        with st.expander(f"{role} {u['display_name']} ({u['username']}) — {status_txt} | {u['created_at']}"):
            if not u['is_admin']:
                c1, c2, c3 = st.columns(3)
                if u.get('status') == 'pending':
                    if c1.button(f"✅ 승인", key=f"approve2_{u['username']}", use_container_width=True):
                        approve_user(u['username'])
                        st.rerun()
                if c2.button(f"🗑 삭제", key=f"del_{u['username']}", use_container_width=True):
                    delete_user(u['username'])
                    st.success(f"✅ '{u['username']}' 삭제 완료!")
                    st.rerun()
                reset_pw = c3.text_input("새 비밀번호", key=f"reset_{u['username']}", type="password")
                if c3.button("비밀번호 초기화", key=f"resetbtn_{u['username']}", use_container_width=True):
                    if reset_pw:
                        change_password(u['username'], reset_pw)
                        st.success(f"✅ '{u['username']}' 비밀번호 변경 완료!")

    st.divider()
    st.subheader("➕ 사용자 직접 추가")
    c1, c2, c3 = st.columns(3)
    new_id = c1.text_input("아이디", key="new_user_id")
    new_name = c2.text_input("이름", key="new_user_name")
    new_pw_admin = c3.text_input("초기 비밀번호", type="password", key="new_user_pw")

    if st.button("사용자 추가", type="primary", key="add_user"):
        if new_id and new_pw_admin:
            if add_user(new_id, new_pw_admin, new_name):
                init_user_db(new_id)
                st.success(f"✅ '{new_id}' 계정 생성 완료!")
                st.rerun()
            else:
                st.error("이미 존재하는 아이디입니다.")
        else:
            st.warning("아이디와 비밀번호를 입력해주세요.")

    # ── 공유 제품 DB 관리 ──────────────────────────────────────────
    st.divider()
    st.subheader("🏪 공유 제품 DB 관리 (모든 판매자 공용)")
    st.caption("영수증 업로드로 자동 등록되거나 아래에서 직접 추가·수정·삭제할 수 있습니다.")

    shared_all = get_shared_products()
    if shared_all:
        sp_search = st.text_input("🔍 공유 제품 검색", placeholder="상품명 또는 상품번호", key="admin_sp_search")
        disp_shared = shared_all
        if sp_search:
            sl = sp_search.strip().lower()
            disp_shared = [s for s in shared_all if
                sl in s.get('costco_name', '').lower() or
                sl in s.get('match_keyword', '').lower() or
                sl in str(s.get('product_no', ''))]

        # 페이지네이션
        SP_PER_PAGE = 30
        sp_total = len(disp_shared)
        sp_total_pages = max(1, math.ceil(sp_total / SP_PER_PAGE))
        try:
            _sqp = st.query_params.get("admin_sp_page")
            if _sqp:
                _sqp_v = max(1, min(int(_sqp), sp_total_pages))
                st.session_state['admin_sp_page'] = _sqp_v
                del st.query_params["admin_sp_page"]
        except Exception:
            pass
        if 'admin_sp_page' not in st.session_state:
            st.session_state['admin_sp_page'] = 1
        if st.session_state['admin_sp_page'] > sp_total_pages:
            st.session_state['admin_sp_page'] = 1
        sp_page = st.session_state['admin_sp_page']
        sp_start = (sp_page - 1) * SP_PER_PAGE
        page_shared = disp_shared[sp_start: sp_start + SP_PER_PAGE]

        st.caption(f"총 {sp_total}개 (전체 {len(shared_all)}개)  |  페이지 {sp_page}/{sp_total_pages}")

        # 헤더 — 매장가 / 온라인가 분리, 구분 컬럼 제거
        SP_HDR = [0.8, 2.6, 1.7, 1.05, 1.05, 0.6, 1.2, 1.0, 0.7, 0.6]
        SP_LABELS = ['상품번호', '코스트코 상품명', '매칭키', '매장가', '온라인가', '소분', '최종수정자', '업데이트', '수정', '삭제']
        sp_hdr_cols = st.columns(SP_HDR)
        for lbl, col in zip(SP_LABELS, sp_hdr_cols):
            col.markdown(f"<span style='font-size:16px;font-weight:600;color:#555'>{lbl}</span>",
                         unsafe_allow_html=True)
        st.markdown("<hr style='margin:4px 0 2px 0;border-color:#dee2e6'>", unsafe_allow_html=True)

        editing_sp_id = st.session_state.get('admin_editing_sp_id')

        for sp in page_shared:
            spid  = sp['id']
            sq_v  = int(sp.get('split_qty', 1) or 1)
            pt_cur = sp.get('price_type') or '매장'
            price_fmt = f"{fmt(sp['unit_price'])}원"

            # 매장가 / 온라인가 분리 표시
            if pt_cur == '온라인':
                store_disp  = "<span style='font-size:17px;color:#ccc'>-</span>"
                online_disp = f"<span style='font-size:17px;font-weight:600;color:#1565c0'>🌐 {price_fmt}</span>"
            else:
                store_disp  = f"<span style='font-size:17px;font-weight:600;color:#2e7d32'>{price_fmt}</span>"
                online_disp = "<span style='font-size:17px;color:#ccc'>-</span>"

            if editing_sp_id == spid:
                st.markdown(
                    "<div style='background:#fff8e1;border:1px solid #ffe082;border-radius:6px;"
                    "padding:10px 12px;margin:4px 0'>",
                    unsafe_allow_html=True
                )
                fc = st.columns([0.8, 2.6, 1.7, 1.2, 0.6, 0.8, 1.0, 0.8])
                sp_e_pno   = fc[0].text_input("상품번호", value=sp.get('product_no', ''),
                                              key=f"sp_pno_{spid}", label_visibility="collapsed")
                sp_e_name  = fc[1].text_input("상품명",   value=sp['costco_name'],
                                              key=f"sp_name_{spid}", label_visibility="collapsed")
                sp_e_kw    = fc[2].text_input("매칭키",   value=sp['match_keyword'],
                                              key=f"sp_kw_{spid}", label_visibility="collapsed")
                sp_e_price = fc[3].number_input("가격", value=int(sp['unit_price']),
                                                step=100, key=f"sp_price_{spid}", label_visibility="collapsed")
                sp_e_sq    = fc[4].number_input("소분", value=sq_v, min_value=1, max_value=20,
                                                key=f"sp_sq_{spid}", label_visibility="collapsed")
                sp_e_pt    = fc[5].selectbox("구분", ['매장', '온라인'],
                                             index=0 if pt_cur == '매장' else 1,
                                             key=f"sp_pt_{spid}", label_visibility="collapsed")
                if fc[6].button("✅ 저장", key=f"sp_save_{spid}", use_container_width=True, type="primary"):
                    upsert_shared_product(
                        costco_name=sp_e_name, keyword=sp_e_kw,
                        price=sp_e_price, product_no=sp_e_pno,
                        split_qty=sp_e_sq, updated_by=USERNAME, price_type=sp_e_pt
                    )
                    st.session_state.pop('admin_editing_sp_id', None)
                    st.rerun()
                if fc[7].button("✖", key=f"sp_cancel_{spid}", use_container_width=True):
                    st.session_state.pop('admin_editing_sp_id', None)
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            else:
                row = st.columns(SP_HDR)
                row[0].markdown(f"<span style='font-size:16px;color:#888'>{sp.get('product_no','') or '-'}</span>",
                                unsafe_allow_html=True)
                row[1].markdown(f"<span style='font-size:17px'>{sp['costco_name']}</span>",
                                unsafe_allow_html=True)
                row[2].markdown(f"<span style='font-size:16px;color:#555'>{sp['match_keyword']}</span>",
                                unsafe_allow_html=True)
                row[3].markdown(store_disp,  unsafe_allow_html=True)
                row[4].markdown(online_disp, unsafe_allow_html=True)
                row[5].markdown(f"<span style='font-size:17px;color:#888'>{sq_v if sq_v > 1 else '-'}</span>",
                                unsafe_allow_html=True)
                row[6].markdown(f"<span style='font-size:16px;color:#888'>{sp.get('updated_by','')}</span>",
                                unsafe_allow_html=True)
                row[7].markdown(f"<span style='font-size:15px;color:#aaa'>{(sp.get('updated_at','') or '')[:10]}</span>",
                                unsafe_allow_html=True)
                if row[8].button("✏️", key=f"sp_edit_{spid}", use_container_width=True):
                    st.session_state['admin_editing_sp_id'] = spid
                    st.rerun()
                if row[9].button("🗑", key=f"sp_del_{spid}", use_container_width=True):
                    delete_shared_product(spid)
                    st.session_state.pop('admin_editing_sp_id', None)
                    st.rerun()
            st.markdown("<hr style='margin:-4px 0 -6px 0;border-color:#f0f0f0'>", unsafe_allow_html=True)

        # 페이지 번호 — 하단 중앙 (HTML 링크 스타일)
        if sp_total_pages > 1:
            _s = max(1, sp_page - 5)
            _e = min(sp_total_pages, _s + 9)
            if _e - _s < 9: _s = max(1, _e - 9)
            _a = "color:#555;text-decoration:none;padding:4px 10px;font-size:14px"
            _n = "color:#333;text-decoration:none;padding:4px 10px;font-size:14px"
            _cur = ("border:1px solid #e74c3c;color:#e74c3c;padding:3px 9px;"
                    "border-radius:3px;font-size:14px;font-weight:700")
            _pg_parts = []
            if sp_page > 1:
                _pg_parts.append(f'<a href="?admin_sp_page={sp_page-1}" style="{_a}">&lt; 이전</a>')
            for _p in range(_s, _e + 1):
                if _p == sp_page:
                    _pg_parts.append(f'<span style="{_cur}">{_p}</span>')
                else:
                    _pg_parts.append(f'<a href="?admin_sp_page={_p}" style="{_n}">{_p}</a>')
            if sp_page < sp_total_pages:
                _pg_parts.append(f'<a href="?admin_sp_page={sp_page+1}" style="{_a}">다음 &gt;</a>')
            st.markdown(
                '<div style="display:flex;justify-content:center;align-items:center;'
                'gap:2px;padding:14px 0">' + ''.join(_pg_parts) + '</div>',
                unsafe_allow_html=True
            )
    else:
        st.info("공유 제품이 없습니다. 아래에서 추가하거나 영수증 등록 탭에서 업로드하세요.")

    st.divider()
    st.subheader("➕ 공유 제품 직접 추가")
    with st.form("admin_add_shared_product"):
        ac1, ac2, ac3, ac4, ac5, ac6 = st.columns([1.2, 2.8, 1.8, 1.3, 0.7, 0.8])
        a_pno   = ac1.text_input("코스트코 상품번호", placeholder="1234567")
        a_name  = ac2.text_input("코스트코 상품명")
        a_kw    = ac3.text_input("매칭키 (비워두면 상품명 사용)")
        a_price = ac4.number_input("가격 (원)", min_value=0, step=100)
        a_sq    = ac5.number_input("소분", min_value=1, max_value=20, value=1)
        a_pt    = ac6.selectbox("구분", ['매장', '온라인'])
        if st.form_submit_button("➕ 추가", type="primary"):
            a_name = a_name.strip()
            a_kw   = a_kw.strip() or a_name
            if a_name and a_price > 0:
                upsert_shared_product(a_name, a_kw, a_price, a_pno, a_sq, USERNAME, price_type=a_pt)
                st.success(f"✅ '{a_name}' 공유 DB에 추가 완료! ({a_pt}가)")
                st.rerun()
            else:
                st.warning("상품명과 가격을 입력해주세요.")

    # ── 공유 제품 내보내기 / 가져오기 ────────────────────────────────
    st.divider()
    st.subheader("📤 공유 제품 DB 내보내기 / 가져오기")
    st.caption("다른 컴퓨터에 설치된 프로그램과 제품 DB를 동기화할 때 사용합니다.")

    # 개인 DB → 공유 DB 이전
    my_prods = get_all_products(USERNAME)
    shared_cnt = len(get_shared_products())
    if my_prods and shared_cnt == 0:
        st.warning(f"⚠️ 공유 DB가 비어있습니다. 내 개인 DB에 제품 {len(my_prods)}개가 있습니다.")
    if my_prods:
        if st.button(f"⬆️ 내 개인 DB({len(my_prods)}개) → 공유 DB로 이전", key="migrate_to_shared", use_container_width=True, type="primary"):
            migrated, skipped = 0, 0
            for p in my_prods:
                kw = (p.get('match_keyword') or '').strip()
                name = (p.get('costco_name') or '').strip()
                if not kw or not name:
                    skipped += 1
                    continue
                upsert_shared_product(
                    costco_name=name,
                    keyword=kw,
                    price=int(p.get('unit_price', 0) or 0),
                    product_no=p.get('product_no', '') or '',
                    split_qty=int(p.get('split_qty', 1) or 1),
                    updated_by=USERNAME,
                )
                migrated += 1
            st.success(f"✅ {migrated}개 공유 DB로 이전 완료! (건너뜀: {skipped}개)")
            st.rerun()

    st.divider()
    exp_col, imp_col = st.columns(2)

    with exp_col:
        st.markdown("**📤 내보내기**")
        if st.button("JSON 파일로 내보내기", key="export_shared", use_container_width=True):
            all_sp = get_shared_products()
            if all_sp:
                import json as _json
                export_data = []
                for sp in all_sp:
                    export_data.append({
                        "product_no":   sp.get("product_no", ""),
                        "costco_name":  sp["costco_name"],
                        "match_keyword": sp["match_keyword"],
                        "unit_price":   int(sp["unit_price"]),
                        "split_qty":    int(sp.get("split_qty", 1) or 1),
                        "updated_by":   sp.get("updated_by", ""),
                        "updated_at":   sp.get("updated_at", ""),
                    })
                json_bytes = _json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")
                st.download_button(
                    label=f"⬇️ 다운로드 ({len(export_data)}개 제품)",
                    data=json_bytes,
                    file_name=f"shared_products_{datetime.now().strftime('%Y%m%d')}.json",
                    mime="application/json",
                    key="download_shared_json",
                    use_container_width=True,
                )
            else:
                st.warning("내보낼 제품이 없습니다.")

    with imp_col:
        st.markdown("**📥 가져오기**")
        up_json = st.file_uploader("JSON 파일 선택", type=["json"], key="import_shared_json")
        overwrite = st.checkbox("기존 동일 키 제품 덮어쓰기", value=True, key="import_overwrite")
        if up_json and st.button("가져오기 실행", key="do_import_shared", use_container_width=True, type="primary"):
            import json as _json
            try:
                items = _json.loads(up_json.read().decode("utf-8"))
                ok_cnt = 0
                skip_cnt = 0
                conn_imp = sqlite3.connect(AUTH_DB)
                for it in items:
                    kw = it.get("match_keyword", "").strip()
                    name = it.get("costco_name", "").strip()
                    if not kw or not name:
                        skip_cnt += 1
                        continue
                    exists = conn_imp.execute(
                        "SELECT id FROM shared_products WHERE match_keyword=?", (kw,)
                    ).fetchone()
                    if exists and not overwrite:
                        skip_cnt += 1
                        continue
                    conn_imp.execute("""
                        INSERT INTO shared_products
                            (product_no, costco_name, match_keyword, unit_price, split_qty, updated_by, updated_at)
                        VALUES (?,?,?,?,?,?,?)
                        ON CONFLICT(match_keyword) DO UPDATE SET
                            costco_name=excluded.costco_name,
                            unit_price=excluded.unit_price,
                            split_qty=excluded.split_qty,
                            product_no=excluded.product_no,
                            updated_by=excluded.updated_by,
                            updated_at=excluded.updated_at
                    """, (
                        it.get("product_no", ""),
                        name, kw,
                        int(it.get("unit_price", 0)),
                        int(it.get("split_qty", 1) or 1),
                        it.get("updated_by", USERNAME),
                        it.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M")),
                    ))
                    ok_cnt += 1
                conn_imp.commit()
                conn_imp.close()
                st.success(f"✅ {ok_cnt}개 가져오기 완료! (건너뜀: {skip_cnt}개)")
                st.rerun()
            except Exception as e:
                st.error(f"❌ 가져오기 실패: {e}")



# ═══════════════════════════════════════
# 네이버 등록 탭
# ═══════════════════════════════════════
elif tab_choice == "🛍 네이버 등록":
    st.header("🛍 네이버 스마트스토어 상품 등록")
    st.caption("① 카테고리 매핑 설정 → ② 일괄 등록 순서로 진행하세요.")

    if not HAS_NAVER_API:
        st.error("naver_api.py 없음 — 관리자에게 문의하세요.")
        st.stop()
    if not api_id or not api_secret:
        st.warning("⚙️ 설정 탭에서 네이버 API 키를 먼저 입력하세요.")
        st.stop()

    import json as _nr_json
    _nr_all    = get_all_products_merged(USERNAME)
    _nr_kw_sel = st.session_state.get("nreg2_kw")
    _nr_prod   = next((p for p in _nr_all if p["match_keyword"] == _nr_kw_sel), None) if _nr_kw_sel else None

    _cat_map = {}
    try:
        _cat_map = _nr_json.loads(get_setting(USERNAME, "naver_cat_mappings") or "{}")
    except Exception:
        pass
    _nr_all_costco_cats = sorted({p.get("category", "") for p in _nr_all if p.get("category", "")})

    # ── A. 카테고리 매핑 설정 ──────────────────────────────────────────
    _map_set_cnt = sum(1 for c in _nr_all_costco_cats if (_cat_map.get(c) or {}).get("id"))
    with st.expander(
        f"① 카테고리 매핑 설정  ({_map_set_cnt}/{len(_nr_all_costco_cats)} 완료)",
        expanded=(_map_set_cnt < len(_nr_all_costco_cats)),
    ):
        st.caption("코스트코 카테고리별 네이버 카테고리 ID를 한번 설정하면 일괄 등록에 자동 적용됩니다.")
        _mh1, _mh2 = st.columns([4, 1])
        _map_kw = _mh1.text_input("네이버 카테고리 검색", placeholder="예: 냉동, 건강식품, 피자", key="map_srch_kw")
        if _mh2.button("🔍 검색", key="map_srch_btn") and _map_kw.strip():
            with st.spinner("검색 중..."):
                _msr, _mse = naver_api.search_naver_categories(api_id, api_secret, _map_kw.strip())
            st.session_state["map_srch_res"] = _msr if not _mse else []
            if _mse: st.warning(f"검색 오류: {_mse}")
            elif not _msr: st.info("검색 결과 없음")

        if st.session_state.get("map_srch_res"):
            _msr_opts = ["— 선택하세요 —"] + [f"{c['id']} — {c['full_name']}" for c in st.session_state["map_srch_res"]]
            _msr_sel  = st.selectbox("검색 결과", _msr_opts, key="map_srch_sel")
            if _msr_sel != "— 선택하세요 —":
                _msr_id   = _msr_sel.split(" — ")[0].strip()
                _msr_name = _msr_sel.split(" — ", 1)[1] if " — " in _msr_sel else ""
                st.info(f"선택된 ID: **{_msr_id}**  |  {_msr_name}")

        st.divider()
        if not _nr_all_costco_cats:
            st.info("상품에 카테고리 정보가 없습니다.")
        else:
            for _ccat in _nr_all_costco_cats:
                _cur      = _cat_map.get(_ccat) or {}
                _cur_id   = _cur.get("id", "")   if isinstance(_cur, dict) else str(_cur or "")
                _cur_name = _cur.get("name", "") if isinstance(_cur, dict) else ""
                _mc1, _mc2 = st.columns([2, 3])
                _mc1.markdown(f"**{_ccat}**")
                if _cur_id:
                    _mc1.caption(f"현재: `{_cur_id}`")
                _mc2.text_input(
                    f"네이버 카테고리 ID ({_ccat})",
                    value=_cur_id, placeholder="예: 50001234",
                    key=f"mapid_{_ccat}", label_visibility="collapsed",
                )
            if st.button("💾 매핑 저장", key="map_save_btn", type="primary"):
                _new_map = {}
                for _ccat in _nr_all_costco_cats:
                    _v = (st.session_state.get(f"mapid_{_ccat}") or "").strip()
                    if _v:
                        _ex = _cat_map.get(_ccat) or {}
                        _new_map[_ccat] = {"id": _v, "name": (_ex.get("name", "") if isinstance(_ex, dict) else "")}
                set_setting(USERNAME, "naver_cat_mappings", _nr_json.dumps(_new_map, ensure_ascii=False))
                st.success(f"✅ {len(_new_map)}개 카테고리 매핑 저장 완료!")
                st.rerun()

    # ── B. 일괄 등록 ───────────────────────────────────────────────────
    if st.session_state.get("nr2_bulk_results"):
        _br = st.session_state["nr2_bulk_results"]
        _br_ok   = sum(1 for r in _br if r["결과"] == "✅")
        _br_fail = sum(1 for r in _br if r["결과"] == "❌")
        if _br_fail:
            st.error(f"일괄 등록 완료 — 성공 {_br_ok}개 / 실패 {_br_fail}개")
        else:
            st.success(f"일괄 등록 완료 — 전체 {_br_ok}개 성공!")
        st.dataframe(pd.DataFrame(_br), use_container_width=True, hide_index=True)
        if st.button("결과 닫기", key="bulk_clr"):
            st.session_state.pop("nr2_bulk_results", None)
            st.rerun()
        st.divider()

    _bulk_ok, _bulk_nocat, _bulk_noimg, _bulk_noprice = [], [], [], []
    for _bp in _nr_all:
        if _bp.get("naver_product_no"): continue
        _bcat    = _bp.get("category", "")
        _bcat_id = (_cat_map.get(_bcat) or {}).get("id", "") if _bcat else ""
        _bimg    = _bp.get("local_image") or _bp.get("image_url") or ""
        _bprice  = int(_bp.get("sale_price") or 0) or int(_bp.get("unit_price") or 0)
        if   not _bcat_id:  _bulk_nocat.append(_bp)
        elif not _bimg:     _bulk_noimg.append(_bp)
        elif not _bprice:   _bulk_noprice.append(_bp)
        else:
            _bulk_ok.append({**_bp, "_cat_id": _bcat_id, "_img": _bimg, "_price": _bprice})

    with st.expander(f"② 일괄 등록  —  대상 {len(_bulk_ok)}개", expanded=True):
        _bmc1, _bmc2, _bmc3 = st.columns(3)
        _bmc1.metric("등록 가능",       f"{len(_bulk_ok)}개")
        _bmc2.metric("카테고리 미설정",  f"{len(_bulk_nocat)}개", help="① 카테고리 매핑 먼저 설정")
        _bmc3.metric("이미지/가격 없음", f"{len(_bulk_noimg)+len(_bulk_noprice)}개")

        if _bulk_nocat:
            with st.expander(f"카테고리 미설정 상품 ({len(_bulk_nocat)}개)"):
                for _bp in _bulk_nocat:
                    st.caption(f"• {_bp['costco_name']}  (카테고리: {_bp.get('category') or '없음'})")

        if _bulk_ok:
            _bas_c, _bstk_c = st.columns(2)
            _bulk_as  = _bas_c.text_input("A/S 전화번호 (공통)",
                                          value=get_setting(USERNAME, "naver_as_tel") or "",
                                          placeholder="010-0000-0000", key="bulk_as_tel")
            _bulk_stk = _bstk_c.number_input("재고수량 (공통)", value=100, step=10, key="bulk_stk_qty")

            if st.button(f"🚀 {len(_bulk_ok)}개 일괄 등록 시작", type="primary", key="bulk_run_btn"):
                _bprog  = st.progress(0)
                _btxt   = st.empty()
                _bres_list = []
                for _bi, _bp in enumerate(_bulk_ok):
                    _btxt.text(f"처리 중 ({_bi+1}/{len(_bulk_ok)}): {_bp['costco_name'][:35]}")
                    _bcdn, _be1 = naver_api.upload_product_image(api_id, api_secret, _bp["_img"])
                    if _be1 or not _bcdn:
                        _bres_list.append({"상품명": _bp["costco_name"], "결과": "❌", "내용": f"이미지 실패: {_be1}"})
                        _bprog.progress((_bi+1)/len(_bulk_ok))
                        continue
                    _bapi, _be2 = naver_api.register_product(api_id, api_secret, {
                        "name":              _bp["costco_name"][:100],
                        "sale_price":        _bp["_price"],
                        "image_url":         _bcdn,
                        "category_id":       _bp["_cat_id"],
                        "stock":             int(_bulk_stk),
                        "shipping_fee":      int(_bp.get("shipping_fee") or 0),
                        "after_service_tel": _bulk_as or "1588-1234",
                    })
                    if _be2 or not _bapi:
                        _bres_list.append({"상품명": _bp["costco_name"], "결과": "❌", "내용": str(_be2)[:80]})
                    else:
                        _bnpno = _bapi.get("origin_product_no", "")
                        upsert_user_private(USERNAME, _bp["match_keyword"],
                                            _bp["costco_name"], naver_product_no=_bnpno)
                        if _bp.get("shared_id"):
                            try:
                                _bca = sqlite3.connect(AUTH_DB)
                                _bca.execute("UPDATE shared_products SET naver_category_id=? WHERE id=?",
                                             (_bp["_cat_id"], _bp["shared_id"]))
                                _bca.commit(); _bca.close()
                            except Exception:
                                pass
                        _bres_list.append({"상품명": _bp["costco_name"], "결과": "✅",
                                           "내용": f"상품번호 {_bnpno}"})
                    _bprog.progress((_bi+1)/len(_bulk_ok))

                _ok_n = sum(1 for r in _bres_list if r["결과"] == "✅")
                _btxt.text(f"완료! 성공 {_ok_n}개 / 실패 {len(_bres_list)-_ok_n}개")
                if _bulk_as:
                    set_setting(USERNAME, "naver_as_tel", _bulk_as)
                st.session_state["nr2_bulk_results"] = _bres_list
                st.rerun()
        else:
            if not _nr_all:
                st.info("등록할 상품이 없습니다.")
            elif _bulk_nocat:
                st.warning("① 카테고리 매핑을 먼저 설정하세요.")

    # ── C. 개별 등록 폼 ────────────────────────────────────────────
    if _nr_prod:
        _nr_sp_id  = _nr_prod.get("shared_id")
        _saved_cat = ""
        if _nr_sp_id:
            try:
                _ca2 = sqlite3.connect(AUTH_DB)
                _ca2.row_factory = sqlite3.Row
                _nr_sp_row = _ca2.execute("SELECT naver_category_id FROM shared_products WHERE id=?",
                                          (_nr_sp_id,)).fetchone()
                _ca2.close()
                if _nr_sp_row:
                    _saved_cat = _nr_sp_row["naver_category_id"] or ""
            except Exception:
                pass
        if not _saved_cat:
            _prod_ccat = _nr_prod.get("category", "")
            _saved_cat = (_cat_map.get(_prod_ccat) or {}).get("id", "") if _prod_ccat else ""
        _saved_cat = _saved_cat or get_setting(USERNAME, "naver_default_category") or ""
        _saved_as  = get_setting(USERNAME, "naver_as_tel") or ""

        with st.expander(f"✏️ 개별 등록 — {_nr_prod['costco_name']}", expanded=True):
            _nr_name = st.text_input("상품명", value=_nr_prod["costco_name"][:100], key="nr2_name")

            st.markdown("**네이버 카테고리**")
            _nr_cc1, _nr_cc2 = st.columns([4, 1])
            _nr_cat_kw = _nr_cc1.text_input("카테고리 키워드", placeholder="예: 냉동, 건강식품",
                                             key="nr2_cat_kw", label_visibility="collapsed")
            if _nr_cc2.button("🔍 검색", key="nr2_cat_search") and _nr_cat_kw.strip():
                with st.spinner():
                    _nr_cr, _nr_ce = naver_api.search_naver_categories(api_id, api_secret, _nr_cat_kw.strip())
                st.session_state["nr2_cat_results"] = _nr_cr if not _nr_ce else []
                if _nr_ce: st.warning(f"검색 오류: {_nr_ce}")
                elif not _nr_cr: st.info("검색 결과 없음")

            _nr_catlist = st.session_state.get("nr2_cat_results", [])
            if _nr_catlist:
                _nr_catopts = [f"{c['id']} — {c['full_name']}" for c in _nr_catlist]
                _nr_catidx  = 0
                if _saved_cat:
                    _nm = next((i for i, c in enumerate(_nr_catlist) if c["id"] == _saved_cat), None)
                    if _nm is not None: _nr_catidx = _nm
                _nr_catchosen = st.selectbox("카테고리 선택", options=_nr_catopts,
                                             index=_nr_catidx, key="nr2_cat_sel")
                _nr_cat = _nr_catchosen.split(" — ")[0].strip() if _nr_catchosen else _saved_cat
                st.caption(f"선택된 카테고리 ID: `{_nr_cat}`")
            else:
                _nr_cat = st.text_input("카테고리 ID", value=_saved_cat,
                                        placeholder="예: 50000803", key="nr2_cat",
                                        label_visibility="collapsed")
                st.caption(f"저장된 카테고리 ID: `{_saved_cat}`" if _saved_cat
                           else "키워드 검색 또는 ID 직접 입력")

            if st.columns([6, 1])[1].button("🔄 갱신", key="nr2_cat_refresh"):
                with st.spinner():
                    _rf2, _rf2e = naver_api.load_naver_category_cache(api_id, api_secret, force_refresh=True)
                if _rf2e: st.error(f"갱신 실패: {_rf2e}")
                else: st.success(f"✅ {len(_rf2):,}개 갱신 완료")

            _nr_c3, _nr_c4, _nr_c5 = st.columns(3)
            _nr_defprice = int(_nr_prod.get("sale_price") or 0) or int(_nr_prod.get("unit_price") or 0)
            _nr_deffee   = int(_nr_prod.get("shipping_fee") or 0)
            _nr_price = _nr_c3.number_input("판매가 (원)",    value=_nr_defprice, step=100, key="nr2_price")
            _nr_fee   = _nr_c4.number_input("배송비 (0=무료)", value=_nr_deffee,   step=500, key="nr2_fee")
            _nr_stock = _nr_c5.number_input("재고 수량",       value=100,          step=10,  key="nr2_stock")
            _nr_as    = st.text_input("A/S 전화번호", value=_saved_as,
                                      placeholder="010-0000-0000", key="nr2_as")

            _nr_img = _nr_prod.get("local_image") or _nr_prod.get("image_url") or ""
            if _nr_img: st.image(_nr_img, width=80, caption="등록 이미지")
            else: st.warning("이미지 없음 — 크롤링 후 재시도 권장")

            _nr_b1, _nr_b2 = st.columns([1, 4])
            if _nr_b2.button("✖ 취소", key="nr2_cancel"):
                st.session_state.pop("nreg2_kw", None)
                st.session_state.pop("nr2_cat_results", None)
                st.rerun()
            if _nr_b1.button("🛍 등록", key="nr2_submit", type="primary"):
                if not _nr_cat.strip(): st.error("카테고리 ID를 입력하세요.")
                elif not _nr_price:    st.error("판매가를 입력하세요.")
                elif not _nr_img:      st.error("이미지가 없습니다.")
                else:
                    with st.spinner("이미지 업로드 중..."):
                        _nr_cdn, _nr_e1 = naver_api.upload_product_image(api_id, api_secret, _nr_img)
                    if _nr_e1 or not _nr_cdn:
                        st.error(f"이미지 업로드 실패: {_nr_e1}")
                    else:
                        with st.spinner("상품 등록 중..."):
                            _nr_res, _nr_e2 = naver_api.register_product(api_id, api_secret, {
                                "name": _nr_name, "sale_price": _nr_price,
                                "image_url": _nr_cdn, "category_id": _nr_cat.strip(),
                                "stock": _nr_stock, "shipping_fee": _nr_fee,
                                "after_service_tel": _nr_as,
                            })
                        if _nr_e2 or not _nr_res:
                            st.error(f"상품 등록 실패: {_nr_e2}")
                        else:
                            _nr_npno = _nr_res.get("origin_product_no", "")
                            upsert_user_private(USERNAME, _nr_kw_sel, _nr_prod["costco_name"],
                                                naver_product_no=_nr_npno)
                            if _nr_sp_id:
                                try:
                                    _ca3 = sqlite3.connect(AUTH_DB)
                                    _ca3.execute("UPDATE shared_products SET naver_category_id=? WHERE id=?",
                                                 (_nr_cat.strip(), _nr_sp_id))
                                    _ca3.commit(); _ca3.close()
                                except Exception: pass
                            set_setting(USERNAME, "naver_default_category", _nr_cat.strip())
                            set_setting(USERNAME, "naver_as_tel", _nr_as)
                            st.success(f"✅ 등록 완료! 네이버 상품번호: {_nr_npno}")
                            st.session_state.pop("nreg2_kw", None)
                            st.session_state.pop("nr2_cat_results", None)
                            st.rerun()

    # ── D. 상품 목록 ──────────────────────────────────────────────────
    st.divider()
    _nr_cats = sorted({p.get("category", "") for p in _nr_all if p.get("category", "")})
    if "nr2_cat_filter" not in st.session_state:
        st.session_state["nr2_cat_filter"] = "전체"
    _nr_cat_active = st.session_state["nr2_cat_filter"]
    if _nr_cats:
        _nr_cat_btns = ["전체"] + _nr_cats
        _nr_cbcols = st.columns(min(len(_nr_cat_btns), 8))
        for _ci, _cname in enumerate(_nr_cat_btns):
            _cstyle = "primary" if _cname == _nr_cat_active else "secondary"
            if _nr_cbcols[_ci % 8].button(_cname, key=f"nr2_catbtn_{_ci}",
                                           type=_cstyle, use_container_width=True):
                st.session_state["nr2_cat_filter"] = _cname
                st.rerun()

    if _nr_cat_active == "전체":
        _nr_cat_filtered = _nr_all
    else:
        _nr_cat_filtered = [p for p in _nr_all if p.get("category", "") == _nr_cat_active]

    _nr_unreg = [p for p in _nr_cat_filtered if not p.get("naver_product_no")]
    _nr_reg   = [p for p in _nr_cat_filtered if p.get("naver_product_no")]
    st.caption(f"전체 {len(_nr_cat_filtered)}개 · 미등록 {len(_nr_unreg)}개 · 등록완료 {len(_nr_reg)}개")

    _nr_flt = st.radio("필터", ["전체", f"미등록 ({len(_nr_unreg)})", f"등록완료 ({len(_nr_reg)})"],
                       horizontal=True, key="nr2_filter")
    if "미등록" in _nr_flt:    _nr_show = _nr_unreg
    elif "등록완료" in _nr_flt: _nr_show = _nr_reg
    else:                       _nr_show = _nr_cat_filtered

    if not _nr_show:
        st.info("상품이 없습니다. 먼저 제품 DB에서 상품을 추가하세요.")
    else:
        _nr_hcols = st.columns([1, 5, 2, 3, 1])
        for _h, _t in zip(_nr_hcols, ["이미지", "상품명", "판매가", "네이버 등록", "등록"]):
            _h.markdown(f"**{_t}**")
        st.markdown("<hr style='margin:4px 0 2px 0'>", unsafe_allow_html=True)
        for _np in _nr_show:
            _nr_row = st.columns([1, 5, 2, 3, 1])
            _np_thumb = _np.get("image_url", "")
            if _np_thumb:
                _nr_row[0].markdown(
                    f"<img src='{_np_thumb}' width='52' height='52' "
                    f"style='object-fit:cover;border-radius:5px'>",
                    unsafe_allow_html=True)
            else:
                _nr_row[0].markdown("—")
            _nr_row[1].markdown(_np["costco_name"])
            _np_sale = int(_np.get("sale_price") or 0)
            _nr_row[2].markdown(f"{fmt(_np_sale)}원" if _np_sale else "—")
            _np_nno = _np.get("naver_product_no") or ""
            _nr_row[3].markdown(f"✅ `{_np_nno}`" if _np_nno else "미등록")
            if _nr_row[4].button("✅" if _np_nno else "🛍",
                                  key=f"nr2_btn_{_np['match_keyword']}", use_container_width=True):
                st.session_state["nreg2_kw"] = _np["match_keyword"]
                st.session_state.pop("nr2_cat_results", None)
                st.rerun()
            st.markdown("<hr style='margin:-4px 0 -6px 0;border-color:#f0f0f0'>", unsafe_allow_html=True)

elif tab_choice == "🤖 자동화":
    st.header("🤖 자동화 설정")
    st.caption("Windows 작업 스케줄러를 통해 매일 지정된 시간에 자동 실행됩니다.")

    SCRIPT_PATH = os.path.join(BASE_DIR, "auto_task.py")
    PYTHON_PATH = sys.executable

    def _schtasks_run(args_list):
        try:
            r = subprocess.run(
                ["schtasks"] + args_list,
                capture_output=True, text=True, encoding="cp949", errors="replace"
            )
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as e:
            return False, str(e)

    def _register_task(task_name, task_type, time_str, user):
        cmd = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task {task_type} --user {user}'
        ok, out = _schtasks_run([
            "/create", "/tn", task_name,
            "/tr", cmd,
            "/sc", "daily", "/st", time_str,
            "/f"
        ])
        return ok, out

    def _delete_task(task_name):
        ok, out = _schtasks_run(["/delete", "/tn", task_name, "/f"])
        return ok, out

    def _query_task(task_name):
        ok, out = _schtasks_run(["/query", "/tn", task_name, "/fo", "LIST"])
        return ok, out

    TASK1_NAME = f"CostcoHotdeal_Shopping_{USERNAME}"
    TASK2_NAME = f"CostcoHotdeal_Shipping_{USERNAME}"
    TASK3_NAME = "CostcoHotdeal_Crawl"

    # ── 현재 스케줄 상태 ──
    with st.expander("📌 현재 등록된 작업 스케줄러 상태", expanded=True):
        c1, c2, c3 = st.columns(3)
        t1_ok, t1_out = _query_task(TASK1_NAME)
        t2_ok, t2_out = _query_task(TASK2_NAME)
        t3_ok, t3_out = _query_task(TASK3_NAME)
        with c1:
            if t1_ok:
                st.success("✅ Task 1 (장보기) 등록됨")
                st.code(t1_out[:400], language=None)
            else:
                st.warning("⚠️ Task 1 미등록")
        with c2:
            if t2_ok:
                st.success("✅ Task 2 (발송처리) 등록됨")
                st.code(t2_out[:400], language=None)
            else:
                st.warning("⚠️ Task 2 미등록")
        with c3:
            if t3_ok:
                st.success("✅ Task 3 (크롤링) 등록됨")
                st.code(t3_out[:400], language=None)
            else:
                st.warning("⚠️ Task 3 미등록")

    st.divider()

    # ── Task 1: 장보기 목록 발송 ──
    st.subheader("📋 Task 1 — 장보기 목록 카카오 발송")
    st.caption("매일 지정 시간에 배송준비 주문을 조회하고 장보기 목록을 카카오톡/텔레그램으로 전송합니다.")

    task1_en = get_setting(USERNAME, 'auto_shopping_enabled') == '1'
    task1_time_str = get_setting(USERNAME, 'auto_shopping_time') or '09:00'
    t1h, t1m = [int(x) for x in task1_time_str.split(':')]

    c1, c2 = st.columns([1, 2])
    new_t1_en = c1.checkbox("활성화", value=task1_en, key="t1_en")
    new_t1_time = c2.time_input("실행 시간", value=dtime(t1h, t1m), key="t1_time")

    col_s1, col_d1, col_run1 = st.columns(3)
    if col_s1.button("💾 Task 1 저장 & 등록", key="save_t1", type="primary", use_container_width=True):
        t1_str = new_t1_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_shopping_enabled', '1' if new_t1_en else '0')
        set_setting(USERNAME, 'auto_shopping_time', t1_str)
        if new_t1_en:
            ok, out = _register_task(TASK1_NAME, "shopping", t1_str, USERNAME)
            if ok:
                st.success(f"✅ Task 1 등록 완료 — 매일 {t1_str} 자동 실행")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
        else:
            _delete_task(TASK1_NAME)
            st.info("Task 1 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if col_d1.button("🗑 Task 1 삭제", key="del_t1", use_container_width=True):
        ok, out = _delete_task(TASK1_NAME)
        set_setting(USERNAME, 'auto_shopping_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if col_run1.button("▶ 지금 테스트 실행", key="run_t1", use_container_width=True):
        with st.spinner("Task 1 실행 중..."):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "shopping", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120
            )
        output = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            st.success("✅ 실행 완료")
        else:
            st.error("❌ 실행 중 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── Task 2: 자동 발송처리 ──
    st.subheader("🚀 Task 2 — CJ 접수 + 네이버 일괄 발송처리")
    st.caption("매일 지정 시간에 배송준비 주문을 CJ 택배에 접수하고 네이버 스마트스토어에 자동 발송처리합니다.")

    cj_id_check = get_setting(USERNAME, 'cj_api_id')
    if not cj_id_check:
        st.warning("⚠️ CJ API 미설정 — 설정 탭 > 택배사 설정에서 CJ ID/PW/고객번호를 먼저 입력하세요.")

    task2_en = get_setting(USERNAME, 'auto_shipping_enabled') == '1'
    task2_time_str = get_setting(USERNAME, 'auto_shipping_time') or '14:00'
    t2h, t2m = [int(x) for x in task2_time_str.split(':')]

    c1, c2 = st.columns([1, 2])
    new_t2_en = c1.checkbox("활성화", value=task2_en, key="t2_en")
    new_t2_time = c2.time_input("실행 시간", value=dtime(t2h, t2m), key="t2_time")

    col_s2, col_d2, col_run2 = st.columns(3)
    if col_s2.button("💾 Task 2 저장 & 등록", key="save_t2", type="primary", use_container_width=True):
        t2_str = new_t2_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_shipping_enabled', '1' if new_t2_en else '0')
        set_setting(USERNAME, 'auto_shipping_time', t2_str)
        if new_t2_en:
            ok, out = _register_task(TASK2_NAME, "shipping", t2_str, USERNAME)
            if ok:
                st.success(f"✅ Task 2 등록 완료 — 매일 {t2_str} 자동 실행")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
        else:
            _delete_task(TASK2_NAME)
            st.info("Task 2 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if col_d2.button("🗑 Task 2 삭제", key="del_t2", use_container_width=True):
        ok, out = _delete_task(TASK2_NAME)
        set_setting(USERNAME, 'auto_shipping_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if col_run2.button("▶ 지금 테스트 실행", key="run_t2", use_container_width=True):
        with st.spinner("Task 2 실행 중 (CJ 접수 + 발송처리)..."):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "shipping", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=180
            )
        output = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            st.success("✅ 실행 완료")
        else:
            st.error("❌ 실행 중 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── Task 3: 정기 크롤링 (admin 전용) ──
    if IS_ADMIN:
        st.subheader("🕐 Task 3 — 코스트코 정기 크롤링")
        st.caption("매일 지정 시간에 코스트코 상품을 자동 크롤링하여 공유 제품 DB를 최신 상태로 유지합니다.")

        _CRAWL_PRESETS = {
            "🔄 정기갱신": ["신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품"],
            "🔥 핫딜시즌": ["스페셜할인", "커클랜드", "신상품"],
            "🆕 새상품탐색": ["신상품", "스페셜할인"],
            "🏗️ 전체카테고리": ["식품", "신선식품", "냉동식품", "과자/간식", "커피/음료",
                                "가공식품", "생활용품", "세제/청소", "화장지", "가전/디지털",
                                "주방가전", "뷰티/화장품", "건강/영양제", "의류/패션",
                                "완구", "반려동물", "자동차용품"],
        }

        task3_en = get_setting(USERNAME, 'auto_crawl_enabled') == '1'
        task3_time_str = get_setting(USERNAME, 'auto_crawl_time') or '06:00'
        t3h, t3m = [int(x) for x in task3_time_str.split(':')]
        _saved_cats_json = get_setting(USERNAME, 'auto_crawl_categories') or '[]'
        try:
            _saved_cats = json.loads(_saved_cats_json)
        except Exception:
            _saved_cats = []
        _saved_max = int(get_setting(USERNAME, 'auto_crawl_max') or 200)

        cr1, cr2 = st.columns([1, 2])
        new_t3_en   = cr1.checkbox("활성화", value=task3_en, key="t3_en")
        new_t3_time = cr2.time_input("실행 시간", value=dtime(t3h, t3m), key="t3_time")

        st.markdown("**크롤링 카테고리 선택**")
        _preset_cols = st.columns(4)
        for _pi, (_plabel, _pcats) in enumerate(_CRAWL_PRESETS.items()):
            if _preset_cols[_pi].button(_plabel, key=f"t3_preset_{_pi}", use_container_width=True):
                _saved_cats = list(set(_saved_cats) | set(_pcats))

        from costco_crawler import CATEGORIES as _ALL_CATS
        _cat_names = [c for c in _ALL_CATS if c not in ("전체",)]
        _sel_cats = st.multiselect("크롤링 대상 카테고리",
                                   options=_cat_names,
                                   default=[c for c in _saved_cats if c in _cat_names],
                                   key="t3_cats")
        _new_max = st.number_input("카테고리당 최대 수집 수", value=_saved_max,
                                   min_value=50, max_value=500, step=50, key="t3_max")

        col_s3, col_d3, col_run3 = st.columns(3)
        if col_s3.button("💾 Task 3 저장 & 등록", key="save_t3", type="primary", use_container_width=True):
            t3_str = new_t3_time.strftime("%H:%M")
            set_setting(USERNAME, 'auto_crawl_enabled', '1' if new_t3_en else '0')
            set_setting(USERNAME, 'auto_crawl_time', t3_str)
            set_setting(USERNAME, 'auto_crawl_categories', json.dumps(_sel_cats, ensure_ascii=False))
            set_setting(USERNAME, 'auto_crawl_max', str(int(_new_max)))
            if new_t3_en:
                _cmd3 = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task crawl --user {USERNAME}'
                ok, out = _schtasks_run(["/create", "/tn", TASK3_NAME, "/tr", _cmd3,
                                         "/sc", "daily", "/st", t3_str, "/f"])
                if ok:
                    st.success(f"✅ Task 3 등록 완료 — 매일 {t3_str} 자동 크롤링")
                else:
                    st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
            else:
                _schtasks_run(["/delete", "/tn", TASK3_NAME, "/f"])
                st.info("Task 3 비활성화 — 스케줄 삭제됨")
            st.rerun()

        if col_d3.button("🗑 Task 3 삭제", key="del_t3", use_container_width=True):
            ok, out = _schtasks_run(["/delete", "/tn", TASK3_NAME, "/f"])
            set_setting(USERNAME, 'auto_crawl_enabled', '0')
            st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
            st.rerun()

        if col_run3.button("▶ 지금 테스트 실행", key="run_t3", use_container_width=True):
            if not _sel_cats:
                st.warning("카테고리를 선택하세요.")
            else:
                set_setting(USERNAME, 'auto_crawl_categories',
                            json.dumps(_sel_cats, ensure_ascii=False))
                with st.spinner(f"크롤링 실행 중 ({len(_sel_cats)}개 카테고리)... 수 분 소요"):
                    r = subprocess.run(
                        [PYTHON_PATH, SCRIPT_PATH, "--task", "crawl", "--user", USERNAME],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=600
                    )
                output = (r.stdout + r.stderr).strip()
                if r.returncode == 0:
                    st.success("✅ 크롤링 완료")
                else:
                    st.error("❌ 크롤링 오류")
                st.code(output, language=None)

        st.divider()

    # ── 실행 로그 ──
    st.subheader("📄 자동화 실행 로그")
    LOG_PATH = os.path.join(DATA_DIR, "auto_task.log")
    col_log1, col_log2 = st.columns([3, 1])
    log_lines = 50
    with col_log1:
        log_lines = st.slider("최근 줄 수", min_value=20, max_value=200, value=50, step=10, key="log_lines")
    with col_log2:
        st.write("")
        st.write("")
        if st.button("🗑 로그 초기화", key="clear_log"):
            open(LOG_PATH, "w", encoding="utf-8").close()
            st.rerun()

    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        recent = "".join(all_lines[-log_lines:]) if all_lines else "(로그 없음)"
        st.code(recent, language=None)
    else:
        st.info("아직 실행 로그가 없습니다.")

    st.divider()

    # ── 서버 관리 (네트워크 서버 모드) ──
    st.subheader("🖥️ 서버 관리")
    st.caption("이 PC를 Streamlit 네트워크 서버로 운영할 때 사용하는 설정입니다.")

    # 현재 서버 IP 목록 조회
    def _get_local_ips():
        try:
            r = subprocess.run(
                ["ipconfig"],
                capture_output=True, text=True, encoding="cp949", errors="replace"
            )
            ips = re.findall(r"IPv4.*?:\s*([\d.]+)", r.stdout)
            return [ip for ip in ips if not ip.startswith("127.")]
        except Exception:
            return []

    local_ips = _get_local_ips()

    with st.expander("📡 현재 서버 접속 주소", expanded=True):
        if local_ips:
            for ip in local_ips:
                st.markdown(f"**내부 네트워크:** `http://{ip}:8501`")
        else:
            st.info("IP 주소를 가져올 수 없습니다.")
        st.markdown("**이 컴퓨터:** `http://localhost:8501`")
        st.caption("외부(인터넷) 접속은 공유기 포트포워딩 + DDNS 설정이 필요합니다.")

    with st.expander("⚙️ 부팅 자동시작 설정 방법", expanded=False):
        st.markdown("""
**1단계: 서버 부팅 자동시작 등록**
```
setup_server_boot.bat  →  관리자 권한으로 실행
```
- Windows 작업 스케줄러에 로그인 시 자동 서버 시작 등록
- 방화벽 포트 8501 자동 개방

**2단계: 공유기 포트포워딩**
- 공유기 관리 페이지 접속 (보통 192.168.0.1 또는 192.168.1.1)
- 포트포워딩 메뉴 → 외부포트 **8501** → 내부 IP:{port} **8501** 추가

**3단계: DDNS 설정 (고정 도메인)**
- https://www.duckdns.org 접속 → 무료 도메인 등록
- `yourname.duckdns.org` 형태로 외부에서 접속 가능
- 30분마다 IP 업데이트 자동화 스크립트 실행

**4단계 이후 접속 주소**
```
http://yourname.duckdns.org:8501
```
""")

    c_start, c_stop = st.columns(2)
    if c_start.button("▶ 서버 시작 (start_server.bat)", key="btn_start_server", use_container_width=True):
        server_bat = os.path.join(BASE_DIR, "start_server.bat")
        if os.path.exists(server_bat):
            subprocess.Popen(["cmd", "/c", "start", "", server_bat], cwd=BASE_DIR)
            st.success("서버 시작 명령을 보냈습니다. 새 창이 열립니다.")
        else:
            st.error("start_server.bat 파일을 찾을 수 없습니다.")

    if c_stop.button("⏹ 서버 중지 (stop_server.bat)", key="btn_stop_server", use_container_width=True):
        stop_bat = os.path.join(BASE_DIR, "stop_server.bat")
        if os.path.exists(stop_bat):
            subprocess.Popen(["cmd", "/c", "start", "", stop_bat], cwd=BASE_DIR)
            st.warning("서버 중지 명령을 보냈습니다.")
        else:
            st.error("stop_server.bat 파일을 찾을 수 없습니다.")

    st.divider()

    # ── 코스트코 크롤링 ───────────────────────────────────────────
    st.divider()
    st.subheader("🔍 코스트코 쇼핑몰 크롤링")
    st.caption("수집 결과는 공유 제품 DB에 가격구분='온라인'으로 저장됩니다.")

    try:
        import costco_crawler as _cc
        _crawler_ok = True
    except ImportError:
        _crawler_ok = False

    if not _crawler_ok:
        st.error("costco_crawler.py 파일을 찾을 수 없습니다.")
    else:
        # ── 코스트코 계정 설정 ────────────────────────────────
        with st.expander("🔑 코스트코 계정 설정", expanded=not _cc.is_profile_exists()):
            st.caption("크롤링 시 로그인에 사용됩니다. 앱 서버(이 PC)에만 저장됩니다.")
            saved_email = get_global_setting('costco_email', '')
            saved_pw    = get_global_setting('costco_password', '')

            cx1, cx2 = st.columns(2)
            c_email = cx1.text_input(
                "코스트코 이메일",
                value=saved_email,
                placeholder="example@email.com",
                key="costco_email_input",
            )
            c_pw = cx2.text_input(
                "비밀번호",
                value=saved_pw,
                type="password",
                key="costco_pw_input",
            )
            cs1, cs2 = st.columns(2)
            if cs1.button("💾 계정 저장", key="save_costco_cred", use_container_width=True):
                set_global_setting('costco_email',    c_email.strip())
                set_global_setting('costco_password', c_pw.strip())
                st.success("✅ 계정 저장 완료!")
                st.rerun()

            profile_exists = _cc.is_profile_exists()
            if profile_exists:
                cs2.success("✅ 브라우저 프로필 저장됨")
            else:
                cs2.warning("⚠️ 첫 로그인 설정 필요")

            st.divider()
            st.markdown("**첫 로그인 설정** — OTP 포함 최초 1회만 필요")
            st.caption(
                "버튼 클릭 시 브라우저가 열립니다. "
                "코스트코에 로그인하고 OTP 인증을 완료하면 자동으로 저장됩니다."
            )
            if st.button(
                "🌐 브라우저 열어서 코스트코 첫 로그인",
                key="btn_setup_profile",
                use_container_width=True,
                type="primary" if not profile_exists else "secondary",
            ):
                # playwright 설치 여부 먼저 확인
                try:
                    import playwright as _pw_check
                    _pw_installed = True
                except ImportError:
                    _pw_installed = False

                if not _pw_installed:
                    st.error(
                        "playwright가 설치되지 않았습니다.\n"
                        "터미널에서 실행:\n"
                        "pip install playwright\n"
                        "python -m playwright install chromium"
                    )
                else:
                    _setup_email = get_global_setting('costco_email', '')
                    _setup_pw    = get_global_setting('costco_password', '')
                    _script = os.path.join(BASE_DIR, "costco_crawler.py")
                    try:
                        # Windows: CREATE_NEW_CONSOLE — 새 콘솔 창에서 실행
                        subprocess.Popen(
                            [sys.executable, _script, "--setup-auto",
                             _setup_email, _setup_pw],
                            cwd=BASE_DIR,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                        )
                        st.success(
                            "✅ 새 창이 열립니다!\n\n"
                            "1. 열린 브라우저에서 코스트코 이메일 / 비밀번호 입력\n"
                            "2. OTP 인증 완료\n"
                            "3. 로그인 완료 후 콘솔 창이 자동으로 닫힙니다\n"
                            "4. 이 페이지를 새로고침(F5)하면 상태가 업데이트됩니다."
                        )
                    except Exception as _e:
                        st.error(f"실행 오류: {_e}")

        # ── 크롤링 실행 ───────────────────────────────────────
        profile_ok = _cc.is_profile_exists()
        _c_email   = get_global_setting('costco_email', '')
        _c_pw      = get_global_setting('costco_password', '')

        if not profile_ok:
            st.warning("위 '코스트코 계정 설정'에서 첫 로그인을 먼저 완료해주세요.")
        else:
            crawl_tab1, crawl_tab2 = st.tabs(["카테고리 크롤링", "키워드 검색"])

            with crawl_tab1:
                # ── 빠른 선택 프리셋 ──
                PRESETS = {
                    "🏗️ 최초구축": ["식품", "신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품",
                                     "생활용품", "세제/청소", "화장지", "가전/디지털", "주방가전",
                                     "뷰티/화장품", "건강/영양제", "의류/패션", "완구", "반려동물", "자동차용품"],
                    "🔄 정기갱신": ["신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품"],
                    "🔥 핫딜시즌": ["스페셜할인", "커클랜드", "신상품"],
                    "🆕 새상품탐색": ["신상품", "스페셜할인"],
                }
                st.markdown("**빠른 선택**")
                p_cols = st.columns(4)
                for pi, (label, cats) in enumerate(PRESETS.items()):
                    if p_cols[pi].button(label, key=f"preset_{pi}", use_container_width=True):
                        for c in cats:
                            st.session_state[f"cat_{c}"] = True

                st.markdown("**수집할 카테고리 선택**")
                cat_names = list(_cc.CATEGORIES.keys())
                cat_cols = st.columns(3)
                sel_cats = []
                for i, cat in enumerate(cat_names):
                    if cat_cols[i % 3].checkbox(cat, key=f"cat_{cat}"):
                        sel_cats.append(cat)

                max_cat = st.number_input(
                    "카테고리당 최대 수집 수", min_value=10, max_value=1000,
                    value=300, step=10, key="crawl_max_cat"
                )
                if st.button(
                    f"🚀 카테고리 크롤링 시작 ({len(sel_cats)}개 선택)",
                    type="primary", key="btn_crawl_cat",
                    disabled=len(sel_cats) == 0,
                    use_container_width=True,
                ):
                    targets = [{"type": "category", "name": c} for c in sel_cats]
                    progress_box = st.empty()
                    log_lines = []

                    def _cb_cat(msg):
                        log_lines.append(msg)
                        progress_box.code("\n".join(log_lines[-20:]))

                    _crawl_ok = False
                    with st.spinner("크롤링 중... (수 분 소요될 수 있습니다)"):
                        try:
                            result = _cc.run_crawl(
                                targets=targets,
                                email=_c_email,
                                password=_c_pw,
                                max_products=int(max_cat),
                                progress_cb=_cb_cat,
                                updated_by='crawler',
                            )
                            if result["errors"]:
                                st.warning("오류:\n" + "\n".join(result["errors"]))
                            st.session_state['last_crawl_result'] = result
                            _crawl_ok = True
                        except RuntimeError as e:
                            st.error(f"❌ {e}")
                    if _crawl_ok:
                        r = st.session_state['last_crawl_result']
                        st.success(
                            f"✅ 크롤링 완료!\n\n"
                            f"수집 **{r['total_crawled']}**개  →  "
                            f"신규 **{r['new']}**개 / 업데이트 **{r['updated']}**개"
                        )
                        st.balloons()
                        if st.button("📦 결과 보기 (제품 DB)", type="primary",
                                     key="go_db_cat", use_container_width=True):
                            st.session_state['main_tab'] = "📦 제품 DB"
                            st.rerun()

            with crawl_tab2:
                kw_input = st.text_input(
                    "검색 키워드 (쉼표로 여러 개 입력 가능)",
                    placeholder="예: 그릭요거트, 올리브오일, 커클랜드",
                    key="crawl_kw_input",
                )
                max_kw = st.number_input(
                    "키워드당 최대 수집 수", min_value=10, max_value=500,
                    value=100, step=10, key="crawl_max_kw"
                )
                if st.button(
                    "🔍 키워드 크롤링 시작",
                    type="primary", key="btn_crawl_kw",
                    disabled=not kw_input.strip(),
                    use_container_width=True,
                ):
                    keywords = [k.strip() for k in kw_input.split(",") if k.strip()]
                    targets = [{"type": "keyword", "keyword": k} for k in keywords]
                    progress_box2 = st.empty()
                    log_lines2 = []

                    def _cb_kw(msg):
                        log_lines2.append(msg)
                        progress_box2.code("\n".join(log_lines2[-20:]))

                    _crawl_ok2 = False
                    with st.spinner("크롤링 중..."):
                        try:
                            result2 = _cc.run_crawl(
                                targets=targets,
                                email=_c_email,
                                password=_c_pw,
                                max_products=int(max_kw),
                                progress_cb=_cb_kw,
                                updated_by='crawler',
                            )
                            if result2["errors"]:
                                st.warning("오류:\n" + "\n".join(result2["errors"]))
                            st.session_state['last_crawl_result'] = result2
                            _crawl_ok2 = True
                        except RuntimeError as e:
                            st.error(f"❌ {e}")
                    if _crawl_ok2:
                        r2 = st.session_state['last_crawl_result']
                        st.success(
                            f"✅ 크롤링 완료!\n\n"
                            f"수집 **{r2['total_crawled']}**개  →  "
                            f"신규 **{r2['new']}**개 / 업데이트 **{r2['updated']}**개"
                        )
                        st.balloons()
                        if st.button("📦 결과 보기 (제품 DB)", type="primary",
                                     key="go_db_kw", use_container_width=True):
                            st.session_state['main_tab'] = "📦 제품 DB"
                            st.rerun()

        # 온라인 수집 제품 현황
        online_prods = [p for p in get_shared_products() if p.get('price_type') == '온라인']
        if online_prods:
            st.divider()
            st.markdown(f"**🌐 온라인 수집 제품: {len(online_prods)}개**")
            preview_df = pd.DataFrame([{
                "상품번호": p.get("product_no", ""),
                "상품명":  p.get("costco_name", ""),
                "가격(원)": f"{int(p.get('unit_price', 0)):,}",
                "업데이트": (p.get("updated_at") or "")[:10],
            } for p in online_prods[:50]])
            st.dataframe(preview_df, use_container_width=True, height=300)
            if len(online_prods) > 50:
                st.caption(f"상위 50개만 표시 (전체 {len(online_prods)}개)")
