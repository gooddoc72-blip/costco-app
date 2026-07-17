"""재고 관리 — 대량구매 공지/요청(사용자) + 추천건·승인·입고·정산(관리자).

수량 단위는 전부 '소분 단위(판매 1개)'. 화면에는 팩 수도 함께 보여준다.
"""
from datetime import datetime

import streamlit as st

from db import (
    get_all_users, get_shared_products, get_all_products,
    create_bulk_deal, get_bulk_deals, get_bulk_deal, set_deal_status, delete_bulk_deal,
    request_bulk_purchase, get_bulk_requests, decide_bulk_request, get_deal_request_summary,
    receive_deal_lots, add_lot, get_inventory_lots, get_stock_summary,
    get_moves, get_cross_settlement_summary, mark_cross_settled,
    get_return_due_lots, get_cross_surcharge,
    NOTICE_LEVELS, create_notice, get_notices, set_notice_active, delete_notice,
)
from utils import fmt

RETURN_DAYS = 30


def _age_badge(days: int) -> str:
    d = int(days or 0)
    if d >= RETURN_DAYS:
        return f"🔴 {d}일"
    if d >= RETURN_DAYS - 5:
        return f"🟠 {d}일"
    return f"🟢 {d}일"


def render(USERNAME, IS_ADMIN, settings):
    st.title("📦 재고 관리")
    sur = get_cross_surcharge()

    if IS_ADMIN:
        tabs = st.tabs(["📢 공지사항", "🏷 할인제품 등록", "✅ 요청 승인·입고",
                        "📊 전체 재고", "💳 정산 장부", "↩️ 반품 대상"])
        with tabs[0]:
            _admin_notices(USERNAME)
        with tabs[1]:
            _admin_deals(USERNAME)
        with tabs[2]:
            _admin_requests(USERNAME)
        with tabs[3]:
            _admin_stock()
        with tabs[4]:
            _admin_settlement(sur)
        with tabs[5]:
            _return_due(None)
    else:
        tabs = st.tabs(["📢 대량구매 공지", "📥 내 요청", "📦 내 재고"])
        with tabs[0]:
            _user_notices(USERNAME)
        with tabs[1]:
            _user_requests(USERNAME)
        with tabs[2]:
            _user_stock(USERNAME, sur)


# ── 관리자: 공지사항 ──────────────────────────────────────
def _admin_notices(USERNAME):
    st.subheader("📢 공지사항")
    st.caption("등록하면 모든 사용자 홈 상단에 바로 뜹니다. 할인제품과는 별개인 일반 알림입니다.")

    with st.form("new_notice", clear_on_submit=True):
        c1, c2, c3 = st.columns([3, 1, 1])
        title = c1.text_input("제목 *", placeholder="7월 정산 일정 안내")
        level = c2.selectbox("중요도", list(NOTICE_LEVELS.keys()),
                             format_func=lambda k: f"{NOTICE_LEVELS[k][0]} {NOTICE_LEVELS[k][1]}")
        pinned = c3.checkbox("상단 고정", value=False)
        body = st.text_area("내용", placeholder="줄바꿈 그대로 표시됩니다.", height=90)
        c4, c5, _ = st.columns([1, 1.3, 1.7])
        ends = c4.date_input("표시 종료일", value=None,
                             help="이 날짜가 지나면 홈에서 자동으로 사라집니다.")
        # 체크박스를 날짜 입력과 같은 높이로 내림 (라벨 높이만큼 여백)
        c5.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        no_end = c5.checkbox("종료일 없음 (계속 표시)", value=True,
                             help="체크하면 날짜를 골라도 무시하고 계속 표시합니다.")
        if st.form_submit_button("📢 공지 등록", type="primary", use_container_width=True):
            if not title.strip():
                st.error("제목을 입력하세요.")
            elif not no_end and not ends:
                st.error("표시 종료일을 고르거나 **종료일 없음**을 체크하세요.")
            else:
                create_notice(title, body, level=level, pinned=pinned,
                              ends_at='' if no_end else ends.strftime("%Y-%m-%d"),
                              created_by=USERNAME)
                st.success("등록 완료 — 사용자 홈에 노출됩니다."
                           + ("" if no_end else f" ({ends.strftime('%Y-%m-%d')}까지)"))
                st.rerun()

    st.divider()
    rows = get_notices(active_only=False, limit=50)
    if not rows:
        st.info("등록된 공지가 없습니다.")
        return
    for n in rows:
        icon, lname = NOTICE_LEVELS.get(n['level'], ('ℹ️', '안내'))
        _live = bool(n['active'])
        _exp = bool(n['ends_at']) and n['ends_at'] < datetime.now().strftime("%Y-%m-%d")
        _tag = "🟢 노출중" if (_live and not _exp) else ("⏰ 기간종료" if _exp else "⚪ 숨김")
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.markdown(f"{icon} **{n['title']}**" + ("  📌" if n['pinned'] else ""))
            if n['body']:
                c1.caption(n['body'][:120] + ("…" if len(n['body']) > 120 else ""))
            c1.caption(f"{_tag} · {n['created_at'][:16]}"
                       + (f" · 종료 {n['ends_at']}" if n['ends_at'] else ""))
            if c2.button("숨김" if _live else "노출", key=f"nt_{n['id']}",
                         use_container_width=True):
                set_notice_active(n['id'], not _live)
                st.rerun()
            if c3.button("🗑 삭제", key=f"nd_{n['id']}", use_container_width=True):
                delete_notice(n['id'])
                st.rerun()


