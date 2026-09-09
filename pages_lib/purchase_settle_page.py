"""🔗 구매가 · 매핑 관리 (관리자)

영수증 정산이 상품을 제대로 찾고 올바른 단가를 붙이도록 구매가와 번호 매핑을
손보는 도구 모음이다. 청구액을 만드는 화면이 아니다 — 청구는 정산 원장(db_settle)
하나에서만 나온다.

예전 이름은 '구매내역 정산'이었고 여기서 예상→확정 스냅샷을 따로 저장했다.
같은 청구액을 영수증 정산과 이 화면이 각자 계산해 값이 어긋났고
(9/7 oxo: 원장 780,720 vs 스냅샷 184,560), 그게 이 시스템이 복잡해진 큰 이유였다.
"""
from datetime import date, timedelta

import streamlit as st
import pandas as pd

from db import (get_all_users, get_shared_products,
                get_split_rules, upsert_split_rule, delete_split_rule,
                get_price_log, PRICE_SOURCES,
                collect_shared_naver_map, get_costco_conflicts,
                resolve_costco_conflict, clear_costco_mapping)
from db_purchase_settle import (
    compute_daily_purchase, suggest_shared_matches, link_product_mapping,
)
from utils import fmt


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def _sellers():
    """정산·청구 대상 사용자 — 관리자와 '직접구매' 계정은 뺀다.

    직접 매장에서 사는 계정(주문건도 관리자에게 보내지 않는다)을 넣으면
    발송건이 전부 미매칭으로 쌓이고, 어쩌다 붙으면 사지도 않은 물건이 청구된다.
    """
    from receipt_settle import billable_users
    return billable_users()


def _render_billing_scope(dmap, USERNAME):
    """계정별로 구매대행 대상인지 지정한다.

    모든 사용자가 대행 대상은 아니다. 직접 매장에서 사고 주문건도 관리자에게
    보내지 않는 계정이 있는데, 그런 계정을 정산에 넣으면 발송건이 전부
    미매칭으로 쌓이고(9/7 미매칭 35건 중 23건) 어쩌다 이름이 겹쳐 붙으면
    사지도 않은 물건이 청구된다(9/7 23,970원).
    """
    from db import get_all_users, get_setting, set_setting
    from receipt_settle import SELF_PURCHASE_KEY

    with st.expander("👥 정산 대상 계정 — 구매대행 / 직접구매", expanded=False):
        st.caption("**구매대행**: 관리자가 대신 사서 보내는 계정 → 영수증 매칭·청구 대상입니다."
                   " / **직접구매**: 본인이 매장에서 사는 계정 → 정산·청구에서 **제외**됩니다. "
                   "발송건이 미매칭으로 쌓이지 않고, 청구도 잡히지 않습니다.")
        _us = [u for u in (get_all_users() or []) if not u.get('is_admin')]
        if not _us:
            st.caption("사용자가 없습니다.")
            return
        _rows = []
        for u in _us:
            _self = str(get_setting(u['username'], SELF_PURCHASE_KEY) or '').strip() == '1'
            _rows.append({'직접구매(제외)': _self,
                          '사용자': dmap.get(u['username'], u['username']),
                          '계정': u['username']})
        _ed = st.data_editor(
            pd.DataFrame(_rows), use_container_width=True, hide_index=True,
            key="ps_scope_ed", disabled=['사용자', '계정'],
            column_config={'직접구매(제외)': st.column_config.CheckboxColumn(
                '직접구매(제외)',
                help='체크하면 이 계정은 영수증 매칭·청구에서 빠집니다')})
        if st.button("💾 정산 대상 저장", key="ps_scope_save", type="primary"):
            _n = 0
            for _old, _new in zip(_rows, _ed.to_dict('records')):
                if bool(_old['직접구매(제외)']) != bool(_new.get('직접구매(제외)')):
                    set_setting(_old['계정'], SELF_PURCHASE_KEY,
                                '1' if _new.get('직접구매(제외)') else '')
                    _n += 1
            st.success(f"✅ {_n}개 계정 설정을 저장했습니다." if _n else "변경된 계정이 없습니다.")
            st.rerun()
        _excl = [r['사용자'] for r in _rows if r['직접구매(제외)']]
        if _excl:
            st.info("🚫 정산·청구 제외 중 — " + " · ".join(_excl))


