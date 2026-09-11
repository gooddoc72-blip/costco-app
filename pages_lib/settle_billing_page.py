"""💳 정산·청구 (관리자) — 정산리스트 · 청구 · 예치금 차감 · 입금완료 · 미입금자.

청구액은 여기서 만들지 않는다. 영수증 정산이 남긴 원장(settle_item)을 읽어
보여줄 뿐이다. 금액을 만드는 곳과 보여 주는 곳을 갈라야 화면마다 값이
달라지는 일이 다시 생기지 않는다.

예치금도 마찬가지다. 잔액은 예치금 원장(db_deposit) 합계에서만 나온다.
예치금 차감은 **입금의 한 방법**이라 별도 상태를 만들지 않고 그날 청구서를
'입금완료'로 만든다 — 그래야 미입금자·월별 정리가 그대로 맞는다.

흐름: ⑤ 정산리스트 → ⑦ 청구 → 💳 예치금 차감 or 입금완료 체크 → ⑧ 미입금자
"""
from datetime import date, timedelta

import streamlit as st
import pandas as pd

import db_settle as _ds
import db_deposit as _dep
from db import get_all_users
from utils import fmt

_ST_ICON = {'draft': '⚪ 정산완료', 'billed': '🟡 청구됨', 'paid': '🟢 입금완료'}

#: 예치금으로 결제된 청구서에 남기는 표시 — 계좌 입금과 구분하는 유일한 근거는
#  예치금 원장이지만, 청구서만 봐도 알 수 있게 메모에도 같은 말을 남긴다.
_DEP_MEMO = '예치금 차감'


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    if not IS_ADMIN:
        st.error("관리자 전용 페이지입니다.")
        return

    st.header("💳 정산 · 청구")
    st.caption("영수증 정산이 남긴 원장을 그대로 읽습니다 — 이 화면이 청구액의 정본입니다. "
               "구매일 다음날 청구하고, 입금되면 체크하세요. "
               "**예치금이 있는 판매자는 입금을 기다리지 않고 그 자리에서 차감합니다.**")

    dmap = _disp_map()
    t_day, t_dep, t_unpaid, t_month = st.tabs(
        ["📋 일별 정산·청구", "💳 예치금", "🔴 미입금자", "📆 월별 정리"])
    with t_day:
        _tab_day(USERNAME, dmap)
    with t_dep:
        _tab_deposit(USERNAME, dmap)
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

    # 예치금 — 잔액은 원장 합계, 차감 여부는 그날 차감 행이 있는지로 본다
    _bal = _dep.balances()
    _ded = _dep.deducted_map(ds)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("총 청구액", f"{fmt(_tot)}원", f"{len(invs)}명")
    m2.metric("청구 전", f"{fmt(sum(int(i['total_amount'] or 0) for i in _draft))}원",
              f"{len(_draft)}명")
    m3.metric("청구·미입금", f"{fmt(sum(int(i['total_amount'] or 0) for i in _billed))}원",
              f"{len(_billed)}명")
    m4.metric("입금완료", f"{fmt(sum(int(i['paid_amount'] or 0) for i in _paid))}원",
              f"{len(_paid)}명 · 예치금 {len(_ded)}명")

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
        '결제': '💳 예치금' if i['username'] in _ded else (
            '🏦 계좌입금' if i['status'] == 'paid' else ''),
        '입금액': int(i['paid_amount'] or 0),
        '예치금잔액': int(_bal.get(i['username'], 0)),
        '청구일시': str(i['billed_at'] or '')[:16],
        '입금일시': str(i['paid_at'] or '')[:16],
    } for i in invs]), use_container_width=True, hide_index=True,
        column_config={k: st.column_config.NumberColumn(k, format='%d')
                       for k in ('물건값', '택배비', '포장비', '청구액', '입금액',
                                 '예치금잔액')})

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

    # ── 💳 예치금 차감 (입금을 기다리지 않는 길) ──
    _deduct_section(ds, invs, dmap, _bal, _ded, USERNAME)

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
                       "청구액이 원장에서 다시 계산됩니다. 예치금으로 결제한 건은 "
                       "**차감했던 금액도 예치금으로 되돌아갑니다.**")
            for i in _paid:
                _u = i['username']
                _isdep = _u in _ded
                c0, c1 = st.columns([3, 1])
                c0.markdown(f"**{dmap.get(_u, _u)}** · "
                            f"{fmt(int(i['paid_amount'] or 0))}원 · "
                            f"{'💳 예치금' if _isdep else '🏦 계좌입금'} · "
                            f"{str(i['paid_at'] or '')[:16]}")
                if c1.button("취소", key=f"sb_unpay_{ds}_{_u}"):
                    _ds.unmark_paid(ds, _u)
                    if _isdep:
                        # 청구서만 되돌리고 예치금을 그대로 두면 사용자는 쓰지도
                        # 않은 돈이 빠진 채로 남는다. 둘은 반드시 함께 움직인다.
                        _r = _dep.undo_deduct(ds, _u, by=USERNAME)
                        st.toast(f"💳 {fmt(int(_ded.get(_u, 0)))}원 예치금 반환 "
                                 f"(잔액 {fmt(_r['balance'])}원)", icon="↩️")
                    st.rerun()