# ── 관리자: 할인제품 등록 ─────────────────────────────────
def _search_products(USERNAME, kw):
    """제품 DB 검색 — 공유 DB 우선, 개인 판매가를 붙여서 반환."""
    kw = (kw or '').strip().lower()
    if not kw:
        return []
    try:
        shared = get_shared_products() or []
    except Exception:
        shared = []
    try:
        mine = get_all_products(USERNAME) or []
    except Exception:
        mine = []
    # 코스트코번호 → 내 판매가 (기존 판매금액 참고용)
    _sale_by_pno = {}
    for p in mine:
        _p = str(p.get('product_no') or '').strip()
        if _p and int(p.get('sale_price') or 0) > 0:
            _sale_by_pno.setdefault(_p, int(p['sale_price']))
    out = []
    for s in shared:
        name = str(s.get('costco_name') or '')
        pno = str(s.get('product_no') or '').strip()
        if not pno:
            continue
        if kw not in name.lower() and kw not in pno.lower():
            continue
        out.append({
            'product_no': pno,
            'name': name,
            'unit_price': int(s.get('unit_price') or 0),
            'split_qty': max(1, int(s.get('split_qty') or 1)),
            'sale_price': _sale_by_pno.get(pno, 0),
        })
        if len(out) >= 50:
            break
    return out


