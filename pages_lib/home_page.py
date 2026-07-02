"""🏠 홈 대시보드 — KPI / 일별·월간 차트 / 가격 변동 이력.

멀티페이지 마이그레이션 2번째 추출 모듈.
"""
import calendar as _calendar
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from db import (
    get_dashboard_kpi, get_rank_drops, get_daily_profit_trend,
    get_week_best_products, get_monthly_stats, get_price_history_monthly,
    get_cumulative_sales, get_date_range_stats, get_dispatch_counts,
    get_daily_order_counts,
)
from ui_theme import (
    COLORS, CHART_COLORS,
    hero_section, quick_action_buttons, kpi_card, section_header,
    chart_card_open, chart_card_close,
)
from utils import fmt, get_week_range, get_month_range


def render(USERNAME: str):
    """홈 대시보드 렌더링.

    Args:
        USERNAME: 현재 로그인한 사용자명.
    """
    today = datetime.today()
    w_start, w_end = get_week_range()
    m_start, m_end = get_month_range()

    kpi = get_dashboard_kpi(USERNAME)
    wk = kpi['week']
    mk = kpi['month']
    lwk = kpi['last_week']
    lmk = kpi['last_month']
    cumul = get_cumulative_sales(USERNAME)  # 어제까지 누적

    # ── 환영 히어로 ──
    _weekday_kr = ['월', '화', '수', '목', '금', '토', '일'][today.weekday()]
    hero_section(
        title=f"안녕하세요, {USERNAME}님",
        subtitle=f"📅 {today.strftime('%Y년 %m월 %d일')} ({_weekday_kr}) — 오늘도 좋은 하루 되세요!",
        icon="👋"
    )

    # ── 빠른 액션 ──
    quick_action_buttons([
        {"label": "📋 주문 업로드",   "tab": "📋 주문 업로드"},
        {"label": "🧾 영수증 등록",   "tab": "🧾 영수증 등록"},
        {"label": "📈 순위 체크",     "tab": "📈 순위 체크"},
        {"label": "🤖 자동화",        "tab": "🤖 자동화"},
    ])

    st.markdown("<div style='margin-top:18px'></div>", unsafe_allow_html=True)

    # ── 모던 KPI 카드 ──
    # 상단 행: 누적 주문금액 (전체 너비 강조 카드)
    _cumul_sales = cumul.get('total_sales', 0)
    _cumul_cnt   = cumul.get('total_cnt', 0)
    _from_str    = cumul.get('from', today.replace(day=1).strftime("%Y-%m-%d"))
    _until_str   = cumul.get('until', (today - timedelta(days=1)).strftime("%Y-%m-%d"))
    _range_label = f"{_from_str} ~ {_until_str}"
    st.markdown(
        f'<div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);'
        f'border-radius:14px;padding:22px 32px;margin-bottom:18px;'
        f'box-shadow:0 4px 20px rgba(0,0,0,0.15);display:flex;align-items:center;gap:32px">'
        f'<div style="font-size:2.2rem">💰</div>'
        f'<div style="flex:1">'
        f'<div style="font-size:12px;color:#aab;font-weight:500;letter-spacing:0.5px;margin-bottom:4px">'
        f'이번 달 주문금액 <span style="color:#7ecfff;font-size:11px">({_range_label}, 매월 1일 초기화)</span></div>'
        f'<div style="font-size:2.4rem;font-weight:800;color:#ffffff;line-height:1.1">'
        f'{fmt(_cumul_sales)}원</div>'
        f'<div style="font-size:13px;color:#aab;margin-top:4px">누적 {_cumul_cnt:,}건</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True
    )

    # 하단 행: KPI 4개 — flex-wrap 반응형 (화면 좁아지면 아래로 쌓임)
    _wp = wk['profit'] - lwk['profit'] if lwk.get('profit') else 0
    _mp = mk['profit'] - lmk['profit'] if lmk.get('profit') else 0
    _w_delta = f"{((wk['profit']-lwk['profit'])/abs(lwk['profit'])*100):+.1f}% (전주 대비)" if lwk['profit'] else None
    _m_delta = f"{((mk['profit']-lmk['profit'])/abs(lmk['profit'])*100):+.1f}% (전월 대비)" if lmk['profit'] else None
    _wc_delta = f"전주 {lwk['cnt']}건" if lwk.get('cnt') else None
    _mc_delta = f"전월 {lmk['cnt']}건" if lmk.get('cnt') else None

    def _kpi_html(title, value, delta, delta_pos, icon, accent):
        d_color = COLORS["success"] if delta_pos else COLORS["danger"]
        arrow   = "▲" if delta_pos else "▼"
        d_html  = (f'<div style="font-size:13px;color:{d_color};font-weight:600;margin-top:6px">'
                   f'{arrow} {delta}</div>') if delta else ''
        return (
            f'<div style="flex:1 1 180px;min-width:160px;'
            f'background:{COLORS["bg"]};border:1px solid {COLORS["border"]};'
            f'border-top:3px solid {accent};border-radius:10px;'
            f'padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,0.05);box-sizing:border-box">'
            f'<div style="font-size:22px;margin-bottom:6px">{icon}</div>'
            f'<div style="font-size:13px;color:{COLORS["muted"]};font-weight:500">{title}</div>'
            f'<div style="font-size:26px;font-weight:700;color:{COLORS["text"]};margin-top:4px">{value}</div>'
            f'{d_html}</div>'
        )

    _kpi_cards_html = (
        '<div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:0">'
        + _kpi_html("이번 주 수익",   f"{fmt(wk['profit'])}원", _w_delta,  _wp >= 0,  "📅", COLORS["primary"])
        + _kpi_html("이번 달 수익",   f"{fmt(mk['profit'])}원", _m_delta,  _mp >= 0,  "📆", COLORS["success"])
        + _kpi_html("주간 주문건수",  f"{wk['cnt']}건",         _wc_delta, True,      "📦", COLORS["info"])
        + _kpi_html("월간 주문건수",  f"{mk['cnt']}건",         _mc_delta, True,      "📦", COLORS["warning"])
        + '</div>'
    )
    st.markdown(_kpi_cards_html, unsafe_allow_html=True)

    st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)

    # ── 📅 달력 (대시보드에서 이동) ──
    _render_calendar(USERNAME, today)

    st.divider()

    # ── ⚠️ 순위 하락 알림 ──────────────────────────────────
    _drops = get_rank_drops(USERNAME, lookback_days=14, limit=20)
    _hd1, _hd2 = st.columns([5, 1])
    with _hd1:
        if _drops:
            section_header(
                f"순위 하락 알림 ({len(_drops)}건)",
                "최근 체크 기준 전회 대비 순위가 떨어진 키워드",
                icon="⚠️"
            )
        else:
            section_header(
                "순위 하락 없음",
                "최근 체크 기준 모든 추적 키워드가 유지/상승 중입니다",
                icon="✅"
            )
    if _hd2.button("📈 순위 체크 →", key="home_goto_rank", use_container_width=True):
        st.session_state['_pending_tab'] = "📈 순위 체크"
        st.rerun()

    if _drops:
        # 카드 형태로 가로 스크롤 가능한 그리드 (최대 5개씩 행)
        _drop_html = '<div style="display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 12px 0">'
        for _d in _drops:
            _diff = _d['drop']
            _severity_bg = "#fee" if _diff <= 5 else ("#fdd" if _diff <= 15 else "#fbb")
            _severity_border = "#e74c3c"
            _icon = "⚠️" if _diff <= 10 else "🚨"
            _drop_html += (
                f'<div style="flex:1 1 240px;max-width:300px;padding:12px 14px;'
                f'background:{_severity_bg};border-left:4px solid {_severity_border};'
                f'border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,0.06)">'
                f'<div style="font-size:12px;color:#888;margin-bottom:4px">{_icon} <b>"{_d["search_keyword"]}"</b> 검색</div>'
                f'<div style="font-size:14px;font-weight:600;color:#222;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:6px"'
                f'title="{_d["product_keyword"]}">{_d["product_keyword"]}</div>'
                f'<div style="display:flex;align-items:center;gap:8px;font-size:13px">'
                f'<span style="color:#666">{_d["prev_rank"]}위</span>'
                f'<span style="color:#e74c3c;font-weight:700;font-size:15px">→ {_d["current_rank"]}위</span>'
                f'<span style="margin-left:auto;color:#e74c3c;font-weight:700;'
                f'background:#fff;padding:2px 8px;border-radius:10px;border:1px solid #e74c3c">↓ {_diff}</span>'
                f'</div>'
                f'<div style="font-size:11px;color:#999;margin-top:6px">{(_d["checked_at"] or "")[:16]}</div>'
                f'</div>'
            )
        _drop_html += '</div>'
        st.markdown(_drop_html, unsafe_allow_html=True)
    st.divider()

    # (일별 수익 추이 차트는 달력으로 대체 — 중복 제거)

    # ── 주간 베스트 / 월간 수익 추이 ──
    col_left, col_right = st.columns(2)

    with col_left:
        section_header(f"주간 베스트 상품", f"{w_start[5:]} ~ {w_end[5:]}", icon="🏆")
        chart_card_open()
        best = get_week_best_products(USERNAME)
        if best:
            bdf = pd.DataFrame(best)
            short_names = [n[:22] + ('…' if len(n) > 22 else '') for n in bdf['product_name']]
            fig_best = go.Figure(go.Bar(
                y=short_names, x=bdf['total_profit'], orientation='h',
                marker_color=[CHART_COLORS['profit_pos'] if v >= 0 else CHART_COLORS['profit_neg']
                              for v in bdf['total_profit']],
                text=bdf['total_profit'].apply(lambda x: f"{x:,.0f}원"),
                textposition='inside', textfont=dict(color='white', size=11),
            ))
            fig_best.update_layout(
                height=240, margin=dict(l=10, r=10, t=10, b=10),
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                font=dict(color=COLORS['text']),
                yaxis=dict(autorange='reversed'),
                xaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)'),
            )
            st.plotly_chart(fig_best, use_container_width=True)
            # 상세 테이블 (HTML, 상품명 전체 표시)
            rows_b = []
            for rank, row in enumerate(best, 1):
                pc = '#1D9E75' if row['total_profit'] >= 0 else '#E74C3C'
                rows_b.append(
                    f'<tr style="border-bottom:1px solid #f0f0f0">'
                    f'<td style="padding:5px 8px;text-align:center;font-weight:bold;color:#888">{rank}</td>'
                    f'<td style="padding:5px 8px;white-space:normal;word-break:break-word">{row["product_name"]}</td>'
                    f'<td style="padding:5px 8px;text-align:right">{int(row["total_qty"]):,}개</td>'
                    f'<td style="padding:5px 8px;text-align:right;font-weight:bold;color:{pc}">{int(row["total_profit"]):,}원</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
                f'<thead><tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6">'
                f'<th style="padding:6px 8px;width:28px">#</th>'
                f'<th style="padding:6px 8px;text-align:left">상품명</th>'
                f'<th style="padding:6px 8px;text-align:right">수량</th>'
                f'<th style="padding:6px 8px;text-align:right">수익</th></tr></thead>'
                f'<tbody>{"".join(rows_b)}</tbody></table>',
                unsafe_allow_html=True
            )
        else:
            st.info("이번 주 데이터가 없습니다.")
        chart_card_close()

    with col_right:
        section_header("월간 수익 추이", "최근 6개월", icon="📊")
        chart_card_open()
        monthly = get_monthly_stats(USERNAME)
        if monthly:
            mdf = pd.DataFrame(monthly).tail(6)
            m_colors = [CHART_COLORS['profit_neg'] if v < 0 else CHART_COLORS['accent']
                        for v in mdf['total_profit']]
            fig_month = go.Figure()
            fig_month.add_trace(go.Bar(
                x=mdf['month'], y=mdf['total_profit'], name='월 수익',
                marker_color=m_colors,
                text=mdf['total_profit'].apply(lambda x: f"{x/10000:.1f}만"),
                textposition='outside',
            ))
            fig_month.add_trace(go.Scatter(
                x=mdf['month'], y=mdf['cnt'], name='주문건수', yaxis='y2',
                mode='lines+markers',
                line=dict(color=CHART_COLORS['warning'], width=2), marker=dict(size=8),
            ))
            fig_month.update_layout(
                height=300, margin=dict(l=10, r=10, t=10, b=40),
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                font=dict(color=COLORS['text']),
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                yaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)'),
                yaxis2=dict(overlaying='y', side='right', showgrid=False), bargap=0.35,
            )
            st.plotly_chart(fig_month, use_container_width=True)

            # 월별 합계 테이블
            mdf_disp = mdf[['month', 'cnt', 'total_sales', 'total_profit']].copy()
            mdf_disp.columns = ['월', '주문건', '매출', '순수익']
            rows_m = []
            for _, row in mdf_disp.iterrows():
                pc = '#1D9E75' if row['순수익'] >= 0 else '#E74C3C'
                rows_m.append(
                    f'<tr style="border-bottom:1px solid #f0f0f0">'
                    f'<td style="padding:5px 10px">{row["월"]}</td>'
                    f'<td style="padding:5px 10px;text-align:right">{int(row["주문건"]):,}건</td>'
                    f'<td style="padding:5px 10px;text-align:right">{int(row["매출"]):,}원</td>'
                    f'<td style="padding:5px 10px;text-align:right;font-weight:bold;color:{pc}">{int(row["순수익"]):,}원</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
                f'<thead><tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6">'
                f'<th style="padding:6px 10px;text-align:left">월</th>'
                f'<th style="padding:6px 10px;text-align:right">주문건</th>'
                f'<th style="padding:6px 10px;text-align:right">매출</th>'
                f'<th style="padding:6px 10px;text-align:right">순수익</th></tr></thead>'
                f'<tbody>{"".join(rows_m)}</tbody></table>',
                unsafe_allow_html=True
            )
        else:
            st.info("월별 데이터가 없습니다.")
        chart_card_close()

    # ── 이번 달 가격 변동 이력 ──
    section_header(f"이번 달 가격 변동 이력", today.strftime('%Y년 %m월'), icon="💹")
    chart_card_open()
    ph = get_price_history_monthly(USERNAME)
    if ph:
        rows_ph = []
        for row in ph:
            old_p, new_p = int(row['old_price']), int(row['new_price'])
            is_up = new_p > old_p
            arrow = '▲' if is_up else '▼'
            color = CHART_COLORS['profit_pos'] if is_up else CHART_COLORS['profit_neg']
            pct = f"{((new_p - old_p) / old_p * 100):+.1f}%" if old_p > 0 else '-'
            rows_ph.append(
                f'<tr style="border-bottom:1px solid #f0f0f0">'
                f'<td style="padding:6px 10px;white-space:nowrap;font-size:12px;color:#888">{row["created_at"]}</td>'
                f'<td style="padding:6px 10px;white-space:normal;word-break:break-word">{row["product_name"]}</td>'
                f'<td style="padding:6px 10px;text-align:right">{old_p:,}원</td>'
                f'<td style="padding:6px 10px;text-align:right;font-weight:bold">{new_p:,}원</td>'
                f'<td style="padding:6px 10px;text-align:center;font-weight:bold;color:{color}">{arrow} {pct}</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#666">{row["reason"]}</td>'
                f'</tr>'
            )
        ths = ['일시', '상품명', '변경 전', '변경 후', '변동폭', '사유']
        thead = ''.join(
            f'<th style="padding:7px 10px;background:#f8f9fa;text-align:'
            f'{"right" if h in ("변경 전", "변경 후") else "center" if h == "변동폭" else "left"};'
            f'font-weight:600;white-space:nowrap;border-bottom:2px solid #dee2e6">{h}</th>'
            for h in ths
        )
        st.markdown(
            f'<div style="overflow-x:auto;border:1px solid #dee2e6;border-radius:4px">'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'<thead><tr>{thead}</tr></thead>'
            f'<tbody>{"".join(rows_ph)}</tbody></table></div>',
            unsafe_allow_html=True
        )
        # 가격변동 막대 차트 (최근 변동 top10)
        if len(ph) >= 2:
            ph_chart = ph[:10]
            names_ph = [r['product_name'][:18] + ('…' if len(r['product_name']) > 18 else '')
                        for r in ph_chart]
            diffs = [int(r['new_price']) - int(r['old_price']) for r in ph_chart]
            fig_ph = go.Figure(go.Bar(
                y=names_ph, x=diffs, orientation='h',
                marker_color=[CHART_COLORS['profit_pos'] if d > 0 else CHART_COLORS['profit_neg']
                              for d in diffs],
                text=[f"{d:+,}원" for d in diffs],
                textposition='inside', textfont=dict(color='white', size=11),
            ))
            fig_ph.update_layout(
                height=max(200, len(ph_chart) * 36),
                margin=dict(l=10, r=10, t=20, b=10),
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                font=dict(color=COLORS['text']),
                title=dict(text='가격 변동액 (최근 10건)', font=dict(size=13)),
                yaxis=dict(autorange='reversed'),
                xaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)', title='변동액 (원)'),
            )
            st.plotly_chart(fig_ph, use_container_width=True)
    else:
        st.info("이번 달 가격 변동 이력이 없습니다.")
    chart_card_close()


