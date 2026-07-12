"""📦 제품 DB 페이지 — pages_lib 자동 추출."""
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
    bulk_update_category, link_naver_to_shared, unlink_naver_from_shared,
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
from pages_lib._product_db_categories import (
    map_naver_category, guess_category_from_name,
)
from pages_lib._product_db_naver_import import render_naver_import_section

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
    """📦 제품 DB 탭 렌더링."""
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    st.header("📦 제품 가격 DB 관리")
    st.caption("🔗 공유 필드(매입가·상품명)는 읽기전용 — 영수증 업로드 또는 관리자 탭에서 수정 | ✏️ 판매가·배송비는 개인별 수정 가능")

    # ── 🧹 이전 스토어 상품 정리 (현재 API 키 스토어에 없는 네이버 상품 삭제) ──
    with st.expander("🧹 이전 스토어 상품 정리 — 현재 스토어에 없는 네이버 상품 삭제", expanded=False):
        st.caption("스토어를 옮긴 경우 DB에 남은 **이전 스토어 상품**(현재 API 키로 접근 불가·순위추적/태그에서 혼란)을 정리합니다. "
                   "현재 스토어 상품목록을 API로 조회해, 거기 없는 네이버(from_naver) 상품만 삭제합니다.")
        if not (api_id and api_secret):
            st.info("설정 탭 > 네이버 커머스 API 키가 필요합니다.")
        else:
            from db import get_all_products as _get_all_prods, delete_user_products_by_ids as _del_prods
            import naver_api as _clean_napi
            if st.button("🔎 분석 (삭제 대상 미리보기)", key="clean_analyze", use_container_width=True):
                with st.spinner("현재 스토어 상품목록 조회 중..."):
                    _cur_list, _cerr = _clean_napi.get_product_list(api_id, api_secret)
                if _cerr:
                    st.error(f"현재 스토어 조회 실패: {_cerr}")
                else:
                    _valid = set()
                    for _it in (_cur_list or []):
                        for _k in ('originProductNo', 'channelProductNo'):
                            _v = str(_it.get(_k) or '').strip()
                            if _v:
                                _valid.add(_v)
                    _stale = []
                    for _p in _get_all_prods(USERNAME):
                        if int(_p.get('from_naver') or 0) != 1:
                            continue
                        _nos = {str(_p.get('naver_origin_pno') or '').strip(),
                                str(_p.get('naver_channel_pno') or '').strip()}
                        _nos.discard('')
                        if not (_nos & _valid):   # 현재 스토어에 하나도 없으면 이전 스토어 상품
                            _stale.append(_p)
                    st.session_state['_clean_stale_ids'] = [_p['id'] for _p in _stale]
                    st.session_state['_clean_cur_n'] = len(_valid)
                    st.session_state['_clean_stale_sample'] = [
                        str(_p.get('costco_name') or '')[:45] for _p in _stale[:10]]
                    st.rerun()

            _stale_ids = st.session_state.get('_clean_stale_ids')
            if _stale_ids is not None:
                st.warning(f"현재 스토어 상품 {st.session_state.get('_clean_cur_n', 0)}개 확인 · "
                           f"**삭제 대상(이전 스토어) 상품: {len(_stale_ids)}개**")
                for _nm in st.session_state.get('_clean_stale_sample', []):
                    st.caption(f"• {_nm}")
                if len(_stale_ids) > 10:
                    st.caption(f"… 외 {len(_stale_ids) - 10}개")
                if _stale_ids:
                    _cc1, _cc2 = st.columns([1, 2])
                    if _cc1.button(f"🗑 {len(_stale_ids)}개 삭제 실행", key="clean_delete",
                                   type="primary", use_container_width=True):
                        _n = _del_prods(USERNAME, _stale_ids)
                        if callable(invalidate_data_cache):
                            invalidate_data_cache()
                        for _k in ('_clean_stale_ids', '_clean_cur_n', '_clean_stale_sample'):
                            st.session_state.pop(_k, None)
                        st.success(f"✅ 이전 스토어 상품 {_n}개 삭제 완료")
                        st.rerun()
                    _cc2.caption("⚠️ 되돌릴 수 없습니다. 현재 스토어 상품은 삭제되지 않습니다.")
                else:
                    st.success("✅ 삭제할 이전 스토어 상품이 없습니다. (모두 현재 스토어 소속)")

    # ── 관리자 전용: 카페24·바코드/사진 매입가 수정 ──
    if IS_ADMIN:
        # ── 🏷 바코드·사진으로 매입가(코스트코 가격) 수정 (매장용) ────────
        with st.expander("🏷 바코드·사진으로 매입가 수정 — 매장에서 스캔/촬영", expanded=False):
            _sp_all = cached_shared_products() if callable(cached_shared_products) else get_shared_products()
            _sp_by_pno = {str(s.get('product_no', '')).strip(): s for s in _sp_all
                          if str(s.get('product_no', '')).strip()}
            st.caption("코스트코 매장에서 가격표를 촬영하거나 바코드를 스캔/입력 → 그 상품의 **공유 매입가**를 수정합니다.")

            def _save_price(pno, sp, price, name=''):
                _cn = (sp or {}).get('costco_name') or name or pno
                _kw = (sp or {}).get('match_keyword') or _cn
                upsert_shared_store_price(costco_name=_cn, keyword=_kw, price=int(price),
                                          product_no=pno, updated_by=USERNAME)
                if callable(invalidate_data_cache):
                    invalidate_data_cache()

            _tab_cam, _tab_scan = st.tabs(["📷 사진/카메라 판독", "⌨ 바코드/번호 입력"])

            with _tab_cam:
                _pt_key = _gs('anthropic_api_key')
                if not _pt_key:
                    st.info("사진 판독은 설정 탭 > 🤖 AI 설정에 Anthropic 키가 필요합니다.")
                else:
                    _pt_img = st.file_uploader("가격표 사진 (모바일은 파일선택 시 '촬영' 가능)", key="pt_up")
                    if _pt_img is not None and st.button("🔎 가격표 판독", key="pt_read", type="primary"):
                        import ai_service
                        _b = _pt_img.getvalue(); _mt = getattr(_pt_img, 'type', None) or 'image/jpeg'
                        with st.spinner("가격표 판독 중..."):
                            _pinfo, _pe = ai_service.analyze_price_tag(_pt_key, _b, _mt)
                        st.session_state['_pt_read'] = None if _pe else _pinfo
                        if _pe:
                            st.error(f"판독 실패: {_pe}")
                    _r = st.session_state.get('_pt_read')
                    if _r:
                        _pno = _r['product_no']; _sp = _sp_by_pno.get(_pno)
                        st.markdown(f"**판독 결과** — 상품번호 `{_pno or '?'}` · 가격 **{fmt(_r['price'])}원** · "
                                    f"{str(_r['product_name'])[:34]}")
                        if not _pno:
                            st.warning("상품번호를 못 읽었습니다. 가격표(좌상단 번호)가 선명하게 나오도록 다시 촬영하세요.")
                        elif _sp:
                            st.markdown(f"공유DB: **{str(_sp['costco_name'])[:30]}** · 현재 매입가 "
                                        f"{fmt(int(_sp.get('unit_price') or 0))}원")
                            _np = st.number_input("새 매입가", value=int(_r['price']), min_value=0, step=100, key="pt_np")
                            if st.button("💾 매입가 수정", key="pt_save", type="primary"):
                                _save_price(_pno, _sp, _np)
                                st.session_state.pop('_pt_read', None)
                                st.success(f"✅ {str(_sp['costco_name'])[:20]} 매입가 → {fmt(_np)}원"); st.rerun()
                        else:
                            st.warning(f"공유DB에 상품번호 {_pno}가 없습니다 — 신규로 등록합니다.")
                            _nm = st.text_input("상품명", value=_r['product_name'], key="pt_addname")
                            _np = st.number_input("매입가", value=int(_r['price']), min_value=0, step=100, key="pt_addprice")
                            if st.button("➕ 공유DB 신규 등록", key="pt_add", type="primary") and _np > 0:
                                _save_price(_pno, None, _np, name=_nm)
                                st.session_state.pop('_pt_read', None)
                                st.success(f"✅ {_pno} 신규 등록 {fmt(_np)}원"); st.rerun()

            with _tab_scan:
                st.caption("블루투스 바코드 스캐너(키보드형)로 스캔하면 번호가 자동 입력됩니다. 없으면 직접 입력.")
                _sc = st.text_input("바코드 스캔 / 상품번호", key="pt_scan_no",
                                    placeholder="예: 713160 (스캔 시 자동입력)")
                _sc = "".join(ch for ch in (_sc or '') if ch.isdigit())
                if _sc:
                    _sp = _sp_by_pno.get(_sc)
                    if _sp:
                        st.markdown(f"**{str(_sp['costco_name'])[:34]}** · 현재 매입가 "
                                    f"{fmt(int(_sp.get('unit_price') or 0))}원")
                        _np = st.number_input("새 매입가", value=int(_sp.get('unit_price') or 0),
                                              min_value=0, step=100, key="pt_scan_np")
                        if st.button("💾 매입가 수정", key="pt_scan_save", type="primary"):
                            _save_price(_sc, _sp, _np)
                            st.success(f"✅ 매입가 → {fmt(_np)}원"); st.rerun()
                    else:
                        st.warning(f"공유DB에 상품번호 {_sc}가 없습니다 — 신규로 등록합니다.")
                        _nm = st.text_input("상품명", key="pt_scan_name")
                        _np = st.number_input("매입가", min_value=0, step=100, key="pt_scan_addprice")
                        if st.button("➕ 신규 등록", key="pt_scan_add", type="primary") and _np > 0:
                            _save_price(_sc, None, _np, name=_nm)
                            st.success(f"✅ {_sc} 신규 등록 {fmt(_np)}원"); st.rerun()

        # ── 🛒 카페24 상품 가격 수정 (라이브 스토어 반영) ────────────
        _cf_mall = _gs('cafe24_mall_id'); _cf_cid = _gs('cafe24_client_id'); _cf_tok = _gs('cafe24_access_token')
        if _cf_mall and _cf_cid and _cf_tok:
            with st.expander("🛒 카페24 상품 가격 수정 — 검색 후 새 가격 입력·변경 (라이브 반영)", expanded=False):
                import cafe24_api
                _cf_creds = {'mall_id': _cf_mall, 'client_id': _cf_cid,
                             'client_secret': _gs('cafe24_client_secret'),
                             'access_token': _cf_tok, 'refresh_token': _gs('cafe24_refresh_token'),
                             'expires_at': _gs('cafe24_token_expires_at')}
                def _cf_save(t):
                    set_setting(USERNAME, 'cafe24_access_token', t.get('access_token', ''))
                    set_setting(USERNAME, 'cafe24_refresh_token', t.get('refresh_token', ''))
                    set_setting(USERNAME, 'cafe24_token_expires_at', t.get('expires_at', ''))
                _cq1, _cq2 = st.columns([4, 1])
                _cf_q = _cq1.text_input("상품명 검색", key="cf_price_q",
                                        placeholder="상품명 일부 입력 (비우면 최근 등록 상품)",
                                        label_visibility="collapsed")
                if _cq2.button("🔎 조회", key="cf_price_search", use_container_width=True):
                    with st.spinner("카페24 상품 조회 중..."):
                        _prods, _perr = cafe24_api.search_products(_cf_creds, _cf_q, save_tokens=_cf_save)
                    st.session_state['_cf_prods'] = [] if _perr else (_prods or [])
                    if _perr:
                        st.error(f"조회 실패: {_perr}")
                _cf_prods = st.session_state.get('_cf_prods') or []
                _cf_flash = st.session_state.pop('_cf_price_flash', None)
                if _cf_flash:
                    st.success(_cf_flash)   # rerun 후에도 완료 메시지 유지
                if _cf_prods:
                    st.caption(f"{len(_cf_prods)}개 — 새 가격 입력 후 '가격변경'을 누르면 카페24 스토어에 즉시 반영됩니다.")
                    for _p in _cf_prods[:30]:
                        _pno = _p['product_no']
                        _pc1, _pc2, _pc3 = st.columns([4, 1.6, 1.2])
                        _pc1.markdown(
                            f"<div style='padding-top:6px'>{str(_p['product_name'])[:42]} "
                            f"<span style='color:#999;font-size:11px'>#{_pno} · 현재 {fmt(int(_p['price']))}원</span></div>",
                            unsafe_allow_html=True)
                        _new_price = _pc2.number_input("새가격", value=int(_p['price']), min_value=0,
                                                       step=100, key=f"cfp_{_pno}",
                                                       label_visibility="collapsed")
                        if _pc3.button("가격변경", key=f"cfpb_{_pno}", use_container_width=True):
                            with st.spinner("변경 중..."):
                                _ok, _uerr = cafe24_api.update_product_price(
                                    _cf_creds, _pno, _new_price, save_tokens=_cf_save)
                            if _ok:
                                _p['price'] = int(_new_price)   # 목록 캐시 갱신
                                st.session_state['_cf_price_flash'] = (
                                    f"✅ {str(_p['product_name'])[:20]} → {fmt(int(_new_price))}원 변경 완료")
                                st.rerun()
                            else:
                                _pc3.error("실패"); st.error(f"가격변경 실패: {_uerr}")
                else:
                    st.caption("상품명으로 검색하거나 빈 칸으로 조회하면 카페24 상품이 나타납니다.")

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
                    _saved_cat = _sp.get('naver_category_id') or _gs('naver_default_category') or ''
                    _saved_as  = _gs('naver_as_tel') or ''

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
                    _up = next((x for x in cached_merged(USERNAME) if x.get('shared_id') == _nreg_sp_id), {})
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
                                    # 사용자 products 테이블에 네이버 등록 정보 저장
                                    # - costco_name = 사용자가 입력한 네이버 상품명 (_reg_name)
                                    # - sale_price/shipping_fee = 네이버 등록 가격
                                    # - from_naver=1 → 네이버 등록 상품 필터에서 표시
                                    upsert_user_private(USERNAME, _nreg_kw,
                                                        _reg_name,
                                                        sale_price=int(_reg_price or 0),
                                                        shipping_fee=int(_reg_fee or 0),
                                                        naver_product_no=_npno,
                                                        from_naver=1,
                                                        naver_origin_pno=_npno)
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

    # ── 저장 완료 결과 요약 ─────────────────────────
    _ni_res = st.session_state.pop('_naver_import_result', None)
    if _ni_res:
        st.success(
            f"✅ **총 {_ni_res['total']}개 상품 저장 완료**  &nbsp;|&nbsp;  "
            f"✅ 판매중 {_ni_res['sale']}개  &nbsp;  "
            f"🟠 품절 {_ni_res['oos']}개  &nbsp;  "
            f"🚫 판매중지 {_ni_res['susp']}개"
        )
        st.info("🛍 네이버 등록 상품 필터로 이동했습니다.")

    # ── 네이버 스마트스토어 상품 가져오기 ─────────────────────────
    render_naver_import_section(USERNAME, api_id, api_secret, channel_seller_id, invalidate_data_cache)

    # ── 기존 네이버 상품 카테고리 일괄 적용 ───────────────────────
    _all_prods_for_cat = cached_merged(USERNAME)
    _no_cat_naver = [p for p in (_all_prods_for_cat or [])
                     if int(p.get('from_naver') or 0) == 1 and not p.get('category', '')]
    if _no_cat_naver:
        with st.expander(f"🏷 기존 네이버 상품 카테고리 일괄 적용 ({len(_no_cat_naver)}개 미분류)", expanded=False):
            st.caption(
                f"카테고리가 없는 네이버 상품 {len(_no_cat_naver)}개가 있습니다. "
                "상품명 키워드로 카테고리를 자동 추정해 저장합니다."
            )
            if st.button("🏷 상품명으로 카테고리 자동 분류", key="bulk_cat_btn", type="primary"):
                _cat_map = {}
                _ok = _skip = 0
                for _bp in _no_cat_naver:
                    _pid = _bp.get('private_id')
                    if not _pid:
                        _skip += 1
                        continue
                    _guessed = guess_category_from_name(_bp.get('costco_name', ''))
                    if _guessed:
                        _cat_map[_pid] = _guessed
                        _ok += 1
                    else:
                        _skip += 1
                if _cat_map:
                    bulk_update_category(USERNAME, _cat_map)
                    invalidate_data_cache()
                st.success(f"✅ {_ok}개 분류 완료, {_skip}개 키워드 미매칭 (미분류 유지)")
                st.rerun()

    # ── 네이버 ↔ 매장가격 상품 매칭 ───────────────────────────────
    _all_for_match = cached_merged(USERNAME)
    # 미연결 네이버 상품: from_naver=1 이고 shared_id가 없는 것 (user-only)
    _unlinked_naver = [p for p in (_all_for_match or [])
                       if int(p.get('from_naver') or 0) == 1
                       and p.get('shared_id') is None
                       and p.get('private_id') is not None]
    # 공유DB 상품 (매장가격 있는 것 우선 정렬)
    _shared_all = cached_shared_products() if callable(cached_shared_products) else get_shared_products()

    if _unlinked_naver and _shared_all:
        from utils import ProductMatcher
        
        # 속도 향상을 위해 공유DB 특징 미리 추출
        for sp in _shared_all:
            if 'features' not in sp:
                sp['features'] = ProductMatcher.extract_features(sp['costco_name'])

        _shared_names = {sp['id']: sp['costco_name'] for sp in _shared_all}

        with st.expander(f"🔗 네이버 상품 ↔ 매장 상품 매칭 ({len(_unlinked_naver)}개 미연결)", expanded=False):
            st.caption(
                "네이버에서 가져온 상품과 영수증으로 등록된 매장 상품을 연결합니다. "
                "네이버 상품명의 수량/용량이 매장 상품과 다르면 매칭에서 제외됩니다."
            )
            # 검색 필터
            _mq = st.text_input("🔍 네이버 상품명 검색", placeholder="상품명 일부 입력",
                                key="match_search_q", label_visibility="visible")
            _disp_list = _unlinked_naver
            if _mq.strip():
                _mql = _mq.strip().lower()
                _disp_list = [p for p in _unlinked_naver if
                              _mql in (p.get('naver_name') or p.get('costco_name', '')).lower()]
            st.caption(f"검색 결과: {len(_disp_list)}개")

            # 페이지네이션
            _MP = 20
            _mp_total = max(1, (len(_disp_list) + _MP - 1) // _MP)
            if 'match_page' not in st.session_state:
                st.session_state['match_page'] = 1
            if st.session_state['match_page'] > _mp_total:
                st.session_state['match_page'] = 1
            _mp_cur = st.session_state['match_page']
            _page_items = _disp_list[(_mp_cur - 1) * _MP: _mp_cur * _MP]

            for _np in _page_items:
                _nname = _np.get('naver_name') or _np.get('costco_name', '')
                _pid   = _np['private_id']
                _npno  = _np.get('naver_origin_pno') or _np.get('naver_product_no') or ''
                
                # 네이버 상품명 특징 추출 (1회)
                _nf = ProductMatcher.extract_features(_nname)

                # 이름 유사도로 상위 10개 제안 (수량 불일치 시 0.1 페널티 적용됨)
                _matches = []
                for sp in _shared_all:
                    si = ProductMatcher.get_score_from_features(_nf, sp['features'])
                    if si['total'] > 0.1:  # 최소 임계값 (0.1 이하는 수량 mismatch 가능성 높음)
                        _matches.append((sp, si['total']))
                
                _matches.sort(key=lambda x: x[1], reverse=True)
                _top10_tuples = _matches[:10]
                _top10 = [t[0] for t in _top10_tuples]
                
                _best = _top10[0] if _top10 and _top10_tuples[0][1] >= 0.3 else None

                _mc1, _mc2, _mc3 = st.columns([4, 4, 1])
                _pno_tag = f"<span style='color:#999;font-size:11px'> #{_npno}</span>" if _npno else ''
                _mc1.markdown(
                    f"<div style='padding:6px 8px;background:#f0f7ff;border-radius:6px;"
                    f"border-left:3px solid #3498db;font-size:13px'>"
                    f"🛍 <b>{_nname}</b>{_pno_tag}</div>",
                    unsafe_allow_html=True
                )

                # 셀렉트박스 옵션: 유사도 상위10
                _sel_opts = ["— 선택 안 함 —"] + [
                    f"{sp['costco_name']} (#{sp['id']}) [점수:{int(sc*100)}%]" 
                    for sp, sc in _top10_tuples
                ]
                _default_idx = 1 if _best else 0
                _sel = _mc2.selectbox(
                    "연결할 매장 상품",
                    _sel_opts,
                    index=_default_idx,
                    key=f"match_sel_{_pid}",
                    label_visibility="collapsed"
                )

                if _mc3.button("🔗", key=f"match_btn_{_pid}", use_container_width=True,
                               help="연결"):
                    if _sel and _sel != "— 선택 안 함 —":
                        try:
                            _linked_sid = int(_sel.split("(#")[-1].split(")")[0])
                            link_naver_to_shared(USERNAME, _pid, _linked_sid)
                            invalidate_data_cache()
                            st.success(f"✅ 연결 완료: {_nname} → {_shared_names.get(_linked_sid, '')}")
                            st.rerun()
                        except Exception as _le:
                            st.error(f"연결 실패: {_le}")

            # 페이지 이동 버튼
            if _mp_total > 1:
                _pa, _pb, _pc = st.columns([1, 4, 1])
                if _pa.button("◀ 이전", key="match_prev", disabled=(_mp_cur <= 1)):
                    st.session_state['match_page'] = _mp_cur - 1
                    st.rerun()
                _pb.caption(f"페이지 {_mp_cur} / {_mp_total}")
                if _pc.button("다음 ▶", key="match_next", disabled=(_mp_cur >= _mp_total)):
                    st.session_state['match_page'] = _mp_cur + 1
                    st.rerun()

    # ── 이미 연결된 상품 연결 해제 버튼 (네이버 등록 필터에서만 표시) ──
    # (아래 제품 목록의 각 행 수정 폼에서 처리)

    products = cached_merged(USERNAME)
    if products:
        # ── 분류 버튼 (카운트 포함) ──
        _cnt_online = sum(1 for p in products if (p.get('price_type') or '') == '온라인')
        _cnt_naver  = sum(1 for p in products
                          if int(p.get('from_naver') or 0) == 1
                          or (p.get('naver_product_no') and str(p.get('naver_product_no', '')).strip())
                          or (p.get('naver_origin_pno') and str(p.get('naver_origin_pno', '')).strip()))
        _cnt_oos    = sum(1 for p in products if (p.get('status') or 'SALE').upper() in ('OUTOFSTOCK', 'SOLD_OUT', 'SOLDOUT'))
        _cnt_susp   = sum(1 for p in products if (p.get('status') or 'SALE').upper() in ('SUSPENSION', 'STOP', 'PAUSE', 'CLOSE', 'PROHIBITION'))
        _cnt_total  = len(products)
        _filter_opts = [
            f"전체 ({_cnt_total})",
            f"🌐 코스트코 온라인 ({_cnt_online})",
            f"🛍 네이버 등록 상품 ({_cnt_naver})",
            f"🟠 품절 ({_cnt_oos})",
            f"🚫 판매중지 ({_cnt_susp})",
        ]
        _prev_filter = st.session_state.get('_db_filter_prev', _filter_opts[0])
        _db_filter = st.radio(
            "상품 분류", _filter_opts, horizontal=True,
            key="db_product_filter", label_visibility="collapsed"
        ) or _filter_opts[0]
        # 분류 변경 시 모든 탭 페이지 리셋
        if _db_filter != _prev_filter:
            for _rk in list(st.session_state.keys()):
                if _rk.startswith('ppage_t') or _rk.startswith('db_pills_t'):
                    st.session_state[_rk] = 1
            st.session_state['_db_filter_prev'] = _db_filter
        # 라벨에서 카운트 부분 제거하여 핵심 키워드로 분기
        _is_filter_online = "코스트코 온라인" in _db_filter
        _is_filter_naver  = "네이버 등록" in _db_filter
        _is_filter_oos    = "품절" in _db_filter
        _is_filter_susp   = "판매중지" in _db_filter
        if _is_filter_online:
            products = [p for p in products if (p.get('price_type') or '') == '온라인']
        elif _is_filter_naver:
            products = [p for p in products
                        if int(p.get('from_naver') or 0) == 1
                        or (p.get('naver_product_no') and str(p.get('naver_product_no', '')).strip())
                        or (p.get('naver_origin_pno') and str(p.get('naver_origin_pno', '')).strip())]
        elif _is_filter_oos:
            products = [p for p in products if (p.get('status') or 'SALE').upper() in ('OUTOFSTOCK', 'SOLD_OUT', 'SOLDOUT')]
        elif _is_filter_susp:
            products = [p for p in products if (p.get('status') or 'SALE').upper() in ('SUSPENSION', 'STOP', 'PAUSE', 'CLOSE', 'PROHIBITION')]

        st.caption(f"{_db_filter} 표시 중 — {len(products)}개")

        # ── 카테고리 탭 ──
        _all_cats = sorted({p.get('category', '') for p in products if p.get('category', '')})
        _has_uncat = any(not p.get('category', '') for p in products)
        _tab_names = ["전체"] + _all_cats + (["(미분류)"] if _has_uncat else [])
        _db_tabs = st.tabs(_tab_names)

        _HDR_DATA_LABELS = ['상품번호', '상품명(네이버/코스트코)', '매장가🔒', '온라인가🔒', '소분🔒',
                            '판매가(네이버)✏️', '고객배송비✏️', '업데이트']
        _HDR_DATA_WIDTHS = [130, 330, 95, 95, 60, 110, 100, 90]

        for _ti, (_dtab, _tname) in enumerate(zip(_db_tabs, _tab_names)):
            with _dtab:
                # 탭별 상품 필터
                if _tname == "전체":
                    _filtered = list(products)
                elif _tname == "(미분류)":
                    _filtered = [p for p in products if not p.get('category', '')]
                else:
                    _filtered = [p for p in products if p.get('category', '') == _tname]

                # 검색
                _sc, _ = st.columns([2, 3])
                _sq = _sc.text_input("\U0001f50d 검색", placeholder="상품명 또는 상품번호",
                                     key=f"psearch_t{_ti}")
                if _sq:
                    _sql = _sq.strip().lower()
                    _filtered = [p for p in _filtered if
                        _sql in p.get('costco_name', '').lower() or
                        _sql in str(p.get('product_no', '')) or
                        _sql in (p.get('naver_name') or '').lower() or
                        _sql in str(p.get('naver_origin_pno') or '') or
                        _sql in str(p.get('naver_product_no') or '')]

                _tot  = len(_filtered)
                _pp   = 10  # 페이지당 10개로 축소 (속도 최적화)
                _tp   = max(1, (_tot + _pp - 1) // _pp)
                _pkey = f"ppage_t{_ti}"
                if _pkey not in st.session_state:
                    st.session_state[_pkey] = 1
                if st.session_state[_pkey] > _tp:
                    st.session_state[_pkey] = 1
                _pg = st.session_state[_pkey]

                _si2 = (_pg - 1) * _pp
                _ei2 = min(_si2 + _pp, _tot)
                _page_prods = _filtered[_si2:_ei2]

                st.caption(f"총 {_tot}개 제품 (페이지 {_pg}/{_tp})")

                # 헤더 — 데이터 행과 동일한 st.columns([10, 2]) 구조로 정렬
                _hdr_disp, _hdr_btn = st.columns([10, 2])
                _hdr_labels = list(_HDR_DATA_LABELS)
                if _is_filter_naver:
                    _hdr_labels[0] = '네이버번호 / 코스트코번호'
                    _hdr_labels[1] = '네이버 상품명'
                _hdr_cells = ''.join(
                    f'<th style="width:{w}px;text-align:left;padding:5px 8px;font-size:13px;'
                    f'color:#555;background:#fafafa;border-bottom:1px solid #dee2e6">{lbl}</th>'
                    for lbl, w in zip(_hdr_labels, _HDR_DATA_WIDTHS)
                )
                _hdr_disp.markdown(
                    f'<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
                    f'<thead><tr>{_hdr_cells}</tr></thead></table>',
                    unsafe_allow_html=True
                )
                _hb1, _hb2, _hb3 = _hdr_btn.columns(3)
                _hb1.markdown("<div style='font-size:12px;color:#555;text-align:center'>수정</div>", unsafe_allow_html=True)
                _hb2.markdown("<div style='font-size:12px;color:#555;text-align:center'>🛍등록</div>", unsafe_allow_html=True)
                _hb3.markdown("<div style='font-size:12px;color:#555;text-align:center'>삭제</div>", unsafe_allow_html=True)

                editing_kw  = st.session_state.get('editing_product_kw')
                editing_tab = st.session_state.get('editing_product_tab', 0)

                for _idx, p in enumerate(_page_prods):
                    kw        = p['match_keyword']
                    # 위젯 key 충돌 방지용 unique row id (같은 match_keyword 다중 행 대비)
                    _uid      = p.get('private_id') or p.get('shared_id') or _idx
                    is_shared = p.get('shared_id') is not None
                    sq_val    = int(p.get('split_qty', 1) or 1)
                    fee_val   = int(p.get('shipping_fee', 0) or 0)
                    sale_val  = int(p.get('sale_price', 0) or 0)
                    # 가격 분리 상태: costco_no_display 있으면 분리됨 (product_no는 'BASE (N)' suffix)
                    _split_disp_loop = (p.get('costco_no_display') or '').strip()
                    _is_split_loop   = bool(_split_disp_loop)

                    if editing_kw == kw and editing_tab == _ti:
                        st.markdown(
                            "<div style='background:#eaf4fb;border:1px solid #aed6f1;border-radius:6px;"
                            "padding:10px 12px;margin:4px 0'>",
                            unsafe_allow_html=True
                        )
                        if is_shared:
                            st.caption(f"🔗 공유 제품 — 매입가·상품명은 읽기전용 (관리자 탭에서 수정) | 소분·판매가·배송비는 직접 수정 가능")
                            fc = st.columns([3.0, 1.0, 1.3, 1.3, 1.0, 0.8])
                            fc[0].markdown(
                                f"**{p.get('naver_name') or p['costco_name']}**  "
                                f"<span style='color:#888;font-size:13px'>({p.get('product_no','') or '-'})</span><br>"
                                f"<span style='color:#888;font-size:12px'>매입가: {fmt(p.get('unit_price',0))}원</span>",
                                unsafe_allow_html=True
                            )
                            e_sq   = fc[1].number_input("소분", value=sq_val, min_value=1, max_value=20,
                                                        key=f"e_sq_t{_ti}_{kw}_{_uid}", label_visibility="visible")
                            e_sale = fc[2].number_input("판매가(네이버)", value=sale_val, min_value=0, step=100,
                                                        key=f"e_sale_t{_ti}_{kw}_{_uid}", label_visibility="visible")
                            e_fee  = fc[3].number_input("고객배송비 (0=무료)", value=fee_val, min_value=0, step=100,
                                                        key=f"e_fee_t{_ti}_{kw}_{_uid}", label_visibility="visible")
                            _cur_ncat = p.get('naver_category_id') or ''
                            e_ncat = st.text_input(
                                "네이버 카테고리 ID (제품별 고정, 비워두면 카테고리 기본값 사용)",
                                value=_cur_ncat, placeholder="예: 50000803",
                                key=f"e_ncat_t{_ti}_{kw}_{_uid}",
                            )
                            if fc[4].button("✅ 저장", key=f"e_save_t{_ti}_{kw}_{_uid}",
                                            use_container_width=True, type="primary"):
                                upsert_user_private(USERNAME, kw, p['costco_name'],
                                                    sale_price=e_sale, shipping_fee=e_fee,
                                                    split_qty=e_sq)
                                if p.get('shared_id'):
                                    _ca_edit = sqlite3.connect(AUTH_DB)
                                    _ca_edit.execute(
                                        "UPDATE shared_products SET naver_category_id=? WHERE id=?",
                                        (e_ncat.strip(), p['shared_id'])
                                    )
                                    _ca_edit.commit(); _ca_edit.close()
                                st.session_state.pop('editing_product_kw', None)
                                st.session_state.pop('editing_product_tab', None)
                                st.rerun()
                            if fc[5].button("✖ 취소", key=f"e_cancel_t{_ti}_{kw}_{_uid}",
                                            use_container_width=True):
                                st.session_state.pop('editing_product_kw', None)
                                st.session_state.pop('editing_product_tab', None)
                                st.rerun()
                        else:
                            # 가격 분리된 행: 해제 옵션을 폼 상단에 한 줄 추가
                            if _is_split_loop:
                                _uc1, _uc2 = st.columns([6, 1])
                                _uc1.warning(
                                    f"🔒 가격 분리됨 — 코스트코 번호 매칭에서 제외 (원본: {_split_disp_loop})"
                                )
                                if _uc2.button("🔓 해제", key=f"unlock_t{_ti}_{kw}_{_uid}",
                                               use_container_width=True,
                                               help="이 행을 원래 코스트코 번호 매칭으로 복귀"):
                                    pid_un = p.get('private_id')
                                    if pid_un:
                                        conn_u = get_user_db(USERNAME)
                                        conn_u.execute(
                                            "UPDATE products SET product_no=?, costco_no_display='' WHERE id=?",
                                            (_split_disp_loop, pid_un)
                                        )
                                        conn_u.commit(); conn_u.close()
                                        invalidate_data_cache()
                                        st.success(f"✅ 분리 해제 — {_split_disp_loop} 매칭 복귀")
                                        st.session_state.pop('editing_product_kw', None)
                                        st.session_state.pop('editing_product_tab', None)
                                        st.rerun()

                            fc = st.columns([0.9, 4.6, 1.3, 0.8, 1.2, 1.1, 1.0, 0.8])
                            pid_legacy = p.get('private_id')
                            e_pno  = fc[0].text_input("상품번호", value=p.get('product_no', ''),
                                                      key=f"e_pno_t{_ti}_{kw}_{_uid}",
                                                      label_visibility="collapsed", placeholder="상품번호")
                            e_name = fc[1].text_input("상품명", value=p['costco_name'],
                                                      key=f"e_name_t{_ti}_{kw}_{_uid}",
                                                      label_visibility="collapsed")
                            e_price= fc[2].number_input("매입가", value=int(p.get('unit_price', 0) or 0),
                                                        step=100, key=f"e_price_t{_ti}_{kw}_{_uid}",
                                                        label_visibility="collapsed")
                            e_sq   = fc[3].number_input("소분", value=sq_val, min_value=1, max_value=20,
                                                        key=f"e_sq_t{_ti}_{kw}_{_uid}",
                                                        label_visibility="collapsed")
                            e_sale = fc[4].number_input("판매가", value=sale_val, min_value=0, step=100,
                                                        key=f"e_sale2_t{_ti}_{kw}_{_uid}",
                                                        label_visibility="collapsed")
                            e_fee  = fc[5].number_input("배송비", value=fee_val, min_value=0, step=100,
                                                        key=f"e_fee2_t{_ti}_{kw}_{_uid}",
                                                        label_visibility="collapsed")
                            if fc[6].button("✅ 저장", key=f"e_save2_t{_ti}_{kw}_{_uid}",
                                            use_container_width=True, type="primary"):
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
                                st.session_state.pop('editing_product_tab', None)
                                st.rerun()
                            if fc[7].button("✖", key=f"e_cancel2_t{_ti}_{kw}_{_uid}",
                                            use_container_width=True):
                                st.session_state.pop('editing_product_kw', None)
                                st.session_state.pop('editing_product_tab', None)
                                st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                    else:
                        # ── 일반 표시 행 (HTML 일괄 렌더 + 액션 버튼만 분리) ──
                        _store  = int(p.get('store_price') or 0)
                        _online = int(p.get('online_price') or 0)
                        if _store == 0 and _online == 0 and p.get('unit_price'):
                            if (p.get('price_type') or '매장') == '온라인':
                                _online = int(p.get('unit_price') or 0)
                            else:
                                _store = int(p.get('unit_price') or 0)
                        # 온라인 가격이 있으면 매장가는 숨김 (사용자 요청: 온라인 등록 상품은 온라인 가격만)
                        if _online > 0:
                            _store = 0
                        store_disp  = (f"<span style='font-weight:600;color:#2e7d32'>{fmt(_store)}원</span>"
                                       if _store > 0 else "<span style='color:#ccc'>—</span>")
                        online_disp = (f"<span style='font-weight:600;color:#1565c0'>🌐 {fmt(_online)}원</span>"
                                       if _online > 0 else "<span style='color:#ccc'>—</span>")
                        sq_disp     = (f"<span style='color:#1565C0;font-weight:bold'>÷{sq_val}</span>"
                                       if sq_val > 1 else "<span style='color:#888'>-</span>")
                        sale_disp   = (f"<span style='color:#1a237e;font-weight:600'>{sale_val:,}원</span>"
                                       if sale_val > 0 else "<span style='color:#ccc'>-</span>")
                        fee_disp    = ("<span style='color:#2e7d32;font-weight:600'>무료</span>"
                                       if fee_val == 0 else f"<span style='color:#555'>{fee_val:,}원</span>")
                        updated_str = (p.get('shared_updated_at') or '')[:10]

                        _thumb = p.get('image_url', '')
                        _img_html = (
                            f"<img src='{_thumb}' width='40' height='40' "
                            f"style='object-fit:cover;border-radius:4px;vertical-align:middle;margin-right:6px;border:1px solid #eee'>"
                            if _thumb else ""
                        )
                        _ps = (p.get('status') or 'SALE').upper()
                        _is_oos  = _ps in ('OUTOFSTOCK', 'SOLD_OUT', 'SOLDOUT')
                        _is_susp = _ps in ('SUSPENSION', 'STOP', 'PAUSE', 'CLOSE', 'PROHIBITION')
                        _badge = ""
                        if _is_oos:
                            _badge = " <span style='color:#f39c12;font-size:11px;font-weight:600;background:#fff5e6;padding:1px 5px;border-radius:3px;border:1px solid #f39c12'>🟠 품절</span>"
                        elif _is_susp:
                            _badge = " <span style='color:#e74c3c;font-size:11px;font-weight:600;background:#fee;padding:1px 5px;border-radius:3px;border:1px solid #e74c3c'>🚫 판매중지</span>"
                        _name_color = "#999" if (_is_oos or _is_susp) else "#222"
                        # 상품명: 네이버 등록 필터에서는 naver_name 우선
                        _naver_name = p.get('naver_name') or ''
                        if _is_filter_naver:
                            if _naver_name:
                                _display_name = _naver_name
                            else:
                                # naver_name 없음 — 코스트코명 + 배지 표시
                                _display_name = (
                                    f"{p['costco_name']} "
                                    f"<span style='color:#e67e22;font-size:11px;background:#fff3e0;"
                                    f"padding:1px 5px;border-radius:3px;border:1px solid #e67e22'>"
                                    f"네이버명 없음</span>"
                                )
                        else:
                            _display_name = _naver_name if _naver_name else p['costco_name']
                            if _naver_name and _naver_name != p['costco_name']:
                                _display_name = f"{_naver_name} <span style='color:#aaa;font-size:12px'>({p['costco_name']})</span>"

                        # 분리된 행: costco_no_display 있음 (product_no는 'BASE (N)' 형태)
                        _split_disp = (p.get('costco_no_display') or '').strip()
                        _is_split   = bool(_split_disp)
                        _split_badge = (
                            "<br><span style='color:#c0392b;font-size:10px;font-weight:600;"
                            "background:#fdeded;padding:1px 5px;border-radius:3px;border:1px solid #f5b7b1' "
                            "title='가격 수정으로 분리된 행 — 코스트코 번호 매칭에서 제외됨'>🔒 가격분리</span>"
                        ) if _is_split else ""

                        # 상품번호: 네이버 등록 필터에서는 네이버번호 + 코스트코번호 함께 표시
                        if _is_filter_naver:
                            _naver_no  = p.get('naver_product_no') or '-'
                            _costco_no = _split_disp if _is_split else (p.get('product_no', '') or '-')
                            _no_disp   = (
                                f"{_naver_no}"
                                f"<br><span style='color:#aaa;font-size:11px'>{_costco_no}</span>"
                                f"{_split_badge}"
                            )
                        else:
                            _costco_no = _split_disp if _is_split else (p.get('product_no', '') or '-')
                            # 네이버번호(channel 우선) 있으면 코스트코번호 아래 함께 표시
                            _nv_no = (p.get('naver_channel_pno') or p.get('naver_origin_pno') or '').strip()
                            _nv_line = (f"<br><span style='color:#3498db;font-size:11px' title='네이버 스토어 상품번호'>🛍 {_nv_no}</span>"
                                        if _nv_no else "")
                            _no_disp = f"{_costco_no}{_nv_line}{_split_badge}"

                        # 행 표시(8개 셀)을 HTML 한 덩어리로 + 액션 3 버튼은 별도 컬럼
                        row_cell_style = "padding:6px 8px;border-bottom:1px solid #f5f5f5;font-size:14px;vertical-align:middle;overflow:hidden;text-overflow:ellipsis"
                        _row_html = (
                            f'<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
                            f'<tr>'
                            f'<td style="{row_cell_style};width:130px;color:#888">{_no_disp}</td>'
                            f'<td style="{row_cell_style};width:380px;color:{_name_color}">{_img_html}{_display_name}{_badge}</td>'
                            f'<td style="{row_cell_style};width:95px">{store_disp}</td>'
                            f'<td style="{row_cell_style};width:95px">{online_disp}</td>'
                            f'<td style="{row_cell_style};width:60px">{sq_disp}</td>'
                            f'<td style="{row_cell_style};width:110px">{sale_disp}</td>'
                            f'<td style="{row_cell_style};width:100px">{fee_disp}</td>'
                            f'<td style="{row_cell_style};width:90px;color:#888;font-size:12px">{updated_str}</td>'
                            f'</tr></table>'
                        )

                        disp_col, btn_col = st.columns([10, 2])
                        disp_col.markdown(_row_html, unsafe_allow_html=True)
                        bc1, bc2, bc3 = btn_col.columns(3)
                        if bc1.button("✏️", key=f"edit_btn_t{_ti}_{kw}_{_uid}", use_container_width=True):
                            st.session_state['editing_product_kw']  = kw
                            st.session_state['editing_product_tab'] = _ti
                            st.rerun()
                        _n_registered = bool(p.get('naver_product_no'))
                        _n_label = "✅" if _n_registered else "🛍"
                        if bc2.button(_n_label, key=f"nreg_btn_t{_ti}_{kw}_{_uid}", use_container_width=True,
                                      help="네이버 등록" if not _n_registered else f"등록됨 ({p.get('naver_product_no')})"):
                            st.session_state['naver_reg_sp_id'] = p.get('shared_id')
                            st.session_state['naver_reg_kw'] = kw
                            st.rerun()
                        if bc3.button("🗑", key=f"del_btn_t{_ti}_{kw}_{_uid}", use_container_width=True):
                            pid_del = p.get('private_id')
                            if pid_del:
                                conn_u = get_user_db(USERNAME)
                                conn_u.execute("DELETE FROM products WHERE id=?", (pid_del,))
                                conn_u.commit(); conn_u.close()
                                invalidate_data_cache()
                            st.session_state.pop('editing_product_kw', None)
                            st.rerun()

                # ── 페이지 네비게이션 ──
                if _tp > 1:
                    # 현재 페이지 중심으로 9개 윈도우, 경계에서 한쪽으로 붙임
                    _half = 4
                    _ds = max(1, _pg - _half)
                    _de = _ds + 8
                    if _de > _tp:
                        _de = _tp
                        _ds = max(1, _de - 8)
                    _db_pnums = list(range(_ds, _de + 1))
                    # 9슬롯 미만이면 빈 슬롯으로 채워 항상 9개 유지
                    while len(_db_pnums) < 9:
                        _db_pnums.append(None)

                    # ── 핵심: 모든 요소를 st.button으로 통일 ──
                    # 이전 방식(button/markdown 혼용)은 DOM 구조가 달라 레이아웃이 계속 틀어짐
                    # 전부 st.button + use_container_width=True 로 통일하면 구조가 동일
                    # 현재 페이지 = type="primary", 비활성 = disabled=True
                    st.markdown("""<style>
[data-testid="stHorizontalBlock"]:has(.sp-pg-marker) { gap: 0 !important; }
[data-testid="stHorizontalBlock"]:has(.sp-pg-marker) [data-testid="stButton"] {
    padding: 0 !important; margin: 0 !important;
}
/* 마커 wrapper를 normal flow에서 완전히 제거 → 이전 버튼 밀림 방지 */
[data-testid="stMarkdown"]:has(.sp-pg-marker),
[data-testid="stElementContainer"]:has(.sp-pg-marker),
.element-container:has(.sp-pg-marker) {
    position: absolute !important;
    width: 0 !important; height: 0 !important;
    overflow: hidden !important;
    margin: 0 !important; padding: 0 !important;
    opacity: 0 !important; pointer-events: none !important;
}
[data-testid="stHorizontalBlock"]:has(.sp-pg-marker) [data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: #333 !important;
    font-size: 14px !important;
    font-weight: 400 !important;
}
[data-testid="stHorizontalBlock"]:has(.sp-pg-marker) [data-testid="stBaseButton-secondary"]:hover:not(:disabled) {
    color: #e74c3c !important;
    background: transparent !important;
}
[data-testid="stHorizontalBlock"]:has(.sp-pg-marker) [data-testid="stBaseButton-secondary"]:disabled {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: #ccc !important;
    opacity: 1 !important;
}
[data-testid="stHorizontalBlock"]:has(.sp-pg-marker) [data-testid="stBaseButton-primary"] {
    background: transparent !important;
    border: 1.5px solid #e74c3c !important;
    box-shadow: none !important;
    color: #e74c3c !important;
    font-size: 14px !important;
    font-weight: 600 !important;
}
[data-testid="stHorizontalBlock"]:has(.sp-pg-marker) [data-testid="stBaseButton-primary"]:hover {
    background: transparent !important;
}
.sp-pg-marker { display: none !important; }
</style>""", unsafe_allow_html=True)

                    _PSLOTS = 9
                    _fcs = st.columns([1.5] + [1] * _PSLOTS + [1.5, 8], vertical_alignment="center")
                    _fcs[0].markdown('<div class="sp-pg-marker"></div>', unsafe_allow_html=True)

                    with _fcs[0]:
                        if _pg > 1:
                            if st.button('‹ 이전', key=f'db_prev_t{_ti}', use_container_width=True):
                                st.session_state[_pkey] = _pg - 1
                                st.rerun()
                        else:
                            st.button('‹ 이전', key=f'db_prev_t{_ti}',
                                      use_container_width=True, disabled=True)

                    for _si in range(_PSLOTS):
                        with _fcs[1 + _si]:
                            _dp = _db_pnums[_si]
                            if _dp is None:
                                pass
                            elif _dp == _pg:
                                st.button(str(_dp), key=f'db_pg_{_ti}_{_dp}',
                                          use_container_width=True, type="primary")
                            else:
                                if st.button(str(_dp), key=f'db_pg_{_ti}_{_dp}',
                                             use_container_width=True):
                                    st.session_state[_pkey] = _dp
                                    st.rerun()

                    with _fcs[1 + _PSLOTS]:
                        if _pg < _tp:
                            if st.button('다음 ›', key=f'db_next_t{_ti}', use_container_width=True):
                                st.session_state[_pkey] = _pg + 1
                                st.rerun()
                        else:
                            st.button('다음 ›', key=f'db_next_t{_ti}',
                                      use_container_width=True, disabled=True)
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
