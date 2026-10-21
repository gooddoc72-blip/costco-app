"""📅 수익계산 월별 분석 — 한 달 정산저장(profit_settlements)을 모아 보고, 점검하고, 고친다.

일별 화면은 하루씩만 보여서 '이번 달 얼마 벌었나'와 '어디가 틀렸나'를 한 번에
볼 수 없었다. 2026-09 실데이터를 훑어 보니 틀린 곳이 적지 않았다.
  · 같은 주문이 두 날짜에 저장돼 월 합계에 두 번 잡힘 (oxo 273건)
  · 구입가 0 — 수익 집계에서 조용히 빠짐 (clglobal0919 378건)
  · 저장 수익 ≠ 지금 값으로 계산한 수익 — 영수증 정산이 구입가만 바꾸고
    수익은 그대로 둔다(receipt_settle.apply_receipt_settlement)
그래서 합계보다 **점검 목록**이 이 화면의 본론이다.

합계 규칙은 대시보드·홈(db_stats._PS_PROFIT_EXPR)과 같다: 수익은 저장값이 아니라
지금 값으로 다시 계산하고, 구입가 0 행은 수익에서 뺀다. 화면마다 수익이 다르면
어느 쪽도 믿을 수 없다.
"""
from collections import defaultdict
from datetime import date

import pandas as pd
import streamlit as st

from db_profit_calc import (
    get_month_settlements, get_profit_months, update_profit_rows,
    delete_profit_rows, calc_row_profit, get_settlements_by_order_nos,
)
from utils import fmt

#: 구입가가 판매가의 이 배수를 넘으면 묶음(×N)·소분 계산 오류를 의심한다.
COST_HIGH_RATIO = 2.0
#: 구입가가 판매가의 이 비율보다 낮으면 수량 반영 누락을 의심한다
#  (9월 살사소스 4개: 판매 35,560원에 구입가 7,495원 — 1개 값만 들어감).
COST_LOW_RATIO = 0.3

# (코드, 제목, 심각도) — 심각도 순으로 보여 준다.
CHECKS = [
    ('dup',      '같은 주문이 여러 날짜에 저장됨 (월 합계에 중복)', 'high'),
    ('nocost',   '구입가 없음 — 수익 집계에서 빠짐',               'high'),
    ('loss',     '역마진 (수익 < 0)',                             'high'),
    ('costhigh', f'구입가가 판매가의 {COST_HIGH_RATIO:g}배 초과 — 묶음/소분 오류 의심', 'high'),
    ('costlow',  f'구입가가 판매가의 {int(COST_LOW_RATIO * 100)}% 미만 — 수량 반영 누락 의심', 'mid'),
    ('nosettle', '정산예정 0원',                                    'mid'),
    ('nodisp',   '발송 기록 없음 — 취소·미발송이면 빼야 함(앱 밖 발송이면 정상)', 'mid'),
    ('stale',    '저장 수익 ≠ 현재 값으로 계산한 수익',             'mid'),
    ('kw',       '키워드로 매칭됨 — 맞는 상품인지 확인',            'low'),
    ('noship',   '택배비 0원',                                     'low'),
]
_CHECK_TITLE = {c: t for c, t, _ in CHECKS}
_SEV_ICON = {'high': '🔴', 'mid': '🟠', 'low': '🟡'}


def row_profit(r):
    """집계용 수익 — 구입가 0이면 0(대시보드와 같은 규칙)."""
    return calc_row_profit(r) if int(r.get('cost_price') or 0) > 0 else 0


def audit_rows(rows, other=(), dispatched=None):
    """행마다 걸린 점검 코드 목록. 반환: {id: [코드, ...]} (문제 없는 행은 빠짐).
    other: 같은 주문번호의 **다른 달** 행 — 달을 넘긴 중복(9/30·10/1)도 잡는다."""
    by_order = defaultdict(set)
    for r in list(rows) + list(other):
        _o = str(r.get('order_no') or '')
        if _o and not _o.startswith('_r'):
            by_order[_o].add(r.get('settlement_date'))
    out = {}
    for r in rows:
        f = []
        cost = int(r.get('cost_price') or 0)
        amt = int(r.get('order_amount') or 0)
        if len(by_order.get(str(r.get('order_no') or ''), ())) > 1:
            f.append('dup')
        if cost <= 0:
            f.append('nocost')
        else:
            if calc_row_profit(r) < 0:
                f.append('loss')
            if amt > 0 and cost > amt * COST_HIGH_RATIO:
                f.append('costhigh')
            elif amt > 0 and cost < amt * COST_LOW_RATIO:
                f.append('costlow')
            if calc_row_profit(r) != int(r.get('profit') or 0):
                f.append('stale')
        if int(r.get('settlement_amount') or 0) <= 0:
            f.append('nosettle')
        _ono = str(r.get('order_no') or '')
        if dispatched is not None and _ono not in dispatched and not _ono.startswith('_r'):
            f.append('nodisp')
        if '키워드' in str(r.get('match_source') or ''):
            f.append('kw')
        if int(r.get('delivery_cost') or 0) <= 0:
            f.append('noship')
        if f:
            out[r['id']] = f
    return out


