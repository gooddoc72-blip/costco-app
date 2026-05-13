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
    shipping_cost = int(_gs('shipping_cost') or 1800)
    box_cost = int(_gs('box_cost') or 300)
    
    # ⚠️ 설정값 이상치 체크 (비정상적으로 큰 값 방지)
    if shipping_cost > 100000: shipping_cost = 1800
    if box_cost > 10000: box_cost = 300

    _ship_fee_rate_info = float(_gs('naver_ship_fee_commission_rate') or 4.0)
    st.info(
        f"📐 수익 = (정산예정 + **실정산배송비**) - (구입가 + 택배비 {fmt(shipping_cost)} + 박스비 {fmt(box_cost)})  "
        f"· 실정산배송비 = 고객택배비 × {100 - _ship_fee_rate_info:.1f}% (수수료율 {_ship_fee_rate_info}%)"
    )

    col_date, _col_refresh, _col_clean, _ = st.columns([1.5, 1, 1.5, 2.5])
    with col_date:
        calc_date = st.date_input("계산할 주문 날짜 선택", value=datetime.today() - timedelta(days=1))
        calc_date_str = calc_date.strftime("%Y-%m-%d")
    with _col_refresh:
        st.write(""); st.write("")
        if st.button("🔄 새로고침", key="profit_force_refresh",
                     use_container_width=True,
                     help="30초 캐시 무효화 — 일일 주문 수집에서 재저장했는데 옛 데이터가 보일 때"):
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
            if k.startswith(('k_', 'c_', '_buf_k_', '_buf_c_', 'sel_p_', 'rq_'))
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

    # ⭐ 신규 데이터 소스: dispatch_log (일괄발송 성공건) + order_history JOIN
    # → "이 날짜에 발송된 주문" = "이 날짜의 수익계산 대상" 으로 일치 보장
    # → 결제일 필터/저장 액션 불필요, daily_orders 의존 제거
    _src_label = None
    _dispatched_rows = get_dispatched_orders_with_details(USERNAME, calc_date_str)

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

    if _dispatched_rows:
        df = pd.DataFrame(_dispatched_rows)
        df = df.rename(columns=rename_map)
        # order_no를 stable_key 로 사용 (dispatch_log UNIQUE)
        if 'order_no' in df.columns:
            df.index = df['order_no'].astype(str)
            df.index.name = None
        _src_label = f"🚀 발송 기준 ({len(df)}건) — dispatch_log"
    else:
        # Fallback: 옛 daily_orders 데이터 (이전 워크플로 호환)
        _get_daily = _cached_daily_orders if _cached_daily_orders else get_daily_orders
        saved_rows = _get_daily(USERNAME, calc_date_str)
        if saved_rows:
            df = pd.DataFrame(saved_rows)
            df = df.rename(columns=rename_map)
            if 'id' in df.columns:
                df.index = df['id'].astype(str)
                df.index.name = None
            _src_label = f"📋 옛 데이터 ({len(df)}건) — daily_orders fallback"
        else:
            df = None

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
        receipt_items = st.session_state.get('receipt_items', [])
        # 영수증이 없어도 daily_orders 가 있으면 정산표 표시 (영수증 매칭 enrichment만 비활성)
        if not receipt_items:
            st.caption("💡 영수증 PDF 업로드 시 영수증 매칭 기반 매입가 보정이 추가됩니다 (선택사항).")
        unique_products = df['상품명'].unique().tolist()

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
            _skip_match_loop = True
        else:
            costs, match_sources, matched_names, matched_pnos = [], [], [], []
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
                        _computed = (_ri1['단가'] // sq) * _aq
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

                # ── 3차: 키워드 토큰 매칭 (상품번호 미등록 DB 항목) ──
                elif p:
                    sq = max(1, int(p.get('split_qty', 1) or 1))
                    _aq = qty * _sell_factor
                    costs.append((p['unit_price'] // sq) * _aq)
                    match_sources.append("DB-키워드")
                    matched_names.append(p['costco_name'])
                    matched_pnos.append('')

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

        df['구입가격'] = costs
        df['매칭출처'] = match_sources
        df['매칭제품'] = matched_names
        df['매칭상품번호'] = matched_pnos

        # 매칭 결과 캐시 저장 (페이지 이동 시 재계산 방지)
        if not _skip_match_loop:
            st.session_state[_mc_state] = {
                'key': _mc_key, 'costs': costs, 'sources': match_sources,
                'names': matched_names, 'pnos': matched_pnos,
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
            if _widget_val is not None and int(_widget_val) != _auto_cost:
                st.session_state['cost_overrides'][key] = int(_widget_val)
            elif _widget_val is not None and int(_widget_val) == _auto_cost:
                st.session_state['cost_overrides'].pop(key, None)
            if key in st.session_state['cost_overrides']:
                df.loc[idx, '구입가격'] = st.session_state['cost_overrides'][key]
                if st.session_state['cost_overrides'][key] > 0:
                    df.loc[idx, '매칭출처'] = '수동입력'

        # 🚚 배송비 수수료율 적용: 네이버는 고객결제 배송비에서도 수수료를 떼고 정산
        # 실정산 배송비 = 고객결제 배송비 × (1 - 수수료율/100)
        _ship_fee_rate = float(_gs('naver_ship_fee_commission_rate') or 4.0)
        _ship_settle_factor = max(0.0, 1.0 - _ship_fee_rate / 100.0)
        df['실정산배송비'] = (df['배송비 합계'] * _ship_settle_factor).round().astype(int)

        # 수입 계산: 벡터화 (apply 대비 ~10배 빠름)
        # 정산예정금액(상품, 수수료 차감 후) + 실정산배송비(고객배송비 - 수수료) - 지출
        df['수입'] = (df['정산예정금액'] + df['실정산배송비']) - (df['구입가격'] + shipping_cost + box_cost)

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

        # ── 액션 바: 3등분 균일 레이아웃 ──
        _act1, _act4, _act5 = st.columns([2, 2, 2])
        _bulk_save = _act1.button(
            f"💾 선택 {len(_checked_rows)}개 저장" if _checked_rows else "💾 선택 저장",
            type="primary",
            disabled=not _checked_rows,
            key="bulk_save_kw",
            use_container_width=True
        )
        _bulk_price_val = _act4.number_input(
            "일괄 구입가격",
            value=0, min_value=0, step=100,
            label_visibility="collapsed",
            key="bulk_price_input",
            disabled=not _checked_rows,
        )
        _bulk_apply = _act5.button(
            f"💰 {len(_checked_rows)}개 금액 일괄적용" if _checked_rows else "💰 금액 일괄적용",
            disabled=not _checked_rows or not _bulk_price_val,
            key="bulk_apply_price",
            use_container_width=True,
        )

        # 헤더 — outer column: [전체선택][표시][구입가][🧾영수증]
        _TH = "text-align:{a};padding:3px 6px;font-size:12px;color:#444;background:#fafafa;border-bottom:1px solid #dee2e6"
        _h0, _h1, _h2, _h4 = st.columns([0.3, 9, 1.5, 0.6])
        # 전체 선택 버튼 — 클릭 시 모든 행 sel_p_<sk> 토글
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
            f'<th style="width:11%;{_TH.format(a="right")}">정산예정</th>'
            f'<th style="width:10%;{_TH.format(a="right")}">택배비</th>'
            f'<th style="width:7%;{_TH.format(a="right")}">박스비</th>'
            f'<th style="width:12%;{_TH.format(a="right")}">💰 수입</th>'
            '</tr></thead></table>',
            unsafe_allow_html=True
        )
        _h2.markdown("<b style='font-size:13px;color:#444'>구입가격✏️</b>", unsafe_allow_html=True)
        _h4.markdown("<b style='font-size:13px;color:#444' title='영수증에서 수동 매칭'>🧾</b>", unsafe_allow_html=True)

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
            _name_html = (
                f"{_ss['badge']} {_pno_prefix}{_full_name}"
                if _ss['badge']
                else f"{_pno_prefix}{_full_name}"
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
            _box_str = fmt(box_cost) if box_cost > 0 else '-'

            row_html = (
                f'<table style="width:100%;border-collapse:collapse;table-layout:fixed;background:{bg};border-radius:4px;margin-bottom:0">'
                f'<tr>'
                f'<td style="{_CELL};width:10%" title="{r["수취인명"]}">{r["수취인명"]}</td>'
                f'<td style="{_CELL_NAME};width:44%">{_name_html}</td>'
                f'<td style="{_CELL};width:5%;text-align:center">{int(r["수량"])}</td>'
                f'<td style="{_CELL};width:11%;text-align:right">{fmt(r["정산예정금액"])}</td>'
                f'<td style="{_CELL};width:10%;text-align:right">{fmt(r["배송비 합계"])}</td>'
                f'<td style="{_CELL};width:7%;text-align:right;color:#888">{_box_str}</td>'
                f'<td style="{_CELL};width:12%;text-align:right;font-weight:700;color:{_pv_color}">{_pv_str}</td>'
                f'</tr></table>'
            )
            chk_col, disp_col, c_cost, c_rcpt = st.columns([0.3, 9, 1.5, 0.6])
            chk_col.checkbox("", key=f"sel_p_{sk}", label_visibility="collapsed")
            disp_col.markdown(row_html, unsafe_allow_html=True)

            current_cost = int(r['구입가격'])
            new_cost = c_cost.number_input("", value=current_cost, min_value=0, step=100,
                                           label_visibility="collapsed", key=f"c_{sk}")
            if new_cost != current_cost:
                st.session_state['cost_overrides'][key] = new_cost

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

        # ── 일괄 저장 처리 ──
        if _bulk_save and _checked_rows:
            _saved_n = 0
            for _i in _checked_rows:
                _r = df.loc[_i]
                _key = f"{_r['수취인명']}_{_r['상품명']}_{_i}_{calc_date_str}"
                _new_cost = st.session_state.get(f"c_{_i}", int(_r['구입가격']))
                _new_kw   = st.session_state.get(f"k_{_i}", _r['매칭제품'] or "").strip()
                _qty = int(_r['수량'])
                _save_kw = _new_kw or (_r.get('매칭제품', '') or "").strip()
                _picked_pno = (st.session_state.get('receipt_pick', {}) or {}).get(_key, '')
                if _save_kw and _new_cost > 0:
                    _unit = _new_cost // _qty if _qty > 1 else _new_cost
                    _shared_match = next(
                        (sp for sp in (_preload_shared or []) if sp.get('match_keyword') == _save_kw),
                        None
                    )
                    # ⚠️ 사용자 입력 우선 — 공유 DB 가격으로 덮어쓰는 옛 로직 제거
                    _user_match = next(
                        (up for up in (_preload_user or []) if up.get('match_keyword') == _save_kw),
                        None
                    )
                    _keep_sq = max(1, int((_user_match or {}).get('split_qty') or 1))
                    _existing_pno = _picked_pno or (_user_match or {}).get('product_no', '') or ''
                    _existing_fee = (_user_match or {}).get('shipping_fee', None)
                    # ⭐ 네이버 원상품번호 — 같은 코스트코 상품번호로 여러 네이버 상품이 있어도 각각 별도 저장
                    _existing_naver_origin = (_user_match or {}).get('naver_origin_pno', '') or ''
                    # 사용자가 입력한 _new_cost (= _unit × _qty) 을 그대로 단가로 저장
                    _save_price = _unit * _keep_sq
                    upsert_product(USERNAME, _save_kw, _save_kw, _save_price,
                                   product_no=_existing_pno, split_qty=_keep_sq,
                                   naver_origin_pno=_existing_naver_origin,
                                   shipping_fee=_existing_fee)
                    _saved_n += 1
            invalidate_data_cache()
            for _k in list(st.session_state.keys()):
                if _k.startswith('sel_p_') or _k.startswith('c_') or _k.startswith('k_'):
                    st.session_state.pop(_k, None)
            st.session_state['cost_overrides'] = {}
            st.session_state['kw_overrides'] = {}
            st.session_state['receipt_pick'] = {}
            st.session_state.pop('_pcalc_match_cache', None)
            st.session_state['_profit_save_toast'] = (
                f"✅ {_saved_n}개 저장 완료! 단가가 제품 DB에 반영되었습니다."
            )
            st.rerun()

        # ── 금액 일괄 적용 처리 ──
        if _bulk_apply and _checked_rows:
            _apply_price = int(st.session_state.get('bulk_price_input', 0) or 0)
            if _apply_price > 0:
                for _i in _checked_rows:
                    _r = df.loc[_i]
                    _bkey = f"{_r['수취인명']}_{_r['상품명']}_{_i}_{calc_date_str}"
                    st.session_state['cost_overrides'][_bkey] = _apply_price
                    st.session_state[f'_buf_c_{_i}'] = _apply_price
                for _k in list(st.session_state.keys()):
                    if _k.startswith('sel_p_'):
                        st.session_state.pop(_k, None)
                st.session_state['_profit_save_toast'] = f"✅ {len(_checked_rows)}개 항목에 {fmt(_apply_price)}원 일괄 적용 완료!"
                st.rerun()

        if st.button("💾 수정사항 반영", key="recalc", type="primary"):
            save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
            # Phase 1: upsert_product로 매칭 행 저장 (각각 자체 connection)
            _pno_units = {}  # {product_no: new_unit_price} — Phase 2 일괄 동기화용
            for _, _r in df.iterrows():
                _pno = str(_r.get('매칭상품번호', '') or '').strip()
                _kw = (_r.get('매칭제품', '') or '').strip()
                _cost = int(_r.get('구입가격', 0) or 0)
                _qty = max(1, int(_r.get('수량', 1) or 1))
                if _cost > 0 and (_pno or _kw):
                    _up = next((p for p in (_preload_user or [])
                                if (p.get('product_no') and p.get('product_no') == _pno)
                                or p.get('match_keyword') == _kw), None)
                    _sq = max(1, int((_up or {}).get('split_qty') or 1))
                    _new_unit = (_cost // _qty) * _sq
                    upsert_product(USERNAME, _kw or _pno, _kw or _pno, _new_unit,
                                    product_no=_pno, split_qty=_sq,
                                    shipping_fee=(_up or {}).get('shipping_fee'))
                    if _pno:
                        _pno_units[_pno] = _new_unit
            # Phase 2: 같은 product_no의 다른 키워드 행 일괄 동기화 (단일 connection)
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
            st.session_state['cost_overrides'] = {}
            st.session_state['kw_overrides'] = {}
            st.session_state['receipt_pick'] = {}
            st.session_state['_profit_save_toast'] = f"✅ {calc_date_str} 수정사항 + 제품DB 매입가 저장 완료!"
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
        total_ship = len(matched_df) * shipping_cost
        total_box = len(matched_df) * box_cost
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
        with st.expander(_rcpt_label, expanded=False):
            from pages_lib import receipt_page as _rcpt_pg
            _rcpt_pg.render(USERNAME, IS_ADMIN, settings, embedded=True, order_date=calc_date_str)

        st.divider()
        if st.button("💾 정산 데이터 저장", type="primary"):
            save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
            # Phase 1: upsert_product로 매칭 행 저장
            _pno_units = {}
            for _, _r in df.iterrows():
                _pno = str(_r.get('매칭상품번호', '') or '').strip()
                _kw = (_r.get('매칭제품', '') or '').strip()
                _cost = int(_r.get('구입가격', 0) or 0)
                _qty = max(1, int(_r.get('수량', 1) or 1))
                if _cost > 0 and (_pno or _kw):
                    _up = next((p for p in (_preload_user or [])
                                if (p.get('product_no') and p.get('product_no') == _pno)
                                or p.get('match_keyword') == _kw), None)
                    _sq = max(1, int((_up or {}).get('split_qty') or 1))
                    _new_unit = (_cost // _qty) * _sq
                    upsert_product(USERNAME, _kw or _pno, _kw or _pno, _new_unit,
                                    product_no=_pno, split_qty=_sq,
                                    shipping_fee=(_up or {}).get('shipping_fee'))
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
            st.success(f"✅ {calc_date_str} 저장 완료! (제품DB 매입가도 갱신)")

        # ── 수익 마이너스 — 네이버 판매가 검토 및 적용 ──
        loss_df = df[(df['구입가격'] > 0) & (df['수입'] < 0)].copy()
        if len(loss_df) > 0:
            st.divider()
            st.subheader("🔴 수익 마이너스 — 네이버 판매가 검토 및 적용")
            _margin_rate = int(_gs('target_margin') or 10) / 100

            # 상품명(네이버 주문명) 기준 de-dup → 사용자가 인식할 수 있는 이름으로 표시
            # _loss_seen: 표시명 → (row, 매칭키워드)
            _loss_seen = {}
            for _, _lr in loss_df.iterrows():
                _order_name = str(_lr.get('상품명', '') or '').strip()
                _match_kw   = str(_lr.get('매칭제품', '') or '').strip()
                _disp_key   = _order_name or _match_kw
                if _disp_key and _disp_key not in _loss_seen:
                    _loss_seen[_disp_key] = (_lr, _match_kw)

            _loss_apply = []
            for _li, (_disp_key, (_row, _match_kw)) in enumerate(_loss_seen.items()):
                _qty    = max(1, int(_row['수량']))
                _settle = int(_row['정산예정금액'])
                _cfee   = int(_row['배송비 합계'])
                _cost   = int(_row['구입가격'])
                _profit = int(_row['수입'])

                _unit_cost   = _cost // _qty
                _unit_settle = _settle // _qty
                _cur_sale    = max(100, int(_unit_settle / 0.945 / 100) * 100)

                # 권장가: 손익분기 + 목표마진
                _min_needed = _unit_cost + shipping_cost + box_cost - _cfee / _qty
                _suggested  = max(
                    int(_min_needed * (1 + _margin_rate) / 0.945 / 100) * 100,
                    _cur_sale + 100
                )

                # naver_origin_pno 조회: naver_origin_pno 있는 매칭을 우선 선택
                # 후보: _disp_key 또는 _match_kw 가 user product의 match_keyword/costco_name과 일치
                def _is_match(p):
                    _mk = (p.get('match_keyword','') or '').strip()
                    _cn = (p.get('costco_name','') or '').strip()
                    if _disp_key and (_mk == _disp_key.strip() or _cn == _disp_key.strip()):
                        return True
                    if _match_kw and (_mk == _match_kw or _cn == _match_kw.strip()):
                        return True
                    return False
                # 1순위: 매칭 + naver_origin_pno 있음
                _up_rec = next((p for p in _preload_user if _is_match(p) and p.get('naver_origin_pno')), None)
                # 2순위: 매칭 (PNO 없어도)
                if not _up_rec:
                    _up_rec = next((p for p in _preload_user if _is_match(p)), None)
                _nv_pno = (_up_rec or {}).get('naver_origin_pno', '') or ''
                # 네이버 상품명: from_naver=1이면 costco_name이 네이버 상품명
                _is_naver = int((_up_rec or {}).get('from_naver') or 0) == 1
                _nv_name  = (_up_rec.get('costco_name', '') if _up_rec and _is_naver else '') or ''
                # 최종 표시명: 네이버명 > 주문상품명 > 매칭키워드
                _disp_name = _nv_name or _disp_key

                with st.expander(
                    f"🔴 {_disp_name[:50]}  |  수익 {fmt(_profit)}원 ({_qty}개)",
                    expanded=True
                ):
                    # ── 현황 카드 ──
                    _card = (
                        '<div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap">'
                        f'<div style="flex:1;min-width:90px;background:#fff3f3;border:1px solid #fcc;'
                        f'border-radius:6px;padding:8px 10px;text-align:center">'
                        f'<div style="font-size:11px;color:#888;margin-bottom:2px">현재 판매가</div>'
                        f'<div style="font-size:15px;font-weight:700;color:#333">{fmt(_cur_sale)}원</div>'
                        f'</div>'
                        f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                        f'border-radius:6px;padding:8px 10px;text-align:center">'
                        f'<div style="font-size:11px;color:#888;margin-bottom:2px">정산금액</div>'
                        f'<div style="font-size:15px;font-weight:600">{fmt(_settle)}원</div>'
                        f'</div>'
                        f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                        f'border-radius:6px;padding:8px 10px;text-align:center">'
                        f'<div style="font-size:11px;color:#888;margin-bottom:2px">고객택배비</div>'
                        f'<div style="font-size:15px;font-weight:600">{fmt(_cfee)}원</div>'
                        f'</div>'
                        f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                        f'border-radius:6px;padding:8px 10px;text-align:center">'
                        f'<div style="font-size:11px;color:#888;margin-bottom:2px">구매가격</div>'
                        f'<div style="font-size:15px;font-weight:600">{fmt(_cost)}원</div>'
                        f'</div>'
                        f'<div style="flex:1;min-width:90px;background:#ffe0e0;border:1px solid #faa;'
                        f'border-radius:6px;padding:8px 10px;text-align:center">'
                        f'<div style="font-size:11px;color:#888;margin-bottom:2px">현재 수익</div>'
                        f'<div style="font-size:15px;font-weight:700;color:#E74C3C">{fmt(_profit)}원</div>'
                        f'</div>'
                        f'</div>'
                    )
                    st.markdown(_card, unsafe_allow_html=True)

                    # ── 수정 판매가 / 택배비 입력 ──
                    _ca, _cb, _cd, _cc = st.columns([1, 3, 2, 1])
                    _do = _ca.checkbox("적용", value=True, key=f"lp_chk_{_li}")
                    _new_price = _cb.number_input(
                        "🔧 수정 판매가 (원)",
                        value=_suggested, min_value=100, step=100,
                        key=f"lp_price_{_li}"
                    )
                    _new_cfee = _cd.number_input(
                        "🔧 수정 택배비 (원)",
                        value=int(_cfee), min_value=0, step=100,
                        key=f"lp_cfee_{_li}"
                    )
                    _new_settle = int(_new_price * 0.945)
                    _new_profit = (_new_settle * _qty + _new_cfee) - (_cost + shipping_cost + box_cost)
                    if _new_profit < 0:
                        _cc.error(f"❌ {fmt(_new_profit)}원")
                    else:
                        _cc.success(f"✅ +{fmt(_new_profit)}원")

                    # ── 네이버 상품번호 ──
                    _pno_label = (
                        "✅ 네이버 상품번호 (자동 입력됨)"
                        if _nv_pno else
                        "⚠️ 네이버 상품번호 (미입력 — 직접 입력 필요)"
                    )
                    _pno = st.text_input(
                        _pno_label,
                        value=_nv_pno,
                        key=f"lp_pno_{_li}",
                        placeholder="네이버 originProductNo — 미입력 시 API 적용 불가"
                    )
                    if _do:
                        _loss_apply.append({
                            'name': _match_kw or _disp_key,
                            'display_name': _disp_name,
                            'new_sale_price': _new_price,
                            'new_shipping_fee': _new_cfee,
                            'product_no': _pno,
                            'new_profit': _new_profit,
                        })

            if _loss_apply:
                _still_neg = [t for t in _loss_apply if t['new_profit'] < 0]
                if _still_neg:
                    st.warning(
                        f"⚠️ 아직 수익 마이너스 {len(_still_neg)}건: "
                        + ", ".join(t['display_name'][:20] for t in _still_neg)
                        + "  →  판매가를 더 올려주세요."
                    )
                if st.button("✅ 선택 상품 네이버 판매가 적용", type="primary",
                             key="loss_naver_apply", use_container_width=True):
                    if not api_id or not api_secret:
                        st.error("설정 탭에서 네이버 API 키를 등록해주세요.")
                    elif not HAS_NAVER_API:
                        st.error("naver_api.py 모듈이 없습니다.")
                    else:
                        _ok_names, _fail_msgs = [], []
                        for t in _loss_apply:
                            if not t['product_no']:
                                _fail_msgs.append(f"{t['display_name'][:20]}: 상품번호 미입력")
                                continue
                            _r_ok, _r_err = naver_api.update_product_price(
                                api_id, api_secret, t['product_no'], t['new_sale_price']
                            )
                            if _r_ok:
                                _ok_names.append(t['display_name'])
                            else:
                                _fail_msgs.append(f"{t['display_name'][:20]}: {_r_err}")
                        if _ok_names:
                            st.success(f"✅ 네이버 판매가 적용 완료: {', '.join(_ok_names)}")
                        for _fm in _fail_msgs:
                            st.error(f"❌ {_fm}")
    else:
        st.info(
            f"📭 **{calc_date_str}** 에 표시할 데이터가 없습니다.\n\n"
            "**해결 방법 (둘 중 하나):**\n\n"
            "1. **📋 일일 주문 수집** 페이지 → API/엑셀 조회 → 💾 저장 "
            "(결제일이 이 날짜인 주문 자동 필터링)\n"
            "2. **📮 송장번호** 페이지 → 🚀 발송처리 → dispatch_log 자동 생성"
        )



    # ═══════════════════════════════════════
    # 탭 4: 대시보드
    # ═══════════════════════════════════════