def _admin_deals(USERNAME):
    st.subheader("🏷 할인제품 대량구매 등록")
    st.caption("제품 DB에서 상품을 찾아 등록합니다. 등록하면 모든 사용자 홈에 노출되고 "
               "그 자리에서 구매 요청을 받습니다.")

    kw = st.text_input("🔍 제품 검색 (상품명 또는 코스트코 번호)",
                       key="deal_kw", placeholder="예: 스파클링  /  123456")
    hits = _search_products(USERNAME, kw)
    if kw and not hits:
        st.warning("검색 결과가 없습니다. 제품 DB에 없는 상품이면 먼저 등록하거나 영수증을 올려주세요.")
    if not hits:
        return

    _opts = {f"[{h['product_no']}] {h['name'][:44]}"
             f"  — 매입가 {fmt(h['unit_price'])}원": h for h in hits}
    pick_label = st.selectbox(f"상품 선택 ({len(hits)}건)", list(_opts.keys()), key="deal_pick")
    sel = _opts[pick_label]

    m1, m2, m3 = st.columns(3)
    m1.metric("제품 DB 매입가", f"{fmt(sel['unit_price'])}원")
    m2.metric("내 네이버 판매가", f"{fmt(sel['sale_price'])}원" if sel['sale_price'] else "—")
    m3.metric("소분수", f"÷{sel['split_qty']}" if sel['split_qty'] > 1 else "1 (안 나눔)")

    with st.form("new_deal"):
        c1, c2 = st.columns(2)
        # 기존 판매금액 = 할인 전 가격. 제품 DB 매입가를 기본값으로 채운다.
        normal = c1.number_input("기존 판매금액 (할인 전) *", min_value=0, step=100,
                                 value=int(sel['unit_price']),
                                 help="제품 DB의 현재 매입가를 불러왔습니다. 다르면 고치세요.")
        sale = c2.number_input("할인금액 (행사가) *", min_value=0, step=100,
                               value=0, help="이 가격이 재고 단가가 됩니다.")
        c3, c4 = st.columns(2)
        limit = c3.number_input("총 한도(팩)", min_value=0, step=10, value=0,
                                help="0이면 무제한")
        deadline = c4.date_input("요청 마감일", value=None)
        memo = st.text_input("메모", placeholder="7/25까지 행사가")
        if st.form_submit_button("🏷 할인제품 등록", type="primary", use_container_width=True):
            if int(sale) <= 0:
                st.error("할인금액을 입력하세요.")
            elif int(normal) and int(sale) >= int(normal):
                st.error(f"할인금액({fmt(int(sale))}원)이 기존 판매금액({fmt(int(normal))}원)보다 "
                         "싸야 합니다. 금액을 확인하세요.")
            else:
                did = create_bulk_deal(
                    sel['name'], int(sale), product_no=sel['product_no'],
                    normal_price=int(normal), split_qty=int(sel['split_qty']),
                    total_limit=int(limit),
                    deadline=deadline.strftime("%Y-%m-%d") if deadline else '',
                    memo=memo, created_by=USERNAME)
                if did:
                    _rate = round((1 - int(sale) / int(normal)) * 100) if int(normal) else 0
                    st.success(f"등록 완료 — {_rate}% 할인으로 사용자 홈에 노출됩니다. (#{did})")
                    st.rerun()

    st.divider()
    deals = get_bulk_deals(limit=50)
    if not deals:
        st.info("등록된 추천건이 없습니다.")
        return
    for d in deals:
        s = get_deal_request_summary(d['id'])
        icon = {"OPEN": "🟢", "CLOSED": "⚪", "PURCHASED": "📦"}.get(d['status'], "•")
        with st.expander(
                f"{icon} [{d['status']}] {d['product_name']} · {fmt(int(d['sale_price']))}원"
                f" · 요청 {s['req_total']}팩 / 승인 {s['approved_total']}팩", expanded=False):
            st.caption(f"상품번호 {d['product_no'] or '—'} · 소분 {d['split_qty']} · "
                       f"한도 {d['total_limit'] or '무제한'} · 마감 {d['deadline'] or '—'}")
            if d.get('memo'):
                st.caption(f"메모: {d['memo']}")
            b1, b2, b3 = st.columns(3)
            if d['status'] == 'OPEN' and b1.button("요청 마감", key=f"cl_{d['id']}",
                                                   use_container_width=True):
                set_deal_status(d['id'], 'CLOSED')
                st.rerun()
            if d['status'] == 'CLOSED' and b2.button("↩ 다시 열기", key=f"op_{d['id']}",
                                                     use_container_width=True):
                set_deal_status(d['id'], 'OPEN')
                st.rerun()
            if b3.button("🗑 삭제", key=f"dl_{d['id']}", use_container_width=True):
                if delete_bulk_deal(d['id']):
                    st.rerun()
                else:
                    st.error("이미 입고된 재고가 있어 삭제할 수 없습니다.")


