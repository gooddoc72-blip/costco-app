"""재고 관리 — 대량구매 공지/요청(사용자) + 추천건·승인·입고·정산(관리자).

수량 단위는 전부 '소분 단위(판매 1개)'. 화면에는 팩 수도 함께 보여준다.
"""
from datetime import datetime, timedelta

import streamlit as st

import db_customer_return as _cr
from db import (
    get_all_users, get_shared_products, get_all_products,
    search_order_history, resolve_costco_no,
    create_bulk_deal, get_bulk_deals, get_bulk_deal, set_deal_status, delete_bulk_deal,
    request_bulk_purchase, get_bulk_requests, decide_bulk_request, get_deal_request_summary,
    receive_deal_lots, add_lot, get_inventory_lots, get_stock_summary,
    get_moves, get_cross_settlement_summary, mark_cross_settled,
    get_return_due_lots, get_cross_surcharge,
    NOTICE_LEVELS, create_notice, get_notices, set_notice_active, delete_notice,
)
from utils import fmt
# 설정 가이드와 같은 STEP 카드·팁/경고 스타일을 그대로 사용 (일관성)
from pages_lib.guide_page import _step, _tip, _warn, _ok

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
                        "📊 전체 재고", "💳 정산 장부", "↩️ 반품 대상",
                        "📥 고객 반품", "📖 구매 가이드"])
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
        with tabs[6]:
            _customer_returns(USERNAME)
        with tabs[7]:
            _guide(sur, IS_ADMIN=True)
    else:
        tabs = st.tabs(["📢 대량구매 공지", "📥 내 요청", "📦 내 재고", "↩️ 내 반품",
                        "📖 구매 가이드"])
        with tabs[0]:
            _user_notices(USERNAME)
        with tabs[1]:
            _user_requests(USERNAME)
        with tabs[2]:
            _user_stock(USERNAME, sur)
        with tabs[3]:
            _user_returns(USERNAME)
        with tabs[4]:
            _guide(sur, IS_ADMIN=False)


# ── 📖 구매 가이드 ────────────────────────────────────────
def _guide(sur, IS_ADMIN=False):
    st.header("📖 할인제품 구매 안내")
    st.caption("관리자가 코스트코 세일 상품을 찾아 올리면, 판매자들이 수량을 모아 한 번에 삽니다. "
               "혼자 사는 것보다 싸게 들여올 수 있습니다.")

    st.info(
        "**⚡ 전체 흐름**  \n"
        "1️⃣ 관리자가 할인제품 등록  →  2️⃣ 홈에서 수량 요청  →  "
        "3️⃣ 관리자 승인  →  4️⃣ 코스트코에서 일괄 구매·입고  →  "
        "5️⃣ 내 재고로 판매  →  6️⃣ 발송하면 재고 자동 차감"
    )

    st.divider()

    # ── 구매 흐름 ─────────────────────────────────────────
    with st.expander("🛒 할인제품 사는 방법 (5단계)", expanded=True):
        _step(1, "홈 최상단에서 할인제품 확인",
              "로그인하면 화면 맨 위 오른쪽에 <b>🏷 할인제품 구매</b> 칸이 뜹니다. "
              "정상가에 취소선이 그어지고 할인율이 표시됩니다. "
              "마감일과 잔여 수량도 함께 보이니 확인하세요.")
        _step(2, "수량을 넣고 [🛒 구매 요청]",
              "<b>팩 단위</b>로 넣습니다. 소분 상품이어도 <b>코스트코에서 사는 팩 수</b>를 넣으세요. "
              "예: 소분 ÷4 상품을 5팩 요청하면 → 나중에 재고는 20개로 잡힙니다.")
        _step(3, "관리자 승인을 기다립니다",
              "요청 직후에는 <b>⏳ 승인 대기</b>로 표시됩니다. 이때는 수량을 다시 넣어 <b>바꿀 수 있습니다</b>. "
              "관리자가 한도·재고 사정에 따라 수량을 줄여서 승인할 수도 있습니다.")
        _step(4, "관리자가 코스트코에서 사 와서 입고",
              "승인만으로는 재고가 생기지 않습니다. 관리자가 실제로 구매한 뒤 "
              "<b>입고 처리</b>를 해야 <b>📦 내 재고</b>에 잡힙니다. "
              "이 입고일부터 반품 기한 30일이 계산됩니다.")
        _step(5, "평소처럼 팔면 재고가 알아서 빠집니다",
              "따로 할 일이 없습니다. 주문을 <b>발송처리</b>하는 순간 재고가 차감됩니다.")

        _ok("승인된 뒤에는 홈에서 수량을 못 바꿉니다. 관리자가 이미 구매에 들어갔을 수 있기 때문입니다. "
            "바꿔야 하면 관리자에게 문의하세요.")
        _tip("요청은 <b>추천건당 1건</b>입니다. 수량을 다시 넣고 다시 누르면 새 요청이 쌓이는 게 아니라 "
             "<b>기존 요청 수량이 바뀝니다</b>.")

    # ── 재고 차감 ─────────────────────────────────────────
    with st.expander("📦 재고는 언제, 얼마나 빠지나요?", expanded=False):
        st.markdown("##### 차감 시점 — **발송처리할 때**")
        st.markdown(
            "주문이 들어온 시점이 아니라 **송장이 나가고 발송처리가 된 시점**에 빠집니다. "
            "실제로 물건이 나간 때와 재고를 맞추기 위해서입니다. "
            "자동화(CJ 접수 + 일괄 발송처리)로 나간 건도 똑같이 차감됩니다.")

        st.markdown("##### 차감 수량 — **판매 1개 = 재고 1개**")
        st.markdown(
            "재고 수량은 **소분 단위(판매 1개 기준)**로 표시됩니다.\n\n"
            "- 소분 ÷4 상품을 **5팩** 입고 → 재고 **20개**\n"
            "- 그 상품이 **1건** 팔리면 → 재고 **19개**")
        _warn("상품에 **코스트코 상품번호가 없으면 차감되지 않습니다.** "
              "할인제품은 등록할 때 제품 DB에서 찾아 넣으므로 번호가 항상 붙지만, "
              "네이버에 따로 올린 상품이 번호와 연결돼 있지 않으면 재고가 그대로 남습니다. "
              "제품 DB에서 코스트코 번호를 확인하세요.")
        _tip("주문이 취소·반품되면 재고를 되돌려야 합니다. 관리자에게 알려주세요.")

    # ── 500원 규칙 ────────────────────────────────────────
    with st.expander(f"💰 내 재고가 없을 때 — 구입가 +{fmt(sur)}원 규칙", expanded=False):
        st.markdown(
            "여러 판매자가 같은 상품을 나눠 갖고 있어서, **내 재고가 없어도 판매가 가능합니다.** "
            "이때 다른 판매자의 재고가 대신 나갑니다.")

        st.markdown("##### 차감 순서")
        st.markdown(
            "1. **내 재고 먼저** — 있으면 여기서 빠집니다. 추가 비용 없음\n"
            "2. **없으면 다른 보유자** — 가장 **오래된 입고분**부터 빠집니다")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**내가 남의 재고를 썼다면**")
            st.markdown(
                f"- 내 **구입가격에 개당 {fmt(sur)}원**이 더해집니다\n"
                f"- 수익 계산에 자동 반영됩니다\n"
                f"- **📦 내 재고** 탭 아래에서 확인")
        with c2:
            st.markdown(f"**내 재고를 남이 썼다면**")
            st.markdown(
                f"- **구입가 + {fmt(sur)}원(개당)**을 정산받습니다\n"
                f"- 관리자가 중간에서 정산합니다\n"
                f"- **📦 내 재고** 탭에서 대기 금액 확인")

        _ok(f"예시 — 구입가 2,500원짜리를 남의 재고로 5개 팔았다면: "
            f"보유자는 (2,500 + {fmt(sur)}) × 5 = **{fmt((2500 + sur) * 5)}원**을 받습니다. "
            f"판매한 사람은 구입가격이 {fmt(sur * 5)}원 늘어납니다.")
        _tip("내 재고로만 팔면 이 웃돈이 붙지 않습니다. 많이 팔 상품은 넉넉히 요청해 두세요.")

    # ── 반품 ──────────────────────────────────────────────
    with st.expander(f"↩️ 안 팔리면? — 입고 {RETURN_DAYS}일 반품 안내", expanded=False):
        st.markdown(
            f"입고 후 **{RETURN_DAYS}일이 지나도 남아 있는 재고**는 반품 대상으로 잡힙니다. "
            f"매일 아침 **카카오톡으로 목록이 옵니다.** (보유자 본인 것만)")
        _warn("**자동으로 반품되지 않습니다.** 코스트코에는 반품 API가 없어서 "
              "시스템은 목록만 알려드립니다. **직접 매장에 가서 처리**하셔야 합니다.")
        _tip(f"**📦 내 재고** 탭의 경과일 배지로 미리 확인하세요. "
             f"🟢 여유 · 🟠 {RETURN_DAYS - 5}일 이상 · 🔴 {RETURN_DAYS}일 경과")

    # ── 관리자 전용 ───────────────────────────────────────
    if IS_ADMIN:
        st.divider()
        st.subheader("👑 관리자 — 등록부터 정산까지")
        with st.expander("🏷 할인제품 등록·승인·입고 (4단계)", expanded=True):
            _step(1, "제품 DB에서 상품을 찾아 등록",
                  "<b>🏷 할인제품 등록</b> 탭에서 상품명이나 코스트코 번호로 검색합니다. "
                  "고르면 <b>기존 판매금액</b>이 제품 DB 매입가로 자동으로 채워집니다. "
                  "<b>할인금액</b>만 넣으면 됩니다. 상품번호·소분수도 같이 딸려옵니다.")
            _step(2, "요청을 승인",
                  "<b>✅ 요청 승인·입고</b> 탭에 요청이 쌓입니다. "
                  "수량을 조정해서 승인할 수 있습니다 (요청 20팩 → 승인 15팩).")
            _step(3, "코스트코에서 사 온 뒤 [📦 입고]",
                  "승인 수량대로 요청자별 재고가 만들어집니다. "
                  "<b>입고일부터 반품 30일이 계산</b>되니 실제 구매일을 넣으세요.")
            _step(4, "500원 정산",
                  "<b>💳 정산 장부</b> 탭에서 판매자에게 받아 보유자에게 줄 금액이 정리됩니다. "
                  "지급 후 [정산완료]를 누르세요.")
            _warn("**상품번호가 없으면 판매해도 재고가 차감되지 않습니다.** "
                  "그래서 등록은 반드시 제품 DB 검색으로 하도록 막아 뒀습니다.")
            _tip("입고는 추천건당 <b>1회만</b> 됩니다. 재입고가 필요하면 "
                 "<b>📊 전체 재고 > 재고 직접 입고</b>를 쓰세요.")

        with st.expander("⚠️ 등록 전에 확인할 것 — 묶음 상품", expanded=False):
            st.markdown(
                "상품명의 **\"x N개\"** 는 상품마다 뜻이 다릅니다.\n\n"
                "- `신라면 120g x 30개` → 30개들이 **한 박스** (내용물 설명)\n"
                "- `그릭요거트 907g x 2개` → **2개를 함께** 판매")
            _warn("이걸 구분하지 못하면 구입가격이 몇십 배로 부풀거나 재고가 과도하게 깎입니다. "
                  "**제품 DB** 상단의 <b>🔢 묶음 상품 분류 필요</b> 목록에서 먼저 지정하세요. "
                  "지정 전까지는 기존 계산이 그대로 유지됩니다.")

    # ── FAQ ───────────────────────────────────────────────
    st.divider()
    with st.expander("❓ 자주 묻는 질문", expanded=False):
        _faq = [
            ("요청했는데 재고에 안 보여요",
             "승인만으로는 재고가 생기지 않습니다. 관리자가 실제로 코스트코에서 사 온 뒤 "
             "**입고 처리**를 해야 잡힙니다. **📥 내 요청** 탭에서 상태를 확인하세요."),
            ("수량을 잘못 넣었어요",
             "**⏳ 승인 대기** 상태면 홈에서 수량을 다시 넣고 요청하면 덮어써집니다. "
             "**✅ 승인** 뒤에는 관리자에게 문의하세요."),
            ("재고 수량이 요청한 것보다 많아요",
             "소분 상품입니다. 5팩을 요청하고 소분이 ÷4면 재고는 20개로 잡힙니다. "
             "**판매 1개 = 재고 1개** 기준이라 그렇습니다."),
            ("팔았는데 재고가 그대로예요",
             "① 아직 **발송처리**를 안 했거나, ② 그 상품에 **코스트코 상품번호가 연결돼 있지 않은** 경우입니다. "
             "제품 DB에서 번호를 확인하세요."),
            (f"수익 계산의 구입가격이 {fmt(sur)}원씩 높아요",
             "내 재고가 없어서 **다른 판매자 재고로 나간 건**입니다. 실제 원가가 그만큼 높은 게 맞습니다. "
             "**📦 내 재고 > 내가 타인 재고로 판매한 건**에서 확인할 수 있습니다."),
            ("30일 지난 재고는 자동으로 반품되나요",
             "아닙니다. 코스트코 반품 API가 없어 **목록만 알려드립니다.** 직접 매장에서 처리하세요."),
            ("주문이 취소됐는데 재고가 안 돌아와요",
             "발송처리 후 취소·반품된 건은 관리자가 되돌려야 합니다. 관리자에게 알려주세요."),
        ]
        for q, a in _faq:
            st.markdown(f"**Q. {q}**")
            st.markdown(f'<div style="color:#444;font-size:13px;margin:-6px 0 12px 0">{a}</div>',
                        unsafe_allow_html=True)


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