def _amt_of(invs, username):
    for i in invs:
        if i['username'] == username:
            return int(i['total_amount'] or 0)
    return 0


def _render_items_detail(ds, invs, dmap):
    """청구 근거 — 어느 주문의 어느 품목이 얼마인지. 청구액만으로는 설명이 안 된다.

    오매칭 품목을 여기서 뺄 수 있다. 지운 주문은 '이미 정산된 주문' 목록에서
    빠지므로 영수증 정산에서 **자동으로 다시 매칭 후보가 된다** — 잘못 붙은 건을
    고치려고 그날 정산을 통째로 취소할 필요가 없다.
    """
    with st.expander("🔍 청구 근거 — 품목별 내역 · 오매칭 삭제", expanded=False):
        _u = st.selectbox("판매자", [i['username'] for i in invs],
                          format_func=lambda u: dmap.get(u, u), key=f"sb_det_{ds}")
        items = _ds.get_items(ds, username=_u)
        if not items:
            st.caption("품목 내역이 없습니다 (비용만 청구된 날).")
            return
        _inv = next((i for i in invs if i['username'] == _u), None) or {}
        _paid = str(_inv.get('status') or '') == 'paid'

        _rows = [{
            '삭제': False,
            '근거': _ds.SOURCE_LABEL.get(it['source'], it['source']),
            '상품명': str(it['product_name'])[:44],
            '코스트코번호': it['product_no'],
            '수량': int(it['qty'] or 1),
            '팩단가': int(it['unit_price'] or 0),
            '금액': int(it['amount'] or 0),
            '주문번호': it['order_no'],
            '_id': int(it['id']),
        } for it in items]
        _cfg = {'삭제': st.column_config.CheckboxColumn('삭제'), '_id': None,
                **{k: st.column_config.NumberColumn(k, format='%d')
                   for k in ('팩단가', '금액')}}
        _dis = ['근거', '상품명', '코스트코번호', '수량', '팩단가', '금액', '주문번호', '_id']

        if _paid:
            # 입금완료 건은 금액이 잠긴다 — 받은 돈과 청구액이 달라지면 무엇을
            # 받은 것인지 설명할 수 없다. 되돌리려면 '입금완료 취소'가 먼저다.
            st.info("🟢 입금완료된 건이라 품목을 뺄 수 없습니다. 고치려면 위 "
                    "**입금완료 취소**를 먼저 누르세요.")
            st.dataframe(pd.DataFrame([{k: v for k, v in r.items()
                                        if k not in ('삭제', '_id')} for r in _rows]),
                         use_container_width=True, hide_index=True,
                         column_config={k: st.column_config.NumberColumn(k, format='%d')
                                        for k in ('팩단가', '금액')})
        else:
            _ed = st.data_editor(pd.DataFrame(_rows), use_container_width=True,
                                 hide_index=True, key=f"sb_items_ed_{ds}_{_u}",
                                 disabled=_dis, column_config=_cfg)
            _pick = [int(r['_id']) for r in _ed.to_dict('records') if r.get('삭제')]
            if _pick:
                _sum = sum(int(r['금액']) for r in _ed.to_dict('records') if r.get('삭제'))
                st.warning(
                    f"🗑 **{len(_pick)}개 품목 · {fmt(_sum)}원**을 청구에서 뺍니다. "
                    "그 주문은 **영수증 정산에서 다시 매칭 대상**이 되고, 주문 구입가도 "
                    "정산 전 값으로 되돌아갑니다.")
            if st.button(f"🗑 선택 품목 삭제 ({len(_pick)}개)", key=f"sb_items_del_{ds}_{_u}",
                         disabled=not _pick):
                _n = _ds.delete_items_by_id(_pick)
                st.success(f"✅ {_n}개 품목을 뺐습니다 — 관리자 › 영수증 정산에서 "
                           f"**{ds}** 를 열어 다시 매칭하세요.")
                st.rerun()

        st.caption(f"물건값 {fmt(sum(int(i['amount'] or 0) for i in items))}원 "
                   f"+ 택배비 {fmt(int(_inv.get('ship_fee') or 0))}원 "
                   f"+ 포장비 {fmt(int(_inv.get('pack_fee') or 0))}원 "
                   f"= 청구액 {fmt(int(_inv.get('total_amount') or 0))}원")


