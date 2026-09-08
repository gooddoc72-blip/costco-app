"""🧾 내 구매내역 정산 — 각 사용자가 자기 구매내역과 청구액을 확인한다.

관리자가 영수증 정산에서 '각 사용자에게 전송'하면 그 금액이 여기 확정으로 뜬다.

일별만 보여주면 '이번 달 얼마 나왔나'를 알 수가 없다. 계산서는 월 단위로
끊기므로 월별 합계와 그달 전체 품목 목록이 함께 있어야 한다.
"""
from datetime import date, timedelta

import io as _io

import streamlit as st
import pandas as pd

from db_purchase_settle import (
    compute_daily_purchase, get_snapshot, get_user_badge, month_fees_if_last_day,
    get_period_rows, get_order_dispatch_counts, compute_month_fees,
)
from utils import fmt


def _day_view(USERNAME):
    d = st.date_input("날짜", value=date.today(), key="mp_day")
    ds = str(d)

    items, cur_total = compute_daily_purchase(USERNAME, ds)
    snap = get_snapshot(ds, USERNAME)
    badge = get_user_badge(ds, USERNAME)
    is_final = bool(snap and snap.get('status') == 'final')

    if not items and not snap:
        st.info(f"{ds} 구매내역이 없습니다. 발송(송장 등록)한 주문이 있어야 청구가 잡힙니다.")
        return

    goods_total = int(snap['final_total']) if is_final else cur_total
    status_txt = ("✅ 확정 (영수증 반영)" if is_final
                  else ("🕐 예상 (영수증 반영 전)" if snap else "🕐 예상"))

    fees = month_fees_if_last_day(USERNAME, ds)
    charge_total = goods_total + (fees['fees_total'] if fees else 0)

    c1, c2 = st.columns([1.3, 3])
    c1.metric("청구액", f"{fmt(charge_total)}원")
    c2.caption(f"상태: **{status_txt}**  ·  구매 {len(items)}건  ·  {ds}"
               + ("  ·  📦 **말일 정산(택배·포장 포함)**" if fees else "  ·  매일=구매가만"))

    if fees:
        st.info(
            f"📦 **말일 정산** — 구매가 {fmt(goods_total)}원 "
            f"+ 그달 택배·포장 {fmt(fees['fees_total'])}원 "
            f"(택배 {fees['ship_count']}건 × {fmt(fees['ship_fee'])} = {fmt(fees['ship_total'])} · "
            f"포장 실배정 {fmt(fees['pkg_total'])})  =  **청구액 {fmt(charge_total)}원**")

    if badge:
        _diff = badge['diff']
        _arrow = "🔺" if _diff > 0 else "🔻"
        st.warning(
            f"{_arrow} **실단가 반영으로 금액이 변경되었습니다** — "
            f"확정 {fmt(badge['final_total'])}원 "
            f"(예상 {fmt(badge['est_total'])}원 대비 **{_diff:+,}원**), 변경 {len(badge['changed'])}건")
        if badge['changed']:
            st.dataframe(pd.DataFrame([{
                '상품': c['product_name'], '예상': fmt(c['prev']),
                '확정': fmt(c['now']), '차액': f"{c['diff']:+,}",
            } for c in badge['changed']]), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### 구매 상세")
    _df = pd.DataFrame([{
        '수취인': it['recipient'],
        '상품명': it['product_name'],
        '코스트코번호': it.get('product_no', ''),
        '수량': it['qty'],
        '구매단가': fmt(it['unit_price']),
        '구매금액': fmt(it['amount']),
    } for it in items])
    st.dataframe(_df, use_container_width=True, hide_index=True)

    _matched = sum(1 for it in items if it['amount'] > 0)
    if _matched < len(items):
        st.caption(f"ℹ️ 구매가 미등록 {len(items) - _matched}건은 0원으로 표시됩니다 "
                   "(관리자가 구매가/영수증 반영 시 갱신).")


def _month_view(USERNAME):
    """월별 — 계산서는 월 단위로 끊으므로 그달 전체가 한눈에 보여야 한다."""
    _today = date.today()
    _yms, _y, _m = [], _today.year, _today.month
    for _ in range(12):
        _yms.append('%04d-%02d' % (_y, _m))
        _m -= 1
        if _m == 0:
            _y, _m = _y - 1, 12
    _ym = st.selectbox("정산 월", _yms, key="mp_ym")
    import calendar as _cal
    _last = _cal.monthrange(int(_ym[:4]), int(_ym[5:7]))[1]
    _mf, _mt = '%s-01' % _ym, '%s-%02d' % (_ym, _last)

    _snap = {r['settle_date']: r for r in get_period_rows(_mf, _mt, username=USERNAME)}
    _cnt = get_order_dispatch_counts(USERNAME, _mf, _mt) or {}
    _days = sorted(set(_snap) | {d for d, v in _cnt.items() if v.get('dispatch')},
                   reverse=True)
    if not _days:
        st.info(f"{_ym} 발송·청구 기록이 없습니다.")
        return

    # 저장 전인 날은 지금 값으로 계산한다. '아직 저장 안 했다'는 이유로 0원이
    # 뜨면 그달 청구액이 실제보다 적게 보인다.
    _rows, _live = [], 0
    with st.spinner("월 집계 중..."):
        for _d in _days:
            _s = _snap.get(_d)
            _c = _cnt.get(_d) or {'orders': 0, 'dispatch': 0}
            if _s:
                _amt = int(_s.get('amount') or 0)
                _stt = {'est': '예상', 'final': '확정'}.get(str(_s.get('status')), '-')
            else:
                try:
                    _amt = int(compute_daily_purchase(USERNAME, _d)[1] or 0)
                except Exception:
                    _amt = 0
                _stt = '미저장'
                _live += 1
            _rows.append({'날짜': _d, '주문수집': _c.get('orders', 0),
                          '발송': _c.get('dispatch', 0), '구매금액': _amt, '상태': _stt})

    _goods = sum(r['구매금액'] for r in _rows)
    try:
        _fees = compute_month_fees(USERNAME, _ym) or {}
    except Exception:
        _fees = {}
    _fee_total = int(_fees.get('fees_total') or 0)

    m1, m2, m3 = st.columns(3)
    m1.metric("그달 구매금액", f"{fmt(_goods)}원")
    m2.metric("택배·포장", f"{fmt(_fee_total)}원")
    m3.metric("청구 합계", f"{fmt(_goods + _fee_total)}원")
    st.caption(f"발송 {sum(r['발송'] for r in _rows)}건 · "
               f"주문수집 {sum(r['주문수집'] for r in _rows)}건 · {len(_rows)}일"
               + (f"  ·  **미저장 {_live}일**은 지금 값으로 계산했습니다"
                  if _live else ""))

    st.dataframe(
        pd.DataFrame([{**r, '구매금액': fmt(r['구매금액'])} for r in _rows]),
        use_container_width=True, hide_index=True)

    # ── 그달 전체 품목 ──
    with st.expander(f"📋 {_ym} 구매 품목 전체 보기", expanded=False):
        _all = []
        with st.spinner("품목 모으는 중..."):
            for _d in _days:
                try:
                    _its, _ = compute_daily_purchase(USERNAME, _d)
                except Exception:
                    continue
                for _it in _its:
                    _all.append({'날짜': _d, '수취인': _it['recipient'],
                                 '상품명': _it['product_name'],
                                 '코스트코번호': _it.get('product_no', ''),
                                 '수량': _it['qty'],
                                 '구매단가': _it['unit_price'],
                                 '구매금액': _it['amount']})
        if not _all:
            st.caption("품목 내역이 없습니다.")
            return
        st.dataframe(
            pd.DataFrame([{**r, '구매단가': fmt(r['구매단가']),
                           '구매금액': fmt(r['구매금액'])} for r in _all]),
            use_container_width=True, hide_index=True)
        st.caption(f"{len(_all)}건 · 합계 {fmt(sum(r['구매금액'] for r in _all))}원")
        try:
            _buf = _io.BytesIO()
            with pd.ExcelWriter(_buf, engine='openpyxl') as _xw:
                pd.DataFrame(_all).to_excel(_xw, index=False, sheet_name='구매내역')
            _buf.seek(0)
            st.download_button(f"📥 {_ym} 구매내역 엑셀", data=_buf.getvalue(),
                               file_name=f"purchase_{USERNAME}_{_ym}.xlsx",
                               mime=("application/vnd.openxmlformats-officedocument"
                                     ".spreadsheetml.sheet"),
                               key="mp_xlsx")
        except Exception as _e:
            st.caption(f"엑셀 생성 실패: {_e}")


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    st.header("🧾 내 구매내역 정산")
    st.caption("코스트코 구매대행 **구매금액(청구액)**입니다. 관리자가 영수증을 반영해 "
               "전송하면 실단가로 갱신되고 변경 내용이 표시됩니다.")
    _t_day, _t_month = st.tabs(["일별", "월별"])
    with _t_day:
        _day_view(USERNAME)
    with _t_month:
        _month_view(USERNAME)
