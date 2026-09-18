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
from db import get_all_users, get_receipt_items_by_date
from utils import fmt

_ST_ICON = {'draft': '⚪ 정산완료', 'billed': '🟡 청구됨', 'paid': '🟢 입금완료'}

#: 예치금으로 결제된 청구서에 남기는 표시 — 계좌 입금과 구분하는 유일한 근거는
#  예치금 원장이지만, 청구서만 봐도 알 수 있게 메모에도 같은 말을 남긴다.
_DEP_MEMO = '예치금 차감'


def _ask(context, *, key, settings=None, username='', hint=''):
    """AI 질문 패널 — 화면마다 자기 데이터를 넘겨 부른다.

    한 화면에만 두면 정작 물어보고 싶은 자리(정산·예치금·재고)에서는 못 쓴다.
    """
    try:
        from pages_lib import _ask_ai
        _ask_ai.render(context, key=key, settings=settings, username=username,
                       hint=hint)
    except Exception as _e:
        st.caption(f"AI 질문 패널을 열지 못했습니다: {_e}")


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
        '청구액(물건값)': int(i['total_amount'] or 0),
        '결제': '💳 예치금' if i['username'] in _ded else (
            '🏦 계좌입금' if i['status'] == 'paid' else ''),
        '입금액': int(i['paid_amount'] or 0),
        '예치금잔액': int(_bal.get(i['username'], 0)),
        '청구일시': str(i['billed_at'] or '')[:16],
        '입금일시': str(i['paid_at'] or '')[:16],
    } for i in invs]), use_container_width=True, hide_index=True,
        column_config={k: st.column_config.NumberColumn(k, format='%d')
                       for k in ('청구액(물건값)', '입금액', '예치금잔액')})
    st.caption("청구액은 **물건값만**입니다 — 택배비·포장비는 별도로 청구합니다.")

    _render_items_detail(ds, invs, dmap, ded=_ded, by=USERNAME)

    # ── 청구 전 초기화 ──────────────────────────────────────
    _render_reset_draft(ds, _draft, dmap, USERNAME)

    _ask(  # 이 날짜 정산·청구 상태를 그대로 넘겨 물어본다
        {'화면': '정산·청구 — 일별', '정산일': ds,
         '청구서': [{'판매자': dmap.get(i['username'], i['username']),
                  '상태': _ST_ICON.get(i['status'], i['status']),
                  '청구액': int(i['total_amount'] or 0),
                  '입금액': int(i['paid_amount'] or 0),
                  '품목수': int(i['item_count'] or 0),
                  '예치금결제': i['username'] in _ded,
                  '예치금잔액': int(_bal.get(i['username'], 0))} for i in invs]},
        key=f"sb_day_{ds}", settings=None, username=USERNAME,
        hint="예: 김혜림 청구액이 왜 이 금액인가요?")

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
                    _unpay(ds, _u, _isdep, USERNAME, _ded.get(_u, 0))
                    st.rerun()


def _render_reset_draft(ds, drafts, dmap, USERNAME):
    """🗑 청구 전 정산 초기화 — 아직 사용자에게 안 보낸 것만 지운다.

    잘못 매칭한 채로 정산해 버렸을 때, 지금까지는 그 날짜를 다시 정산해 덮는
    수밖에 없었다. 그런데 이번 배치에 안 붙은 옛 품목은 그대로 남아 금액이
    실제보다 커진다. 통째로 비우고 다시 쌓는 길이 있어야 한다.

    **청구 전(⚪ 정산완료)만 지운다.** 청구까지 간 건을 지우면 사용자가 이미
    받아 본 금액이 말없이 사라진다. 그건 정산·청구가 아니라 '정산 취소'로
    따로 판단할 일이다(영수증 정산 › 정산 이력).
    """
    if not drafts:
        return
    _amt = sum(int(i['total_amount'] or 0) for i in drafts)
    with st.expander(f"🗑 청구 전 정산 초기화 — {len(drafts)}명 · {fmt(_amt)}원",
                     expanded=False):
        st.caption("아직 **청구하지 않은** 정산만 지웁니다. 지우면 그 날짜 품목과 "
                   "청구서가 없어지고, 영수증 정산에서 처음부터 다시 매칭할 수 "
                   "있습니다. 잘못 매칭한 채로 정산했을 때 쓰세요.")
        st.dataframe(pd.DataFrame([{
            '판매자': dmap.get(i['username'], i['username']),
            '품목': int(i['item_count'] or 0),
            '청구액': int(i['total_amount'] or 0),
        } for i in sorted(drafts, key=lambda x: -int(x['total_amount'] or 0))]),
            use_container_width=True, hide_index=True,
            column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                           for _k in ('품목', '청구액')})
        st.warning("🟡 **청구됨**·🟢 **입금완료**인 사용자는 지우지 않습니다 — "
                   "이미 사용자가 받아 본 금액이라 말없이 없앨 수 없습니다. "
                   "그 건까지 되돌리려면 **입금완료 취소** 후 "
                   "**영수증 정산 › 정산 이력**에서 그 날짜를 취소하세요.")
        _ok = st.checkbox(f"{len(drafts)}명 · {fmt(_amt)}원을 지웁니다 — 확인했습니다",
                          key=f"sb_reset_ok_{ds}_{_amt}")
        if st.button(f"🗑 청구 전 {len(drafts)}명 정산 초기화", key=f"sb_reset_{ds}",
                     type="primary", disabled=not _ok):
            _n, _kept = _ds.delete_settlement(ds, only_status=('draft',))
            st.success(f"🗑 {ds} 청구 전 정산 {_n}명분을 지웠습니다 — "
                       "영수증 정산에서 그 날짜를 다시 매칭해 정산하세요."
                       + (f" (입금완료라 남긴 사용자: "
                          f"{', '.join(dmap.get(u, u) for u in _kept)})" if _kept else ""))
            st.rerun()


