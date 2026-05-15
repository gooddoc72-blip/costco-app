"""💰 수익 계산 페이지 — Refactored & High Performance.
구조: 데이터 로드 -> 배치 매칭 -> 오버라이드 병합 -> 수입 계산 -> UI 렌더링 -> 저장
"""
import os
import re
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

from db import (
    get_user_db, get_daily_orders, save_daily_orders,
    get_dispatched_orders_with_details, upsert_product,
)
from services import match_product_to_db, _index_products
from utils import fmt

# app.py에서 주입되는 캐시 헬퍼
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
    st.header("💰 수익 계산 (고속 모드)")
    
    # ── 1. 설정 및 필터 ──────────────────────────────────────────
    ship_cost = int(settings.get('shipping_cost') or 1800)
    box_cost = int(settings.get('box_cost') or 300)
    ship_fee_rate = float(settings.get('naver_ship_fee_commission_rate') or 4.0)
    
    col_date, col_refresh, _ = st.columns([1.5, 1, 4])
    with col_date:
        calc_date = st.date_input("계산할 주문 날짜 선택", value=datetime.today() - timedelta(days=1))
        calc_date_str = calc_date.strftime("%Y-%m-%d")
    
    with col_refresh:
        st.write(""); st.write("")
        if st.button("🔄 새로고침", use_container_width=True):
            if invalidate_data_cache: invalidate_data_cache()
            st.session_state.pop('_pcalc_batch_cache', None)
            st.rerun()

    # ── 2. 데이터 로드 ────────────────────────────────────────────
    # 발송 이력 우선, 없으면 일일 주문 fallback
    rows = get_dispatched_orders_with_details(USERNAME, calc_date_str)
    if rows:
        df = pd.DataFrame(rows)
        df.index = df['order_no'].astype(str)
        src = "dispatch_log"
    else:
        rows = get_daily_orders(USERNAME, calc_date_str)
        if not rows:
            st.warning(f"📅 {calc_date_str} 에 해당하는 주문 데이터가 없습니다.")
            return
        df = pd.DataFrame(rows)
        df.index = df['id'].astype(str)
        src = "daily_orders"

    # 컬럼 표준화
    rename_map = {
        'recipient': '수취인명', 'product_name': '상품명', 'qty': '수량',
        'order_amount': '주문금액', 'shipping_fee': '배송비', 'settlement': '정산예정',
        'cost_price': '기존구입가'
    }
    df = df.rename(columns=rename_map)
    # save_daily_orders가 기대하는 컬럼 별칭 보강 (옛 네이버 엑셀 컬럼명)
    if '최종 상품별 총 주문금액' not in df.columns:
        df['최종 상품별 총 주문금액'] = df.get('주문금액', 0)
    if '배송비 합계' not in df.columns:
        df['배송비 합계'] = df.get('배송비', 0)
    if '정산예정금액' not in df.columns:
        df['정산예정금액'] = df.get('정산예정', 0)
    if '구입가격' not in df.columns:
        df['구입가격'] = 0  # ← 매칭 이후 채워짐
    if '상품번호' not in df.columns:
        df['상품번호'] = df.get('product_no', '')
    if '옵션정보' not in df.columns:
        df['옵션정보'] = df.get('option_info', '')

    # ── 3. 배치 매칭 (성능의 핵심) ──────────────────────────────────
    # 한 번의 렌더링 주기 동안 모든 행을 매칭하고 캐시함.
    # 캐시 키: 날짜 + 주문건수 + 수동수정건수
    _cache_key = f"{calc_date_str}_{len(df)}_{len(st.session_state.get('kw_overrides', {}))}"
    if st.session_state.get('_pcalc_batch_cache_key') != _cache_key:
        with st.spinner("상품 매칭 중..."):
            user_prods = cached_user_products(USERNAME)
            shared_prods = cached_shared_products()
            
            costs, matches, sources, pnos = [], [], [], []
            for _row_idx, r in df.iterrows():
                p_name, p_no = r['상품명'], str(r.get('product_no', '') or '').strip()
                n_pno = str(r.get('naver_origin_pno', '') or '').strip()
                # row_key는 stable id 포함 (같은 수취인+상품 중복 주문 collision 방지)
                row_key = f"{r['수취인명']}_{p_name}_{_row_idx}_{calc_date_str}"
                
                # 매칭 순위: 수동 -> 네이버번호 -> 상품번호 -> 이름/키워드
                p = None
                if row_key in st.session_state.get('kw_overrides', {}):
                    p = match_product_to_db(USERNAME, st.session_state['kw_overrides'][row_key], 
                                            naver_pno=n_pno or None,
                                            _user_prods=user_prods, _shared_prods=shared_prods)
                    source = "수동"
                else:
                    p = match_product_to_db(USERNAME, p_name, product_no=p_no or None,
                                            naver_pno=n_pno or None,
                                            _user_prods=user_prods, _shared_prods=shared_prods)
                    source = "DB-네이버" if p and p.get('naver_origin_pno') else ("DB-번호" if p and p.get('product_no') else "DB-키워드")
                
                if p:
                    sq = max(1, int(p.get('split_qty', 1) or 1))
                    
                    # [버그 수정] DB의 unit_price는 이미 해당 번들(예: x 2개)의 가격이므로, 
                    # 주문 상품명에 'x 2개'가 있다고 해서 또 2를 곱하면 안 됨.
                    # 오직 '소분(split_qty)'과 '주문 수량(r['수량'])'만 계산에 반영.
                    cost = (int(p['unit_price']) // sq) * int(r['수량'])
                    
                    costs.append(cost)
                    matches.append(p['costco_name'])
                    sources.append(source)
                    pnos.append(p.get('product_no', ''))
                else:
                    costs.append(int(r.get('기존구입가', 0) or 0))
                    matches.append("")
                    sources.append("미매칭")
                    pnos.append("")
            
            st.session_state['_pcalc_batch_cache'] = (costs, matches, sources, pnos)
            st.session_state['_pcalc_batch_cache_key'] = _cache_key

    costs, matches, sources, pnos = st.session_state['_pcalc_batch_cache']
    df['구입가'] = costs
    df['매칭제품'] = matches
    df['출처'] = sources
    df['상품번호'] = pnos

    # ── 4. 수동 오버라이드 실시간 반영 ──────────────────────────────
    for idx in df.index:
        override = st.session_state.get(f"c_{idx}")
        if override is not None and override != (df.loc[idx, '구입가'] // df.loc[idx, '수량']):
            # 위젯 단가는 1회 주문 기준이므로 수량 곱해줌
            df.loc[idx, '구입가'] = int(override) * int(df.loc[idx, '수량'])
            df.loc[idx, '출처'] = "수동수정"

    # 수입 계산
    ship_factor = 1.0 - (ship_fee_rate / 100.0)
    df['실정산배송비'] = (df['배송비'] * ship_factor).round().astype(int)
    # 수입 계산 (고정비를 행별로 배분하여 개별 수익성 체크)
    num_orders = len(df)
    overhead_per_row = (ship_cost + box_cost) / num_orders if num_orders > 0 else 0
    ship_factor = 1.0 - (ship_fee_rate / 100.0)
    
    df['실정산배송비'] = (df['배송비'] * ship_factor).round().astype(int)
    # 수익 = (정산예정 + 실배송비) - (구입가 + 배분된 고정비)
    df['수익'] = (df['정산예정'] + df['실정산배송비']) - (df['구입가'] + overhead_per_row)

    # 요약 메트릭
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("총 정산", f"{fmt(df['정산예정'].sum() + df['실정산배송비'].sum())}원")
    m2.metric("총 매입", f"{fmt(df['구입가'].sum())}원")
    _total_profit = df['수익'].sum()
    m3.metric("순수익", f"{fmt(_total_profit)}원", delta=f"{fmt(_total_profit)}원")
    m4.metric("매칭률", f"{(len(df[df['출처']!='미매칭'])/len(df)*100):.1f}%")

    # ── 5. UI 렌더링 ──────────────────────────────────────────────
    st.subheader("📋 정산 명세 (개별 수익성 포함)")
    st.info(f"💡 택배비/박스비({fmt(ship_cost+box_cost)}원)가 각 품목별로 {fmt(int(overhead_per_row))}원씩 배분되어 계산되었습니다.")
    
    # 헤더
    h_sel, h_info, h_cost, h_act = st.columns([0.5, 7, 1.5, 1])
    h_info.markdown("**수취인 | 매칭제품 | 주문 상품명**")
    
    for idx, r in df.iterrows():
        c_sel, c_info, c_cost, c_act = st.columns([0.5, 7, 1.5, 1])
        
        c_sel.checkbox("", key=f"sel_{idx}", label_visibility="collapsed")
        
        # 디자인 박스
        _color = "#d4edda" if r['출처'] in ('영수증', 'DB-번호') else ("#fff3cd" if r['출처'] == '미매칭' else "#f8f9fa")
        _profit = int(r['수익'])
        _p_style = "color:#28a745;font-weight:bold" if _profit >= 0 else "color:#dc3545;font-weight:bold"
        
        c_info.markdown(f"""
        <div style="background:{_color}; padding:8px; border-radius:4px; margin-bottom:4px; border:1px solid #ddd">
            <table style="width:100%; border-collapse:collapse; font-size:13px">
                <tr>
                    <td style="width:15%; font-weight:bold">{r['수취인명'][:5]}</td>
                    <td style="width:25%; color:#555">[{r['매칭제품'] or '미매칭'}]</td>
                    <td style="width:40%">{r['상품명']}</td>
                    <td style="width:10%; text-align:center">x{r['수량']}</td>
                    <td style="width:10%; text-align:right; {_p_style}">{fmt(_profit)}</td>
                </tr>
            </table>
        </div>
        """, unsafe_allow_html=True)
        
        # 단가 입력 (1개 기준)
        unit_cost = int(r['구입가']) // int(r['수량'])
        c_cost.number_input("단가", value=unit_cost, step=100, key=f"c_{idx}", label_visibility="collapsed")
        
        if c_act.button("✏️", key=f"btn_{idx}", help="수동 키워드 수정"):
            st.session_state['edit_target'] = idx
            st.rerun()

    # ── 6. 저장 및 동기화 ──────────────────────────────────────────
    st.divider()
    if st.button("💾 수정사항 저장 및 제품 DB 업데이트", type="primary", use_container_width=True):
        with st.spinner("데이터 저장 중..."):
            # 1) daily_orders 저장 (개별 행별 profit 반영)
            # save_daily_orders 내부에서 ship_cost, box_cost를 0으로 넘겨서 중복 차감 방지 (이미 r['수익']에 포함됨)
            # 하지만 기존 로직 유지를 위해 r['수익']을 다시 계산할 수도 있음. 
            # 여기서는 r['수익'] 컬럼이 포함된 df를 그대로 넘김.
            # db_orders가 사용할 표준 컬럼명으로 매칭/계산 결과 동기화
            df['구입가격'] = df['구입가']
            # ship_cost/box_cost는 daily_orders.delivery_cost/box_cost 컬럼에 그대로 기록.
            # profit 컬럼은 db_orders가 r['수익']을 우선 사용하므로 배분된 값 유지됨.
            save_daily_orders(USERNAME, calc_date_str, df, ship_cost, box_cost)
            
            # 2) 제품 DB 동기화
            for idx, r in df.iterrows():
                # 수동 수정 건이거나, 미매칭이었던 건을 수동 입력한 경우
                if r['출처'] in ('수동수정', '수동입력'):
                    # 매칭 정책(line ~109)과 일관: DB unit_price는 1주문 단가, sell_factor 사용 안 함.
                    # 저장 공식도 sf 곱셈 제거 → new_unit = (cost × sq) // qty
                    _naver_origin = str(r.get('naver_origin_pno', '') or '').strip()
                    p = match_product_to_db(USERNAME, r['상품명'],
                                            product_no=r.get('상품번호'),
                                            naver_pno=_naver_origin or None,
                                            _user_prods=cached_user_products(USERNAME))
                    sq = max(1, int((p or {}).get('split_qty', 1) or 1))

                    new_unit = (int(r['구입가']) * sq) // max(1, int(r['수량']))
                    
                    # 네이버 원본 상품번호(naver_origin_pno)가 있다면 이를 최우선으로 저장
                    # 소분 제품(동일 코스트코 번호) 구분을 위함
                    origin_pno = r.get('naver_origin_pno', '')
                    
                    upsert_product(USERNAME, r['매칭제품'] or r['상품명'], r['매칭제품'] or r['상품명'], 
                                   new_unit, product_no=r.get('상품번호'), split_qty=sq, 
                                   naver_origin_pno=origin_pno,
                                   auto_split_costco_no=True)
            
            if invalidate_data_cache: invalidate_data_cache()
            st.success("✅ 저장 및 DB 업데이트가 완료되었습니다.")
            st.rerun()
