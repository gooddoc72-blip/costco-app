"""🧾 내 구매내역 정산 — 각 사용자가 자기 구매내역과 청구액·입금상태를 확인한다.

관리자가 영수증 정산에서 정산을 확정하면 그 금액이 여기 뜬다. 청구·입금 상태도
같은 원장(db_settle)에서 읽으므로 관리자 화면과 값이 어긋날 수 없다.

일별만 보여주면 '이번 달 얼마 나왔나'를 알 수가 없다. 계산서는 월 단위로
끊기므로 월별 합계와 그달 전체 품목 목록이 함께 있어야 한다.
"""
import io as _io
from datetime import date

import streamlit as st
import pandas as pd

import db_settle as _ds
from utils import fmt

_STATUS = {
    'draft':  ("⚪", "정산완료 — 아직 청구 전입니다"),
    'billed': ("🟡", "청구됨 — 입금해 주세요"),
    'paid':   ("🟢", "입금완료"),
}


def _day_view(USERNAME):
    d = st.date_input("날짜 (발송일 기준)", value=date.today(), key="mp_day")
    ds = str(d)

    inv = _ds.get_invoice(ds, USERNAME)
    items = _ds.get_items(ds, username=USERNAME)
    disp = _dispatch_rows(USERNAME, ds)

    if not inv:
        # 발송은 했는데 아직 정산 전인 날 — 청구가 없다고 화면을 비우면
        # "내가 보낸 게 잡히긴 한 건가"를 확인할 방법이 없다.
        if disp:
            st.info(f"{ds} 발송 {len(disp)}건은 등록됐고, **아직 정산 전**입니다. "
                    "관리자가 익일 영수증으로 정산하면 청구액이 여기 뜹니다.")
            _render_dispatch(disp, items)
        else:
            st.info(f"{ds} 청구 내역이 없습니다. 발송(송장 등록)한 주문을 관리자가 "
                    "익일 영수증으로 정산하면 여기 뜹니다.")
        return

    _icon, _txt = _STATUS.get(inv['status'], ("⚪", inv['status']))
    c1, c2 = st.columns([1.3, 3])
    c1.metric("청구액", f"{fmt(int(inv['total_amount'] or 0))}원")
    c2.markdown(f"### {_icon} {_txt}")
    _sub = f"구매 {len(items)}건 · {ds}"
    if inv['billed_at']:
        _sub += f" · 청구 {str(inv['billed_at'])[:16]}"
    if inv['paid_at']:
        _sub += f" · 입금 {str(inv['paid_at'])[:16]} ({fmt(int(inv['paid_amount'] or 0))}원)"
    c2.caption(_sub)

    _ship, _pack = int(inv['ship_fee'] or 0), int(inv['pack_fee'] or 0)
    st.caption(
        f"물건값 {fmt(int(inv['goods_amount'] or 0))}원"
        + (f" + 택배비 {fmt(_ship)}원" if _ship else "")
        + (f" + 포장비 {fmt(_pack)}원" if _pack else "")
        + f" = **청구액 {fmt(int(inv['total_amount'] or 0))}원**")

    if not items:
        st.caption("품목 내역이 없습니다 (비용만 청구된 날).")
        _render_dispatch(disp, items)
        return

    st.divider()
    st.markdown("#### 구매 상세")
    st.dataframe(pd.DataFrame([{
        '수취인': it['recipient'],
        '상품명': str(it['product_name'])[:44],
        '코스트코번호': it['product_no'],
        '수량': int(it['qty'] or 1),
        '구매단가': int(it['unit_price'] or 0),
        '구매금액': int(it['amount'] or 0),
        '근거': _ds.SOURCE_LABEL.get(it['source'], it['source']),
    } for it in items]), use_container_width=True, hide_index=True,
        column_config={k: st.column_config.NumberColumn(k, format='%d')
                       for k in ('구매단가', '구매금액')})
    st.caption("**근거** — 영수증: 그날 코스트코 영수증 실단가 · 재고: 이전 구입분 단가 · "
               "수동: 관리자가 직접 지정한 단가")

    _render_dispatch(disp, items)