def _unpay(ds, username, is_dep, by, dep_amt=0):
    """입금완료 취소 — 청구서와 예치금은 **반드시 함께** 되돌린다.

    청구서만 되돌리고 예치금을 그대로 두면 사용자는 쓰지도 않은 돈이 빠진 채로
    남는다. 부르는 곳이 둘이라(아래 '입금완료 취소' 목록, 청구 근거 패널의
    인라인 버튼) 한 곳에 모아 둔다 — 나눠 두면 한쪽만 고쳐져 예치금이 안
    돌아오는 경로가 생긴다.
    """
    _ds.unmark_paid(ds, username)
    if is_dep:
        _r = _dep.undo_deduct(ds, username, by=by)
        st.toast(f"💳 {fmt(int(dep_amt or 0))}원 예치금 반환 "
                 f"(잔액 {fmt(_r['balance'])}원)", icon="↩️")


def _amt_of(invs, username):
    for i in invs:
        if i['username'] == username:
            return int(i['total_amount'] or 0)
    return 0


def _render_items_detail(ds, invs, dmap, ded=None, by=''):
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

        # 영수증 원본의 정가·할인을 붙인다. 청구 근거에 실단가만 있으면
        # "이 금액이 할인 반영된 값인가"에 답할 수 없다 — 숫자만 보고는
        # 17,790이 할인 전인지 후인지 알 길이 없어 같은 질문이 반복됐다.
        _rmap = _receipt_price_map(items)

        _rows = []
        for it in items:
            _ri = _rmap.get((str(it.get('receipt_date') or ''),
                             str(it.get('product_no') or ''))) or {}
            _list = int(_ri.get('정가단가') or 0)
            _paid_unit = int(_ri.get('단가') or 0)
            # 할인은 영수증 줄 전체 금액이라 행마다 그대로 쓰면 안 된다.
            # 팩 하나당 얼마를 덜 냈는지로 환산해야 팩단가와 나란히 읽힌다.
            _dunit = max(0, _list - _paid_unit) if (_list and _paid_unit) else 0
            _rows.append({
                '삭제': False,
                '근거': _ds.SOURCE_LABEL.get(it['source'], it['source']),
                '상품명': str(it['product_name'])[:44],
                '코스트코번호': it['product_no'],
                '수량': int(it['qty'] or 1),
                '정가': _list,
                '할인(팩당)': _dunit,
                '팩단가': int(it['unit_price'] or 0),
                '금액': int(it['amount'] or 0),
                '주문번호': it['order_no'],
                '_id': int(it['id']),
            })
        _cfg = {'삭제': st.column_config.CheckboxColumn('삭제'), '_id': None,
                '정가': st.column_config.NumberColumn(
                    '정가', format='%d', help='영수증에 찍힌 할인 전 단가. '
                                            '0이면 그 날짜 영수증에서 이 상품을 못 찾은 것입니다.'),
                '할인(팩당)': st.column_config.NumberColumn(
                    '할인(팩당)', format='%d',
                    help='정가 − 영수증 실단가. 0이면 그 영수증 줄에 쿠폰이 없었거나 '
                         '판독이 할인을 못 가른 것입니다.'),
                **{k: st.column_config.NumberColumn(k, format='%d')
                   for k in ('팩단가', '금액')}}
        _dis = ['근거', '상품명', '코스트코번호', '수량', '정가', '할인(팩당)',
                '팩단가', '금액', '주문번호', '_id']

        if _paid:
            # 입금완료 건은 금액이 잠긴다 — 받은 돈과 청구액이 달라지면 무엇을
            # 받은 것인지 설명할 수 없다. 되돌리려면 '입금완료 취소'가 먼저다.
            #
            # 취소 버튼은 여기 둔다. 전에는 "위 입금완료 취소를 누르세요"라고만
            # 했는데, 그 목록은 이 패널보다 **아래**에 있는 접힌 expander라
            # 가리키는 자리에 아무것도 없었다. 고치려는 자리에서 바로 눌러야 한다.
            _isdep = _u in (ded or {})
            _cu1, _cu2 = st.columns([3, 1])
            _cu1.info("🟢 입금완료된 건이라 품목을 뺄 수 없습니다. "
                      f"오른쪽 **입금완료 취소**를 누르면 '청구됨'으로 돌아가 "
                      "고칠 수 있습니다."
                      + (" 💳 예치금으로 결제한 건이라 **차감했던 금액도 "
                         "예치금으로 되돌아갑니다.**" if _isdep else ""))
            with _cu2:
                st.write("")
                if st.button("↩️ 입금완료 취소", key=f"sb_det_unpay_{ds}_{_u}",
                             use_container_width=True,
                             help="받은 돈과 청구액이 어긋나지 않게 입금완료를 먼저 "
                                  "풀어야 품목을 뺄 수 있습니다."):
                    _unpay(ds, _u, _isdep, by, (ded or {}).get(_u, 0))
                    # success는 rerun에 씻겨 나간다 — toast는 다시 그려도 남는다
                    st.toast(f"↩️ {dmap.get(_u, _u)} 입금완료 취소 — "
                             "이제 품목을 뺄 수 있습니다.", icon="✅")
                    st.rerun()
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

        _goods_sum = sum(int(i['amount'] or 0) for i in items)
        _inv_amt = int(_inv.get('total_amount') or 0)
        _fee = int(_inv.get('ship_fee') or 0) + int(_inv.get('pack_fee') or 0)
        st.caption(f"물건값 합계 {fmt(_goods_sum)}원 = 청구액 {fmt(_inv_amt)}원 "
                   "— 택배비·포장비는 별도 청구합니다.")

        # 둘이 어긋나면 반드시 말한다. 지금까지는 두 숫자를 나란히 적기만 해서
        # 다른 것을 봐도 그냥 지나쳤다. 입금완료(paid) 뒤에 품목을 더하면
        # recompute_invoice가 금액을 안 올리기 때문에 조용히 벌어진다.
        _diff = _goods_sum + _fee - _inv_amt
        if _diff:
            _msg = (f"⚠️ **물건값 합계와 청구액이 {fmt(abs(_diff))}원 다릅니다.**  ")
            if _paid and _diff > 0:
                _msg += ("입금완료된 뒤에 품목이 추가됐습니다 — 받은 돈과 청구액이 "
                         "달라지면 안 되므로 청구서 금액은 그대로 둡니다. "
                         "예치금으로 결제한 날이면 **차감도 그만큼 덜 됐습니다**. "
                         "고치려면 위 **↩️ 입금완료 취소** → 영수증 정산에서 "
                         "**정산 요청** 다시 → 새 금액으로 청구·차감하세요.")
            else:
                _msg += ("청구서가 품목 합계에서 다시 계산되지 않은 상태입니다. "
                         "영수증 정산에서 이 날짜를 **정산 요청**하면 맞춰집니다.")
            st.warning(_msg)

        # 할인이 반영됐는지 한 줄로 답한다. 열만 보태면 행이 많을 때 다시 못 센다.
        _n_disc = sum(1 for r in _rows if int(r['할인(팩당)']) > 0)
        _n_none = sum(1 for r in _rows if int(r['정가']) and not int(r['할인(팩당)']))
        _n_miss = sum(1 for r in _rows if not int(r['정가']))
        _saved = sum(int(r['할인(팩당)']) * int(r['수량']) for r in _rows)
        if _n_disc:
            st.success(f"💰 할인 반영 **{_n_disc}품목 · {fmt(_saved)}원** "
                       f"(할인 없던 품목 {_n_none}개)")
        elif _n_none:
            st.info("ℹ️ 이 청구에는 **할인이 붙은 품목이 없습니다** — 그 날짜 영수증에 "
                    "쿠폰이 없었거나, 판독이 쿠폰을 품목별로 가르지 못한 것입니다. "
                    "영수증 정산에서 그 날짜를 열어 표의 **할인** 칸과 합계 줄의 "
                    "'− 할인'이 영수증 **쿠폰합계**와 맞는지 확인하세요.")
        if _n_miss:
            st.caption(f"⚠️ {_n_miss}개 품목은 그 날짜 영수증에서 못 찾았습니다 "
                       "(재고 출고·온라인몰·직접청구는 영수증 줄이 없는 것이 정상입니다).")


