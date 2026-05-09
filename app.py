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

try:
    import extra_streamlit_components as stx
    _cmgr = stx.CookieManager(key="_cmgr")
    HAS_COOKIE = True
except Exception:
    _cmgr = None
    HAS_COOKIE = False

# ── 기본 설정 ─────────────────────────────────────────────
APP_TITLE = "costcobiz"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide", menu_items={"About": "costcobiz"})

# 네비게이션 캐시 버전 강제 초기화 (메뉴명 변경 시 구 브라우저 캐시 제거)
import streamlit.components.v1 as _stc
_NAV_VER = "20260509v2"
_stc.html(f"""<script>
(function(){{
  var v = '{_NAV_VER}';
  if (window.localStorage.getItem('_nav_ver') !== v) {{
    Object.keys(window.localStorage)
      .filter(function(k){{ return k.indexOf('streamlit') === 0; }})
      .forEach(function(k){{ window.localStorage.removeItem(k); }});
    window.localStorage.setItem('_nav_ver', v);
    window.location.reload();
  }}
}})();
</script>""", height=0, scrolling=False)

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
    init_auth_db, check_login, get_global_setting, register_user,
    get_user_info, create_session, get_session_user, delete_session,
    init_user_db, get_all_settings, get_shared_products, get_all_products,
    get_all_products_merged, get_saved_dates, get_daily_orders,
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
from pages_lib import (
    home_page, order_upload_page, tracking_page, receipt_page,
    profit_calc_page, dashboard_page, rank_check_page, settings_page,
    product_db_page, admin_page, naver_register_page, automation_page,
    guide_page,
)

# 페이지 모듈에 캐시 헬퍼 주입 (페이지 모듈이 동일한 캐시 인스턴스 공유)
for _mod in (
    order_upload_page, tracking_page, receipt_page, profit_calc_page,
    rank_check_page, product_db_page, admin_page, naver_register_page,
    automation_page,
):
    if hasattr(_mod, '_set_cache_helpers'):
        _mod._set_cache_helpers(
            cached_shared_products, cached_user_products, cached_merged,
            invalidate_data_cache,
            cached_saved_dates=cached_saved_dates,
            cached_daily_orders=cached_daily_orders,
        )


# ── 초기화 ──────────────────────────────────────────────
init_auth_db()


def _get_qparam(key, default=''):
    return st.query_params.get(key, default)


def _set_qparam(key, value):
    st.query_params[key] = value


def _clear_qparams():
    st.query_params.clear()


# ── 로그인 상태 체크 ─────────────────────────────────────
if 'user' not in st.session_state:
    st.session_state['user'] = None

if st.session_state['user'] is None:
    # 자동 로그인 — 쿠키 우선, 없으면 query param 폴백
    _sid = ((_cmgr.get('_cbsid') if HAS_COOKIE else None)
            or _get_qparam('sid')
            or '')
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
            if HAS_COOKIE:
                _cmgr.delete('_cbsid')
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
                            if HAS_COOKIE:
                                _cmgr.set('_cbsid', _token, max_age=30 * 24 * 3600)
                            else:
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
        if HAS_COOKIE:
            _cmgr.delete('_cbsid')
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
    home_page.render(USERNAME)


def run_order_upload():
    order_upload_page.render(USERNAME, IS_ADMIN, settings)


def run_tracking():
    tracking_page.render(USERNAME, IS_ADMIN, settings)


def run_profit_calc():
    profit_calc_page.render(USERNAME, IS_ADMIN, settings)


def run_dashboard():
    dashboard_page.render(USERNAME)


def run_rank_check():
    rank_check_page.render(USERNAME, IS_ADMIN, settings)


def run_settings():
    settings_page.render(USERNAME, lambda k, d='': settings.get(k) or d)


def run_product_db():
    product_db_page.render(USERNAME, IS_ADMIN, settings)


def run_naver_register():
    naver_register_page.render(USERNAME, IS_ADMIN, settings)


def run_automation():
    automation_page.render(USERNAME, IS_ADMIN, settings)


def run_admin():
    admin_page.render(USERNAME, IS_ADMIN, settings)


# 페이지 정의 (섹션 그룹)
_pages = {
    "운영": [
        st.Page(run_home,         title="홈",          icon="🏠", default=True),
        st.Page(run_order_upload, title="주문 업로드", icon="📋"),
        st.Page(run_tracking,     title="송장번호",    icon="📮"),
        st.Page(run_profit_calc,  title="수익 계산",   icon="💰"),
        st.Page(run_dashboard,    title="대시보드",    icon="📊"),
    ],
    "상품 관리": [
        st.Page(run_product_db,     title="제품 DB",     icon="📦"),
        st.Page(run_naver_register, title="네이버 등록", icon="🛍"),
        st.Page(run_rank_check,     title="순위 체크",   icon="📈"),
    ],
    "자동화": [
        st.Page(run_automation, title="자동화", icon="🤖"),
    ],
    "설정": [
        st.Page(run_settings, title="설정", icon="⚙️"),
    ],
}

if IS_ADMIN:
    _pages["관리자"] = [st.Page(run_admin, title="관리자", icon="👑")]

# 라우팅 실행 — Streamlit이 사이드바 네비게이션 메뉴 자동 생성 + 페이지 전환 시 잔상 자동 제거
pg = st.navigation(_pages)
pg.run()
