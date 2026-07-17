"""코스트코핫딜 주문 수익 관리 v3 (Multi-page Architecture)
- st.navigation 적용으로 잔상 제거 및 성능 최적화
- 단일 5,000줄 app.py → 라우터 ~250줄 + pages_lib/*.py
"""
import os

# .env 파일 로드 (서버 배포 시 환경변수 주입)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import streamlit as st

# ── 기본 설정 ─────────────────────────────────────────────
APP_TITLE = "costcobiz"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide",
                   initial_sidebar_state="expanded", menu_items={"About": "costcobiz"})


# 디자인 시스템 CSS 주입
from ui_theme import inject_global_css as _inject_global_css
_inject_global_css()

# 제품 목록 행 간격 축소 (글로벌)
st.markdown("""
<style>
div[data-testid="stHorizontalBlock"] {
    margin-bottom: -0.4rem;
}
</style>
""", unsafe_allow_html=True)

# ── DB 및 유틸리티 임포트 ────────────────────────────────
from db import (
    init_auth_db, check_login, get_global_setting, register_user, ensure_local_user,
    get_user_info, create_session, get_session_user, delete_session,
    init_user_db, get_all_settings, get_shared_products, get_all_products,
    get_all_products_merged, get_saved_dates, get_daily_orders,
    set_setting,
)
from utils import fmt

# ── 캐시 wrapper (페이지 모듈에 주입) ──────────────────────
@st.cache_data(ttl=180, show_spinner=False)
def cached_shared_products():
    """공유 제품 DB (3분 캐시)"""
    return get_shared_products()


@st.cache_data(ttl=60, show_spinner=False)
def cached_merged(username: str):
    """병합된 제품 데이터 (60초 캐시)"""
    return get_all_products_merged(username)


@st.cache_data(ttl=60, show_spinner=False)
def cached_user_products(username: str):
    """사용자 제품 (60초 캐시)"""
    return get_all_products(username)


@st.cache_data(ttl=30, show_spinner=False)
def cached_saved_dates(username: str):
    """저장된 주문 날짜 목록 (30초 캐시)"""
    return get_saved_dates(username)


@st.cache_data(ttl=30, show_spinner=False)
def cached_daily_orders(username: str, order_date: str):
    """일별 주문 조회 (30초 캐시)"""
    return get_daily_orders(username, order_date)


def invalidate_data_cache():
    """데이터 변경 시 호출 — 모든 데이터 캐시 일괄 무효화"""
    cached_shared_products.clear()
    cached_merged.clear()
    cached_user_products.clear()
    cached_saved_dates.clear()
    cached_daily_orders.clear()


# ── 페이지 모듈 임포트 ───────────────────────────────────
# plotly 의존 모듈(automation_page, rank_check_page)은 lazy import로 첫 진입 시 1회만 로드
from pages_lib import (
    home_page, order_upload_page, tracking_page, receipt_page,
    profit_calc_page, settings_page, accounting_page,
    product_db_page, admin_page, naver_register_page,
    guide_page, settlement_page, cafe24_page, inventory_page,
)

# 페이지 모듈에 캐시 헬퍼 주입 (페이지 모듈이 동일한 캐시 인스턴스 공유)
def _inject_cache_helpers(_mod):
    if hasattr(_mod, '_set_cache_helpers') and not getattr(_mod, '_cache_injected', False):
        _mod._set_cache_helpers(
            cached_shared_products, cached_user_products, cached_merged,
            invalidate_data_cache,
            cached_saved_dates=cached_saved_dates,
            cached_daily_orders=cached_daily_orders,
        )
        _mod._cache_injected = True

for _mod in (
    order_upload_page, tracking_page, receipt_page, profit_calc_page,
    product_db_page, admin_page, naver_register_page,
):
    _inject_cache_helpers(_mod)


# ── 초기화 ──────────────────────────────────────────────
init_auth_db()


def _get_qparam(key, default=''):
    return st.query_params.get(key, default)


def _set_qparam(key, value):
    st.query_params[key] = value


def _clear_qparams():
    st.query_params.clear()