def _render_costco_map(dmap, USERNAME):
    """코스트코 상품번호 공유맵 — 수집과 충돌 해소.

    코스트코 상품번호는 상품 고유값이라 모든 사용자에게 같아야 한다.
    한 네이버 상품에 두 개의 코스트코번호가 붙었다면 둘 중 하나가 틀린 것이므로,
    자동으로 고르지 않고 근거를 보여주고 관리자가 정한다.
    """
    with st.expander("🔢 코스트코 상품번호 공유맵 — 수집 · 충돌 해소", expanded=False):
        st.caption(
            "코스트코 상품번호는 **상품 고유값**이라 사용자가 달라도 같아야 합니다. "
            "각 사용자 제품DB에 흩어진 매핑을 공유맵에 모으면 **한 사람이 이은 것을 "
            "전원이 씁니다.** 아래에서 수집하고, 값이 갈리는 건만 골라주세요.")

        c1, c2 = st.columns([1.4, 4])
        if c1.button("🔄 매핑 수집", key="cm_collect",
                     help="사용자 제품DB의 확정 매핑을 공유맵으로 모읍니다. "
                          "형식 검증을 통과한 것만 넣고, 값이 갈리는 건은 건드리지 않습니다."):
            _r = collect_shared_naver_map()
            st.session_state['_cm_msg'] = (
                f"✅ 공유맵에 {_r['added']}건 저장 · 충돌 {len(_r['conflicts'])}건은 "
                "아래에서 골라주세요.")
            st.rerun()

        _cf = get_costco_conflicts()
        if not _cf:
            st.success("✅ 값이 갈리는 매핑이 없습니다.")
            return

        st.markdown(f"### ⚠️ 값이 갈리는 매핑 {len(_cf)}건")
        st.caption("같은 네이버 상품에 코스트코번호가 둘 붙었습니다 — **하나는 틀린 값**입니다. "
                   "상품명과 매입가를 보고 맞는 쪽을 고르세요.")

        for i, _c in enumerate(_cf):
            _u = _c['username']
            st.markdown(f"**{dmap.get(_u, _u)}** · 네이버 `{_c['naver_no']}` · "
                        f"{(_c['product_name'] or '')[:44]}")
            _labels, _map = [], {}
            for _o in _c['options']:
                _lab = (f"{_o['costco_no']} · {(_o['name'] or '(공유DB에 없음)')[:30]}"
                        + (f" · {fmt(_o['price'])}원" if _o['price'] else " · 가격없음"))
                _labels.append(_lab)
                _map[_lab] = _o['costco_no']
            _cur = _c.get('chosen') or ''
            _idx = 0
            for _j, _lab in enumerate(_labels):
                if _map[_lab] == _cur:
                    _idx = _j
                    break
            _NONE = "❌ 둘 다 아님 — 번호 비우기"
            _DIRECT = "✏️ 직접 입력"
            _k = f"cm_pick_{i}_{_u}_{_c['naver_no']}"
            _sel = st.radio("맞는 코스트코 상품번호", _labels + [_NONE, _DIRECT],
                            index=_idx, key=_k, horizontal=False,
                            label_visibility="collapsed")
            _typed = ''
            if _sel == _DIRECT:
                _typed = st.text_input("코스트코 상품번호 (4~7자리)", key=f"{_k}_in",
                                       placeholder="예: 667538")
            _cc1, _cc2 = st.columns([1.2, 4])
            if _sel == _NONE:
                # 후보가 둘 다 틀릴 수 있다. 틀린 번호를 남기면 그 단가로 계속
                # 청구되므로, 맞는 게 없으면 비우는 편이 안전하다.
                if _cc1.button("번호 비우기", key=f"{_k}_clear"):
                    clear_costco_mapping(_u, _c['naver_no'])
                    st.session_state['_cm_msg'] = (
                        f"🧹 {dmap.get(_u, _u)} · {_c['naver_no']} 매핑을 비웠습니다 — "
                        "영수증을 등록하면 올바른 번호로 채워집니다.")
                    st.rerun()
                _cc2.caption("공유맵과 제품DB에서 이 상품의 코스트코번호를 지웁니다. "
                             "단가·상품명은 그대로 둡니다.")
            else:
                _pick_no = _typed.strip() if _sel == _DIRECT else _map.get(_sel, '')
                if _cc1.button("이 번호로 확정", key=f"{_k}_ok",
                               disabled=(_sel == _DIRECT and not _pick_no)):
                    if resolve_costco_conflict(_u, _c['naver_no'], _pick_no,
                                               _c.get('product_name', '')):
                        st.session_state['_cm_msg'] = (
                            f"✅ {dmap.get(_u, _u)} · {_c['naver_no']} → {_pick_no} 확정")
                        st.rerun()
                    else:
                        st.error("확정하지 못했습니다 — 코스트코번호는 4~7자리여야 합니다.")
                _cc2.caption("확정하면 공유맵과 그 사용자 제품DB가 함께 이 번호로 맞춰집니다.")
            st.divider()


