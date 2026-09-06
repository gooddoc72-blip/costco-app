"""🧾 구매내역 정산 (관리자) — 사용자별 일별 구매금액(구매가) 집계 · 예상→확정 · 변경 고지.

수익계산과 별개 모듈. 예상(제품/공유DB 구매가) 저장 → 코스트코 영수증 반영(실단가) 후
'확정'하면 예상 대비 상품별 변경금액을 산출하고, 사용자 화면에 배지로 고지된다.
매일=구매가만 / 월말=택배·포장비 추가(예정).
"""
from datetime import date, timedelta

import streamlit as st
import pandas as pd

from db import (get_all_users, get_shared_products,
                get_split_rules, upsert_split_rule, delete_split_rule,
                get_price_log, PRICE_SOURCES)
from db_purchase_settle import (
    compute_daily_purchase, save_estimate, finalize, get_snapshot, diff_against_snapshot,
    month_fees_if_last_day, is_last_day_of_month,
    suggest_shared_matches, link_product_mapping,
    get_daily_summary, get_monthly_summary, get_period_rows,
    get_order_dispatch_counts, get_dispatch_list, compute_month_fees,
)
from utils import fmt


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def _sellers():
    return [u['username'] for u in get_all_users() if not u.get('is_admin')]


def _render_receipt_match(dmap, USERNAME, date_from, date_to):
    """발송 상품 <-> 영수증 품목 매칭.

    상품주문번호는 '어느 판매자의 주문인가'만 가른다. 청구금액은 그 발송 상품이
    영수증의 어느 품목인지 물려야 나온다. 자동 매칭이 못 붙인 건을 여기서
    사람이 직접 잇는다. 한 번 이으면 제품DB에 코스트코번호가 남아 다음부터 자동이다.
    """
    import db_billing_ledger as bl

    _np = bl.unpriced_rows(str(date_from), str(date_to))
    _pd_rows = [r for r in bl.get_ledger(str(date_from), str(date_to), status='pending')
                if int(r.get('amount') or 0) > 0]
    _targets = _np + _pd_rows
    if not _targets:
        st.success("✅ 이 기간 발송건은 모두 영수증 단가로 확정됐습니다.")
        return

    with st.expander(f"🔗 발송 상품 ↔ 영수증 품목 매칭 {len(_targets)}건 "
                     f"(가격없음 {len(_np)} · 예상가 {len(_pd_rows)})", expanded=False):
        st.caption(
            "상품주문번호는 **어느 판매자의 주문인지**만 가릅니다. "
            "청구금액은 그 발송 상품이 **영수증의 어느 품목인지** 물려야 나옵니다. "
            "아래에서 이으면 영수증 실단가로 확정되고, 제품DB에도 코스트코번호가 남아 "
            "다음부터는 자동으로 붙습니다.")

        _cands = bl.receipt_items_in_range(
            str(pd.Timestamp(date_from) - pd.Timedelta(days=14))[:10], str(date_to))
        if not _cands:
            st.warning("이 기간(발송일 기준 2주 전까지) 영수증 품목이 없습니다 — "
                       "영수증 정산에서 영수증을 먼저 올려주세요.")
            return
        st.caption(f"영수증 품목 후보 {len(_cands)}종")

        _key = f"_rm_sugg_{date_from}_{date_to}_{len(_targets)}"
        if _key not in st.session_state:
            with st.spinner("영수증 품목에서 후보 찾는 중..."):
                _sg = {}
                for _t in _targets:
                    _n = str(_t.get('product_name') or '')
                    if _n not in _sg:
                        _sg[_n] = bl.suggest_receipt_for(_n, _cands, top=1)
                st.session_state[_key] = _sg
        _sugg = st.session_state[_key]

        _bylabel = {f"{c['costco_no']} · {c['name'][:28]} · {fmt(c['unit_price'])}원": c
                    for c in _cands}
        _labels = ['(선택 안 함)'] + list(_bylabel.keys())

        _rows = []
        for _t in _targets:
            _n = str(_t.get('product_name') or '')
            _c = (_sugg.get(_n) or [{}])[0]
            _lab = '(선택 안 함)'
            if _c.get('costco_no'):
                _cand_lab = f"{_c['costco_no']} · {_c['name'][:28]} · {fmt(_c['unit_price'])}원"
                if _cand_lab in _bylabel:
                    _lab = _cand_lab
            _rows.append({
                '확정': False,
                '사용자': dmap.get(_t['username'], _t['username']),
                '발송일': _t['dispatched_at'],
                '발송 상품명': _n[:38],
                '수량': int(_t.get('qty') or 1),
                '소분': int(_t.get('split_qty') or 1),
                '현재 청구액': int(_t.get('amount') or 0),
                '영수증 품목': _lab,
                '유사도': _c.get('score', 0),
                '_id': int(_t['id']),
                '_u': _t['username'],
                '_name': _n,
            })
        _ed = st.data_editor(
            pd.DataFrame(_rows), use_container_width=True, hide_index=True,
            key=f"rm_editor_{date_from}_{date_to}_{len(_targets)}",
            column_order=['확정', '사용자', '발송일', '발송 상품명', '수량', '소분',
                          '현재 청구액', '영수증 품목', '유사도'],
            disabled=['사용자', '발송일', '발송 상품명', '수량', '소분',
                      '현재 청구액', '유사도'],
            column_config={
                '확정': st.column_config.CheckboxColumn('확정', help='체크한 행만 반영됩니다'),
                '영수증 품목': st.column_config.SelectboxColumn('영수증 품목', options=_labels),
                '현재 청구액': st.column_config.NumberColumn('현재 청구액', format='%d'),
            })

        _recs = _ed.to_dict('records')
        _pick = [(i, r) for i, r in enumerate(_recs)
                 if r.get('확정') and str(r.get('영수증 품목')) in _bylabel]
        _bad = [r for r in _recs
                if r.get('확정') and str(r.get('영수증 품목')) not in _bylabel]
        if _bad:
            st.warning(f"⚠️ {len(_bad)}행은 영수증 품목을 고르지 않아 반영되지 않습니다.")
        if _pick:
            _prev = []
            for _i, _r in _pick[:8]:
                _c = _bylabel[str(_r['영수증 품목'])]
                _amt = (_c['unit_price'] // max(1, int(_r['소분']))) * int(_r['수량'])
                _prev.append(f"{_r['발송 상품명'][:18]} → {fmt(_amt)}원")
            st.caption("확정될 금액 — " + " · ".join(_prev)
                       + (" …" if len(_pick) > 8 else ""))
        _link = st.checkbox("제품DB에 코스트코번호도 함께 저장 (다음부터 자동 매칭)",
                            value=True, key="rm_link")
        if st.button(f"✅ 선택한 {len(_pick)}건 영수증 단가로 확정", key="rm_apply",
                     type="primary", disabled=not _pick):
            _n_ok = _n_link = 0
            for _i, _r in _pick:
                _src = _rows[_i]
                _c = _bylabel[str(_r['영수증 품목'])]
                if bl.confirm_with_receipt(_src['_id'], _c['costco_no'],
                                           _c['unit_price'], _c['receipt_date']):
                    _n_ok += 1
                    if _link:
                        try:
                            link_product_mapping(_src['_u'], '', _src['_name'],
                                                 _c['costco_no'], int(_src['소분']))
                            _n_link += 1
                        except Exception:
                            pass
            st.session_state.pop(_key, None)
            st.session_state['_rm_msg'] = (
                f"✅ {_n_ok}건을 영수증 단가로 확정했습니다"
                + (f" · 제품DB 연결 {_n_link}건" if _n_link else ""))
            st.rerun()


def _render_ledger(dmap, USERNAME):
    """청구 원장 — 발송 1건 = 청구 근거 1행.

    총액 한 줄만 있으면 반품 한 건을 빼낼 수도, 이미 발행한 계산서를 설명할 수도
    없다. 건별 근거를 쌓아 계산서 발행의 바탕으로 쓴다.
    """
    import db_billing_ledger as bl

    st.divider()
    st.subheader("📒 청구 원장 — 발송건별 청구 근거")
    c1, c2, c3 = st.columns([1, 1, 1.4])
    _to = c2.date_input("종료일", value=date.today(), key="bl_to")
    _from = c1.date_input("시작일", value=_to - timedelta(days=6), key="bl_from")
    _stf = c3.selectbox("상태", ['(전체)', 'pending', 'confirmed', 'invoiced', 'canceled'],
                        format_func=lambda k: {'pending': '예상(미확정)', 'confirmed': '확정',
                                               'invoiced': '계산서 발행됨',
                                               'canceled': '취소'}.get(k, k),
                        key="bl_status")

    _b1, _b2 = st.columns([1.6, 4])
    if _b1.button("🔄 발송건에서 원장 만들기", key="bl_sync", type="primary",
                  help="기간 내 발송 기록을 훑어 원장 행을 만들고, 영수증에서 확정된 건은 "
                       "실단가로 채웁니다. 계산서가 발행된 행은 건드리지 않습니다."):
        _c = _u = _s = 0
        _d = _from
        while _d <= _to:
            for _un in _sellers():
                _r = bl.sync_from_dispatch(_un, str(_d))
                _c += _r['created']; _u += _r['updated']; _s += _r['skipped']
            _d += timedelta(days=1)
        st.session_state['_bl_msg'] = (
            f"✅ 원장 갱신 — 신규 {_c}건 · 갱신 {_u}건"
            + (f" · 발행돼 건너뜀 {_s}건" if _s else ""))
        st.rerun()

    _rows = bl.get_ledger(str(_from), str(_to),
                          status=(None if _stf == '(전체)' else _stf))
    if not _rows:
        st.info("원장이 비어 있습니다 — 위 '발송건에서 원장 만들기'를 눌러 채우세요.")
        return

    _sum = bl.summarize(str(_from), str(_to))
    st.dataframe(pd.DataFrame([{
        '사용자': dmap.get(u, u), '청구건수': v['count'], '청구액': fmt(v['amount']),
        '가격 미확인': v.get('no_price', 0),
        '예상(미확정)': v['pending'], '확정': v['confirmed'],
        '발행': v['invoiced'], '취소': v['canceled'],
    } for u, v in sorted(_sum.items(), key=lambda kv: -kv[1]['amount'])]),
        use_container_width=True, hide_index=True)
    _tot = sum(v['amount'] for v in _sum.values())
    _pend = sum(v['pending'] for v in _sum.values())
    st.markdown(f"### 기간 청구액: **{fmt(_tot)}원**  ·  {sum(v['count'] for v in _sum.values())}건")
    if _pend:
        st.caption(f"⚠️ 아직 예상가인 행이 {_pend}건입니다 — 영수증 정산을 하면 "
                   "실단가로 확정되고, 다시 '원장 만들기'를 누르면 반영됩니다.")
    _np = bl.unpriced_rows(str(_from), str(_to))
    if _np:
        with st.expander(f"❗ 매입가가 아직 없는 행 {len(_np)}건 — 이대로 계산서를 끊으면 "
                         "그만큼 덜 청구됩니다", expanded=False):
            st.caption("아직 구매하지 않아 매입가를 모르는 건입니다(정상). "
                       "영수증이 올라오면 채워집니다. "
                       "제품가격 DB에 코스트코번호 연결이 없어 0원인 것도 여기 섞이므로, "
                       "발행 전에 한 번 훑어보세요.")
            st.dataframe(pd.DataFrame([{
                '발송일': r['dispatched_at'],
                '사용자': dmap.get(r['username'], r['username']),
                '주문번호': r['order_no'],
                '상품명': (r['product_name'] or '')[:40],
                '수량': r['qty'],
            } for r in _np[:200]]), use_container_width=True, hide_index=True)

    _render_receipt_match(dmap, USERNAME, _from, _to)

    with st.expander(f"📄 원장 상세 {len(_rows)}건", expanded=False):
        st.dataframe(pd.DataFrame([{
            '발송일': r['dispatched_at'],
            '사용자': dmap.get(r['username'], r['username']),
            '주문번호': r['order_no'],
            '상품명': (r['product_name'] or '')[:32],
            '수량': r['qty'], '소분': r['split_qty'],
            '단가': fmt(int(r['unit_cost'] or 0)),
            '청구액': fmt(int(r['amount'] or 0)),
            '근거': bl.COST_SOURCES.get(r['cost_source'], r['cost_source']),
            '상태': {'pending': '예상', 'confirmed': '확정',
                     'invoiced': '발행', 'canceled': '취소'}.get(r['status'], r['status']),
        } for r in _rows[:500]]), use_container_width=True, hide_index=True)


def _render_dispatch_upload(dmap, USERNAME):
    """발송 파일 업로드 — 주문번호로 사용자를 갈라 dispatch_log에 기록.

    청구는 발송 기준인데 송장 등록을 안 하는 계정이 있으면 청구가 0원이 된다
    (clglobal0919는 8/31~9/6 주문 205건에 발송 0건이었다).
    관리자가 전체 발송 파일 하나를 올려 분류하면 그 의존이 사라진다.
    """
    import dispatch_upload as du

    with st.expander("🚚 발송 파일 업로드 — 주문번호로 사용자 분류", expanded=False):
        st.caption(
            "전체 발송내역 파일(엑셀·CSV)을 올리면 **주문번호**로 각 사용자에게 나눠 "
            "발송 기록에 넣습니다. 이후 영수증 정산이 그 발송건에 매입가를 채웁니다. "
            "같은 파일을 두 번 올려도 중복 저장되지 않습니다.")
        _f = st.file_uploader("발송 파일 (xlsx · xls · csv)", type=['xlsx', 'xls', 'csv'],
                              key="du_file")
        if not _f:
            return
        try:
            if _f.name.lower().endswith('.csv'):
                _df = pd.read_csv(_f, dtype=str)
            else:
                _df = pd.read_excel(_f, dtype=str)
        except Exception as _e:
            st.error(f"파일을 읽지 못했습니다: {_e}")
            return
        if _df is None or _df.empty:
            st.warning("빈 파일입니다.")
            return
        _df = _df.fillna('')
        st.caption(f"📄 {_f.name} — {len(_df)}행 · 열 {len(_df.columns)}개")

        _cols = list(_df.columns)
        _guess = du.guess_columns(_cols)
        st.markdown("**열 매핑** — 자동으로 찾은 값이 맞는지 확인하세요")
        _m1, _m2, _m3 = st.columns(3)
        _opts = ['(없음)'] + [str(c) for c in _cols]

        def _pick(col, label, key, need=False):
            _d = _guess.get(key)
            _i = _opts.index(str(_d)) if _d and str(_d) in _opts else 0
            _v = col.selectbox(label + (" *" if need else ""), _opts, index=_i,
                               key=f"du_col_{key}")
            return None if _v == '(없음)' else _v

        _cm = {
            'order_no':     _pick(_m1, "주문번호", 'order_no', need=True),
            'tracking_no':  _pick(_m2, "송장번호", 'tracking_no'),
            'recipient':    _pick(_m3, "수취인", 'recipient'),
            'product_name': _pick(_m1, "상품명", 'product_name'),
            'qty':          _pick(_m2, "수량", 'qty'),
            'courier':      _pick(_m3, "택배사", 'courier'),
        }
        if not _cm.get('order_no'):
            st.error("⚠️ **주문번호** 열을 지정해야 분류할 수 있습니다.")
            return

        _dd = st.date_input("발송일 (청구 귀속일)", value=date.today(), key="du_date",
                            help="이 날짜로 발송 기록이 남고, 그날 청구에 잡힙니다.")

        _idx, _dup = du.build_order_owner_index()
        if _dup:
            st.warning(f"⚠️ 두 사용자에 걸친 주문번호 {len(_dup)}건이 있습니다 — "
                       "그 건은 분류가 부정확할 수 있습니다.")
        _by_user, _unknown = du.classify_rows(_df.to_dict('records'), _cm, _idx)

        _tot = sum(len(v) for v in _by_user.values())
        st.markdown(f"### 분류 결과 — {_tot}건 매칭 · {len(_unknown)}건 미분류")
        if _by_user:
            _sum = []
            for _u, _rows in sorted(_by_user.items(), key=lambda kv: -len(kv[1])):
                _ex = du.existing_dispatch(_u, [r['order_no'] for r in _rows])
                _sum.append({'사용자': dmap.get(_u, _u), '건수': len(_rows),
                             '이미 발송기록 있음': len(_ex),
                             '새로 저장될 건': len(_rows) - len(_ex)})
            st.dataframe(pd.DataFrame(_sum), use_container_width=True, hide_index=True)
        if _unknown:
            with st.expander(f"❓ 미분류 {len(_unknown)}건 — 어느 사용자 것인지 못 찾음",
                             expanded=False):
                st.caption("주문번호가 비었거나, 어느 사용자 DB에도 없는 번호입니다. "
                           "주문 수집이 안 된 건일 수 있습니다.")
                st.dataframe(pd.DataFrame([{
                    '행': u['_row'], '주문번호': u['order_no'], '수취인': u['recipient'],
                    '상품명': u['product_name'][:34], '사유': u.get('_why', '')}
                    for u in _unknown[:200]]), use_container_width=True, hide_index=True)

        _skip = st.checkbox("이미 발송 기록이 있는 주문은 건너뛰기", value=True,
                            key="du_skip",
                            help="끄면 같은 주문의 발송일을 이 날짜로 덮어씁니다.")
        if st.button(f"💾 {_tot}건 발송 기록 저장", key="du_save", type="primary",
                     disabled=not _by_user):
            _saved, _skipped = du.save_dispatch(_by_user, str(_dd),
                                                skip_existing=bool(_skip))
            _n = sum(_saved.values())
            st.session_state['_du_msg'] = (
                f"✅ 발송 기록 {_n}건 저장 — "
                + " · ".join(f"{dmap.get(u, u)} {c}건" for u, c in _saved.items())
                + (f"  ·  ⏭ 이미 있어 건너뜀 {_skipped}건" if _skipped else ""))
            st.rerun()


def _render_period_summary(dmap):
    """사용자별 일별·월별 — 주문수집 / 발송 건수와 청구금액.

    금액은 청구 원장(billing_ledger)에서 온다. 원장은 발송 1건 = 1행이라
    월 총액이 어느 발송건에서 나왔는지 되짚을 수 있다.
    예전에는 예상 저장·확정을 눌러야 생기는 스냅샷을 봐서, 그 버튼을 안 누르면
    표가 통째로 비어 있었다.
    월 청구 총액 = 제품 구매금액(원장) + 택배비 + 포장비.
    """
    import db_billing_ledger as bl

    st.divider()
    st.subheader("📅 사용자별 정리 — 주문수집 · 발송 · 청구금액")
    _t_day, _t_month = st.tabs(["일별", "월별"])
    _users = _sellers()

    with _t_day:
        c1, c2 = st.columns(2)
        _to = c2.date_input("종료일", value=date.today(), key="ps_sum_to")
        _from = c1.date_input("시작일", value=_to - timedelta(days=13), key="ps_sum_from")

        # 원장을 (날짜, 사용자)로 모아 둔다 — 취소분은 금액에서 뺀다
        _led = {}
        for _r in bl.get_ledger(str(_from), str(_to)):
            if str(_r.get('status')) == 'canceled':
                continue
            _k = (_r['dispatched_at'], _r['username'])
            _e = _led.setdefault(_k, {'amt': 0, 'cnt': 0, 'nop': 0})
            _e['amt'] += int(_r.get('amount') or 0)
            _e['cnt'] += 1
            if int(_r.get('amount') or 0) <= 0:
                _e['nop'] += 1

        _rows = []
        for _u in _users:
            for _d, _c in get_order_dispatch_counts(_u, str(_from), str(_to)).items():
                if not (_c['orders'] or _c['dispatch']):
                    continue
                _l = _led.get((_d, _u)) or {'amt': 0, 'cnt': 0, 'nop': 0}
                _rows.append({
                    '날짜': _d,
                    '사용자': dmap.get(_u, _u),
                    '주문수집': _c['orders'],
                    '발송': _c['dispatch'],
                    '미발송': max(0, _c['orders'] - _c['dispatch']),
                    '청구건수': _l['cnt'],
                    '청구금액': _l['amt'],
                    '가격미확인': _l['nop'],
                    '_u': _u,
                })
        if not _rows:
            st.info(f"{_from} ~ {_to} 주문·발송 기록이 없습니다.")
        else:
            _rows.sort(key=lambda r: (r['날짜'], r['사용자']), reverse=True)
            _df = pd.DataFrame(_rows)
            _ev = st.dataframe(
                _df.drop(columns=['_u']), use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="ps_day_tbl",
                column_config={'청구금액': st.column_config.NumberColumn('청구금액', format='%d')})
            st.caption("👆 행을 클릭하면 그날 **발송 목록**이 아래에 열립니다.")
            _sel = (_ev.selection.rows if getattr(_ev, 'selection', None) else []) or []
            if _sel:
                _r = _rows[_sel[0]]
                _lst = get_dispatch_list(_r['_u'], _r['날짜'])
                st.markdown(f"#### 🚚 {_r['사용자']} — {_r['날짜']} 발송 {len(_lst)}건")
                if not _lst:
                    st.caption("이 날짜에 발송처리된 주문이 없습니다.")
                else:
                    _amt = {str(x['order_no']): int(x.get('amount') or 0)
                            for x in bl.get_ledger(_r['날짜'], _r['날짜'], username=_r['_u'])}
                    st.dataframe(pd.DataFrame([{
                        '주문번호': x.get('order_no'),
                        '수취인': x.get('recipient'),
                        '상품명': (x.get('product_name') or '')[:40],
                        '수량': x.get('qty'),
                        '택배사': x.get('courier') or '',
                        '송장번호': x.get('tracking_no') or '',
                        '청구금액': _amt.get(str(x.get('order_no')), 0),
                    } for x in _lst]), use_container_width=True, hide_index=True)
            _to_ = sum(r['주문수집'] for r in _rows)
            _td = sum(r['발송'] for r in _rows)
            _tc = sum(r['청구금액'] for r in _rows)
            _tn = sum(r['가격미확인'] for r in _rows)
            st.markdown(f"### 기간 합계 — 주문수집 **{_to_}건** · 발송 **{_td}건** · "
                        f"청구금액 **{fmt(_tc)}원**")
            if _to_ > _td:
                st.caption(f"⚠️ 아직 안 나간 주문이 {_to_ - _td}건입니다.")
            if _tn:
                st.caption(f"⚠️ 매입가가 아직 없는 발송건이 {_tn}건 — 영수증이 올라오면 "
                           "채워집니다. 아래 청구 원장에서 확인하세요.")

    with _t_month:
        _today = date.today()
        _yms, _y, _m = [], _today.year, _today.month
        for _ in range(12):
            _yms.append('%04d-%02d' % (_y, _m))
            _m -= 1
            if _m == 0:
                _y, _m = _y - 1, 12
        _ym = st.selectbox("정산 월", _yms, key="ps_sum_ym")
        import calendar as _cal
        _last = _cal.monthrange(int(_ym[:4]), int(_ym[5:7]))[1]
        _mf, _mt = '%s-01' % _ym, '%s-%02d' % (_ym, _last)

        _incl_fee = st.checkbox("택배비·포장비 포함", value=True, key="ps_ym_fee",
                                help="끄면 제품 구매금액만 봅니다.")
        _msum = bl.summarize(_mf, _mt)
        _mrows = []
        for _u in _users:
            _c = get_order_dispatch_counts(_u, _mf, _mt)
            _o = sum(v['orders'] for v in _c.values())
            _d = sum(v['dispatch'] for v in _c.values())
            _l = _msum.get(_u) or {'amount': 0, 'count': 0, 'no_price': 0,
                                   'pending': 0, 'confirmed': 0}
            _fee = {'ship_total': 0, 'pkg_total': 0, 'fees_total': 0}
            if _incl_fee:
                try:
                    _fee = compute_month_fees(_u, _ym) or _fee
                except Exception:
                    pass
            if not (_o or _d or _l['amount']):
                continue
            _mrows.append({
                '사용자': dmap.get(_u, _u),
                '주문수집': _o, '발송': _d,
                '청구건수': _l['count'],
                '제품 구매금액': _l['amount'],
                '택배비': int(_fee.get('ship_total') or 0),
                '포장비': int(_fee.get('pkg_total') or 0),
                '월 청구총액': _l['amount'] + int(_fee.get('fees_total') or 0),
                '가격미확인': _l.get('no_price', 0),
                '미확정': _l.get('pending', 0),
            })
        if not _mrows:
            st.info(f"{_ym} 기록이 없습니다. 발송 기록이 있어야 청구 원장이 만들어집니다.")
        else:
            _mrows.sort(key=lambda r: -r['월 청구총액'])
            st.dataframe(pd.DataFrame(_mrows), use_container_width=True, hide_index=True,
                         column_config={
                             '제품 구매금액': st.column_config.NumberColumn(format='%d'),
                             '택배비': st.column_config.NumberColumn(format='%d'),
                             '포장비': st.column_config.NumberColumn(format='%d'),
                             '월 청구총액': st.column_config.NumberColumn(format='%d'),
                         })
            _g = sum(r['제품 구매금액'] for r in _mrows)
            _t = sum(r['월 청구총액'] for r in _mrows)
            st.markdown(f"### {_ym} 월 청구총액: **{fmt(_t)}원** "
                        f"(제품 {fmt(_g)} + 택배·포장 {fmt(_t - _g)})  ·  "
                        f"사용자 {len(_mrows)}명")
            _nop = sum(r['가격미확인'] for r in _mrows)
            _pend = sum(r['미확정'] for r in _mrows)
            if _nop:
                st.warning(f"❗ 매입가가 아직 없는 발송건이 {_nop}건입니다 — "
                           "그만큼 **덜 청구**됩니다. 영수증을 올린 뒤 "
                           "'발송건에서 원장 만들기'를 다시 누르세요.")
            elif _pend:
                st.caption(f"ℹ️ 예상가로 잡힌 건이 {_pend}건입니다 — 영수증 반영 후 "
                           "원장을 다시 만들면 실단가로 바뀝니다.")
            st.download_button(
                "⬇️ 월별 내역 CSV",
                pd.DataFrame(_mrows).to_csv(index=False).encode('utf-8-sig'),
                file_name=f"청구내역_{_ym}.csv", mime="text/csv", key="ps_ym_csv")


def _render_ledger(dmap, USERNAME):
    """청구 원장 — 발송 1건 = 청구 근거 1행.

    총액 한 줄만 있으면 반품 한 건을 빼낼 수도, 이미 발행한 계산서를 설명할 수도
    없다. 건별 근거를 쌓아 계산서 발행의 바탕으로 쓴다.
    """
    import db_billing_ledger as bl

    st.divider()
    st.subheader("📒 청구 원장 — 발송건별 청구 근거")
    c1, c2, c3 = st.columns([1, 1, 1.4])
    _to = c2.date_input("종료일", value=date.today(), key="bl_to")
    _from = c1.date_input("시작일", value=_to - timedelta(days=6), key="bl_from")
    _stf = c3.selectbox("상태", ['(전체)', 'pending', 'confirmed', 'invoiced', 'canceled'],
                        format_func=lambda k: {'pending': '예상(미확정)', 'confirmed': '확정',
                                               'invoiced': '계산서 발행됨',
                                               'canceled': '취소'}.get(k, k),
                        key="bl_status")

    _b1, _b2 = st.columns([1.6, 4])
    if _b1.button("🔄 발송건에서 원장 만들기", key="bl_sync", type="primary",
                  help="기간 내 발송 기록을 훑어 원장 행을 만들고, 영수증에서 확정된 건은 "
                       "실단가로 채웁니다. 계산서가 발행된 행은 건드리지 않습니다."):
        _c = _u = _s = 0
        _d = _from
        while _d <= _to:
            for _un in _sellers():
                _r = bl.sync_from_dispatch(_un, str(_d))
                _c += _r['created']; _u += _r['updated']; _s += _r['skipped']
            _d += timedelta(days=1)
        st.session_state['_bl_msg'] = (
            f"✅ 원장 갱신 — 신규 {_c}건 · 갱신 {_u}건"
            + (f" · 발행돼 건너뜀 {_s}건" if _s else ""))
        st.rerun()

    _rows = bl.get_ledger(str(_from), str(_to),
                          status=(None if _stf == '(전체)' else _stf))
    if not _rows:
        st.info("원장이 비어 있습니다 — 위 '발송건에서 원장 만들기'를 눌러 채우세요.")
        return

    _sum = bl.summarize(str(_from), str(_to))
    st.dataframe(pd.DataFrame([{
        '사용자': dmap.get(u, u), '청구건수': v['count'], '청구액': fmt(v['amount']),
        '가격 미확인': v.get('no_price', 0),
        '예상(미확정)': v['pending'], '확정': v['confirmed'],
        '발행': v['invoiced'], '취소': v['canceled'],
    } for u, v in sorted(_sum.items(), key=lambda kv: -kv[1]['amount'])]),
        use_container_width=True, hide_index=True)
    _tot = sum(v['amount'] for v in _sum.values())
    _pend = sum(v['pending'] for v in _sum.values())
    st.markdown(f"### 기간 청구액: **{fmt(_tot)}원**  ·  {sum(v['count'] for v in _sum.values())}건")
    if _pend:
        st.caption(f"⚠️ 아직 예상가인 행이 {_pend}건입니다 — 영수증 정산을 하면 "
                   "실단가로 확정되고, 다시 '원장 만들기'를 누르면 반영됩니다.")
    _np = bl.unpriced_rows(str(_from), str(_to))
    if _np:
        with st.expander(f"❗ 매입가가 아직 없는 행 {len(_np)}건 — 이대로 계산서를 끊으면 "
                         "그만큼 덜 청구됩니다", expanded=False):
            st.caption("아직 구매하지 않아 매입가를 모르는 건입니다(정상). "
                       "영수증이 올라오면 채워집니다. "
                       "제품가격 DB에 코스트코번호 연결이 없어 0원인 것도 여기 섞이므로, "
                       "발행 전에 한 번 훑어보세요.")
            st.dataframe(pd.DataFrame([{
                '발송일': r['dispatched_at'],
                '사용자': dmap.get(r['username'], r['username']),
                '주문번호': r['order_no'],
                '상품명': (r['product_name'] or '')[:40],
                '수량': r['qty'],
            } for r in _np[:200]]), use_container_width=True, hide_index=True)

    with st.expander(f"📄 원장 상세 {len(_rows)}건", expanded=False):
        st.dataframe(pd.DataFrame([{
            '발송일': r['dispatched_at'],
            '사용자': dmap.get(r['username'], r['username']),
            '주문번호': r['order_no'],
            '상품명': (r['product_name'] or '')[:32],
            '수량': r['qty'], '소분': r['split_qty'],
            '단가': fmt(int(r['unit_cost'] or 0)),
            '청구액': fmt(int(r['amount'] or 0)),
            '근거': bl.COST_SOURCES.get(r['cost_source'], r['cost_source']),
            '상태': {'pending': '예상', 'confirmed': '확정',
                     'invoiced': '발행', 'canceled': '취소'}.get(r['status'], r['status']),
        } for r in _rows[:500]]), use_container_width=True, hide_index=True)


def _render_dispatch_upload(dmap, USERNAME):
    """발송 파일 업로드 — 주문번호로 사용자를 갈라 dispatch_log에 기록.

    청구는 발송 기준인데 송장 등록을 안 하는 계정이 있으면 청구가 0원이 된다
    (clglobal0919는 8/31~9/6 주문 205건에 발송 0건이었다).
    관리자가 전체 발송 파일 하나를 올려 분류하면 그 의존이 사라진다.
    """
    import dispatch_upload as du

    with st.expander("🚚 발송 파일 업로드 — 주문번호로 사용자 분류", expanded=False):
        st.caption(
            "전체 발송내역 파일(엑셀·CSV)을 올리면 **주문번호**로 각 사용자에게 나눠 "
            "발송 기록에 넣습니다. 이후 영수증 정산이 그 발송건에 매입가를 채웁니다. "
            "같은 파일을 두 번 올려도 중복 저장되지 않습니다.")
        _f = st.file_uploader("발송 파일 (xlsx · xls · csv)", type=['xlsx', 'xls', 'csv'],
                              key="du_file")
        if not _f:
            return
        try:
            if _f.name.lower().endswith('.csv'):
                _df = pd.read_csv(_f, dtype=str)
            else:
                _df = pd.read_excel(_f, dtype=str)
        except Exception as _e:
            st.error(f"파일을 읽지 못했습니다: {_e}")
            return
        if _df is None or _df.empty:
            st.warning("빈 파일입니다.")
            return
        _df = _df.fillna('')
        st.caption(f"📄 {_f.name} — {len(_df)}행 · 열 {len(_df.columns)}개")

        _cols = list(_df.columns)
        _guess = du.guess_columns(_cols)
        st.markdown("**열 매핑** — 자동으로 찾은 값이 맞는지 확인하세요")
        _m1, _m2, _m3 = st.columns(3)
        _opts = ['(없음)'] + [str(c) for c in _cols]

        def _pick(col, label, key, need=False):
            _d = _guess.get(key)
            _i = _opts.index(str(_d)) if _d and str(_d) in _opts else 0
            _v = col.selectbox(label + (" *" if need else ""), _opts, index=_i,
                               key=f"du_col_{key}")
            return None if _v == '(없음)' else _v

        _cm = {
            'order_no':     _pick(_m1, "주문번호", 'order_no', need=True),
            'tracking_no':  _pick(_m2, "송장번호", 'tracking_no'),
            'recipient':    _pick(_m3, "수취인", 'recipient'),
            'product_name': _pick(_m1, "상품명", 'product_name'),
            'qty':          _pick(_m2, "수량", 'qty'),
            'courier':      _pick(_m3, "택배사", 'courier'),
        }
        if not _cm.get('order_no'):
            st.error("⚠️ **주문번호** 열을 지정해야 분류할 수 있습니다.")
            return

        _dd = st.date_input("발송일 (청구 귀속일)", value=date.today(), key="du_date",
                            help="이 날짜로 발송 기록이 남고, 그날 청구에 잡힙니다.")

        _idx, _dup = du.build_order_owner_index()
        if _dup:
            st.warning(f"⚠️ 두 사용자에 걸친 주문번호 {len(_dup)}건이 있습니다 — "
                       "그 건은 분류가 부정확할 수 있습니다.")
        _by_user, _unknown = du.classify_rows(_df.to_dict('records'), _cm, _idx)

        _tot = sum(len(v) for v in _by_user.values())
        st.markdown(f"### 분류 결과 — {_tot}건 매칭 · {len(_unknown)}건 미분류")
        if _by_user:
            _sum = []
            for _u, _rows in sorted(_by_user.items(), key=lambda kv: -len(kv[1])):
                _ex = du.existing_dispatch(_u, [r['order_no'] for r in _rows])
                _sum.append({'사용자': dmap.get(_u, _u), '건수': len(_rows),
                             '이미 발송기록 있음': len(_ex),
                             '새로 저장될 건': len(_rows) - len(_ex)})
            st.dataframe(pd.DataFrame(_sum), use_container_width=True, hide_index=True)
        if _unknown:
            with st.expander(f"❓ 미분류 {len(_unknown)}건 — 어느 사용자 것인지 못 찾음",
                             expanded=False):
                st.caption("주문번호가 비었거나, 어느 사용자 DB에도 없는 번호입니다. "
                           "주문 수집이 안 된 건일 수 있습니다.")
                st.dataframe(pd.DataFrame([{
                    '행': u['_row'], '주문번호': u['order_no'], '수취인': u['recipient'],
                    '상품명': u['product_name'][:34], '사유': u.get('_why', '')}
                    for u in _unknown[:200]]), use_container_width=True, hide_index=True)

        _skip = st.checkbox("이미 발송 기록이 있는 주문은 건너뛰기", value=True,
                            key="du_skip",
                            help="끄면 같은 주문의 발송일을 이 날짜로 덮어씁니다.")
        if st.button(f"💾 {_tot}건 발송 기록 저장", key="du_save", type="primary",
                     disabled=not _by_user):
            _saved, _skipped = du.save_dispatch(_by_user, str(_dd),
                                                skip_existing=bool(_skip))
            _n = sum(_saved.values())
            st.session_state['_du_msg'] = (
                f"✅ 발송 기록 {_n}건 저장 — "
                + " · ".join(f"{dmap.get(u, u)} {c}건" for u, c in _saved.items())
                + (f"  ·  ⏭ 이미 있어 건너뜀 {_skipped}건" if _skipped else ""))
            st.rerun()


def _render_period_summary(dmap):
    """사용자별 일별·월별 — 주문수집 / 발송 건수와 청구액.

    청구액만 봐서는 '몇 건 받아 몇 건 내보냈나'를 알 수 없다. 수집만 되고 안 나간
    건이 쌓이면 청구가 어긋나므로 두 수를 나란히 둔다.
    발송 건수를 누르면(행 선택) 그날 나간 목록을 그 자리에서 편다.
    금액은 확정된 날은 확정액, 아직이면 예상액이다(스냅샷 기준).
    """
    st.divider()
    st.subheader("📅 사용자별 정리 — 주문수집 · 발송 · 청구")
    _t_day, _t_month = st.tabs(["일별", "월별"])
    _users = _sellers()

    with _t_day:
        c1, c2 = st.columns(2)
        _to = c2.date_input("종료일", value=date.today(), key="ps_sum_to")
        _from = c1.date_input("시작일", value=_to - timedelta(days=13), key="ps_sum_from")
        _snap = {(r['settle_date'], r['username']): r
                 for r in get_period_rows(str(_from), str(_to))}
        _rows = []
        for _u in _users:
            for _d, _c in get_order_dispatch_counts(_u, str(_from), str(_to)).items():
                if not (_c['orders'] or _c['dispatch']):
                    continue
                _sn = _snap.get((_d, _u))
                _rows.append({
                    '날짜': _d,
                    '사용자': dmap.get(_u, _u),
                    '주문수집': _c['orders'],
                    '발송': _c['dispatch'],
                    '미발송': max(0, _c['orders'] - _c['dispatch']),
                    '청구액': int(_sn['amount']) if _sn else 0,
                    '상태': {'est': '예상', 'final': '확정'}.get(
                        str((_sn or {}).get('status')), '-'),
                    '_u': _u,
                })
        if not _rows:
            st.info(f"{_from} ~ {_to} 주문·발송 기록이 없습니다.")
        else:
            _rows.sort(key=lambda r: (r['날짜'], r['사용자']), reverse=True)
            _df = pd.DataFrame(_rows)
            _ev = st.dataframe(
                _df.drop(columns=['_u']), use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="ps_day_tbl",
                column_config={'청구액': st.column_config.NumberColumn('청구액', format='%d')})
            st.caption("👆 행을 클릭하면 그날 **발송 목록**이 아래에 열립니다.")
            _sel = (_ev.selection.rows if getattr(_ev, 'selection', None) else []) or []
            if _sel:
                _r = _rows[_sel[0]]
                _lst = get_dispatch_list(_r['_u'], _r['날짜'])
                st.markdown(f"#### 🚚 {_r['사용자']} — {_r['날짜']} 발송 {len(_lst)}건")
                if not _lst:
                    st.caption("이 날짜에 발송처리된 주문이 없습니다.")
                else:
                    st.dataframe(pd.DataFrame([{
                        '주문번호': x.get('order_no'),
                        '수취인': x.get('recipient'),
                        '상품명': (x.get('product_name') or '')[:40],
                        '수량': x.get('qty'),
                        '택배사': x.get('courier') or '',
                        '송장번호': x.get('tracking_no') or '',
                        '구입가': int(x.get('cost_price') or 0),
                        '정산예정': int(x.get('settlement') or 0),
                    } for x in _lst]), use_container_width=True, hide_index=True)
            _to_ = sum(r['주문수집'] for r in _rows)
            _td = sum(r['발송'] for r in _rows)
            _tc = sum(r['청구액'] for r in _rows)
            st.markdown(f"### 기간 합계 — 주문수집 **{_to_}건** · 발송 **{_td}건** · "
                        f"청구액 **{fmt(_tc)}원**")
            if _to_ > _td:
                st.caption(f"⚠️ 아직 안 나간 주문이 {_to_ - _td}건입니다 — "
                           "송장 등록으로 발송처리하면 발송 건수에 잡힙니다.")

    with _t_month:
        _today = date.today()
        _yms, _y, _m = [], _today.year, _today.month
        for _ in range(12):
            _yms.append('%04d-%02d' % (_y, _m))
            _m -= 1
            if _m == 0:
                _y, _m = _y - 1, 12
        _ym = st.selectbox("정산 월", _yms, key="ps_sum_ym")
        import calendar as _cal
        _last = _cal.monthrange(int(_ym[:4]), int(_ym[5:7]))[1]
        _mf, _mt = '%s-01' % _ym, '%s-%02d' % (_ym, _last)
        _mon = get_monthly_summary(_ym)
        _mrows = []
        for _u in _users:
            _c = get_order_dispatch_counts(_u, _mf, _mt)
            _o = sum(v['orders'] for v in _c.values())
            _d = sum(v['dispatch'] for v in _c.values())
            _v = _mon.get(_u) or {'goods': 0, 'fees': 0, 'charge': 0,
                                  'days': 0, 'final_days': 0}
            if not (_o or _d or _v['charge']):
                continue
            _mrows.append({'사용자': dmap.get(_u, _u), '주문수집': _o, '발송': _d,
                           '미발송': max(0, _o - _d),
                           '구매금액': _v['goods'], '택배·포장': _v['fees'],
                           '청구액': _v['charge'],
                           '정산일수': _v['days'], '확정일수': _v['final_days']})
        if not _mrows:
            st.info(f"{_ym} 기록이 없습니다.")
        else:
            _mrows.sort(key=lambda r: -r['청구액'])
            st.dataframe(pd.DataFrame(_mrows), use_container_width=True, hide_index=True)
            st.markdown(
                f"### {_ym} — 주문수집 **{sum(r['주문수집'] for r in _mrows)}건** · "
                f"발송 **{sum(r['발송'] for r in _mrows)}건** · "
                f"청구액 **{fmt(sum(r['청구액'] for r in _mrows))}원**")
            _pend = sum(r['정산일수'] - r['확정일수'] for r in _mrows)
            if _pend:
                st.caption(f"⚠️ 확정되지 않은 날이 {_pend}건 있습니다 — 예상액으로 집계됐습니다.")


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
    _dm = st.session_state.pop('_du_msg', None)
    if _dm:
        st.success(_dm)
    _bm = st.session_state.pop('_bl_msg', None)
    if _bm:
        st.success(_bm)
    _rm = st.session_state.pop('_rm_msg', None)
    if _rm:
        st.success(_rm)
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

    _render_dispatch_upload(dmap, USERNAME)
    _render_period_summary(dmap)
    _render_ledger(dmap, USERNAME)
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
