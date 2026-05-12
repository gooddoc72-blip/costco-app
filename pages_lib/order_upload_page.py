"""📋 주문 업로드 페이지 — pages_lib 자동 추출."""
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
    get_active_orders, db_rows_to_orders_df, active_orders_to_naver_excel_df,
    save_receipt_items, get_recent_receipt_items, delete_receipt_items_by_date, get_receipt_dates,
    get_date_range_stats, get_monthly_stats, get_product_ranking, get_saved_dates,
    get_dashboard_kpi, get_daily_profit_trend, get_week_best_products,
    get_price_history_monthly, save_price_changes_to_history, get_price_change_history,
    add_keyword_tracking, get_keyword_trackings, delete_keyword_tracking,
    save_rank_result, get_rank_history, get_latest_ranks,
    get_daily_ranks_in_month, get_yearly_rank_history, delete_trackings_bulk,
    get_rank_drops,
    submit_shopping_list,
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

try:
    import coupang_api
    HAS_COUPANG_API = True
except ImportError:
    HAS_COUPANG_API = False
    coupang_api = None

# 네이버 엑셀 / API 공통 컬럼 (save_daily_orders 입력 스펙)
EXTRACT_COLS = [
    '수취인명', '상품명', '옵션정보', '수량',
    '최종 상품별 총 주문금액', '배송비 합계',
    '제주/도서 추가배송비', '정산예정금액',
    '결제일',  # save_daily_orders가 행별 실제 결제일자로 분산 저장하기 위함
]

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
    """📋 주문 업로드 탭 렌더링."""
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    st.header("📋 일일 주문 수집")
    st.caption("주문을 가져온 뒤 검토하고 **💾 저장** 버튼을 눌러야 수익계산에 반영됩니다.")

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
        # 마지막 동기화 시점 → 증분 hours_back 자동 계산. 기본 48시간이면 충분.
        # 안전 마진 24h: last-changed-statuses API가 상태 변경 안 된 주문은 누락하므로
        # 마지막 sync 시점 ± 24h 범위까지 재조회하여 누락 방지 (병렬 호출이라 속도 영향 미미)
        from datetime import datetime as _dt
        _last_iso = _gs('last_order_sync')
        if not _last_iso:
            hours = 48  # 첫 조회: 48시간
        else:
            try:
                _delta = (_dt.now() - _dt.fromisoformat(_last_iso)).total_seconds() / 3600
                hours = max(48, int(_delta) + 24)  # 마지막 sync 이후 + 24h 안전 마진, 최소 48h
            except Exception:
                hours = 48
        if fetch_btn:
            all_orders = []
            types_to_query = ["READY", "PAYED"] if status_type == "ALL" else [status_type]

            with st.spinner(f"네이버 커머스 API 조회 중... ({hours}시간 범위)"):
                for st_type in types_to_query:
                    orders, err = naver_api.get_new_orders(api_id, api_secret, hours_back=hours, status_type=st_type)
                    if orders:
                        all_orders.extend(orders)
                    elif err:
                        if err.startswith("DEBUG_RESP:"):
                            st.caption(f"🔍 API 응답: {err[11:]}")
                        else:
                            st.warning(f"{st_type} 조회: {err}")

            # ── 1. API raw 주문 → DB에 UPSERT (status 누적 갱신용) ──
            api_count = 0
            fetched_df = None
            if all_orders:
                fetched_df = pd.DataFrame(all_orders).drop_duplicates(subset=['상품주문번호'], keep='last')
                api_count = len(fetched_df)
                save_order_history(USERNAME, fetched_df)

            # 동기화 시점 기록 → 다음 호출 시 증분 윈도우 계산
            try:
                set_setting(USERNAME, 'last_order_sync', _dt.now().isoformat())
            except Exception:
                pass

            # ── 1-B. raw_json 없는 옛 주문 보완 (주소/연락처 등 복원) ──
            _no_rj_ids = [r['order_no'] for r in get_active_orders(USERNAME) if not r.get('raw_json')]
            if _no_rj_ids:
                with st.spinner(f"기존 주문 데이터 보완 중... ({len(_no_rj_ids)}건)"):
                    _detail_rows, _ = naver_api.fetch_order_details_by_ids(api_id, api_secret, _no_rj_ids)
                if _detail_rows:
                    save_order_history(USERNAME, pd.DataFrame(_detail_rows))

            # ── 2. DB에서 미발송 주문(active)만 추려 화면용 df 구성 ──
            active_rows = get_active_orders(USERNAME)
            if not active_rows:
                st.info(f"미발송 주문이 없습니다. (API 수집 {api_count}건)")
            else:
                df = db_rows_to_orders_df(active_rows)
                for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
                df = df.sort_values('상품명').reset_index(drop=True)

                # 구입가격 재계산 (DB 1회 로드)
                _all_shared = get_shared_products()
                _all_user   = get_all_products(USERNAME)
                costs = []
                for _, r in df.iterrows():
                    p_no = str(r.get('상품번호', '')) if r.get('상품번호') else ''
                    p = match_product_to_db(USERNAME, r['상품명'], product_no=p_no,
                                            _user_prods=_all_user, _shared_prods=_all_shared)
                    if p:
                        _sq = max(1, int(p.get('split_qty', 1) or 1))
                        costs.append((p['unit_price'] // _sq) * int(r['수량']))
                    else:
                        costs.append(0)
                    if p_no and p:
                        upsert_product(USERNAME, p['costco_name'], p['match_keyword'], p['unit_price'], product_no=p_no)
                df['구입가격'] = costs

                # 화면용 df 저장
                st.session_state['orders'] = df
                st.session_state['order_date'] = datetime.today().strftime("%Y-%m-%d")

                # ── 송장등록/Excel 다운로드용: DB의 raw_json에서 모든 active 주문 복원 (72컬럼) ──
                _excel_df = active_orders_to_naver_excel_df(USERNAME)
                if _excel_df is not None and not _excel_df.empty:
                    st.session_state['order_full'] = _excel_df
                else:
                    # raw_json이 아직 없는 옛 데이터만 있는 경우 → DB 변환 df라도 사용
                    st.session_state['order_full'] = df.copy()
                # Excel bytes는 렌더 시 lazy 생성
                st.session_state['order_excel_bytes'] = None

                # 저장은 사용자가 명시적으로 저장 버튼을 눌러야 함
                st.session_state['orders_unsaved'] = True

                # status 분포 디버그
                try:
                    _dist = naver_api.get_last_status_dist()
                    if _dist:
                        _dist_str = ", ".join(f"{k}={v}" for k, v in sorted(_dist.items(), key=lambda x: -x[1]))
                        st.session_state['_naver_status_dist'] = _dist_str
                except Exception:
                    pass

            # ── 3. 매입가 계산만 수행 (수익계산 저장은 사용자가 명시적으로) ──
            if fetched_df is not None and not fetched_df.empty:
                _s_cost = int(_gs('shipping_cost') or 1800)
                _b_cost = int(_gs('box_cost') or 300)
                try:
                    from services import process_and_save_orders
                    _r = process_and_save_orders(
                        USERNAME, fetched_df,
                        datetime.today().strftime("%Y-%m-%d"),
                        _s_cost, _b_cost,
                        save_history=False,    # 위에서 이미 save_order_history 호출됨
                        save_daily=False,      # 사용자가 저장 버튼 누를 때만 daily_orders 저장
                    )
                    if _r.get('error_orders'):
                        st.error(f"매입가 계산 일부 실패: {_r['error_orders']}")
                except Exception as _se:
                    st.error(f"매입가 계산 실패: {_se}")

            st.success(f"✅ API 수집 {api_count}건 / DB 미발송 {len(df)}건 표시 — 💾 저장 버튼을 눌러 수익계산에 반영하세요")
            st.rerun()
        st.divider()
    elif not HAS_NAVER_API:
        st.caption("💡 naver_api.py 파일과 bcrypt, pybase64 패키지를 설치하면 API 자동 조회를 사용할 수 있습니다.")
    elif not api_id:
        st.caption("💡 설정에서 네이버 API 키를 등록하면 자동 주문 조회를 사용할 수 있습니다.")

    # ── 쿠팡 주문 자동 조회 ─────────────────────────────────
    cq_access = _gs('coupang_access_key')
    cq_secret = _gs('coupang_secret_key')
    cq_vendor = _gs('coupang_vendor_id')

    if HAS_COUPANG_API and cq_access and cq_secret and cq_vendor:
        cq_c1, cq_c2 = st.columns([2, 1])
        with cq_c1:
            _cq_status_opts = {
                "결제완료 (신규주문)":   "ACCEPT",
                "발주확인":              "INSTRUCT",
                "신규 + 발주확인 (전체)": "ALL",
            }
            _cq_status_label = st.selectbox(
                "쿠팡 주문 상태", list(_cq_status_opts.keys()),
                index=0, key="cq_status_sel"
            )
            _cq_status = _cq_status_opts[_cq_status_label]
        with cq_c2:
            st.write("")
            st.write("")
            cq_fetch_btn = st.button("🛒 쿠팡 주문 조회", type="primary", key="cq_fetch")

        if cq_fetch_btn:
            with st.spinner("쿠팡 Wing API에서 주문을 조회 중..."):
                cq_rows, cq_err = coupang_api.get_orders(
                    cq_access, cq_secret, cq_vendor,
                    status=_cq_status, days_back=2,
                )
            if cq_err:
                st.error(f"❌ {cq_err}")
            elif not cq_rows:
                st.info("조회된 쿠팡 주문이 없습니다.")
            else:
                cq_df = pd.DataFrame(cq_rows)
                # 숫자 컬럼 정수 변환
                for _c in ['수량', '최종 상품별 총 주문금액', '배송비 합계',
                            '제주/도서 추가배송비', '정산예정금액']:
                    if _c in cq_df.columns:
                        cq_df[_c] = pd.to_numeric(cq_df[_c], errors='coerce').fillna(0).astype(int)
                cq_df = cq_df.sort_values('상품명').reset_index(drop=True)

                # 통합 진입점으로 매입가 계산만 수행 (daily_orders는 사용자 저장 시점에)
                from services import process_and_save_orders
                _s_cost = int(_gs('shipping_cost') or 1800)
                _b_cost = int(_gs('box_cost') or 300)
                _cq_result = process_and_save_orders(
                    USERNAME, cq_df,
                    datetime.today().strftime("%Y-%m-%d"),
                    _s_cost, _b_cost,
                    save_history=True,
                    save_daily=False,  # 사용자가 💾 저장 버튼 눌러야 daily_orders 반영
                )
                cq_df = _cq_result['df']  # 구입가격 채워진 df

                # 송장용 전체 저장 + Excel bytes 미리 생성
                st.session_state['order_full'] = cq_df.copy()
                _cq_xl = io.BytesIO()
                with pd.ExcelWriter(_cq_xl, engine='openpyxl') as _w:
                    cq_df.to_excel(_w, index=False)
                st.session_state['order_excel_bytes'] = _cq_xl.getvalue()
                st.session_state['orders'] = cq_df
                st.session_state['order_date'] = datetime.today().strftime("%Y-%m-%d")
                st.session_state['orders_unsaved'] = True  # 저장 대기 상태

                _notes = []
                if _cq_result['history']:    _notes.append(f"이력 {_cq_result['history']}건 저장")
                if _cq_result['fee_updates']: _notes.append(f"배송비 {_cq_result['fee_updates']}건 업데이트")
                if _cq_result['sale_updates']: _notes.append(f"판매가 {_cq_result['sale_updates']}건 업데이트")
                if _notes:
                    st.caption(f"💡 제품 DB: {' / '.join(_notes)}")

                st.success(f"✅ 쿠팡 주문 {len(cq_df)}건 조회 완료!")
                st.rerun()

        st.divider()
    elif HAS_COUPANG_API and not cq_access:
        st.caption("💡 설정에서 쿠팡 Wing API 키를 등록하면 쿠팡 주문도 자동 조회됩니다.")

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
            # 결제일은 옵셔널 (옛 엑셀 형식 호환)
            _required_cols = [c for c in EXTRACT_COLS if c != '결제일']
            missing = [c for c in _required_cols if c not in df.columns]
            if missing:
                st.error(f"필요한 컬럼이 없습니다: {missing}")
            else:
                # 송장번호 등록용 전체 데이터 저장 + Excel bytes 미리 생성
                if '상품주문번호' in df.columns:
                    st.session_state['order_full'] = df.copy()
                    _ful_xl = io.BytesIO()
                    with pd.ExcelWriter(_ful_xl, engine='openpyxl') as _w:
                        df.to_excel(_w, index=False)
                    st.session_state['order_excel_bytes'] = _ful_xl.getvalue()

                # '상품번호'·'결제일' 옵션 컬럼은 있으면 보존
                _extra_cols = [c for c in ['상품번호'] if c in df.columns]
                _cols_to_use = _required_cols + (['결제일'] if '결제일' in df.columns else []) + _extra_cols
                df = df[_cols_to_use].copy()
                for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
                df = df.sort_values('상품명').reset_index(drop=True)

                # 매칭 + 매입가 계산만 (daily_orders는 사용자가 명시적으로 💾 저장 버튼 눌러야 반영)
                from services import process_and_save_orders
                _s_cost = int(_gs('shipping_cost') or 1800)
                _b_cost = int(_gs('box_cost') or 300)
                _xl_result = process_and_save_orders(
                    USERNAME, df,
                    order_date.strftime("%Y-%m-%d"),
                    _s_cost, _b_cost,
                    save_history=True,
                    save_daily=False,
                )
                df = _xl_result['df']  # 구입가격 채워진 df

                st.session_state['orders'] = df
                st.session_state['order_date'] = order_date.strftime("%Y-%m-%d")
                st.session_state['orders_unsaved'] = True  # 저장 대기

                if _xl_result.get('error_orders'):
                    st.error(f"매입가 계산 일부 실패: {_xl_result['error_orders']}")
                else:
                    st.success(f"✅ {_xl_result['orders']}건 수집 완료 — 💾 저장 버튼을 눌러 수익계산에 반영하세요")

                notes = []
                if _xl_result['history']:    notes.append(f"이력 {_xl_result['history']}건 저장")
                if _xl_result['fee_updates']: notes.append(f"배송비 {_xl_result['fee_updates']}건 업데이트")
                if _xl_result['sale_updates']: notes.append(f"판매가 {_xl_result['sale_updates']}건 업데이트")
                if notes:
                    st.caption(f"💡 제품 DB: {' / '.join(notes)}")

    if 'orders' in st.session_state and st.session_state['orders'] is not None:
        df = st.session_state['orders']
        _default_date_str = st.session_state.get('order_date', datetime.today().strftime("%Y-%m-%d"))

        _unsaved = st.session_state.get('orders_unsaved', False)
        st.subheader(f"📦 주문 목록 ({len(df)}건)" + (" — 💾 저장 대기" if _unsaved else " — ✅ 저장됨"))

        # 저장 행: 날짜 선택 + 저장 버튼 + 지우기 버튼
        _sc_date, _sc_save, _sc_clear, _ = st.columns([2, 2, 1.5, 3])
        with _sc_date:
            try:
                _default_dt = datetime.strptime(_default_date_str, "%Y-%m-%d")
            except Exception:
                _default_dt = datetime.today()
            _save_date = st.date_input(
                "저장할 주문 날짜", value=_default_dt, key="save_date_input",
                help="이 날짜로 daily_orders 에 저장됩니다 (수익계산에서 이 날짜를 선택하면 불러옴)"
            )
            order_date_str = _save_date.strftime("%Y-%m-%d")
        with _sc_save:
            st.write("")
            st.write("")
            _save_clicked = st.button(
                "💾 저장하기" + (f" ({order_date_str})" if _unsaved else " (재저장)"),
                key="save_orders_btn",
                type="primary" if _unsaved else "secondary",
                use_container_width=True,
            )
        with _sc_clear:
            st.write("")
            st.write("")
            _clear_clicked = st.button("🗑 지우기", key="cancel_orders_btn", use_container_width=True)

        if _save_clicked:
            _s_cost = int(_gs('shipping_cost') or 1800)
            _b_cost = int(_gs('box_cost') or 300)
            try:
                from services import process_and_save_orders
                _r = process_and_save_orders(
                    USERNAME, df, order_date_str, _s_cost, _b_cost,
                    save_history=True, save_daily=True,
                )
                if _r.get('error_orders'):
                    st.error(f"저장 실패: {_r['error_orders']}")
                else:
                    st.session_state['orders'] = _r['df']
                    st.session_state['order_date'] = order_date_str
                    st.session_state['orders_unsaved'] = False
                    st.success(f"✅ {order_date_str} 주문 {_r['orders']}건 저장 완료 — 수익계산에서 확인하세요")
                    st.rerun()
            except Exception as _e:
                st.error(f"저장 실패: {_e}")
        if _clear_clicked:
            for _k in ['orders', 'order_date', 'order_full', 'order_excel_bytes', 'orders_unsaved', '_naver_status_dist']:
                st.session_state.pop(_k, None)
            st.rerun()

        # 디버그: API status 분포 (수량 안 맞을 때 원인 추적용)
        _dist_str = st.session_state.get('_naver_status_dist')
        if _dist_str:
            st.caption(f"🔍 네이버 API 상태 분포: {_dist_str}")

        _excel_bytes = st.session_state.get('order_excel_bytes')
        if not _excel_bytes and st.session_state.get('order_full') is not None:
            # 세션에 bytes 없으면 1회 생성 후 캐시
            _tmp = io.BytesIO()
            with pd.ExcelWriter(_tmp, engine='openpyxl') as _w:
                st.session_state['order_full'].to_excel(_w, index=False)
            _excel_bytes = _tmp.getvalue()
            st.session_state['order_excel_bytes'] = _excel_bytes
        if _excel_bytes:
            st.download_button(
                label="📥 배송준비건 엑셀 다운로드 (비밀번호 없음)",
                data=_excel_bytes,
                file_name=f"발주발송관리_{order_date_str}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="secondary"
            )

        st.dataframe(df[['수취인명','상품명','옵션정보','수량','최종 상품별 총 주문금액','배송비 합계','정산예정금액']],
                   use_container_width=True, hide_index=True)

        st.subheader("🛒 코스트코 장보기 목록")
        shop_cols = ['상품번호', '상품명', '옵션정보', '수량', '정산예정금액']
        available_cols = [c for c in shop_cols if c in df.columns]
        shopping = df[available_cols].copy()
        shopping['옵션정보'] = shopping['옵션정보'].fillna('') if '옵션정보' in shopping.columns else ''
        if '정산예정금액' in shopping.columns:
            shopping['정산예정금액'] = pd.to_numeric(shopping['정산예정금액'], errors='coerce').fillna(0).astype(int)

        # ── 집계: 상품번호·상품명·옵션정보가 모두 같아야 한 묶음 ──
        group_cols = [c for c in ['상품번호', '상품명', '옵션정보'] if c in shopping.columns]
        # 주문건수: 동일 상품의 고객 수 (row 수)
        _order_cnt = shopping.groupby(group_cols, sort=True, dropna=False).size().reset_index(name='주문건수')
        agg_map = {'수량': 'sum'}
        if '정산예정금액' in shopping.columns:
            agg_map['정산예정금액'] = 'sum'
        shopping = shopping.groupby(group_cols, sort=True, dropna=False).agg(agg_map).reset_index()
        rename_cols = list(group_cols) + ['주문수량']
        if '정산예정금액' in agg_map:
            rename_cols.append('정산금액')
        shopping.columns = rename_cols
        shopping = shopping.merge(_order_cnt, on=group_cols, how='left')

        # ── 묶음수량 추출 (옵션/상품명 기반) ──
        shopping['묶음수량'] = shopping.apply(
            lambda r: extract_pack_qty(r.get('옵션정보', ''), r['상품명']), axis=1)

        # ── DB 단가 + 분리수량 조회 (DB 1회 로드) ──
        _rnd_shared = get_shared_products()
        _rnd_user   = get_all_products(USERNAME)
        db_prices, db_splits = [], []
        for _, r in shopping.iterrows():
            p = match_product_to_db(USERNAME, r['상품명'], product_no=r.get('상품번호', ''),
                                    _user_prods=_rnd_user, _shared_prods=_rnd_shared)
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
        kakao_token = _gs('kakao_access_token')
        tg_token = _gs('telegram_token')
        tg_chat = _gs('telegram_chat_id')

        _ship_b1, _ship_b2 = st.columns(2)
        if _ship_b2.button("📋 장보기 목록 관리자에게 보내기", key="send_shopping_admin",
                            use_container_width=True):
            _items = []
            _total_amount = 0
            for _, r in shopping.iterrows():
                _est = r.get('예상금액')
                _est_v = int(_est) if pd.notna(_est) else 0
                _total_amount += _est_v
                _items.append({
                    "코스트코상품번호": str(r.get('코스트코상품번호') or r.get('상품번호') or ''),
                    "상품명": str(r.get('상품명', '')),
                    "옵션정보": str(r.get('옵션정보', '') or ''),
                    "주문건수": int(r.get('주문건수', 1) or 1),
                    "주문수량": int(r.get('주문수량', 0) or 0),
                    "분리수량": int(r.get('분리수량', 1) or 1),
                    "묶음수량": int(r.get('묶음수량', 1) or 1),
                    "코스트코구매수량": int(r.get('코스트코구매수량', 0) or 0),
                    "팩단가": int(r['팩단가']) if pd.notna(r.get('팩단가')) else 0,
                    "예상금액": _est_v,
                    "정산금액": int(r['정산금액']) if pd.notna(r.get('정산금액')) else 0,
                })
            try:
                submit_shopping_list(USERNAME, order_date_str, _items,
                                     total_items=len(_items),
                                     total_amount=_total_amount)
                st.success(f"✅ 관리자에게 전송 완료 — {len(_items)}개 상품 ({fmt(_total_amount)}원)")
            except Exception as _se:
                st.error(f"❌ 전송 실패: {_se}")

        if _ship_b1.button("📱 장보기 목록 휴대폰 전송", key="send_shopping",
                            use_container_width=True):
            order_date_obj = datetime.strptime(order_date_str, "%Y-%m-%d")
            lines = [f"🛒 코스트코 장보기 목록 ({order_date_obj.strftime('%m/%d')})", ""]
            _has_settle = '정산금액' in shopping.columns
            for _, r in shopping.iterrows():
                opt = f"({r['옵션정보']})" if r.get('옵션정보') else ""
                sq = int(r.get('분리수량', 1))
                pq = int(r.get('묶음수량', 1))
                buy_qty = int(r['코스트코구매수량'])
                order_cnt = int(r.get('주문건수', 1))   # 실제 주문 고객 수
                order_qty = int(r['주문수량'])           # 총 수량 합계
                if sq > 1:
                    qty_str = f"{buy_qty}팩 ({order_cnt}건/{order_qty}개÷{sq}소분)"
                elif pq > 1:
                    qty_str = f"{buy_qty}개 ({order_cnt}건×{pq}구)"
                else:
                    qty_str = f"{buy_qty}개 ({order_cnt}건)" if order_cnt > 1 else f"{buy_qty}개"
                name_part = " ".join(p for p in [r['상품명'][:22], opt] if p)
                lines.append(f"▪ {name_part} × {qty_str}")
                if _has_settle:
                    _settle = int(r.get('정산금액', 0) or 0)
                    if _settle:
                        lines.append(f"   💳 정산: {fmt(_settle)}원")
            lines.append(f"\n💰 예상 총액: {fmt(shopping['예상금액'].dropna().sum())}원")
            if _has_settle:
                _total_settle = int(shopping['정산금액'].fillna(0).sum())
                if _total_settle:
                    lines.append(f"💳 총 정산예정: {fmt(_total_settle)}원")
            lines.append(f"📦 총 {len(df)}건")
            msg = "\n".join(lines)

            sent_ok = False
            kakao_api_key = _gs('kakao_api_key')
            kakao_refresh = _gs('kakao_refresh_token')

            # 200자 초과 + 텔레그램 설정 시 → 텔레그램 전체 발송 + 카톡엔 알림만
            if len(msg) > 200 and tg_token and tg_chat:
                ok_tg, terr = naver_api.send_telegram(tg_token, tg_chat, msg)
                if ok_tg:
                    sent_ok = True
                    if kakao_token:
                        short_msg = f"🛒 코스트코 장보기 알림 발송됨\n총 {len(df)}건 ({len(msg):,}자)\n자세한 내역은 텔레그램에서 확인하세요."
                        ok_k, kerr = naver_api.send_kakao(kakao_token, short_msg, rest_api_key=kakao_api_key, refresh_token=kakao_refresh)
                        if ok_k and kerr and "__TOKEN_REFRESHED__" in str(kerr):
                            parts = str(kerr).replace("__TOKEN_REFRESHED__", "").split("||")
                            set_setting(USERNAME, 'kakao_access_token', parts[0])
                            if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                else:
                    st.error(f"❌ 텔레그램 실패: {terr}")
            else:
                # 200자 이내 또는 텔레그램 미설정 → 카톡 우선
                if kakao_token:
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