def _dispatch_rows(username, ds):
    """그날 송장을 등록해 일괄발송 처리한 주문들."""
    try:
        from db import get_dispatch_log_by_date
        return get_dispatch_log_by_date(username, str(ds)) or []
    except Exception:
        return []


def _render_dispatch(disp, items):
    """🚚 발송 내역 — 그날 송장 등록해 내보낸 주문.

    청구 상세만 보여 주면 '내가 보낸 게 다 청구됐나'를 확인할 방법이 없다.
    발송은 사용자가 직접 한 일이고 청구는 관리자가 한 일이라, 둘을 나란히 놓고
    빠진 게 있는지 스스로 볼 수 있어야 한다.
    """
    st.divider()
    st.markdown(f"#### 🚚 발송 내역 — 송장 등록 {len(disp)}건")
    if not disp:
        st.caption("이 날짜에 송장을 등록해 발송 처리한 주문이 없습니다. "
                   "송장번호 탭에서 등록하면 여기 잡힙니다.")
        return

    _billed = {str(it['order_no']) for it in (items or []) if it.get('order_no')}
    _rows = []
    for r in disp:
        _ono = str(r.get('order_no') or '')
        _rows.append({
            '청구': '✅ 청구됨' if _ono in _billed else '⏳ 미청구',
            '수취인': str(r.get('recipient') or ''),
            '상품명': str(r.get('product_name') or '')[:44],
            '송장번호': str(r.get('tracking_no') or ''),
            '택배사': str(r.get('courier') or ''),
            '주문번호': _ono,
            '발송처리': str(r.get('created_at') or '')[:16],
        })
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)

    _miss = [r for r in _rows if r['청구'] == '⏳ 미청구']
    if _miss:
        st.warning(
            f"⏳ 발송했지만 아직 청구에 안 잡힌 주문 **{len(_miss)}건** — "
            "관리자가 영수증에서 이 상품을 못 찾았거나 아직 정산을 안 돌린 것입니다. "
            "며칠 지나도 그대로면 관리자에게 확인하세요.")
    _extra = len(_billed) - (len(disp) - len(_miss))
    if _extra > 0:
        st.caption(f"ℹ️ 청구 품목 중 {_extra}건은 이 날짜 발송 목록에 없습니다 — "
                   "이전 주문의 교환·추가 발송분이거나 관리자가 직접 배정한 건입니다.")


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

    invs = _ds.list_invoices(_mf, _mt, username=USERNAME)
    # 발송은 했는데 아직 정산 전인 날도 보여야 한다 — 청구만 줄 세우면
    # '내가 보낸 것이 다 잡혔나'를 확인할 수 없다.
    try:
        from db import get_dispatch_counts
        _dcnt = get_dispatch_counts(USERNAME, _mf, _mt) or {}
    except Exception:
        _dcnt = {}
    if not invs and not _dcnt:
        st.info(f"{_ym} 청구·발송 기록이 없습니다.")
        return

    _goods = sum(int(i['goods_amount'] or 0) for i in invs)
    _fees = sum(int(i['ship_fee'] or 0) + int(i['pack_fee'] or 0) for i in invs)
    _total = sum(int(i['total_amount'] or 0) for i in invs)
    _paid = sum(int(i['paid_amount'] or 0) for i in invs if i['status'] == 'paid')
    _unpaid = sum(int(i['total_amount'] or 0) for i in invs if i['status'] == 'billed')

    _dtot = sum(int(v or 0) for v in _dcnt.values())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("그달 물건값", f"{fmt(_goods)}원", f"발송 {_dtot}건")
    m2.metric("택배·포장", f"{fmt(_fees)}원")
    m3.metric("청구 합계", f"{fmt(_total)}원", f"{len(invs)}일")
    m4.metric("미입금", f"{fmt(_unpaid)}원", f"입금 {fmt(_paid)}원")

    # 청구서가 있는 날 + 발송만 있는 날을 합쳐 날짜순으로 보여 준다
    _by_date = {str(i['settle_date']): i for i in invs}
    _rows = []
    for _d in sorted(set(_by_date) | set(_dcnt), reverse=True):
        i = _by_date.get(_d)
        if i:
            _rows.append({
                '날짜': _d,
                '상태': _STATUS.get(i['status'], ("", i['status']))[0] + " "
                        + _ds.STATUS_LABEL.get(i['status'], i['status']),
                '발송': int(_dcnt.get(_d) or 0),
                '품목': int(i['item_count'] or 0),
                '물건값': int(i['goods_amount'] or 0),
                '택배·포장': int(i['ship_fee'] or 0) + int(i['pack_fee'] or 0),
                '청구액': int(i['total_amount'] or 0),
                '입금액': int(i['paid_amount'] or 0),
            })
        else:
            _rows.append({'날짜': _d, '상태': '⏳ 정산 전',
                          '발송': int(_dcnt.get(_d) or 0), '품목': 0,
                          '물건값': 0, '택배·포장': 0,
                          '청구액': 0, '입금액': 0})
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True,
        column_config={k: st.column_config.NumberColumn(k, format='%d')
                       for k in ('발송', '품목', '물건값', '택배·포장',
                                 '청구액', '입금액')})
    _pend = [r for r in _rows if r['상태'] == '⏳ 정산 전']
    if _pend:
        st.caption("⏳ **정산 전** — 발송은 등록됐지만 관리자가 아직 그날 영수증으로 "
                   "정산하지 않은 날입니다: "
                   + " · ".join(f"**{r['날짜']}** {r['발송']}건" for r in _pend[:8]))

    with st.expander(f"📋 {_ym} 구매 품목 전체 보기", expanded=False):
        _all = _ds.get_items_range(_mf, _mt, username=USERNAME)
        if not _all:
            st.caption("품목 내역이 없습니다.")
            return
        _rows = [{'날짜': it['settle_date'], '수취인': it['recipient'],
                  '상품명': it['product_name'], '코스트코번호': it['product_no'],
                  '수량': int(it['qty'] or 1), '구매단가': int(it['unit_price'] or 0),
                  '구매금액': int(it['amount'] or 0)} for it in _all]
        st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True,
                     column_config={k: st.column_config.NumberColumn(k, format='%d')
                                    for k in ('구매단가', '구매금액')})
        st.caption(f"{len(_rows)}건 · 합계 {fmt(sum(r['구매금액'] for r in _rows))}원")
        try:
            _buf = _io.BytesIO()
            with pd.ExcelWriter(_buf, engine='openpyxl') as _xw:
                pd.DataFrame(_rows).to_excel(_xw, index=False, sheet_name='구매내역')
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
    st.caption("코스트코 구매대행 **청구액**입니다. 관리자가 익일 영수증을 반영해 정산하면 "
               "실단가로 뜨고, 청구·입금 상태가 함께 표시됩니다.")

    _unpaid = [i for i in _ds.list_invoices('2000-01-01', str(date.today()),
                                            username=USERNAME, status='billed')]
    if _unpaid:
        _amt = sum(int(i['total_amount'] or 0) for i in _unpaid)
        st.warning(f"🟡 미입금 **{fmt(_amt)}원** · {len(_unpaid)}건 — "
                   + " · ".join(f"{i['settle_date']} {fmt(int(i['total_amount'] or 0))}원"
                                for i in _unpaid[:6])
                   + (" …" if len(_unpaid) > 6 else ""))

    _t_day, _t_month = st.tabs(["일별", "월별"])
    with _t_day:
        _day_view(USERNAME)
    with _t_month:
        _month_view(USERNAME)