# ── 관리자: 요청 승인 + 입고 ──────────────────────────────
def _admin_requests(USERNAME):
    st.subheader("✅ 대량구매 요청 승인")
    pend = get_bulk_requests(status='PENDING')
    if not pend:
        st.info("대기 중인 요청이 없습니다.")
    for r in pend:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 1.2, 1, 1])
            c1.markdown(f"**{r.get('product_name') or '(삭제된 추천건)'}**")
            c1.caption(f"👤 {r['username']} · 요청 {r['requested_at'][:16]}"
                       + (f" · {r['memo']}" if r.get('memo') else ""))
            qty = c2.number_input("승인 수량(팩)", min_value=0, step=1,
                                  value=int(r['req_qty'] or 0), key=f"aq_{r['id']}")
            if c3.button("승인", key=f"ap_{r['id']}", type="primary",
                         use_container_width=True):
                decide_bulk_request(r['id'], True, int(qty), decided_by=USERNAME)
                st.rerun()
            if c4.button("거절", key=f"rj_{r['id']}", use_container_width=True):
                decide_bulk_request(r['id'], False, decided_by=USERNAME)
                st.rerun()

    st.divider()
    st.subheader("📦 입고 처리")
    st.caption("코스트코에서 실제로 사 온 뒤 누르세요. 승인된 요청이 요청자별 재고로 들어갑니다. "
               "이 시점부터 30일 반품 기한이 계산됩니다.")
    ready = [d for d in get_bulk_deals() if d['status'] in ('OPEN', 'CLOSED')]
    ready = [d for d in ready if get_deal_request_summary(d['id'])['approved_total'] > 0]
    if not ready:
        st.info("입고할 추천건이 없습니다. (승인된 요청이 있어야 합니다)")
        return
    for d in ready:
        s = get_deal_request_summary(d['id'])
        c1, c2, c3 = st.columns([3, 1.2, 1])
        c1.markdown(f"**{d['product_name']}** · 승인 {s['approved_total']}팩")
        rdate = c2.date_input("입고일", value=datetime.now(), key=f"rd_{d['id']}")
        if c3.button("📦 입고", key=f"rc_{d['id']}", type="primary",
                     use_container_width=True):
            n = receive_deal_lots(d['id'], received_at=rdate.strftime("%Y-%m-%d"))
            if n:
                st.success(f"{n}명에게 재고 배정 완료")
                st.rerun()
            else:
                st.warning("이미 입고 처리된 추천건입니다.")


# ── 관리자: 전체 재고 ─────────────────────────────────────
def _admin_stock():
    st.subheader("📊 전체 재고")
    rows = get_stock_summary()
    if not rows:
        st.info("재고가 없습니다.")
    else:
        import pandas as pd
        df = pd.DataFrame([{
            "상품번호": r['product_no'], "상품명": r['product_name'], "보유자": r['owner'],
            "잔여(개)": r['qty_left'], "입고(개)": r['qty_in'],
            "최초입고": r['oldest_at'], "경과": _age_badge(r['age_days']),
        } for r in rows])
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    with st.expander("➕ 재고 직접 입고 (추천건 없이)"):
        with st.form("manual_lot"):
            c1, c2 = st.columns([2, 1])
            name = c1.text_input("상품명")
            pno = c2.text_input("코스트코 상품번호")
            c3, c4, c5, c6 = st.columns(4)
            users = [u['username'] for u in get_all_users() if u.get('approved')]
            owner = c3.selectbox("보유자", users) if users else c3.text_input("보유자")
            cost = c4.number_input("구입가(1팩)", min_value=0, step=100)
            packs = c5.number_input("수량(팩)", min_value=1, step=1, value=1)
            sq = c6.number_input("소분수", min_value=1, step=1, value=1)
            rdate = st.date_input("입고일", value=datetime.now())
            if st.form_submit_button("입고", type="primary"):
                if not (name.strip() and pno.strip() and owner):
                    st.error("상품명·상품번호·보유자를 채우세요.")
                else:
                    add_lot(pno.strip(), name.strip(), owner, int(cost), int(packs),
                            split_qty=int(sq), received_at=rdate.strftime("%Y-%m-%d"))
                    st.success("입고 완료")
                    st.rerun()

    st.divider()
    st.subheader("🔄 최근 차감 내역")
    mv = get_moves(limit=100)
    if not mv:
        st.caption("차감 내역이 없습니다.")
        return
    import pandas as pd
    st.dataframe(pd.DataFrame([{
        "발송일": m['dispatched_at'], "상품번호": m['product_no'],
        "판매자": m['seller'], "재고 보유자": m['owner'],
        "구분": "🔀 타인재고" if m['is_cross'] else "본인재고",
        "수량": m['qty'], "웃돈": fmt(int(m['surcharge'])) if m['surcharge'] else "—",
        "주문번호": m['order_no'],
    } for m in mv]), use_container_width=True, hide_index=True)


