"""수익계산 — 정산표 데이터 로드.
profit_settlements → dispatch_log → order_history → daily_orders 순으로 df 구성.
profit_settlements 소스면 저장값(ship/box/cost/kw)을 session_state로 복원한다.
profit_calc/page.py 에서 분리 (동작 불변).
"""
import pandas as pd
import streamlit as st

from db import (
    get_dispatched_orders_with_details, get_profit_settlements,
    search_order_history, get_daily_orders,
)


def load_settlement_df(USERNAME, calc_date_str, _cached_daily_orders=None):
    """(df, src_label) 반환. df 없으면 (None, label|None)."""
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

    # 0순위: profit_settlements (정산저장된 확정 데이터 — 최우선)
    _ps_rows_src = get_profit_settlements(USERNAME, calc_date_str)
    if _ps_rows_src:
        df = pd.DataFrame(_ps_rows_src)
        _ps_col_map = {
            'recipient': '수취인명', 'product_name': '상품명',
            'option_info': '옵션정보', 'qty': '수량',
            'order_amount': '최종 상품별 총 주문금액',
            'shipping_fee': '배송비 합계',
            'extra_shipping': '제주/도서 추가배송비',
            'settlement_amount': '정산예정금액',
            'cost_price': '구입가격',
            'delivery_cost': '택배원가',
            'box_cost': '박스원가',
        }
        df = df.rename(columns=_ps_col_map)
        if 'id' in df.columns:
            df = df.drop(columns=['id'])  # profit_settlements PK 제거 — order_no를 stable_key로 사용
        if 'order_no' in df.columns:
            df.index = df['order_no'].astype(str)
            df.index.name = None
        _src_label = f"✅ 정산완료 ({len(df)}건) — profit_settlements"
        # session_state에 저장값 복합키로 직접 주입 (kw/cost/ship/box 모두)
        _restore_flag_ps = f"_do_restored_{calc_date_str}"
        if not st.session_state.get(_restore_flag_ps):
            if 'cost_overrides' not in st.session_state:
                st.session_state['cost_overrides'] = {}
            if 'kw_overrides' not in st.session_state:
                st.session_state['kw_overrides'] = {}
            _ids_ps = df.index.values
            for _pi, (_pidx, _pr) in enumerate(df.iterrows()):
                _psk = str(_ids_ps[_pi])
                _pkey = f"{_pr.get('수취인명', '')}_{_pr.get('상품명', '')}_{_psk}_{calc_date_str}"
                _pcp = int(_pr.get('구입가격', 0) or 0)
                if _pcp > 0:
                    st.session_state['cost_overrides'][_pkey] = _pcp
                _pship = int(_pr.get('택배원가', 0) or 0)
                if _pship > 0 and f"ship_{_psk}" not in st.session_state:
                    st.session_state[f"ship_{_psk}"] = _pship
                _pbox = int(_pr.get('박스원가', 0) or 0)
                if _pbox > 0 and f"box_{_psk}" not in st.session_state:
                    st.session_state[f"box_{_psk}"] = _pbox
                _pkw = str(_pr.get('matched_keyword') or '')
                if _pkw:
                    st.session_state['kw_overrides'][_pkey] = _pkw
            st.session_state[_restore_flag_ps] = True

    elif _dispatched_rows:
        df = pd.DataFrame(_dispatched_rows)
        df = df.rename(columns=rename_map)
        # order_no를 stable_key 로 사용 (dispatch_log UNIQUE)
        if 'order_no' in df.columns:
            df.index = df['order_no'].astype(str)
            df.index.name = None
        _src_label = f"🚀 발송 기준 ({len(df)}건) — dispatch_log"
    else:
        # Fallback 1: order_history (결제일 기준) — 각 주문이 자기 날짜에 정확히 있어
        # '그 날짜 주문건'만 정확히 로드 (daily_orders 누적 오염 회피)
        _hist_rows = search_order_history(USERNAME, date_from=calc_date_str, date_to=calc_date_str)
        if _hist_rows:
            df = pd.DataFrame(_hist_rows)
            df = df.rename(columns=rename_map)
            if '제주/도서 추가배송비' not in df.columns:
                df['제주/도서 추가배송비'] = 0
            if 'order_no' in df.columns:
                df.index = df['order_no'].astype(str)
                df.index.name = None
            _src_label = f"📋 주문이력 ({len(df)}건) — order_history (결제일 기준)"
        else:
            # Fallback 2: 옛 daily_orders 데이터 (order_history 없을 때만)
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
    return df, _src_label
