"""📖 설정 가이드 — 초보자용 네이버·카카오톡 설정 안내."""
import streamlit as st


_STEP_STYLE = (
    "background:#f8f9fa;border-left:4px solid #E31837;padding:12px 16px;"
    "border-radius:0 6px 6px 0;margin:8px 0"
)
_TIP_STYLE = (
    "background:#fff8e1;border-left:4px solid #f39c12;padding:10px 14px;"
    "border-radius:0 6px 6px 0;margin:8px 0;font-size:13px"
)
_WARN_STYLE = (
    "background:#fff0f0;border-left:4px solid #e74c3c;padding:10px 14px;"
    "border-radius:0 6px 6px 0;margin:8px 0;font-size:13px"
)
_OK_STYLE = (
    "background:#f0fff4;border-left:4px solid #27ae60;padding:10px 14px;"
    "border-radius:0 6px 6px 0;margin:8px 0;font-size:13px"
)


def _step(n, title, body=""):
    _body_html = (
        '<br><span style="font-size:13px;color:#444;margin-top:4px;display:block">'
        + body + '</span>'
    ) if body else ""
    st.markdown(
        f'<div style="{_STEP_STYLE}">'
        f'<b style="color:#E31837;font-size:16px">STEP {n}</b> '
        f'<b style="font-size:15px">{title}</b>'
        f'{_body_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _tip(text):
    st.markdown(f'<div style="{_TIP_STYLE}">💡 {text}</div>', unsafe_allow_html=True)


def _warn(text):
    st.markdown(f'<div style="{_WARN_STYLE}">⚠️ {text}</div>', unsafe_allow_html=True)


def _ok(text):
    st.markdown(f'<div style="{_OK_STYLE}">✅ {text}</div>', unsafe_allow_html=True)


def render(USERNAME: str):
    st.header("📖 설정 가이드")
    st.caption("처음 시작하는 분도 따라할 수 있도록 단계별로 안내합니다. 순서대로 진행하세요.")

    st.info(
        "**⚡ 전체 설정 순서**  \n"
        "1️⃣ 네이버 스마트스토어 커머스 API 발급  →  "
        "2️⃣ 카카오톡 REST API 발급 & 토큰 연결  →  "
        "3️⃣ 네이버 Open API 발급 (순위 체크용)  →  "
        "4️⃣ ⚙️ 설정 탭에 각 키 입력 & 저장"
    )

    # ═══════════════════════════════════════════════════════
    st.divider()

    # ── 1. 네이버 커머스 API ──────────────────────────────
    with st.expander("🔗 네이버 커머스 API 설정 가이드 (주문 조회 · 자동 발송처리)", expanded=True):
        st.markdown("주문 자동 조회, 발송처리 자동화에 필요합니다. **스마트스토어 판매자 계정**이 있어야 합니다.")

        st.warning(
            "⚠️ **API 키는 사용자마다 따로 발급받아야 합니다.**\n\n"
            "- 제품·주문 수집은 입력한 **애플리케이션 ID/시크릿**에 연결된 **본인 스토어 상품만** 가져와 **본인 DB**에 저장됩니다.\n"
            "- 다른 사람의 키나 판매자 ID로는 내 상품을 가져올 수 없습니다. **각자 본인 계정으로 로그인 → 본인 키 발급 → 본인 설정 탭에 입력**하세요.\n"
            "- 핵심은 **애플리케이션 ID / 시크릿**입니다. (판매자 ID는 참고용)"
        )

        st.markdown("---")
        _step(1, "네이버 커머스 API 센터 접속",
              "아래 주소를 복사해서 브라우저에 붙여넣기 하세요.")
        st.code("https://apicenter.commerce.naver.com", language=None)

        _step(2, "본인 스마트스토어 계정으로 로그인",
              "<b>반드시 본인의</b> 네이버 스마트스토어 판매자 계정으로 로그인합니다. (이 계정의 상품이 수집됩니다)")

        _step(3, "애플리케이션 등록",
              "상단 메뉴 <b>애플리케이션 관리</b> → <b>애플리케이션 등록</b> 클릭")
        st.markdown(
            """
| 입력 항목 | 입력값 |
|---|---|
| 애플리케이션 이름 | 코스트코핫딜 (자유롭게) |
| 사용 API | ✅ **스마트스토어 API** 체크 |
| Callback URL | `http://localhost` |
""",
            unsafe_allow_html=False,
        )

        _step(4, "Client ID / Client Secret 복사",
              "등록 완료 후 <b>애플리케이션 상세</b> 페이지에서 아래 두 값을 복사하세요.")
        st.markdown(
            """
- **Client ID** → ⚙️ 설정 탭 > 네이버 커머스 API > **애플리케이션 ID** 에 붙여넣기
- **Client Secret** → **애플리케이션 시크릿** 에 붙여넣기
"""
        )

        _step(5, "판매자 채널 ID 확인 (참고용 · 선택)",
              "스마트스토어 센터(smartstore.naver.com) → <b>외부서비스 연동 → API 관리</b>에서 확인")
        st.markdown("- **채널 API 연동용 판매자ID** 항목을 복사 → ⚙️ 설정 탭 > **API 연동용 판매자ID** 에 붙여넣기")
        _warn("판매자 ID는 <b>참고용</b>입니다. 상품·주문 수집은 위 <b>애플리케이션 ID/시크릿</b>으로만 이뤄지며, 판매자 ID를 바꿔도 수집 대상은 바뀌지 않습니다.")

        _step(6, "⚙️ 설정 탭에 입력 후 저장",
              "저장 버튼을 누르면 자동으로 API 연결 테스트가 진행됩니다.")
        _ok("✅ API 연결 성공! 메시지가 뜨면 완료입니다.")
        _warn("연결 실패 시: Client Secret을 다시 확인하거나, 등록한 API 권한에 <b>스마트스토어 API</b>가 체크되어 있는지 확인하세요.")

    # ═══════════════════════════════════════════════════════
    st.divider()

    # ── 2. 카카오톡 알림 ──────────────────────────────────
    with st.expander("📱 카카오톡 알림 설정 가이드 (장보기 목록 · 자동화 알림)", expanded=True):
        st.markdown("자동화 작업 완료 후 카카오톡 **나에게 보내기**로 알림을 받습니다.")

        st.markdown("---")
        _step(1, "카카오 개발자 센터 접속",
              "아래 주소를 복사해서 브라우저에 붙여넣기 하세요.")
        st.code("https://developers.kakao.com", language=None)

        _step(2, "로그인 & 앱 만들기",
              "카카오 계정으로 로그인 → 상단 <b>내 애플리케이션</b> → <b>애플리케이션 추가하기</b>")
        st.markdown(
            """
| 입력 항목 | 입력값 |
|---|---|
| 앱 이름 | 코스트코핫딜 (자유롭게) |
| 사업자명 | 본인 이름 또는 상호 |
"""
        )

        _step(3, "REST API 키 복사",
              "앱 생성 후 <b>앱 키</b> 섹션에서 <b>REST API 키</b>를 복사하세요.")
        st.markdown("- ⚙️ 설정 탭 > 카카오톡 알림 > **REST API 키** 에 붙여넣기 → **REST API 키 저장** 클릭")

        _step(4, "플랫폼 등록",
              "왼쪽 메뉴 <b>앱 설정 → 플랫폼</b> → <b>Web 플랫폼 등록</b>")
        st.markdown(
            """
- **사이트 도메인**: `http://localhost` 입력 후 저장
"""
        )
        _warn("이 단계를 빠뜨리면 토큰 발급이 실패합니다.")

        _step(5, "카카오 로그인 활성화",
              "왼쪽 메뉴 <b>제품 설정 → 카카오 로그인</b>")
        st.markdown(
            """
1. **활성화 설정** → **ON** 으로 변경
2. **Redirect URI** → `http://localhost` 추가
3. **동의항목** 탭 → **카카오톡 메시지 전송** → **필수 동의** 설정
"""
        )

        _step(6, "⚙️ 설정 탭에서 인가 코드 발급 & 토큰 연결",
              "REST API 키를 저장하면 아래 과정이 자동으로 안내됩니다.")
        st.markdown(
            """
1. ⚙️ 설정 탭 > 카카오톡 알림 > **[여기를 클릭하여 카카오 로그인]** 링크 클릭
2. 카카오 로그인 후 **동의하고 계속** 클릭
3. 브라우저 주소창 확인 → `http://localhost?code=` **뒤의 코드** 복사
   - 예시: `http://localhost?code=` <span style='background:#ffe082;padding:2px 6px;border-radius:3px'>**abc123xyz**</span>
4. 복사한 코드를 **인가 코드 붙여넣기** 칸에 붙여넣기 → **🔑 토큰 발급받기** 클릭
""",
            unsafe_allow_html=True,
        )
        _ok("액세스 토큰 설정됨 메시지 확인 → <b>🔔 카카오톡 테스트 전송</b> 클릭으로 최종 확인")
        _tip("토큰은 6시간마다 자동 갱신됩니다. 한 번 설정하면 재설정이 필요 없습니다.")
        _warn("인가 코드는 <b>1회용</b>입니다. 이미 사용한 코드를 다시 입력하면 오류가 납니다. 오류 시 위 1단계 링크를 다시 클릭하세요.")

    # ═══════════════════════════════════════════════════════
    st.divider()

    # ── 3. 네이버 Open API (순위 체크) ───────────────────
    with st.expander("🔍 네이버 Open API 설정 가이드 (키워드 순위 체크)", expanded=True):
        st.markdown("키워드 순위 체크용 API입니다. **네이버 커머스 API와 별개**로 발급해야 합니다.")

        st.markdown("---")
        _step(1, "네이버 개발자 센터 접속",
              "아래 주소를 복사해서 브라우저에 붙여넣기 하세요.")
        st.code("https://developers.naver.com", language=None)

        _step(2, "로그인 & 애플리케이션 등록",
              "네이버 계정으로 로그인 → 상단 <b>Application</b> → <b>애플리케이션 등록</b>")

        _step(3, "사용 API 선택",
              "애플리케이션 등록 화면에서 아래만 체크하면 됩니다.")
        st.markdown(
            """
| 체크 항목 | 설명 |
|---|---|
| ✅ **검색 > 쇼핑** | 쇼핑 검색 결과 조회 (순위 체크에 사용) |
"""
        )
        st.markdown(
            """
- **Callback URL**: 공란으로 비워두거나 `http://localhost` 입력
- **서비스 환경**: `PC` 체크
"""
        )

        _step(4, "Client ID / Client Secret 복사",
              "등록 완료 후 <b>내 애플리케이션</b>에서 방금 만든 앱 클릭")
        st.markdown(
            """
- **Client ID** → ⚙️ 설정 탭 > 네이버 Open API > **Client ID** 에 붙여넣기
- **Client Secret** → **Client Secret** 에 붙여넣기 → **Open API 저장** 클릭
"""
        )
        _ok("저장 후 📈 순위 체크 탭 → <b>🧪 API 키 테스트</b> 버튼으로 정상 동작 확인")
        _tip("무료 사용량: 하루 25,000건. 키워드 수십 개를 매일 체크해도 충분합니다.")

    # ═══════════════════════════════════════════════════════
    st.divider()

    # ── 4. 쿠팡 Wing Open API ─────────────────────────────
    with st.expander("🛒 쿠팡 Wing Open API 설정 가이드 (쿠팡 주문 조회)", expanded=True):
        st.markdown("쿠팡 Wing 판매자 계정이 있어야 합니다. 개발자 센터에서 API 키를 발급합니다.")

        st.markdown("---")
        _step(1, "쿠팡 Wing 개발자 센터 접속",
              "아래 주소를 복사해서 브라우저에 붙여넣기 하세요.")
        st.code("https://wing.coupang.com", language=None)
        st.markdown("로그인 후 상단 메뉴 **개발자 센터** → **Open API** 클릭")

        _step(2, "Access Key / Secret Key 발급",
              "<b>API Key 발급</b> 버튼 클릭 → 새 키 생성")
        st.markdown(
            """
- **Access Key** → ⚙️ 설정 탭 > 쿠팡 Wing Open API > **Access Key** 에 붙여넣기
- **Secret Key** → **Secret Key** 에 붙여넣기
"""
        )
        _warn("Secret Key는 발급 즉시 한 번만 표시됩니다. 반드시 바로 복사해두세요.")

        _step(3, "Vendor ID 확인",
              "Wing 로그인 후 브라우저 주소창 URL에서 확인합니다.")
        st.markdown(
            """
URL 예시: `https://wing.coupang.com/vendor/` <span style='background:#ffe082;padding:2px 6px;border-radius:3px'>**A00012345**</span> `/dashboard`

- 위 강조 부분(`A`로 시작하는 값)을 복사 → **Vendor ID** 에 붙여넣기
""",
            unsafe_allow_html=True,
        )

        _step(4, "⚙️ 설정 탭에 입력 후 저장",
              "저장 버튼을 누르면 자동으로 API 연결 테스트가 진행됩니다.")
        _ok("연결 성공 메시지 확인 → 📋 주문 업로드 탭에서 <b>🛒 쿠팡 주문 조회</b> 버튼 사용 가능")
        _tip("쿠팡은 배송비를 판매자가 부담하는 구조입니다. 정산예정금액 = 판매가 - 쿠팡 수수료")

    st.divider()

    # ── 5. 엑셀 비밀번호 & 기타 ──────────────────────────
    with st.expander("📋 엑셀 비밀번호 & 기타 설정", expanded=False):
        st.markdown("---")
        st.subheader("🔓 엑셀 비밀번호")
        _step(1, "네이버 스마트스토어 센터에서 주문 엑셀 다운로드",
              "주문 관리 → 발주 확인/발송 관리 → 엑셀 다운로드 시 파일에 비밀번호가 설정됩니다.")
        _step(2, "비밀번호 확인",
              "엑셀 파일을 열면 비밀번호 입력창이 나옵니다. 그 비밀번호를 복사해두세요.")
        _step(3, "⚙️ 설정 탭 > 엑셀 비밀번호에 입력 후 저장",
              "저장하면 이후 파일 업로드 시 자동으로 비밀번호가 해제됩니다.")
        _tip("비밀번호는 네이버 판매자 계정마다 다를 수 있습니다. 오류 시 비밀번호를 재확인하세요.")

        st.markdown("---")
        st.subheader("📦 고정 비용")
        st.markdown(
            """
- **택배비**: 실제 납부하는 택배비를 입력 (기본 1,800원)
- **박스비**: 포장 박스 단가 (기본 300원)
- 수익 계산 시 자동으로 차감됩니다.
"""
        )

    # ═══════════════════════════════════════════════════════
    st.divider()

    # ── 6. 자주 묻는 질문 ────────────────────────────────
    with st.expander("❓ 자주 묻는 질문 (FAQ)", expanded=False):
        faqs = [
            (
                "Q. 카카오톡 토큰 발급 시 'redirect_uri mismatch' 오류가 납니다.",
                "카카오 개발자 센터 → 제품 설정 → 카카오 로그인 → Redirect URI 목록에 "
                "`http://localhost` 가 정확히 등록되어 있는지 확인하세요. "
                "오타 없이 `http://localhost` (끝에 `/` 없음)로 입력해야 합니다.",
            ),
            (
                "Q. 인가 코드를 붙여넣었는데 '이미 사용된 코드'라고 오류가 납니다.",
                "인가 코드는 1회용입니다. ⚙️ 설정 탭에서 카카오 로그인 링크를 다시 클릭해 "
                "새 코드를 발급받으세요.",
            ),
            (
                "Q. 네이버 커머스 API 저장 후 '401 Unauthorized' 오류가 납니다.",
                "Client Secret이 틀렸거나, API 등록 시 스마트스토어 API 권한이 미체크된 경우입니다. "
                "커머스 API 센터에서 애플리케이션을 다시 확인해 주세요.",
            ),
            (
                "Q. Open API 순위 체크 시 '검색 결과 없음'이 뜹니다.",
                "키워드가 너무 구체적이거나 상품명이 정확히 일치하지 않는 경우입니다. "
                "📈 순위 체크 탭에서 추적 키워드를 더 짧고 일반적인 검색어로 변경해 보세요.",
            ),
            (
                "Q. 엑셀 업로드 시 '비밀번호가 틀렸습니다' 오류가 납니다.",
                "⚙️ 설정 탭 > 엑셀 비밀번호를 재확인하세요. 네이버 스마트스토어 "
                "비밀번호와 엑셀 비밀번호는 다릅니다.",
            ),
            (
                "Q. CJ대한통운 자동 접수가 실패합니다.",
                "⚙️ 설정 탭 > 택배사 설정에서 CJ ID / PW / 고객번호가 모두 입력되어 있는지 확인하세요. "
                "CJ대한통운 계약 고객번호는 CJ 영업 담당자 또는 "
                "CJ대한통운 기업 고객센터(1588-1255)에 문의하세요.",
            ),
        ]
        for q, a in faqs:
            st.markdown(f"**{q}**")
            st.markdown(f"> {a}")
            st.markdown("")

    # ── 설정 탭 바로가기 안내 ─────────────────────────────
    st.markdown(
        '<div style="background:#f0f4ff;border:1px solid #aac4ff;border-radius:8px;'
        'padding:14px 18px;margin-top:12px;font-size:14px">'
        '📌 가이드를 참고해 키를 발급했다면, 왼쪽 메뉴 <b>⚙️ 설정</b> 탭으로 이동해 입력 & 저장하세요.'
        '</div>',
        unsafe_allow_html=True,
    )
