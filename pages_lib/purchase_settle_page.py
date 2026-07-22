"""🧾 구매내역 정산 (관리자) — 사용자별 일별 구매금액(구매가) 집계 · 예상→확정 · 변경 고지.

수익계산과 별개 모듈. 예상(제품/공유DB 구매가) 저장 → 코스트코 영수증 반영(실단가) 후
'확정'하면 예상 대비 상품별 변경금액을 산출하고, 사용자 화면에 배지로 고지된다.
매일=구매가만 / 월말=택배·포장비 추가(예정).
"""
from datetime import date

import streamlit as st
import pandas as pd

from db import get_all_users
from db_purchase_settle import (
    compute_daily_purchase, save_estimate, finalize, get_snapshot, diff_against_snapshot,
    month_fees_if_last_day, is_last_day_of_month,
)
from utils import fmt


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def _sellers():
    return [u['username'] for u in get_all_users() if not u.get('is_admin')]


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    if not IS_ADMIN:
        st.error("관리자 전용 기능입니다.")
        return
    st.header("🧾 구매내역 정산")
    st.caption("각 사용자에게 청구할 **구매금액(구매가)**을 집계합니다. "
               "예상(제품·공유DB 구매가) 저장 → 코스트코 영수증 업로드로 실단가 반영 후 "
               "**확정**하면 예상 대비 변경금액이 사용자 화면에 배지로 표시됩니다.")

    dmap = _disp_map()
    d = st.date_input("정산 날짜 (주문일 기준)", value=date.today())
    ds = str(d)

    _is_last = is_last_day_of_month(ds)
    if _is_last:
        st.info(f"📦 **말일 정산** — 이 날짜 청구액에는 그달(1일~말일) **택배·포장 누적**이 포함됩니다 "
                "(실배정 포장비 + 발송건수×택배비).")

    per_user = {}
    for u in _sellers():
        items, total = compute_daily_purchase(u, ds)
        if not items:
            continue
        snap = get_snapshot(ds, u)
        dd = diff_against_snapshot(ds, u) if snap else None
        fees = month_fees_if_last_day(u, ds) if _is_last else None
        per_user[u] = {'items': items, 'total': total, 'snap': snap, 'diff': dd,
                       'matched': sum(1 for it in items if it['amount'] > 0),
                       'fees': fees, 'charge': total + (fees['fees_total'] if fees else 0)}

    if not per_user:
        st.info(f"{ds} 주문이 없습니다.")
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
            '주문수': len(v['items']),
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
            dd = diff_against_snapshot(ds, u)
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