# ── 관리자: 직접구매자 재고·영수증 ────────────────────────────
def _admin_self_purchase_view():
    """직접구매자 한 사람의 재고와 그 사람이 직접 올린 영수증을 본다.

    전체 재고 표에도 섞여 있지만, 대리구매를 할지 판단하려면 '이 사람이 지금
    무엇을 얼마나 갖고 있나'를 한 사람 단위로 봐야 한다. 직접구매자 영수증은
    관리자 영수증과 따로 저장되므로(db_self_receipt) 영수증 정산 화면에는 안 보인다
    — 여기서만 볼 수 있다.
    """
    import pandas as pd
    try:
        import receipt_settle as _rs
        _sp = [u['username'] for u in get_all_users()
               if not u.get('is_admin') and _rs.is_self_purchase(u['username'])]
    except Exception:
        _sp = []
    if not _sp:
        return
    _nm = _name_map()
    with st.expander(f"🏪 직접구매자 재고·영수증 ({len(_sp)}명)", expanded=False):
        _u = st.selectbox("직접구매자", _sp, format_func=lambda u: _nm.get(u, u),
                          key="inv_sp_user")
        _rows = get_stock_summary(owner=_u) or []
        if _rows:
            st.dataframe(pd.DataFrame([{
                "상품번호": r['product_no'], "상품명": r['product_name'],
                "잔여(개)": r['qty_left'], "입고(개)": r['qty_in'],
                "구입가": int(r.get('unit_cost') or 0),
                "재고금액": int(r.get('unit_cost') or 0) * int(r['qty_left'] or 0),
                "최초입고": r['oldest_at'], "경과": _age_badge(r['age_days']),
            } for r in _rows]), use_container_width=True, hide_index=True,
                column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                               for _k in ("구입가", "재고금액")})
            st.caption(f"재고 {len(_rows)}종 · 재고금액 "
                       f"{sum(int(r.get('unit_cost') or 0) * int(r['qty_left'] or 0) for r in _rows):,}원")
        else:
            st.caption("보유 재고가 없습니다.")

        try:
            import db_self_receipt as _sr
            _ds = _sr.dates(_u, limit=30)
        except Exception as _e:
            _ds = []
            st.caption(f"⚠️ 영수증 조회 실패: {_e}")
        if not _ds:
            st.caption("직접 올린 영수증이 없습니다.")
            return
        _opts = [f"{d} ({c}종)" for d, c in _ds]
        _pick = st.selectbox("직접 올린 영수증", _opts, key=f"inv_sp_rcpt_{_u}")
        _items = _sr.items_by_date(_u, _ds[_opts.index(_pick)][0])
        st.dataframe(pd.DataFrame([{
            "상품번호": i['상품번호'], "상품명": i['상품명'], "수량": i['수량'],
            "정가": i['정가단가'], "할인": i['할인'], "실단가": i['단가'],
            "금액": i['단가'] * i['수량']} for i in _items]),
            use_container_width=True, hide_index=True,
            column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                           for _k in ("정가", "할인", "실단가", "금액")})
        st.caption(f"실지불 합계 {sum(i['단가'] * i['수량'] for i in _items):,}원 — "
                   "직접구매자 본인 돈으로 산 것이라 청구 대상이 아닙니다. "
                   "관리자가 대신 산 것(대리구매)은 **영수증 정산**에서 관리자 영수증으로 "
                   "매칭하면 그 행만 청구됩니다.")