def _receipt_price_map(items):
    """{(영수증날짜, 코스트코번호): 영수증 줄} — 청구 금액의 출처를 되짚는다.

    settle_item에는 실단가만 남는다(정가·할인은 영수증 쪽에 있다). 날짜를
    행마다 다시 조회하면 같은 날짜를 수십 번 읽으므로 날짜별로 한 번만 읽는다.
    """
    out = {}
    for _rd in {str(it.get('receipt_date') or '') for it in (items or [])}:
        if not _rd:
            continue
        try:
            for _ri in (get_receipt_items_by_date('', _rd) or []):
                out[(_rd, str(_ri.get('상품번호') or ''))] = _ri
        except Exception:
            continue
    return out


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
               "아래 **💰 입금 확인 › ↩️ 입금완료 취소**를 쓰면 예치금도 함께 "
               "돌아갑니다.")

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
    # 잔액만으로는 '얼마를 받았나'에 답할 수 없다 — 많이 받고 많이 쓴 사람과
    # 조금 받고 안 쓴 사람의 잔액이 같다. 받은 돈·쓴 돈을 따로 낸다.
    _sm = _dep.summaries()
    _tt = _dep.totals()

    # ── 잔액 현황 ──
    # 청구 합계도 함께 놓는다. "예치금 − 청구액 = 잔액"이 안 맞는다는 질문이
    # 계속 나오는데, 안 맞는 게 맞다 — 잔액에서 빠지는 것은 **예치금으로 결제한
    # 청구**뿐이고, 계좌입금·미입금 청구는 잔액과 아무 관계가 없다. 숫자를
    # 나란히 놓지 않으면 그 차이가 오류로 읽힌다.
    try:
        _inv = _ds.totals_by_user()
    except Exception:
        _inv = {}

    _rows = [{'판매자': dmap.get(u, u),
              '누적 입금': int((_sm.get(u) or {}).get('charged') or 0),
              '예치금 차감': int((_sm.get(u) or {}).get('spent') or 0),
              # 조정 열이 없으면 '입금 − 차감 = 잔액'이 안 맞는 행이 생기고,
              # 표만 보면 장부가 틀린 것으로 읽힌다(실제로 그 질문이 나왔다).
              '관리자 조정': int((_sm.get(u) or {}).get('adjusted') or 0),
              '잔액': int(_bal.get(u, 0)),
              '청구 합계': int((_inv.get(u) or {}).get('billed') or 0),
              '예치금 외 결제': (int((_inv.get(u) or {}).get('billed') or 0)
                            - int((_sm.get(u) or {}).get('spent') or 0)),
              '최근 입금일': str((_sm.get(u) or {}).get('last_charge') or ''),
              '상태': ('⚠️ 부족(마이너스)' if int(_bal.get(u, 0)) < 0
                       else ('💳 사용 중' if int(_bal.get(u, 0)) > 0 else '— 예치 없음')),
              '_u': u} for u in _users]
    _rows.sort(key=lambda r: r['잔액'])
    _pos = sum(r['잔액'] for r in _rows if r['잔액'] > 0)
    _neg = [r for r in _rows if r['잔액'] < 0]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("예치 입금 총액", f"{fmt(_tt['charged'])}원",
              f"{len([r for r in _rows if r['누적 입금'] > 0])}명 입금",
              delta_color="off")
    m2.metric("구매 차감 누계", f"{fmt(_tt['spent'])}원", delta_color="off")
    m3.metric("예치금 총잔액", f"{fmt(_pos)}원",
              f"{len([r for r in _rows if r['잔액'] > 0])}명")
    m4.metric("마이너스", f"{fmt(sum(r['잔액'] for r in _neg))}원", f"{len(_neg)}명")
    # 잔액은 '입금 − 차감 (± 조정)' 하나로 닫힌다. 되돌린 차감은 짝이 되는
    # spend_void와 상쇄되므로 이 식에 나열하면 안 된다 — 전에 그렇게 적어 두어
    # 화면의 숫자를 그대로 더하면 잔액이 685,970원 부풀어 보였다.
    _eq = f"**{fmt(_tt['charged'])}원** 입금 − **{fmt(_tt['spent'])}원** 차감"
    if _tt['adjusted']:
        _eq += (f" {'+' if _tt['adjusted'] > 0 else '−'} "
                f"관리자 조정 **{fmt(abs(_tt['adjusted']))}원**")
    _eq += f" = 잔액 **{fmt(_tt['net'])}원**"
    st.caption(_eq + f"  (예치 없는 사람 {len([r for r in _rows if r['잔액'] == 0])}명)")
    if _tt.get('refunded'):
        st.caption(f"↩️ 되돌린 차감 **{fmt(_tt['refunded'])}원**은 원래 차감과 짝을 "
                   "이뤄 서로 상쇄되므로 위 계산에 들어가지 않습니다 "
                   "— 내역에는 '차감 취소됨'과 '차감 되돌림' 두 줄로 남습니다.")
    _chk = _tt['charged'] - _tt['spent'] + _tt['adjusted'] - _tt['net']
    if _chk:
        # 식이 안 닫히면 예상 못 한 종류의 행이 있다는 뜻이다. 숨기면 안 된다.
        st.warning(f"⚠️ 위 식이 **{fmt(_chk)}원** 어긋납니다 — 원장에 예상하지 못한 "
                   "종류의 행이 있습니다. 아래 예치금 내역을 확인하세요.")

    if _neg:
        st.warning("⚠️ 잔액이 마이너스인 판매자 — 추가 예치를 받아야 합니다: "
                   + " · ".join(f"**{r['판매자']}** {fmt(-r['잔액'])}원" for r in _neg))

    st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != '_u'} for r in _rows]),
                 use_container_width=True, hide_index=True,
                 column_config={k: st.column_config.NumberColumn(k, format='%d')
                                for k in ('누적 입금', '예치금 차감', '관리자 조정',
                                          '잔액', '청구 합계', '예치금 외 결제')})
    st.info(
        "**잔액 = 누적 입금 − 예치금 차감** (± 되돌림·조정)입니다. "
        "**청구 합계에서 빼는 것이 아닙니다.** "
        "청구했더라도 **계좌로 받았거나 아직 못 받은 건**은 예치금에서 빠지지 "
        "않습니다. 그 몫이 **예치금 외 결제** 열입니다 "
        "— `청구 합계 = 예치금 차감 + 예치금 외 결제`.")

    _recon_user(_rows, dmap)

    _ask({'화면': '정산·청구 — 예치금',
          '합계': {'입금총액': _tt['charged'], '차감누계': _tt['spent'],
                 '관리자조정': _tt['adjusted'], '잔액합계': _tt['net']},
          '사용자별': [{k: v for k, v in r.items() if k != '_u'} for r in _rows]},
         key="sb_dep", settings=None, username=USERNAME,
         hint="예: 고영부 잔액이 왜 이 금액인가요?")

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
    # 기간 합계도 받은 돈·쓴 돈을 갈라서 낸다. 증감 한 줄만으로는
    # "이 달에 얼마 받았나"에 답할 수 없다(입금과 차감이 섞여 상쇄된다).
    _pt = _dep.totals(date_from=str(_lf), date_to=str(_lt),
                      username=None if _lu == '(전체)' else _lu)
    st.markdown(f"📥 이 기간 **예치 입금 {fmt(_pt['charged'])}원** · "
                f"📤 구매 차감 **{fmt(_pt['spent'])}원** · "
                f"증감 **{fmt(_pt['net'])}원**")
    st.caption(f"{len(_lg)}건 — "
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
def _recon_user(rows, dmap):
    """한 판매자의 예치금 ↔ 청구 대조 — 차액이 어느 날에서 왔는지까지 짚는다.

    "예치금 585만인데 청구가 554만이면 잔액이 31만이어야 하는 것 아니냐"는
    질문에 답하는 자리다. 답은 **예치금으로 내지 않은 청구가 섞여 있다**인데,
    그게 어느 날 건인지 못 짚으면 설명이 설명으로 안 들린다.
    """
    _cand = [r for r in rows if r['누적 입금'] or r['청구 합계']]
    if not _cand:
        return
    with st.expander("🧮 예치금 ↔ 청구 대조 — 차액이 어디서 왔나", expanded=False):
        _lbl = [f"{r['판매자']} · 잔액 {fmt(r['잔액'])}원" for r in _cand]
        _p = st.selectbox("판매자", _lbl, key="dep_recon_u")
        _r = _cand[_lbl.index(_p)]
        _u = _r['_u']

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("누적 입금", f"{fmt(_r['누적 입금'])}원")
        c2.metric("예치금 차감", f"{fmt(_r['예치금 차감'])}원")
        c3.metric("관리자 조정", f"{fmt(_r['관리자 조정'])}원", delta_color="off")
        c4.metric("잔액", f"{fmt(_r['잔액'])}원")
        _eq = f"`{fmt(_r['누적 입금'])} − {fmt(_r['예치금 차감'])}"
        if _r['관리자 조정']:
            _eq += (f" {'+' if _r['관리자 조정'] > 0 else '−'} "
                    f"{fmt(abs(_r['관리자 조정']))}")
        _eq += f" = {fmt(_r['잔액'])}`"
        _gap = (_r['누적 입금'] - _r['예치금 차감'] + _r['관리자 조정'] - _r['잔액'])
        st.markdown(_eq + ("  ·  ✅ 잔액과 일치합니다" if not _gap else
                           f"  ·  ⚠️ **{fmt(_gap)}원** 어긋납니다 — 아래 "
                           "**예치금 내역**에서 원인을 확인하세요"))

        st.divider()
        st.markdown(f"**청구 합계 {fmt(_r['청구 합계'])}원** 중 "
                    f"예치금으로 낸 것 **{fmt(_r['예치금 차감'])}원** · "
                    f"그 밖 **{fmt(_r['예치금 외 결제'])}원**")
        if not _r['예치금 외 결제']:
            st.success("청구가 전부 예치금으로 결제됐습니다.")
            return

        # 예치금으로 결제하지 않은 날 — 계좌입금분과 미입금분을 갈라 보여 준다
        _invs = _ds.list_invoices('2000-01-01', '2999-12-31', username=_u)
        try:
            _depd = _dep.spend_dates(_u)      # 날짜마다 조회하면 연결을 그만큼 연다
        except Exception:
            _depd = set()
        _drows = []
        for i in _invs:
            _d = str(i['settle_date'])
            _amt = int(i['total_amount'] or 0)
            if not _amt or _d in _depd:
                continue
            _drows.append({
                '정산일': _d,
                '상태': _ST_ICON.get(i['status'], i['status']),
                '금액': _amt,
                '결제': ('🏦 계좌입금' if i['status'] == 'paid'
                       else ('🔴 미입금' if i['status'] == 'billed' else '⚪ 청구 전')),
                '입금일시': str(i['paid_at'] or '')[:16],
            })
        if not _drows:
            st.caption("예치금 밖 결제 건을 찾지 못했습니다 — 금액 차이는 "
                       "관리자 조정이나 차감 되돌림일 수 있습니다. "
                       "아래 **예치금 내역**을 확인하세요.")
            return
        _drows.sort(key=lambda r: r['정산일'])
        st.dataframe(pd.DataFrame(_drows), use_container_width=True, hide_index=True,
                     column_config={'금액': st.column_config.NumberColumn('금액',
                                                                        format='%d')})
        _acc = sum(r['금액'] for r in _drows if r['결제'] == '🏦 계좌입금')
        _un = sum(r['금액'] for r in _drows if r['결제'] == '🔴 미입금')
        _dr = sum(r['금액'] for r in _drows if r['결제'] == '⚪ 청구 전')
        st.caption(f"🏦 계좌입금 {fmt(_acc)}원 · 🔴 미입금 {fmt(_un)}원 · "
                   f"⚪ 아직 청구 전 {fmt(_dr)}원 — 합계 {fmt(_acc + _un + _dr)}원. "
                   "이 금액들은 예치금 잔액과 무관합니다.")


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
    # 택배 발송건수 — 물건값 청구와 함께 '그달 몇 건 나갔나'를 같이 본다.
    #   택배비 청구(포장 관리 탭)와 **같은 기준**이어야 한다. dispatch_log를 그냥
    #   세면 한 주문의 여러 품목이 각각 잡혀 건수가 부풀고, 코스트코 온라인몰
    #   직배송(관리자가 보내지 않은 건)까지 섞인다. daily_fees가 주문번호 기준으로
    #   중복을 없애고 온라인몰 건을 빼 주므로 그것을 그대로 쓴다.
    import calendar as _cal
    _last = _cal.monthrange(int(_ym[:4]), int(_ym[5:7]))[1]
    _ship = {u: 0 for u in summ}
    _ship_day = {}          # {(사용자, 날짜): 발송건} — 일별 표가 그대로 쓴다
    try:
        import settle_core as _sc
        with st.spinner(f"{_ym} 발송건수를 세는 중..."):
            for _d in range(1, _last + 1):
                _dt = '%s-%02d' % (_ym, _d)
                for _u, _f in (_sc.fees_for_users(list(summ), _dt) or {}).items():
                    _c = int(_f.get('ship_count') or 0)
                    _ship[_u] = _ship.get(_u, 0) + _c
                    if _c:
                        _ship_day[(_u, _dt)] = _c
    except Exception as _e:
        st.caption(f"⚠️ 발송건수 집계 실패: {_e}")

    _order = [u for u, _ in sorted(summ.items(), key=lambda kv: -kv[1]['total'])]
    rows = [{
        '판매자': dmap.get(u, u),
        '청구액(물건값)': e['total'],
        '입금완료': e['paid'], '미입금': e['unpaid'],
        '택배 발송건': _ship.get(u, 0), '정산일수': e['days'],
    } for u, e in sorted(summ.items(), key=lambda kv: -kv[1]['total'])]
    rows.append({'판매자': '— 합계 —',
                 '청구액(물건값)': sum(r['청구액(물건값)'] for r in rows),
                 '입금완료': sum(r['입금완료'] for r in rows),
                 '미입금': sum(r['미입금'] for r in rows),
                 '택배 발송건': sum(r['택배 발송건'] for r in rows),
                 '정산일수': ''})
    # 행을 클릭하면 아래 상세가 그 사람으로 바뀐다. 이름을 표에서 찾아 놓고
    # 아래 선택칸에서 또 고르는 동작이 반복돼 왔다.
    _ev = st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                       on_select="rerun", selection_mode="single-row",
                       key=f"sb_m_tbl_{_ym}",
                       column_config={k: st.column_config.NumberColumn(k, format='%d')
                                      for k in ('청구액(물건값)', '입금완료', '미입금',
                                                '택배 발송건')})
    _clicked = ''
    try:
        _sr = list(_ev.selection.rows)
        if _sr and _sr[0] < len(_order):      # 마지막 '— 합계 —' 행은 사람이 아니다
            _clicked = _order[_sr[0]]
    except Exception:
        _clicked = ''
    st.caption("👆 판매자 행을 클릭하면 아래에 그 사람의 **일별 내역·예치금·품목**이 "
               "펼쳐집니다.")
    st.caption("택배비·포장비는 이 청구에 포함되지 않습니다 — 별도로 청구합니다. "
               "**택배 발송건**은 그달 실제 발송한 주문 수(주문번호 기준)이며, "
               "코스트코 온라인몰 직배송은 빠집니다 — 포장 관리 › 택배·부자재비 "
               "청구의 발송건수와 같은 기준입니다.")

    _month_online(_ym, _last, dmap)
    _month_detail(_ym, _last, summ, dmap, _ship_day, clicked=_clicked)