# ── 카페24 OAuth 콜백 (redirect_uri=https://cocobiz.shop/?code=..&state=sid) ──
#   state에 로그인 sid를 실어 보냈으므로 이를 이용해 사용자 복원 후 토큰 저장.
_cf_code = _get_qparam('code')
_cf_state = _get_qparam('state')
if _cf_code and _cf_state:
    try:
        import cafe24_api
        _cf_user = get_session_user(_cf_state)
        if _cf_user:
            _cf_set = get_all_settings(_cf_user)
            _tok, _terr = cafe24_api.exchange_code_for_token(
                _cf_set.get('cafe24_mall_id', ''),
                _cf_set.get('cafe24_client_id', ''),
                _cf_set.get('cafe24_client_secret', ''),
                _cf_code)
            if _tok and not _terr:
                set_setting(_cf_user, 'cafe24_access_token', _tok.get('access_token', ''))
                set_setting(_cf_user, 'cafe24_refresh_token', _tok.get('refresh_token', ''))
                set_setting(_cf_user, 'cafe24_token_expires_at', _tok.get('expires_at', ''))
                st.session_state['_cafe24_auth_msg'] = "✅ 카페24 인증 완료 — 주문 수집·가격 수정 사용 가능"
            else:
                st.session_state['_cafe24_auth_msg'] = f"❌ 카페24 인증 실패: {_terr}"
        _clear_qparams()
        if _cf_user:
            _set_qparam('sid', _cf_state)
    except Exception as _cfe:
        st.session_state['_cafe24_auth_msg'] = f"❌ 카페24 인증 오류: {_cfe}"
        _clear_qparams()
    st.rerun()


# ── 로컬 설치형: 1-PC 라이선스 인증 (웹 모드는 건너뜀) ──
try:
    import license_client as _lic
    if _lic.is_local_mode() and not st.session_state.get('_license_ok'):
        _lk = _lic.get_stored_key()
        _lres = _lic.verify_license(_lk) if _lk else {"ok": False, "code": "no_key"}
        if _lres.get("ok"):
            st.session_state['_license_ok'] = True
            st.session_state['_license_user'] = _lres.get('username') or 'local'
            st.session_state['_license_display'] = _lres.get('display') or _lres.get('username') or 'local'
        else:
            st.title("🔑 프로그램 활성화")
            st.caption("이 PC에서 최초 1회 라이선스키 활성화가 필요합니다. (1키 = 1PC)")
            if _lres.get("code") and _lres.get("code") != "no_key":
                st.error(_lres.get("message", "인증 실패"))
            _ik = st.text_input("라이선스키 입력", placeholder="COCO-XXXX-XXXX-XXXX", key="_lic_input")
            if st.button("활성화", type="primary", key="_lic_activate"):
                _vr = _lic.verify_license((_ik or '').strip())
                if _vr.get("ok"):
                    _lic.save_key((_ik or '').strip())
                    st.session_state['_license_ok'] = True
                    st.success("✅ 활성화 완료!")
                    st.rerun()
                else:
                    st.error(_vr.get("message", "활성화 실패"))
            st.caption(f"내 PC ID: {_lic.get_machine_id()[:18]}…  (관리자 문의 시 전달)")
            st.stop()
except Exception:
    pass  # 라이선스 모듈/네트워크 오류 시 (웹 모드 등) 무시


# ── 로그인 상태 체크 ─────────────────────────────────────
if 'user' not in st.session_state:
    st.session_state['user'] = None

# 로컬 설치형: 라이선스 계정으로 자동 로그인 (회원가입/비번 불필요)
if st.session_state['user'] is None and st.session_state.get('_license_ok'):
    try:
        _lu = st.session_state.get('_license_user') or 'local'
        _ld = st.session_state.get('_license_display') or _lu
        st.session_state['user'] = ensure_local_user(_lu, _ld)
    except Exception:
        pass

if st.session_state['user'] is None:
    # 자동 로그인 — query param의 sid 토큰
    _sid = _get_qparam('sid') or ''
    if _sid:
        _auto_username = get_session_user(_sid)
        if _auto_username:
            _auto_user = get_user_info(_auto_username)
            if _auto_user:
                st.session_state['user'] = _auto_user
                st.session_state['_sid'] = _sid
                init_user_db(_auto_username)
                st.rerun()
        else:
            _clear_qparams()

    # 로그인 UI
    st.markdown("<h1 style='text-align:center;margin-top:60px'>📦 costcobiz</h1>",
                unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.6, 1])
    with col2:
        allow_signup = get_global_setting('allow_signup', '1')
        tab_labels = ["🔐 로그인", "📝 회원가입"] if allow_signup == '1' else ["🔐 로그인"]
        tabs = st.tabs(tab_labels)

        with tabs[0]:
            with st.form("login_form"):
                username = st.text_input("아이디")
                password = st.text_input("비밀번호", type="password")
                remember_me = st.checkbox("자동 로그인 (30일간 유지)", value=True)
                if st.form_submit_button("로그인", use_container_width=True, type="primary"):
                    result = check_login(username, password)
                    if result and isinstance(result, dict):
                        st.session_state['user'] = result
                        init_user_db(result['username'])
                        if remember_me:
                            _token = create_session(result['username'], days=30)
                            st.session_state['_sid'] = _token
                            _set_qparam('sid', _token)
                        st.rerun()
                    else:
                        st.error("로그인 실패")

        if allow_signup == '1' and len(tabs) > 1:
            with tabs[1]:
                with st.form("signup_form"):
                    reg_id = st.text_input("아이디")
                    reg_name = st.text_input("이름")
                    reg_pw = st.text_input("비밀번호", type="password")
                    if st.form_submit_button("회원가입 신청", use_container_width=True):
                        ok, status = register_user(reg_id, reg_pw, reg_name)
                        if ok:
                            st.success("신청 완료")
                            init_user_db(reg_id)
                        else:
                            st.error("이미 존재하는 아이디")
    st.stop()


