"""UI 디자인 시스템 — 색상/컴포넌트/스타일 함수 모음
모든 탭에서 일관된 디자인을 위해 import해서 사용.
"""
import streamlit as st

# ──────────────────────────────────────────────────────
# 브랜드 컬러 팔레트
# ──────────────────────────────────────────────────────
COLORS = {
    "primary":   "#E31837",   # 코스트코 빨강 (메인 액센트)
    "primary_d": "#B0142C",   # 진한 빨강
    "primary_l": "#FFE5E9",   # 연한 빨강 배경
    "success":   "#1D9E75",   # 수익/상승
    "danger":    "#E74C3C",   # 손실/하락
    "warning":   "#F39C12",   # 주의
    "info":      "#3498DB",   # 정보/보조
    "text":      "#1F2937",   # 본문
    "muted":     "#6B7280",   # 흐린 텍스트
    "border":    "#E5E7EB",   # 테두리
    "bg":        "#FFFFFF",   # 카드 배경
    "bg_soft":   "#F8F9FA",   # 섹션 배경
}

# Plotly 차트용 컬러 (브랜드 통일)
CHART_COLORS = {
    "profit_pos":  "#1D9E75",   # 수익 (양수)
    "profit_neg":  "#E74C3C",   # 손실 (음수)
    "secondary":   "#3498DB",   # 보조선
    "accent":      "#E31837",   # 강조 (빨강)
    "warning":     "#F39C12",
    "neutral":     "#6B7280",
}

# ──────────────────────────────────────────────────────
# 전역 CSS 주입 (앱 시작 시 1회 호출)
# ──────────────────────────────────────────────────────
def inject_global_css():
    """전체 앱에 적용되는 공통 CSS"""
    st.markdown(f"""
<style>
/* ── 전역 폰트/컬러 ── */
.main .block-container {{
    padding-top: 1.5rem;
    padding-bottom: 3rem;
}}
/* metric 카드 살짝 개선 */
[data-testid="stMetric"] {{
    background: {COLORS['bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 14px 16px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}}
[data-testid="stMetricLabel"] {{
    color: {COLORS['muted']} !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}}
[data-testid="stMetricValue"] {{
    font-size: 26px !important;
    font-weight: 700 !important;
    color: {COLORS['text']} !important;
}}

/* ====================================================================
   탭 전환 페이드 — 잔상을 부드럽게 가림
   --------------------------------------------------------------------
   동작 원리:
     - app.py가 탭 변경 감지 시에만 .pgfade-marker div를 렌더링
     - CSS :has() 셀렉터로 marker 존재 시 stMainBlockContainer에 애니메이션 적용
     - 애니메이션은 marker가 "새로 추가될 때마다" 한 번만 발동 (체크박스
       클릭 등 위젯 인터랙션 시 marker 미렌더 → 애니메이션 발동 안 함)
   ==================================================================== */
@keyframes _pgFadeAnim {{
    0%   {{ opacity: 0.25; transform: translateY(3px); }}
    50%  {{ opacity: 0.85; }}
    100% {{ opacity: 1; transform: translateY(0); }}
}}
section[data-testid="stMain"]:has(.pgfade-marker) [data-testid="stMainBlockContainer"] {{
    animation: _pgFadeAnim 0.4s ease-out;
}}
/* marker 자체는 화면에 보이지 않도록 (CSS 트리거 용도만) */
.pgfade-marker {{
    display: none !important;
    height: 0 !important;
    width: 0 !important;
    pointer-events: none !important;
}}

/* ── Streamlit 내장 stale dimming 무력화 (위젯 클릭 시 화면 흐려짐 방지) ── */
[data-stale="true"], .stale-element {{
    opacity: 1 !important;
}}

/* ── 부드러운 스크롤 (탭 이동 시 위로 스크롤될 때 자연스럽게) ── */
html {{
    scroll-behavior: smooth;
}}
</style>
""", unsafe_allow_html=True)
    _inject_design_system()