def _month_online(_ym, _last, dmap):
    """🛒 그달 온라인몰 직배송 — 카드 명세서와 맞춰 보는 자리.

    온라인몰 결제는 카드 한 장으로 여러 판매자 몫을 한꺼번에 한다. 그런데
    시스템에는 '지정한 건'만 남아서, **지정하지 않은 주문은 청구에서 통째로
    빠진 채 아무도 모른다.** 실제로 9월에 두 건(제스프리 32,490 · 치즈피자
    43,990 = 76,480원)이 그렇게 빠져 있었고, 카드 명세서와 맞춰 보고 나서야
    드러났다.

    실제 결제액을 적어 두면 차액을 늘 띄운다 — 빠진 건을 찾으러 날짜를 하나씩
    뒤질 필요가 없다.
    """
    import db_online_purchase as _op
    from db import get_global_setting, set_global_setting

    _from, _to = '%s-01' % _ym, '%s-%02d' % (_ym, _last)
    try:
        _rows = _op.list_range(_from, _to) or []
    except Exception as _e:
        st.caption(f"온라인몰 내역 조회 실패: {_e}")
        return

    _tot = sum(int(r.get('amount') or 0) for r in _rows)
    st.divider()
    st.markdown(f"##### 🛒 온라인몰 직배송 — {_ym}")

    _key = 'online_actual_%s' % _ym
    try:
        _actual = int(float(get_global_setting(_key) or 0))
    except (TypeError, ValueError):
        _actual = 0

    c1, c2, c3 = st.columns(3)
    c1.metric("정산에 잡힌 금액", f"{fmt(_tot)}원", f"{len(_rows)}건")
    _new_actual = c2.number_input("실제 온라인몰 결제액(원)", min_value=0, step=1000,
                                  value=_actual, key=f"sb_onl_act_{_ym}",
                                  help="코스트코 온라인몰 주문내역·카드 명세서의 "
                                       "그달 합계를 적어 두세요. 차액이 늘 보입니다.")
    _diff = int(_new_actual) - _tot
    c3.metric("차액 (실제 − 정산)", f"{fmt(_diff)}원",
              "맞음" if not _diff else ("정산 누락 의심" if _diff > 0 else "정산이 더 큼"),
              delta_color="off")
    if int(_new_actual) != _actual:
        set_global_setting(_key, str(int(_new_actual)))
        st.rerun()

    if _new_actual and _diff > 0:
        st.error(f"⚠️ **{fmt(_diff)}원이 정산에 안 잡혀 있습니다.** 온라인몰로 "
                 "**지정하지 않은 주문**이 있거나, 단가를 실제 결제액보다 낮게 "
                 "넣은 건이 있습니다. 그대로 두면 그 금액이 그대로 손실입니다 — "
                 "영수증 정산에서 그 날짜를 열어 **📮 송장등록 안 된 주문** 또는 "
                 "**📋 영수증에 없는 발송건**에서 온라인몰로 지정하세요.")
    elif _new_actual and _diff < 0:
        st.warning(f"⚠️ 정산이 실제 결제액보다 **{fmt(-_diff)}원 많습니다.** "
                   "단가를 실제보다 높게 넣었거나, 온라인몰이 아닌 건을 "
                   "온라인몰로 지정했을 수 있습니다.")
    elif _new_actual:
        st.success("✅ 실제 결제액과 정산 금액이 일치합니다.")

    if not _rows:
        st.caption("이 달에 온라인몰로 지정된 건이 없습니다.")
        return
    st.dataframe(pd.DataFrame([{
        '정산일': str(r.get('settle_date') or ''),
        '판매자': dmap.get(str(r.get('username') or ''), str(r.get('username') or '')),
        '주문번호': str(r.get('order_no') or ''),
        '수취인': str(r.get('recipient') or ''),
        '상품명': str(r.get('product_name') or '')[:36],
        '수량': int(r.get('qty') or 1),
        '단가': int(r.get('unit_price') or 0),
        '청구액': int(r.get('amount') or 0),
        '메모': str(r.get('memo') or ''),
    } for r in sorted(_rows, key=lambda x: (str(x.get('settle_date') or ''),
                                            str(x.get('username') or '')))]),
        use_container_width=True, hide_index=True,
        column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                       for _k in ('수량', '단가', '청구액')})
    _byd = {}
    for r in _rows:
        _d = str(r.get('settle_date') or '')
        _byd[_d] = _byd.get(_d, 0) + int(r.get('amount') or 0)
    st.caption("날짜별 — " + " · ".join(f"**{_d}** {fmt(_v)}원"
                                      for _d, _v in sorted(_byd.items()))
               + "  ·  이 건들은 택배비·포장비에서 제외됩니다.")


