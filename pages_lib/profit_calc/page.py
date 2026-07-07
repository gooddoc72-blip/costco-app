"""💰 수익 계산 페이지 — pages_lib 자동 추출."""
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
    set_naver_origin_pno,
    get_product_detail,
    save_daily_orders, get_daily_orders, save_order_history, search_order_history,
    save_profit_settlements, get_profit_settlements, get_settlement_overrides_map, save_settlement_override,
    get_actual_settlements_map, get_coupang_settled_map,
    save_receipt_items, get_recent_receipt_items, delete_receipt_items_by_date, get_receipt_dates,
    get_date_range_stats, get_monthly_stats, get_product_ranking, get_saved_dates,
    get_dashboard_kpi, get_daily_profit_trend, get_week_best_products,
    get_price_history_monthly, save_price_changes_to_history, get_price_change_history,
    add_keyword_tracking, get_keyword_trackings, delete_keyword_tracking,
    save_rank_result, get_rank_history, get_latest_ranks,
    get_daily_ranks_in_month, get_yearly_rank_history, delete_trackings_bulk,
    get_rank_drops,
    get_dispatched_orders_with_details,
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


_cached_saved_dates = None
_cached_daily_orders = None

def _set_cache_helpers(shared_fn, user_fn, merged_fn, invalidate_fn,
                       cached_saved_dates=None, cached_daily_orders=None):
    global cached_shared_products, cached_user_products, cached_merged, invalidate_data_cache
    global _cached_saved_dates, _cached_daily_orders
    cached_shared_products = shared_fn
    cached_user_products = user_fn
    cached_merged = merged_fn
    invalidate_data_cache = invalidate_fn
    if cached_saved_dates is not None:
        _cached_saved_dates = cached_saved_dates
    if cached_daily_orders is not None:
        _cached_daily_orders = cached_daily_orders


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    """💰 수익 계산 탭 렌더링."""
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    st.header("💰 수익 계산")
    _ship_default = int(_gs('shipping_cost') or 1800)
    _box_default  = int(_gs('box_cost') or 300)
    if _ship_default > 100000: _ship_default = 1800
    if _box_default  > 10000:  _box_default  = 300

    _ship_fee_rate_info = float(_gs('naver_ship_fee_commission_rate') or 4.0)

    # 택배비 / 박스비 인라인 수정 (설정 탭 이동 없이 직접 변경)
    _fc1, _fc2, _fc3 = st.columns([1.3, 1.3, 4])
    shipping_cost = int(_fc1.number_input(
        "📦 기본 택배비 (원)", value=_ship_default, min_value=0, step=100,
        key="profit_ship_cost",
        help="이 화면에서만 임시 변경. 기본값은 설정 탭에서 수정하세요."
    ))
    box_cost = int(_fc2.number_input(
        "📦 박스비 (원)", value=_box_default, min_value=0, step=100,
        key="profit_box_cost",
        help="이 화면에서만 임시 변경. 기본값은 설정 탭에서 수정하세요."
    ))
    _fc3.info(
        f"📐 수익 = (정산예정 + 고객배송비) − (구입가 + 택배비 **{fmt(shipping_cost)}** + 박스비 **{fmt(box_cost)}**)  "
        f"· 고객배송비는 수수료 차감 없이 전액 정산 (수수료 5.5%는 판매가에만 적용 → 정산예정에 반영)"
    )

    col_date, _col_refresh, _col_clean, _ = st.columns([1.5, 1, 1.5, 2.5])
    with col_date:
        calc_date = st.date_input("계산할 주문 날짜 선택", value=datetime.today() - timedelta(days=1))
        calc_date_str = calc_date.strftime("%Y-%m-%d")
    with _col_refresh:
        st.write(""); st.write("")
        if st.button("📋 조회", key="profit_force_refresh",
                     use_container_width=True,
                     help="날짜를 선택한 후 클릭하여 해당 날짜 주문 조회"):
            try:
                if invalidate_data_cache:
                    invalidate_data_cache()
            except Exception:
                pass
            st.session_state.pop('_pcalc_match_cache', None)
            st.rerun()
    with _col_clean:
        st.write(""); st.write("")
        # 확인 다이얼로그: 첫 클릭 → 경고 표시, 두 번째 클릭 → 실제 삭제
        _confirm_key = f"_clean_confirm_{calc_date_str}"
        _is_confirming = st.session_state.get(_confirm_key, False)
        _btn_label = "⚠️ 정말 삭제? (한번더)" if _is_confirming else "🗑 이 날짜 정리"
        if st.button(_btn_label, key="profit_clean_date",
                     use_container_width=True,
                     type="primary" if _is_confirming else "secondary",
                     help=f"{calc_date_str} daily_orders 통째 삭제 (확인용 더블클릭)."):
            if not _is_confirming:
                # 첫 클릭 → 확인 모드 진입
                st.session_state[_confirm_key] = True
                st.rerun()
            else:
                # 두 번째 클릭 → 실제 삭제
                try:
                    _cn = get_user_db(USERNAME)
                    _cur = _cn.execute("DELETE FROM daily_orders WHERE order_date=?", (calc_date_str,))
                    _deleted = _cur.rowcount
                    _cn.commit()
                    _cn.close()
                    if invalidate_data_cache:
                        invalidate_data_cache()
                    st.session_state.pop('_pcalc_match_cache', None)
                    st.session_state.pop(_confirm_key, None)
                    st.success(f"✅ {calc_date_str} daily_orders {_deleted}건 삭제됨")
                    st.rerun()
                except Exception as _de:
                    st.error(f"삭제 실패: {_de}")

    # ── 매칭/구입가 전체 초기화 (빠른 액션) ─────────────────────
    _kw_clear_col1, _kw_clear_col2 = st.columns([1.5, 4])
    if _kw_clear_col1.button("🗑 매칭/구입가 전체 초기화", key="clear_all_overrides",
                              help="모든 행의 수동 키워드 + 수동 구입가 + 영수증선택 + 위젯 state까지 완전 초기화 → 자동 매칭으로 재계산"):
        # 1) overrides 딕셔너리 초기화
        st.session_state['kw_overrides'] = {}
        st.session_state['cost_overrides'] = {}
        st.session_state['receipt_pick'] = {}
        # 2) 위젯 state 키 일괄 삭제 (k_X, c_X, _buf_k_X, _buf_c_X, sel_p_X, rq_X)
        _keys_to_remove = [
            k for k in list(st.session_state.keys())
            if k.startswith(('k_', 'c_', '_buf_k_', '_buf_c_', 'sel_p_', 'rq_', 'ship_', 'box_'))
        ]
        for _k in _keys_to_remove:
            try:
                del st.session_state[_k]
            except KeyError:
                pass
        # 매칭 캐시도 삭제 → 자동 재매칭 강제
        for _k in ('_pcalc_match_cache', '_pcalc_match_cache_key',
                   '_receipt_match_cache', '_receipt_match_cache_key'):
            st.session_state.pop(_k, None)
        # 3) 데이터 캐시 무효화 (DB 직접 수정사항 반영)
        try:
            if invalidate_data_cache:
                invalidate_data_cache()
        except Exception:
            pass
        st.success(f"✅ 전체 초기화 완료 ({len(_keys_to_remove)}개 위젯 state 정리) — 자동 매칭으로 재계산")
        st.rerun()

    # 정산표 데이터 로드 (loader 모듈): profit_settlements→dispatch→order_history→daily
    from pages_lib.profit_calc.loader import load_settlement_df
    df, _src_label = load_settlement_df(USERNAME, calc_date_str, _cached_daily_orders)

    # 데이터 소스 표시 (디버그/안심용)
    if _src_label:
        st.caption(_src_label)

    # 저장완료 토스트 (rerun 전에 큐에 저장된 메시지 표시)
    if '_profit_save_toast' in st.session_state:
        st.toast(st.session_state.pop('_profit_save_toast'), icon="✅")
    # 영수증 picker 토스트
    if '_rcpt_pick_toast' in st.session_state:
        st.toast(st.session_state.pop('_rcpt_pick_toast'), icon="🧾")

    if df is not None and not df.empty:
        # 저장 전후 데이터 소스(order_history/profit_settlements)가 달라도 행 순서 고정
        # → 저장 시 리스트가 재정렬되어 바뀌는 현상 방지
        if '상품명' in df.columns and '수취인명' in df.columns:
            df = df.sort_values(['상품명', '수취인명'], kind='stable')
        receipt_items = st.session_state.get('receipt_items', [])
        # 영수증이 없어도 daily_orders 가 있으면 정산표 표시 (영수증 매칭 enrichment만 비활성)
        if not receipt_items:
            st.caption("💡 영수증 PDF 업로드 시 영수증 매칭 기반 매입가 보정이 추가됩니다 (선택사항).")
        unique_products = df['상품명'].unique().tolist()

        # ── 정산 저장 데이터 복원 (세션 첫 진입 시 profit_settlements → session_state) ──
        _restore_flag = f"_do_restored_{calc_date_str}"
        if not st.session_state.get(_restore_flag):
            _ids_restore = df['id'].values if 'id' in df.columns else df.index.values
            if 'cost_overrides' not in st.session_state:
                st.session_state['cost_overrides'] = {}
            if 'kw_overrides' not in st.session_state:
                st.session_state['kw_overrides'] = {}

            # 1순위: profit_settlements (새 DB)
            _saved_ps = get_profit_settlements(USERNAME, calc_date_str)
            if _saved_ps:
                _sv_map = {(str(_sd['recipient']), str(_sd['product_name'])): _sd
                           for _sd in _saved_ps}
                for _ri, (_ridx, _rrow) in enumerate(df.iterrows()):
                    _rsk = str(_ids_restore[_ri])
                    _rk = (str(_rrow.get('수취인명', '')), str(_rrow.get('상품명', '')))
                    _sv = _sv_map.get(_rk)
                    if not _sv:
                        continue
                    _per_ship = int(_sv.get('delivery_cost', 0) or 0)
                    _per_box  = int(_sv.get('box_cost', 0) or 0)
                    if _per_ship > 0 and f"ship_{_rsk}" not in st.session_state:
                        st.session_state[f"ship_{_rsk}"] = _per_ship
                    if _per_box > 0 and f"box_{_rsk}" not in st.session_state:
                        st.session_state[f"box_{_rsk}"] = _per_box
                    _cp = int(_sv.get('cost_price', 0) or 0)
                    if _cp > 0:
                        _rkey = f"{_rrow['수취인명']}_{_rrow['상품명']}_{_rsk}_{calc_date_str}"
                        st.session_state['cost_overrides'][_rkey] = _cp
                    _kw_saved = str(_sv.get('matched_keyword', '') or '')
                    if _kw_saved:
                        st.session_state['kw_overrides'][_rsk] = _kw_saved
            else:
                # 2순위 fallback: daily_orders (구 DB 호환)
                _saved_daily = get_daily_orders(USERNAME, calc_date_str)
                if _saved_daily:
                    _sv_map = {(str(_sd.get('recipient', '')), str(_sd.get('product_name', ''))): _sd
                               for _sd in _saved_daily}
                    for _ri, (_ridx, _rrow) in enumerate(df.iterrows()):
                        _rsk = str(_ids_restore[_ri])
                        _rk = (str(_rrow.get('수취인명', '')), str(_rrow.get('상품명', '')))
                        _sv = _sv_map.get(_rk)
                        if not _sv:
                            continue
                        _per_ship = int(_sv.get('delivery_cost', 0) or 0)
                        _per_box  = int(_sv.get('box_cost', 0) or 0)
                        if _per_ship > 0 and f"ship_{_rsk}" not in st.session_state:
                            st.session_state[f"ship_{_rsk}"] = _per_ship
                        if _per_box > 0 and f"box_{_rsk}" not in st.session_state:
                            st.session_state[f"box_{_rsk}"] = _per_box
                        # ⚠️ daily_orders의 cost_price(수집 시점 동결가)는 복원하지 않음.
                        #    단가는 products DB 신선 매칭이 기준 (가격 수정이 즉시 반영되도록).
                        #    저장 결과 단가는 profit_settlements(명시 저장한 날)에서만 복원.

            # 영구 정산매칭 오버라이드: 키워드 매핑만 복원 (단가 마스킹 제거 → products DB 기준)
            _so_map = get_settlement_overrides_map(USERNAME)
            if _so_map:
                for _ri, (_ridx, _rrow) in enumerate(df.iterrows()):
                    _rsk = str(_ids_restore[_ri])
                    _rk = (str(_rrow.get('수취인명', '')), str(_rrow.get('상품명', '')))
                    _so = _so_map.get(_rk)
                    if not _so:
                        continue
                    if _so.get('override_keyword') and _rsk not in st.session_state['kw_overrides']:
                        st.session_state['kw_overrides'][_rsk] = _so['override_keyword']
                    # override_cost(영구 단가)는 더 이상 복원하지 않음 — products DB 단가가 진실원천

            st.session_state[_restore_flag] = True

        # 제품 목록 1회 로드 (루프마다 DB 재조회 방지, 영수증 캐시보다 먼저 로드)
        _preload_user = cached_user_products(USERNAME)
        _preload_shared = cached_shared_products()

        # 영수증 상품번호 lookup (코스트코 상품번호 → 영수증 항목)
        _rcpt_by_pno: dict = {}
        _pno_map: dict = {}  # {코스트코 상품번호: [주문 상품명, ...]} — 상품번호 우선 매칭용
        if receipt_items:
            _rcpt_by_pno = {str(ri.get('상품번호', '') or ''): ri
                            for ri in receipt_items if ri.get('상품번호')}
            if _rcpt_by_pno:
                for _un in unique_products:
                    _p0 = match_product_to_db(USERNAME, _un, product_no='',
                                              _user_prods=_preload_user,
                                              _shared_prods=_preload_shared)
                    _pno0 = str((_p0 or {}).get('product_no', '') or '').strip()
                    if _pno0:
                        _pno_map.setdefault(_pno0, []).append(_un)

        # 영수증 매칭 결과 캐시 (매 rerun마다 재계산 방지)
        _rcm_sig = (calc_date_str, len(receipt_items),
                    tuple(str(r.get('상품번호', '')) for r in receipt_items[:5]))
        _rcm_key = f"_rcm_{hash(_rcm_sig)}"
        if receipt_items and _rcm_key not in st.session_state:
            with st.spinner("영수증 매칭 중..."):
                st.session_state[_rcm_key] = match_receipt_to_orders(
                    receipt_items, unique_products,
                    pno_map=_pno_map if _pno_map else None
                )
        receipt_matches = st.session_state.get(_rcm_key, {}) if receipt_items else {}

        if 'kw_overrides' not in st.session_state:
            st.session_state['kw_overrides'] = {}
        # 세션당 1회만 실행할 product_no 자동 링크 추적
        if '_auto_linked_pnos' not in st.session_state:
            st.session_state['_auto_linked_pnos'] = set()

        import re as _re  # 루프 외부에서 1회만 import
        _match_memo = {}  # 같은 상품명 중복 매칭 방지 (메모이제이션)

        # 매칭 결과 캐시 — 페이지 이동 시 108행 재계산 방지
        # 무효화 조건: 날짜/df크기/영수증/오버라이드 변경
        _mc_key = (
            calc_date_str, len(df), len(receipt_items),
            tuple(str(r.get('상품번호', '')) for r in receipt_items[:5]),
            tuple(sorted(st.session_state.get('kw_overrides', {}).items())),
        )
        _mc_state = '_pcalc_match_cache'
        _cached = st.session_state.get(_mc_state)
        if _cached and _cached.get('key') == _mc_key:
            costs = _cached['costs']
            match_sources = _cached['sources']
            matched_names = _cached['names']
            matched_pnos = _cached['pnos']
            matched_sqtys = _cached.get('sqtys', [1] * len(costs))
            _skip_match_loop = True
        else:
            costs, match_sources, matched_names, matched_pnos, matched_sqtys = [], [], [], [], []
            _skip_match_loop = False

        for idx, r in (iter([]) if _skip_match_loop else df.iterrows()):
            product, qty = r['상품명'], r['수량']
            saved_cost = int(r.get('구입가격', 0) or 0)
            _row_key = f"{r['수취인명']}_{r['상품명']}_{idx}_{calc_date_str}"
            p_no = str(r.get('product_no', '') or '') if 'product_no' in r.index else ''
            _sell_m = _re.search(r'x\s*(\d+)\s*개', product, _re.IGNORECASE)
            _sell_val = int(_sell_m.group(1)) if _sell_m else 1
            _sell_factor = _sell_val if 1 < _sell_val <= 50 else 1

            # ── 매칭 우선순위 정립 ──
            p = None
            # 1. 수동 키워드/수동 금액 오버라이드 (최우선)
            if _row_key in st.session_state['kw_overrides']:
                _manual_kw = st.session_state['kw_overrides'][_row_key]
                p = match_product_to_db(USERNAME, _manual_kw, product_no='',
                                        _user_prods=_preload_user, _shared_prods=_preload_shared)
                if p:
                    sq = max(1, int(p.get('split_qty', 1) or 1))
                    _aq = qty * _sell_factor
                    costs.append((p['unit_price'] // sq) * _aq)
                elif saved_cost > 0:
                    costs.append(saved_cost)
                else:
                    costs.append(0)
                match_sources.append("수동입력")
                matched_names.append(_manual_kw)
                _picked_pno = (st.session_state.get('receipt_pick', {}) or {}).get(_row_key, '')
                matched_pnos.append(_picked_pno or (p.get('product_no', '') if p else ''))
                matched_sqtys.append(max(1, int((p or {}).get('split_qty', 1) or 1)))

            # 2. 상품번호 매칭 (주문서의 p_no 또는 DB에 저장된 p_no)
            else:
                # 주문서의 p_no로 DB 조회
                if p_no:
                    p = match_product_to_db(USERNAME, product, product_no=p_no,
                                            _user_prods=_preload_user, _shared_prods=_preload_shared)
                
                # DB 키워드 매칭 시 pno가 있는 항목 (이미 검증된 상품)
                if not p:
                    if product not in _match_memo:
                        _match_memo[product] = match_product_to_db(
                            USERNAME, product, product_no='',
                            _user_prods=_preload_user, _shared_prods=_preload_shared)
                    p = _match_memo[product]

                if p and p.get('product_no'):
                    _pno1 = str(p.get('product_no', '')).strip()
                    sq = max(1, int(p.get('split_qty', 1) or 1))
                    _aq = qty * _sell_factor
                    # 영수증에 같은 상품번호 있으면 영수증 가격 우선 (현재 실제 매입가)
                    if _rcpt_by_pno and _pno1 and _pno1 in _rcpt_by_pno:
                        _ri1 = _rcpt_by_pno[_pno1]
                        # ⚠️ 최신 영수증 단가 우선 (saved_cost는 fallback)
                        # 사용자가 단가 수정 후 저장 → DB에 반영 → 다음 render 시 새 값 사용
                        # 영수증 단가는 "묶음(x N개)" 통가격 → split_qty 미설정(1)이면
                        # sell_factor로 나눠 이중계산 방지 (예: 17990(2팩) → //2 후 ×2 = 17990)
                        _eff_sq = sq if sq > 1 else _sell_factor
                        _computed = (_ri1['단가'] // _eff_sq) * _aq
                        costs.append(_computed if _computed > 0 else saved_cost)
                        match_sources.append("영수증")
                        matched_names.append(_ri1['상품명'])
                        matched_pnos.append(_pno1)
                    else:
                        # ⚠️ 최신 DB unit_price 우선 (사용자 저장값 반영)
                        _computed = (p['unit_price'] // sq) * _aq
                        costs.append(_computed if _computed > 0 else saved_cost)
                        match_sources.append("DB-번호")
                        matched_names.append(p['costco_name'])
                        matched_pnos.append(_pno1)
                    matched_sqtys.append(sq)

                # 3. 영수증 매칭 (현재 업로드된 영수증에서 상품번호/이름으로 찾기)
                elif product in receipt_matches:
                    item = receipt_matches[product]
                    _rcpt_pno = str(item.get('상품번호', '') or '')
                    # 영수증 가격을 실시간 반영 (saved_cost가 0이거나 영수증이 새로 업로드된 경우)
                    # p 재활용하여 split_qty 확인
                    _rsq = max(1, int((p or {}).get('split_qty', 1) or 1))
                    if _rsq == 1: # 영수증 이름에서 수량 파싱 시도 (예: x2)
                        _m2 = _re.search(r'x\s*(\d+)\s*개', item['상품명'], _re.IGNORECASE)
                        if _m2: _rsq = max(1, int(_m2.group(1)))
                    
                    _aq = qty * _sell_factor
                    costs.append((item['단가'] // _rsq) * _aq)
                    match_sources.append("영수증")
                    matched_names.append(item['상품명'])
                    matched_pnos.append(_rcpt_pno)
                    
                    # 영수증 상품번호를 DB에 자동 링크 (세션당 1회만 — render마다 쓰기 방지)
                    if _rcpt_pno and p and p.get('match_keyword'):
                        _link_key = f"{_rcpt_pno}::{p['match_keyword']}"
                        if _link_key not in st.session_state['_auto_linked_pnos']:
                            try:
                                upsert_product(USERNAME, p.get('costco_name') or p['match_keyword'],
                                               p['match_keyword'], int(p.get('unit_price') or 0),
                                               product_no=_rcpt_pno,
                                               split_qty=int(p.get('split_qty') or 1))
                                st.session_state['_auto_linked_pnos'].add(_link_key)
                            except Exception:
                                pass
                    matched_sqtys.append(_rsq)

                # ── 3차: 키워드 토큰 매칭 (상품번호 미등록 DB 항목) ──
                elif p:
                    sq = max(1, int(p.get('split_qty', 1) or 1))
                    _aq = qty * _sell_factor
                    costs.append((p['unit_price'] // sq) * _aq)
                    match_sources.append("DB-키워드")
                    matched_names.append(p['costco_name'])
                    matched_pnos.append('')
                    matched_sqtys.append(sq)

                else:
                    if saved_cost > 0:
                        costs.append(saved_cost)
                        match_sources.append("DB-키워드")
                        matched_names.append(product)
                        matched_pnos.append('')
                    else:
                        costs.append(0)
                        match_sources.append("미매칭")
                        matched_names.append("")
                        matched_pnos.append('')
                    matched_sqtys.append(1)

        df['구입가격'] = costs
        df['매칭출처'] = match_sources
        df['매칭제품'] = matched_names
        df['매칭상품번호'] = matched_pnos
        df['소분단위'] = matched_sqtys

        # 매칭 결과 캐시 저장 (페이지 이동 시 재계산 방지)
        if not _skip_match_loop:
            st.session_state[_mc_state] = {
                'key': _mc_key, 'costs': costs, 'sources': match_sources,
                'names': matched_names, 'pnos': matched_pnos, 'sqtys': matched_sqtys,
            }

        if 'cost_overrides' not in st.session_state:
            st.session_state['cost_overrides'] = {}
        if 'kw_overrides' not in st.session_state:
            st.session_state['kw_overrides'] = {}

        # 자동 계산된 원래 비용 보존 (위젯 값이 같은지 비교용)
        _auto_costs = {idx: int(costs[df.index.get_loc(idx)]) for idx in df.index}

        # 영수증 picker 버퍼 조기 적용: session_state['c_<sk>'], session_state['k_<sk>']에 반영
        # ⚓ stable_key(DB id) 기반 — DataFrame 위치 변경에도 안정적으로 매핑
        _ids_for_buf = df['id'].values if 'id' in df.columns else df.index.values
        for _bsk in (str(_i) for _i in _ids_for_buf):
            _bc_early = st.session_state.pop(f'_buf_c_{_bsk}', None)
            if _bc_early is not None:
                st.session_state[f'c_{_bsk}'] = _bc_early
            _bk_early = st.session_state.pop(f'_buf_k_{_bsk}', None)
            if _bk_early is not None:
                st.session_state[f'k_{_bsk}'] = _bk_early

        # df.loc[idx,'수취인명'] 반복 lookup → numpy array로 1회 추출 (~10x 빠름)
        _recipients = df['수취인명'].values
        _products = df['상품명'].values
        # ⚓ 안정 키: DB row id 우선 (DataFrame 위치 변경에도 안정)
        _ids_arr = df['id'].values if 'id' in df.columns else df.index.values
        for i, idx in enumerate(df.index):
            sk = str(_ids_arr[i])  # stable key
            key = f"{_recipients[i]}_{_products[i]}_{sk}_{calc_date_str}"
            _widget_val = st.session_state.get(f"c_{sk}")
            _auto_cost = _auto_costs[idx]
            # widget value는 '1주문 단가'. 합계 cost로 환산해서 비교/저장.
            _qty_for_cost = max(1, int(df.loc[idx, '수량'] or 1))
            if _widget_val is not None:
                _widget_cost = int(_widget_val) * _qty_for_cost
                if _widget_cost != _auto_cost:
                    st.session_state['cost_overrides'][key] = _widget_cost
                else:
                    st.session_state['cost_overrides'].pop(key, None)
            if key in st.session_state['cost_overrides']:
                df.loc[idx, '구입가격'] = st.session_state['cost_overrides'][key]
                if st.session_state['cost_overrides'][key] > 0:
                    df.loc[idx, '매칭출처'] = '수동입력'

        # ── 실정산 확정: 저장된 정산매칭(settlement_matches)의 실제 정산액으로 정산예정금액 대체 ──
        df['_실정산확정'] = False
        try:
            _act_map = get_actual_settlements_map(USERNAME)
        except Exception:
            _act_map = {}
        if _act_map:
            for _aidx in df.index:
                _av = _act_map.get(str(_aidx))
                if _av and int(_av.get('actual') or 0) > 0:
                    df.loc[_aidx, '정산예정금액'] = int(_av['actual'])
                    df.loc[_aidx, '_실정산확정'] = True
        # 쿠팡 실정산 반영: order_no='{orderId}-..' → coupang_settlements(전액 정산금)
        try:
            _cp_map = get_coupang_settled_map(USERNAME)
        except Exception:
            _cp_map = {}
        if _cp_map:
            for _cidx in df.index:
                _ono = str(_cidx)
                if '-' in _ono:
                    _cv = _cp_map.get(_ono.split('-')[0])
                    if _cv and int(_cv.get('settlement') or 0) > 0:
                        df.loc[_cidx, '정산예정금액'] = int(_cv['settlement'])
                        df.loc[_cidx, '_실정산확정'] = True

        # 🚚 고객배송비는 수수료 차감 없이 전액 정산 (수수료 5.5%는 판매가에만 적용 → 정산예정금액에 이미 반영)
        _ship_settle_factor = 1.0
        df['실정산배송비'] = df['배송비 합계'].fillna(0).round().astype(int)

        # 행별 발송비/박스비: 위젯에서 수정된 값 반영 (기본값 = 전역 설정)
        df['택배원가'] = [int(st.session_state.get(f"ship_{str(_ids_arr[i])}", shipping_cost))
                        for i in range(len(df))]
        df['박스원가']  = [int(st.session_state.get(f"box_{str(_ids_arr[i])}", box_cost))
                        for i in range(len(df))]

        # 수입 계산: 행별 발송비/박스비 적용
        df['수입'] = (df['정산예정금액'] + df['실정산배송비']) - (df['구입가격'] + df['택배원가'] + df['박스원가'])

        st.caption(f"📅 {calc_date_str}")

        # value_counts() 1회 호출로 5개 카운트 추출 (이전: df 5번 스캔 → ~5x 빠름)
        _src_counts = df['매칭출처'].value_counts().to_dict()
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("🟢 영수증",     f"{_src_counts.get('영수증', 0)}건")
        c2.metric("🔵 DB-번호",    f"{_src_counts.get('DB-번호', 0)}건",
                  help="상품번호 정확 매칭 (확실)")
        c3.metric("🟠 DB-키워드",  f"{_src_counts.get('DB-키워드', 0)}건",
                  help="키워드 유사도 매칭 — 확인 필요!")
        c4.metric("✏️ 수동",       f"{_src_counts.get('수동입력', 0)}건")
        c5.metric("🟡 미매칭",     f"{_src_counts.get('미매칭', 0)}건")

        # ⚠️ 키워드 매칭 경고
        _kw_match_n = len(df[df['매칭출처']=='DB-키워드'])
        if _kw_match_n > 0:
            st.warning(
                f"⚠️ **{_kw_match_n}건이 키워드만으로 매칭되었습니다** — 상품번호 매칭이 아니므로 정확하지 않을 수 있습니다. "
                f"아래 표에서 🟠 표시된 행을 확인하고, 잘못된 경우 매칭키워드를 수정하거나 영수증 등록으로 상품번호를 채워주세요."
            )

        st.subheader("📊 일별 정산표")
        st.caption("🟢 영수증 | 🔵 DB-번호 (확실) | 🟠 DB-키워드 (확인 필요) | ⬜ 수동 | 🟡 미매칭")

        # 전체 선택 — 체크박스 대신 버튼 사용 (Streamlit 위젯 sync 이슈 회피)
        # ⚓ stable_key 기반 (DB id 우선)
        _ids_for_sel = df['id'].values if 'id' in df.columns else df.index.values
        _sk_list = [str(_v) for _v in _ids_for_sel]
        _checked_rows = [_sk for _sk in _sk_list if st.session_state.get(f"sel_p_{_sk}", False)]
        _hdr_sel_key = f'_hdr_sel_{calc_date_str}'

        # ── 액션 바 ──
        _act1, _act_del, _act4, _act5, _act6 = st.columns([1.9, 1.4, 1.9, 1.9, 1.9])
        _bulk_save = _act1.button(
            f"📊 {len(_checked_rows)}개 정산저장" if _checked_rows else "📊 정산저장",
            type="primary",
            disabled=not _checked_rows,
            key="bulk_save_kw",
            use_container_width=True,
            help="수익계산 데이터 저장 (제품가격DB 변경 없음)"
        )
        _bulk_del = _act_del.button(
            f"🗑 {len(_checked_rows)}개 삭제" if _checked_rows else "🗑 삭제",
            disabled=not _checked_rows,
            key="bulk_delete_rows",
            use_container_width=True,
            help="선택한 행(취소건 등)을 정산에서 삭제 (주문이력·정산저장에서 제거)"
        )
        _bulk_price_val = _act4.number_input(
            "일괄 단가 (1주문)",
            value=0, min_value=0, step=100,
            label_visibility="collapsed",
            key="bulk_price_input",
            disabled=not _checked_rows,
            help="단가 입력 — 각 행에서 수량을 곱해 합계 매입가가 자동 계산됩니다.",
        )
        _bulk_apply = _act5.button(
            f"💰 {len(_checked_rows)}개 단가 일괄적용" if _checked_rows else "💰 단가 일괄적용",
            disabled=not _checked_rows or not _bulk_price_val,
            key="bulk_apply_price",
            use_container_width=True,
        )
        if _act6.button(
            f"🛒 {len(_checked_rows)}개 네이버 가격수정" if _checked_rows else "🛒 네이버 가격수정",
            disabled=not _checked_rows,
            key="bulk_naver_edit",
            use_container_width=True,
            help="선택한 행의 네이버 판매가/택배비를 수정합니다 (아래 패널에서 확인 후 적용)",
        ):
            st.session_state['_show_naver_edit'] = True

        # 헤더 — outer column: [전체선택][표시][구입가][발송비][박스비][🧾영수증]
        _TH = "text-align:{a};padding:3px 6px;font-size:12px;color:#444;background:#fafafa;border-bottom:1px solid #dee2e6"
        _h0, _h1, _h2, _h3, _h4, _h5 = st.columns([0.3, 7.5, 1.3, 1.0, 1.0, 0.6])
        # 전체 선택 버튼
        _all_sel = len(_checked_rows) == len(df) and len(df) > 0
        if _h0.button("☑" if _all_sel else "☐", key=_hdr_sel_key, help="전체 선택/해제"):
            _new_v = not _all_sel
            for _sk in _sk_list:
                st.session_state[f'sel_p_{_sk}'] = _new_v
            st.rerun()
        _h1.markdown(
            '<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
            '<thead><tr>'
            f'<th style="width:10%;{_TH.format(a="left")}">수취인</th>'
            f'<th style="width:44%;{_TH.format(a="left")}">상품명</th>'
            f'<th style="width:5%;{_TH.format(a="center")}">수량</th>'
            f'<th style="width:12%;{_TH.format(a="right")}">정산예정</th>'
            f'<th style="width:10%;{_TH.format(a="right")}">고객택배비</th>'
            f'<th style="width:6%;{_TH.format(a="right")}">박스비</th>'
            f'<th style="width:13%;{_TH.format(a="right")}">💰 수입</th>'
            '</tr></thead></table>',
            unsafe_allow_html=True
        )
        _h2.markdown(
            "<div style='padding-left:12px'><b style='font-size:12px;color:#444' title='1주문 단가 입력'>단가✏️</b></div>",
            unsafe_allow_html=True
        )
        _h3.markdown(
            "<div style='padding-left:12px'><b style='font-size:12px;color:#555' title='택배사 발송비 (행별 변경 가능, 기본값=설정값)'>발송비✏️</b></div>",
            unsafe_allow_html=True
        )
        _h4.markdown(
            "<div style='padding-left:12px'><b style='font-size:12px;color:#555' title='포장 박스비 (행별 변경 가능, 기본값=설정값)'>박스비✏️</b></div>",
            unsafe_allow_html=True
        )
        _h5.markdown("<b style='font-size:13px;color:#444' title='영수증에서 수동 매칭'>🧾</b>", unsafe_allow_html=True)

        # 셀 기본 스타일
        _SRC_STYLE = {
            '영수증':    {'bg': '#d4edda', 'badge': '🟢'},
            'DB-번호':   {'bg': '#d6eaf8', 'badge': '🔵'},
            'DB-키워드': {'bg': '#fff5e6', 'badge': '🟠'},
            '수동입력':  {'bg': '#ffffff', 'badge': '✏️'},
            '미매칭':    {'bg': '#fff3cd', 'badge': '🟡'},
        }
        _CELL = "padding:5px 6px;font-size:13px;border-bottom:1px solid #f0f0f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
        _CELL_NAME = "padding:5px 6px;font-size:13px;border-bottom:1px solid #f0f0f0;white-space:normal;word-break:break-all;line-height:1.4"

        # ── 페이지네이션 ──
        _PG_SIZE = 20
        _total_rows = len(df)
        _total_pages = max(1, (_total_rows + _PG_SIZE - 1) // _PG_SIZE)
        if st.session_state.get('_profit_last_date') != calc_date_str:
            st.session_state['profit_pg'] = 1
            st.session_state['_profit_last_date'] = calc_date_str
        if 'profit_pg' not in st.session_state:
            st.session_state['profit_pg'] = 1
        _cur_pg = max(1, min(int(st.session_state['profit_pg']), _total_pages))
        _pg_start = (_cur_pg - 1) * _PG_SIZE
        _pg_end   = min(_pg_start + _PG_SIZE, _total_rows)
        _page_df  = df.iloc[_pg_start:_pg_end]
        if _total_pages > 1:
            st.caption(f"전체 {_total_rows}건 | {_cur_pg}/{_total_pages} 페이지 ({_pg_start+1}~{_pg_end}번째)")

        # 영수증 picker가 다음 rerun 직전에 위젯값을 갱신할 수 있도록 버퍼 적용
        # (위젯이 인스턴스화된 후 session_state 수정 불가 → 위젯 생성 직전에 적용)
        # ⚓ stable_key 기반
        for _bri, _brow in _page_df.iterrows():
            _bsk = str(_brow.get('id', _bri))
            _bk = st.session_state.pop(f'_buf_k_{_bsk}', None)
            if _bk is not None:
                st.session_state[f'k_{_bsk}'] = _bk
            _bc = st.session_state.pop(f'_buf_c_{_bsk}', None)
            if _bc is not None:
                st.session_state[f'c_{_bsk}'] = _bc

        for idx, r in _page_df.iterrows():
            sk = str(r.get('id', idx))  # ⚓ stable_key (DB row id 우선)
            key = f"{r['수취인명']}_{r['상품명']}_{sk}_{calc_date_str}"
            _src = r['매칭출처']
            _ss  = _SRC_STYLE.get(_src, {'bg': '#ffffff', 'badge': ''})
            bg = _ss['bg']
            _full_name = str(r['상품명'])
            # 상품번호가 있으면 상품명 앞에 [번호] 형식으로 prefix
            _row_pno = str(r.get('매칭상품번호', '') or '').strip()
            _pno_prefix = (
                f'<span style="color:#1565c0;font-weight:600;background:#e3f2fd;'
                f'padding:1px 5px;border-radius:3px;font-size:13px;margin-right:5px">'
                f'#{_row_pno}</span>'
                if _row_pno else ''
            )
            _row_sq = int(r.get('소분단위', 1) or 1)
            _split_badge = (
                f'<span style="background:#ede7f6;color:#6c3db7;border-radius:3px;'
                f'padding:1px 5px;font-size:11px;font-weight:700;margin-right:3px">'
                f'소분÷{_row_sq}</span>'
                if _row_sq > 1 else ''
            )
            # 옵션 표기 (있을 때만 상품명 아래 작은 회색 줄)
            _opt = str(r.get('옵션정보', '') or '').strip()
            _opt_html = (
                f'<br><span style="color:#9aa0a6;font-size:12px">└ {_opt}</span>'
                if _opt and _opt.lower() not in ('nan', 'none', '-') else ''
            )
            _name_html = (
                f"{_ss['badge']} {_split_badge}{_pno_prefix}{_full_name}{_opt_html}"
                if _ss['badge']
                else f"{_split_badge}{_pno_prefix}{_full_name}{_opt_html}"
            )
            pv = r['수입']
            try:
                _pv_int = int(pv) if pd.notna(pv) else None
            except Exception:
                _pv_int = None
            if _pv_int is None:
                _pv_str = '-'; _pv_color = '#888'
            else:
                _pv_str = fmt(_pv_int)
                _pv_color = '#1D9E75' if _pv_int >= 0 else '#E74C3C'
            _cur_box_disp = int(r.get('박스원가', box_cost) or box_cost)
            _box_str = fmt(_cur_box_disp) if _cur_box_disp > 0 else '-'

            row_html = (
                f'<table style="width:100%;border-collapse:collapse;table-layout:fixed;background:{bg};border-radius:4px;margin-bottom:0">'
                f'<tr>'
                f'<td style="{_CELL};width:10%" title="{r["수취인명"]}">{r["수취인명"]}</td>'
                f'<td style="{_CELL_NAME};width:44%">{_name_html}</td>'
                f'<td style="{_CELL};width:5%;text-align:center">{int(r["수량"])}</td>'
                f'<td style="{_CELL};width:11%;text-align:right" title="{"실정산 확정" if r.get("_실정산확정") else "예상 정산"}">'
                f'{"✅" if r.get("_실정산확정") else ""}{fmt(r["정산예정금액"])}</td>'
                f'<td style="{_CELL};width:10%;text-align:right">{fmt(r["배송비 합계"])}</td>'
                f'<td style="{_CELL};width:7%;text-align:right;color:#888">{_box_str}</td>'
                f'<td style="{_CELL};width:12%;text-align:right;font-weight:700;color:{_pv_color}">{_pv_str}</td>'
                f'</tr></table>'
            )
            chk_col, disp_col, c_cost, c_ship, c_box, c_rcpt = st.columns([0.3, 7.5, 1.3, 1.0, 1.0, 0.6])
            chk_col.checkbox("", key=f"sel_p_{sk}", label_visibility="collapsed")
            disp_col.markdown(row_html, unsafe_allow_html=True)

            # 입력 필드 = 단가(1주문 단가). 합계 매입가는 시스템이 qty 자동 곱셈.
            current_cost = int(r['구입가격'])
            _qty_row = max(1, int(r.get('수량', 1) or 1))
            current_unit = current_cost // _qty_row if current_cost > 0 else 0
            new_unit_in = c_cost.number_input("", value=current_unit, min_value=0, step=100,
                                              label_visibility="collapsed", key=f"c_{sk}",
                                              help=f"1주문 단가 (× 수량 {_qty_row} = 합계 매입가)")
            new_cost = new_unit_in * _qty_row
            if new_cost != current_cost:
                st.session_state['cost_overrides'][key] = new_cost

            # 행별 택배원가 (발송비)
            _cur_ship_row = int(r.get('택배원가', shipping_cost) or shipping_cost)
            c_ship.number_input("", value=_cur_ship_row, min_value=0, step=100,
                                label_visibility="collapsed", key=f"ship_{sk}",
                                help=f"이 주문의 택배 발송비 (기본: {fmt(shipping_cost)}원)")
            # 행별 박스원가
            _cur_box_row = int(r.get('박스원가', box_cost) or box_cost)
            c_box.number_input("", value=_cur_box_row, min_value=0, step=100,
                               label_visibility="collapsed", key=f"box_{sk}",
                               help=f"이 주문의 박스비 (기본: {fmt(box_cost)}원)")

            # 🧾 영수증 picker — 잘못된 매칭/미매칭을 영수증 항목으로 직접 매칭
            if 'receipt_pick' not in st.session_state:
                st.session_state['receipt_pick'] = {}
            _picked_now = st.session_state['receipt_pick'].get(key)
            
            # 미매칭인 경우 버튼 강조 (빨간색/노란색 느낌)
            _btn_type = "primary" if _src == "미매칭" else "secondary"
            _btn_label = "✅" if _picked_now else ("❓" if _src == "미매칭" else "🧾")
            
            if receipt_items:
                with c_rcpt.popover(_btn_label, use_container_width=True,
                                     help="영수증에서 정확한 항목 선택"):
                    st.caption(f"**{r['상품명'][:50]}**")
                    if _picked_now:
                        # 이미 매칭된 경우 상단에 명확히 표시
                        _matched_kw = st.session_state.get('kw_overrides', {}).get(key, '')
                        st.success(f"✅ 매칭됨 — {_matched_kw[:40]} (#{_picked_now})")
                    else:
                        st.caption("아래 항목 중 하나를 클릭하면 즉시 매칭됩니다")
                    _rq = st.text_input("검색", key=f"rq_{sk}",
                                        placeholder="상품명 / 상품번호 검색",
                                        label_visibility="collapsed")
                    _rq_low = _rq.strip().lower() if _rq else ""
                    _show_items = receipt_items
                    if _rq_low:
                        _show_items = [it for it in receipt_items
                                       if _rq_low in (it.get('상품명', '') or '').lower()
                                       or _rq_low in str(it.get('상품번호', '') or '')]
                    st.caption(f"{len(_show_items)}개 항목")
                    for _ri, _item in enumerate(_show_items[:30]):
                        _ip = int(_item.get('단가', 0) or 0)
                        _in = _item.get('상품명', '') or ''
                        _io = str(_item.get('상품번호', '') or '')
                        _btn_clicked = st.button(
                            f"{_in[:40]}\n💰 {_ip:,}원 · {_io}",
                            key=f"rpick_{sk}_{_ri}_{_io}",
                            use_container_width=True,
                            # 이미 선택된 항목은 강조
                            type="primary" if _picked_now == _io else "secondary",
                        )
                        if _btn_clicked:
                            _qty_row = max(1, int(r['수량']))
                            # 키워드/구입가 오버라이드 저장
                            st.session_state['kw_overrides'][key] = _in
                            st.session_state['cost_overrides'][key] = _ip * _qty_row
                            st.session_state['receipt_pick'][key] = _io
                            # ⚡ 캐시 in-place 업데이트 (전체 재매칭 회피 — 238행 매칭 스킵)
                            _cached_inc = st.session_state.get('_pcalc_match_cache')
                            if _cached_inc:
                                try:
                                    _row_pos = list(df.index).index(idx)
                                    _new_cost = _ip * _qty_row
                                    _cached_inc['costs'][_row_pos]   = _new_cost
                                    _cached_inc['sources'][_row_pos] = '영수증'
                                    _cached_inc['names'][_row_pos]   = _in
                                    _cached_inc['pnos'][_row_pos]    = _io
                                    # 캐시 키의 kw_overrides 부분을 새 값으로 재구성하여 캐시 유지
                                    _new_kw_tuple = tuple(sorted(st.session_state['kw_overrides'].items()))
                                    _key_list = list(_cached_inc['key'])
                                    _key_list[-1] = _new_kw_tuple
                                    _cached_inc['key'] = tuple(_key_list)
                                except (ValueError, KeyError, IndexError):
                                    # 캐시 업데이트 실패 시 안전하게 무효화
                                    st.session_state.pop('_pcalc_match_cache', None)
                            # 위젯 state 버퍼 (⚓ stable_key)
                            st.session_state[f'_buf_k_{sk}'] = _in
                            st.session_state[f'_buf_c_{sk}'] = _ip * _qty_row
                            st.session_state['_rcpt_pick_toast'] = (
                                f"✅ 영수증 매칭: {r['수취인명']} → {_in[:30]} "
                                f"({_ip:,}원 × {_qty_row}개)"
                            )
                            st.rerun()
                    if _picked_now:
                        st.divider()
                        if st.button("❌ 매칭 해제", key=f"runpick_{sk}",
                                     use_container_width=True, type="secondary"):
                            st.session_state['receipt_pick'].pop(key, None)
                            st.session_state['kw_overrides'].pop(key, None)
                            st.session_state['cost_overrides'].pop(key, None)
                            # 해제는 전체 재매칭 필요 (원래 자동 매칭 결과 복원)
                            st.session_state.pop('_pcalc_match_cache', None)
                            st.session_state[f'_buf_k_{sk}'] = ''
                            st.session_state[f'_buf_c_{sk}'] = 0
                            st.session_state['_rcpt_pick_toast'] = (
                                f"❌ 매칭 해제: {r['수취인명']}"
                            )
                            st.rerun()
            else:
                c_rcpt.markdown(
                    "<div style='text-align:center;color:#ccc;font-size:11px;padding:6px 0' "
                    "title='영수증 등록 탭에서 영수증을 먼저 업로드하세요'>—</div>",
                    unsafe_allow_html=True
                )

        # ── 선택 삭제 처리 (취소건 등 — 주문이력/정산저장에서 제거) ──
        if _bulk_del and _checked_rows:
            try:
                _conn_del = get_user_db(USERNAME)
                _ph_del = ",".join("?" * len(_checked_rows))
                # 정산표 행 key = order_no (order_history/profit_settlements 인덱스) 기준 삭제
                _conn_del.execute(f"DELETE FROM order_history WHERE order_no IN ({_ph_del})", _checked_rows)
                _conn_del.execute(f"DELETE FROM profit_settlements WHERE order_no IN ({_ph_del})", _checked_rows)
                _conn_del.commit()
                _conn_del.close()
                # 캐시·복원플래그·선택 초기화 → 즉시 반영
                st.session_state.pop('_pcalc_match_cache', None)
                st.session_state.pop(f"_do_restored_{calc_date_str}", None)
                for _k in list(st.session_state.keys()):
                    if _k.startswith('sel_p_'):
                        st.session_state.pop(_k, None)
                try:
                    invalidate_data_cache()
                except Exception:
                    pass
                st.session_state['_profit_save_toast'] = f"🗑 {len(_checked_rows)}건 삭제 완료"
            except Exception as _de:
                st.error(f"삭제 오류: {_de}")
            st.rerun()

        # ── 정산저장 처리 (수익계산 기록 보존 — 제품가격DB 변경 없음) ──
        if _bulk_save and _checked_rows:
            _ps_save_rows = []
            _cost_ov_s = st.session_state.get('cost_overrides', {}) or {}
            _kw_ov_s   = st.session_state.get('kw_overrides', {}) or {}
            _ids_save  = df['id'].values if 'id' in df.columns else df.index.values
            import re as _re_ps
            for _si, (_sidx, _sr) in enumerate(df.iterrows()):
                _ssk  = str(_ids_save[_si])
                _skey = f"{_sr['수취인명']}_{_sr['상품명']}_{_ssk}_{calc_date_str}"
                _cost = _cost_ov_s.get(_skey, int(_sr.get('구입가격', 0) or 0))
                _per_ship = int(st.session_state.get(f"ship_{_ssk}", shipping_cost) or shipping_cost)
                _per_box  = int(st.session_state.get(f"box_{_ssk}", box_cost) or box_cost)
                _kw_s     = _kw_ov_s.get(_ssk) or str(_sr.get('매칭제품', '') or '')
                _qty_s    = max(1, int(_sr.get('수량', 1) or 1))
                _settle_s = int(_sr.get('정산예정금액', 0) or 0)
                _ship_s   = int(_sr.get('배송비 합계', 0) or 0)
                _profit_s = (_settle_s + _ship_s) - (_cost + _per_ship + _per_box)
                # sell_factor (상품명 "x N개" 패턴)
                _sm_ps = _re_ps.search(r'x\s*(\d+)\s*개', str(_sr.get('상품명', '') or ''), _re_ps.IGNORECASE)
                _sf_ps = int(_sm_ps.group(1)) if _sm_ps and 1 < int(_sm_ps.group(1)) <= 50 else 1
                _ps_save_rows.append({
                    'order_no':           str(_sidx),
                    'recipient':          str(_sr.get('수취인명', '') or ''),
                    'product_name':       str(_sr.get('상품명', '') or ''),
                    'product_no':         str(_sr.get('상품번호', '') or _sr.get('product_no', '') or ''),
                    'option_info':        str(_sr.get('옵션정보', '') or ''),
                    'qty':                _qty_s,
                    'order_amount':       int(_sr.get('최종 상품별 총 주문금액', 0) or 0),
                    'shipping_fee':       _ship_s,
                    'extra_shipping':     int(_sr.get('제주/도서 추가배송비', 0) or 0),
                    'settlement_amount':  _settle_s,
                    'cost_price':         _cost,
                    'delivery_cost':      _per_ship,
                    'box_cost':           _per_box,
                    'profit':             _profit_s,
                    'matched_keyword':    _kw_s,
                    'matched_product_no': str(_sr.get('매칭상품번호', '') or ''),
                    'match_source':       str(_sr.get('매칭출처', '') or ''),
                    'split_qty':          int(_sr.get('소분단위', 1) or 1),
                    'sell_factor':        _sf_ps,
                })
                # 수동 오버라이드는 영구 저장 (정산매칭 DB)
                _has_kw_ov_s  = _ssk in _kw_ov_s
                _has_cost_ov_s = _skey in _cost_ov_s
                if _has_kw_ov_s or _has_cost_ov_s:
                    save_settlement_override(
                        USERNAME,
                        str(_sr.get('수취인명', '') or ''),
                        str(_sr.get('상품명', '') or ''),
                        keyword=_kw_s if _has_kw_ov_s else '',
                        cost=_cost if _has_cost_ov_s else 0,
                    )
            save_profit_settlements(USERNAME, calc_date_str, _ps_save_rows)
            for _k in list(st.session_state.keys()):
                if _k.startswith('sel_p_'):
                    st.session_state.pop(_k, None)
            # 저장 후 매칭 캐시·복원플래그 초기화 → 다음 render에서 최신 매칭/저장값으로 재구성
            st.session_state.pop('_pcalc_match_cache', None)
            st.session_state.pop(f"_do_restored_{calc_date_str}", None)
            st.session_state['_profit_save_toast'] = (
                f"✅ {len(_checked_rows)}개 정산 데이터 저장 완료 (제품가격DB 미변경)"
            )
            st.rerun()

        # ── 단가 일괄 적용 처리 — 각 행 수량 자동 곱셈 ──
        if _bulk_apply and _checked_rows:
            _apply_unit = int(st.session_state.get('bulk_price_input', 0) or 0)
            if _apply_unit > 0:
                for _i in _checked_rows:
                    _r = df.loc[_i]
                    _bkey = f"{_r['수취인명']}_{_r['상품명']}_{_i}_{calc_date_str}"
                    _qty_b = max(1, int(_r.get('수량', 1) or 1))
                    _apply_cost = _apply_unit * _qty_b
                    st.session_state['cost_overrides'][_bkey] = _apply_cost
                    st.session_state[f'_buf_c_{_i}'] = _apply_unit  # 위젯 표시값은 단가
                for _k in list(st.session_state.keys()):
                    if _k.startswith('sel_p_'):
                        st.session_state.pop(_k, None)
                st.session_state['_profit_save_toast'] = f"✅ {len(_checked_rows)}개 항목에 {fmt(_apply_price)}원 일괄 적용 완료!"
                st.rerun()

        if st.button("💾 제품가격 DB 저장", key="recalc", type="primary",
                     help="단가 수정사항을 제품가격 DB에 반영합니다"):
            save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
            # 수익계산 결과도 함께 저장 (profit_settlements)
            _ps_rc_rows = []
            _ids_rc = df['id'].values if 'id' in df.columns else df.index.values
            _kw_ov_rc = st.session_state.get('kw_overrides', {}) or {}
            _co_rc = st.session_state.get('cost_overrides', {}) or {}
            import re as _re_rc
            for _rci, (_rcidx, _rcr) in enumerate(df.iterrows()):
                _rcsk   = str(_ids_rc[_rci])
                _rckey  = f"{_rcr['수취인명']}_{_rcr['상품명']}_{_rcsk}_{calc_date_str}"
                _rccost = _co_rc.get(_rckey, int(_rcr.get('구입가격', 0) or 0))
                _rcship = int(st.session_state.get(f"ship_{_rcsk}", shipping_cost) or shipping_cost)
                _rcbox  = int(st.session_state.get(f"box_{_rcsk}", box_cost) or box_cost)
                _rckw   = _kw_ov_rc.get(_rcsk) or str(_rcr.get('매칭제품', '') or '')
                _rcsettle = int(_rcr.get('정산예정금액', 0) or 0)
                _rcshipf  = int(_rcr.get('배송비 합계', 0) or 0)
                _sm_rc  = _re_rc.search(r'x\s*(\d+)\s*개', str(_rcr.get('상품명', '') or ''), _re_rc.IGNORECASE)
                _sf_rc  = int(_sm_rc.group(1)) if _sm_rc and 1 < int(_sm_rc.group(1)) <= 50 else 1
                _ps_rc_rows.append({
                    'order_no': str(_rcidx), 'recipient': str(_rcr.get('수취인명', '') or ''),
                    'product_name': str(_rcr.get('상품명', '') or ''),
                    'product_no': str(_rcr.get('상품번호', '') or _rcr.get('product_no', '') or ''),
                    'option_info': str(_rcr.get('옵션정보', '') or ''),
                    'qty': max(1, int(_rcr.get('수량', 1) or 1)),
                    'order_amount': int(_rcr.get('최종 상품별 총 주문금액', 0) or 0),
                    'shipping_fee': _rcshipf, 'extra_shipping': int(_rcr.get('제주/도서 추가배송비', 0) or 0),
                    'settlement_amount': _rcsettle, 'cost_price': _rccost,
                    'delivery_cost': _rcship, 'box_cost': _rcbox,
                    'profit': (_rcsettle + _rcshipf) - (_rccost + _rcship + _rcbox),
                    'matched_keyword': _rckw, 'matched_product_no': str(_rcr.get('매칭상품번호', '') or ''),
                    'match_source': str(_rcr.get('매칭출처', '') or ''),
                    'split_qty': int(_rcr.get('소분단위', 1) or 1), 'sell_factor': _sf_rc,
                })
            save_profit_settlements(USERNAME, calc_date_str, _ps_rc_rows)
            import re as _re_save
            _overrides = st.session_state.get('cost_overrides', {}) or {}
            for _idx_save, _r in df.iterrows():
                _pno = str(_r.get('매칭상품번호', '') or '').strip()
                _kw = (_r.get('매칭제품', '') or '').strip()
                _cost = int(_r.get('구입가격', 0) or 0)
                _qty = max(1, int(_r.get('수량', 1) or 1))
                # 🛡 사용자가 명시적으로 수정한 행만 DB에 반영 (같은 코스트코 번호 행 가격 오염 방지)
                _row_save_key = f"{_r['수취인명']}_{_r['상품명']}_{_idx_save}_{calc_date_str}"
                if _row_save_key not in _overrides:
                    continue
                if _cost > 0 and (_pno or _kw):
                    _order_channel = str(_r.get('product_no', '') or '').strip()
                    # Rule2: 가격수정 저장은 주문 channel번호로 레코드 우선 식별 → 없으면 코스트코번호/키워드
                    _up = next((p for p in (_preload_user or [])
                                if _order_channel and str(p.get('naver_channel_pno', '') or '') == _order_channel), None)
                    if not _up:
                        _up = next(
                            (p for p in (_preload_user or [])
                             if (p.get('product_no') and p.get('product_no') == _pno)
                             or p.get('match_keyword') == _kw),
                            None
                        )
                    _sq = max(1, int((_up or {}).get('split_qty') or 1))
                    _naver_origin = (_up or {}).get('naver_origin_pno', '') or ''
                    # ⭐ sell_factor 보정: 상품명에 "x N개" 표기 시 1주문 = N개
                    # 매칭 공식: cost = (unit_price / sq) × (qty × sell_factor)
                    # 저장 공식: unit_price = (cost × sq) / (qty × sell_factor)
                    _prod_name = str(_r.get('상품명', '') or '')
                    _sm = _re_save.search(r'x\s*(\d+)\s*개', _prod_name, _re_save.IGNORECASE)
                    _sell_val = int(_sm.group(1)) if _sm else 1
                    _sell_factor = _sell_val if 1 < _sell_val <= 50 else 1
                    _denom = max(1, _qty * _sell_factor)
                    _new_unit = (_cost * _sq) // _denom
                    # 식별된 레코드(채널 우선)에 정확히 반영 — origin칸에 channel 넣지 않음(오염 방지)
                    _save_kw  = ((_up or {}).get('match_keyword') or '') or _kw or _pno
                    _save_pno = ((_up or {}).get('product_no') or '') or _pno
                    upsert_product(USERNAME, _save_kw, _save_kw, _new_unit,
                                    product_no=_save_pno, split_qty=_sq,
                                    naver_origin_pno=_naver_origin,
                                    shipping_fee=(_up or {}).get('shipping_fee'),
                                    auto_split_costco_no=False,  # 부작용으로 비활성화
                                    manual=True)  # 사용자 직접 수정 → 박스단가 보호 우회
            invalidate_data_cache()
            # 위젯 state 정리 → 다음 render에서 새 값 표시
            for _k in list(st.session_state.keys()):
                if _k.startswith(('c_', 'k_', 'ship_', 'box_')):
                    st.session_state.pop(_k, None)
            st.session_state.pop('_pcalc_match_cache', None)
            st.session_state.pop(f"_do_restored_{calc_date_str}", None)
            st.session_state['cost_overrides'] = {}
            st.session_state['kw_overrides'] = {}
            st.session_state['receipt_pick'] = {}
            st.session_state['_profit_save_toast'] = f"✅ {calc_date_str} 제품가격 DB 저장 완료!"
            st.rerun()

        # ── 페이지 네비게이션 ──
        if _total_pages > 1:
            _pnav_cols = st.columns([1, 1, 2, 1, 1])
            if _pnav_cols[0].button("◀◀ 처음", key="profit_first", disabled=_cur_pg <= 1):
                st.session_state['profit_pg'] = 1; st.rerun()
            if _pnav_cols[1].button("◀ 이전", key="profit_prev", disabled=_cur_pg <= 1):
                st.session_state['profit_pg'] = _cur_pg - 1; st.rerun()
            _pnav_cols[2].markdown(
                f"<div style='text-align:center;padding:6px 0;font-size:14px'>"
                f"{_cur_pg} / {_total_pages} 페이지</div>",
                unsafe_allow_html=True)
            if _pnav_cols[3].button("다음 ▶", key="profit_next", disabled=_cur_pg >= _total_pages):
                st.session_state['profit_pg'] = _cur_pg + 1; st.rerun()
            if _pnav_cols[4].button("끝 ▶▶", key="profit_last", disabled=_cur_pg >= _total_pages):
                st.session_state['profit_pg'] = _total_pages; st.rerun()

        # 합계
        st.subheader("📋 합계")
        matched_df = df[df['구입가격'] > 0]
        total_settlement = matched_df['정산예정금액'].sum()
        total_cust_ship = matched_df['배송비 합계'].sum()
        total_settled_ship = matched_df['실정산배송비'].sum() if '실정산배송비' in matched_df.columns else total_cust_ship
        total_ship_commission = total_cust_ship - total_settled_ship
        total_cost = matched_df['구입가격'].sum()
        total_ship = matched_df['택배원가'].sum() if '택배원가' in matched_df.columns else len(matched_df) * shipping_cost
        total_box = matched_df['박스원가'].sum() if '박스원가' in matched_df.columns else len(matched_df) * box_cost
        total_profit = matched_df['수입'].sum() if len(matched_df) > 0 else 0

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**수입**")
            st.write(
                f"정산예정: {fmt(total_settlement)}원 + 실정산배송비: {fmt(total_settled_ship)}원 "
                f"= **{fmt(total_settlement + total_settled_ship)}원**"
            )
            if total_ship_commission > 0:
                st.caption(
                    f"💡 고객결제 배송비 {fmt(total_cust_ship)}원 - 네이버 수수료 {fmt(int(total_ship_commission))}원 "
                    f"= 실정산 {fmt(total_settled_ship)}원"
                )
        with c2:
            st.markdown("**지출**")
            st.write(f"구입가: {fmt(total_cost)}원 + 택배: {fmt(total_ship)}원 + 박스: {fmt(total_box)}원 = **{fmt(total_cost + total_ship + total_box)}원**")
        st.markdown(f"### 순수익: {'🟢' if total_profit >= 0 else '🔴'} {fmt(total_profit)}원")

        st.divider()
        # ── 🧾 영수증 등록 (하단 — 정산표 본 후 매칭 가격 보정용) ─────────
        _rcpt_loaded = bool(st.session_state.get('receipt_items'))
        _rcpt_label = (
            f"🧾 영수증 등록 — 매칭 가격 보정 (선택사항)"
            + (f" — ✅ {len(st.session_state.get('receipt_items', []))}개 로드됨" if _rcpt_loaded else "")
        )
        with st.expander(_rcpt_label, expanded=not _rcpt_loaded):
            from pages_lib import receipt_page as _rcpt_pg
            _rcpt_pg.render(USERNAME, IS_ADMIN, settings, embedded=True, order_date=calc_date_str)

        st.divider()
        if st.button("💾 정산 데이터 저장", type="primary"):
            save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
            # 수익계산 결과 profit_settlements에도 저장
            _ps_all_rows = []
            _ids_all = df['id'].values if 'id' in df.columns else df.index.values
            _kw_ov_all = st.session_state.get('kw_overrides', {}) or {}
            _co_all = st.session_state.get('cost_overrides', {}) or {}
            import re as _re_all
            for _alli, (_allidx, _allr) in enumerate(df.iterrows()):
                _allsk  = str(_ids_all[_alli])
                _allkey = f"{_allr['수취인명']}_{_allr['상품명']}_{_allsk}_{calc_date_str}"
                _allcost = _co_all.get(_allkey, int(_allr.get('구입가격', 0) or 0))
                _allship = int(st.session_state.get(f"ship_{_allsk}", shipping_cost) or shipping_cost)
                _allbox  = int(st.session_state.get(f"box_{_allsk}", box_cost) or box_cost)
                _allkw   = _kw_ov_all.get(_allsk) or str(_allr.get('매칭제품', '') or '')
                _alls    = int(_allr.get('정산예정금액', 0) or 0)
                _allsf_m = _re_all.search(r'x\s*(\d+)\s*개', str(_allr.get('상품명', '') or ''), _re_all.IGNORECASE)
                _allsf   = int(_allsf_m.group(1)) if _allsf_m and 1 < int(_allsf_m.group(1)) <= 50 else 1
                _allshipf = int(_allr.get('배송비 합계', 0) or 0)
                _ps_all_rows.append({
                    'order_no': str(_allidx), 'recipient': str(_allr.get('수취인명', '') or ''),
                    'product_name': str(_allr.get('상품명', '') or ''),
                    'product_no': str(_allr.get('상품번호', '') or _allr.get('product_no', '') or ''),
                    'option_info': str(_allr.get('옵션정보', '') or ''),
                    'qty': max(1, int(_allr.get('수량', 1) or 1)),
                    'order_amount': int(_allr.get('최종 상품별 총 주문금액', 0) or 0),
                    'shipping_fee': _allshipf, 'extra_shipping': int(_allr.get('제주/도서 추가배송비', 0) or 0),
                    'settlement_amount': _alls, 'cost_price': _allcost,
                    'delivery_cost': _allship, 'box_cost': _allbox,
                    'profit': (_alls + _allshipf) - (_allcost + _allship + _allbox),
                    'matched_keyword': _allkw, 'matched_product_no': str(_allr.get('매칭상품번호', '') or ''),
                    'match_source': str(_allr.get('매칭출처', '') or ''),
                    'split_qty': int(_allr.get('소분단위', 1) or 1), 'sell_factor': _allsf,
                })
            save_profit_settlements(USERNAME, calc_date_str, _ps_all_rows)
            # Phase 1: upsert_product로 매칭 행 저장 — 사용자가 명시적으로 수정한 행만
            _overrides2 = st.session_state.get('cost_overrides', {}) or {}
            _pno_units = {}
            for _idx2, _r in df.iterrows():
                _pno = str(_r.get('매칭상품번호', '') or '').strip()
                _kw = (_r.get('매칭제품', '') or '').strip()
                _cost = int(_r.get('구입가격', 0) or 0)
                _qty = max(1, int(_r.get('수량', 1) or 1))
                _row_save_key2 = f"{_r['수취인명']}_{_r['상품명']}_{_idx2}_{calc_date_str}"
                if _row_save_key2 not in _overrides2:
                    continue  # 사용자가 안 건드린 행은 DB 갱신 안 함 (다른 행 가격 덮어쓰기 방지)
                if _cost > 0 and (_pno or _kw):
                    _up = next((p for p in (_preload_user or [])
                                if (p.get('product_no') and p.get('product_no') == _pno)
                                or p.get('match_keyword') == _kw), None)
                    _sq = max(1, int((_up or {}).get('split_qty') or 1))
                    import re as _re_s2
                    _prod_name_s2 = str(_r.get('상품명', '') or '')
                    _sm_s2 = _re_s2.search(r'x\s*(\d+)\s*개', _prod_name_s2, _re_s2.IGNORECASE)
                    _sell_val_s2 = int(_sm_s2.group(1)) if _sm_s2 else 1
                    _sell_factor_s2 = _sell_val_s2 if 1 < _sell_val_s2 <= 50 else 1
                    _denom_s2 = max(1, _qty * _sell_factor_s2)
                    _new_unit = (_cost * _sq) // _denom_s2
                    upsert_product(USERNAME, _kw or _pno, _kw or _pno, _new_unit,
                                    product_no=_pno, split_qty=_sq,
                                    shipping_fee=(_up or {}).get('shipping_fee'),
                                    auto_split_costco_no=False,  # 부작용으로 비활성화
                                    manual=True)  # 사용자 직접 수정 → 박스단가 보호 우회
                    if _pno:
                        _pno_units[_pno] = _new_unit
            # Phase 2: 같은 product_no 일괄 동기화
            if _pno_units:
                _conn = get_user_db(USERNAME)
                _now = datetime.now().strftime("%Y-%m-%d %H:%M")
                for _pno, _unit in _pno_units.items():
                    _conn.execute(
                        "UPDATE products SET unit_price=?, updated_at=? WHERE product_no=?",
                        (_unit, _now, _pno)
                    )
                _conn.commit()
                _conn.close()
            invalidate_data_cache()
            # 저장 후 매칭 캐시·복원플래그 초기화 → 최신 매칭/저장값 재구성
            st.session_state.pop('_pcalc_match_cache', None)
            st.session_state.pop(f"_do_restored_{calc_date_str}", None)
            st.success(f"✅ {calc_date_str} 저장 완료! (제품DB 매입가도 갱신)")

        # ── 🛒 선택 상품 네이버 가격 수정 (naver_price 모듈) ──
        from pages_lib.profit_calc.naver_price import render_selected_price_panel
        render_selected_price_panel(df, USERNAME, api_id, api_secret,
                                    shipping_cost, box_cost, _preload_user, _gs,
                                    _checked_rows, _ids_for_sel)

        # ── 수익 마이너스 — 네이버 판매가 검토 및 적용 (naver_price 모듈) ──
        from pages_lib.profit_calc.naver_price import render_loss_price_panel
        render_loss_price_panel(df, USERNAME, api_id, api_secret,
                                shipping_cost, box_cost, _preload_user, _gs)
    else:
        st.info(
            f"📭 **{calc_date_str}** 에 표시할 데이터가 없습니다.\n\n"
            "**해결 방법 (둘 중 하나):**\n\n"
            "1. **📋 일일 주문 수집** 페이지 → API/엑셀 조회 → 💾 저장 "
            "(결제일이 이 날짜인 주문 자동 필터링)\n"
            "2. **📮 송장번호** 페이지 → 🚀 발송처리 → dispatch_log 자동 생성"
        )
        # 데이터 없어도 영수증 미리 업로드 가능 — 조회 후 자동 매칭
        _rcpt_loaded = bool(st.session_state.get('receipt_items'))
        _rcpt_label = (
            "🧾 영수증 업로드"
            + (f" — ✅ {len(st.session_state.get('receipt_items', []))}개 로드됨" if _rcpt_loaded else " (미리 업로드 후 조회하면 자동 매칭)")
        )
        with st.expander(_rcpt_label, expanded=not _rcpt_loaded):
            from pages_lib import receipt_page as _rcpt_pg
            _rcpt_pg.render(USERNAME, IS_ADMIN, settings, embedded=True, order_date=calc_date_str)



    # ═══════════════════════════════════════
    # 탭 4: 대시보드
    # ═══════════════════════════════════════