# ── 관리자: 전체 재고 ─────────────────────────────────────
def _admin_stock():
    st.subheader("📊 전체 재고")
    rows = get_stock_summary()
    if not rows:
        st.info("재고가 없습니다.")
    else:
        import pandas as pd
        _nm3 = _name_map()
        df = pd.DataFrame([{
            "상품번호": r['product_no'], "상품명": r['product_name'],
            "보유자": _nm3.get(r['owner'], r['owner']),
            "잔여(개)": r['qty_left'], "입고(개)": r['qty_in'],
            "정가": int(r.get('list_price') or 0),
            "구입가": int(r.get('unit_cost') or 0),
            "할인": max(0, int(r.get('list_price') or 0) - int(r.get('unit_cost') or 0)),
            "재고금액": int(r.get('unit_cost') or 0) * int(r['qty_left'] or 0),
            "최초입고": r['oldest_at'], "경과": _age_badge(r['age_days']),
        } for r in rows])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                                    for _k in ("정가", "구입가", "할인", "재고금액")})
        st.caption("정가는 할인 전 영수증 단가, 구입가는 실제 지불 단가입니다 "
                   "(판매 1개 기준). 이 기능 이전 입고분은 정가가 0입니다.")

    _admin_self_purchase_view()

    st.divider()
    # 폼을 쓰지 않는다 — 폼 안에서 엔터를 치면 '입고'가 눌려 검증 오류와 함께
    # 처음으로 돌아갔다. 접기 박스도 rerun마다 닫혀 토글(세션 유지)로 연다.
    _ml_msg = st.session_state.pop('_ml_msg', None)
    if _ml_msg:
        st.success(_ml_msg)
    if st.toggle("➕ 재고 직접 입고 (추천건 없이)", key="inv_manual_open"):
        with st.container(border=True):
            # 🔍 상품명·번호로 공용 DB를 찾아 고르면 번호·매장가가 채워진다
            _q = st.text_input("🔍 상품명·상품번호로 찾기", key="ml_q",
                               placeholder="예: 팬틴 / 메이플시럽 / 854362 — 입력 후 엔터")
            # 공용 DB(크롤링 카탈로그)만 보면 매장 전용·미수집 상품은 안 나온다 —
            # 영수증 구매이력(실제로 산 것)을 함께 찾고, 같은 번호면 하나로 합친다.
            _hits = _manual_lot_search(_q) if _q.strip() else []
            if _q.strip() and not _hits:
                st.caption("공용 DB·영수증 구매이력에서 찾지 못했습니다 — 더 짧게 넣거나 "
                           "아래에 직접 입력하세요.")
            _sel = None
            if _hits:
                _lbls = ["(직접 입력)"] + [
                    f"{h['product_no']} · {h['name'][:40]} · "
                    + (f"🧾 최근구매 {h['receipt_date']} {fmt(h['paid'])}원"
                       if h['paid'] else f"매장가 {fmt(h['store'])}원")
                    for h in _hits]
                _pk = st.selectbox(f"찾은 상품 {len(_hits)}개 (🧾 = 영수증 구매이력)",
                                   _lbls, index=1, key="ml_pick")
                if _pk != "(직접 입력)":
                    _sel = _hits[_lbls.index(_pk) - 1]
            # 고른 상품이 바뀌면 key가 바뀌어 기본값(번호·이름·가격)이 새로 채워진다
            _sk = str((_sel or {}).get('product_no') or 'manual')
            # 구입가 = 최근 영수증 실결제가 우선, 없으면 매장가
            _sp = int((_sel or {}).get('paid') or (_sel or {}).get('store') or 0)
            _lp0 = int((_sel or {}).get('list') or _sp)
            if _sel is not None:
                _sel = dict(_sel, costco_name=_sel['name'])
            c1, c2 = st.columns([2, 1])
            name = c1.text_input("상품명", value=str((_sel or {}).get('costco_name') or ''),
                                 key=f"ml_name_{_sk}")
            pno = c2.text_input("코스트코 상품번호", value=str((_sel or {}).get('product_no') or ''),
                                key=f"ml_pno_{_sk}")
            c3, c4, c5, c6, c7 = st.columns(5)
            users = [u['username'] for u in get_all_users() if u.get('status', 'active') == 'active']
            owner = (c3.selectbox("보유자", users, key="ml_owner") if users
                     else c3.text_input("보유자", key="ml_owner_t"))
            cost = c4.number_input("구입가(1팩)", min_value=0, step=100, value=_sp,
                                   key=f"ml_cost_{_sk}",
                                   help="상품을 고르면 매장가가 들어갑니다. 실제 결제가로 고치세요.")
            lprice = c5.number_input("정가(1팩)", min_value=0, step=100, value=_lp0,
                                     key=f"ml_lp_{_sk}",
                                     help="할인 전 금액. 0이면 구입가와 같게 봅니다.")
            packs = c6.number_input("수량(팩)", min_value=1, step=1, value=1, key="ml_packs")
            sq = c7.number_input("소분수", min_value=1, step=1, value=1, key="ml_sq")
            rdate = st.date_input("입고일", value=datetime.now(), key="ml_date")
            if st.button("입고", type="primary", key="ml_add"):
                if not (name.strip() and pno.strip() and owner):
                    st.error("상품명·상품번호·보유자를 채우세요.")
                else:
                    add_lot(pno.strip(), name.strip(), owner, int(cost), int(packs),
                            split_qty=int(sq), received_at=rdate.strftime("%Y-%m-%d"),
                            list_price=int(lprice or cost))
                    st.session_state['_ml_msg'] = (f"✅ 입고 완료 — {name.strip()[:30]} "
                                                   f"{int(packs)}팩 → {owner}")
                    st.rerun()

    # ── 잘못 들어간 재고를 고치는 두 길 ──────────────────────
    #   재고를 보는 화면과 고치는 화면이 갈라져 있어, 틀린 걸 발견해도 영수증
    #   정산으로 건너가야 했다. 보는 자리에서 고칠 수 있어야 한다.
    #   구현은 영수증 정산과 공용(receipt_settle_page) — 한쪽만 고쳐지는 일을 막는다.
    st.divider()
    st.subheader("🛠 재고 고치기")
    st.caption("**수량만 틀렸다면 수동 입출고**로 차액만 ± 하세요(사유 필수). "
               "**입고 자체를 잘못 넣었다면 입고 취소**로 통째로 지웁니다 — 단 "
               "이미 판매에 쓰인 입고는 지울 수 없습니다(누구 재고에서 나갔는지와 "
               "웃돈 근거를 잃습니다). 그런 건은 수동 입출고로 맞추세요.")
    try:
        from pages_lib import receipt_settle_page as _rsp
        _rsp._render_manual_stock(rows, key_prefix="inv")
        _rsp._render_lot_undo(key_prefix="inv")
    except Exception as _e:
        st.caption(f"재고 수정 화면을 열지 못했습니다: {_e}")

    # 재고 숫자가 왜 이런지 묻는 자리 — 화면에 뜬 값만 넘긴다
    try:
        from pages_lib import _ask_ai
        _ask_ai.render(
            {'화면': '재고 관리 — 전체 재고',
             '재고': [{'상품번호': r['product_no'], '상품명': r['product_name'],
                     '보유자': r['owner'], '잔여': r['qty_left'], '입고': r['qty_in'],
                     '구입가': int(r.get('unit_cost') or 0),
                     '최초입고': r['oldest_at'], '경과일': r['age_days']}
                    for r in (rows or [])[:80]]},
            key="inv_stock", username='',
            hint="예: 이 상품 재고가 왜 안 줄어드나요?")
    except Exception as _e:
        st.caption(f"AI 질문 패널을 열지 못했습니다: {_e}")

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
    import pandas as pd
    from db_inventory import get_cross_moves
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
            # 합계만으로는 '어느 주문이 얼마'인지 모른다 — 펼치면 차감 건별 내역
            _mv = get_cross_moves(r['owner'], r['seller'], r['product_no'])
            _nm = next((m['product_name'] for m in _mv if m.get('product_name')), '')
            with st.expander(f"🔍 내역 보기 — {_nm[:30] or r['product_no']} · {len(_mv)}건",
                             expanded=False):
                if not _mv:
                    st.caption("내역이 없습니다.")
                else:
                    st.dataframe(pd.DataFrame([{
                        "발송일": str(m['dispatched_at'] or '')[:16],
                        "주문번호": m['order_no'],
                        "상품명": str(m.get('product_name') or '')[:34],
                        "수량": int(m['qty'] or 0),
                        "구입가(개당)": int(m['unit_cost'] or 0),
                        "웃돈": int(m['surcharge'] or 0),
                        "금액": int(m['unit_cost'] or 0) * int(m['qty'] or 0)
                                + int(m['surcharge'] or 0),
                        "재고 입고일": str(m.get('lot_received_at') or '')[:10],
                        "판매처": m.get('platform') or '',
                    } for m in _mv]), use_container_width=True, hide_index=True,
                        column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                                       for _k in ("수량", "구입가(개당)", "웃돈", "금액")})
                    st.caption(f"금액 = 구입가 × 수량 + 웃돈 · 합계 "
                               f"**{fmt(sum(int(m['unit_cost'] or 0) * int(m['qty'] or 0) + int(m['surcharge'] or 0) for m in _mv))}원**")


def _name_key(s):
    """상품명 비교용 정규화 — 띄어쓰기·기호를 지우고 소문자로.

    영수증 축약어('KS메이플시럽1L')와 네이버 상품명('커클랜드 시그니처 메이플
    시럽 1L 유기농')은 띄어쓰기와 기호가 달라 LIKE 한 방으로는 절대 안 맞는다.
    """
    import re as _re
    return _re.sub(r'[^0-9a-z가-힣]', '', str(s or '').lower())


def _name_tokens(s):
    """검색어에서 **반드시 들어 있어야 할** 토막만 뽑는다.

    'KS메이플시럽1L' → ['메이플시럽']

    한글·영문·숫자 덩어리로 자른 뒤 세 글자 이상만 남긴다. 'KS'·'1L' 같은
    짧은 토막을 필수로 걸면 안 된다 — 영수증 축약어의 브랜드 머리글자(KS,
    CJ)와 용량 표기는 네이버 상품명에 없는 경우가 더 많아서, 요구하는 순간
    진짜 그 상품까지 걸러진다('KS메이플시럽1L'로 '커클랜드 시그니처 유기농
    메이플 시럽 1L'을 못 찾는다).

    세 글자 이상이 하나도 없으면(예: '우유') 두 글자까지 받아 준다.
    """
    import re as _re
    _t = _re.findall(r'[0-9]+|[a-z]+|[가-힣]+', str(s or '').lower())
    _long = [x for x in _t if len(x) >= 3]
    return _long or [x for x in _t if len(x) >= 2]


def _search_orders(usernames, keyword, product_name, date_from, date_to, limit=3000):
    """여러 판매자의 주문을 한 번에 훑는다 — 반환 행에 '_u'(판매자)를 붙인다.

    고객 반품은 상자만 와서 **어느 판매자 주문인지 모르는 채로** 시작한다.
    판매자를 먼저 고르게 하면 틀렸을 때 "주문이 없다"만 나오고, 맞는 사람을
    찾을 때까지 계정을 하나씩 바꿔 봐야 한다.

    상품명은 SQL LIKE에 맡기지 않는다. 축약어로 넣으면 한 글자도 안 맞기
    때문이다('메이플시럽'은 '메이플 시럽'과 LIKE로 안 맞는다) — 기간 안의
    주문을 받아 와 정규화한 뒤 토막이 전부 들어 있는지로 판단한다.
    그래서 limit을 넉넉히 둔다. 여기서 잘리면 찾는 주문이 조용히 빠진다.
    """
    _q = _name_key(product_name)
    _tok = _name_tokens(product_name)
    out = []
    for _u in (usernames or []):
        try:
            _rows = search_order_history(_u, keyword=keyword or '',
                                         date_from=date_from, date_to=date_to,
                                         limit=limit) or []
        except Exception:
            continue
        for _r in _rows:
            if _q:
                _k = _name_key(_r.get('product_name'))
                if _q not in _k and not all(t in _k for t in _tok):
                    continue
            _r = dict(_r)
            _r['_u'] = _u
            out.append(_r)
    out.sort(key=lambda r: str(r.get('order_date') or ''), reverse=True)
    return out[:200]