def _month_deposit(_ym, _from, _to, _u, dmap, invs, spend_dates):
    """그 판매자의 그달 **돈 흐름** — 예치 입금 · 예치금 차감 · 계좌입금.

    청구 표와 갈라 두면 "9월에 얼마 냈나"에 답할 수 없다. 낸 방법이 둘이라
    (예치금 차감 / 계좌입금) 한쪽만 봐서는 늘 반쪽이다. 게다가 예치 입금은
    청구와 날짜가 다르다 — 미리 맡기는 돈이라 그달 청구와 대응하지 않는다.
    그래서 같은 화면에 놓되 **표는 따로** 둔다.
    """
    st.markdown("##### 💳 예치금 · 입금")

    try:
        _mt = _dep.totals(date_from=_from, date_to=_to, username=_u)
    except Exception:
        _mt = {'charged': 0, 'spent': 0, 'adjusted': 0, 'refunded': 0, 'net': 0}
    try:
        _bal_now = _dep.balance(_u)
    except Exception:
        _bal_now = 0
    # 계좌입금 = 입금완료인데 예치금 차감이 아닌 날
    _acct = sum(int(i['paid_amount'] or 0) for i in invs
                if i['status'] == 'paid' and str(i['settle_date']) not in spend_dates)

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("이달 예치 입금", f"{fmt(_mt['charged'])}원")
    d2.metric("이달 예치금 차감", f"{fmt(_mt['spent'])}원")
    d3.metric("이달 계좌입금", f"{fmt(_acct)}원")
    d4.metric("현재 잔액", f"{fmt(_bal_now)}원",
              f"이달 증감 {fmt(_mt['net'])}원", delta_color="off")
    st.caption("**현재 잔액은 오늘 기준 전체 잔액**입니다 — 이달 증감만으로 계산되지 "
               "않습니다. 예치금은 미리 맡기는 돈이라 청구와 날짜가 맞지 않습니다.")

    try:
        _lg = _dep.ledger(username=_u, date_from=_from, date_to=_to) or []
    except Exception as _e:
        st.caption(f"예치금 내역 조회 실패: {_e}")
        return
    if not _lg:
        st.caption(f"{_ym}에 예치금 움직임이 없습니다 "
                   + ("— 이달 청구는 계좌입금으로 받았거나 아직 미입금입니다."
                      if _acct or any(i['status'] == 'billed' for i in invs) else ""))
        return
    st.dataframe(pd.DataFrame([{
        '날짜': r['tx_date'],
        '구분': _dep.KIND_LABEL.get(r['kind'], r['kind']),
        '금액': int(r['amount'] or 0),
        '정산일': r['settle_date'] or '',
        '메모': r['memo'] or '',
        '처리자': r['created_by'] or '',
    } for r in sorted(_lg, key=lambda x: (str(x['tx_date']), int(x['id'])))]),
        use_container_width=True, hide_index=True,
        column_config={'금액': st.column_config.NumberColumn('금액', format='%d')})
    st.caption(f"{len(_lg)}건 · 이달 증감 **{fmt(_mt['net'])}원** "
               "— '차감 취소됨'과 '차감 되돌림'은 짝을 이뤄 서로 상쇄됩니다.")