def summarize(rows):
    """월 합계. 매출·정산은 전 행, 수익은 구입가 있는 행만."""
    s = defaultdict(int)
    for r in rows:
        s['cnt'] += 1
        s['qty'] += int(r.get('qty') or 0)
        s['sales'] += int(r.get('order_amount') or 0)
        s['settle'] += int(r.get('settlement_amount') or 0)
        s['ship_in'] += int(r.get('shipping_fee') or 0)
        s['cost'] += int(r.get('cost_price') or 0)
        s['delivery'] += int(r.get('delivery_cost') or 0)
        s['box'] += int(r.get('box_cost') or 0)
        s['profit'] += row_profit(r)
        if int(r.get('cost_price') or 0) <= 0:
            s['nocost_cnt'] += 1
            s['nocost_settle'] += int(r.get('settlement_amount') or 0)
    return dict(s)


def _prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "-"


def render_monthly(USERNAME, IS_ADMIN):
    """월별 분석 화면. 관리자는 다른 사용자도 골라 본다."""
    target = USERNAME
    if IS_ADMIN:
        try:
            from db import get_all_users
            _us = [u['username'] for u in get_all_users()
                   if (u.get('status') or 'active') == 'active']
        except Exception:
            _us = [USERNAME]
        if USERNAME not in _us:
            _us.insert(0, USERNAME)
        target = st.selectbox("👤 사용자 (관리자)", _us, index=_us.index(USERNAME),
                              key="pm_user")

    _months = get_profit_months(target)
    _this = date.today().strftime("%Y-%m")
    if _this not in _months:
        _months = [_this] + _months
    _c1, _c2 = st.columns([1, 3])
    ym = _c1.selectbox("📅 월", _months, key=f"pm_month_{target}")
    rows = get_month_settlements(target, ym)
    if not rows:
        st.info(f"{ym}에 정산저장된 주문이 없습니다. 일별 화면에서 '📊 정산저장'을 하면 여기 모입니다.")
        return

    s = summarize(rows)
    ps = summarize(get_month_settlements(target, _prev_month(ym)))
    other = [r for r in get_settlements_by_order_nos(
                 target, {str(r.get('order_no') or '') for r in rows})
             if str(r.get('settlement_date') or '')[:7] != ym]
    try:
        from db import get_user_db as _gud
        _c = _gud(target)
        _dispatched = {str(r[0]) for r in _c.execute('SELECT order_no FROM dispatch_log')}
        _c.close()
    except Exception:
        _dispatched = None
    issues = audit_rows(rows, other, dispatched=_dispatched)

    # ── 1) 월 합계 ────────────────────────────────────────────
    st.subheader(f"📊 {ym} 합계" + (f" — {target}" if target != USERNAME else ""))

    def _d(k):
        return (f"{s.get(k, 0) - ps.get(k, 0):+,}원 (전월 대비)") if ps else None
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("주문", f"{s['cnt']:,}건", (f"{s['cnt'] - ps.get('cnt', 0):+,}건" if ps else None))
    k2.metric("매출", f"{fmt(s['sales'])}원", _d('sales'))
    k3.metric("정산(예정+실정산)", f"{fmt(s['settle'])}원", _d('settle'))
    k4.metric("수익", f"{fmt(s['profit'])}원", _d('profit'))
    k5, k6, k7, k8 = st.columns(4)
    k5.metric("구입가", f"{fmt(s['cost'])}원")
    k6.metric("택배비 + 박스비", f"{fmt(s['delivery'] + s['box'])}원")
    k7.metric("고객 배송비", f"{fmt(s['ship_in'])}원")
    k8.metric("수익률 (수익÷매출)", _pct(s['profit'], s['sales']))
    if s.get('nocost_cnt'):
        st.warning(f"⚠️ 구입가가 없는 **{s['nocost_cnt']}건**(정산 {fmt(s['nocost_settle'])}원)은 "
                   "수익에서 빠져 있습니다 — 아래 점검에서 구입가를 넣으면 수익에 들어갑니다.")
    _dup_n = sum(1 for v in issues.values() if 'dup' in v)
    if _dup_n:
        st.error(f"🔴 같은 주문이 여러 날짜에 저장된 행이 **{_dup_n}건** — 매출·수익이 두 번 "
                 "잡혀 있습니다. 아래 점검 › '중복'에서 정리하세요.")

    # ── 2) 일별 추이 ──────────────────────────────────────────
    with st.expander("📆 일별 내역", expanded=False):
        by_day = defaultdict(list)
        for r in rows:
            by_day[r['settlement_date']].append(r)
        try:
            from db_dispatch_log import get_dispatch_counts
            _disp = get_dispatch_counts(target, f"{ym}-01", f"{ym}-31")
        except Exception:
            _disp = {}
        _drows = []
        for d in sorted(set(by_day) | set(_disp)):
            ds = summarize(by_day.get(d, []))
            _drows.append({
                '날짜': d, '발송': _disp.get(d, 0), '정산저장': ds.get('cnt', 0),
                '매출': ds.get('sales', 0), '정산': ds.get('settle', 0),
                '구입가': ds.get('cost', 0), '수익': ds.get('profit', 0),
                '점검': sum(1 for r in by_day.get(d, []) if r['id'] in issues),
            })
        _df = pd.DataFrame(_drows)
        st.bar_chart(_df.set_index('날짜')[['수익']])
        st.dataframe(_df, use_container_width=True, hide_index=True,
                     column_config={k: st.column_config.NumberColumn(k, format="%d")
                                    for k in ('매출', '정산', '구입가', '수익')})
        _gap = [r['날짜'] for r in _drows if r['발송'] and r['정산저장'] < r['발송']]
        if _gap:
            st.caption("⚠️ 발송 건수보다 정산저장이 적은 날: " + ", ".join(_gap)
                       + " — 일별 화면에서 그날을 열어 정산저장하세요.")

    # ── 3) 상품별 ────────────────────────────────────────────
    with st.expander("🏷 상품별 수익 (상위·하위)", expanded=False):
        by_p = defaultdict(lambda: defaultdict(int))
        for r in rows:
            p = by_p[r.get('product_name') or '(이름 없음)']
            p['건수'] += 1
            p['수량'] += int(r.get('qty') or 0)
            p['매출'] += int(r.get('order_amount') or 0)
            p['수익'] += row_profit(r)
        _pdf = pd.DataFrame([{'상품명': k, **v} for k, v in by_p.items()]).sort_values('수익')
        _a, _b = st.columns(2)
        _a.caption("💰 수익 상위 10")
        _a.dataframe(_pdf.tail(10).iloc[::-1], hide_index=True, use_container_width=True)
        _b.caption("🔻 수익 하위 10")
        _b.dataframe(_pdf.head(10), hide_index=True, use_container_width=True)

    # ── 4) 점검 · 수정 ───────────────────────────────────────
    st.subheader("🔍 점검 · 수정")
    if not issues:
        st.success("✅ 점검에 걸린 행이 없습니다.")
        return
    _cnt = defaultdict(int)
    for v in issues.values():
        for c in v:
            _cnt[c] += 1
    _opts = [c for c, _, _ in CHECKS if _cnt.get(c)]
    st.caption(" · ".join(f"{_SEV_ICON[sv]} {t.split(' —')[0]} **{_cnt[c]}**"
                          for c, t, sv in CHECKS if _cnt.get(c)))
    pick = st.radio("점검 항목", _opts, horizontal=True, key=f"pm_pick_{target}_{ym}",
                    format_func=lambda c: f"{_CHECK_TITLE[c].split(' —')[0]} ({_cnt[c]})")
    st.caption(f"{_SEV_ICON[dict((c, sv) for c, _, sv in CHECKS)[pick]]} {_CHECK_TITLE[pick]}")

    sel = [r for r in rows if pick in issues.get(r['id'], [])]
    if pick == 'dup':
        _dup_o = {str(r.get('order_no')) for r in sel}
        _render_dup(target, sel + [r for r in other if str(r.get('order_no')) in _dup_o])
        return
    if pick == 'stale':
        st.info("영수증 정산이 구입가를 바꿀 때 저장 수익은 그대로 남아 생깁니다. "
                "합계는 **현재 값 기준 수익**으로 계산합니다. 구입가가 맞는지 보고, "
                "틀렸으면 고쳐 저장하세요 — 저장하면 수익이 다시 계산됩니다.")

    _edf = pd.DataFrame([{
        'id': r['id'], '날짜': r['settlement_date'], '수취인': r.get('recipient', ''),
        '상품명': r.get('product_name', ''), '수량': int(r.get('qty') or 0),
        '매출': int(r.get('order_amount') or 0),
        '정산': int(r.get('settlement_amount') or 0),
        '구입가': int(r.get('cost_price') or 0),
        '택배비': int(r.get('delivery_cost') or 0),
        '박스비': int(r.get('box_cost') or 0),
        '저장수익': int(r.get('profit') or 0),
        '현재수익': calc_row_profit(r),
        '매칭': r.get('match_source', ''),
        '다른 점검': ", ".join(_CHECK_TITLE[c].split(' —')[0].split(' (')[0]
                            for c in issues[r['id']] if c != pick),
    } for r in sel])
    _ed = st.data_editor(
        _edf, hide_index=True, use_container_width=True,
        key=f"pm_ed_{target}_{ym}_{pick}",
        disabled=[c for c in _edf.columns if c not in ('정산', '구입가', '택배비', '박스비')],
        column_config={
            'id': None,
            '구입가': st.column_config.NumberColumn('구입가 ✏️', min_value=0, step=100,
                                                 help='이 주문 전체 구입가(수량 포함)'),
            '정산': st.column_config.NumberColumn('정산 ✏️', min_value=0, step=100),
            '택배비': st.column_config.NumberColumn('택배비 ✏️', min_value=0, step=100),
            '박스비': st.column_config.NumberColumn('박스비 ✏️', min_value=0, step=100),
        })
    _map = {'정산': 'settlement_amount', '구입가': 'cost_price',
            '택배비': 'delivery_cost', '박스비': 'box_cost'}
    edits = {}
    for (_, a), (_, b) in zip(_edf.iterrows(), _ed.iterrows()):
        ch = {_map[k]: int(b[k] or 0) for k in _map if int(a[k] or 0) != int(b[k] or 0)}
        if ch:
            edits[int(a['id'])] = ch
    if edits:
        st.caption(f"✏️ 수정 {len(edits)}행 — 저장하면 수익이 다시 계산됩니다.")
    if st.button(f"💾 수정 저장 ({len(edits)}행)", type="primary", disabled=not edits,
                 key=f"pm_save_{target}_{ym}_{pick}"):
        n = update_profit_rows(target, edits)
        st.success(f"✅ {n}행 저장했습니다.")
        st.rerun()