def _render_price_log(dmap):
    """제품가격 DB에 언제·어디서·얼마로 등록됐는지 확인하는 화면.

    영수증 업로드나 사진등록으로 가격이 저장돼도 그 순간 메시지를 놓치면
    확인할 데가 없었다. 3,895개 공유상품 중에서 찾아야 했다.
    값이 이상할 때 어느 경로로 들어온 건지 봐야 원인을 잡을 수 있다.
    """
    with st.expander("💰 최근 가격 등록·갱신 확인", expanded=False):
        c1, c2, c3 = st.columns([1, 1.4, 2])
        _days = c1.selectbox("기간", [1, 3, 7, 30, 90], index=2,
                             format_func=lambda d: f"최근 {d}일", key="ps_plog_days")
        _src_opts = ['(전체)'] + list(PRICE_SOURCES.keys())
        _src = c2.selectbox("출처", _src_opts,
                            format_func=lambda k: PRICE_SOURCES.get(k, k), key="ps_plog_src")
        _kw = c3.text_input("상품명·상품번호 검색", key="ps_plog_kw", placeholder="이디야")

        _rows = get_price_log(limit=300, days=int(_days),
                              source=(None if _src == '(전체)' else _src),
                              keyword=_kw)
        if not _rows:
            st.caption("해당 조건의 가격 변경 이력이 없습니다. "
                       "영수증을 업로드하거나 사진으로 상품을 등록하면 여기에 쌓입니다.")
            return
        _disp = [{
            '시각': (r.get('created_at') or '')[:16],
            '상품번호': r.get('product_no') or '',
            '상품명': (r.get('costco_name') or '')[:34],
            '이전': fmt(int(r.get('prev_price') or 0)) if r.get('prev_price') else '-',
            '가격': fmt(int(r.get('price') or 0)),
            '구분': r.get('price_type') or '',
            '출처': PRICE_SOURCES.get(r.get('source') or '', r.get('source') or '-'),
            '등록자': dmap.get(r.get('updated_by') or '', r.get('updated_by') or ''),
            '영수증일': r.get('receipt_date') or '',
        } for r in _rows]
        st.dataframe(pd.DataFrame(_disp), use_container_width=True, hide_index=True,
                     height=min(420, 40 + 28 * len(_disp)))
        _by_src = {}
        for r in _rows:
            _k = PRICE_SOURCES.get(r.get('source') or '', r.get('source') or '기타')
            _by_src[_k] = _by_src.get(_k, 0) + 1
        st.caption(f"최근 {_days}일 {len(_rows)}건 — "
                   + " · ".join(f"{k} {v}건" for k, v in
                                sorted(_by_src.items(), key=lambda kv: -kv[1])))


