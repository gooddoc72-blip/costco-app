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


def _prepare_dispatch_rows(username, result_df, success_order_ids):
    """발송 성공건을 dispatch_log row 리스트로 가공 (저장 대기용)."""
    from db import search_order_history
    if not success_order_ids:
        return []
    success_set = {str(x).strip() for x in success_order_ids}

    # 정산예정금액 보강을 위해 order_history에서 매칭
    _hist = search_order_history(username, date_from='', date_to='', limit=5000)
    _hist_by_no = {str(r.get('order_no', '')): r for r in (_hist or [])}

    rows = []
    for _, r in result_df.iterrows():
        po = str(r.get('상품주문번호', '')).split('.')[0].strip()
        if po not in success_set:
            continue
        _h = _hist_by_no.get(po, {})
        rows.append({
            'order_no':              po,
            'recipient':             _h.get('recipient') or r.get('수취인명') or '',
            'product_name':          _h.get('product_name') or r.get('상품명') or '',
            'expected_settlement':   int(_h.get('settlement') or 0),
            'customer_shipping_fee': int(_h.get('shipping_fee') or 0),
            'tracking_no':           str(r.get('송장번호', '')).replace('-', '').strip(),
            'courier':               str(r.get('택배사', '')).strip(),
        })
    return rows


