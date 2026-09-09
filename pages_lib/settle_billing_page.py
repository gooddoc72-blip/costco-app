"""💳 정산·청구 (관리자) — 정산리스트 · 청구 · 입금완료 · 미입금자.

청구액은 여기서 만들지 않는다. 영수증 정산이 남긴 원장(settle_item)을 읽어
보여줄 뿐이다. 금액을 만드는 곳과 보여 주는 곳을 갈라야 화면마다 값이
달라지는 일이 다시 생기지 않는다.

흐름: ⑤ 정산리스트 → ⑦ 청구 → 입금완료 체크 → ⑧ 미입금자
"""
from datetime import date, timedelta

import streamlit as st
import pandas as pd

import db_settle as _ds
from db import get_all_users
from utils import fmt

_ST_ICON = {'draft': '⚪ 정산완료', 'billed': '🟡 청구됨', 'paid': '🟢 입금완료'}


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    if not IS_ADMIN:
        st.error("관리자 전용 페이지입니다.")
        return

    st.header("💳 정산 · 청구")
    st.caption("영수증 정산이 남긴 원장을 그대로 읽습니다 — 이 화면이 청구액의 정본입니다. "
               "구매일 다음날 청구하고, 입금되면 체크하세요.")

    dmap = _disp_map()
    t_day, t_unpaid, t_month = st.tabs(
        ["📋 일별 정산·청구", "🔴 미입금자", "📆 월별 정리"])
    with t_day:
        _tab_day(USERNAME, dmap)
    with t_unpaid:
        _tab_unpaid(USERNAME, dmap)
    with t_month:
        _tab_month(dmap)