def _render_dup(target, sel):
    """중복 정리 — 같은 주문의 행들 중 남길 하나를 고른다(기본: 가장 이른 날짜)."""
    st.info("같은 상품주문번호가 여러 날짜에 저장돼 있습니다(다른 달 날짜 포함). "
            "8~9월 주문수집 '이월 중복' 버그(9/28 수정)로 생긴 것이라 그 뒤로는 새로 생기지 "
            "않습니다. 주문은 한 번만 정산돼야 하므로 "
            "**한 날짜만 남기고** 나머지를 지웁니다. 기본은 **발송기록의 발송일**과 같은 "
            "날짜를, 발송기록이 없으면 가장 이른 날짜를 남깁니다 — 다른 날이 맞으면 "
            "'남김'을 옮기세요.")
    groups = defaultdict(list)
    for r in sel:
        groups[str(r.get('order_no'))].append(r)
    try:
        from db_dispatch_log import get_dispatch_by_order_nos
        _disp = get_dispatch_by_order_nos(target, list(groups))
    except Exception:
        _disp = {}
    _rows = []
    for ono, rs in groups.items():
        rs = sorted(rs, key=lambda x: (x['settlement_date'], x['id']))
        _dd = str((_disp.get(ono) or {}).get('dispatched_at') or '')[:10]
        _keep = next((r['id'] for r in rs if r['settlement_date'] == _dd), rs[0]['id'])
        for r in rs:
            _rows.append({'id': r['id'], '주문번호': ono, '날짜': r['settlement_date'],
                          '발송일': _dd, '수취인': r.get('recipient', ''),
                          '상품명': r.get('product_name', ''),
                          '매출': int(r.get('order_amount') or 0),
                          '수익': row_profit(r), '남김': r['id'] == _keep})
    _df = pd.DataFrame(_rows)
    _ed = st.data_editor(_df, hide_index=True, use_container_width=True,
                         key=f"pm_dup_{target}",
                         disabled=[c for c in _df.columns if c != '남김'],
                         column_config={'id': None})
    _keep = _ed.groupby('주문번호')['남김'].sum()
    _bad = list(_keep[_keep != 1].index)
    if _bad:
        st.warning(f"주문마다 '남김'을 정확히 하나씩 골라야 합니다 — 확인 필요 {len(_bad)}건")
    _del = [int(i) for i, k in zip(_ed['id'], _ed['남김']) if not k]
    _amt = int(_ed.loc[~_ed['남김'], '매출'].sum()) if _del else 0
    st.caption(f"지울 행 {len(_del)}개 · 중복으로 잡혀 있던 매출 {fmt(_amt)}원")
    _ck = st.checkbox("지울 행을 확인했습니다", key=f"pm_dup_ck_{target}")
    if st.button(f"🗑 중복 {len(_del)}행 지우기", type="primary",
                 disabled=bool(_bad) or not _del or not _ck, key=f"pm_dup_go_{target}"):
        n = delete_profit_rows(target, _del)
        st.success(f"✅ {n}행 지웠습니다.")
        st.rerun()
