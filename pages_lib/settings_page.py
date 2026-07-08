"""⚙️ 설정 페이지 — app.py에서 분리된 첫 번째 멀티페이지 모듈."""
import streamlit as st
import pandas as pd

from db import (
    set_setting, change_password, get_user_db,
)
from utils import fmt
from pages_lib import guide_page

try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False
    naver_api = None

try:
    import coupang_api
    HAS_COUPANG_API = True
except ImportError:
    HAS_COUPANG_API = False
    coupang_api = None


def _render_settings_content(USERNAME: str, _gs):
    # ── 엑셀 비밀번호 ─────────────────────────────────────
    st.subheader("🔓 엑셀 비밀번호")
    st.caption("네이버 스마트스토어에서 다운받은 엑셀 파일의 비밀번호를 저장하면 자동으로 해제됩니다.")
    current_pw = _gs('excel_password')
    new_pw = st.text_input("엑셀 비밀번호", value=current_pw, type="password", key="excel_pw_input")
    if st.button("비밀번호 저장", key="save_pw"):
        set_setting(USERNAME, 'excel_password', new_pw)
        st.success("✅ 엑셀 비밀번호 저장 완료!")

    # ── 네이버 커머스 API ──────────────────────────────────
    st.divider()
    st.subheader("🔗 네이버 커머스 API")
    st.caption("커머스API센터에서 발급받은 키를 입력하면 주문 자동 조회 + 발송 자동 처리가 가능합니다.")
    api_id_val = _gs('api_client_id')
    api_secret_val = _gs('api_client_secret')
    c1, c2 = st.columns(2)
    new_api_id = c1.text_input("애플리케이션 ID", value=api_id_val, key="api_id_input")
    new_api_secret = c2.text_input("애플리케이션 시크릿", value=api_secret_val, type="password", key="api_secret_input")
    new_channel_seller_id = st.text_input(
        "API 연동용 판매자ID (채널 API)",
        value=_gs('channel_seller_id'),
        placeholder="예: ncp_1o30xr_01  (스마트스토어 센터 > 외부서비스 연동 > API 관리)",
        key="channel_seller_id_input"
    )
    st.caption("상품 목록 전체 조회 시 사용됩니다. 스마트스토어 센터 → 외부서비스 연동 → API 관리에서 확인")
    if st.button("API 키 저장", key="save_api"):
        set_setting(USERNAME, 'api_client_id', new_api_id)
        set_setting(USERNAME, 'api_client_secret', new_api_secret)
        set_setting(USERNAME, 'channel_seller_id', new_channel_seller_id.strip())
        st.success("✅ API 키 저장 완료!")
        if HAS_NAVER_API and new_api_id and new_api_secret:
            with st.spinner("API 연결 테스트 중..."):
                token, err = naver_api.get_token(new_api_id, new_api_secret)
            if token:
                st.success("✅ API 연결 성공!")
            else:
                st.error(f"❌ API 연결 실패: {err}")
    if not HAS_NAVER_API:
        st.warning("naver_api.py 파일이 프로그램 폴더에 없습니다. 관리자에게 문의하세요.")

    # ── 네이버 상품 등록 기본값 ────────────────────────────
    st.divider()
    st.subheader("🛍 네이버 상품 등록 기본값")
    st.caption("제품 DB에서 '🛍등록' 버튼 클릭 시 자동 입력되는 기본값입니다.")
    _nc1, _nc2 = st.columns(2)
    _def_cat = _nc1.text_input("기본 카테고리 ID",
                                value=_gs('naver_default_category'),
                                placeholder="예: 50000803",
                                key="set_naver_cat")
    _def_as  = _nc2.text_input("A/S 전화번호",
                                value=_gs('naver_as_tel'),
                                placeholder="010-0000-0000",
                                key="set_naver_as")
    if st.button("상품 등록 기본값 저장", key="save_naver_reg_defaults"):
        set_setting(USERNAME, 'naver_default_category', _def_cat.strip())
        set_setting(USERNAME, 'naver_as_tel', _def_as.strip())
        st.success("✅ 저장 완료!")

    # ── 쿠팡 Wing API ─────────────────────────────────────
    st.divider()
    st.subheader("🛒 쿠팡 Wing Open API")
    st.caption("쿠팡 Wing에서 발급한 키를 입력하면 주문 자동 조회가 가능합니다.")

    cq_access = _gs('coupang_access_key')
    cq_secret = _gs('coupang_secret_key')
    cq_vendor = _gs('coupang_vendor_id')

    cq1, cq2 = st.columns(2)
    new_cq_access = cq1.text_input("Access Key",  value=cq_access, key="cq_access_in")
    new_cq_secret = cq2.text_input("Secret Key",  value=cq_secret, type="password", key="cq_secret_in")
    new_cq_vendor = st.text_input(
        "Vendor ID",
        value=cq_vendor,
        placeholder="예: A00012345  (Wing 대시보드 URL의 vendorId 값)",
        key="cq_vendor_in",
    )
    st.caption("발급 방법: wing.coupang.com → 개발자 센터 → Open API → Access Key 발급")

    if st.button("쿠팡 API 저장", key="save_coupang"):
        set_setting(USERNAME, 'coupang_access_key', new_cq_access.strip())
        set_setting(USERNAME, 'coupang_secret_key', new_cq_secret.strip())
        set_setting(USERNAME, 'coupang_vendor_id',  new_cq_vendor.strip())
        st.success("✅ 쿠팡 API 키 저장 완료!")
        if HAS_COUPANG_API and new_cq_access and new_cq_secret and new_cq_vendor:
            with st.spinner("API 연결 테스트 중..."):
                ok, msg = coupang_api.test_connection(
                    new_cq_access.strip(), new_cq_secret.strip(), new_cq_vendor.strip()
                )
            if ok:
                st.success(f"✅ {msg}")
            else:
                st.error(f"❌ {msg}")

    # ── 카카오톡 알림 ─────────────────────────────────────
    st.divider()
    st.subheader("📱 카카오톡 알림")
    st.caption("장보기 목록을 카카오톡(나에게 보내기)으로 전송합니다.")

    kakao_api_key = _gs('kakao_api_key')
    kakao_secret = _gs('kakao_client_secret')
    kakao_token = _gs('kakao_access_token')
    kakao_refresh = _gs('kakao_refresh_token')

    new_kakao_api_key = st.text_input("REST API 키", value=kakao_api_key, key="kakao_api_key_input",
                                       help="카카오 개발자 콘솔 > 앱 > 플랫폼 키 > REST API 키")
    new_kakao_secret = st.text_input(
        "Client Secret (선택)", value=kakao_secret, key="kakao_client_secret_input",
        type="password",
        help="앱에 Client Secret이 '사용함'이면 입력하세요 (카카오 로그인 > 보안/고급). "
             "'사용 안 함'이면 비워두면 됩니다. 미입력+사용함이면 KOE010 오류 발생."
    )

    if st.button("REST API 키 저장", key="save_kakao_api_key"):
        set_setting(USERNAME, 'kakao_api_key', new_kakao_api_key.strip())
        set_setting(USERNAME, 'kakao_client_secret', new_kakao_secret.strip())
        st.success("✅ REST API 키 저장!")
        kakao_api_key = new_kakao_api_key.strip()
        kakao_secret = new_kakao_secret.strip()

    if kakao_api_key:
        # 인가 코드 발급 링크
        auth_url = f"https://kauth.kakao.com/oauth/authorize?client_id={kakao_api_key}&redirect_uri=http://localhost&response_type=code&scope=talk_message"
        st.markdown(f"**1단계:** [여기를 클릭하여 카카오 로그인]({auth_url}) → 동의 후 브라우저 주소창에서 `code=` 뒤의 값을 복사하세요.")
        st.caption("예: http://localhost?code=**abc123xyz** → `abc123xyz` 부분을 아래에 붙여넣기")

        auth_code = st.text_input("2단계: 인가 코드 붙여넣기", key="kakao_auth_code", placeholder="인가 코드를 여기에 붙여넣으세요")
        if st.button("🔑 토큰 발급받기", key="kakao_get_token"):
            if auth_code and HAS_NAVER_API:
                with st.spinner("토큰 발급 중..."):
                    access, refresh, err = naver_api.get_kakao_token_by_code(
                        kakao_api_key, auth_code, client_secret=kakao_secret)
                if access:
                    set_setting(USERNAME, 'kakao_access_token', access)
                    set_setting(USERNAME, 'kakao_refresh_token', refresh or '')
                    st.success(f"✅ 토큰 발급 성공! (길이: {len(access)}자)")
                    kakao_token = access
                    kakao_refresh = refresh or ''
                else:
                    st.error(f"❌ {err}")
            else:
                st.warning("인가 코드를 입력해주세요.")

    # 현재 토큰 상태 표시
    if kakao_token:
        st.info(f"✅ 액세스 토큰 설정됨 (길이: {len(kakao_token)}자)")
    else:
        st.warning("⚠️ 아직 토큰이 없습니다. 위의 과정을 진행해주세요.")

    if st.button("🔔 카카오톡 테스트 전송", key="test_kakao"):
        if kakao_token and HAS_NAVER_API:
            ok, err = naver_api.send_kakao(kakao_token, "✅ 코스트코핫딜 알림 테스트 성공!",
                                            rest_api_key=kakao_api_key, refresh_token=kakao_refresh,
                                            client_secret=kakao_secret)
            if ok:
                if err and "__TOKEN_REFRESHED__" in str(err):
                    parts = str(err).replace("__TOKEN_REFRESHED__", "").split("||")
                    set_setting(USERNAME, 'kakao_access_token', parts[0])
                    if len(parts) > 1:
                        set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                    st.success("✅ 카카오톡 전송 성공! (토큰 자동 갱신됨)")
                else:
                    st.success("✅ 카카오톡 전송 성공!")
            else:
                st.error(f"❌ {err}")
        else:
            st.warning("토큰을 먼저 발급받아 주세요.")

    # ── 택배사 설정 ───────────────────────────────────────
    st.divider()
    st.subheader("🚛 택배사 설정")
    current_courier = _gs('default_courier') or 'CJGLS'
    courier_options = {"CJ대한통운": "CJGLS", "롯데택배": "HYUNDAI"}
    sel_courier = st.selectbox("기본 택배사", list(courier_options.keys()),
                                index=0 if current_courier == 'CJGLS' else 1)

    st.caption("CJ대한통운 API 접수 설정 (자동 송장 발급용)")
    cj_id = _gs('cj_api_id')
    cj_pw = _gs('cj_api_pw')
    cj_acc = _gs('cj_account_no')
    col1, col2, col3 = st.columns(3)
    new_cj_id = col1.text_input("CJ ID", value=cj_id)
    new_cj_pw = col2.text_input("CJ PW", value=cj_pw, type="password")
    new_cj_acc = col3.text_input("고객번호", value=cj_acc)

    if st.button("택배사 설정 저장", key="save_courier"):
        set_setting(USERNAME, 'default_courier', courier_options[sel_courier])
        set_setting(USERNAME, 'cj_api_id', new_cj_id)
        set_setting(USERNAME, 'cj_api_pw', new_cj_pw)
        set_setting(USERNAME, 'cj_account_no', new_cj_acc)
        st.success(f"✅ 택배사 설정 저장 완료! (기본: {sel_courier})")

    # ── 🤖 AI (Claude) 설정 ───────────────────────────────
    st.divider()
    st.subheader("🤖 AI 설정 (Claude)")
    st.caption("Anthropic API 키를 등록하면 정산 매칭 탭에서 **일일 정산 AI 브리핑**을 사용할 수 있습니다. "
               "발급: [console.anthropic.com](https://console.anthropic.com) → API Keys")
    _ai_c1, _ai_c2 = st.columns([3, 1])
    _new_ai_key = _ai_c1.text_input("Anthropic API 키", value=_gs('anthropic_api_key'),
                                    type="password", key="ai_key_in",
                                    placeholder="sk-ant-...")
    _ai_auto = _ai_c2.checkbox("정산수집 후 카톡 자동발송", key="ai_auto_in",
                               value=(_gs('ai_briefing_auto') == '1'),
                               help="자동화 주문수집·정산매칭 후 AI 브리핑을 카카오톡으로 발송")
    if st.button("AI 설정 저장", key="save_ai"):
        set_setting(USERNAME, 'anthropic_api_key', _new_ai_key.strip())
        set_setting(USERNAME, 'ai_briefing_auto', '1' if _ai_auto else '0')
        st.success("✅ AI 설정 저장!")

    # ── 네이버 Open API ───────────────────────────────────
    st.divider()
    st.subheader("🔍 네이버 Open API (순위 체크용)")
    st.caption("developers.naver.com에서 쇼핑 검색 API를 신청하면 발급받을 수 있습니다. 네이버 커머스 API 키와 별개입니다.")
    _oc1, _oc2 = st.columns(2)
    _new_open_cid  = _oc1.text_input("Client ID",     value=_gs('naver_open_client_id'),  key="open_cid_in")
    _new_open_csec = _oc2.text_input("Client Secret", value=_gs('naver_open_client_secret'), type="password", key="open_csec_in")
    if st.button("Open API 저장", key="save_open_api"):
        set_setting(USERNAME, 'naver_open_client_id',     _new_open_cid)
        set_setting(USERNAME, 'naver_open_client_secret', _new_open_csec)
        st.success("✅ Open API 키 저장 완료!")

    # ── 네이버 검색광고 API (키워드 검색량·연관검색어용) ─────────
    st.divider()
    st.subheader("📊 네이버 검색광고 API (키워드 검색량·연관검색어용)")
    st.caption("searchad.naver.com(광고주센터) > 도구 > **API 사용 관리**에서 발급. "
               "월간 검색량·연관검색어 조회에 사용됩니다. Open API·커머스 API와 별개입니다.")
    _ac1, _ac2, _ac3 = st.columns(3)
    _new_ad_key  = _ac1.text_input("API_KEY (액세스라이선스)", value=_gs('naver_ad_api_key'), key="ad_key_in")
    _new_ad_sec  = _ac2.text_input("SECRET_KEY (비밀키)", value=_gs('naver_ad_secret'), type="password", key="ad_sec_in")
    _new_ad_cust = _ac3.text_input("고객 ID (CUSTOMER_ID)", value=_gs('naver_ad_customer_id'), key="ad_cust_in")
    if st.button("검색광고 API 저장", key="save_ad_api"):
        set_setting(USERNAME, 'naver_ad_api_key',     _new_ad_key.strip())
        set_setting(USERNAME, 'naver_ad_secret',      _new_ad_sec.strip())
        set_setting(USERNAME, 'naver_ad_customer_id', _new_ad_cust.strip())
        st.success("✅ 검색광고 API 키 저장 완료!")

    # ── 고정 비용 ─────────────────────────────────────────
    st.divider()
    st.subheader("📦 고정 비용")
    c1, c2, c3 = st.columns(3)
    new_ship = c1.number_input("택배비 (원)", value=int(_gs('shipping_cost') or 1800), step=100)
    new_box = c2.number_input("박스비 (원)", value=int(_gs('box_cost') or 300), step=50)
    new_naver_ship_fee_rate = c3.number_input(
        "네이버 배송비 수수료율 (%)",
        value=float(_gs('naver_ship_fee_commission_rate') or 4.0),
        min_value=0.0, max_value=20.0, step=0.1,
        help="네이버가 고객결제 배송비에 부과하는 수수료율 (보통 3~5%). "
             "실정산 배송비 = 고객결제 배송비 × (1 - 수수료율) 로 수익계산에 반영"
    )
    if st.button("비용 저장", key="save_cost"):
        set_setting(USERNAME, 'shipping_cost', new_ship)
        set_setting(USERNAME, 'box_cost', new_box)
        set_setting(USERNAME, 'naver_ship_fee_commission_rate', new_naver_ship_fee_rate)
        st.success(
            f"✅ 택배비 {fmt(new_ship)}원, 박스비 {fmt(new_box)}원, "
            f"배송비 수수료율 {new_naver_ship_fee_rate}% 저장"
        )

    # ── 가격 자동 조정 ────────────────────────────────────
    st.divider()
    st.subheader("💰 가격 자동 조정")
    st.caption("적자 상품 감지 시 스마트스토어 판매가를 자동으로 조정합니다.")
    c1, c2 = st.columns(2)
    new_margin = c1.number_input("목표 마진율 (%)", value=int(_gs('target_margin') or 10),
                                  min_value=1, max_value=50, step=1)
    new_max_inc = c2.number_input("최대 인상폭 (%)", value=int(_gs('max_increase_pct') or 20),
                                   min_value=5, max_value=50, step=5)
    st.caption(
        f"예시: 원가 10,000원 + 택배비 {fmt(new_ship)}원 + 박스비 {fmt(new_box)}원 → "
        f"최소 판매가 약 {fmt(int((10000+new_ship+new_box) * (1+new_margin/100) / 0.945 / 100) * 100)}원"
    )
    if st.button("마진 설정 저장", key="save_margin"):
        set_setting(USERNAME, 'target_margin', new_margin)
        set_setting(USERNAME, 'max_increase_pct', new_max_inc)
        st.success(f"✅ 목표 마진 {new_margin}%, 최대 인상폭 {new_max_inc}% 저장")

    # ── 가격 변경 이력 ────────────────────────────────────
    conn = get_user_db(USERNAME)
    history = conn.execute("SELECT * FROM price_history ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()
    if history:
        st.divider()
        st.subheader("📋 가격 변경 이력")
        hdf = pd.DataFrame([dict(h) for h in history])[
            ['created_at', 'product_name', 'old_price', 'new_price',
             'cost_price', 'reason', 'status']
        ]
        hdf.columns = ['일시', '상품명', '변경전', '변경후', '원가', '사유', '상태']
        st.dataframe(hdf, use_container_width=True, hide_index=True)

    # ── 비밀번호 변경 ─────────────────────────────────────
    st.divider()
    st.subheader("🔑 비밀번호 변경")
    new_login_pw = st.text_input("새 로그인 비밀번호", type="password", key="new_login_pw")
    new_login_pw2 = st.text_input("비밀번호 확인", type="password", key="new_login_pw2")
    if st.button("비밀번호 변경", key="change_pw"):
        if new_login_pw and new_login_pw == new_login_pw2:
            change_password(USERNAME, new_login_pw)
            st.success("✅ 비밀번호 변경 완료!")
        elif new_login_pw != new_login_pw2:
            st.error("비밀번호가 일치하지 않습니다.")


def render(USERNAME: str, _gs):
    st.header("⚙️ 설정")
    _tab_settings, _tab_guide = st.tabs(["⚙️ 설정", "📖 설정 가이드"])
    with _tab_settings:
        _render_settings_content(USERNAME, _gs)
    with _tab_guide:
        guide_page.render(USERNAME)