def _inject_design_system():
    """미니멀 라이트 + 코스트코 레드 디자인 시스템 (폰트·크롬·버튼·입력·사이드바·탭).
    색상 하드코딩으로 f-string 중괄호 충돌 회피."""
    st.markdown("""<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@latest/dist/web/static/pretendard.min.css');

/* ── 전역 폰트: Pretendard (한글 최적 모던 산세리프) ── */
html, body, .stApp, .stApp *,
button, input, textarea, select, [class*="st-"] {
    font-family: 'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,
                 'Segoe UI','Apple SD Gothic Neo','Malgun Gothic',sans-serif !important;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
}

/* ── Streamlit 기본 크롬 숨김 (AI/기본 느낌 제거) ── */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header[data-testid="stHeader"] {background: transparent;}
[data-testid="stToolbar"] {display: none !important;}
[data-testid="stDecoration"] {display: none !important;}
[data-testid="stStatusWidget"] {display: none !important;}
.stDeployButton {display: none !important;}
[data-testid="stAppDeployButton"] {display: none !important;}

/* ── 본문 폭/여백 정돈 ── */
.main .block-container {max-width: 1280px; padding-top: 1.2rem;}

/* ── 타이포 위계 (절제된 굵기/자간) ── */
h1 {font-weight: 800 !important; letter-spacing: -0.025em !important; color:#111827 !important;}
h2 {font-weight: 700 !important; letter-spacing: -0.02em !important; color:#111827 !important;}
h3 {font-weight: 700 !important; letter-spacing: -0.015em !important; color:#1F2937 !important;}
h4, h5 {font-weight: 600 !important; letter-spacing: -0.01em !important;}

/* ── 버튼: 라운드 + 호버 + 그림자 (평평한 기본 버튼 탈피) ── */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    border-radius: 9px !important;
    font-weight: 600 !important;
    border: 1px solid #E5E7EB !important;
    box-shadow: 0 1px 2px rgba(16,24,40,0.05) !important;
    transition: all .15s ease !important;
}
.stButton > button:hover, .stDownloadButton > button:hover {
    border-color: #E31837 !important;
    color: #E31837 !important;
    transform: translateY(-1px);
    box-shadow: 0 3px 8px rgba(227,24,55,0.12) !important;
}
/* primary 버튼 = 코스트코 레드 솔리드 */
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
    background: #E31837 !important;
    border-color: #E31837 !important;
    color: #fff !important;
}
.stButton > button[kind="primary"]:hover {
    background: #C2142E !important; border-color: #C2142E !important; color:#fff !important;
}

/* ── 입력칸: 라운드 + 레드 포커스 링 ── */
.stTextInput input, .stNumberInput input, .stDateInput input,
[data-baseweb="input"], [data-baseweb="select"] > div, .stTextArea textarea {
    border-radius: 9px !important;
}
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
    border-color: #E31837 !important;
    box-shadow: 0 0 0 3px rgba(227,24,55,0.12) !important;
}

/* ── 사이드바: 깔끔한 흰 배경 + 얇은 경계 ── */
section[data-testid="stSidebar"] {
    background: #FFFFFF !important;
    border-right: 1px solid #EEF0F2 !important;
}
[data-testid="stSidebarNav"] a {border-radius: 9px !important; margin: 1px 6px;}
[data-testid="stSidebarNav"] a[aria-current="page"] {background: #FFE7EB !important;}
[data-testid="stSidebarNav"] a[aria-current="page"] span {color:#E31837 !important; font-weight:700 !important;}

/* ── 탭: 레드 인디케이터 ── */
.stTabs [data-baseweb="tab-list"] {gap: 2px; border-bottom: 1px solid #EEF0F2;}
.stTabs [aria-selected="true"] {color: #E31837 !important;}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {background-color: #E31837 !important;}

/* ── expander / 컨테이너: 부드러운 라운드 카드 ── */
[data-testid="stExpander"] {border: 1px solid #EEF0F2 !important; border-radius: 11px !important;
    box-shadow: 0 1px 2px rgba(16,24,40,0.04);}
[data-testid="stExpander"] summary:hover {color: #E31837;}

/* ── 라디오/체크 강조색 ── */
[data-baseweb="radio"] [aria-checked="true"] div:first-child,
[data-testid="stCheckbox"] [aria-checked="true"] {background-color: #E31837 !important; border-color:#E31837 !important;}

/* ── 구분선 옅게, 코드/캡션 정돈 ── */
hr {border-color: #EEF0F2 !important; margin: 1rem 0 !important;}
[data-testid="stCaptionContainer"] {color:#6B7280 !important;}

/* ── 데이터프레임 둥근 모서리 ── */
[data-testid="stDataFrame"], [data-testid="stTable"] {border-radius: 10px; overflow: hidden;}
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────
# 컴포넌트 함수
# ──────────────────────────────────────────────────────
def hero_section(title: str, subtitle: str = "", icon: str = "👋"):
    """홈 상단 환영 히어로 섹션 (한 줄 HTML로 마크다운 코드블록 회피)"""
    style = (
        f"background:linear-gradient(135deg,{COLORS['primary']} 0%,{COLORS['primary_d']} 100%);"
        f"color:white;padding:22px 28px;border-radius:12px;margin-bottom:20px;"
        f"box-shadow:0 4px 12px rgba(227,24,55,0.18);"
    )
    html = (
        f'<div style="{style}">'
        f'<div style="font-size:24px;font-weight:700;margin-bottom:4px">{icon} {title}</div>'
        f'<div style="font-size:14px;opacity:0.9">{subtitle}</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def section_header(title: str, subtitle: str = "", icon: str = ""):
    """일관된 섹션 헤더"""
    icon_html = f"{icon} " if icon else ""
    sub_html = (
        f'<div style="color:{COLORS["muted"]};font-size:13px;margin-top:2px">{subtitle}</div>'
        if subtitle else ''
    )
    html = (
        f'<div style="margin:18px 0 12px 0">'
        f'<div style="font-size:18px;font-weight:700;color:{COLORS["text"]}">{icon_html}{title}</div>'
        f'{sub_html}'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def kpi_card(title: str, value: str, delta: str = None, delta_pos: bool = True,
             icon: str = "", accent_color: str = None):
    """모던 KPI 카드 (st.metric 대체).
    주의: 마크다운이 4-space 들여쓰기를 code block으로 처리하므로 HTML은 한 줄로 작성.
    """
    if accent_color is None:
        accent_color = COLORS["primary"]
    delta_html = ""
    if delta:
        delta_color = COLORS["success"] if delta_pos else COLORS["danger"]
        arrow = "▲" if delta_pos else "▼"
        delta_html = (
            f'<div style="font-size:13px;color:{delta_color};font-weight:600;margin-top:6px">'
            f'{arrow} {delta}</div>'
        )
    icon_html = f'<div style="font-size:22px;margin-bottom:6px">{icon}</div>' if icon else ''
    style = (
        f"background:{COLORS['bg']};"
        f"border:1px solid {COLORS['border']};"
        f"border-top:3px solid {accent_color};"
        f"border-radius:10px;"
        f"padding:16px 18px;"
        f"box-shadow:0 1px 3px rgba(0,0,0,0.05);"
        f"height:100%;box-sizing:border-box;"
    )
    return (
        f'<div style="{style}">'
        f'{icon_html}'
        f'<div style="font-size:13px;color:{COLORS["muted"]};font-weight:500">{title}</div>'
        f'<div style="font-size:26px;font-weight:700;color:{COLORS["text"]};margin-top:4px">{value}</div>'
        f'{delta_html}'
        f'</div>'
    )


def chart_card_open(title: str = "", subtitle: str = ""):
    """차트 카드 시작 — 차트를 카드로 감싸기"""
    title_html = ""
    if title:
        sub = (
            f'<div style="color:{COLORS["muted"]};font-size:12px">{subtitle}</div>'
            if subtitle else ''
        )
        title_html = (
            f'<div style="margin-bottom:10px">'
            f'<div style="font-weight:600;font-size:15px;color:{COLORS["text"]}">{title}</div>'
            f'{sub}'
            f'</div>'
        )
    style = (
        f"background:{COLORS['bg']};"
        f"border:1px solid {COLORS['border']};"
        f"border-radius:10px;padding:16px 18px;"
        f"box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-bottom:14px;"
    )
    st.markdown(f'<div style="{style}">{title_html}', unsafe_allow_html=True)


def chart_card_close():
    """차트 카드 닫기"""
    st.markdown("</div>", unsafe_allow_html=True)


def quick_action_buttons(actions: list):
    """빠른 액션 버튼 그룹 (홈 히어로 아래)
    actions = [{"label": "📋 주문 업로드", "tab": "📋 주문 업로드"}, ...]
    참고: main_tab 위젯이 이미 인스턴스화된 후이므로 _pending_tab 임시키 사용.
    다음 rerun 시 사이드바 위젯 렌더 직전에 적용됨.
    """
    cols = st.columns(len(actions))
    for col, act in zip(cols, actions):
        if col.button(act["label"], key=f"qa_{act['tab']}", use_container_width=True):
            st.session_state['_pending_tab'] = act["tab"]
            st.rerun()


def info_pill(text: str, color: str = "info"):
    """작은 알림 뱃지"""
    bg_map = {
        "success": COLORS["success"],
        "danger":  COLORS["danger"],
        "warning": COLORS["warning"],
        "info":    COLORS["info"],
    }
    bg = bg_map.get(color, COLORS["info"])
    return (
        f'<span style="display:inline-block;background:{bg};color:#fff;'
        f'padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600">{text}</span>'
    )