def _month_detail(_ym, _last, summ, dmap, ship_day, clicked=''):
    """월 합계 밑에 **사용자 한 명의 일별 내역**을 편다.

    합계만 있으면 "이 금액이 어디서 나왔나"에 답할 수 없다. 판매자가 금액을
    물어올 때 관리자가 일별 정산·청구 탭에서 날짜를 하나씩 바꿔 가며 세고
    있었다 — 한 달이면 30번이다. 계산서는 월 단위로 끊기므로, 그 한 장의
    근거도 월 단위 화면에서 나와야 한다.

    세 겹으로 보여 준다:
      ① 일별 — 며칠에 얼마가 청구됐고 **어떻게 냈나** (청구서 한 줄 = 하루)
      ② 예치금 — 그달 맡긴 돈과 빠져나간 돈 (청구와 나란히 봐야 말이 된다)
      ③ 품목별 — 그 금액이 어느 상품에서 나왔나 (필요할 때만 편다)

    셋을 갈라 놓으면 판매자 문의 한 건에 화면을 세 번 옮겨야 한다. 물어보는
    말은 늘 하나다 — "9월에 얼마 썼고 얼마 냈나".
    """
    _from, _to = '%s-01' % _ym, '%s-%02d' % (_ym, _last)
    st.divider()
    st.markdown("##### 🔎 사용자별 일별 내역")

    _users = sorted(summ, key=lambda u: -summ[u]['total'])
    _lbl = [f"{dmap.get(u, u)} · {fmt(summ[u]['total'])}원 · {summ[u]['days']}일"
            for u in _users]
    # 위 표에서 행을 클릭하면 선택칸을 그 사람으로 옮긴다. **새로 클릭했을
    # 때만** 옮긴다 — 클릭 상태는 계속 남아 있어서, 매번 덮으면 선택칸으로
    # 다른 사람을 고를 수가 없다.
    _key, _seen = f"sb_m_u_{_ym}", f"_sb_m_click_{_ym}"
    # 달을 바꾸면 옛 라벨이 남는다. 목록에 없는 값이 세션에 있으면 선택칸이
    # 만들어지는 순간 예외가 난다 — 먼저 치운다.
    if _key in st.session_state and st.session_state[_key] not in _lbl:
        st.session_state.pop(_key, None)
    if clicked and clicked in _users and st.session_state.get(_seen) != clicked:
        st.session_state[_seen] = clicked
        st.session_state[_key] = _lbl[_users.index(clicked)]
    _pick = st.selectbox("판매자", _lbl, key=_key)
    _u = _users[_lbl.index(_pick)]

    _invs = _ds.list_invoices(_from, _to, username=_u)
    if not _invs:
        st.caption("이 달에 정산 내역이 없습니다.")
        return

    # 예치금으로 낸 날 — 결제수단을 같이 보여야 "청구는 됐는데 왜 잔액이
    # 안 줄었나"를 이 표 안에서 답할 수 있다.
    try:
        _spd = _dep.spend_dates(_u)
    except Exception:
        _spd = set()

    def _pay_label(i):
        _d = str(i['settle_date'])
        if _d in _spd:
            return '💳 예치금'
        if i['status'] == 'paid':
            return '🏦 계좌입금'
        return '🔴 미입금' if i['status'] == 'billed' else '⚪ 청구 전'

    _rows = [{
        '정산일': str(i['settle_date']),
        '상태': _ST_ICON.get(i['status'], i['status']),
        '결제': _pay_label(i),
        '품목': int(i['item_count'] or 0),
        '청구액(물건값)': int(i['total_amount'] or 0),
        '입금액': int(i['paid_amount'] or 0),
        '택배 발송건': int(ship_day.get((_u, str(i['settle_date'])), 0)),
        '청구일시': str(i['billed_at'] or '')[:16],
        '입금일시': str(i['paid_at'] or '')[:16],
    } for i in sorted(_invs, key=lambda x: str(x['settle_date']))]
    _rows.append({
        '정산일': '— 합계 —', '상태': '', '결제': '',
        '품목': sum(r['품목'] for r in _rows),
        '청구액(물건값)': sum(r['청구액(물건값)'] for r in _rows),
        '입금액': sum(r['입금액'] for r in _rows),
        '택배 발송건': sum(r['택배 발송건'] for r in _rows),
        '청구일시': '', '입금일시': '',
    })
    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True,
                 column_config={k: st.column_config.NumberColumn(k, format='%d')
                                for k in ('품목', '청구액(물건값)', '입금액',
                                          '택배 발송건')})
    _unpaid = sum(int(i['total_amount'] or 0) for i in _invs if i['status'] == 'billed')
    _draft = sum(int(i['total_amount'] or 0) for i in _invs if i['status'] == 'draft')
    _line = f"**{dmap.get(_u, _u)}** · {_ym} · 정산 {len(_invs)}일"
    if _unpaid:
        _line += f"  ·  🔴 미입금 **{fmt(_unpaid)}원**"
    if _draft:
        _line += f"  ·  ⚪ 아직 청구 안 함 **{fmt(_draft)}원**"
    st.markdown(_line)

    _month_deposit(_ym, _from, _to, _u, dmap, _invs, _spd)

    # ── 품목별 — 그 금액이 어느 상품에서 나왔나 ──
    with st.expander(f"📦 {dmap.get(_u, _u)} · {_ym} 품목 내역", expanded=False):
        try:
            _items = _ds.get_items_range(_from, _to, username=_u) or []
        except Exception as _e:
            st.caption(f"품목 조회 실패: {_e}")
            _items = []
        if not _items:
            st.caption("품목 내역이 없습니다 (비용만 청구된 달).")
        else:
            _irows = [{
                '정산일': str(it['settle_date']),
                '근거': _ds.SOURCE_LABEL.get(it['source'], it['source']),
                '상품명': str(it['product_name'])[:40],
                '코스트코번호': str(it['product_no'] or ''),
                '수량': int(it['qty'] or 1),
                '팩단가': int(it['unit_price'] or 0),
                '금액': int(it['amount'] or 0),
                '주문번호': str(it['order_no'] or ''),
            } for it in _items]
            st.dataframe(pd.DataFrame(_irows), use_container_width=True,
                         hide_index=True,
                         column_config={k: st.column_config.NumberColumn(k, format='%d')
                                        for k in ('수량', '팩단가', '금액')})
            # 상품별로도 한 번 접어 본다 — "이 상품을 한 달에 얼마어치 가져갔나"
            _by_p = {}
            for r in _irows:
                e = _by_p.setdefault(r['상품명'], {'수량': 0, '금액': 0})
                e['수량'] += r['수량']
                e['금액'] += r['금액']
            st.caption(f"{len(_irows)}줄 · 합계 {fmt(sum(r['금액'] for r in _irows))}원 "
                       f"· 상품 {len(_by_p)}종")
            if st.checkbox("상품별로 묶어 보기", key=f"sb_m_grp_{_ym}_{_u}"):
                st.dataframe(pd.DataFrame(sorted(
                    [{'상품명': k, '수량': v['수량'], '금액': v['금액']}
                     for k, v in _by_p.items()], key=lambda r: -r['금액'])),
                    use_container_width=True, hide_index=True,
                    column_config={k: st.column_config.NumberColumn(k, format='%d')
                                   for k in ('수량', '금액')})
            try:
                _csv = pd.DataFrame(_irows).to_csv(index=False).encode('utf-8-sig')
                st.download_button("📥 품목 내역 CSV", data=_csv,
                                   file_name=f"정산내역_{_u}_{_ym}.csv",
                                   mime="text/csv", key=f"sb_m_dl_{_ym}_{_u}")
            except Exception:
                pass
