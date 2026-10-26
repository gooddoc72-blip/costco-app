"""수익계산 — 정산표 데이터 로드.
(그날 송장 등록으로 발송처리된 건 + 온라인몰 직배송 건)만 df로 만든다. 저장값(profit_settlements)은 행을 만들지 않고 값만 덮어쓴다.
profit_settlements 소스면 저장값(ship/box/cost/kw)을 session_state로 복원한다.
profit_calc/page.py 에서 분리 (동작 불변).

구조:
  build_settlement_df() — 세션 비의존 순수 DB→df (Streamlit/FastAPI 공용).
  load_settlement_df()  — build + profit_settlements 소스일 때 session_state 복원.
"""
import pandas as pd
import streamlit as st

from db import (
    get_dispatched_orders_with_details, get_profit_settlements,
    search_order_history, get_daily_orders,
)


def build_settlement_df(USERNAME, calc_date_str, _cached_daily_orders=None):
    """(df, src_label, source_kind) 반환 — 세션 비의존(순수 DB→df).

    source_kind: 'ps' | 'dispatch' | None.  (행 = 그날 발송 기록 + 온라인몰 직배송, 그 외는 없음)
    df 없으면 (None, label|None, None).
    """
    # ⭐ 신규 데이터 소스: dispatch_log (일괄발송 성공건) + order_history JOIN
    # → "이 날짜에 발송된 주문" = "이 날짜의 수익계산 대상" 으로 일치 보장
    # → 결제일 필터/저장 액션 불필요, daily_orders 의존 제거
    _src_label = None
    _source_kind = None
    _dispatched_rows = get_dispatched_orders_with_details(USERNAME, calc_date_str)
    # 코스트코 온라인몰 직배송 — 우리 송장 등록이 없어도 실제로 판 주문이다(그날 정산일로 표시된 것)
    try:
        from db_dispatch_log import get_online_orders_with_details
        _dispatched_rows = list(_dispatched_rows or []) + get_online_orders_with_details(
            USERNAME, calc_date_str, exclude={str(r.get('order_no')) for r in _dispatched_rows or []})
    except Exception:
        pass

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

    _ps_rows_src = get_profit_settlements(USERNAME, calc_date_str)
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

    if _dispatched_rows:
        # ⭐ 행 집합은 '그날 송장이 찍힌 건'(dispatch_log)이 정한다.
        #    정산저장(profit_settlements)은 행을 만들지 않고 저장값만 덮어쓴다.
        #    - 정산저장은 '선택한 행만' 저장하므로 그것으로 행을 정하면
        #      아직 저장 안 한 발송건이 통째로 사라진다.
        #    - 반대로 발송 안 된 주문을 정산저장해 두면 그 날짜에 유령 행이 남는다.
        #    송장 업로드 = 그날 실제로 나간 것 → 수익계산 대상도 그것과 일치해야 한다.
        df = pd.DataFrame(_dispatched_rows).rename(columns=rename_map)
        if 'order_no' in df.columns:
            df.index = df['order_no'].astype(str)
            df.index.name = None

        _saved = {str(r.get('order_no', '')): r for r in (_ps_rows_src or [])}
        _hit = sum(1 for k in df.index.astype(str) if k in _saved)
        if _hit:
            _keys = list(df.index.astype(str))

            def _from_saved(col):
                """저장값만 채우고 없으면 None — 전역 기본값이 적용되게 둔다."""
                return [(_saved[k].get(col) if k in _saved else None) for k in _keys]

            df['택배원가'] = _from_saved('delivery_cost')
            df['박스원가'] = _from_saved('box_cost')
            df['matched_keyword'] = _from_saved('matched_keyword')
            # 구입가격은 저장값 우선, 없으면 order_history 값 유지
            _base_cost = df['구입가격'] if '구입가격' in df.columns else [0] * len(df)
            df['구입가격'] = [
                (_saved[k].get('cost_price') if k in _saved and _saved[k].get('cost_price')
                 else _b)
                for k, _b in zip(_keys, _base_cost)
            ]
        for _c in ('수량', '최종 상품별 총 주문금액', '배송비 합계',
                   '제주/도서 추가배송비', '정산예정금액', '구입가격'):
            if _c in df.columns:
                df[_c] = pd.to_numeric(df[_c], errors='coerce').fillna(0).astype(int)

        _n_onl = sum(1 for r in _dispatched_rows if r.get('platform') == 'online')
        _n_dsp = len(df) - _n_onl
        _src_label = (f"🚀 발송 {_n_dsp}건 (송장 기준)" + (f" + 🛒 온라인몰 직배송 {_n_onl}건" if _n_onl else "")
                      + (f" — 정산저장 {_hit}건 반영" if _hit else ""))
        # 저장값이 하나라도 있으면 session 복원 루프를 태워야 한다
        _source_kind = 'ps' if _hit else 'dispatch'

    else:
        # 그날 송장 등록으로 발송처리된 건도, 온라인몰 직배송 건도 없다 — 수익계산 대상이 없다.
        #   예전에는 저장값·주문이력(결제일)·daily_orders로 대신 보여 줬는데, 그러면 발송하지 않은
        #   주문이 그 날짜 수익으로 저장돼 통계가 어긋났다(10/26: 결제일 기준 행 109·발송 없는 행 186).
        #   수익계산은 무조건 '송장 등록으로 발송처리된 건 + 온라인몰 직배송 건'이다.
        df = None
        _src_label = ("ℹ️ 이 날짜에 송장 등록으로 발송처리된 건(온라인몰 직배송 포함)이 없습니다 — "
                      "수익계산은 발송된 건만 합니다.")
        if _ps_rows_src:
            _src_label += (f" ⚠️ 이 날짜에 발송 기록 없이 저장된 수익 {len(_ps_rows_src)}건이 있습니다"
                           " — 발송 기준이 아니라 수익 통계에서 정리할 대상입니다.")
    # ⚓ 단일 안정키(_sk): 항상 '문자열'로 통일.
    #   정수 id 컬럼은 iterrows()에서 실수(143→143.0)로 승격돼 위젯키/계산키가 어긋난다.
    #   → 여기서 문자열 컬럼으로 고정해 전 구간이 동일한 키를 쓰게 한다. (삭제·박스·택배·체크박스 키 일치)
    if df is not None and '_sk' not in df.columns:
        if 'id' in df.columns:
            df['_sk'] = df['id'].astype('Int64').astype(str)
        else:
            df['_sk'] = df.index.astype(str)
    return df, _src_label, _source_kind