# ── 관리자: 500원 정산 장부 ───────────────────────────────
def _admin_settlement(sur):
    st.subheader("💳 타인 재고 판매 정산")
    st.caption(f"판매자에게 받아 재고 보유자에게 줍니다. 금액 = (구입가 + {fmt(sur)}원) × 수량")
    rows = get_cross_settlement_summary('PENDING')
    if not rows:
        st.success("정산할 건이 없습니다.")
        return
    total = sum(int(r['payable'] or 0) for r in rows)
    st.metric("정산 대기 총액", f"{fmt(total)}원")
    for r in rows:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1.4, 1])
            c1.markdown(f"**{r['seller']}** 님이 판매 → **{r['owner']}** 님 재고")
            c1.caption(f"상품번호 {r['product_no']} · {r['qty']}개 "
                       f"(웃돈 {fmt(int(r['surcharge']))}원 포함)")
            c2.markdown(f"### {fmt(int(r['payable']))}원")
            if c3.button("정산완료", key=f"st_{r['owner']}_{r['seller']}_{r['product_no']}",
                         type="primary", use_container_width=True):
                mark_cross_settled(r['owner'], r['seller'], r['product_no'])
                st.rerun()


# ── 반품 대상 ─────────────────────────────────────────────
def _return_due(owner):
    st.subheader(f"↩️ 반품 대상 (입고 {RETURN_DAYS}일 경과)")
    st.caption("코스트코 반품 API는 없습니다. 목록만 알려드리니 매장에서 직접 처리하세요.")
    rows = get_return_due_lots(days=RETURN_DAYS, owner=owner)
    if not rows:
        st.success(f"{RETURN_DAYS}일 넘게 남은 재고가 없습니다.")
        return
    import pandas as pd
    st.warning(f"⚠️ 반품 권장 {len(rows)}건")
    st.dataframe(pd.DataFrame([{
        "상품번호": r['product_no'], "상품명": r['product_name'], "보유자": r['owner'],
        "잔여(개)": r['qty_left'], "입고일": r['received_at'],
        "경과": _age_badge(r['age_days']),
        "묶인 금액": fmt(int(r['unit_cost']) * int(r['qty_left'])) + "원",
    } for r in rows]), use_container_width=True, hide_index=True)


# ── 사용자: 공지 ──────────────────────────────────────────
def _user_notices(USERNAME):
    st.subheader("📢 대량구매 추천")
    deals = get_bulk_deals(status='OPEN')
    if not deals:
        st.info("현재 진행 중인 대량구매 추천이 없습니다.")
        return
    mine = {r['deal_id']: r for r in get_bulk_requests(username=USERNAME)}
    for d in deals:
        s = get_deal_request_summary(d['id'])
        left = (int(d['total_limit']) - int(s['approved_total'])) if d['total_limit'] else None
        with st.container(border=True):
            c1, c2 = st.columns([3, 1.4])
            c1.markdown(f"### {d['product_name']}")
            _disc = ""
            if int(d['normal_price'] or 0) > int(d['sale_price'] or 0) > 0:
                _rate = round((1 - int(d['sale_price']) / int(d['normal_price'])) * 100)
                _disc = f"  ~~{fmt(int(d['normal_price']))}원~~  **{_rate}% ↓**"
            c1.markdown(f"**{fmt(int(d['sale_price']))}원** / 1팩{_disc}")
            _meta = [f"상품번호 {d['product_no'] or '—'}"]
            if int(d['split_qty'] or 1) > 1:
                _meta.append(f"소분 ÷{d['split_qty']}")
            if d['deadline']:
                _meta.append(f"마감 {d['deadline']}")
            if left is not None:
                _meta.append(f"잔여 {max(0, left)}팩")
            c1.caption(" · ".join(_meta))
            if d.get('memo'):
                c1.info(d['memo'])

            got = mine.get(d['id'])
            if got and got['status'] == 'APPROVED':
                c2.success(f"✅ 승인 {got['approved_qty']}팩")
            elif got and got['status'] == 'PENDING':
                c2.warning(f"⏳ 요청 {got['req_qty']}팩 — 승인 대기")
            elif got and got['status'] == 'REJECTED':
                c2.error("거절됨")
            with c2.form(f"req_{d['id']}"):
                q = st.number_input("요청 수량(팩)", min_value=0, step=1,
                                    value=int(got['req_qty']) if got else 0,
                                    key=f"rq_{d['id']}")
                if st.form_submit_button("대량구매 요청", use_container_width=True,
                                         type="primary"):
                    if int(q) <= 0:
                        st.error("수량을 입력하세요.")
                    elif got and got['status'] == 'APPROVED':
                        st.error("이미 승인된 요청은 바꿀 수 없습니다. 관리자에게 문의하세요.")
                    else:
                        request_bulk_purchase(d['id'], USERNAME, int(q))
                        st.success("요청 접수 — 관리자 승인 후 확정됩니다.")
                        st.rerun()