def _render_split_rules(USERNAME):
    """소분 판매 상품을 상품명 키워드로 고정한다.

    소분 여부는 코스트코 규격이 아니라 '내가 어떻게 파느냐'라, 상품명의 'x N'만
    보고 자동 판정할 수 없다(신라면 30개들이 박스 vs 그릭요거트 2개입).
    소분 품목은 많지 않으므로 여기에 키워드로 명시해 고정한다.
    한 번 넣으면 이후 모든 정산·수익계산이 이 값을 쓴다.
    """
    _rules = get_split_rules(force=True)
    with st.expander(f"🔪 소분 판매 상품 {len(_rules)}건 — 상품명으로 고정", expanded=False):
        st.caption(
            "코스트코 1팩을 몇 개로 나눠 파는지를 **상품명 키워드**로 지정합니다. "
            "예: 키워드 `커클랜드 그릭요거트 907g` · 소분수 `2` → 그 이름이 들어간 주문은 "
            "매입가가 **팩값 ÷ 2**로 잡힙니다. "
            "키워드가 여러 개 걸리면 **가장 긴(구체적인) 것**이 이깁니다. "
            "띄어쓰기·대소문자는 무시합니다.")

        _rows = [{'삭제': False, '상품명 키워드': r['keyword'], '소분수': int(r['split_qty']),
                  '메모': r.get('memo') or '', '수정': f"{r.get('updated_by') or ''} {r.get('updated_at') or ''}".strip()}
                 for r in _rules]
        if _rows:
            _ed = st.data_editor(
                pd.DataFrame(_rows), use_container_width=True, hide_index=True,
                key="ps_split_editor", disabled=['상품명 키워드', '수정'],
                column_config={
                    '삭제': st.column_config.CheckboxColumn('삭제'),
                    '소분수': st.column_config.NumberColumn('소분수', min_value=1, max_value=50, step=1),
                })
            c1, c2 = st.columns([1, 4])
            if c1.button("💾 변경 저장", key="ps_split_save"):
                _n = 0
                for r in _ed.to_dict('records'):
                    _kw = str(r.get('상품명 키워드') or '')
                    if r.get('삭제'):
                        delete_split_rule(_kw); _n += 1
                    else:
                        _old = next((x for x in _rules if x['keyword'] == _kw), None)
                        if _old and (int(_old['split_qty']) != int(r.get('소분수') or 1)
                                     or (_old.get('memo') or '') != str(r.get('메모') or '')):
                            upsert_split_rule(_kw, int(r.get('소분수') or 1),
                                              str(r.get('메모') or ''), USERNAME)
                            _n += 1
                st.success(f"✅ {_n}건 반영했습니다.") if _n else st.info("변경된 내용이 없습니다.")
                if _n:
                    st.rerun()
        else:
            st.caption("등록된 소분 규칙이 없습니다. 아래에서 추가하세요.")

        st.markdown("**➕ 소분 규칙 추가**")
        a1, a2, a3, a4 = st.columns([3, 1, 2.4, 1])
        _kw = a1.text_input("상품명 키워드", key="ps_split_kw",
                            placeholder="커클랜드 그릭요거트 907g")
        _sq = a2.number_input("소분수", min_value=2, max_value=50, step=1, value=2,
                              key="ps_split_sq")
        _mm = a3.text_input("메모", key="ps_split_memo", placeholder="907g 2개입을 낱개로 판매")
        with a4:
            st.write("")
            if st.button("추가", key="ps_split_add", use_container_width=True,
                         disabled=not str(_kw).strip()):
                upsert_split_rule(str(_kw).strip(), int(_sq), str(_mm or ''), USERNAME)
                st.success(f"✅ '{str(_kw).strip()}' → 소분 {int(_sq)} 저장")
                st.rerun()