# ── 로그인 후 ────────────────────────────────────────────
user = st.session_state['user']
USERNAME = user['username']
IS_ADMIN = user['is_admin']
settings = get_all_settings(USERNAME)

# 카페24 인증 콜백 결과 알림 (OAuth 리다이렉트 처리 후)
_cf_msg = st.session_state.pop('_cafe24_auth_msg', None)
if _cf_msg:
    st.toast(_cf_msg, icon="🛒")


# 사이드바 상단 (로고 + 사용자 정보 + 로그아웃)
with st.sidebar:
    st.markdown(f"""
        <style>
            [data-testid="stSidebarContent"] > div:first-child {{
                display: flex;
                flex-direction: column;
            }}
            .sidebar-top-box {{
                order: -1;
                padding: 10px 15px 14px 15px;
                border-bottom: 1px solid rgba(151, 151, 151, 0.2);
                margin-bottom: 10px;
            }}
            .logo-text {{
                font-size: 1.3rem;
                font-weight: 800;
                color: #E31837;
                margin-bottom: 5px;
            }}
            .user-info-text {{
                color: #555;
                font-size: 0.85rem;
            }}
            .sidebar-cost-info {{
                font-size: 0.8rem;
                color: #888;
                padding: 8px 15px 0 15px;
            }}
        </style>
        <div class="sidebar-top-box">
            <div class="logo-text">📦 costcobiz</div>
            <div class="user-info-text">
                👤 <b>{user['display_name']}</b> ({USERNAME})
            </div>
        </div>
    """, unsafe_allow_html=True)

    if st.button("🚪 로그아웃", use_container_width=True):
        _sid = st.session_state.get('_sid')
        if _sid:
            delete_session(_sid)
        _clear_qparams()
        st.session_state.clear()
        st.rerun()

    # 사이드바 하단 — 설정 미리보기
    ship = settings.get('shipping_cost') or 1800
    box = settings.get('box_cost') or 300
    st.markdown(
        f'<div class="sidebar-cost-info">택배비: {fmt(int(ship))}원 | 박스비: {fmt(int(box))}원</div>',
        unsafe_allow_html=True,
    )


# ── 페이지 라우터 (st.navigation 사용) ──────────────────────
def run_home():
    home_page.render(USERNAME, IS_ADMIN)


def run_order_upload():
    order_upload_page.render(USERNAME, IS_ADMIN, settings)


def run_tracking():
    tracking_page.render(USERNAME, IS_ADMIN, settings)


def run_profit_calc():
    profit_calc_page.render(USERNAME, IS_ADMIN, settings)


def run_accounting():
    accounting_page.render(USERNAME)


def run_settlement_match():
    settlement_page.render(USERNAME, IS_ADMIN, settings)


def run_rank_check():
    from pages_lib import rank_check_page  # lazy: plotly import 무거움
    _inject_cache_helpers(rank_check_page)
    rank_check_page.render(USERNAME, IS_ADMIN, settings)


def run_settings():
    settings_page.render(USERNAME, lambda k, d='': settings.get(k) or d)


def run_product_db():
    product_db_page.render(USERNAME, IS_ADMIN, settings)


def run_naver_register():
    naver_register_page.render(USERNAME, IS_ADMIN, settings)


def run_inventory():
    inventory_page.render(USERNAME, IS_ADMIN, settings)


def run_cafe24():
    cafe24_page.render(USERNAME, IS_ADMIN, settings)


def run_automation():
    from pages_lib import automation_page  # lazy: plotly + subprocess 무거움
    _inject_cache_helpers(automation_page)
    automation_page.render(USERNAME, IS_ADMIN, settings)


def run_admin():
    admin_page.render(USERNAME, IS_ADMIN, settings)