def load_settlement_df(USERNAME, calc_date_str, _cached_daily_orders=None):
    """(df, src_label) 반환. df 없으면 (None, label|None).
    profit_settlements 소스면 저장값(ship/box/cost/kw)을 session_state로 복원."""
    df, _src_label, _source_kind = build_settlement_df(
        USERNAME, calc_date_str, _cached_daily_orders)

    if _source_kind == 'ps' and df is not None:
        # session_state에 저장값 복합키로 직접 주입 (kw/cost/ship/box 모두)
        _restore_flag_ps = f"_do_restored_{calc_date_str}"
        if not st.session_state.get(_restore_flag_ps):
            if 'cost_overrides' not in st.session_state:
                st.session_state['cost_overrides'] = {}
            if 'kw_overrides' not in st.session_state:
                st.session_state['kw_overrides'] = {}
            def _saved_int(v):
                """저장값만 복원. 미정산 발송건은 택배원가/박스원가 컬럼이 없어
                NaN이 오는데, 그건 '저장된 적 없음'이라 전역 기본값을 써야 한다.
                (int(NaN)은 ValueError라 그대로 두면 페이지가 죽는다)"""
                try:
                    if v is None or pd.isna(v):
                        return None
                    return int(v)
                except (TypeError, ValueError):
                    return None

            _ids_ps = df.index.values
            for _pi, (_pidx, _pr) in enumerate(df.iterrows()):
                _psk = str(_ids_ps[_pi])
                _pkey = f"{_pr.get('수취인명', '')}_{_pr.get('상품명', '')}_{_psk}_{calc_date_str}"
                _pcp = _saved_int(_pr.get('구입가격')) or 0
                if _pcp > 0:
                    st.session_state['cost_overrides'][_pkey] = _pcp
                _pship = _saved_int(_pr.get('택배원가'))
                if _pship is not None and f"ship_{_psk}" not in st.session_state:
                    st.session_state[f"ship_{_psk}"] = _pship
                _pbox = _saved_int(_pr.get('박스원가'))
                if _pbox is not None and f"box_{_psk}" not in st.session_state:
                    st.session_state[f"box_{_psk}"] = _pbox
                _pkw = str(_pr.get('matched_keyword') or '')
                if _pkw:
                    st.session_state['kw_overrides'][_pkey] = _pkw
            st.session_state[_restore_flag_ps] = True

    return df, _src_label