# ── ⑤⑦ 일별 정산리스트 → 청구 → 입금완료 ─────────────────────
def _tab_day(USERNAME, dmap):
    # 기본값은 어제 — 구매일 다음날 청구하므로 오늘 볼 것은 어제 정산분이다.
    _def = date.today() - timedelta(days=1)
    c1, c2 = st.columns([1, 3])
    d = c1.date_input("정산 날짜 (발송일 기준)", value=_def, key="sb_day")
    ds = str(d)

    _recent = _ds.settled_dates(limit=14)
    if _recent:
        c2.caption("최근 정산 — " + " · ".join(
            f"**{r['settle_date']}** {r['users']}명 {fmt(r['total'])}원"
            + ("" if not r['paid'] else f" (입금 {r['paid']})")
            for r in _recent[:6]))

    invs = _ds.list_invoices(ds)
    if not invs:
        st.info(f"{ds} 정산 내역이 없습니다. "
                "관리자 › 영수증 정산에서 그날 영수증을 매칭해 정산하세요.")
        return

    _tot = sum(int(i['total_amount'] or 0) for i in invs)
    _draft = [i for i in invs if i['status'] == 'draft']
    _billed = [i for i in invs if i['status'] == 'billed']
    _paid = [i for i in invs if i['status'] == 'paid']

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("총 청구액", f"{fmt(_tot)}원", f"{len(invs)}명")
    m2.metric("청구 전", f"{fmt(sum(int(i['total_amount'] or 0) for i in _draft))}원",
              f"{len(_draft)}명")
    m3.metric("청구·미입금", f"{fmt(sum(int(i['total_amount'] or 0) for i in _billed))}원",
              f"{len(_billed)}명")
    m4.metric("입금완료", f"{fmt(sum(int(i['paid_amount'] or 0) for i in _paid))}원",
              f"{len(_paid)}명")

    st.divider()
    st.subheader("📋 정산리스트")
    st.dataframe(pd.DataFrame([{
        '상태': _ST_ICON.get(i['status'], i['status']),
        '판매자': dmap.get(i['username'], i['username']),
        '품목': int(i['item_count'] or 0),
        '물건값': int(i['goods_amount'] or 0),
        '택배비': int(i['ship_fee'] or 0),
        '포장비': int(i['pack_fee'] or 0),
        '청구액': int(i['total_amount'] or 0),
        '입금액': int(i['paid_amount'] or 0),
        '청구일시': str(i['billed_at'] or '')[:16],
        '입금일시': str(i['paid_at'] or '')[:16],
    } for i in invs]), use_container_width=True, hide_index=True,
        column_config={k: st.column_config.NumberColumn(k, format='%d')
                       for k in ('물건값', '택배비', '포장비', '청구액', '입금액')})

    _render_items_detail(ds, invs, dmap)

    # ── ⑦ 청구 ──
    st.divider()
    st.subheader("📨 청구")
    if not _draft:
        st.success("이 날짜는 모두 청구했습니다.")
    else:
        st.caption("청구하면 사용자 화면에 '청구됨'으로 뜨고, 입금 전까지 미입금자 "
                   "목록에 남습니다. 금액이 0원인 사용자는 청구되지 않습니다.")
        _opts = [i['username'] for i in _draft if int(i['total_amount'] or 0) > 0]
        _sel = st.multiselect(
            "청구할 판매자 (비우면 전원)", _opts,
            format_func=lambda u: f"{dmap.get(u, u)} · {fmt(_amt_of(_draft, u))}원",
            key=f"sb_bill_sel_{ds}")
        _targets = _sel or _opts
        _sum = sum(_amt_of(_draft, u) for u in _targets)
        if st.button(f"📨 {len(_targets)}명 청구 ({fmt(_sum)}원)", type="primary",
                     key=f"sb_bill_{ds}", disabled=not _targets):
            n = _ds.mark_billed(ds, usernames=_targets, by=USERNAME)
            st.success(f"✅ {n}명 청구 완료 — 각 사용자 '내 구매내역 정산'에 청구액이 뜹니다.")
            st.rerun()

    # ── 입금완료 체크 ──
    st.divider()
    st.subheader("💰 입금 확인")
    if not _billed:
        st.caption("입금 대기 중인 청구가 없습니다.")
    else:
        st.caption("입금된 판매자를 체크하고 저장하세요. 금액이 다르면 실입금액을 "
                   "고칠 수 있습니다 (부분 입금).")
        _ed = st.data_editor(
            pd.DataFrame([{
                '입금완료': False,
                '판매자': dmap.get(i['username'], i['username']),
                '청구액': int(i['total_amount'] or 0),
                '실입금액': int(i['total_amount'] or 0),
                '메모': '',
                '_u': i['username'],
            } for i in _billed]),
            use_container_width=True, hide_index=True,
            key=f"sb_pay_ed_{ds}",
            disabled=['판매자', '청구액', '_u'],
            column_config={
                '입금완료': st.column_config.CheckboxColumn('입금완료'),
                '청구액': st.column_config.NumberColumn('청구액', format='%d'),
                '실입금액': st.column_config.NumberColumn('실입금액', format='%d', min_value=0),
                '_u': None,
            })
        _picked = [r for r in _ed.to_dict('records') if r.get('입금완료')]
        if _picked:
            st.markdown(f"선택 **{len(_picked)}명** · 입금액 합계 "
                        f"**{fmt(sum(int(r.get('실입금액') or 0) for r in _picked))}원**")
        if st.button(f"💰 입금완료 저장 ({len(_picked)}명)", type="primary",
                     key=f"sb_pay_{ds}", disabled=not _picked):
            for r in _picked:
                _ds.mark_paid(ds, str(r['_u']), paid_amount=int(r.get('실입금액') or 0),
                              memo=str(r.get('메모') or ''))
            st.success(f"✅ {len(_picked)}명 입금완료 처리")
            st.rerun()

    if _paid:
        with st.expander(f"↩️ 입금완료 취소 ({len(_paid)}명)", expanded=False):
            st.caption("잘못 체크했을 때만 쓰세요. 취소하면 '청구됨'으로 돌아가고 "
                       "청구액이 원장에서 다시 계산됩니다.")
            for i in _paid:
                c0, c1 = st.columns([3, 1])
                c0.markdown(f"**{dmap.get(i['username'], i['username'])}** · "
                            f"{fmt(int(i['paid_amount'] or 0))}원 · "
                            f"{str(i['paid_at'] or '')[:16]}")
                if c1.button("취소", key=f"sb_unpay_{ds}_{i['username']}"):
                    _ds.unmark_paid(ds, i['username'])
                    st.rerun()


def _amt_of(invs, username):
    for i in invs:
        if i['username'] == username:
            return int(i['total_amount'] or 0)
    return 0


def _render_items_detail(ds, invs, dmap):
    """청구 근거 — 어느 주문의 어느 품목이 얼마인지. 청구액만으로는 설명이 안 된다."""
    with st.expander("🔍 청구 근거 — 품목별 내역", expanded=False):
        _u = st.selectbox("판매자", [i['username'] for i in invs],
                          format_func=lambda u: dmap.get(u, u), key=f"sb_det_{ds}")
        items = _ds.get_items(ds, username=_u)
        if not items:
            st.caption("품목 내역이 없습니다 (비용만 청구된 날).")
            return
        st.dataframe(pd.DataFrame([{
            '근거': _ds.SOURCE_LABEL.get(it['source'], it['source']),
            '상품명': str(it['product_name'])[:44],
            '코스트코번호': it['product_no'],
            '수량': int(it['qty'] or 1),
            '팩단가': int(it['unit_price'] or 0),
            '금액': int(it['amount'] or 0),
            '주문번호': it['order_no'],
        } for it in items]), use_container_width=True, hide_index=True,
            column_config={k: st.column_config.NumberColumn(k, format='%d')
                           for k in ('팩단가', '금액')})
        _inv = next((i for i in invs if i['username'] == _u), None) or {}
        st.caption(f"물건값 {fmt(sum(int(i['amount'] or 0) for i in items))}원 "
                   f"+ 택배비 {fmt(int(_inv.get('ship_fee') or 0))}원 "
                   f"+ 포장비 {fmt(int(_inv.get('pack_fee') or 0))}원 "
                   f"= 청구액 {fmt(int(_inv.get('total_amount') or 0))}원")