# 페이지 정의 (섹션 그룹)
_pages = {
    "운영": [
        st.Page(run_home,         title="홈",          icon=":material/home:", default=True),
        st.Page(run_order_upload, title="일일 주문 수집", icon=":material/receipt_long:"),
        st.Page(run_tracking,     title="송장번호",    icon=":material/local_shipping:"),
        st.Page(run_profit_calc,  title="수익 계산",   icon=":material/payments:"),
        st.Page(run_settlement_match, title="정산 매칭", icon=":material/account_balance_wallet:"),
        st.Page(run_accounting,   title="세무회계",    icon=":material/calculate:"),
    ],
    "상품 관리": [
        st.Page(run_product_db,     title="제품 DB",     icon=":material/inventory_2:"),
        st.Page(run_inventory,      title="재고 관리",   icon=":material/warehouse:"),
        st.Page(run_naver_register, title="네이버 등록", icon=":material/storefront:"),
        st.Page(run_rank_check,     title="순위 체크",   icon=":material/trending_up:"),
    ],
    "자동화": [
        st.Page(run_automation, title="자동화", icon=":material/smart_toy:"),
    ],
    "설정": [
        st.Page(run_settings, title="설정", icon=":material/settings:"),
    ],
}

if IS_ADMIN:
    # 카페24 메뉴 — 네이버 등록 바로 아래(상품 관리 그룹), 관리자 전용
    # ※ 재고 관리가 1번으로 들어와 네이버 등록이 2번 → 그 아래는 3번
    _pages["상품 관리"].insert(3, st.Page(run_cafe24, title="카페24", icon=":material/sync_alt:"))
    _pages["관리자"] = [st.Page(run_admin, title="관리자", icon=":material/admin_panel_settings:")]

# 페이지 이동 시 sid 보존 — st.navigation()이 URL 경로를 바꿔도 query param 유지
_persist_sid = st.session_state.get('_sid')
if _persist_sid and _get_qparam('sid') != _persist_sid:
    _set_qparam('sid', _persist_sid)

# (localStorage 강제 정리 스크립트 제거 — 사이드바 상태와 충돌해 메뉴가 사라지는 문제 유발)

# 라우팅 실행 — Streamlit이 사이드바 네비게이션 메뉴 자동 생성 + 페이지 전환 시 잔상 자동 제거
pg = st.navigation(_pages)

# 홈 퀵버튼(_pending_tab) → 실제 페이지 전환. st.navigation은 이 값을 자동 소비하지 않으므로
# 여기서 친숙한 라벨을 페이지 제목에 매핑해 st.switch_page 로 이동시킨다. (홈 버튼 미작동 수정)
_TAB_TITLE = {
    "📋 주문 업로드": "일일 주문 수집",
    "🧾 영수증 등록": "수익 계산",
    "📈 순위 체크":   "순위 체크",
    "🤖 자동화":      "자동화",
}
_title_to_page = {getattr(p, 'title', ''): p for _lst in _pages.values() for p in _lst}
_pending_tab = st.session_state.pop('_pending_tab', None)
if _pending_tab:
    _target_pg = _title_to_page.get(_TAB_TITLE.get(_pending_tab, _pending_tab))
    if _target_pg is not None:
        st.switch_page(_target_pg)

# ── 저장하지 않은 변경 경고 (메뉴 이동 직후 모달) ──
# Streamlit은 이동 자체를 막지 못하므로, 떠난 페이지에 미저장이 있으면 이동 직후 1회 경고.
# (데이터는 세션에 남아 있어 돌아가서 저장 가능)
try:
    _cur_page = getattr(pg, 'title', '') or ''
    _unsaved_pages = st.session_state.get('_unsaved_pages', {}) or {}
    _last_page = st.session_state.get('_last_page')
    if _last_page and _last_page != _cur_page and _last_page in _unsaved_pages:
        _leaving_page = _last_page
        _leaving_msg = _unsaved_pages.get(_leaving_page) or \
            "저장하지 않으면 새로고침 시 사라집니다. 돌아가서 저장하세요."

        @st.dialog("⚠️ 저장하지 않은 변경")
        def _unsaved_warn():
            st.warning(f"**{_leaving_page}** 페이지에 저장하지 않은 내용이 있습니다.")
            st.caption(_leaving_msg)
            if st.button("확인 (그대로 진행)", type="primary", use_container_width=True):
                st.rerun()

        _unsaved_warn()
    st.session_state['_last_page'] = _cur_page
except Exception:
    pass

# ── 상단 ☰ 메뉴 (모바일에서 좌측 사이드바 접근이 어려울 때 페이지 이동용) ──
#    · 데스크톱에서는 CSS(@media ≥768px)로 숨김 → 좌측 사이드바 사용
#    · 라벨에 현재 페이지를 포함 → 페이지 이동 시 expander가 새로 생성되어 자동 접힘
#    · 단일 열로 렌더 → 사이드바(웹)와 동일한 순서 유지
with st.expander(f"☰ 메뉴 · 현재: {getattr(pg, 'title', '')}", expanded=False):
    st.markdown('<span class="mnav-flag"></span>', unsafe_allow_html=True)
    for _lst in _pages.values():
        for _mp in _lst:
            try:
                st.page_link(_mp)
            except Exception:
                pass

pg.run()