def _render_calendar(USERNAME: str, today: datetime):
    """📅 월별 달력 — 각 날짜에 주문건 · 발송건 · 수익 · 정산 표시."""
    section_header("달력 (일별 주문 · 발송 · 수익 · 입금정산)",
                   "📋 입금정산 = 그날 실제 입금된 정산금(정산일 기준)", icon="📅")

    _months = [m['month'] for m in (get_monthly_stats(USERNAME) or [])]
    cur_month = today.strftime("%Y-%m")
    if cur_month not in _months:
        _months.append(cur_month)
    _months = sorted(set(_months), reverse=True)
    sel_month = st.selectbox("월 선택", _months,
                             index=_months.index(cur_month) if cur_month in _months else 0,
                             key="home_cal_month", label_visibility="collapsed")

    y, m = int(sel_month[:4]), int(sel_month[5:7])
    last_day = _calendar.monthrange(y, m)[1]
    _d_from, _d_to = f"{sel_month}-01", f"{sel_month}-{last_day:02d}"
    stats = get_date_range_stats(USERNAME, _d_from, _d_to)
    by_date = {s['order_date']: s for s in stats}
    order_map = get_daily_order_counts(USERNAME, _d_from, _d_to)  # {주문일: 수집주문건수}
    disp_map = get_dispatch_counts(USERNAME, _d_from, _d_to)  # {date: 발송건수}

    # 📋 정산금 = 그날 실제 입금된 정산금(정산일/입금일 기준) — 네이버 /daily + 쿠팡(주정산 70/30 분배)
    _dep_key = f"_home_deposit_{sel_month}"
    dep_map = st.session_state.get(_dep_key)
    if dep_map is None:
        dep_map = {}
        try:
            from db import get_all_settings, get_coupang_deposit_map
            import naver_api
            _s = get_all_settings(USERNAME)
            if _s.get('api_client_id') and _s.get('api_client_secret'):
                dep_map, _derr = naver_api.get_daily_settlements_range(
                    _s['api_client_id'], _s['api_client_secret'], _d_from, _d_to)
            # 쿠팡 입금 합산 (저장된 coupang_settlements 기준, 1차 70%/2차 30%)
            try:
                _cp_dep = get_coupang_deposit_map(USERNAME, _d_from, _d_to)
                for _dd, _amt in _cp_dep.items():
                    dep_map[_dd] = int(dep_map.get(_dd, 0)) + int(_amt)
            except Exception:
                pass
            st.session_state[_dep_key] = dep_map
        except Exception:
            dep_map = {}

    week_hdr = ['일', '월', '화', '수', '목', '금', '토']
    hdr = ''.join(
        f'<th style="padding:6px;border:1px solid #e9ecef;background:#f8f9fa;'
        f'color:{"#E74C3C" if i == 0 else "#3477eb" if i == 6 else "#333"};'
        f'font-weight:600;width:14.28%">{d}</th>'
        for i, d in enumerate(week_hdr)
    )

    cal = _calendar.Calendar(firstweekday=6)
    rows = []
    _today_str = today.strftime("%Y-%m-%d")
    for week in cal.monthdatescalendar(y, m):
        cells = []
        for i, d in enumerate(week):
            if d.month != m:
                cells.append('<td style="border:1px solid #f1f3f5;background:#fcfcfc;'
                             'height:118px;vertical-align:top"></td>')
                continue
            ds = d.strftime("%Y-%m-%d")
            s = by_date.get(ds)
            ocnt = int(order_map.get(ds, 0) or 0)  # 수집한 주문 건수(주문일 기준)
            dn = int(disp_map.get(ds, 0) or 0)
            has_dep = ds in dep_map
            dep = int(dep_map.get(ds, 0) or 0)
            daycol = "#E74C3C" if i == 0 else "#3477eb" if i == 6 else "#333"
            bg = "#f0fbf6" if ds == _today_str else "white"
            inner = f'<div style="font-weight:700;font-size:17px;color:{daycol}">{d.day}</div>'
            if ocnt > 0:
                inner += f'<div style="font-size:14px;color:#555">🧾 주문 {ocnt}</div>'
            if s:
                pf = int(s.get('total_profit') or 0)
                pfcol = "#1D9E75" if pf >= 0 else "#E74C3C"
                inner += f'<div style="font-size:14px;color:{pfcol};font-weight:600">💰 {pf:,}</div>'
            if dn > 0:
                inner += f'<div style="font-size:14px;color:#3477eb">🚚 발송 {dn}</div>'
            if has_dep:
                _depcol = "#1D9E75" if dep >= 0 else "#E74C3C"
                inner += f'<div style="font-size:14px;color:{_depcol}">📋 입금 {dep:,}</div>'
            cells.append(f'<td style="border:1px solid #e9ecef;background:{bg};'
                         f'height:118px;vertical-align:top;padding:4px">{inner}</td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')

    st.markdown(
        '<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
        f'<thead><tr>{hdr}</tr></thead><tbody>{"".join(rows)}</tbody></table>',
        unsafe_allow_html=True,
    )

    m_cnt = sum(order_map.values())
    m_pf = sum(int(s.get('total_profit') or 0) for s in stats)
    m_disp = sum(disp_map.values())
    m_dep = sum(dep_map.values()) if dep_map else 0
    st.caption(f"📆 {sel_month} 합계 — 주문 {m_cnt}건 · 발송 {m_disp}건 · "
               f"수익 {fmt(m_pf)}원 · 입금정산 {fmt(m_dep)}원 "
               f"(📋 입금 = 그날 실제 입금된 정산금, 정산일 기준)")