# ── ⑧ 미입금자 ───────────────────────────────────────────────
def _tab_unpaid(USERNAME, dmap):
    st.subheader("🔴 미입금자")
    st.caption("청구했는데 아직 입금되지 않은 건입니다. 아직 청구하지 않은 정산분은 "
               "여기 없습니다 — 청구하지 않은 돈을 안 냈다고 할 수는 없으니까요.")

    by_user = _ds.unpaid_by_user()
    if not by_user:
        st.success("🎉 미입금 건이 없습니다.")
        return

    _total = sum(e['amount'] for e in by_user)
    st.metric("미입금 합계", f"{fmt(_total)}원", f"{len(by_user)}명")

    _today = date.today()
    _rows = []
    for e in by_user:
        _old = e['oldest']
        try:
            _dd = (_today - date(int(_old[:4]), int(_old[5:7]), int(_old[8:10]))).days
        except (ValueError, IndexError):
            _dd = 0
        _rows.append({
            '판매자': dmap.get(e['username'], e['username']),
            '미입금액': e['amount'],
            '건수(일)': e['days'],
            '가장 오래된 건': _old,
            '경과일': _dd,
            '최근 건': e['newest'],
        })
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True,
                 column_config={'미입금액': st.column_config.NumberColumn('미입금액',
                                                                     format='%d')})

    st.divider()
    st.subheader("📅 날짜별 미입금 내역")
    _sel_u = st.selectbox("판매자", [e['username'] for e in by_user],
                          format_func=lambda u: dmap.get(u, u), key="sb_unpaid_u")
    _mine = [i for i in _ds.unpaid_invoices() if i['username'] == _sel_u]
    _ed = st.data_editor(
        pd.DataFrame([{
            '입금완료': False,
            '정산일': i['settle_date'],
            '청구액': int(i['total_amount'] or 0),
            '실입금액': int(i['total_amount'] or 0),
            '청구일시': str(i['billed_at'] or '')[:16],
        } for i in _mine]),
        use_container_width=True, hide_index=True, key=f"sb_unpaid_ed_{_sel_u}",
        disabled=['정산일', '청구액', '청구일시'],
        column_config={
            '입금완료': st.column_config.CheckboxColumn('입금완료'),
            '청구액': st.column_config.NumberColumn('청구액', format='%d'),
            '실입금액': st.column_config.NumberColumn('실입금액', format='%d', min_value=0),
        })
    _picked = [r for r in _ed.to_dict('records') if r.get('입금완료')]
    if st.button(f"💰 입금완료 저장 ({len(_picked)}건)", type="primary",
                 key=f"sb_unpaid_save_{_sel_u}", disabled=not _picked):
        for r in _picked:
            _ds.mark_paid(str(r['정산일']), _sel_u,
                          paid_amount=int(r.get('실입금액') or 0))
        st.success(f"✅ {len(_picked)}건 입금완료 처리")
        st.rerun()


# ── 월별 정리 ────────────────────────────────────────────────
def _tab_month(dmap):
    st.subheader("📆 월별 정리")
    st.caption("계산서는 월 단위로 끊기므로 그달 합계를 함께 봅니다.")
    _ym = st.text_input("년월 (YYYY-MM)", value=date.today().strftime("%Y-%m"),
                        key="sb_month")
    try:
        summ = _ds.monthly_summary(_ym)
    except (ValueError, IndexError):
        st.error("년월 형식이 올바르지 않습니다 (예: 2026-09)")
        return
    if not summ:
        st.info(f"{_ym} 정산 내역이 없습니다.")
        return
    rows = [{
        '판매자': dmap.get(u, u),
        '물건값': e['goods'], '택배·포장': e['fees'], '청구액': e['total'],
        '입금완료': e['paid'], '미입금': e['unpaid'], '정산일수': e['days'],
    } for u, e in sorted(summ.items(), key=lambda kv: -kv[1]['total'])]
    rows.append({'판매자': '— 합계 —',
                 '물건값': sum(r['물건값'] for r in rows),
                 '택배·포장': sum(r['택배·포장'] for r in rows),
                 '청구액': sum(r['청구액'] for r in rows),
                 '입금완료': sum(r['입금완료'] for r in rows),
                 '미입금': sum(r['미입금'] for r in rows),
                 '정산일수': ''})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={k: st.column_config.NumberColumn(k, format='%d')
                                for k in ('물건값', '택배·포장', '청구액',
                                          '입금완료', '미입금')})