# ── 💳 예치금 차감 (일별) ─────────────────────────────────────
def _deduct_section(ds, invs, dmap, bal, ded, USERNAME):
    """그날 청구액을 미리 맡긴 예치금에서 뺀다.

    차감은 자동으로 하지 않는다. 영수증 정산은 하루에 여러 회차로 나눠 돌리고
    회차마다 금액이 바뀌는데, 바뀔 때마다 돈이 저절로 빠지면 사용자 잔액이
    조용히 오르내린다. 금액이 확정되는 순간(관리자가 청구를 결정하는 순간)에
    한 번만, 눌러서 뺀다.
    """
    st.divider()
    st.subheader("💳 예치금 차감")

    if ded:
        st.success(f"💳 이미 차감됨 — **{len(ded)}명 · {fmt(sum(ded.values()))}원** · "
                   + " · ".join(f"{dmap.get(u, u)} {fmt(a)}원"
                                for u, a in sorted(ded.items(), key=lambda kv: -kv[1])[:6])
                   + (" …" if len(ded) > 6 else ""))

    _todo = [i for i in invs
             if i['status'] != 'paid' and int(i['total_amount'] or 0) > 0]
    if not _todo:
        st.caption("이 날짜에 예치금으로 결제할 청구가 없습니다.")
        return

    st.caption("예치금이 있는 판매자는 입금을 기다리지 않고 여기서 바로 차감합니다. "
               "차감하면 그날 청구서가 **입금완료(💳 예치금)** 가 되고, 되돌리려면 "
               "위 '입금완료 취소'를 쓰면 예치금도 함께 돌아갑니다.")

    _all = st.checkbox("예치금이 없는 판매자도 보기 (차감하면 잔액이 마이너스가 됩니다)",
                       key=f"sb_dep_all_{ds}")
    _rows = []
    for i in _todo:
        u = i['username']
        amt = int(i['total_amount'] or 0)
        b = int(bal.get(u, 0))
        if not _all and b <= 0:
            continue
        _rows.append({'차감': b >= amt, '판매자': dmap.get(u, u), '청구액': amt,
                      '현재 잔액': b, '차감 후': b - amt, '_u': u})
    if not _rows:
        st.info("예치금 잔액이 있는 판매자가 없습니다. "
                "'💳 예치금' 탭에서 입금을 등록하거나, 위 체크박스를 켜서 "
                "마이너스 차감을 하세요.")
        return
    _rows.sort(key=lambda r: -r['현재 잔액'])

    _ed = st.data_editor(
        pd.DataFrame(_rows), use_container_width=True, hide_index=True,
        key=f"sb_dep_ed_{ds}_{int(_all)}",
        disabled=['판매자', '청구액', '현재 잔액', '차감 후', '_u'],
        column_config={
            '차감': st.column_config.CheckboxColumn('차감'),
            '_u': None,
            **{k: st.column_config.NumberColumn(k, format='%d')
               for k in ('청구액', '현재 잔액', '차감 후')},
        })
    _picked = [r for r in _ed.to_dict('records') if r.get('차감')]
    _short = [r for r in _picked if int(r['차감 후']) < 0]
    if _short:
        st.error("⚠️ 잔액이 모자란 판매자가 있습니다 — 차감하면 마이너스로 남고 "
                 "그만큼 추가 예치를 받아야 합니다: "
                 + " · ".join(f"**{r['판매자']}** {fmt(-int(r['차감 후']))}원 부족"
                              for r in _short))
    _sum = sum(int(r['청구액']) for r in _picked)
    if st.button(f"💳 {len(_picked)}명 예치금 차감 ({fmt(_sum)}원)", type="primary",
                 key=f"sb_dep_go_{ds}", disabled=not _picked):
        _ok = 0
        for r in _picked:
            u, amt = str(r['_u']), int(r['청구액'])
            # 아직 청구 전(draft)이면 청구도 함께 남긴다 — 청구일시가 비면
            # 사용자 화면에서 "언제 청구된 건인지"를 알 수 없다.
            _ds.mark_billed(ds, usernames=[u], by=USERNAME)
            _dep.deduct(ds, u, amt, by=USERNAME)
            _ds.mark_paid(ds, u, paid_amount=amt, memo=_DEP_MEMO)
            _ok += 1
        st.success(f"✅ {_ok}명 예치금 차감 완료 ({fmt(_sum)}원) — "
                   "각 판매자 '내 구매내역 정산'에 잔액이 반영됩니다.")
        st.rerun()


