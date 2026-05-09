"""📮 송장번호 등록 페이지 — pages_lib 자동 추출."""
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

try:
    import coupang_api
    HAS_COUPANG_API = True
except ImportError:
    HAS_COUPANG_API = False
    coupang_api = None

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


def _show_dispatch_result(container, result, err, total):
    """발송처리 API 결과를 container(st.columns 셀 등)에 표시."""
    if err:
        container.error(f"❌ {err}")
        return
    if not result:
        return
    ok   = result.get('success', 0)
    fail = result.get('fail', 0)
    sent = result.get('sent_count', total)
    if ok > 0:
        container.success(f"✅ 성공 {ok}건 / 실패 {fail}건 (전송 {sent}건)")
    else:
        container.error(f"❌ 전체 실패 {fail}건 (전송 {sent}건)")
    if result.get('fail_details'):
        with container.expander("📋 실패 상세", expanded=True):
            for d in result['fail_details']:
                st.text(d)


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    """📮 송장번호 등록 탭 렌더링."""
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    st.header("📮 송장번호 일괄 등록")
    st.caption("택배사 PIDPIC/접수파일을 업로드하면 네이버 스마트스토어 일괄 송장 등록 파일을 생성하거나 API로 자동 발송처리합니다.")

    _ship_c1, _ship_c2 = st.columns([4, 1])
    pidpic_file = _ship_c1.file_uploader(
        "택배사 파일 업로드 (xlsx/xls/csv) — 롯데PIDPIC, CJ파일접수상세내역 모두 지원",
        type=['xlsx', 'xls', 'csv'], key="track_pidpic"
    )
    courier = _ship_c2.selectbox("택배사", ["CJ대한통운", "롯데택배", "한진택배", "우체국택배", "로젠택배"])

    # ── 플랫폼 레지스트리 (항상 렌더, 향후 스토어 추가 시 dict 1개 추가) ──────
    cq_access = _gs('coupang_access_key')
    cq_secret = _gs('coupang_secret_key')
    cq_vendor = _gs('coupang_vendor_id')

    _PLATFORMS = [
        {
            "id": "naver",
            "label": "네이버 스마트스토어",
            "available": HAS_NAVER_API and bool(api_id) and bool(api_secret),
            "unavail_tip": "설정 > 네이버 커머스 API 키 입력 필요",
            "courier_map": {
                "CJ대한통운": "CJGLS", "롯데택배": "HYUNDAI",
                "한진택배": "HANJIN", "우체국택배": "EPOST", "로젠택배": "KGB",
            },
            "code_hint": "CJGLS / HYUNDAI / HANJIN / EPOST / KGB",
        },
        {
            "id": "coupang",
            "label": "쿠팡 Wing",
            "available": HAS_COUPANG_API and bool(cq_access) and bool(cq_secret) and bool(cq_vendor),
            "unavail_tip": "설정 > 쿠팡 Wing API 키 입력 필요",
            "courier_map": {
                "CJ대한통운": "CJ_LOGISTICS", "롯데택배": "LOTTE",
                "한진택배": "HANJIN", "우체국택배": "EPOST", "로젠택배": "LOGEN",
            },
            "code_hint": "CJ_LOGISTICS / LOTTE / HANJIN / EPOST / LOGEN",
        },
        # ── 향후 스토어 추가: 아래 주석 해제 후 id/label/courier_map 수정 ──
        # {"id": "gmarket", "label": "G마켓/옥션", "available": False,
        #  "unavail_tip": "준비 중", "courier_map": {}, "code_hint": ""},
    ]

    # CJ 안내
    if courier == "CJ대한통운":
        with st.expander("ℹ️ CJ대한통운 파일 안내", expanded=False):
            st.markdown("""
**CJ대한통운 파일 사용 방법:**
1. CJ e-Shipping(또는 스마트택배) → **파일접수** 또는 **PIDPIC 다운로드**
2. 파일에서 **A열(고객주문번호) = 네이버 상품주문번호**, **운송장번호 열** 확인
3. 아래에 파일 업로드 후 컬럼 선택

> 💡 '고객주문번호' 열이 A열에 있고, 운송장번호가 별도 열에 있어야 합니다.
            """)

    # ── 파일 파싱 결과 (파일 있을 때만) ─────────────────────────────────────
    result_df = None  # 항상 초기화 — 아래 발송 섹션에서 None 체크

    if pidpic_file:
        pidpic_df, err2 = read_excel_auto(pidpic_file)

        if pidpic_df is None:
            st.error(f"파일 읽기 실패: {err2}")
        else:
            all_cols = [str(c) for c in pidpic_df.columns]

            # ── 컬럼 자동 인식 (롯데·CJ 모두 대응) ─────────────────
            _order_kws  = ['주문번호', '고객주문', 'ORDER', '주문 번호']
            _track_kws  = ['운송장', '송장', 'TRACKING', '운송 장', '운송번호', 'waybill', 'WAYBILL', '운송No', '배송번호']
            col_order_auto = next((c for c in all_cols if any(kw in c for kw in _order_kws)), None)
            col_track_auto = next((c for c in all_cols if any(kw in c for kw in _track_kws)), None)

            with st.expander("📋 파일 컬럼 확인 / 수동 선택", expanded=(not col_order_auto or not col_track_auto)):
                st.caption(f"파일에서 찾은 컬럼 ({len(all_cols)}개): {', '.join(all_cols)}")
                _mc1, _mc2 = st.columns(2)
                col_order = _mc1.selectbox(
                    "🔢 주문번호 컬럼 (= 상품주문번호)", all_cols,
                    index=all_cols.index(col_order_auto) if col_order_auto else 0,
                    key="sel_order_col"
                )
                col_track = _mc2.selectbox(
                    "📦 운송장번호 컬럼", all_cols,
                    index=all_cols.index(col_track_auto) if col_track_auto else 0,
                    key="sel_track_col"
                )
                if col_order_auto and col_track_auto:
                    st.success(f"✅ 자동 인식 완료 — 주문번호: **{col_order}** / 운송장: **{col_track}**")
                else:
                    st.warning("⚠️ 컬럼을 자동으로 찾지 못했습니다. 위에서 직접 선택해주세요.")

            if col_order == col_track:
                st.error(
                    f"⛔ 주문번호 컬럼과 운송장번호 컬럼이 모두 **'{col_order}'** 으로 동일합니다.\n\n"
                    "위 '📦 운송장번호 컬럼' 셀렉트박스에서 실제 운송장번호 컬럼을 선택해주세요."
                )
                st.caption(f"📋 파일의 전체 컬럼: {', '.join(all_cols)}")
            else:
                pidpic_df['_주문번호']   = pidpic_df[col_order].apply(to_id_str)
                pidpic_df['_운송장번호'] = pidpic_df[col_track].apply(to_id_str)
                valid = pidpic_df[
                    (pidpic_df['_주문번호'].str.len() > 5) &
                    (pidpic_df['_운송장번호'].str.len() > 5) &
                    (pidpic_df['_운송장번호'] != 'nan')
                ].copy()

                if valid.empty:
                    st.warning("유효한 데이터가 없습니다. 컬럼 선택이 올바른지 확인하세요.")
                    st.dataframe(pidpic_df[[col_order, col_track]].head(5), use_container_width=True)
                else:
                    result_df = pd.DataFrame({
                        '상품주문번호': valid['_주문번호'].values,
                        '배송방법': '택배,등기,소포',
                        '택배사': courier,
                        '송장번호': valid['_운송장번호'].values,
                    })
                    _same_ratio = (result_df['상품주문번호'] == result_df['송장번호']).mean()
                    if _same_ratio > 0.5:
                        st.error(
                            f"⛔ 송장번호가 상품주문번호와 동일합니다 ({int(_same_ratio*100)}% 일치). "
                            "운송장번호 컬럼 선택을 다시 확인해주세요."
                        )
                        result_df = None  # 오류 시 발송 불가

                    if result_df is not None:
                        st.metric("처리 가능 건수", f"{len(result_df)}건")
                        st.dataframe(result_df, use_container_width=True, hide_index=True)
                        st.divider()

                        # ── 반자동: XLS 다운로드 ──────────────────────────
                        st.subheader("📥 반자동 — 파일 다운로드 후 스마트스토어에 직접 업로드")
                        _xl_out = io.BytesIO()
                        import xlwt as _xlwt
                        _wb = _xlwt.Workbook(encoding='utf-8')
                        _ws = _wb.add_sheet('발송처리')
                        for _ci, _h in enumerate(['상품주문번호', '배송방법', '택배사', '송장번호']):
                            _ws.write(0, _ci, _h)
                        for _ri, (_, _row) in enumerate(result_df.iterrows(), 1):
                            _ws.write(_ri, 0, str(_row['상품주문번호']))
                            _ws.write(_ri, 1, str(_row['배송방법']))
                            _ws.write(_ri, 2, str(_row['택배사']))
                            _ws.write(_ri, 3, str(_row['송장번호']))
                        _wb.save(_xl_out)
                        _xl_out.seek(0)
                        st.download_button(
                            label=f"📥 송장번호_일괄_등록.xls 다운로드 ({len(result_df)}건)",
                            data=_xl_out,
                            file_name=f"송장번호_일괄_등록_{datetime.today().strftime('%Y%m%d')}.xls",
                            mime="application/vnd.ms-excel",
                            use_container_width=True,
                        )

    # ══════════════════════════════════════════════════════════════════════
    # 🚀 자동 발송처리 — 항상 노출 (파일 없으면 안내 메시지, 있으면 버튼 활성)
    # ══════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🚀 자동 발송처리")
    st.caption("발송할 플랫폼을 선택하세요 (복수 동시 발송 가능)")

    # ── 체크박스 행 ──────────────────────────────────────────────────────
    _plat_check_cols = st.columns(len(_PLATFORMS) + 3)
    _plat_checked = {}
    for _pi, _p in enumerate(_PLATFORMS):
        with _plat_check_cols[_pi]:
            if _p["available"]:
                _plat_checked[_p["id"]] = st.checkbox(
                    _p["label"], value=True, key=f"plat_{_p['id']}"
                )
            else:
                st.checkbox(_p["label"], value=False, disabled=True, key=f"plat_{_p['id']}_dis")
                st.caption(f"⚙️ {_p['unavail_tip']}")
                _plat_checked[_p["id"]] = False

    # ── 데이터 없으면 안내 ───────────────────────────────────────────────
    if result_df is None:
        st.info("위에서 택배사 파일을 업로드하면 발송처리 버튼이 활성화됩니다.")
    else:
        # ── 체크된 플랫폼별 발송 카드 ─────────────────────────────────
        for _p in _PLATFORMS:
            if not _plat_checked.get(_p["id"]):
                continue

            st.markdown(
                f"<div style='background:#f8f9fa;border-left:4px solid #e74c3c;"
                f"padding:12px 16px;border-radius:4px;margin:8px 0'>"
                f"<b style='font-size:15px'>{_p['label']}</b></div>",
                unsafe_allow_html=True
            )
            _auto_code = _p["courier_map"].get(courier, courier)
            _dc1, _dc2, _dc3 = st.columns([2, 1, 4])
            _dc1.caption("API 택배사 코드")
            _code_val = _dc1.text_input(
                "코드", value=_auto_code, key=f"code_{_p['id']}",
                label_visibility="collapsed", help=_p["code_hint"],
            )
            _dc2.write(""); _dc2.write("")

            if _p["id"] == "naver":
                if _dc2.button("🚀 발송처리", key=f"btn_{_p['id']}", type="primary", use_container_width=True):
                    _items = [{"productOrderId": str(r['상품주문번호']).split('.')[0].strip(),
                                "택배사": _code_val,
                                "trackingNumber": str(r['송장번호']).replace('-','').strip()}
                               for _, r in result_df.iterrows()]
                    with st.spinner(f"네이버에 {len(_items)}건 발송처리 중..."):
                        _res, _err = naver_api.ship_orders(api_id, api_secret, _items)
                    _show_dispatch_result(_dc3, _res, _err, len(_items))

            elif _p["id"] == "coupang":
                _dc3.caption("💡 쿠팡 상품주문번호 형식: `주문번호-아이템번호` — CJ 고객주문번호에 이 값 입력")
                if _dc2.button("🚀 발송처리", key=f"btn_{_p['id']}", type="primary", use_container_width=True):
                    _items = [{"productOrderId": str(r['상품주문번호']).strip(),
                                "courierCode": _code_val,
                                "trackingNumber": str(r['송장번호']).replace('-','').strip()}
                               for _, r in result_df.iterrows()]
                    with st.spinner(f"쿠팡 Wing에 {len(_items)}건 발송처리 중..."):
                        _res, _err = coupang_api.dispatch_orders(cq_access, cq_secret, cq_vendor, _items)
                    _show_dispatch_result(_dc3, _res, _err, len(_items))

    # ═══════════════════════════════════════
    # 탭 2: 영수증 등록
    # ═══════════════════════════════════════
