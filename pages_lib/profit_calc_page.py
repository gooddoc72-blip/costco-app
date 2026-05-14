"""💰 수익 계산 페이지 — Refactored & Stabilized.
구조: 데이터 로드 -> 매칭(캐시) -> 오버라이드 적용 -> 수입 계산 -> UI 렌더링 -> 저장 액션
"""
import os
import io
import sys
import json
import re
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd

from db import (
    get_user_db, init_user_db, get_setting, set_setting, get_all_settings,
    get_all_products, upsert_product, get_all_products_merged,
    save_daily_orders, get_daily_orders,
    get_dispatched_orders_with_details,
    invalidate_data_cache as db_invalidate_cache
)
from services import (
    match_product_to_db, match_receipt_to_orders, calc_cost
)
from utils import fmt

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
    
    st.header("💰 수익 계산")
    
    # ── 1. 기본 설정 및 날짜 선택 ──────────────────────────────────────────
    shipping_cost = int(_gs('shipping_cost') or 1800)
    box_cost = int(_gs('box_cost') or 300)
    if shipping_cost > 100000: shipping_cost = 1800
    if box_cost > 10000: box_cost = 300

    _ship_fee_rate_info = float(_gs('naver_ship_fee_commission_rate') or 4.0)
    st.info(
        f"📐 수익 = (정산예정 + **실정산배송비**) - (구입가 + 택배비 {fmt(shipping_cost)} + 박스비 {fmt(box_cost)})  \n"
        f"· 실정산배송비 = 고객택배비 × {100 - _ship_fee_rate_info:.1f}% (네이버 배송비 수수료 {_ship_fee_rate_info}% 적용)"
    )

    col_date, col_refresh, col_clean, _ = st.columns([1.5, 1, 1.5, 2.5])
    with col_date:
        calc_date = st.date_input("계산할 주문 날짜 선택", value=datetime.today() - timedelta(days=1))
        calc_date_str = calc_date.strftime("%Y-%m-%d")
    
    with col_refresh:
        st.write(""); st.write("")
        if st.button("🔄 새로고침", key="profit_force_refresh", use_container_width=True):
            if invalidate_data_cache: invalidate_data_cache()
            st.session_state.pop('_pcalc_match_cache', None)
            st.rerun()

    with col_clean:
        st.write(""); st.write("")
        _confirm_key = f"_clean_confirm_{calc_date_str}"
        _is_confirming = st.session_state.get(_confirm_key, False)
        if st.button("⚠️ 정말 삭제?" if _is_confirming else "🗑 이 날짜 정리", 
                     key="profit_clean_date", use_container_width=True, 
                     type="primary" if _is_confirming else "secondary"):
            if not _is_confirming:
                st.session_state[_confirm_key] = True
                st.rerun()
            else:
                _cn = get_user_db(USERNAME)
                _cn.execute("DELETE FROM daily_orders WHERE order_date=?", (calc_date_str,))
                _deleted = _cur.rowcount if hasattr(_cn, 'rowcount') else 0
                _cn.commit(); _cn.close()
                if invalidate_data_cache: invalidate_data_cache()
                st.session_state.pop('_pcalc_match_cache', None)
                st.session_state.pop(_confirm_key, None)
                st.success(f"✅ {calc_date_str} 데이터 삭제됨")
                st.rerun()

    # ── 2. 데이터 로드 및 전처리 ──────────────────────────────────────────
    _dispatched_rows = get_dispatched_orders_with_details(USERNAME, calc_date_str)
    rename_map = {
        'recipient': '수취인명', 'product_name': '상품명', 'option_info': '옵션정보',
        'qty': '수량', 'order_amount': '최종 상품별 총 주문금액',
        'shipping_fee': '배송비 합계', 'settlement': '정산예정금액', 'cost_price': '구입가격'
    }

    if _dispatched_rows:
        df = pd.DataFrame(_dispatched_rows).rename(columns=rename_map)
        df.index = df['order_no'].astype(str)
        _src_label = f"🚀 발송 기준 ({len(df)}건) — dispatch_log"
    else:
        _get_daily = _cached_daily_orders if _cached_daily_orders else get_daily_orders
        saved_rows = _get_daily(USERNAME, calc_date_str)
        if saved_rows:
            df = pd.DataFrame(saved_rows).rename(columns=rename_map)
            df.index = df['id'].astype(str) if 'id' in df.columns else df.index.astype(str)
            _src_label = f"📋 기록 기준 ({len(df)}건) — daily_orders"
        else:
            st.warning(f"📅 {calc_date_str} 에 해당하는 주문 데이터가 없습니다.")
            return

    st.caption(_src_label)
    if '_profit_save_toast' in st.session_state:
        st.toast(st.session_state.pop('_profit_save_toast'), icon="✅")

    # ── 3. 제품 매칭 (캐싱 적용) ──────────────────────────────────────────
    receipt_items = st.session_state.get('receipt_items', [])
    _preload_user = cached_user_products(USERNAME)
    _preload_shared = cached_shared_products()
    
    _rcm_key = f"_rcm_{calc_date_str}_{len(receipt_items)}"
    if receipt_items and _rcm_key not in st.session_state:
        st.session_state[_rcm_key] = match_receipt_to_orders(receipt_items, df['상품명'].unique().tolist())
    receipt_matches = st.session_state.get(_rcm_key, {})

    # 매칭 결과 캐시 (성능 최적화)
    _mc_key = (calc_date_str, len(df), len(receipt_items), 
               tuple(sorted(st.session_state.get('kw_overrides', {}).items())))
    _cached = st.session_state.get('_pcalc_match_cache')
    
    if _cached and _cached.get('key') == _mc_key:
        costs, sources, names, pnos = _cached['costs'], _cached['sources'], _cached['names'], _cached['pnos']
    else:
        costs, sources, names, pnos = [], [], [], []
        _rcpt_by_pno = {str(ri.get('상품번호', '')): ri for ri in receipt_items if ri.get('상품번호')}
        
        for idx, r in df.iterrows():
            row_name, qty = r['상품명'], r['수량']
            saved_cost = int(r.get('구입가격', 0) or 0)
            row_key = f"{r['수취인명']}_{row_name}_{idx}_{calc_date_str}"
            
            # 수량 보정 (x N개 파싱)
            _m = re.search(r'x\s*(\d+)\s*개', row_name, re.IGNORECASE)
            sell_factor = int(_m.group(1)) if _m and 1 < int(_m.group(1)) <= 50 else 1
            total_qty = qty * sell_factor

            # 매칭 순위: 수동 -> 상품번호 -> 영수증 -> 키워드
            p = None
            if row_key in st.session_state.get('kw_overrides', {}):
                p = match_product_to_db(USERNAME, st.session_state['kw_overrides'][row_key], product_no='',
                                        _user_prods=_preload_user, _shared_prods=_preload_shared)
                source, matched_name = "수동입력", st.session_state['kw_overrides'][row_key]
            else:
                p = match_product_to_db(USERNAME, row_name, product_no=str(r.get('product_no', '') or '').strip() or None,
                                        _user_prods=_preload_user, _shared_prods=_preload_shared)
                source, matched_name = "DB-번호" if p and p.get('product_no') else "DB-키워드", (p.get('costco_name') if p else "")

            if p:
                sq = max(1, int(p.get('split_qty', 1) or 1))
                unit = p['unit_price']
                pno = str(p.get('product_no', '')).strip()
                if pno in _rcpt_by_pno:
                    unit, source = _rcpt_by_pno[pno]['단가'], "영수증"
                
                cost = (unit // sq) * total_qty
                costs.append(cost if cost > 0 else saved_cost)
                sources.append(source)
                names.append(matched_name or p.get('costco_name', ''))
                pnos.append(pno)
            elif row_name in receipt_matches:
                item = receipt_matches[row_name]
                costs.append(item['단가'] * total_qty)
                sources.append("영수증")
                names.append(item['상품명'])
                pnos.append(str(item.get('상품번호', '')))
            else:
                costs.append(saved_cost)
                sources.append("미매칭" if saved_cost == 0 else "기존값")
                names.append("")
                pnos.append("")

        st.session_state['_pcalc_match_cache'] = {'key': _mc_key, 'costs': costs, 'sources': sources, 'names': names, 'pnos': pnos}

    df['구입가격'] = costs
    df['매칭출처'] = sources
    df['매칭제품'] = names
    df['매칭상품번호'] = pnos

    # ── 4. 수동 오버라이드 및 실시간 계산 ──────────────────────────────────
    if 'cost_overrides' not in st.session_state:
        st.session_state['cost_overrides'] = {}

    for i, idx in enumerate(df.index):
        row_key = f"{df.loc[idx, '수취인명']}_{df.loc[idx, '상품명']}_{idx}_{calc_date_str}"
        widget_unit = st.session_state.get(f"c_{idx}")
        
        if widget_unit is not None:
            _qty = max(1, int(df.loc[idx, '수량'] or 1))
            _m = re.search(r'x\s*(\d+)\s*개', df.loc[idx, '상품명'], re.IGNORECASE)
            _sf = int(_m.group(1)) if _m and 1 < int(_m.group(1)) <= 50 else 1
            override_cost = int(widget_unit) * _qty * _sf
            
            if override_cost != costs[i]:
                st.session_state['cost_overrides'][row_key] = override_cost
            else:
                st.session_state['cost_overrides'].pop(row_key, None)

        if row_key in st.session_state['cost_overrides']:
            df.loc[idx, '구입가격'] = st.session_state['cost_overrides'][row_key]
            df.loc[idx, '매칭출처'] = '수동수정'

    # 수수료 및 수익 최종 계산
    _ship_fee_rate = float(_gs('naver_ship_fee_commission_rate') or 4.0)
    _ship_settle_factor = max(0.0, 1.0 - _ship_fee_rate / 100.0)
    df['실정산배송비'] = (df['배송비 합계'] * _ship_settle_factor).round().astype(int)
    df['수입'] = (df['정산예정금액'] + df['실정산배송비']) - (df['구입가격'] + shipping_cost + box_cost)

    # ── 5. UI 렌더링 — 요약 메트릭 ───────────────────────────────────────
    _counts = df['매칭출처'].value_counts()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("🟢 영수증", f"{_counts.get('영수증', 0)}건")
    c2.metric("🔵 DB-번호", f"{_counts.get('DB-번호', 0)}건")
    c3.metric("🟠 DB-키워드", f"{_counts.get('DB-키워드', 0)}건")
    c4.metric("✏️ 수동", f"{_counts.get('수동입력', 0) + _counts.get('수동수정', 0)}건")
    c5.metric("🟡 미매칭", f"{_counts.get('미매칭', 0)}건")

    # ── 6. 정산표 테이블 렌더링 ──────────────────────────────────────────
    st.subheader("📊 일별 정산 상세")
    
    # 헤더
    _TH = "text-align:{a};padding:5px;font-size:12px;color:#444;background:#f8f9fa;border-bottom:2px solid #dee2e6"
    h_sel, h_info, h_input, h_rcpt = st.columns([0.4, 9, 1.5, 0.6])
    
    # 전체 선택 로직
    _sk_list = df.index.astype(str).tolist()
    _checked = [sk for sk in _sk_list if st.session_state.get(f"sel_{sk}", False)]
    if h_sel.button("☑" if len(_checked) == len(df) else "☐", key="all_sel"):
        new_val = not (len(_checked) == len(df))
        for sk in _sk_list: st.session_state[f"sel_{sk}"] = new_val
        st.rerun()

    h_info.markdown(
        f'<table style="width:100%;border-collapse:collapse;table-layout:fixed"><thead><tr>'
        f'<th style="width:15%;{_TH.format(a="left")}">수취인</th>'
        f'<th style="width:40%;{_TH.format(a="left")}">상품명</th>'
        f'<th style="width:10%;{_TH.format(a="center")}">수량</th>'
        f'<th style="width:15%;{_TH.format(a="right")}">정산예정</th>'
        f'<th style="width:20%;{_TH.format(a="right")}">💰 수익</th>'
        f'</tr></thead></table>', unsafe_allow_html=True
    )
    h_input.markdown("<div style='text-align:center;font-size:12px;font-weight:bold'>단가✏️</div>", unsafe_allow_html=True)

    # 행 렌더링 (페이지네이션 생략 - 속도 위해)
    _SRC_COLORS = {
        '영수증': '#d4edda', 'DB-번호': '#d6eaf8', 'DB-키워드': '#fff5e6', 
        '수동입력': '#ffffff', '수동수정': '#ffffff', '미매칭': '#fff3cd'
    }
    
    for idx, r in df.iterrows():
        c_sel, c_info, c_input, c_rcpt = st.columns([0.4, 9, 1.5, 0.6])
        
        c_sel.checkbox("", key=f"sel_{idx}", label_visibility="collapsed")
        
        bg = _SRC_COLORS.get(r['매칭출처'], '#f8f9fa')
        profit = int(r['수입'])
        p_color = "#28a745" if profit >= 0 else "#dc3545"
        
        c_info.markdown(
            f'<div style="background:{bg};padding:8px;border-radius:4px;margin-bottom:2px;border:1px solid #eee">'
            f'<table style="width:100%;border-collapse:collapse;table-layout:fixed"><tr>'
            f'<td style="width:15%;font-weight:bold">{r["수취인명"][:5]}</td>'
            f'<td style="width:40%;overflow:hidden;text-overflow:ellipsis" title="{r["상품명"]}">{r["상품명"]}</td>'
            f'<td style="width:10%;text-align:center">{r["수량"]}</td>'
            f'<td style="width:15%;text-align:right">{fmt(r["정산예정금액"])}</td>'
            f'<td style="width:20%;text-align:right;font-weight:bold;color:{p_color}">{fmt(profit)}</td>'
            f'</tr></table></div>', unsafe_allow_html=True
        )
        
        # 단가 입력 (1주문 기준)
        _qty = max(1, int(r['수량']))
        _m = re.search(r'x\s*(\d+)\s*개', r['상품명'], re.IGNORECASE)
        _sf = int(_m.group(1)) if _m and 1 < int(_m.group(1)) <= 50 else 1
        current_unit = int(r['구입가격']) // (_qty * _sf)
        
        c_input.number_input("", value=current_unit, step=100, key=f"c_{idx}", 
                             label_visibility="collapsed", on_change=None)
        
        if c_rcpt.button("🧾", key=f"rcpt_{idx}", help="영수증에서 직접 선택"):
            st.session_state['rcpt_pick_target'] = idx
            st.rerun()

    # ── 7. 합계 및 저장 ────────────────────────────────────────────────
    st.divider()
    total_revenue = df['정산예정금액'].sum() + df['실정산배송비'].sum()
    total_expense = df['구입가격'].sum() + (len(df) * (shipping_cost + box_cost))
    total_profit  = df['수입'].sum()

    col_rev, col_exp, col_net = st.columns(3)
    col_rev.metric("총 수입", f"{fmt(total_revenue)}원")
    col_exp.metric("총 지출", f"{fmt(total_expense)}원")
    col_net.metric("최종 순수익", f"{fmt(total_profit)}원", delta=f"{fmt(total_profit)}원")

    # 저장 버튼
    if st.button("💾 수정사항 반영 및 제품 DB 동기화", type="primary", use_container_width=True):
        # 1) daily_orders 저장 (기록 보존)
        save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
        
        # 2) 제품 DB 업데이트
        updated_cnt = 0
        for idx, r in df.iterrows():
            cost = int(r['구입가격'])
            qty = max(1, int(r['수량']))
            _m = re.search(r'x\s*(\d+)\s*개', r['상품명'], re.IGNORECASE)
            sf = int(_m.group(1)) if _m and 1 < int(_m.group(1)) <= 50 else 1
            
            pno = str(r.get('매칭상품번호', '')).strip()
            kw = str(r.get('매칭제품', '')).strip() or r['상품명']
            
            if cost > 0 and (pno or kw):
                # split_qty 조회
                p_info = next((p for p in _preload_user if (pno and p.get('product_no')==pno) or p.get('match_keyword')==kw), None)
                sq = max(1, int((p_info or {}).get('split_qty', 1) or 1))
                
                # 저장할 단가 = (총매입가 / (주문수량*배수)) * 소분단위
                new_unit = (cost * sq) // (qty * sf)
                
                upsert_product(USERNAME, kw, kw, new_unit, product_no=pno, split_qty=sq)
                updated_cnt += 1
        
        db_invalidate_cache()
        st.session_state['_profit_save_toast'] = f"✅ {calc_date_str} 저장 및 제품 {updated_cnt}건 업데이트 완료!"
        st.rerun()

    # ── 8. 하단 영수증 등록 (선택사항) ──────────────────────────────────
    st.divider()
    with st.expander("🧾 영수증 등록 (매칭 가격 보정)", expanded=not bool(receipt_items)):
        from pages_lib import receipt_page
        receipt_page.render(USERNAME, IS_ADMIN, settings, embedded=True, order_date=calc_date_str)
