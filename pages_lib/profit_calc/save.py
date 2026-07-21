"""수익계산 — 저장 핸들러(정산저장 / 제품가격DB / 정산데이터저장).
profit_calc/page.py 에서 분리 (동작 불변). 각 함수는 해당 버튼 클릭 시 호출.
"""
from datetime import datetime

import streamlit as st

from db import (
    save_profit_settlements, save_settlement_override,
    save_daily_orders, upsert_product, get_user_db,
)


def save_settlements(df, USERNAME, calc_date_str, shipping_cost, box_cost, _checked_rows):
    _ps_save_rows = []
    _cost_ov_s = st.session_state.get('cost_overrides', {}) or {}
    _kw_ov_s   = st.session_state.get('kw_overrides', {}) or {}
    _ids_save  = df['_sk'].values if '_sk' in df.columns else (df['id'].values if 'id' in df.columns else df.index.values)
    import re as _re_ps
    for _si, (_sidx, _sr) in enumerate(df.iterrows()):
        _ssk  = str(_ids_save[_si])
        _skey = f"{_sr['수취인명']}_{_sr['상품명']}_{_ssk}_{calc_date_str}"
        _cost = _cost_ov_s.get(_skey, int(_sr.get('구입가격', 0) or 0))
        _per_ship = int(st.session_state.get(f"ship_{_ssk}", shipping_cost))
        _per_box  = int(st.session_state.get(f"box_{_ssk}", box_cost))
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


def save_price_db(df, USERNAME, calc_date_str, shipping_cost, box_cost, _preload_user, invalidate_data_cache):
    save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
    # 수익계산 결과도 함께 저장 (profit_settlements)
    _ps_rc_rows = []
    _ids_rc = df['_sk'].values if '_sk' in df.columns else (df['id'].values if 'id' in df.columns else df.index.values)
    _kw_ov_rc = st.session_state.get('kw_overrides', {}) or {}
    _co_rc = st.session_state.get('cost_overrides', {}) or {}
    import re as _re_rc
    for _rci, (_rcidx, _rcr) in enumerate(df.iterrows()):
        _rcsk   = str(_ids_rc[_rci])
        _rckey  = f"{_rcr['수취인명']}_{_rcr['상품명']}_{_rcsk}_{calc_date_str}"
        _rccost = _co_rc.get(_rckey, int(_rcr.get('구입가격', 0) or 0))
        _rcship = int(st.session_state.get(f"ship_{_rcsk}", shipping_cost))
        _rcbox  = int(st.session_state.get(f"box_{_rcsk}", box_cost))
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


def save_all(df, USERNAME, calc_date_str, shipping_cost, box_cost, _preload_user, invalidate_data_cache):
    save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
    # 수익계산 결과 profit_settlements에도 저장
    _ps_all_rows = []
    _ids_all = df['_sk'].values if '_sk' in df.columns else (df['id'].values if 'id' in df.columns else df.index.values)
    _kw_ov_all = st.session_state.get('kw_overrides', {}) or {}
    _co_all = st.session_state.get('cost_overrides', {}) or {}
    import re as _re_all
    for _alli, (_allidx, _allr) in enumerate(df.iterrows()):
        _allsk  = str(_ids_all[_alli])
        _allkey = f"{_allr['수취인명']}_{_allr['상품명']}_{_allsk}_{calc_date_str}"
        _allcost = _co_all.get(_allkey, int(_allr.get('구입가격', 0) or 0))
        _allship = int(st.session_state.get(f"ship_{_allsk}", shipping_cost))
        _allbox  = int(st.session_state.get(f"box_{_allsk}", box_cost))
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
