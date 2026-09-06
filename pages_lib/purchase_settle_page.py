"""🧾 구매내역 정산 (관리자) — 사용자별 일별 구매금액(구매가) 집계 · 예상→확정 · 변경 고지.

수익계산과 별개 모듈. 예상(제품/공유DB 구매가) 저장 → 코스트코 영수증 반영(실단가) 후
'확정'하면 예상 대비 상품별 변경금액을 산출하고, 사용자 화면에 배지로 고지된다.
매일=구매가만 / 월말=택배·포장비 추가(예정).
"""
from datetime import date

import streamlit as st
import pandas as pd

from db import (get_all_users, get_shared_products,
                get_split_rules, upsert_split_rule, delete_split_rule,
                get_price_log, PRICE_SOURCES)
from db_purchase_settle import (
    compute_daily_purchase, save_estimate, finalize, get_snapshot, diff_against_snapshot,
    month_fees_if_last_day, is_last_day_of_month,
    suggest_shared_matches, link_product_mapping,
)
from utils import fmt


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def _sellers():
    return [u['username'] for u in get_all_users() if not u.get('is_admin')]


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
    if not IS_ADMIN:
        st.error("관리자 전용 기능입니다.")
        return
    st.header("🧾 구매내역 정산")
    _lm = st.session_state.pop('_ps_link_msg', None)
    if _lm:
        st.success(_lm + " — 아래 표에 구매가가 반영됐는지 확인하세요.")
    st.caption("각 사용자에게 청구할 **구매금액(구매가)**을 집계합니다. "
               "예상(제품·공유DB 구매가) 저장 → 코스트코 영수증 업로드로 실단가 반영 후 "
               "**확정**하면 예상 대비 변경금액이 사용자 화면에 배지로 표시됩니다.")

    dmap = _disp_map()
    # 업무 흐름이 '송장 등록으로 발송처리 → 다음날 영수증 등록 → 매칭 → 정산'이라
    # 청구 대상은 그날 실제로 내보낸 물건(발송처리분)이어야 한다.
    # 주문일 기준은 아직 안 나간 주문까지 청구해 버린다.
    _pb = st.radio("정산 기준", ["🚚 발송일 기준 (권장)", "📅 주문일 기준 (구버전)"],
                   key="ps_basis", horizontal=True,
                   help="발송일 기준: 그날 송장을 등록해 발송처리한 주문을 청구합니다. "
                        "영수증은 다음날 등록해도 됩니다 — 실단가는 공유DB 매입가로 "
                        "반영되므로 등록 시점과 무관합니다.")
    _basis = 'dispatch' if _pb.startswith("🚚") else 'order'
    d = st.date_input("정산 날짜 (%s 기준)" % ('발송일' if _basis == 'dispatch' else '주문일'),
                      value=date.today())
    ds = str(d)

    _is_last = is_last_day_of_month(ds)
    if _is_last:
        st.info(f"📦 **말일 정산** — 이 날짜 청구액에는 그달(1일~말일) **택배·포장 누적**이 포함됩니다 "
                "(실배정 포장비 + 발송건수×택배비).")

    per_user = {}
    _no_dispatch = []
    for u in _sellers():
        items, total = compute_daily_purchase(u, ds, basis=_basis)
        if not items:
            # 발송 이력이 없는데 주문은 있는 계정 — 발송처리를 안 했거나 외부에서
            # 내보낸 경우다. 조용히 빠지면 청구가 통째로 누락되므로 알려준다.
            if _basis == 'dispatch':
                _oi, _ot = compute_daily_purchase(u, ds, basis='order')
                if _oi:
                    _no_dispatch.append((u, len(_oi), _ot))
            continue
        snap = get_snapshot(ds, u)
        dd = diff_against_snapshot(ds, u, basis=_basis) if snap else None
        fees = month_fees_if_last_day(u, ds) if _is_last else None
        per_user[u] = {'items': items, 'total': total, 'snap': snap, 'diff': dd,
                       'matched': sum(1 for it in items if it['amount'] > 0),
                       'fees': fees, 'charge': total + (fees['fees_total'] if fees else 0)}

    if _no_dispatch:
        st.warning(
            "⚠️ 이 날짜에 **발송 이력이 없어 청구에서 빠진** 사용자가 있습니다 — "
            + " · ".join(f"{dmap.get(u, u)} (주문 {n}건 / {fmt(t)}원)"
                         for u, n, t in _no_dispatch)
            + "  ·  송장 등록으로 발송처리했는지 확인하거나, 위에서 '주문일 기준'을 선택하세요.")

    if not per_user:
        st.info(f"{ds} {'발송처리된 주문' if _basis == 'dispatch' else '주문'}이 없습니다.")
        return

    # 요약 표
    rows = []
    for u, v in sorted(per_user.items(), key=lambda kv: -kv[1]['total']):
        snap = v['snap']
        status = {'est': '예상', 'final': '확정'}.get(snap['status'], '-') if snap else '-'
        chg = ''
        if snap and v['diff'] and v['diff']['total_diff'] != 0:
            chg = f"{v['diff']['total_diff']:+,}원"
        elif snap:
            chg = '동일'
        row = {
            '사용자': dmap.get(u, u),
            ('발송건수' if _basis == 'dispatch' else '주문수'): len(v['items']),
            '구매가 있음': v['matched'],
            '구매금액': fmt(v['total']),
        }
        if _is_last:
            row['그달 택배·포장'] = fmt(v['fees']['fees_total']) if v['fees'] else '-'
            row['청구액(구매+월비용)'] = fmt(v['charge'])
        row['상태'] = status
        row['변경(예상대비)'] = chg
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    _tot = sum(v['total'] for v in per_user.values())
    _charge = sum(v['charge'] for v in per_user.values())
    if _is_last:
        st.markdown(f"### 총 청구액: **{fmt(_charge)}원** "
                    f"(구매 {fmt(_tot)} + 월 택배·포장 {fmt(_charge - _tot)})  ·  사용자 {len(per_user)}명")
    else:
        st.markdown(f"### 총 구매금액: **{fmt(_tot)}원**  ·  사용자 {len(per_user)}명")

    _render_price_log(dmap)
    _render_split_rules(USERNAME)
    _render_link_panel(per_user, dmap, ds, USERNAME)

    c1, c2, _ = st.columns([1.4, 1.6, 3])
    if c1.button("💾 예상 저장 (기준선)", type="primary", key="ps_save_est",
                 help="현재 구매가로 예상 청구 기준선 저장. 영수증 반영 전에 눌러 baseline 확보."):
        for u, v in per_user.items():
            save_estimate(ds, u, v['total'], v['items'], created_by=USERNAME)
        st.success(f"✅ 예상 저장 완료 ({len(per_user)}명) — 영수증 반영 후 '확정'하면 변경 산출")
        st.rerun()
    if c2.button("✅ 확정 (영수증 반영 후)", key="ps_finalize",
                 help="코스트코 영수증 업로드로 실단가가 반영된 뒤 클릭 → 예상 대비 변경 산출 + 확정."):
        n_changed = 0
        for u, v in per_user.items():
            dd = diff_against_snapshot(ds, u, basis=_basis)
            finalize(ds, u, v['total'], dd['changed'], created_by=USERNAME)
            if dd['changed']:
                n_changed += 1
        st.success(f"✅ 확정 완료 — 변경 발생 사용자 {n_changed}명. 사용자 화면에 배지 표시됩니다.")
        st.rerun()

    st.divider()
    # 사용자별 상세 + 변경 내역
    for u, v in sorted(per_user.items(), key=lambda kv: -kv[1]['total']):
        badge = ''
        if v['diff'] and v['diff']['total_diff'] != 0:
            _s = v['diff']['total_diff']
            badge = f"  ·  {'🔺' if _s > 0 else '🔻'}변경 {_s:+,}원"
        _head_amt = fmt(v['charge']) if _is_last else fmt(v['total'])
        with st.expander(f"🧾 {dmap.get(u, u)} — {_head_amt}원 ({len(v['items'])}건){badge}"):
            if _is_last and v['fees']:
                f = v['fees']
                st.caption(f"📦 말일: 구매가 {fmt(v['total'])} + 택배 {f['ship_count']}건×{fmt(f['ship_fee'])}"
                           f"={fmt(f['ship_total'])} + 포장 실배정 {fmt(f['pkg_total'])} = 청구 {fmt(v['charge'])}")
            _df = pd.DataFrame([{
                '수취인': it['recipient'], '상품명': it['product_name'], '수량': it['qty'],
                '구매단가': fmt(it['unit_price']), '구매금액': fmt(it['amount']),
            } for it in v['items']])
            st.dataframe(_df, use_container_width=True, hide_index=True)
            if v['diff'] and v['diff']['changed']:
                st.markdown("**🔺 변경 상품 (예상 → 확정)**")
                st.dataframe(pd.DataFrame([{
                    '상품': c['product_name'], '예상': fmt(c['prev']),
                    '확정': fmt(c['now']), '차액': f"{c['diff']:+,}",
                } for c in v['diff']['changed']]), use_container_width=True, hide_index=True)