def _save_dispatch_rows(username, rows, platform, container):
    """대기 중인 row들을 dispatch_log에 실제 insert (수동 저장 버튼이 호출)."""
    from db import log_dispatch_success
    from datetime import datetime as _dt
    if not rows:
        return 0
    today = _dt.today().strftime("%Y-%m-%d")
    saved = log_dispatch_success(username, rows, today, platform=platform)
    container.caption(f"💾 dispatch_log: {saved}건 저장됨 ({today}) — 정산 매칭에 사용")
    return saved


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
    pidpic_files = _ship_c1.file_uploader(
        "택배사 파일 업로드 (xlsx/xls/csv) — 여러 개 한번에 가능, 롯데PIDPIC·CJ파일접수상세내역 모두 지원",
        type=['xlsx', 'xls', 'csv'], key="track_pidpic",
        accept_multiple_files=True,
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
                "CJ대한통운": "CJGLS", "롯데택배": "HDEXP",
                "한진택배": "HANJIN", "우체국택배": "EPOST", "로젠택배": "KGB",
                "경동택배": "KDEXP", "대신택배": "DAESIN",
            },
            "code_hint": "CJGLS / HDEXP / HANJIN / EPOST / KGB / KDEXP",
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

    if pidpic_files:
        _order_kws = ['주문번호', '고객주문', 'ORDER', '주문 번호']
        _track_kws = ['운송장', '송장', 'TRACKING', '운송 장', '운송번호',
                      'waybill', 'WAYBILL', '운송No', '배송번호']

        _per_file_results = []   # [{name, count, df}]
        _per_file_errors  = []   # [str]

        for _pf in pidpic_files:
            _pdf, _perr = read_excel_auto(_pf)
            if _pdf is None:
                _per_file_errors.append(f"❌ {_pf.name}: 읽기 실패 — {_perr}")
                continue
            _cols = [str(c) for c in _pdf.columns]
            _co = next((c for c in _cols if any(kw in c for kw in _order_kws)), None)
            _ct = next((c for c in _cols if any(kw in c for kw in _track_kws)), None)
            if not _co or not _ct or _co == _ct:
                _per_file_errors.append(
                    f"⚠️ {_pf.name}: 주문번호/운송장 컬럼을 자동 인식하지 못했습니다 — "
                    f"발견된 컬럼: {', '.join(_cols)}"
                )
                continue
            _pdf['_주문번호']   = _pdf[_co].apply(to_id_str)
            _pdf['_운송장번호'] = _pdf[_ct].apply(to_id_str)
            _valid = _pdf[
                (_pdf['_주문번호'].str.len() > 5) &
                (_pdf['_운송장번호'].str.len() > 5) &
                (_pdf['_운송장번호'] != 'nan')
            ].copy()
            if _valid.empty:
                _per_file_errors.append(f"⚠️ {_pf.name}: 유효 행 없음")
                continue
            _file_df = pd.DataFrame({
                '상품주문번호': _valid['_주문번호'].values,
                '배송방법': '택배,등기,소포',
                '택배사': courier,
                '송장번호': _valid['_운송장번호'].values,
            })
            _same = (_file_df['상품주문번호'] == _file_df['송장번호']).mean()
            if _same > 0.5:
                _per_file_errors.append(
                    f"⛔ {_pf.name}: 송장번호가 주문번호와 동일 비율 {int(_same*100)}% — 컬럼 인식 오류 의심"
                )
                continue
            _per_file_results.append({'name': _pf.name, 'count': len(_file_df),
                                       'col_order': _co, 'col_track': _ct, 'df': _file_df})

        # 파일별 처리 결과 표시
        if _per_file_results:
            with st.expander(f"📋 파일별 인식 결과 ({len(_per_file_results)}개 성공"
                              + (f" / {len(_per_file_errors)}개 실패" if _per_file_errors else "")
                              + ")", expanded=bool(_per_file_errors)):
                for _r in _per_file_results:
                    st.success(f"✅ {_r['name']}: {_r['count']}건 — 주문번호: **{_r['col_order']}** / 운송장: **{_r['col_track']}**")
                for _e in _per_file_errors:
                    st.error(_e)
        elif _per_file_errors:
            for _e in _per_file_errors:
                st.error(_e)

        # 모든 파일의 valid 행을 합산 + 중복 주문번호 제거(첫 번째 우선)
        if _per_file_results:
            result_df = pd.concat([r['df'] for r in _per_file_results], ignore_index=True)
            _before = len(result_df)
            result_df = result_df.drop_duplicates(subset=['상품주문번호'], keep='first').reset_index(drop=True)
            _dup_dropped = _before - len(result_df)
            if _dup_dropped > 0:
                st.caption(f"💡 중복 주문번호 {_dup_dropped}건 제거됨 (첫 번째 파일의 송장 사용)")

        if result_df is not None:
            st.metric("처리 가능 건수", f"{len(result_df)}건")
            st.dataframe(result_df, use_container_width=True, hide_index=True)
            st.divider()

            # ── 반자동: XLSX 다운로드 ─────────────────────────
            st.subheader("📥 반자동 — 파일 다운로드 후 스마트스토어에 직접 업로드")
            _xl_out = io.BytesIO()
            import xlsxwriter as _xlsxwriter
            _wb = _xlsxwriter.Workbook(_xl_out, {'in_memory': True})
            _ws = _wb.add_worksheet('발송처리')
            for _ci, _h in enumerate(['상품주문번호', '배송방법', '택배사', '송장번호']):
                _ws.write(0, _ci, _h)
            for _ri, (_, _row) in enumerate(result_df.iterrows(), 1):
                _ws.write(_ri, 0, str(_row['상품주문번호']))
                _ws.write(_ri, 1, str(_row['배송방법']))
                _ws.write(_ri, 2, str(_row['택배사']))
                _ws.write(_ri, 3, str(_row['송장번호']))
            _wb.close()
            _xl_out.seek(0)
            st.download_button(
                label=f"📥 송장번호_일괄_등록.xlsx 다운로드 ({len(result_df)}건)",
                data=_xl_out,
                file_name=f"tracking_upload_{datetime.today().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
                    if _res and _res.get('success_order_ids'):
                        # 발송처리 성공 시 dispatch_log에 자동 저장 (별도 저장버튼 불필요 → 홈 달력 즉시 반영)
                        _drows = _prepare_dispatch_rows(USERNAME, result_df, _res['success_order_ids'])
                        _save_dispatch_rows(USERNAME, _drows, 'naver', _dc3)

            elif _p["id"] == "coupang":
                _dc3.caption("💡 쿠팡 상품주문번호 형식: `주문번호-아이템번호` — CJ 고객주문번호에 이 값 입력")
                if _dc2.button("🚀 발송처리", key=f"btn_{_p['id']}", type="primary", use_container_width=True):
                    # 업로드값이 orderId만(하이픈 없음)이면 order_history의 전체 상품주문번호
                    # (orderId-vendorItemId)로 해결 — CJ에 orderId만 넣은 경우도 발송 가능하게.
                    from db import search_order_history as _soh
                    _cp_full = {}
                    for _h in (_soh(USERNAME, date_from='', date_to='', limit=5000) or []):
                        _ono = str(_h.get('order_no', '') or '')
                        if '-' in _ono and not _ono.endswith('-None'):
                            _cp_full.setdefault(_ono.split('-')[0], _ono)

                    def _resolve_cp(_v):
                        _v = str(_v).split('.')[0].strip()
                        return _v if '-' in _v else _cp_full.get(_v, _v)

                    _items = [{"productOrderId": _resolve_cp(r['상품주문번호']),
                                "courierCode": _code_val,
                                "trackingNumber": str(r['송장번호']).replace('-','').strip()}
                               for _, r in result_df.iterrows()]
                    with st.spinner(f"쿠팡 Wing에 {len(_items)}건 발송처리 중..."):
                        _res, _err = coupang_api.dispatch_orders(cq_access, cq_secret, cq_vendor, _items)
                    _show_dispatch_result(_dc3, _res, _err, len(_items))
                    if _res and _res.get('success_order_ids'):
                        # 발송처리 성공 시 dispatch_log에 자동 저장
                        _drows = _prepare_dispatch_rows(USERNAME, result_df, _res['success_order_ids'])
                        _save_dispatch_rows(USERNAME, _drows, 'coupang', _dc3)

            # ── 저장 대기 (수동 저장 버튼) — 같은 플랫폼 카드 안에 표시 ──
            _pkey = f"dispatch_pending_{_p['id']}"
            _pending = st.session_state.get(_pkey)
            if _pending and _pending.get('rows'):
                _sc1, _sc2 = st.columns([3, 1])
                _sc1.info(
                    f"💾 저장 대기 — 성공 {_pending['count']}건. "
                    f"'저장' 버튼을 누르면 dispatch_log에 기록되어 정산 매칭에 사용됩니다."
                )
                _sc2.write(""); _sc2.write("")
                if _sc2.button("💾 저장", key=f"save_{_p['id']}", type="primary", use_container_width=True):
                    _save_dispatch_rows(USERNAME, _pending['rows'], _p['id'], _sc1)
                    st.session_state.pop(_pkey, None)
                    st.rerun()

    # ═══════════════════════════════════════
    # 수동 송장 입력 — 미발송 주문에 직접 입력 → dispatch_log 저장
    # ═══════════════════════════════════════
    st.divider()
    st.subheader("✏️ 수동 송장 입력")
    st.caption("미발송 주문 목록에 송장번호를 직접 입력합니다. 저장하면 dispatch_log에 기록되어 **정산 매칭**에 사용됩니다.")

    from db import get_active_orders, db_rows_to_orders_df, log_dispatch_success
    from db_core import get_user_db as _get_user_db

    _active_rows = get_active_orders(USERNAME)
    if not _active_rows:
        st.info("미발송 주문이 없습니다. 주문 수집 탭에서 먼저 주문을 수집해주세요.")
    else:
        _mdf = db_rows_to_orders_df(_active_rows)
        _show = ['상품주문번호', '수취인명', '상품명', '수량', '정산예정금액']
        _edit_df = _mdf[[c for c in _show if c in _mdf.columns]].copy()
        _edit_df['택배사'] = courier
        _edit_df['송장번호'] = ''

        _edited = st.data_editor(
            _edit_df,
            column_config={
                '상품주문번호': st.column_config.TextColumn("주문번호", disabled=True, width='medium'),
                '수취인명':    st.column_config.TextColumn("수취인", disabled=True),
                '상품명':      st.column_config.TextColumn("상품명", disabled=True, width='large'),
                '수량':        st.column_config.NumberColumn("수량", disabled=True, width='small'),
                '정산예정금액': st.column_config.NumberColumn("정산예정", disabled=True),
                '택배사':      st.column_config.TextColumn("택배사", width='small'),
                '송장번호':    st.column_config.TextColumn("송장번호", width='medium'),
            },
            hide_index=True,
            use_container_width=True,
            key="manual_tracking_editor",
        )

        _filled = _edited[_edited['송장번호'].astype(str).str.strip().str.len() > 5].copy()
        if not _filled.empty:
            _mi1, _mi2 = st.columns([3, 1])
            _mi1.info(f"송장번호 입력된 주문 **{len(_filled)}건** — 저장하면 정산 매칭에 활용됩니다.")
            _mi2.write(""); _mi2.write("")
            if _mi2.button("💾 발송 완료 저장", type="primary", use_container_width=True, key="manual_disp_save"):
                _today = datetime.today().strftime("%Y-%m-%d")
                _save_rows = []
                for _, _r in _filled.iterrows():
                    _pno = str(_r.get('상품주문번호', '')).strip()
                    _trk = str(_r.get('송장번호', '')).replace('-', '').strip()
                    if not _pno or not _trk:
                        continue
                    _save_rows.append({
                        'order_no':              _pno,
                        'recipient':             str(_r.get('수취인명', '')),
                        'product_name':          str(_r.get('상품명', '')),
                        'expected_settlement':   int(_r.get('정산예정금액', 0) or 0),
                        'customer_shipping_fee': 0,
                        'tracking_no':           _trk,
                        'courier':               str(_r.get('택배사', '')).strip(),
                    })
                if _save_rows:
                    # 플랫폼 판별: 쿠팡은 '-' 포함
                    _has_naver   = any('-' not in r['order_no'] for r in _save_rows)
                    _has_coupang = any('-' in r['order_no']     for r in _save_rows)
                    _plat = 'naver' if _has_naver and not _has_coupang else \
                            'coupang' if _has_coupang and not _has_naver else 'mixed'
                    _saved = log_dispatch_success(USERNAME, _save_rows, _today, platform=_plat)
                    # order_history.tracking_no 갱신
                    _conn = _get_user_db(USERNAME)
                    for _r in _save_rows:
                        try:
                            _conn.execute(
                                "UPDATE order_history SET tracking_no=?, courier=? WHERE order_no=?",
                                (_r['tracking_no'], _r['courier'], _r['order_no'])
                            )
                        except Exception:
                            pass
                    _conn.commit()
                    _conn.close()
                    st.success(f"✅ {_saved}건 저장 완료 ({_today}) — 정산 매칭 탭에서 {_today} 발송일로 조회하세요.")
                    st.rerun()

    # ═══════════════════════════════════════
    # 📋 발송내역 (dispatch_log 기록)
    # ═══════════════════════════════════════
    st.divider()
    st.subheader("📋 발송내역")
    st.caption("발송처리되어 dispatch_log에 기록된 내역입니다. (정산 매칭에 사용)")
    from db import get_dispatch_dates, get_dispatched_orders_with_details
    _disp_dates = get_dispatch_dates(USERNAME, limit=30)
    if not _disp_dates:
        st.info("아직 발송내역이 없습니다. 위에서 발송처리(또는 수동 송장 저장) 시 자동 기록됩니다.")
    else:
        _hc1, _hc2 = st.columns([1.5, 3])
        _sel_dd = _hc1.selectbox("발송일 선택", _disp_dates, key="track_hist_date")
        _hrows = get_dispatched_orders_with_details(USERNAME, _sel_dd)
        if _hrows:
            _hc2.metric(f"{_sel_dd} 발송", f"{len(_hrows)}건")
            _df_h = pd.DataFrame(_hrows)
            _show = [c for c in ['platform', 'order_no', 'recipient', 'product_name',
                                 'tracking_no', 'courier', 'settlement'] if c in _df_h.columns]
            _df_h = _df_h[_show]
            _rename = {'platform': '플랫폼', 'order_no': '주문번호', 'recipient': '수취인',
                       'product_name': '상품명', 'tracking_no': '송장번호',
                       'courier': '택배사', 'settlement': '정산예정'}
            _df_h.columns = [_rename.get(c, c) for c in _show]
            st.dataframe(_df_h, use_container_width=True, hide_index=True)
        else:
            st.caption("해당 발송일에 기록이 없습니다.")

    # ═══════════════════════════════════════
    # 탭 2: 영수증 등록
    # ═══════════════════════════════════════