#: 영수증·카탈로그가 한쪽은 한글, 한쪽은 영문으로 적는 브랜드 — 서로 바꿔서도 찾는다
_BRAND_ALIAS = {
    '팬틴': 'pantene', '커클랜드': 'kirkland', '스타벅스': 'starbucks', '다우니': 'downy',
    '타이드': 'tide', '바운티': 'bounty', '세타필': 'cetaphil', '뉴트로지나': 'neutrogena',
    '필립스': 'philips', '브라운': 'braun', '다이슨': 'dyson', '페리에': 'perrier',
    '에비앙': 'evian', '고디바': 'godiva', '헤드앤숄더': 'headshoulders', '오랄비': 'oralb',
    '질레트': 'gillette', '크레스트': 'crest', '하기스': 'huggies', '네슬레': 'nestle',
}


def _query_variants(q):
    """검색어 + 브랜드 한↔영 바꾼 것들 — [(정규화 키, 필수 토막)]."""
    _base = str(q or '').strip().lower()
    _vs = {_base}
    for ko, en in _BRAND_ALIAS.items():
        if ko in _base:
            _vs.add(_base.replace(ko, en))
        if en in _name_key(_base):
            _vs.add(_name_key(_base).replace(en, ko))
    return [(_name_key(v), _name_tokens(v)) for v in _vs if v]


