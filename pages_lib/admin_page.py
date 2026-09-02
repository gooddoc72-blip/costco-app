"""👑 관리자 페이지 — pages_lib 자동 추출."""
import os
import io
import sys
import json
import math
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
    submit_shopping_list, delete_shopping_submission,
    get_shopping_submissions_detail, get_shopping_submission_dates,
    normalize_shopping_items,
    upsert_shared_naver_map, get_shared_naver_costco_map,
    AUTH_DB,
)
from services import (
    match_product_to_db, match_shared_product,
    update_product_info_from_orders, update_product_shipping_fees, update_product_sale_price,
    detect_price_changes, build_price_alert_msg,
    parse_costco_receipt_pdf, match_receipt_to_orders,
    match_receipt_to_naver_products, apply_receipt_pno_updates,
    decrypt_excel, read_excel_auto,
    costco_pno_of, is_costco_pno,
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
    """👑 관리자 탭 렌더링."""
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

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
                    # 붙여넣기로 딸려온 앞뒤 공백은 제거한다. 눈에 안 보이는 공백 하나 때문에
                    # 알려준 비밀번호로 로그인이 안 되는 사고가 나기 쉽다.
                    _pw = (reset_pw or "").strip()
                    if not _pw:
                        st.warning("새 비밀번호를 입력한 뒤 버튼을 눌러주세요.")
                    elif change_password(u['username'], _pw):
                        st.success(f"✅ '{u['username']}' 비밀번호 변경 완료! "
                                   f"(로그인 아이디는 대소문자 구분 없이 `{u['username']}`)")
                    else:
                        st.error(f"❌ '{u['username']}' 계정을 찾지 못해 변경하지 못했습니다.")

                # ── 🛒 카페24→네이버 등록 기능 오픈/숨김 (관리자 카탈로그 공용) ──
                st.markdown("<hr style='margin:6px 0'>", unsafe_allow_html=True)
                _cfo_cur = get_setting(u['username'], 'cafe24_register_open') == '1'
                _cfo_new = st.checkbox(
                    "🛒 카페24→네이버 등록 기능 오픈",
                    value=_cfo_cur, key=f"cf24open_{u['username']}",
                    help="켜면 이 사용자가 '네이버 등록' 탭에서 관리자 카페24 카탈로그를 "
                         "자기 스마트스토어에 등록할 수 있습니다. (카페24 자격증명은 공용)")
                if _cfo_new != _cfo_cur:
                    set_setting(u['username'], 'cafe24_register_open', '1' if _cfo_new else '0')
                    st.success("✅ 오픈됨" if _cfo_new else "🙈 숨김 처리됨")
                    st.rerun()

                # ── 등록 한도(누적) — 열어준 사용자가 무한정 올리는 것 방지 ──
                if _cfo_new:
                    from db_cafe24_queue import cafe24_reg_quota
                    _q = cafe24_reg_quota(u['username'])
                    _lc1, _lc2 = st.columns([1, 2])
                    _lim_new = _lc1.number_input(
                        "카페24 등록 한도 (누적, 0=무제한)", min_value=0, max_value=100000,
                        step=10, value=int(_q['limit'] or 0),
                        key=f"cf24lim_{u['username']}",
                        help="이 사용자가 카페24 카탈로그로 등록할 수 있는 누적 상품 수. "
                             "대기열·크론·건별 등록 모두 합산합니다. 0이면 제한 없음.")
                    with _lc2:
                        st.write("")
                        if _q['limit']:
                            st.caption(f"현재 **{_q['used']} / {_q['limit']}**개 사용 "
                                       f"(남은 {_q['remaining']}개)"
                                       + ("  ·  🚫 한도 소진" if _q['blocked'] else ""))
                        else:
                            st.caption(f"현재 누적 등록 **{_q['used']}개** · 한도 없음")
                    if int(_lim_new) != int(_q['limit'] or 0):
                        set_setting(u['username'], 'cafe24_register_limit', str(int(_lim_new)))
                        st.success(f"✅ 한도 {int(_lim_new)}개로 저장"
                                   if _lim_new else "✅ 한도 해제(무제한)")
                        st.rerun()

                # ── 🛒 카페24 전용 메뉴(대행등록·코스트코 매칭·동기화) 오픈/숨김 ──
                _cmo_cur = get_setting(u['username'], 'cafe24_menu_open') == '1'
                _cmo_new = st.checkbox(
                    "🛒 카페24 메뉴(대행등록·동기화) 오픈",
                    value=_cmo_cur, key=f"cf24menu_{u['username']}",
                    help="켜면 이 사용자에게 좌측 '상품 관리 › 카페24' 전용 메뉴가 보입니다 "
                         "(카페24 카탈로그 대행등록 + 코스트코 매칭·동기화). 끄면 즉시 숨김.")
                if _cmo_new != _cmo_cur:
                    set_setting(u['username'], 'cafe24_menu_open', '1' if _cmo_new else '0')
                    st.success("✅ 카페24 메뉴 오픈됨" if _cmo_new else "🙈 카페24 메뉴 숨김 처리됨")
                    st.rerun()

                # ── 📦 고정비용 (택배비·박스비) — 사용자는 수정 불가, 여기서만 설정 ──
                st.markdown("<hr style='margin:6px 0'>", unsafe_allow_html=True)
                st.caption("📦 고정비용 — 이 사용자의 수익계산 기본 택배비/박스비 "
                           "(사용자 설정 화면에서는 읽기 전용)")
                try:
                    _ship_cur = int(get_setting(u['username'], 'shipping_cost') or 1800)
                except (TypeError, ValueError):
                    _ship_cur = 1800
                try:
                    _box_cur = int(get_setting(u['username'], 'box_cost') or 300)
                except (TypeError, ValueError):
                    _box_cur = 300
                _fc1, _fc2, _fc3 = st.columns([1, 1, 1])
                _ship_new = _fc1.number_input("택배비 (원)", value=_ship_cur, step=100,
                                              min_value=0, key=f"adm_ship_{u['username']}")
                _box_new = _fc2.number_input("박스비 (원)", value=_box_cur, step=50,
                                             min_value=0, key=f"adm_box_{u['username']}")
                _fc3.write(""); _fc3.write("")
                if _fc3.button("💾 고정비용 저장", key=f"adm_cost_save_{u['username']}",
                               use_container_width=True):
                    set_setting(u['username'], 'shipping_cost', int(_ship_new))
                    set_setting(u['username'], 'box_cost', int(_box_new))
                    st.success(f"✅ {u['username']}: 택배비 {int(_ship_new):,}원 / "
                               f"박스비 {int(_box_new):,}원 저장")
                    st.rerun()

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

    if st.session_state.get('_shared_cache_dirty', True):
        st.session_state['_shared_cache'] = get_shared_products()
        st.session_state['_shared_cache_dirty'] = False
    shared_all = st.session_state['_shared_cache']
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
                    st.session_state['_shared_cache_dirty'] = True; invalidate_data_cache()
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
                    st.session_state['_shared_cache_dirty'] = True; invalidate_data_cache()
                    st.rerun()
            st.markdown("<hr style='margin:-4px 0 -6px 0;border-color:#f0f0f0'>", unsafe_allow_html=True)

        # 페이지 번호
        if sp_total_pages > 1:
            _s = max(1, sp_page - 4)
            _e = min(sp_total_pages, _s + 8)
            if _e - _s < 8: _s = max(1, _e - 8)
            _page_nums = list(range(_s, _e + 1))

            st.markdown("""<style>
    [data-testid="stVerticalBlock"]:has(.sp-pg-marker) button,
    [data-testid="column"]:has(.sp-pg-marker) button,
    [data-testid="stColumn"]:has(.sp-pg-marker) button {
    all: unset !important;
    cursor: pointer !important;
    color: #555 !important;
    font-size: 14px !important;
    padding: 2px 4px !important;
    line-height: 30px !important;
    white-space: nowrap !important;
    user-select: none !important;
    text-align: center !important;
    box-sizing: border-box !important;
    width: 100% !important;
    }
    [data-testid="stVerticalBlock"]:has(.sp-pg-marker) button:hover,
    [data-testid="column"]:has(.sp-pg-marker) button:hover,
    [data-testid="stColumn"]:has(.sp-pg-marker) button:hover {
    color: #e74c3c !important;
    }
    [data-testid="stVerticalBlock"]:has(.sp-pg-marker) [data-testid="stButton"],
    [data-testid="stVerticalBlock"]:has(.sp-pg-marker) [data-testid="stBaseButton-secondary"] {
    margin: 0 !important; padding: 0 !important;
    }
    [data-testid="stColumns"]:has(.sp-pg-marker),
    [data-testid="stHorizontalBlock"]:has(.sp-pg-marker) {
    gap: 2px !important;
    }
    /* 마커 wrapper를 layout에서 제거 (DOM에는 남기고 공간만 0) */
    [data-testid="stElementContainer"]:has(.sp-pg-marker),
    [data-testid="stMarkdown"]:has(.sp-pg-marker),
    .element-container:has(.sp-pg-marker) {
    position: absolute !important;
    height: 0 !important;
    width: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
    visibility: hidden !important;
    }
    .sp-pg-marker { display: none !important; }
    </style>""", unsafe_allow_html=True)

            _has_prev = sp_page > 1
            _has_next = sp_page < sp_total_pages
            _w = []
            if _has_prev: _w.append(1.5)
            _w += [1] * len(_page_nums)
            if _has_next: _w.append(1.5)
            _w.append(max(20, sum(_w) * 2))
            _pcols = st.columns(_w, vertical_alignment="center")
            _ci = 0
            _marker_done = False

            def _drop_marker(col):
                col.markdown('<div class="sp-pg-marker"></div>', unsafe_allow_html=True)

            if _has_prev:
                with _pcols[_ci]:
                    _drop_marker(_pcols[_ci]); _marker_done = True
                    if st.button('이전', key='sp_prev'):
                        st.session_state['admin_sp_page'] = sp_page - 1
                        st.rerun()
                _ci += 1

            for _p in _page_nums:
                if _p == sp_page:
                    _pcols[_ci].markdown(
                        f'<div style="display:flex;align-items:center;justify-content:center;height:34px">'
                        f'<span style="border:1.5px solid #e74c3c;color:#e74c3c;border-radius:3px;'
                        f'padding:1px 6px;font-size:14px;font-weight:600;line-height:22px;'
                        f'min-width:22px;text-align:center">{_p}</span>'
                        f'</div>',
                        unsafe_allow_html=True)
                else:
                    with _pcols[_ci]:
                        if not _marker_done:
                            _drop_marker(_pcols[_ci]); _marker_done = True
                        if st.button(str(_p), key=f'sp_pg_{_p}'):
                            st.session_state['admin_sp_page'] = _p
                            st.rerun()
                _ci += 1

            if _has_next:
                with _pcols[_ci]:
                    if not _marker_done:
                        _drop_marker(_pcols[_ci]); _marker_done = True
                    if st.button('다음 ›', key='sp_next'):
                        st.session_state['admin_sp_page'] = sp_page + 1
                        st.rerun()
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
                st.session_state['_shared_cache_dirty'] = True; invalidate_data_cache()
                st.success(f"✅ '{a_name}' 공유 DB에 추가 완료! ({a_pt}가)")
                st.rerun()
            else:
                st.warning("상품명과 가격을 입력해주세요.")

    # ── 공유 제품 내보내기 / 가져오기 ────────────────────────────────
    st.divider()
    st.subheader("📤 공유 제품 DB 내보내기 / 가져오기")
    st.caption("다른 컴퓨터에 설치된 프로그램과 제품 DB를 동기화할 때 사용합니다.")

    # 개인 DB → 공유 DB 이전
    my_prods = cached_user_products(USERNAME) if callable(cached_user_products) else get_all_products(USERNAME)
    shared_cnt = len(st.session_state.get('_shared_cache', []))
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
            st.session_state['_shared_cache_dirty'] = True; invalidate_data_cache()
            st.success(f"✅ {migrated}개 공유 DB로 이전 완료! (건너뜀: {skipped}개)")
            st.rerun()

    st.divider()
    exp_col, imp_col = st.columns(2)

    with exp_col:
        st.markdown("**📤 내보내기**")
        if st.button("JSON 파일로 내보내기", key="export_shared", use_container_width=True):
            all_sp = cached_shared_products()
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
                st.session_state['_shared_cache_dirty'] = True; invalidate_data_cache()
                st.success(f"✅ {ok_cnt}개 가져오기 완료! (건너뜀: {skip_cnt}개)")
                st.rerun()
            except Exception as e:
                st.error(f"❌ 가져오기 실패: {e}")

    st.divider()
    st.subheader("🛒 사용자별 장보기 목록")
    _today_d = datetime.now().date()

    # ── 조회 기간·사용자 필터 (기본값 = 오늘, 과거 제출건도 상세·엑셀·프린트 가능) ──
    _RANGE_OPTS = ["오늘", "어제", "최근 7일", "최근 30일", "이번 달", "지난 달", "직접 선택"]
    _fc = st.columns([1.2, 1.1, 1.1, 1.4])
    _range_opt = _fc[0].selectbox("기간", _RANGE_OPTS, index=0, key="adm_shop_range")

    if _range_opt == "오늘":
        _d_from = _d_to = _today_d
    elif _range_opt == "어제":
        _d_from = _d_to = _today_d - timedelta(days=1)
    elif _range_opt == "최근 7일":
        _d_from, _d_to = _today_d - timedelta(days=6), _today_d
    elif _range_opt == "최근 30일":
        _d_from, _d_to = _today_d - timedelta(days=29), _today_d
    elif _range_opt == "이번 달":
        _d_from, _d_to = _today_d.replace(day=1), _today_d
    elif _range_opt == "지난 달":
        _last_end = _today_d.replace(day=1) - timedelta(days=1)
        _d_from, _d_to = _last_end.replace(day=1), _last_end
    else:  # 직접 선택
        # date_input은 사용자가 값을 비우면 None을 돌려준다 → 오늘로 폴백
        _d_from = _fc[1].date_input("시작일", value=_today_d, key="adm_shop_from") or _today_d
        _d_to = _fc[2].date_input("종료일", value=_today_d, key="adm_shop_to") or _today_d

    if _range_opt != "직접 선택":
        _fc[1].markdown(f"<div style='padding-top:30px;color:#666;font-size:14px'>"
                        f"{_d_from:%Y-%m-%d} ~ {_d_to:%Y-%m-%d}</div>", unsafe_allow_html=True)

    if _d_from > _d_to:
        _d_from, _d_to = _d_to, _d_from

    _uname_opts = ["전체"] + sorted({u['username'] for u in users})
    _sel_user = _fc[3].selectbox("사용자", _uname_opts, index=0, key="adm_shop_user")

    _subs_sel = get_shopping_submissions_detail(
        _d_from.strftime("%Y-%m-%d"), _d_to.strftime("%Y-%m-%d"),
        username=(None if _sel_user == "전체" else _sel_user),
    )
    _period_label = (f"{_d_from:%Y-%m-%d}" if _d_from == _d_to
                     else f"{_d_from:%Y-%m-%d} ~ {_d_to:%Y-%m-%d}")

    # ── 코스트코 번호 미확보 항목 지정 (공유 매핑에 적재 → 이후 전 사용자 자동 해석) ──
    def _render_pno_fixer(_sub, _items):
        """번호가 비어있는 항목을 공유DB 상품에 연결한다.

        저장처는 shared_naver_map (네이버번호 ↔ 코스트코번호, 전 사용자 공용).
        한 번 지정하면 같은 네이버번호의 주문은 match_shared_product()가
        자동으로 코스트코번호·공유가격으로 해석하므로 다시 물어보지 않는다.
        """
        _miss = [_i for _i in _items
                 if not is_costco_pno(_i.get('코스트코상품번호'))
                 and str(_i.get('네이버상품번호') or '').strip()]
        _old_fmt = [_i for _i in _items
                    if not is_costco_pno(_i.get('코스트코상품번호'))
                    and not str(_i.get('네이버상품번호') or '').strip()]
        if not _miss:
            if _old_fmt:
                st.caption(f"⚠️ 코스트코 번호 없는 항목 {len(_old_fmt)}개 — 네이버 상품번호가 "
                           "함께 저장되지 않은 예전 제출건이라 여기서 지정할 수 없습니다. "
                           "사용자가 다시 발송하면 지정 가능해집니다.")
            else:
                st.caption("✅ 모든 항목에 코스트코 상품번호가 있습니다.")
            return

        _shared = cached_shared_products() or []
        _nmap = get_shared_naver_costco_map() or {}

        with st.expander(f"🔗 코스트코 번호 미확보 {len(_miss)}개 — 공유DB 상품에 연결",
                         expanded=False):
            st.caption("연결하면 **모든 사용자**의 같은 네이버 상품번호 주문이 이후 자동으로 "
                       "코스트코 번호·공유 단가로 해석됩니다. (shared_naver_map에 누적)")
            for _mi, _it in enumerate(_miss):
                _nv = str(_it.get('네이버상품번호') or '').strip()
                _nm = str(_it.get('상품명') or '')
                _key = f"pnofix_{_sub['id']}_{_mi}"

                if _nv in _nmap:
                    st.markdown(f"✅ `{_nv}` → **{_nmap[_nv]}** 로 이미 연결됨 — {_nm[:45]}")
                    continue

                st.markdown(
                    f"**{_nm[:70]}**"
                    f"<div style='color:#888;font-size:13px'>네이버번호 {_nv}</div>",
                    unsafe_allow_html=True)
                _q = st.text_input("공유DB 검색어", value=_nm[:20],
                                   key=f"{_key}_q", label_visibility="collapsed",
                                   placeholder="상품명 일부 또는 코스트코 번호")
                _qs = (_q or _nm).strip()

                # 번호를 그대로 입력하면 정확 일치 우선, 아니면 이름 유사도 상위 10개
                _cands = []
                if _qs.isdigit():
                    _cands = [p for p in _shared if str(p.get('product_no') or '') == _qs]
                if not _cands:
                    _scored = []
                    for p in _shared:
                        _sc = max(_token_score(_qs, p.get('match_keyword') or ''),
                                  _token_score(_qs, p.get('costco_name') or ''))
                        if _sc > 0:
                            _scored.append((_sc, p))
                    _scored.sort(key=lambda x: -x[0])
                    _cands = [p for _sc, p in _scored[:10]]

                if not _cands:
                    st.caption("검색 결과 없음 — 검색어를 바꿔보세요.")
                    st.divider()
                    continue

                _labels = [f"[{p.get('product_no')}] {p.get('costco_name')} "
                           f"({fmt(int(p.get('unit_price') or 0))}원)" for p in _cands]
                _c1, _c2 = st.columns([4, 1])
                _pick = _c1.selectbox("연결할 공유DB 상품", _labels, index=0,
                                      key=f"{_key}_pick", label_visibility="collapsed")
                _c2.write("")
                if _c2.button("🔗 연결", key=f"{_key}_save", use_container_width=True,
                              type="primary"):
                    _sel = _cands[_labels.index(_pick)]
                    _cp = str(_sel.get('product_no') or '')
                    if not is_costco_pno(_cp):
                        st.error(f"공유DB 상품번호가 코스트코 번호 형식(4~7자리)이 아닙니다: {_cp}")
                    elif upsert_shared_naver_map(_cp, _sub['username'],
                                                 naver_pno=_nv, product_name=_nm):
                        st.success(f"✅ {_nv} → {_cp} 연결 완료 "
                                   f"({_sel.get('costco_name')}) — 이후 자동 해석됩니다.")
                        st.rerun()
                    else:
                        st.error("저장 실패 — 번호가 비어있습니다.")
                st.divider()

    def _render_shop_sub(_sub):
        try:
            _items = json.loads(_sub['items_json'])
        except Exception:
            _items = []
        _sub_set = sum(int(_i.get('정산금액') or 0) for _i in _items)
        _sub_amt = int(_sub.get('total_amount') or 0)
        _label = (f"📅 {_sub['order_date']}  |  👤 {_sub['username']}  |  "
                  f"📦 {_sub['total_items']}개 상품  |  💰 구매 {fmt(_sub_amt)}원  "
                  f"|  🧾 정산 {fmt(_sub_set)}원  |  ⏰ {_sub['submitted_at']}")
        with st.expander(_label, expanded=False):
            if not _items:
                st.warning("항목이 비어있습니다.")
                return
            _mc1, _mc2, _mc3 = st.columns(3)
            _mc1.metric("💰 매장금액 합계", f"{fmt(_sub_amt)}원")
            _mc2.metric("🧾 정산금액 합계", f"{fmt(_sub_set)}원")
            _mc3.metric("차액(정산−매장)", f"{fmt(_sub_set - _sub_amt)}원")
            # 팩단가 제외 + 구 스키마('예상금액') 하위호환
            _df_sub = pd.DataFrame(normalize_shopping_items(_items))
            st.dataframe(_df_sub, use_container_width=True, hide_index=True)

            _render_pno_fixer(_sub, _items)

            _xbuf = io.BytesIO()
            try:
                with pd.ExcelWriter(_xbuf, engine='openpyxl') as _xw:
                    _df_sub.to_excel(_xw, index=False, sheet_name='장보기')
                _xbuf.seek(0)
                _dl_data = _xbuf.getvalue()
            except Exception as _xe:
                _dl_data = None
                st.error(f"엑셀 생성 실패: {_xe}")

            # ── 프린트용 HTML (관리자가 각 사용자 장보기 목록을 바로 인쇄) ──
            import html as _html_lib
            import streamlit.components.v1 as _components
            _prows = []
            for _it in _items:
                _pno  = str(_it.get('코스트코상품번호') or '') or '—'
                _nm   = _html_lib.escape(str(_it.get('상품명', '')))
                _opt  = _html_lib.escape(str(_it.get('옵션정보', '') or ''))
                _qty  = int(_it.get('코스트코구매수량') or _it.get('주문수량') or 0)
                _cnt  = int(_it.get('주문건수') or 0)
                _sett = int(_it.get('정산금액') or 0)
                _shp  = int(_it.get('배송비') or 0)
                _prows.append(
                    f'<tr><td>{_pno}</td><td>{_nm}</td><td>{_opt}</td>'
                    f'<td style="text-align:right">{_qty}개({_cnt}건)</td>'
                    f'<td style="text-align:right">{fmt(_sett)}</td>'
                    f'<td style="text-align:right">{fmt(_shp)}</td></tr>'
                )
            _print_html = (
                '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
                f'<title>장보기 {_sub["username"]} {_sub["order_date"]}</title><style>'
                'body{font-family:"맑은 고딕",sans-serif;padding:24px}'
                'h1{font-size:20px;margin:0 0 4px}.meta{color:#666;font-size:13px;margin-bottom:12px}'
                'table{width:100%;border-collapse:collapse;font-size:13px}'
                'th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}'
                'th{background:#f4f4f4;font-weight:600}.tot{margin-top:16px;font-size:15px;font-weight:600}'
                '@media print{body{padding:8px}.noprint{display:none}}'
                '</style></head><body>'
                f'<h1>🛒 장보기 — {_html_lib.escape(str(_sub["username"]))} ({_sub["order_date"]})</h1>'
                f'<div class="meta">총 {len(_items)}종 · 매장금액 {fmt(_sub_amt)}원 · '
                f'정산금액 {fmt(_sub_set)}원 · 제출 {_sub["submitted_at"]}</div>'
                '<table><thead><tr><th>상품번호</th><th>상품명</th><th>옵션</th>'
                '<th style="text-align:right">수량</th><th style="text-align:right">정산금액</th>'
                '<th style="text-align:right">택배비</th></tr></thead><tbody>'
                + ''.join(_prows) +
                f'</tbody></table><div class="tot">💰 매장금액: {fmt(_sub_amt)}원'
                f' &nbsp;·&nbsp; 🧾 정산금액: {fmt(_sub_set)}원</div>'
                '<button class="noprint" onclick="window.print()" '
                'style="margin-top:20px;padding:10px 24px;font-size:14px;cursor:pointer">🖨 인쇄</button>'
                '</body></html>'
            )
            _esc = _html_lib.escape(_print_html, quote=True)

            _dc1, _dc2, _dc3 = st.columns([2, 2, 1])
            if _dl_data:
                _dc1.download_button(
                    "📥 엑셀 다운로드",
                    data=_dl_data,
                    file_name=f"shopping_{_sub['username']}_{_sub['order_date']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_sub_{_sub['id']}",
                    use_container_width=True,
                )
            with _dc2:
                _components.html(
                    f'''<button onclick="(function(){{
                        var f=document.getElementById('pf_{_sub['id']}');
                        if(f&&f.contentWindow){{f.contentWindow.focus();f.contentWindow.print();}}
                    }})()" style="width:100%;padding:7px 0;background:white;
                        border:1px solid rgba(49,51,63,0.2);border-radius:8px;cursor:pointer;
                        font-family:'Source Sans Pro',sans-serif;font-size:14px;color:rgb(49,51,63)"
                      onmouseover="this.style.borderColor='#ff4b4b';this.style.color='#ff4b4b'"
                      onmouseout="this.style.borderColor='rgba(49,51,63,0.2)';this.style.color='rgb(49,51,63)'">
                        🖨 프린트
                    </button>
                    <iframe id="pf_{_sub['id']}" srcdoc="{_esc}" style="display:none"></iframe>''',
                    height=44,
                )
            if _dc3.button("🗑 삭제", key=f"del_sub_{_sub['id']}", use_container_width=True):
                delete_shopping_submission(_sub['id'])
                st.rerun()

    # ── 선택 기간의 제출건 리스트 (기본 = 오늘, 과거 날짜도 조회 가능) ──
    if _subs_sel:
        st.caption(f"📅 {_period_label} 제출 {len(_subs_sel)}건"
                   + ("" if _sel_user == "전체" else f"  ·  👤 {_sel_user}"))

        # ── 사용자별 기간 합계 (금액 = 코스트코 매장금액) ──
        _agg_a = {}
        for _s in _subs_sel:
            _a = _agg_a.setdefault(_s['username'],
                                   {'종수': 0, '건수': 0, '금액': 0, '정산': 0, '제출': ''})
            _a['종수'] += int(_s.get('total_items') or 0)
            _a['금액'] += int(_s.get('total_amount') or 0)
            try:
                for _i in json.loads(_s.get('items_json') or '[]'):
                    _a['건수'] += int(_i.get('주문건수') or 0)
                    _a['정산'] += int(_i.get('정산금액') or 0)
            except Exception:
                pass
            _a['제출'] = max(_a['제출'], str(_s.get('submitted_at') or ''))
        _tot_amt_a = sum(v['금액'] for v in _agg_a.values())
        _tot_set_a = sum(v['정산'] for v in _agg_a.values())
        st.markdown(
            f"**📊 {_period_label} 사용자별 합계** — {len(_agg_a)}명 · "
            f"주문 {sum(v['건수'] for v in _agg_a.values())}건 · "
            f"구매금액 **{fmt(_tot_amt_a)}원** · 정산금액 **{fmt(_tot_set_a)}원** "
            f"· 차액 **{fmt(_tot_set_a - _tot_amt_a)}원**"
        )
        st.dataframe(
            pd.DataFrame([
                {'사용자': _u, '상품 종수': f"{_v['종수']}개", '주문건수': f"{_v['건수']}건",
                 '구매금액(예상)': f"{fmt(_v['금액'])}원",
                 '정산금액': f"{fmt(_v['정산'])}원",
                 '차액(정산−구매)': f"{fmt(_v['정산'] - _v['금액'])}원",
                 '최종 제출': _v['제출']}
                for _u, _v in sorted(_agg_a.items(), key=lambda kv: -kv[1]['금액'])
            ]),
            use_container_width=True, hide_index=True,
        )
        st.caption("💰 매장금액 = 코스트코 계산대 결제 예상액 합계 · "
                   "🧾 정산금액 = 항목별 정산예정금액 합계. "
                   "차액은 택배·박스 원가와 수수료를 뺀 값이 아니므로 순이익이 아닙니다.")

        # ── 날짜별 합계 (기간 조회 시에만) ──
        if _d_from != _d_to:
            _agg_d = {}
            for _s in _subs_sel:
                _dd = _agg_d.setdefault(_s['order_date'],
                                        {'사용자': set(), '종수': 0, '건수': 0, '금액': 0, '정산': 0})
                _dd['사용자'].add(_s['username'])
                _dd['종수'] += int(_s.get('total_items') or 0)
                _dd['금액'] += int(_s.get('total_amount') or 0)
                try:
                    for _i in json.loads(_s.get('items_json') or '[]'):
                        _dd['건수'] += int(_i.get('주문건수') or 0)
                        _dd['정산'] += int(_i.get('정산금액') or 0)
                except Exception:
                    pass
            st.markdown("**📅 날짜별 합계**")
            st.dataframe(
                pd.DataFrame([
                    {'날짜': _k, '사용자': f"{len(_v['사용자'])}명",
                     '상품 종수': f"{_v['종수']}개", '주문건수': f"{_v['건수']}건",
                     '구매금액(예상)': f"{fmt(_v['금액'])}원",
                     '정산금액': f"{fmt(_v['정산'])}원",
                     '차액(정산−구매)': f"{fmt(_v['정산'] - _v['금액'])}원"}
                    for _k, _v in sorted(_agg_d.items(), reverse=True)
                ]),
                use_container_width=True, hide_index=True,
            )

        # ── 기간 전체 엑셀 (사용자×날짜 상세 시트 통합) ──
        try:
            _allbuf = io.BytesIO()
            _all_rows = []
            for _s in _subs_sel:
                try:
                    _its = json.loads(_s.get('items_json') or '[]')
                except Exception:
                    _its = []
                for _r in normalize_shopping_items(_its):
                    _all_rows.append({'날짜': _s['order_date'], '사용자': _s['username'], **_r})
            if _all_rows:
                with pd.ExcelWriter(_allbuf, engine='openpyxl') as _xw:
                    pd.DataFrame(_all_rows).to_excel(_xw, index=False, sheet_name='장보기전체')
                _allbuf.seek(0)
                st.download_button(
                    f"📥 기간 전체 엑셀 다운로드 ({len(_all_rows)}행)",
                    data=_allbuf.getvalue(),
                    file_name=(f"shopping_{_d_from:%Y%m%d}_{_d_to:%Y%m%d}"
                               f"{'' if _sel_user == '전체' else '_' + _sel_user}.xlsx"),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_shop_range_all",
                )
        except Exception as _xe:
            st.caption(f"기간 전체 엑셀 생성 실패: {_xe}")

        st.divider()

        for _sub in _subs_sel:
            _render_shop_sub(_sub)
    else:
        st.caption(f"{_period_label} 기간에 제출된 장보기 목록이 없습니다. "
                   "(사용자가 주문 수집 시 자동 발송되거나 '📋 장보기 목록 관리자에게 발송' 클릭 시 표시됨)")
        try:
            _avail = get_shopping_submission_dates(limit=14)
        except Exception:
            _avail = []
        if _avail:
            st.caption("🗓 최근 제출 이력이 있는 날짜: " + ", ".join(_avail))

    # ── 로컬 설치형 라이선스 관리 (1-PC 사용 인증) ──
    st.divider()
    st.subheader("🔑 로컬 설치형 라이선스 (1-PC 사용 인증)")
    st.caption("로컬 설치판 사용자에게 발급하는 라이선스키입니다. 1키 = 1PC (최초 실행 PC에 자동 바인딩). "
               "활성화 시 **계정 사용자명으로 자동 로그인**됩니다 (회원가입/비번 불필요).")
    try:
        import re as _re_lic
        from db_license import (create_license, list_licenses, revoke_license,
                                unbind_license, delete_license)
        _lic_c1, _lic_c2, _lic_c3 = st.columns([1.4, 1.4, 1])
        _lic_uid = _lic_c1.text_input("계정 사용자명(영문/숫자)", key="lic_new_uid",
                                      placeholder="예: theblueshop")
        _lic_disp = _lic_c2.text_input("표시이름(가게명, 선택)", key="lic_new_disp",
                                       placeholder="예: 더블루샵")
        _lic_c3.write("")
        if _lic_c3.button("🔑 발급", key="lic_issue", type="primary", use_container_width=True):
            _uid = _re_lic.sub(r'[^A-Za-z0-9_\-]', '', (_lic_uid or '').strip())
            if not _uid:
                st.error("계정 사용자명을 영문/숫자로 입력하세요. (DB 폴더명으로 사용)")
            else:
                _newk = create_license(username=_uid, memo=(_lic_disp.strip() or _uid))
                st.success(f"✅ 발급 완료: {_newk}  (계정: {_uid})")
                st.rerun()

        _lics = list_licenses(limit=200)
        if not _lics:
            st.caption("발급된 라이선스가 없습니다.")
        else:
            st.caption(f"총 {len(_lics)}개")
            for _l in _lics:
                _bound = (_l.get('bound_machine_id') or '').strip()
                _badge = ("🟢 활성" if _l['status'] == 'active' else "🔴 정지")
                _pcst = (f"💻 PC바인딩됨" if _bound else "⚪ 미사용(미바인딩)")
                with st.expander(f"{_badge} | 🔑 {_l['license_key']} | 👤 {_l.get('username','')} | {_pcst}",
                                 expanded=False):
                    st.write({
                        "키": _l['license_key'], "대상": _l.get('username', ''),
                        "상태": _l['status'], "바인딩 PC": _bound or '(없음)',
                        "발급": _l.get('created_at', ''), "활성화": _l.get('activated_at', ''),
                        "최근접속": _l.get('last_seen_at', ''),
                    })
                    _b1, _b2, _b3 = st.columns(3)
                    if _l['status'] == 'active':
                        if _b1.button("⏸ 정지", key=f"lic_rev_{_l['id']}", use_container_width=True):
                            revoke_license(_l['license_key'], True); st.rerun()
                    else:
                        if _b1.button("▶ 재활성", key=f"lic_act_{_l['id']}", use_container_width=True):
                            revoke_license(_l['license_key'], False); st.rerun()
                    if _b2.button("🔄 PC 해제(재바인딩 허용)", key=f"lic_unbind_{_l['id']}",
                                  use_container_width=True, help="PC 교체 시 → 다음 실행 PC에 다시 바인딩"):
                        unbind_license(_l['license_key']); st.rerun()
                    if _b3.button("🗑 삭제", key=f"lic_del_{_l['id']}", use_container_width=True):
                        delete_license(_l['license_key']); st.rerun()
    except Exception as _le:
        st.error(f"라이선스 관리 로드 오류: {_le}")