def _render_link_panel(per_user, dmap, ds, USERNAME):
    """구매가가 0원인 항목을 공유DB 상품에 연결하고 소분수를 지정한다.

    0원의 원인은 값이 없어서가 아니라 **주문(네이버번호) ↔ 공유DB(코스트코번호)**를
    잇는 매핑이 사용자 제품DB에 없어서다. 값은 공유DB에 이미 있다
    (이디야 커피믹스 41,990원 등). 여기서 한 번 연결하면 이후로는 자동으로 붙는다.
    소분수도 같이 받는다 — 공유DB 3,891개가 전부 split_qty=1이라
    907g 2개들이를 낱개로 팔아도 팩 값을 통째로 청구하고 있었다.
    """
    _miss = []
    for u, v in per_user.items():
        for it in v['items']:
            if int(it.get('unit_price') or 0) > 0:
                continue
            _miss.append({'username': u, 'item': it})
    if not _miss:
        return

    with st.expander(f"🔗 구매가 없는 항목 연결 {len(_miss)}건 — 코스트코 상품·소분수 지정",
                     expanded=False):
        st.caption(
            "값이 없는 게 아니라 **주문과 공유DB를 잇는 코스트코 상품번호가 없어서** 0원입니다. "
            "아래에서 한 번 연결하면 다음부터 자동으로 붙습니다. "
            "**소분**은 코스트코 묶음을 몇 개로 나눠 파는지입니다 "
            "(907g 2개들이를 낱개로 팔면 2 → 단가가 절반으로 잡힙니다).")

        _sp = get_shared_products() or []
        _key = f"_ps_sugg_{ds}_{len(_miss)}"
        if _key not in st.session_state:
            with st.spinner("공유DB에서 후보 찾는 중..."):
                _sg = {}
                for m in _miss:
                    _nm = str(m['item'].get('product_name') or '')
                    if _nm not in _sg:
                        _sg[_nm] = suggest_shared_matches(_nm, shared_prods=_sp, top=1)
                st.session_state[_key] = _sg
        _sugg = st.session_state[_key]

        _rows = []
        for m in _miss:
            it = m['item']
            _nm = str(it.get('product_name') or '')
            _c = (_sugg.get(_nm) or [{}])[0]
            _rows.append({
                '연결': False,
                '사용자': dmap.get(m['username'], m['username']),
                '상품명': _nm[:42],
                '네이버번호': str(it.get('product_no') or ''),
                '추천': (f"{_c.get('costco_name','')[:26]} ({_c.get('unit_price',0):,}원 "
                        f"· 유사도 {_c.get('score',0)})" if _c.get('product_no') else '후보 없음'),
                '코스트코번호': str(_c.get('product_no') or ''),
                '소분수': int(_c.get('split_qty') or 1),
                '_u': m['username'],
                '_full': _nm,
            })
        _ed = st.data_editor(
            pd.DataFrame(_rows), use_container_width=True, hide_index=True,
            key=f"ps_link_editor_{ds}_{len(_miss)}",
            column_order=['연결', '사용자', '상품명', '네이버번호', '추천',
                          '코스트코번호', '소분수'],
            disabled=['사용자', '상품명', '네이버번호', '추천'],
            column_config={
                '연결': st.column_config.CheckboxColumn('연결', help='체크한 행만 저장합니다'),
                '코스트코번호': st.column_config.TextColumn(
                    '코스트코번호', help='추천이 틀리면 직접 고쳐 넣으세요'),
                '소분수': st.column_config.NumberColumn(
                    '소분수', min_value=1, max_value=50, step=1,
                    help='코스트코 1팩을 몇 개로 나눠 파는지. 안 나누면 1'),
            })

        _recs = _ed.to_dict('records')
        _pick = [r for r in _recs
                 if r.get('연결') and str(r.get('코스트코번호') or '').strip()]
        _bad = [r for r in _recs
                if r.get('연결') and not str(r.get('코스트코번호') or '').strip()]
        if _bad:
            st.warning(f"⚠️ {len(_bad)}행은 코스트코번호가 비어 저장되지 않습니다.")
        if _pick:
            st.caption("저장될 연결 — " + " · ".join(
                f"{r.get('상품명')} → {r.get('코스트코번호')}"
                + (f" (소분 {r.get('소분수')})" if int(r.get('소분수') or 1) > 1 else "")
                for r in _pick[:8]) + (" …" if len(_pick) > 8 else ""))
        if st.button(f"🔗 선택한 {len(_pick)}건 연결 저장", key="ps_link_apply",
                     type="primary", disabled=not _pick):
            _ok, _ins, _fail = 0, 0, 0
            for i, r in enumerate(_recs):
                if not (r.get('연결') and str(r.get('코스트코번호') or '').strip()):
                    continue
                _src = _rows[i]
                _res = link_product_mapping(
                    _src['_u'], r.get('네이버번호'), _src['_full'],
                    str(r.get('코스트코번호')).strip(), int(r.get('소분수') or 1))
                if _res == 'inserted':
                    _ins += 1
                elif _res == 'updated':
                    _ok += 1
                else:
                    _fail += 1
            st.session_state.pop(_key, None)
            _msg = f"✅ 연결 저장 — 갱신 {_ok}건 · 신규 {_ins}건"
            if _fail:
                _msg += f" · 실패 {_fail}건"
            st.session_state['_ps_link_msg'] = _msg
            st.rerun()


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    """구매가 도구 — 청구액을 만드는 화면이 아니다.

    예전 이름은 '구매내역 정산'이었고 여기서 예상→확정 스냅샷을 따로 저장했다.
    같은 청구액을 영수증 정산과 이 화면이 각자 계산해 값이 어긋났다
    (9/7 oxo: 원장 780,720 vs 스냅샷 184,560). 청구는 정산 원장 하나로 모았고,
    이 화면에는 그 원장을 정확하게 만들기 위한 **구매가 도구**만 남긴다:
      · 청구 범위 (직접구매 계정 제외)
      · 코스트코 번호 매핑 정리 (중복·충돌)
      · 구매가 변경 이력
      · 소분 규칙
      · 상품 ↔ 코스트코번호 연결
    """
    if not IS_ADMIN:
        st.error("관리자 전용 기능입니다.")
        return
    st.header("🔗 구매가 · 매핑 관리")
    st.caption("영수증 정산이 상품을 제대로 찾고 올바른 단가를 붙이도록 **구매가와 번호 매핑**을 "
               "손보는 곳입니다. 청구액·입금은 관리자 › **정산·청구**에서 봅니다.")

    for _k in ('_du_msg', '_bl_msg', '_rm_msg', '_cm_msg'):
        _m = st.session_state.pop(_k, None)
        if _m:
            st.success(_m)
    _lm = st.session_state.pop('_ps_link_msg', None)
    if _lm:
        st.success(_lm + " — 다음 정산부터 이 단가로 붙습니다.")

    dmap = _disp_map()
    _c1, _c2 = st.columns(2)
    _d_to = _c2.date_input("종료일 (발송일 기준)", value=date.today(), key="ps_to")
    _d_from = _c1.date_input("시작일 (발송일 기준)", value=_d_to, key="ps_from")
    if _d_from > _d_to:
        _d_from, _d_to = _d_to, _d_from
    _days = [str(_d_from + timedelta(days=i)) for i in range((_d_to - _d_from).days + 1)]
    if len(_days) > 62:
        st.error("기간이 너무 깁니다 — 62일 이내로 잡아 주세요.")
        return
    ds = str(_d_to)

    # 구매가가 안 붙은 주문을 찾기 위한 집계 — 연결 패널이 이걸 먹는다.
    per_user = {}
    with st.spinner(f"{len(_days)}일 집계 중..."):
        for u in _sellers():
            _items, _total = [], 0
            for _dd0 in _days:
                _it0, _tt0 = compute_daily_purchase(u, _dd0, basis='dispatch')
                for _x in _it0:
                    _x = dict(_x)
                    _x['settle_date'] = _dd0
                    _items.append(_x)
                _total += int(_tt0 or 0)
            if _items:
                per_user[u] = {'items': _items, 'total': _total,
                               'matched': sum(1 for it in _items if it['amount'] > 0)}

    if per_user:
        _miss = sum(len(v['items']) - v['matched'] for v in per_user.values())
        st.dataframe(pd.DataFrame([{
            '사용자': dmap.get(u, u),
            '발송건수': len(v['items']),
            '구매가 있음': v['matched'],
            '구매가 없음': len(v['items']) - v['matched'],
            '구매금액(참고)': v['total'],
        } for u, v in sorted(per_user.items(), key=lambda kv: -kv[1]['total'])]),
            use_container_width=True, hide_index=True,
            column_config={'구매금액(참고)': st.column_config.NumberColumn(
                '구매금액(참고)', format='%d')})
        st.caption("**구매금액(참고)** 은 현재 제품·공유DB 구매가로 매긴 값입니다 — "
                   "실제 청구액이 아닙니다. 청구액은 영수증 실단가로 정산한 결과입니다."
                   + (f"  ·  ⚠️ **구매가 없음 {_miss}건** — 아래 연결 패널에서 "
                      "코스트코번호를 이어 주세요." if _miss else ""))
    else:
        st.info(f"{_d_from} ~ {_d_to} 발송처리된 주문이 없습니다.")

    _render_billing_scope(dmap, USERNAME)
    _render_costco_map(dmap, USERNAME)
    _render_price_log(dmap)
    _render_split_rules(USERNAME)
    if per_user:
        _render_link_panel(per_user, dmap, ds, USERNAME)
