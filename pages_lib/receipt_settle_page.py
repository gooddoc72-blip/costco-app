"""🧾 영수증 정산 (관리자) — 코스트코 영수증을 각 사용자 주문에 자동배치하고
각 주문 구입가에 실단가를 반영 + 사용자별 정산표 생성."""
from datetime import date

import streamlit as st
import pandas as pd

from services import parse_costco_receipt_pdf
from receipt_settle import (
    allocate_receipt_to_orders, apply_receipt_settlement, cleanup_orphan_settlements,
    build_manual_rows, ai_match_receipt_orders, _summarize,
)
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

    # ── 1) 영수증 업로드 (선택) — 자동 인식 시도 후 표에 채움 ──
    files = st.file_uploader(
        "코스트코 영수증 PDF (여러 개 가능)", type=['pdf'],
        key="rs_pdf", accept_multiple_files=True
    )
    _fkey = tuple(sorted(f.name for f in files)) if files else ()
    if files and st.session_state.get('_rs_fkey') != _fkey:
        parsed, fails = [], []
        with st.spinner("영수증 인식 중..."):
            for f in files:
                try:
                    items, err = parse_costco_receipt_pdf(f)
                except Exception as e:
                    items, err = None, f"파싱 예외: {e}"
                if items:
                    parsed.extend(items)
                else:
                    fails.append((f.name, err or "인식된 상품 항목이 없습니다"))
        merged = {}
        for p in parsed:
            k = _n(p.get('상품번호')) or _n(p.get('상품명'))
            ex = merged.get(k)
            if ex is None or (p.get('receipt_date', '') or '') >= (ex.get('receipt_date', '') or ''):
                merged[k] = p
        st.session_state['rs_receipt_items'] = list(merged.values())
        st.session_state['_rs_fkey'] = _fkey
        st.session_state['_rs_fails'] = fails
        st.session_state.pop('rs_alloc', None)   # 새 업로드 → 이전 미리보기 초기화
        st.session_state.pop('rs_day', None)     # 새 영수증 → 정산일을 새 영수증 날짜로 재설정

    for fn, em in st.session_state.get('_rs_fails', []):
        st.warning(f"⚠️ 자동 인식 실패: **{fn}** — {em}. 아래 표에 **직접 입력**해서 정산할 수 있습니다.")

    # ── 2) 영수증 품목 (자동 인식 + 직접 추가/수정) ──
    st.subheader("🧾 영수증 품목")
    st.caption("자동 인식되면 표에 채워집니다. 인식이 안 되거나 빠진 게 있으면 **코스트코 상품번호·상품명·단가를 직접 입력**하세요. (행 추가 가능)")
    _seed = st.session_state.get('rs_receipt_items') or []
    _rd_by_cno = {_n(p.get('상품번호')): (p.get('receipt_date', '') or '')
                  for p in _seed if _n(p.get('상품번호'))}
    _seed_rows = [{'상품번호': _n(p.get('상품번호')), '상품명': _n(p.get('상품명')),
                   '수량': int(p.get('수량') or 1), '단가': int(float(p.get('단가') or 0))}
                  for p in _seed] or [{'상품번호': '', '상품명': '', '수량': 1, '단가': 0}]
    edited = st.data_editor(
        pd.DataFrame(_seed_rows), num_rows='dynamic', use_container_width=True,
        key=f"rs_item_editor_{abs(hash(_fkey)) % 100000}",
        column_config={
            '상품번호': st.column_config.TextColumn('코스트코 상품번호'),
            '상품명': st.column_config.TextColumn('상품명'),
            '수량': st.column_config.NumberColumn('수량', min_value=1, step=1),
            '단가': st.column_config.NumberColumn('실단가(원)', min_value=0, step=100),
        },
    )
    receipt_items = []
    for r in edited.to_dict('records'):
        cno = _n(r.get('상품번호'))
        try:
            up = int(float(r.get('단가') or 0))
        except (TypeError, ValueError):
            up = 0
        if cno and up > 0:
            receipt_items.append({'상품번호': cno, '상품명': _n(r.get('상품명')),
                                  '수량': int(r.get('수량') or 1), '단가': up,
                                  'receipt_date': _rd_by_cno.get(cno, '')})
    if not receipt_items:
        st.info("정산하려면 표에 **코스트코 상품번호 + 실단가(>0)** 가 있는 항목이 최소 1개 필요합니다.")
        _render_history(_disp_map())
        return
    st.caption(f"✅ 정산 대상 품목 {len(receipt_items)}종")

    # ── 2) 당일 배치 ── (당일 주문건만 매칭 — 매일 그날 주문에 대해 정산)
    st.divider()
    st.subheader("📅 당일 주문 배치")
    # 영수증에서 인식된 날짜를 기본 정산일로 (영수증일자 ↔ 주문일자 매칭)
    _rdates = sorted({(it.get('receipt_date') or '')[:10]
                      for it in receipt_items if (it.get('receipt_date') or '')})
    _def_day = date.today()
    if _rdates:
        try:
            _def_day = date.fromisoformat(_rdates[-1])
        except Exception:
            _def_day = date.today()
    d_day = st.date_input("정산 날짜 (당일 주문 기준)", value=_def_day, key="rs_day")
    if _rdates:
        st.caption(f"🧾 영수증 인식 날짜: **{', '.join(_rdates)}** → 기본 정산일로 설정됨. "
                   "여러 날짜면 각 날짜별로 나눠 배치하세요.")
    st.caption(f"**{d_day}** 에 결제된(주문일 기준) 모든 판매자 주문 중, 위 영수증 상품번호와 일치하는 건에 배치합니다.")
    d_from = d_to = d_day

    if st.button("🔎 당일 자동배치 미리보기", type="primary", key="rs_preview_btn"):
        with st.spinner("당일 주문을 조회해 배치 중..."):
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
            st.caption("해당 상품의 주문이 당일 없거나, 제품 DB에 코스트코↔네이버 번호 매핑이 없어 배치 못 함.")
            st.dataframe(pd.DataFrame([{'상품번호': u['상품번호'], '상품명': u['상품명'],
                                        '단가': fmt(u['단가'])} for u in unmatched]),
                         use_container_width=True, hide_index=True)

    # ── 3.5) 미매칭 수동/AI 매칭 ──
    _render_match_section(alloc, dmap, settings, USERNAME)

    # ── 4) 적용 ──
    if rows:
        st.divider()
        st.warning("⚠️ 적용하면 각 주문의 구입가가 영수증 실단가로 **덮어써집니다**. (되돌리려면 정산 이력에서 삭제 후 재수집)")
        if st.button("✅ 정산 적용 (구입가 반영 + 정산표 저장)", type="primary", key="rs_apply_btn"):
            with st.spinner("적용 중..."):
                n = apply_receipt_settlement(rows)
                bid = save_settlement_batch(
                    label=f"당일 {d_day}", date_from=str(d_from), date_to=str(d_to),
                    receipt_dates=str(d_day), rows=rows, created_by=USERNAME,
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


def _merge_matches(alloc, new_rows, matched_order_indices):
    alloc['rows'].extend(new_rows)
    idxset = set(matched_order_indices)
    alloc['unmatched_orders'] = [o for i, o in enumerate(alloc.get('unmatched_orders', []))
                                 if i not in idxset]
    matched_costco = {str(r['costco_no']) for r in alloc['rows']}
    alloc['unmatched_receipt'] = [u for u in alloc.get('unmatched_receipt', [])
                                  if str(u['상품번호']) not in matched_costco]
    alloc['user_summary'] = _summarize(alloc['rows'])
    st.session_state['rs_alloc'] = alloc


def _render_match_section(alloc, dmap, settings, USERNAME):
    u_ords = alloc.get('unmatched_orders') or []
    u_rcpt = alloc.get('unmatched_receipt') or []
    if not u_ords or not u_rcpt:
        return
    st.divider()
    st.subheader(f"🔗 미매칭 매칭 — 주문 {len(u_ords)}건 · 영수증 {len(u_rcpt)}종")
    st.caption("자동으로 못 붙은 주문을 영수증 품목과 AI 또는 수동으로 연결합니다.")

    ai_key = (settings.get('anthropic_api_key') if settings else '') or ''
    if st.button("🤖 AI 자동매칭", key="rs_ai_match", disabled=not ai_key,
                 help=None if ai_key else "설정 탭에서 Anthropic API 키를 먼저 등록하세요."):
        with st.spinner("AI가 상품명을 비교해 매칭 중..."):
            pairs, ai_err = ai_match_receipt_orders(u_rcpt, u_ords, ai_key)
        if pairs:
            new = build_manual_rows([
                {'order': u_ords[p['order_index']], 'costco_no': p['costco_no'],
                 'unit_price': p['unit_price'], 'via': 'ai'} for p in pairs])
            _merge_matches(alloc, new, [p['order_index'] for p in pairs])
            st.success(f"🤖 AI가 {len(new)}건 매칭했습니다.")
            st.rerun()
        elif ai_err:
            # 실제 API 오류(크레딧 부족 등)를 그대로 노출 — '못 찾음'으로 오인 방지
            _low = ('credit' in ai_err.lower() or '크레딧' in ai_err or 'balance' in ai_err.lower())
            st.error(f"⚠️ AI 매칭을 실행하지 못했습니다: {ai_err}"
                     + ("\n\n👉 Anthropic 계정의 **크레딧이 소진**됐습니다. Plans & Billing에서 "
                        "크레딧을 충전하면 AI 매칭이 동작합니다. 그동안은 아래 **수동 매칭**을 이용하세요."
                        if _low else "\n\n아래 수동 매칭을 이용하세요."))
        else:
            st.info("AI가 자신 있게 매칭할 항목을 못 찾았습니다. 아래 수동 매칭을 이용하세요.")

    with st.expander("✋ 수동 매칭", expanded=False):
        _ri_opts = {i: f"[{it['상품번호']}] {it['상품명']} ({fmt(it['단가'])}원)"
                    for i, it in enumerate(u_rcpt)}
        ri = st.selectbox("영수증 품목", options=list(_ri_opts),
                          format_func=lambda i: _ri_opts[i], key="rs_mm_ri")
        _oi_opts = {i: f"{dmap.get(o['username'], o['username'])} · {o['recipient']} · "
                       f"{o['product_name'][:24]} ×{o['qty']}"
                    for i, o in enumerate(u_ords)}
        ois = st.multiselect("이 품목에 해당하는 주문 선택", options=list(_oi_opts),
                             format_func=lambda i: _oi_opts[i], key="rs_mm_ois")
        if st.button("➕ 매칭 추가", key="rs_mm_add", disabled=not ois):
            it = u_rcpt[ri]
            new = build_manual_rows([
                {'order': u_ords[i], 'costco_no': it['상품번호'],
                 'unit_price': it['단가'], 'via': 'manual'} for i in ois])
            _merge_matches(alloc, new, list(ois))
            st.success(f"✋ {len(new)}건 매칭 추가")
            st.rerun()


def _render_history(dmap):
    st.divider()
    _h1, _h2 = st.columns([3, 1.3])
    _h1.subheader("📚 정산 이력")
    if _h2.button("🧹 삭제된 주문 정리", key="rs_cleanup_btn",
                  help="사용자가 삭제한(더 이상 존재하지 않는) 주문을 구매 정산 내역에서 일괄 제거합니다."):
        res = cleanup_orphan_settlements()
        if res.get('removed'):
            st.success(f"✅ 삭제된 주문 {res['removed']}건을 구매 정산 내역에서 정리했습니다. "
                       f"(검사 {res['checked']}건)")
        else:
            st.info(f"정리할 항목이 없습니다. (검사 {res.get('checked', 0)}건 — 모두 유효)")
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