# ── 💳 예치금 관리 ────────────────────────────────────────────
def _tab_deposit(USERNAME, dmap):
    st.subheader("💳 예치금")
    st.caption("판매자가 미리 맡긴 돈입니다. 잔액은 아래 **내역의 합계**에서만 나옵니다 — "
               "따로 적어 두는 잔액 숫자가 없어야 잔액과 내역이 어긋날 수 없습니다.")

    _bal = _dep.balances()
    _users = [u['username'] for u in get_all_users()]

    # ── 잔액 현황 ──
    _rows = [{'판매자': dmap.get(u, u),
              '잔액': int(_bal.get(u, 0)),
              '상태': ('⚠️ 부족(마이너스)' if int(_bal.get(u, 0)) < 0
                       else ('💳 사용 중' if int(_bal.get(u, 0)) > 0 else '— 예치 없음')),
              '_u': u} for u in _users]
    _rows.sort(key=lambda r: r['잔액'])
    _pos = sum(r['잔액'] for r in _rows if r['잔액'] > 0)
    _neg = [r for r in _rows if r['잔액'] < 0]

    m1, m2, m3 = st.columns(3)
    m1.metric("예치금 총잔액", f"{fmt(_pos)}원",
              f"{len([r for r in _rows if r['잔액'] > 0])}명")
    m2.metric("마이너스", f"{fmt(sum(r['잔액'] for r in _neg))}원", f"{len(_neg)}명")
    m3.metric("예치 없음", f"{len([r for r in _rows if r['잔액'] == 0])}명")

    if _neg:
        st.warning("⚠️ 잔액이 마이너스인 판매자 — 추가 예치를 받아야 합니다: "
                   + " · ".join(f"**{r['판매자']}** {fmt(-r['잔액'])}원" for r in _neg))

    st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != '_u'} for r in _rows]),
                 use_container_width=True, hide_index=True,
                 column_config={'잔액': st.column_config.NumberColumn('잔액', format='%d')})

    # ── 예치 등록 ──
    st.divider()
    st.subheader("➕ 예치 등록")
    st.caption("판매자가 보낸 돈을 확인하고 올립니다. 입금일은 실제 통장에 찍힌 날로 "
               "두세요 — 나중에 통장과 맞춰 볼 때 기준이 됩니다.")
    with st.form("dep_charge", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 1.4, 1.2])
        _u = c1.selectbox("판매자", _users, format_func=lambda u: dmap.get(u, u),
                          key="dep_ch_u")
        _amt = c2.number_input("예치 금액(원)", min_value=0, step=10000, key="dep_ch_amt")
        _d = c3.date_input("입금일", value=date.today(), key="dep_ch_d")
        _memo = st.text_input("메모 (은행·입금자명 등)", key="dep_ch_memo",
                              placeholder="예: 국민 홍길동 9/10")
        if st.form_submit_button("➕ 예치 등록", type="primary", use_container_width=True):
            r = _dep.charge(_u, int(_amt), tx_date=str(_d), memo=_memo, by=USERNAME)
            if r['ok']:
                st.success(f"✅ {dmap.get(_u, _u)} · {fmt(int(_amt))}원 예치 — "
                           f"잔액 {fmt(r['balance'])}원")
                st.rerun()
            else:
                st.error(r['msg'])

    # ── 수동 조정 ──
    with st.expander("🛠 예치금 수동 조정", expanded=False):
        st.caption("오입금·과입금을 바로잡을 때만 씁니다. 사유는 필수입니다 — "
                   "나중에 '왜 이 잔액이 되었나'에 답할 근거가 이 메모뿐입니다. "
                   "구매 차감을 되돌리는 것은 여기가 아니라 **일별 정산·청구 › 입금완료 취소**입니다.")
        c1, c2 = st.columns([2, 1.4])
        _au = c1.selectbox("판매자", _users, format_func=lambda u: dmap.get(u, u),
                           key="dep_adj_u")
        _aamt = c2.number_input("조정 금액 (+늘림 / −줄임)", step=1000, value=0,
                                key="dep_adj_amt")
        _amemo = st.text_input("조정 사유 (필수)", key="dep_adj_memo")
        if st.button("🛠 조정 적용", key="dep_adj_go"):
            r = _dep.adjust(_au, int(_aamt), _amemo, by=USERNAME)
            if r['ok']:
                st.success(f"✅ {dmap.get(_au, _au)} 조정 — 잔액 {fmt(r['balance'])}원")
                st.rerun()
            else:
                st.error(r['msg'])

    # ── 원장 ──
    st.divider()
    st.subheader("📜 예치금 내역")
    c1, c2, c3 = st.columns([2, 1.2, 1.2])
    _lu = c1.selectbox("판매자", ['(전체)'] + _users,
                       format_func=lambda u: u if u == '(전체)' else dmap.get(u, u),
                       key="dep_lg_u")
    _lf = c2.date_input("시작", value=date.today() - timedelta(days=30), key="dep_lg_f")
    _lt = c3.date_input("끝", value=date.today(), key="dep_lg_t")
    _lg = _dep.ledger(username=None if _lu == '(전체)' else _lu,
                      date_from=str(_lf), date_to=str(_lt))
    if not _lg:
        st.caption("이 기간 내역이 없습니다.")
        return
    st.dataframe(pd.DataFrame([{
        '날짜': r['tx_date'],
        '판매자': dmap.get(r['username'], r['username']),
        '구분': _dep.KIND_LABEL.get(r['kind'], r['kind']),
        '금액': int(r['amount'] or 0),
        '정산일': r['settle_date'] or '',
        '메모': r['memo'] or '',
        '처리자': r['created_by'] or '',
    } for r in _lg]), use_container_width=True, hide_index=True,
        column_config={'금액': st.column_config.NumberColumn('금액', format='%d')})
    st.caption(f"{len(_lg)}건 · 이 기간 증감 "
               f"{fmt(sum(int(r['amount'] or 0) for r in _lg))}원 — "
               f"'차감 취소됨'은 바로 아래 '차감 되돌림'과 짝을 이뤄 서로 상쇄됩니다.")

    # 잘못 올린 예치·조정 치우기 (차감 관련 행은 db_deposit이 막는다)
    _rm = {int(r['id']): (f"{r['tx_date']} · {dmap.get(r['username'], r['username'])}"
                          f" · {_dep.KIND_LABEL.get(r['kind'], r['kind'])}"
                          f" {fmt(int(r['amount'] or 0))}원 · {r['memo'] or ''}")
           for r in _lg if r['kind'] in ('charge', 'adjust')}
    if _rm:
        with st.expander("🗑 잘못 올린 예치·조정 지우기", expanded=False):
            _pick = st.selectbox("지울 내역", list(_rm),
                                 format_func=lambda i: _rm[i], key="dep_del_pick")
            if st.button("🗑 이 내역 삭제", key="dep_del_go"):
                ok, msg = _dep.delete_entry(int(_pick))
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()


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
