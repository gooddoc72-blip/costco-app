"""🧾 영수증 정산 (관리자) — 코스트코 영수증을 각 사용자 주문에 자동배치하고
각 주문 구입가에 실단가를 반영 + 사용자별 정산표 생성."""
from datetime import date, timedelta

import streamlit as st
import pandas as pd

from services import parse_costco_receipt_pdf
from receipt_settle import allocate_receipt_to_orders, apply_receipt_settlement
from db_receipt_settle import (
    save_settlement_batch, list_settlement_batches, get_settlement_items,
    get_user_settlement_summary, delete_settlement_batch,
)
from db import get_all_users
from utils import fmt

invalidate_data_cache = None


def _set_cache_helpers(shared_fn=None, user_fn=None, merged_fn=None, invalidate_fn=None, **kwargs):
    global invalidate_data_cache
    invalidate_data_cache = invalidate_fn


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    if not IS_ADMIN:
        st.error("관리자 전용 기능입니다.")
        return

    st.header("🧾 영수증 정산 — 사용자 주문 자동배치")
    st.caption(
        "코스트코 영수증 PDF를 올리면 **상품번호로 각 사용자 주문에 배치**하고, "
        "각 주문 구입가에 **영수증 실단가**를 반영합니다. 사용자별 구매금액 정산표도 만들어집니다."
    )

    # ── 1) 영수증 업로드 ──
    files = st.file_uploader(
        "코스트코 영수증 PDF (여러 개 가능)", type=['pdf'],
        key="rs_pdf", accept_multiple_files=True
    )
    if files:
        parsed, fails = [], []
        for f in files:
            items, err = parse_costco_receipt_pdf(f)
            if items:
                parsed.extend(items)
            else:
                fails.append((f.name, err))
        # 상품번호 기준 dedup (최신 영수증 우선)
        merged = {}
        for p in parsed:
            k = _n(p.get('상품번호')) or _n(p.get('상품명'))
            ex = merged.get(k)
            if ex is None or (p.get('receipt_date', '') or '') >= (ex.get('receipt_date', '') or ''):
                merged[k] = p
        deduped = list(merged.values())
        st.session_state['rs_receipt_items'] = deduped
        if fails:
            for fn, em in fails:
                with st.expander(f"⚠️ 인식 실패: {fn}", expanded=False):
                    st.warning(em)

    receipt_items = st.session_state.get('rs_receipt_items') or []
    if not receipt_items:
        st.info("영수증 PDF를 업로드하면 여기에 인식 결과가 표시됩니다.")
        _render_history(_disp_map())
        return

    st.success(f"✅ 영수증 {len(receipt_items)}종 상품 인식")
    st.dataframe(
        pd.DataFrame([{'상품번호': _n(p.get('상품번호')), '상품명': _n(p.get('상품명')),
                       '수량': p.get('수량'), '단가': p.get('단가'),
                       '영수증일': p.get('receipt_date', '')} for p in receipt_items]),
        use_container_width=True, hide_index=True
    )

    # ── 2) 배치 대상 기간 ──
    rdates = sorted({p.get('receipt_date', '') for p in receipt_items if p.get('receipt_date')})
    if rdates:
        try:
            _dmin = date.fromisoformat(rdates[0])
            _dmax = date.fromisoformat(rdates[-1])
        except ValueError:
            _dmin = _dmax = date.today()
    else:
        _dmax = date.today()
        _dmin = _dmax - timedelta(days=14)
    st.divider()
    st.subheader("📅 배치 대상 주문 기간")
    st.caption("이 기간에 결제된(주문일 기준) 모든 사용자 주문 중 영수증 상품번호와 일치하는 건에 배치합니다.")
    c1, c2 = st.columns(2)
    d_from = c1.date_input("시작일", value=_dmin - timedelta(days=3), key="rs_from")
    d_to = c2.date_input("종료일", value=_dmax + timedelta(days=3), key="rs_to")

    if st.button("🔎 자동배치 미리보기", type="primary", key="rs_preview_btn"):
        with st.spinner("모든 사용자 주문을 조회해 배치 중..."):
            alloc = allocate_receipt_to_orders(
                receipt_items, str(d_from), str(d_to)
            )
        st.session_state['rs_alloc'] = alloc

    alloc = st.session_state.get('rs_alloc')
    if not alloc:
        _render_history(_disp_map())
        return

    dmap = _disp_map()
    rows = alloc['rows']
    summary = alloc['user_summary']
    unmatched = alloc['unmatched_receipt']

    st.divider()
    if not rows:
        st.warning(
            "이 기간에 영수증 상품번호와 일치하는 주문이 없습니다. "
            "기간을 넓히거나, 제품 DB에 코스트코 상품번호↔네이버 번호 매핑이 있는지 확인하세요."
        )
    else:
        # ── 3) 사용자별 정산표 ──
        st.subheader("💰 사용자별 정산표")
        srows = [{'사용자': dmap.get(u, u), '품목수': s['count'], '총수량': s['qty'],
                  '구매금액(정산)': fmt(s['amount'])} for u, s in
                 sorted(summary.items(), key=lambda kv: -kv[1]['amount'])]
        st.dataframe(pd.DataFrame(srows), use_container_width=True, hide_index=True)
        _tot = sum(s['amount'] for s in summary.values())
        st.markdown(f"### 합계 구매금액: **{fmt(_tot)}원**  ·  주문 {len(rows)}건  ·  사용자 {len(summary)}명")

        with st.expander(f"🔍 배치 상세 ({len(rows)}건) — 주문별 구입가 반영 내역", expanded=False):
            drows = [{'사용자': dmap.get(r['username'], r['username']),
                      '주문번호': r['order_no'], '주문일': r['order_date'],
                      '상품명': r['product_name'], '수량': r['qty'],
                      '코스트코번호': r['costco_no'], '실단가': fmt(r['unit_price']),
                      '기존구입가': fmt(r['prev_cost']), '→ 새구입가': fmt(r['amount'])}
                     for r in rows]
            st.dataframe(pd.DataFrame(drows), use_container_width=True, hide_index=True)

    if unmatched:
        with st.expander(f"⚠️ 주문을 못 찾은 영수증 품목 {len(unmatched)}건", expanded=False):
            st.caption("해당 상품의 주문이 기간 내 없거나, 제품 DB에 코스트코↔네이버 번호 매핑이 없어 배치 못 함.")
            st.dataframe(pd.DataFrame([{'상품번호': u['상품번호'], '상품명': u['상품명'],
                                        '단가': fmt(u['단가'])} for u in unmatched]),
                         use_container_width=True, hide_index=True)

    # ── 4) 적용 ──
    if rows:
        st.divider()
        st.warning("⚠️ 적용하면 각 주문의 구입가가 영수증 실단가로 **덮어써집니다**. (되돌리려면 정산 이력에서 삭제 후 재수집)")
        if st.button("✅ 정산 적용 (구입가 반영 + 정산표 저장)", type="primary", key="rs_apply_btn"):
            with st.spinner("적용 중..."):
                n = apply_receipt_settlement(rows)
                label = ", ".join(rdates) if rdates else f"{d_from}~{d_to}"
                bid = save_settlement_batch(
                    label=label, date_from=str(d_from), date_to=str(d_to),
                    receipt_dates=",".join(rdates), rows=rows, created_by=USERNAME,
                )
            try:
                if invalidate_data_cache:
                    invalidate_data_cache()
            except Exception:
                pass
            st.session_state.pop('rs_alloc', None)
            st.success(f"✅ 정산 적용 완료 — 주문 {n}건 구입가 반영, 정산 배치 #{bid} 저장. "
                       "각 사용자 수익계산에 즉시 반영됩니다.")
            st.rerun()

    _render_history(dmap)


def _render_history(dmap):
    st.divider()
    st.subheader("📚 정산 이력")
    batches = list_settlement_batches(limit=30)
    if not batches:
        st.caption("아직 저장된 정산 배치가 없습니다.")
        return
    for b in batches:
        with st.expander(
            f"#{b['id']} · {b['label']} · 주문 {b['order_count']}건 · "
            f"총 {fmt(b['total_amount'])}원 · {b['created_at']}",
            expanded=False
        ):
            usum = get_user_settlement_summary(b['id'])
            if usum:
                st.dataframe(pd.DataFrame([
                    {'사용자': dmap.get(u['username'], u['username']),
                     '품목수': u['item_count'], '총수량': u['qty'],
                     '구매금액': fmt(u['amount'])} for u in usum
                ]), use_container_width=True, hide_index=True)
            _c1, _c2 = st.columns([3, 1])
            if _c2.button("🗑 이 배치 삭제", key=f"rs_del_{b['id']}"):
                delete_settlement_batch(b['id'])
                st.rerun()


def _n(s):
    return str(s or '').strip()