def _manual_lot_search(q, limit=50):
    """재고 직접 입고용 상품 찾기 — 영수증 구매이력 + 공용 DB를 상품번호로 합친다.

    반환: [{product_no, name, paid(최근 영수증 실결제 단가), list(정가),
           receipt_date, store(공용 DB 매장가)}] — 최근 구매한 것이 먼저.
    """
    _qs = str(q or '').strip()
    _vars = _query_variants(_qs)

    def _hit(name, pno):
        _k = _name_key(name)
        if _qs and _qs in str(pno or ''):
            return True
        return any((vk and vk in _k) or (vt and all(t in _k for t in vt))
                   for vk, vt in _vars)

    out = {}
    # ① 영수증 구매이력 — 상품번호별 가장 최근 줄
    try:
        from db_stats import _receipt_conn
        conn = _receipt_conn()
        try:
            rows = conn.execute(
                "SELECT product_no, product_name, unit_price, "
                "COALESCE(list_price,0) AS list_price, receipt_date FROM receipt_items "
                "WHERE COALESCE(product_no,'')<>'' ORDER BY receipt_date DESC, id DESC"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            _pn = str(r['product_no'] or '').strip()
            if _pn in out or not _hit(r['product_name'], _pn):
                continue
            out[_pn] = {'product_no': _pn, 'name': str(r['product_name'] or ''),
                        'paid': int(r['unit_price'] or 0),
                        'list': int(r['list_price'] or 0) or int(r['unit_price'] or 0),
                        'receipt_date': str(r['receipt_date'] or ''), 'store': 0}
    except Exception:
        pass
    # ② 공용 DB — 영수증에 있으면 이름(카탈로그 정식명)·매장가만 보탠다
    try:
        for s in (get_shared_products() or []):
            _pn = str(s.get('product_no') or '').strip()
            if not _pn:
                continue
            _sp = int(s.get('store_price') or 0) or int(s.get('unit_price') or 0)
            if _pn in out:
                out[_pn]['store'] = _sp
                if s.get('costco_name'):
                    out[_pn]['name'] = str(s['costco_name'])
                continue
            if _hit(s.get('costco_name'), _pn):
                out[_pn] = {'product_no': _pn, 'name': str(s.get('costco_name') or ''),
                            'paid': 0, 'list': 0, 'receipt_date': '', 'store': _sp}
    except Exception:
        pass
    # 영수증 건은 최근 구매순으로 이미 들어 있다 — 그 뒤에 공용 DB 건
    _res = ([h for h in out.values() if h['receipt_date']]
            + [h for h in out.values() if not h['receipt_date']])
    return _res[:limit]


def _store_price_of(costco_no):
    """공용 DB의 코스트코 매장가(없으면 단가) — 주문 구입가가 아직 없을 때의 대체값.

    주문 구입가(order_history.cost_price)는 **영수증 정산 뒤에야** 채워진다.
    정산 전 주문을 반품 등록하면 구입가가 0원으로 들어가 매장 환불액·예치금
    적립 사유까지 0원이 됐다. 매장가는 실제 결제가(할인 반영)와 다를 수 있어
    화면에 '매장가 기준'이라고 밝힌다.
    """
    _c = str(costco_no or '').strip()
    if not _c:
        return 0
    try:
        for s in (get_shared_products() or []):
            if str(s.get('product_no') or '').strip() == _c:
                return int(s.get('store_price') or 0) or int(s.get('unit_price') or 0)
    except Exception:
        pass
    return 0


# ── 고객 반품 ─────────────────────────────────────────────
def _customer_returns(USERNAME):
    """📥 고객 반품 — 되돌아온 물건을 받아 적고, 재고로 되돌리거나 매장에 반품한다.

    지금까지 고객 반품은 아무 데도 안 남았다. 물건은 창고에 쌓이는데 장부에는
    '팔린 것'으로 남아 있어, 며칠 뒤에는 그게 어느 주문 반품인지도 모르게 된다.
    매장 반품 기한(30일)은 그동안 계속 흐른다.

    청구는 건드리지 않는다 — 반품분 차감은 관리자가 정산·청구에서 직접 한다.
    여기가 자동으로 깎으면 이미 입금된 청구서까지 흔들린다.
    """
    import pandas as pd
    from datetime import date as _date

    st.subheader("📥 고객 반품 입고·정리")
    st.caption("고객에게서 되돌아온 물건을 등록하고, **재고로 되돌릴지 매장에 반품할지** "
               "정합니다. 청구서는 자동으로 바뀌지 않고, **매장 반품 환불액은 그 주문 "
               "판매자의 예치금에 자동 적립**됩니다.")

    _dmap = _name_map()
    _users = [u['username'] for u in (get_all_users() or []) if not u.get('is_admin')]
    if not _users:
        st.info("등록된 판매자 계정이 없습니다.")
        return

    _sm = _cr.summary()
    _open = _sm.get(_cr.OPEN) or {'count': 0, 'qty': 0, 'amount': 0}
    _rs = _sm.get('restocked') or {'count': 0, 'qty': 0}
    _sr = _sm.get('store_returned') or {'count': 0, 'qty': 0, 'amount': 0}
    # 숫자 카드를 누르면 아래에 그 목록이 열린다 — 정리 대기가 기본
    _view = st.session_state.get('cr_view', 'open')
    _cards = [('open', f"🧺 정리 대기 {_open['count']}건 · {fmt(_open['amount'])}원 묶임"),
              ('restocked', f"📦 재고로 되돌림 {_rs['count']}건"),
              ('store_returned', f"↩️ 매장 반품 완료 {_sr['count']}건")]
    for _col, (_vk, _vl) in zip(st.columns(3), _cards):
        if _col.button(_vl, key=f"cr_card_{_vk}", use_container_width=True,
                       type="primary" if _view == _vk else "secondary"):
            st.session_state['cr_view'] = _vk
            st.rerun()
    st.caption("👆 누르면 아래에 그 목록이 열립니다. 정리 대기는 **수정**, "
               "정리된 건은 **처리 취소**(정리 대기로 되돌림)를 할 수 있습니다.")

    # ── ① 반품입고 입력 ───────────────────────────────────
    with st.expander("➕ 반품입고 등록 — 주문을 찾아 넣습니다", expanded=not _open['count']):
        # 판매자를 먼저 고르게 하면 안 된다. 고객 반품은 상자만 와서 **어느
        # 판매자 주문인지 모르는 채로** 시작한다. 상품명만 넣으면 전 판매자를
        # 훑어 주인을 찾아 준다 — 판매자는 결과에서 확정된다.
        _c1, _c2 = st.columns([1, 2])
        _u_sel = _c1.selectbox("판매자", ['(전체)'] + _users, key="cr_user",
                               format_func=lambda v: v if v == '(전체)'
                               else _dmap.get(v, v),
                               help="모르면 (전체)로 두세요. 상품명·수취인으로 "
                                    "전 판매자를 훑어 누구 주문인지 찾아 줍니다.")
        _kw = _c2.text_input("수취인 · 구매자 · 주문번호로 검색", key="cr_kw",
                             placeholder="홍길동 / 2026000123456")
        _c3, _c4, _c5 = st.columns([2, 1, 1])
        _pn = _c3.text_input("상품명 (일부)", key="cr_pn",
                             help="영수증 축약어로 넣어도 됩니다 — 띄어쓰기·기호를 "
                                  "무시하고 토막마다 찾습니다 (예: KS메이플시럽1L).")
        _df = _c4.date_input("주문일 시작", value=_date.today() - timedelta(days=60),
                             key="cr_from")
        _dt = _c5.date_input("주문일 끝", value=_date.today(), key="cr_to")

        if not (_kw or _pn):
            st.caption("검색어를 넣으면 주문이 나옵니다. 상품명만 넣어도 "
                       "**어느 판매자 주문인지 찾아 줍니다.**")
            _hits = []
        else:
            _scope = _users if _u_sel == '(전체)' else [_u_sel]
            with st.spinner("주문을 찾는 중..."):
                _hits = _search_orders(_scope, _kw, _pn, str(_df), str(_dt))
        if (_kw or _pn) and not _hits:
            st.caption("조건에 맞는 주문이 없습니다. 기간을 넓히거나 상품명을 "
                       "더 짧게(예: '메이플시럽') 넣어 보세요.")
        elif _hits:
            _byu = {}
            for _x in _hits:
                _byu[_x['_u']] = _byu.get(_x['_u'], 0) + 1
            if _u_sel == '(전체)' and len(_byu) > 1:
                st.caption("🔎 여러 판매자에서 찾았습니다 — "
                           + " · ".join(f"**{_dmap.get(_k, _k)}** {_v}건"
                                        for _k, _v in sorted(_byu.items(),
                                                             key=lambda kv: -kv[1])))
            _lbl = [f"{_dmap.get(h['_u'], h['_u'])} · {h.get('order_date') or '-'} · "
                    f"{h.get('recipient') or '-'} · "
                    f"{str(h.get('product_name') or '')[:32]} · "
                    f"{int(h.get('qty') or 1)}개 · {h.get('order_no')}" for h in _hits]
            _pick = st.selectbox(f"반품된 주문 ({len(_hits)}건)", _lbl, key="cr_pick")
            _h = _hits[_lbl.index(_pick)]
            # 판매자는 고른 주문에서 확정된다 — 위 선택칸은 검색 범위일 뿐이다
            _u = str(_h.get('_u') or '')
            st.markdown(f"➡️ **{_dmap.get(_u, _u)}** 판매자의 주문입니다 — "
                        "재고로 되돌리면 이 판매자 재고가 됩니다.")

            # 이미 이 주문으로 들어온 반품 — 같은 건을 두 번 넣는 사고를 막는다
            _prev = _cr.by_order(_u, str(_h.get('order_no') or ''))
            if _prev:
                st.warning("⚠️ 이 주문은 이미 반품이 등록돼 있습니다 — "
                           + " · ".join(f"{p['returned_at']} {p['qty']}개 "
                                        f"({_cr.STATUS_LABEL.get(p['status'], p['status'])})"
                                        for p in _prev[:5])
                           + "\n\n나눠 들어온 반품이면 그대로 등록하세요.")

            # 코스트코 번호는 재고로 되돌릴 때 반드시 필요하다. 자동으로 찾아
            # 채우되 고칠 수 있게 둔다 — 틀린 번호로 입고하면 남의 재고가 된다.
            try:
                _cno = resolve_costco_no(_u, naver_no=str(_h.get('product_no') or ''),
                                         product_name=str(_h.get('product_name') or ''))
            except Exception:
                _cno = ''
            # 위젯 key에 주문번호를 넣는다 — 고정 key면 다른 주문을 골라도
            # 수량·구입가·번호가 먼저 고른 주문 값 그대로 남는다(Streamlit은
            # key가 같으면 value를 무시하고 세션 값을 쓴다).
            _k = str(_h.get('order_no') or 'na')
            _f1, _f2, _f3, _f4 = st.columns([1, 1, 1, 1.4])
            _qty = _f1.number_input("반품 수량(개)", min_value=1, step=1,
                                    max_value=max(1, int(_h.get('qty') or 1)),
                                    value=int(_h.get('qty') or 1), key=f"cr_qty_{_k}")
            _rdate = _f2.date_input("반품입고일", value=_date.today(), key="cr_rdate")
            # order_history.cost_price는 주문 한 줄의 **합계**다(수량 2면 2개 값).
            # 그대로 단가 칸에 넣으면 재고 단가·묶인 금액이 수량배로 부풀었다
            # (9/17 등록 2건이 이렇게 들어감). 주문 수량으로 나눠 개당 값을 쓴다.
            _oqty = max(1, int(_h.get('qty') or 1))
            _ocost = int(_h.get('cost_price') or 0)
            # 정산 전 주문은 구입가가 0 — 공용 DB 매장가로 대신 채운다
            _sp = _store_price_of(_cno) if _ocost <= 0 else 0
            _cost = _f3.number_input("개당 구입가(원)", min_value=0, step=100,
                                     value=round(_ocost / _oqty) if _ocost > 0 else _sp,
                                     key=f"cr_cost_{_k}",
                                     help="재고로 되돌릴 때 이 단가로 입고됩니다. "
                                          "주문 구입가 합계 ÷ 주문 수량으로 자동 계산합니다.")
            _cno_in = _f4.text_input("코스트코 상품번호", value=_cno, key=f"cr_cno_{_k}",
                                     help="재고로 되돌리려면 필요합니다. 매장 반품만 할 "
                                          "거라면 비워도 됩니다.")
            _r1, _r2 = st.columns([1, 2])
            _reason = _r1.selectbox("반품 사유", ["단순변심", "파손·불량", "오배송",
                                               "배송지연", "기타"], key="cr_reason")
            _memo = _r2.text_input("메모", key="cr_memo",
                                   placeholder="예: 박스만 개봉, 재판매 가능")
            if _ocost <= 0 and _sp:
                st.caption(f"ℹ️ 이 주문은 영수증 정산 전이라 구입가가 없어 **코스트코 매장가 "
                           f"{fmt(_sp)}원**으로 채웠습니다. 실제 결제가(할인·묶음)와 다르면 "
                           "고쳐 주세요.")
            elif _ocost <= 0:
                st.caption("⚠️ 이 주문엔 구입가가 아직 기록되지 않았고(영수증 정산 전) "
                           "공용 DB에 매장가도 없습니다. 개당 구입가를 직접 넣어 주세요.")
            elif _oqty > 1:
                st.caption(f"ℹ️ 구입가 {fmt(_ocost)}원 ÷ 주문 {_oqty}개 = "
                           f"개당 {fmt(round(_ocost / _oqty))}원")
            if not _cno_in:
                st.caption("ℹ️ 코스트코 번호가 없으면 **재고로 되돌리기는 안 되고** "
                           "매장 반품 완료만 처리할 수 있습니다.")

            if st.button("📥 반품입고 등록", type="primary", key="cr_add"):
                _res = _cr.add([{
                    'returned_at': str(_rdate), 'username': _u,
                    'order_no': str(_h.get('order_no') or ''),
                    'order_date': str(_h.get('order_date') or ''),
                    'recipient': str(_h.get('recipient') or ''),
                    'product_name': str(_h.get('product_name') or ''),
                    'naver_no': str(_h.get('product_no') or ''),
                    'costco_no': str(_cno_in or '').strip(),
                    'qty': int(_qty), 'split_qty': 1,
                    'unit_cost': int(_cost),
                    'reason': _reason, 'memo': _memo,
                }], created_by=USERNAME)
                if _res['ok']:
                    st.success(f"📥 반품입고 {_res['ok']}건 등록 — 아래 **정리 대기**에서 "
                               "재고로 되돌리거나 매장 반품으로 넘기세요.")
                    st.rerun()
                else:
                    st.error("등록하지 못했습니다 (판매자·수량 확인).")

    if _view == 'open':
        # ── ② 정리 대기 ───────────────────────────────────────
        st.divider()
        # 사용자가 매장 반품을 요청한 건을 맨 위로 — 관리자가 매장에 들고 갈 목록이다
        _rows = sorted(_cr.open_returns(), key=lambda r: -int(r.get('store_req') or 0))
        _nreq = sum(1 for r in _rows if int(r.get('store_req') or 0))
        st.markdown(f"##### 🧺 정리 대기 {len(_rows)}건"
                    + (f" · 🙋 매장 반품 요청 {_nreq}건" if _nreq else ""))
        if not _rows:
            st.success("정리할 반품이 없습니다.")
        else:
            st.caption(f"물건이 창고에 있는 상태입니다. 매장 반품 기한({RETURN_DAYS}일)은 "
                       "반품입고일부터가 아니라 **원래 구매일**부터 흐르므로, 오래된 주문일수록 "
                       "먼저 처리하세요.")
            _today_s = datetime.today().strftime("%Y-%m-%d")
            _tbl = []
            for _r in _rows:
                try:
                    _age = (datetime.strptime(_today_s, "%Y-%m-%d")
                            - datetime.strptime(str(_r['returned_at']), "%Y-%m-%d")).days
                except Exception:
                    _age = 0
                _tbl.append({
                    "선택": False, "번호": _r['id'],
                    "요청": "🙋 매장반품" if int(_r.get('store_req') or 0) else "",
                    "반품입고일": _r['returned_at'], "보관": _age_badge(_age),
                    "판매자": _dmap.get(_r['username'], _r['username']),
                    "수취인": _r['recipient'], "상품명": str(_r['product_name'])[:30],
                    "수량": _r['qty'], "구입가": _r['unit_cost'],
                    "코스트코번호": _r['costco_no'] or '',
                    "사유": _r['reason'], "주문번호": _r['order_no'],
                })
            # ✏️ 수량·구입가·코스트코번호·사유는 표에서 바로 고친다(정리 대기 건만)
            _EDIT = ("수량", "구입가", "코스트코번호", "사유")
            _ed = st.data_editor(
                pd.DataFrame(_tbl), use_container_width=True, hide_index=True,
                # 저장 뒤엔 key를 바꿔 표를 DB 값으로 다시 그린다
                key=f"cr_open_ed_{st.session_state.get('cr_open_v', 0)}",
                disabled=[c for c in _tbl[0] if c not in ("선택",) + _EDIT],
                column_config={
                    "선택": st.column_config.CheckboxColumn("선택"),
                    "번호": st.column_config.NumberColumn("번호", format='%d'),
                    "수량": st.column_config.NumberColumn("수량 ✏️", format='%d',
                                                        min_value=1, step=1),
                    "구입가": st.column_config.NumberColumn("구입가(개당) ✏️", format='%d',
                                                         min_value=0, step=100),
                    "코스트코번호": st.column_config.TextColumn("코스트코번호 ✏️"),
                    "사유": st.column_config.SelectboxColumn(
                        "사유 ✏️", options=["단순변심", "파손·불량", "오배송",
                                           "배송지연", "기타"]),
                })
            _recs = _ed.to_dict('records')
            _sel = [_tbl[i]['번호'] for i, _x in enumerate(_recs) if _x.get('선택')]
            _sel_rows = [_r for _r in _rows if _r['id'] in _sel]

            # 바뀐 칸만 모아 저장
            _chg = {}
            for _o, _x in zip(_tbl, _recs):
                _d = {}
                if int(_x.get('수량') or 0) != int(_o['수량'] or 0):
                    _d['qty'] = int(_x.get('수량') or 0)
                if int(_x.get('구입가') or 0) != int(_o['구입가'] or 0):
                    _d['unit_cost'] = int(_x.get('구입가') or 0)
                if str(_x.get('코스트코번호') or '').strip() != str(_o['코스트코번호'] or ''):
                    _d['costco_no'] = str(_x.get('코스트코번호') or '').strip()
                if str(_x.get('사유') or '') != str(_o['사유'] or ''):
                    _d['reason'] = str(_x.get('사유') or '')
                if _d:
                    _chg[int(_o['번호'])] = _d
            if _chg:
                st.info(f"✏️ 수정한 건 {len(_chg)}건 — "
                        + " · ".join(f"#{_k}" for _k in list(_chg)[:8]))
                _sv1, _sv2 = st.columns(2)
                if _sv1.button(f"💾 수정 저장 ({len(_chg)}건)", key="cr_edit_save",
                               type="primary", use_container_width=True):
                    _bad = []
                    for _k, _d in _chg.items():
                        _res = _cr.update(_k, by=USERNAME, **_d)
                        if not _res['ok']:
                            _bad.append(f"#{_k} {_res['msg']}")
                    st.session_state['cr_open_v'] = st.session_state.get('cr_open_v', 0) + 1
                    if _bad:
                        st.error(" / ".join(_bad[:4]))
                    else:
                        st.toast(f"💾 {len(_chg)}건 수정 저장", icon="✅")
                    st.rerun()
                if _sv2.button("↩ 수정 취소", key="cr_edit_cancel", use_container_width=True):
                    st.session_state['cr_open_v'] = st.session_state.get('cr_open_v', 0) + 1
                    st.rerun()

            _fill_zero_cost(_rows, USERNAME, key="cr_fill_open")

            st.markdown("**선택한 건을 어떻게 정리할까요**")
            _a1, _a2 = st.columns(2)

            # 재고로 되돌림 — 보유자를 바꿀 수 있어야 한다. 반품된 물건을 원래
            # 판매자가 아니라 다른 사람이 가져가는 일이 있다.
            with _a1:
                _owner_same = st.checkbox("원래 판매자 재고로", value=True, key="cr_owner_same")
                _owner = None
                if not _owner_same:
                    _owner = st.selectbox("되돌릴 대상", _users, key="cr_owner",
                                          format_func=lambda v: _dmap.get(v, v))
                _no_cno = [r for r in _sel_rows if not str(r.get('costco_no') or '').strip()]
                _req_sel = [r for r in _sel_rows if int(r.get('store_req') or 0)]
                if _req_sel:
                    st.caption("⚠️ 사용자가 **매장 반품을 요청한 건** "
                               + " · ".join(f"#{r['id']}" for r in _req_sel[:5])
                               + "이 섞여 있습니다 — 재고로 되돌리면 요청과 다르게 처리됩니다.")
                if st.button(f"📦 {len(_sel)}건 **재고로 되돌림**", key="cr_restock",
                             disabled=not _sel or bool(_no_cno), use_container_width=True,
                             help="상태가 멀쩡해 다시 팔 수 있는 건입니다. 그 사용자 "
                                  "재고로 입고되고, 재고 조정 이력에 반품 번호가 남습니다."):
                    _ok, _fail = 0, []
                    for _r in _sel_rows:
                        _res = _cr.restock(_r['id'], owner=(_owner or ''), by=USERNAME)
                        if _res['ok']:
                            _ok += 1
                        else:
                            _fail.append(f"#{_r['id']} {_res['msg']}")
                    if _ok:
                        st.success(f"📦 {_ok}건을 재고로 되돌렸습니다 — "
                                   "**📊 전체 재고**에서 확인하세요.")
                    if _fail:
                        st.error(" / ".join(_fail[:4]))
                    st.rerun()
                if _no_cno:
                    st.caption("⚠️ 코스트코 번호가 없는 건이 섞여 있어 재고로 되돌릴 수 "
                               "없습니다 — " + " · ".join(f"#{r['id']}" for r in _no_cno[:5]))

            # 매장 반품 — 건별 환불액을 받아 그 주문 판매자 예치금에 바로 적립한다.
            # 합계 하나만 받으면 여러 판매자가 섞였을 때 누구에게 얼마를 줄지 못 나눈다.
            with _a2:
                _refunds = {}
                if _sel_rows:
                    st.caption("💳 환불액은 **그 주문 판매자의 예치금에 자동 적립**됩니다. "
                               "건마다 매장에서 실제로 돌려받은 금액으로 고치세요.")
                    _rf_tbl = [{
                        "번호": r['id'],
                        "판매자": _dmap.get(r['username'], r['username']),
                        "상품명": str(r['product_name'])[:24],
                        "수량": int(r['qty'] or 0),
                        "구입가(개당)": int(r['unit_cost'] or 0),
                        "구입가 합계": _cr.purchase_total(r),
                        "환불액": _cr.purchase_total(r),
                    } for r in _sel_rows]
                    _rf_ed = st.data_editor(
                        pd.DataFrame(_rf_tbl), use_container_width=True, hide_index=True,
                        # key에 선택한 건들을 넣어야 선택을 바꿀 때 기본 금액이 다시 잡힌다
                        key="cr_refund_ed_" + "_".join(str(i) for i in _sel[:12]),
                        disabled=[c for c in _rf_tbl[0] if c != "환불액"],
                        column_config={
                            **{_k: st.column_config.NumberColumn(_k, format='%d')
                               for _k in ("번호", "수량", "구입가(개당)", "구입가 합계")},
                            "환불액": st.column_config.NumberColumn(
                                "환불액", format='%d', min_value=0, step=100,
                                help="매장에서 돌려받은 금액 = 예치금 적립액"),
                        })
                    _refunds = {int(x['번호']): int(x.get('환불액') or 0)
                                for x in _rf_ed.to_dict('records')}
                    st.caption(f"구입가 합계 **{fmt(sum(x['구입가 합계'] for x in _rf_tbl))}원** · "
                               f"환불·적립 합계 **{fmt(sum(_refunds.values()))}원**")
                if st.button(f"↩️ {len(_sel)}건 **매장 반품 완료** + 예치금 적립", key="cr_store",
                             disabled=not _sel, use_container_width=True,
                             help="코스트코에 돌려주고 환불까지 확인했을 때 누르세요. "
                                  "재고로는 들어가지 않고, 환불액이 판매자 예치금에 적립됩니다."):
                    _res = _cr.store_return(_sel, by=USERNAME, amounts=_refunds)
                    _cred = _res['credited']
                    st.success(f"↩️ {_res['ok']}건 매장 반품 완료 · 예치금 적립 "
                               f"{len(_cred)}건 {fmt(sum(c['amount'] for c in _cred))}원"
                               + (" — " + ", ".join(f"{_dmap.get(c['username'], c['username'])} "
                                                    f"{fmt(c['amount'])}원" for c in _cred[:6])
                                  if _cred else ""))
                    if _res['errors']:
                        st.error("적립 실패: " + " / ".join(_res['errors'][:4]))
                    st.rerun()

            if st.button(f"🗑 선택한 {len(_sel)}건 삭제 (잘못 입력)", key="cr_del",
                         disabled=not _sel):
                _res = _cr.delete(_sel)
                st.success(f"🗑 {_res['deleted']}건 삭제"
                           + (f" · 이미 정리된 {len(_res['skipped'])}건은 남았습니다"
                              if _res['skipped'] else ""))
                st.rerun()

    else:
        _cr_done_panel(_view, USERNAME, _dmap)


def _fill_zero_cost(rows, USERNAME, key):
    """구입가 0원인 반품 건을 공용 DB 매장가로 채운다 — 정리 대기·적립 전 매장반품만.
    코스트코번호가 없거나 매장가를 못 찾은 건은 건드리지 않고 알린다."""
    _zero = [r for r in rows if not int(r.get('unit_cost') or 0)]
    if not _zero:
        return
    _fill = [(r, _store_price_of(r.get('costco_no'))) for r in _zero]
    _can = [(r, p) for r, p in _fill if p > 0]
    _no = [r for r, p in _fill if p <= 0]
    st.warning(f"💲 구입가 0원 {len(_zero)}건 — 영수증 정산 전에 등록돼 구입가가 비었습니다. "
               + (" · ".join(f"#{r['id']} {str(r['product_name'])[:12]} → 매장가 {fmt(p)}원"
                             for r, p in _can[:6]) if _can else "")
               + (f"\n\n코스트코번호가 없거나 매장가를 못 찾은 건: "
                  + " · ".join(f"#{r['id']}" for r in _no[:8]) + " — ✏️로 직접 입력하세요."
                  if _no else ""))
    if _can and st.button(f"💲 {len(_can)}건 매장가로 채우기", key=key):
        _bad = []
        for r, p in _can:
            if r['status'] == 'store_returned':
                _res = _cr.update_store_returned(r['id'], by=USERNAME, unit_cost=p)
            else:
                _res = _cr.update(r['id'], by=USERNAME, unit_cost=p)
            if not _res['ok']:
                _bad.append(f"#{r['id']} {_res['msg']}")
        for _k in ('cr_open_v', 'cr_done_v'):
            st.session_state[_k] = st.session_state.get(_k, 0) + 1
        if _bad:
            st.error(" / ".join(_bad[:4]))
        else:
            st.toast(f"💲 {len(_can)}건 구입가를 매장가로 채웠습니다", icon="✅")
        st.rerun()


def _cr_done_panel(status, USERNAME, _dmap):
    """📦 재고로 되돌림 / ↩️ 매장 반품 완료 목록 + 처리 취소.

    취소는 정리 대기로 되돌린다 — 재고로 되돌린 건은 그 재고를 다시 빼고,
    매장 반품 건은 적립한 예치금을 (−)로 되돌린다. 그다음 정리 대기에서
    고치거나 다시 처리한다.
    """
    import pandas as pd
    _is_store = status == 'store_returned'
    _rows = _cr.list_returns(status=status, limit=500)
    st.divider()
    st.markdown(f"##### {'↩️ 매장 반품 완료' if _is_store else '📦 재고로 되돌림'} "
                f"{len(_rows)}건")
    if not _rows:
        st.caption("해당하는 반품이 없습니다.")
        return
    _tbl = []
    for _r in _rows:
        _e = {
            "선택": False, "번호": _r['id'],
            "반품입고일": _r['returned_at'],
            "판매자": _dmap.get(_r['username'], _r['username']),
            "수취인": _r['recipient'], "상품명": str(_r['product_name'])[:30],
            "수량": int(_r['qty'] or 0), "사유": _r['reason'],
            "구입가(개당)": int(_r['unit_cost'] or 0),
            "구입가 합계": _cr.purchase_total(_r),
        }
        if _is_store:
            _e["환불액"] = int(_r['refund_amount'] or 0)
            _e["예치금 적립"] = (f"💳 {fmt(int(_r.get('deposit_amount') or 0))}원"
                            if int(_r.get('deposit_id') or 0) > 0 else
                            ('미적립' if int(_r['refund_amount'] or 0) else '—'))
        else:
            _e["재고 대상"] = _dmap.get(_r['restock_owner'], _r['restock_owner']) or '—'
            _e["코스트코번호"] = _r['costco_no']
        _e.update({"처리일시": _r['done_at'], "처리자": _r['done_by'],
                   "주문번호": _r['order_no']})
        _tbl.append(_e)
    # 매장 반품은 적립 전이면 구입가·환불액을 표에서 바로 고친다
    _EDIT = ("구입가(개당)", "환불액") if _is_store else ()
    _cfg = {"선택": st.column_config.CheckboxColumn("선택"),
            **{_k: st.column_config.NumberColumn(_k, format='%d')
               for _k in ("번호", "수량", "구입가(개당)", "구입가 합계", "환불액")}}
    if _is_store:
        _cfg["구입가(개당)"] = st.column_config.NumberColumn(
            "구입가(개당) ✏️", format='%d', min_value=0, step=100)
        _cfg["환불액"] = st.column_config.NumberColumn(
            "환불액 ✏️", format='%d', min_value=0, step=100,
            help="예치금에 적립되는 금액. 적립된 건은 처리 취소 후 고치세요.")
    _vkey = f"cr_done_ed_{status}_{st.session_state.get('cr_done_v', 0)}"
    _ed = st.data_editor(
        pd.DataFrame(_tbl), use_container_width=True, hide_index=True,
        key=_vkey, disabled=[c for c in _tbl[0] if c not in ("선택",) + _EDIT],
        column_config=_cfg)
    _recs = _ed.to_dict('records')
    _sel = [_tbl[i]['번호'] for i, _x in enumerate(_recs) if _x.get('선택')]

    if _is_store:
        _chg = {}
        for _o, _x in zip(_tbl, _recs):
            _d = {}
            if int(_x.get('구입가(개당)') or 0) != int(_o['구입가(개당)'] or 0):
                _d['unit_cost'] = int(_x.get('구입가(개당)') or 0)
            if int(_x.get('환불액') or 0) != int(_o['환불액'] or 0):
                _d['refund_amount'] = int(_x.get('환불액') or 0)
            if _d:
                _chg[int(_o['번호'])] = _d
        if _chg:
            st.info(f"✏️ 수정한 건 {len(_chg)}건 — "
                    + " · ".join(f"#{_k}" for _k in list(_chg)[:8])
                    + " (예치금 적립 전인 건만 저장됩니다)")
            _sv1, _sv2 = st.columns(2)
            if _sv1.button(f"💾 수정 저장 ({len(_chg)}건)", key="cr_done_save",
                           type="primary", use_container_width=True):
                _bad = []
                for _k, _d in _chg.items():
                    _res = _cr.update_store_returned(_k, by=USERNAME, **_d)
                    if not _res['ok']:
                        _bad.append(f"#{_k} {_res['msg']}")
                st.session_state['cr_done_v'] = st.session_state.get('cr_done_v', 0) + 1
                if _bad:
                    st.error(" / ".join(_bad[:4]))
                else:
                    st.toast(f"💾 {len(_chg)}건 수정 저장", icon="✅")
                st.rerun()
            if _sv2.button("↩ 수정 취소", key="cr_done_cancel", use_container_width=True):
                st.session_state['cr_done_v'] = st.session_state.get('cr_done_v', 0) + 1
                st.rerun()
    if _is_store:
        _ref = sum(int(_r['refund_amount'] or 0) for _r in _rows)
        _crd = sum(int(_r.get('deposit_amount') or 0) for _r in _rows
                   if int(_r.get('deposit_id') or 0) > 0)
        st.caption(f"매장 환불 누계 **{fmt(_ref)}원** · 예치금 적립 누계 **{fmt(_crd)}원**")

    # ── 처리 취소 ──
    st.caption("↩ **처리 취소** = 정리 대기로 되돌립니다. "
               + ("적립했던 예치금은 같은 금액을 (−)로 되돌립니다(예치금 내역에 '적립 취소'로 남음)."
                  if _is_store else
                  "그때 넣은 재고를 같은 보유자 재고에서 다시 뺍니다 — 이미 팔려 재고가 "
                  "모자라면 취소되지 않습니다."))
    _ok_chk = st.checkbox(f"선택한 {len(_sel)}건을 정리 대기로 되돌립니다", key=f"cr_undo_ok_{status}",
                          disabled=not _sel)
    if st.button(f"↩ {len(_sel)}건 처리 취소", key=f"cr_undo_{status}",
                 disabled=not (_sel and _ok_chk)):
        _ok, _bad = [], []
        for _id in _sel:
            _res = _cr.cancel(_id, by=USERNAME)
            (_ok if _res['ok'] else _bad).append(_res['msg'] if _res['ok'] else
                                                 f"#{_id} {_res['msg']}")
        if _ok:
            st.toast(f"↩ {len(_ok)}건 처리 취소 — 정리 대기로 돌아갔습니다", icon="✅")
        if _bad:
            st.error(" / ".join(_bad[:4]))
        if _ok and not _bad:
            st.session_state['cr_view'] = 'open'
        st.rerun()

    if _is_store:
        _fill_zero_cost([r for r in _rows if not int(r.get('deposit_id') or 0)],
                        USERNAME, key="cr_fill_store")

    # 미적립 매장 반품 — 표에서 **선택한 건만** 예치금에 적립
    if _is_store:
        _unc = _cr.uncredited_store_returns()
        if _unc:
            st.warning(
                f"💳 예치금에 아직 적립 안 된 매장 반품 **{len(_unc)}건 · "
                f"{fmt(sum(int(r['refund_amount'] or 0) for r in _unc))}원** — "
                "위 표에서 **선택**한 뒤 아래 버튼을 누르면 그 건만 적립됩니다. "
                "환불액이 맞는지 먼저 확인하세요(✏️로 수정 가능).\n\n"
                "예전에 여러 건을 묶어 처리한 경우 합계가 **첫 건에만** 적혀 "
                "있습니다 — 판매자가 섞였으면 환불액을 건별로 고친 뒤 적립하세요.")
            _unc_ids = {int(r['id']) for r in _unc}
            _pick = [r for r in _unc if int(r['id']) in set(_sel)]
            _skip = [i for i in _sel if i not in _unc_ids]
            if _pick:
                st.markdown(
                    "적립 예정: " + " · ".join(
                        f"**{_dmap.get(r['username'], r['username'])}** "
                        f"{str(r['product_name'])[:16]} **{fmt(int(r['refund_amount']))}원**"
                        for r in _pick)
                    + f"  →  합계 **{fmt(sum(int(r['refund_amount']) for r in _pick))}원**")
                _zero = [r for r in _pick if not int(r['unit_cost'] or 0)]
                if _zero:
                    st.caption("⚠️ 구입가가 0원인 건이 있습니다 — "
                               + " · ".join(f"#{r['id']}" for r in _zero)
                               + " (적립 사유에 구입가 0원으로 남습니다. 필요하면 먼저 ✏️ 수정)")
            if _skip:
                st.caption("ℹ️ 선택한 건 중 " + " · ".join(f"#{i}" for i in _skip[:6])
                           + "은 이미 적립됐거나 환불액이 0원이라 건너뜁니다.")
            if _chg:
                st.caption("⚠️ 저장 안 한 수정이 있습니다 — **💾 수정 저장**을 먼저 누르세요 "
                           "(적립은 저장된 환불액으로 됩니다).")
            if st.button(f"💳 선택한 {len(_pick)}건 예치금에 적립", key="cr_backfill",
                         type="primary", disabled=not _pick or bool(_chg)):
                _ok, _err = [], []
                for r in _pick:
                    _c = _cr.credit_deposit(r['id'], by=USERNAME)
                    (_ok if _c['ok'] else _err).append(_c if _c['ok'] else
                                                       f"#{r['id']} {_c['msg']}")
                st.toast(f"💳 {len(_ok)}건 {fmt(sum(c['amount'] for c in _ok))}원 적립",
                         icon="✅")
                if _err:
                    st.error(" / ".join(_err[:4]))
                st.session_state['cr_done_v'] = st.session_state.get('cr_done_v', 0) + 1
                st.rerun()


def _user_returns(USERNAME):
    """↩️ 내 반품 — 관리자가 받아 둔 내 주문의 고객 반품과 그 행방.

    반품 물건은 관리자 창고로 돌아온다. 사용자는 그 사실도, 그 뒤 재고로
    돌아왔는지 매장에 갔는지도 볼 수 없었다. 여기서 보고, 아직 창고에 있는
    건은 **매장 반품을 요청**한다 — 실제 반품은 관리자가 매장에서 한다.

    환불금액은 보여 주지 않는다. 관리자가 여러 건을 한 번에 매장 반품하면
    합계가 첫 건에만 적혀 건별 금액이 아니기 때문이다.
    """
    import pandas as pd
    st.subheader("↩️ 내 반품")
    st.caption("고객이 반품해 **관리자에게 돌아온 내 주문**입니다. 관리자가 입고를 "
               "확인하면 여기에 나타납니다. 아직 창고에 있는 건은 **매장 반품을 "
               "요청**할 수 있고, 관리자가 코스트코 매장에 반품합니다.")
    _all = _cr.list_returns(username=USERNAME, limit=500)
    if not _all:
        st.info("등록된 반품이 없습니다.")
        return
    _open = [r for r in _all if r['status'] == _cr.OPEN]
    _done = [r for r in _all if r['status'] != _cr.OPEN]
    _m1, _m2, _m3 = st.columns(3)
    _m1.metric("창고 보관 중", f"{len(_open)}건")
    _m2.metric("내 재고로 되돌림", f"{sum(1 for r in _done if r['status'] == 'restocked')}건")
    _m3.metric("매장 반품 완료",
               f"{sum(1 for r in _done if r['status'] == 'store_returned')}건")

    st.markdown(f"##### 📦 창고 보관 중 {len(_open)}건")
    if not _open:
        st.caption("창고에 남은 반품이 없습니다.")
    else:
        st.caption(f"매장 반품 기한({RETURN_DAYS}일)은 **원래 구매일**부터 흐릅니다 — "
                   "반품할 거라면 빨리 요청하세요. 요청하지 않은 건은 관리자가 "
                   "상태를 보고 내 재고로 되돌리거나 매장에 반품합니다.")
        _tbl = [{
            "선택": False, "번호": r['id'],
            "상태": ("🙋 매장 반품 요청함" if int(r.get('store_req') or 0)
                   else "📥 입고 확인됨"),
            "반품입고일": r['returned_at'], "주문일": r.get('order_date') or '',
            "수취인": r['recipient'], "상품명": str(r['product_name'])[:34],
            "수량": int(r['qty'] or 0), "사유": r['reason'],
            "주문번호": r['order_no'],
        } for r in _open]
        _ed = st.data_editor(
            pd.DataFrame(_tbl), use_container_width=True, hide_index=True,
            key="ucr_open_ed", disabled=[c for c in _tbl[0] if c != "선택"],
            column_config={"선택": st.column_config.CheckboxColumn("선택"),
                           "번호": st.column_config.NumberColumn("번호", format='%d'),
                           "수량": st.column_config.NumberColumn("수량", format='%d')})
        _sel = [_tbl[i]['번호'] for i, x in enumerate(_ed.to_dict('records'))
                if x.get('선택')]
        _req_ids = {r['id'] for r in _open if int(r.get('store_req') or 0)}
        _to_req = [i for i in _sel if i not in _req_ids]
        _to_cancel = [i for i in _sel if i in _req_ids]
        _b1, _b2 = st.columns(2)
        if _b1.button(f"🙋 {len(_to_req)}건 매장 반품 요청", key="ucr_req", type="primary",
                      disabled=not _to_req, use_container_width=True):
            _res = _cr.set_store_request(_to_req, USERNAME, on=True, by=USERNAME)
            st.success(f"🙋 {_res['ok']}건 매장 반품을 요청했습니다 — 관리자가 처리합니다.")
            st.rerun()
        if _b2.button(f"↩ {len(_to_cancel)}건 요청 취소", key="ucr_cancel",
                      disabled=not _to_cancel, use_container_width=True):
            _res = _cr.set_store_request(_to_cancel, USERNAME, on=False)
            st.success(f"{_res['ok']}건 요청을 취소했습니다.")
            st.rerun()

    with st.expander(f"📋 처리 완료 {len(_done)}건", expanded=False):
        if not _done:
            st.caption("아직 처리된 반품이 없습니다.")
        else:
            _nm = _name_map()
            st.dataframe(pd.DataFrame([{
                "처리": _cr.STATUS_LABEL.get(r['status'], r['status']),
                "처리일": str(r.get('done_at') or '')[:10],
                "재고 대상": (_nm.get(r['restock_owner'], r['restock_owner'])
                          if r['status'] == 'restocked' else '—'),
                "반품입고일": r['returned_at'], "수취인": r['recipient'],
                "상품명": str(r['product_name'])[:34], "수량": int(r['qty'] or 0),
                "구입가 합계": _cr.purchase_total(r),
                # 적립액만 보여 준다 — 예전 묶음 처리의 환불액은 합계라 건별 값이 아니다
                "예치금 적립": (int(r.get('deposit_amount') or 0)
                           if int(r.get('deposit_id') or 0) > 0 else None),
                "사유": r['reason'], "주문번호": r['order_no'],
            } for r in _done]), use_container_width=True, hide_index=True,
                column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                               for _k in ("구입가 합계", "예치금 적립")})
            st.caption("'재고로 되돌림'은 **📦 내 재고**에 수량이 다시 잡힌 건입니다. "
                       "'매장 반품 완료'는 매장에서 돌려받은 금액이 **💳 예치금에 적립**되며, "
                       "내 구매내역 정산 › 예치금 내역에 '매장반품 적립'으로 사유와 함께 "
                       "남습니다.")


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
def _name_map():
    """username → 표시 이름. 재고가 오간 상대를 아이디로만 보여주면 알아볼 수 없다."""
    try:
        return {u['username']: (u.get('display_name') or u['username'])
                for u in get_all_users()}
    except Exception:
        return {}