def _user_requests(USERNAME):
    st.subheader("📥 내 요청 내역")
    rows = get_bulk_requests(username=USERNAME)
    if not rows:
        st.info("요청 내역이 없습니다.")
        return
    import pandas as pd
    _label = {"PENDING": "⏳ 승인 대기", "APPROVED": "✅ 승인", "REJECTED": "❌ 거절"}
    st.dataframe(pd.DataFrame([{
        "상품명": r.get('product_name') or '(삭제됨)',
        "요청(팩)": r['req_qty'], "승인(팩)": r['approved_qty'],
        "상태": _label.get(r['status'], r['status']),
        "요청일": (r['requested_at'] or '')[:16],
    } for r in rows]), use_container_width=True, hide_index=True)


# ── 사용자: 내 재고 ───────────────────────────────────────
def _user_stock(USERNAME, sur):
    st.subheader("📦 내 재고")
    rows = get_stock_summary(owner=USERNAME)
    if not rows:
        st.info("보유 재고가 없습니다. 대량구매 공지에서 요청해 보세요.")
    else:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "상품번호": r['product_no'], "상품명": r['product_name'],
            "잔여(개)": r['qty_left'], "입고(개)": r['qty_in'],
            "최초입고": r['oldest_at'], "경과": _age_badge(r['age_days']),
        } for r in rows]), use_container_width=True, hide_index=True)
        st.caption("수량은 판매 1개 기준입니다. 소분 상품이면 1팩이 여러 개로 잡힙니다.")

    st.divider()
    _return_due(USERNAME)

    st.divider()
    st.subheader("💰 다른 판매자에게 나간 내 재고")
    st.caption(f"내 재고로 다른 판매자가 판매하면 구입가 + {fmt(sur)}원(개당)을 정산받습니다. "
               "관리자가 중간에서 정산합니다.")
    mv = [m for m in get_moves(owner=USERNAME, only_cross=True, limit=200)]
    if not mv:
        st.caption("해당 내역이 없습니다.")
    else:
        import pandas as pd
        _pending = sum(int(m['unit_cost']) * int(m['qty']) + int(m['surcharge'])
                       for m in mv if m['settle_status'] == 'PENDING')
        st.metric("정산 대기 금액", f"{fmt(_pending)}원")
        st.dataframe(pd.DataFrame([{
            "발송일": m['dispatched_at'], "판매자": m['seller'],
            "상품번호": m['product_no'], "수량": m['qty'],
            "정산액": fmt(int(m['unit_cost']) * int(m['qty']) + int(m['surcharge'])) + "원",
            "상태": "✅ 정산완료" if m['settle_status'] == 'SETTLED' else "⏳ 대기",
        } for m in mv]), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🛒 내가 타인 재고로 판매한 건")
    st.caption(f"내 재고가 없어 다른 분 재고로 나간 건입니다. 수익계산의 구입가격에 개당 {fmt(sur)}원이 더해집니다.")
    ms = get_moves(seller=USERNAME, only_cross=True, limit=200)
    if not ms:
        st.caption("해당 내역이 없습니다.")
    else:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "발송일": m['dispatched_at'], "재고 보유자": m['owner'],
            "상품번호": m['product_no'], "수량": m['qty'],
            "추가 부담": fmt(int(m['surcharge'])) + "원", "주문번호": m['order_no'],
        } for m in ms]), use_container_width=True, hide_index=True)
