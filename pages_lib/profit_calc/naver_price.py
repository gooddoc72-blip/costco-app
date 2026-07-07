"""수익계산 — 수익 마이너스 상품의 네이버 판매가 검토·적용 패널.
profit_calc/page.py 에서 분리 (동작 불변).
"""
import streamlit as st
import pandas as pd

from db import set_naver_origin_pno
from utils import fmt

try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False
    naver_api = None


def render_loss_price_panel(df, USERNAME, api_id, api_secret,
                            shipping_cost, box_cost, _preload_user, _gs):
    """수익 마이너스 상품 목록 + 네이버 판매가/택배비 적용."""
    # ── 수익 마이너스 — 네이버 판매가 검토 및 적용 ──
    loss_df = df[(df['구입가격'] > 0) & (df['수입'] < 0)].copy()
    if len(loss_df) > 0:
        st.divider()
        st.subheader("🔴 수익 마이너스 — 네이버 판매가 검토 및 적용")
        _margin_rate = int(_gs('target_margin') or 5) / 100

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
            _unit_cfee   = _cfee // _qty
            _cur_sale    = max(100, int(_unit_settle / 0.945 / 100) * 100)
            # 1개 단위 계산: 택배비/박스비도 1개 기준(개당 비용)으로 사용
            _per_ship_u  = int(_row.get('택배원가', shipping_cost) or shipping_cost)
            _per_box_u   = int(_row.get('박스원가', box_cost) or box_cost)
            _profit_unit_cur = _unit_settle + _unit_cfee - _unit_cost - _per_ship_u - _per_box_u

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
            def _has_nv2(p):
                return (p.get('naver_channel_pno') or '') or (p.get('naver_origin_pno') or '')
            # 1순위: 매칭 + 네이버번호 있음
            _up_rec = next((p for p in _preload_user if _is_match(p) and _has_nv2(p)), None)
            # 2순위: 매칭 (PNO 없어도)
            if not _up_rec:
                _up_rec = next((p for p in _preload_user if _is_match(p)), None)
            # 3순위: 이름 유사도(≥0.5) — 정확 매칭 실패 시 가장 비슷한 등록상품
            if not _up_rec or not _has_nv2(_up_rec):
                try:
                    from services import _token_score as _ts
                    _c = [(p, max(_ts(_disp_key, p.get('costco_name', '') or ''),
                                  _ts(_disp_key, p.get('match_keyword', '') or '')))
                          for p in _preload_user if _has_nv2(p)]
                    _c = [(p, s) for p, s in _c if s >= 0.5]
                    if _c:
                        _up_rec = max(_c, key=lambda x: x[1])[0]
                except Exception:
                    pass
            # 표시번호 = 주문 자체의 네이버 상품번호(productId=channel) 최우선 →
            # 없을 때만 매칭 레코드의 channel/origin 사용 (fuzzy 매칭 오선택 방지)
            _order_nv = str(_row.get('product_no', '') or '').strip()
            _nv_pno = (_order_nv
                       or (_up_rec or {}).get('naver_channel_pno', '') or ''
                       or (_up_rec or {}).get('naver_origin_pno', '') or '')
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
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">정산금액(1개)</div>'
                    f'<div style="font-size:15px;font-weight:600">{fmt(_unit_settle)}원</div>'
                    f'</div>'
                    f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                    f'border-radius:6px;padding:8px 10px;text-align:center">'
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">고객택배비(1개)</div>'
                    f'<div style="font-size:15px;font-weight:600">{fmt(_unit_cfee)}원</div>'
                    f'</div>'
                    f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                    f'border-radius:6px;padding:8px 10px;text-align:center">'
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">구매가격(1개)</div>'
                    f'<div style="font-size:15px;font-weight:600">{fmt(_unit_cost)}원</div>'
                    f'</div>'
                    f'<div style="flex:1;min-width:90px;background:#ffe0e0;border:1px solid #faa;'
                    f'border-radius:6px;padding:8px 10px;text-align:center">'
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">현재 수익(1개)</div>'
                    f'<div style="font-size:15px;font-weight:700;color:#E74C3C">{fmt(_profit_unit_cur)}원</div>'
                    f'</div>'
                    f'</div>'
                )
                st.markdown(_card, unsafe_allow_html=True)

                # ── 수정 판매가 / 택배비 입력 ──
                _ca, _cb, _cd, _cc = st.columns([1, 3, 2, 1])
                _do = _ca.checkbox("적용", value=True, key=f"lp_chk_{_li}_{_disp_key}")
                _new_price = _cb.number_input(
                    "🔧 수정 판매가 (원)",
                    value=_suggested, min_value=100, step=100,
                    key=f"lp_price_{_li}_{_disp_key}"
                )
                _new_cfee = _cd.number_input(
                    "🔧 수정 택배비 (원)",
                    value=_unit_cfee, min_value=0, step=100,
                    key=f"lp_cfee_{_li}_{_disp_key}",
                    help="1개 기준 고객택배비"
                )
                # 1개 단위 수익: 정산(판매가×0.945) + 고객배송비 − 구입가 − 택배비 − 박스비 (모두 1개 기준)
                _new_settle = int(round(_new_price * 0.945))
                _new_profit_unit = _new_settle + _new_cfee - _unit_cost - _per_ship_u - _per_box_u
                if _new_profit_unit < 0:
                    _cc.error(f"❌ {fmt(_new_profit_unit)}원")
                else:
                    _cc.success(f"✅ +{fmt(_new_profit_unit)}원")

                # ── 네이버 상품번호 ──
                _pno_label = (
                    "✅ 네이버 상품번호 (자동 입력됨)"
                    if _nv_pno else
                    "⚠️ 네이버 상품번호 (미입력 — 직접 입력 필요)"
                )
                _pno = st.text_input(
                    _pno_label,
                    value=_nv_pno,
                    key=f"lp_pno_{_li}_{_nv_pno}",
                    placeholder="네이버 originProductNo — 미입력 시 API 적용 불가"
                )
                if _do:
                    _loss_apply.append({
                        'name': _match_kw or _disp_key,
                        'display_name': _disp_name,
                        'new_sale_price': _new_price,
                        'new_shipping_fee': _new_cfee,
                        'product_no': _pno,
                        # 로컬 origin번호 — 가격수정 API 우선 사용(채널→origin 변환 실패 방지)
                        'origin_no': (_up_rec or {}).get('naver_origin_pno') or '',
                        'product_id': (_up_rec or {}).get('id'),
                        'new_profit': _new_profit_unit,
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
                        _api_no = t.get('origin_no') or t['product_no']
                        if not _api_no:
                            _fail_msgs.append(f"{t['display_name'][:20]}: 상품번호 미입력")
                            continue
                        _r_ok, _r_err, _used_pno = naver_api.update_product_price(
                            api_id, api_secret, _api_no, t['new_sale_price'],
                            t.get('new_shipping_fee')
                        )
                        if _r_ok:
                            _ok_names.append(t['display_name'])
                            # 채널번호 → 원번호 변환됐으면 DB에 영구 저장 (다음부터 변환 생략)
                            if _used_pno and _used_pno != str(_api_no) and t.get('product_id'):
                                try:
                                    set_naver_origin_pno(USERNAME, t['product_id'], _used_pno)
                                except Exception:
                                    pass
                        else:
                            _fail_msgs.append(f"{t['display_name'][:20]}: {_r_err}")
                    if _ok_names:
                        st.success(f"✅ 네이버 판매가 적용 완료: {', '.join(_ok_names)}")
                    for _fm in _fail_msgs:
                        st.error(f"❌ {_fm}")


def render_selected_price_panel(df, USERNAME, api_id, api_secret,
                                shipping_cost, box_cost, _preload_user, _gs,
                                _checked_rows, _ids_for_sel):
    """체크박스로 선택한 상품들의 네이버 판매가 수정 패널."""
    # ── 🛒 선택 상품 네이버 가격 수정 (체크박스 선택분) ──
    if st.session_state.get('_show_naver_edit') and _checked_rows:
        st.divider()
        _neh1, _neh2 = st.columns([5, 1])
        _neh1.subheader(f"🛒 선택 상품 네이버 가격 수정 ({len(_checked_rows)}건 선택)")
        if _neh2.button("✖ 닫기", key="naver_edit_close", use_container_width=True):
            st.session_state['_show_naver_edit'] = False
            st.rerun()
        _ne_margin = int(_gs('target_margin') or 5) / 100

        # 주문상품명 기준 de-dup (같은 상품 여러 수취인 → 1회만)
        # sk(=stable key) → row 매핑 ('id' 컬럼 유무와 무관하게 안전)
        _sk_to_row = {str(_ids_for_sel[_ix]): df.iloc[_ix] for _ix in range(len(df))}
        _ne_seen = {}
        for _csk in _checked_rows:
            _crow = _sk_to_row.get(_csk)
            if _crow is None:
                continue
            _dk = (str(_crow.get('상품명', '') or '').strip()
                   or str(_crow.get('매칭제품', '') or '').strip())
            if _dk and _dk not in _ne_seen:
                _ne_seen[_dk] = _crow

        _ne_apply = []
        for _nei, (_dk, _row) in enumerate(_ne_seen.items()):
            _mkw = str(_row.get('매칭제품', '') or '').strip()
            _qty = max(1, int(_row.get('수량', 1) or 1))
            _settle = int(_row.get('정산예정금액', 0) or 0)
            _cfee = int(_row.get('배송비 합계', 0) or 0)
            _cost = int(_row.get('구입가격', 0) or 0)
            _profit = int(_row.get('수입', 0) or 0)
            _unit_cost = _cost // _qty
            _unit_settle = _settle // _qty
            _unit_cfee = _cfee // _qty
            _per_ship_u = int(_row.get('택배원가', shipping_cost) or shipping_cost)
            _per_box_u  = int(_row.get('박스원가', box_cost) or box_cost)
            _profit_unit_cur = _unit_settle + _unit_cfee - _unit_cost - _per_ship_u - _per_box_u
            _cur_sale = max(100, int(_unit_settle / 0.945 / 100) * 100)
            _min_needed = _unit_cost + shipping_cost + box_cost - _cfee / _qty
            _suggested = max(int(_min_needed * (1 + _ne_margin) / 0.945 / 100) * 100,
                             _cur_sale + 100)

            def _ne_ismatch(p, _dk=_dk, _mkw=_mkw):
                _mk = (p.get('match_keyword', '') or '').strip()
                _cn = (p.get('costco_name', '') or '').strip()
                return ((_dk and (_mk == _dk or _cn == _dk))
                        or (_mkw and (_mk == _mkw or _cn == _mkw)))
            def _has_nv(p):
                return (p.get('naver_channel_pno') or '') or (p.get('naver_origin_pno') or '')
            _up_rec = next((p for p in _preload_user
                            if _ne_ismatch(p) and _has_nv(p)), None)
            if not _up_rec:
                _up_rec = next((p for p in _preload_user if _ne_ismatch(p)), None)
            # 이름 유사도 fallback: 정확 매칭이 없거나 네이버번호가 없으면,
            # 주문명과 가장 비슷한(≥0.5) 네이버 등록상품(channel/origin 보유)을 자동 선택
            if not _up_rec or not _has_nv(_up_rec):
                try:
                    from services import _token_score as _ts
                    _cands = [(p, max(_ts(_dk, p.get('costco_name', '') or ''),
                                      _ts(_dk, p.get('match_keyword', '') or '')))
                              for p in _preload_user if _has_nv(p)]
                    _cands = [(p, s) for p, s in _cands if s >= 0.5]
                    if _cands:
                        _up_rec = max(_cands, key=lambda x: x[1])[0]
                except Exception:
                    pass
            # 표시번호 = 주문 자체의 네이버 상품번호(productId=channel) 최우선 →
            # 없을 때만 매칭 레코드 channel/origin (fuzzy 매칭 오선택 방지)
            _order_nv = str(_row.get('product_no', '') or '').strip()
            _nv_pno = (_order_nv
                       or (_up_rec or {}).get('naver_channel_pno', '') or ''
                       or (_up_rec or {}).get('naver_origin_pno', '') or '')
            _is_naver = int((_up_rec or {}).get('from_naver') or 0) == 1
            _disp_name = (_up_rec.get('costco_name', '') if _up_rec and _is_naver else '') or _dk

            with st.expander(
                f"{'🔴' if _profit < 0 else '🟢'} {_disp_name[:50]}  |  "
                f"현재수익 {fmt(_profit)}원 ({_qty}개)", expanded=True):
                # ── 현황 카드 (현재 판매가 · 정산금액 · 고객택배비 · 구매가격 · 현재 수익) ──
                _pf_bg = '#ffe0e0' if _profit < 0 else '#e8f8f0'
                _pf_bd = '#faa' if _profit < 0 else '#9adcc0'
                _pf_col = '#E74C3C' if _profit < 0 else '#1D9E75'
                _card = (
                    '<div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap">'
                    f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                    f'border-radius:6px;padding:8px 10px;text-align:center">'
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">현재 판매가</div>'
                    f'<div style="font-size:15px;font-weight:700;color:#333">{fmt(_cur_sale)}원</div></div>'
                    f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                    f'border-radius:6px;padding:8px 10px;text-align:center">'
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">정산금액(1개)</div>'
                    f'<div style="font-size:15px;font-weight:600">{fmt(_unit_settle)}원</div></div>'
                    f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                    f'border-radius:6px;padding:8px 10px;text-align:center">'
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">고객택배비(1개)</div>'
                    f'<div style="font-size:15px;font-weight:600">{fmt(_unit_cfee)}원</div></div>'
                    f'<div style="flex:1;min-width:90px;background:#f8f8f8;border:1px solid #eee;'
                    f'border-radius:6px;padding:8px 10px;text-align:center">'
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">구매가격(1개)</div>'
                    f'<div style="font-size:15px;font-weight:600">{fmt(_unit_cost)}원</div></div>'
                    f'<div style="flex:1;min-width:90px;background:{_pf_bg};border:1px solid {_pf_bd};'
                    f'border-radius:6px;padding:8px 10px;text-align:center">'
                    f'<div style="font-size:11px;color:#888;margin-bottom:2px">현재 수익(1개)</div>'
                    f'<div style="font-size:15px;font-weight:700;color:{_pf_col}">{fmt(_profit_unit_cur)}원</div></div>'
                    '</div>'
                )
                st.markdown(_card, unsafe_allow_html=True)

                _ca, _cb, _cd, _cc = st.columns([1, 3, 2, 1])
                _do = _ca.checkbox("적용", value=True, key=f"np_chk_{_nei}_{_dk}")
                _new_price = _cb.number_input("🔧 수정 판매가 (원)", value=_suggested,
                                              min_value=100, step=100, key=f"np_price_{_nei}_{_dk}")
                _new_cfee = _cd.number_input("🔧 수정 택배비 (원)", value=_unit_cfee,
                                             min_value=0, step=100, key=f"np_cfee_{_nei}_{_dk}",
                                             help="1개 기준 고객택배비")
                # 1개 단위 수익: 정산(판매가×0.945) + 고객배송비 − 구입가 − 택배비 − 박스비 (모두 1개 기준)
                _new_settle = int(round(_new_price * 0.945))
                _new_profit_unit = _new_settle + _new_cfee - _unit_cost - _per_ship_u - _per_box_u
                if _new_profit_unit < 0:
                    _cc.error(f"❌ {fmt(_new_profit_unit)}원")
                else:
                    _cc.success(f"✅ +{fmt(_new_profit_unit)}원")
                _pno = st.text_input(
                    ("✅ 네이버 상품번호 (자동 입력됨)" if _nv_pno
                     else "⚠️ 네이버 상품번호 (미입력 — 직접 입력 필요)"),
                    value=_nv_pno, key=f"np_pno_{_nei}_{_nv_pno}",
                    placeholder="네이버 originProductNo / channelProductNo")
                if _do:
                    _ne_apply.append({
                        'display_name': _disp_name,
                        'new_sale_price': _new_price,
                        'new_shipping_fee': _new_cfee,
                        'product_no': _pno,
                        # 로컬에 저장된 origin번호 — 가격수정 API에 우선 사용(채널→origin 변환 실패 방지)
                        'origin_no': (_up_rec or {}).get('naver_origin_pno') or '',
                        'product_id': (_up_rec or {}).get('id'),
                    })

        if _ne_apply and st.button(
                f"✅ 선택 {len(_ne_apply)}개 네이버 판매가 적용",
                type="primary", key="ne_naver_apply", use_container_width=True):
            if not api_id or not api_secret:
                st.error("설정 탭에서 네이버 API 키를 등록해주세요.")
            elif not HAS_NAVER_API:
                st.error("naver_api.py 모듈이 없습니다.")
            else:
                _ok_names, _fail_msgs = [], []
                for t in _ne_apply:
                    _api_no = t.get('origin_no') or t['product_no']
                    if not _api_no:
                        _fail_msgs.append(f"{t['display_name'][:20]}: 상품번호 미입력")
                        continue
                    _r_ok, _r_err, _used_pno = naver_api.update_product_price(
                        api_id, api_secret, _api_no, t['new_sale_price'],
                        t.get('new_shipping_fee'))
                    if _r_ok:
                        _ok_names.append(t['display_name'])
                        if _used_pno and _used_pno != str(_api_no) and t.get('product_id'):
                            try:
                                set_naver_origin_pno(USERNAME, t['product_id'], _used_pno)
                            except Exception:
                                pass
                    else:
                        _fail_msgs.append(f"{t['display_name'][:20]}: {_r_err}")
                if _ok_names:
                    st.success(f"✅ 네이버 판매가 적용 완료: {', '.join(_ok_names)}")
                for _fm in _fail_msgs:
                    st.error(f"❌ {_fm}")