def _user_stock(USERNAME, sur):
    st.subheader("📦 내 재고")
    rows = get_stock_summary(owner=USERNAME)
    if not rows:
        st.info("보유 재고가 없습니다. 대량구매 공지에서 요청해 보세요.")
    else:
        import pandas as pd
        # 얼마짜리를 얼마에 샀는지 — 할인을 얼마나 받았는지 남겨야 나중에
        # 타인에게 넘길 값을 정하거나 수익을 따질 때 근거가 된다.
        st.dataframe(pd.DataFrame([{
            "상품번호": r['product_no'], "상품명": r['product_name'],
            "잔여(개)": r['qty_left'], "입고(개)": r['qty_in'],
            "정가": int(r.get('list_price') or 0),
            "구입가": int(r.get('unit_cost') or 0),
            "할인": max(0, int(r.get('list_price') or 0) - int(r.get('unit_cost') or 0)),
            "재고금액": int(r.get('unit_cost') or 0) * int(r['qty_left'] or 0),
            "최초입고": r['oldest_at'], "경과": _age_badge(r['age_days']),
        } for r in rows]), use_container_width=True, hide_index=True,
            column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                           for _k in ("정가", "구입가", "할인", "재고금액")})
        st.caption("수량·금액은 **판매 1개 기준**입니다(소분 상품이면 1팩이 여러 개). "
                   "정가는 할인 전 영수증 단가, 구입가는 실제 지불한 단가입니다. "
                   "이 기능 이전에 입고된 재고는 정가가 0으로 보입니다.")

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
        _nm = _name_map()
        st.dataframe(pd.DataFrame([{
            "발송일": m['dispatched_at'],
            "가져간 판매자": _nm.get(m['seller'], m['seller']),
            "상품번호": m['product_no'], "수량": m['qty'],
            "구입가(개당)": int(m['unit_cost']), "웃돈": int(m['surcharge']),
            "정산액": int(m['unit_cost']) * int(m['qty']) + int(m['surcharge']),
            "주문번호": m['order_no'],
            "상태": "✅ 정산완료" if m['settle_status'] == 'SETTLED' else "⏳ 대기",
        } for m in mv]), use_container_width=True, hide_index=True,
            column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                           for _k in ("구입가(개당)", "웃돈", "정산액")})
        st.caption("**가져간 판매자**가 내 재고를 쓴 사람입니다. 정산액 = 구입가×수량 + 웃돈.")

    st.divider()
    st.subheader("🛒 내가 타인 재고로 판매한 건")
    st.caption(f"내 재고가 없어 다른 분 재고로 나간 건입니다. 수익계산의 구입가격에 개당 {fmt(sur)}원이 더해집니다.")
    ms = get_moves(seller=USERNAME, only_cross=True, limit=200)
    if not ms:
        st.caption("해당 내역이 없습니다.")
    else:
        import pandas as pd
        _nm2 = _name_map()
        st.dataframe(pd.DataFrame([{
            "발송일": m['dispatched_at'],
            "재고 보유자": _nm2.get(m['owner'], m['owner']),
            "상품번호": m['product_no'], "수량": m['qty'],
            "구입가(개당)": int(m['unit_cost']), "추가 부담": int(m['surcharge']),
            "주문번호": m['order_no'],
        } for m in ms]), use_container_width=True, hide_index=True,
            column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                           for _k in ("구입가(개당)", "추가 부담")})
