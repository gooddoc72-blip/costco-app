"""📊 대시보드 — 기간별 통계 + 차트."""
import calendar as _calendar
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from db import get_date_range_stats, get_monthly_stats, get_product_ranking
from utils import fmt


def render(USERNAME: str):
    """대시보드 탭."""
    st.header("📊 대시보드")
    today = datetime.today()

    period = st.radio("기간", ["최근 7일", "최근 14일", "최근 30일"],
                       horizontal=True, label_visibility="collapsed")
    days = {"최근 7일": 7, "최근 14일": 14, "최근 30일": 30}[period]
    start = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    stats = get_date_range_stats(USERNAME, start, end)

    if not stats:
        st.info("저장된 데이터가 없습니다. 주문 업로드 → 수익 계산 → 저장 순서로 진행하세요.")
        return

    today_str = today.strftime("%Y-%m-%d")
    today_stat = next((s for s in stats if s['order_date'] == today_str), None)

    c1, c2, c3 = st.columns(3)
    c1.metric("오늘 주문", f"{today_stat['cnt']}건" if today_stat else "0건")
    c2.metric("오늘 매출", f"{fmt(today_stat['total_sales'])}원" if today_stat else "0원")
    c3.metric("오늘 순수익", f"{fmt(today_stat['total_profit'])}원" if today_stat else "0원")

    _render_calendar(USERNAME, today)

    st.subheader("일별 수익 추이")
    chart_df = pd.DataFrame(stats)
    chart_df['order_date'] = pd.to_datetime(chart_df['order_date'])
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=chart_df['order_date'], y=chart_df['total_profit'],
        name='순수익', marker_color='#1D9E75',
        text=chart_df['total_profit'].apply(lambda x: f"{x:,.0f}"),
        textposition='outside',
    ))
    fig.update_layout(
        height=350, margin=dict(l=20, r=20, t=20, b=40),
        yaxis_tickformat=",",
        xaxis_dtick="D1", xaxis_tickformat="%m/%d",
        plot_bgcolor='rgba(0,0,0,0)',
    )
    fig.update_yaxes(gridcolor='rgba(0,0,0,0.05)')
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("주간 요약")
    tw_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    lw_start = (today - timedelta(days=today.weekday() + 7)).strftime("%Y-%m-%d")
    lw_end = (today - timedelta(days=today.weekday() + 1)).strftime("%Y-%m-%d")
    tw = get_date_range_stats(USERNAME, tw_start, end)
    lw = get_date_range_stats(USERNAME, lw_start, lw_end)
    tw_profit = sum(s['total_profit'] for s in tw)
    lw_profit = sum(s['total_profit'] for s in lw)
    c1, c2 = st.columns(2)
    c1.metric("이번 주 순수익", f"{fmt(tw_profit)}원",
              delta=f"{((tw_profit / lw_profit - 1) * 100):.1f}%" if lw_profit > 0 else None)
    c2.metric("이번 주 주문건수", f"{sum(s['cnt'] for s in tw)}건")

    st.subheader("월별 수익 추이")
    monthly = get_monthly_stats(USERNAME)
    if monthly:
        mdf = pd.DataFrame(monthly)
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=mdf['month'], y=mdf['total_profit'], mode='lines+markers+text',
            line=dict(color='#7F77DD', width=3), marker=dict(size=10),
            text=mdf['total_profit'].apply(lambda x: f"{x/10000:.1f}만"),
            textposition='top center',
        ))
        fig2.update_layout(
            height=300, margin=dict(l=20, r=20, t=20, b=40),
            yaxis_tickformat=",", plot_bgcolor='rgba(0,0,0,0)',
        )
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("상품별 수익 순위")
    ranking = get_product_ranking(USERNAME, today.strftime("%Y-%m"))
    if ranking:
        rdf = pd.DataFrame(ranking)
        rdf.columns = ['상품명', '판매수량', '매출', '순수익']
        rdf.index = range(1, len(rdf) + 1)
        st.dataframe(
            rdf.style.format({'매출': '{:,.0f}', '순수익': '{:,.0f}'}),
            use_container_width=True,
        )


def _render_calendar(USERNAME: str, today: datetime):
    """📅 월별 달력 — 각 날짜에 주문건/수익금/정산금 표시."""
    st.subheader("📅 달력 (일별 주문 · 수익 · 정산)")

    # 월 선택 (데이터 있는 월 + 현재 월)
    _months = [m['month'] for m in (get_monthly_stats(USERNAME) or [])]
    cur_month = today.strftime("%Y-%m")
    if cur_month not in _months:
        _months.append(cur_month)
    _months = sorted(set(_months), reverse=True)
    sel_month = st.selectbox("월 선택", _months,
                             index=_months.index(cur_month) if cur_month in _months else 0,
                             key="dash_cal_month", label_visibility="collapsed")

    y, m = int(sel_month[:4]), int(sel_month[5:7])
    last_day = _calendar.monthrange(y, m)[1]
    stats = get_date_range_stats(USERNAME, f"{sel_month}-01", f"{sel_month}-{last_day:02d}")
    by_date = {s['order_date']: s for s in stats}

    # 헤더 (일~토)
    week_hdr = ['일', '월', '화', '수', '목', '금', '토']
    hdr = ''.join(
        f'<th style="padding:6px;border:1px solid #e9ecef;background:#f8f9fa;'
        f'color:{"#E74C3C" if i == 0 else "#3477eb" if i == 6 else "#333"};'
        f'font-weight:600;width:14.28%">{d}</th>'
        for i, d in enumerate(week_hdr)
    )

    cal = _calendar.Calendar(firstweekday=6)  # 일요일 시작
    rows = []
    for week in cal.monthdatescalendar(y, m):
        cells = []
        for i, d in enumerate(week):
            if d.month != m:
                cells.append('<td style="border:1px solid #f1f3f5;background:#fcfcfc;'
                             'height:120px;vertical-align:top"></td>')
                continue
            ds = d.strftime("%Y-%m-%d")
            s = by_date.get(ds)
            daycol = "#E74C3C" if i == 0 else "#3477eb" if i == 6 else "#333"
            bg = "#f0fbf6" if ds == today.strftime("%Y-%m-%d") else "white"
            inner = f'<div style="font-weight:700;font-size:18px;color:{daycol}">{d.day}</div>'
            if s:
                cnt = int(s.get('cnt') or 0)
                pf = int(s.get('total_profit') or 0)
                se = int(s.get('total_settlement') or 0)
                pfcol = "#1D9E75" if pf >= 0 else "#E74C3C"
                inner += (
                    f'<div style="font-size:16px;color:#555">🧾 {cnt}건</div>'
                    f'<div style="font-size:16px;color:{pfcol};font-weight:600">💰 {pf:,}</div>'
                    f'<div style="font-size:16px;color:#777">📋 {se:,}</div>'
                )
            cells.append(f'<td style="border:1px solid #e9ecef;background:{bg};'
                         f'height:120px;vertical-align:top;padding:4px">{inner}</td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')

    st.markdown(
        '<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
        f'<thead><tr>{hdr}</tr></thead><tbody>{"".join(rows)}</tbody></table>',
        unsafe_allow_html=True,
    )

    m_cnt = sum(int(s.get('cnt') or 0) for s in stats)
    m_pf = sum(int(s.get('total_profit') or 0) for s in stats)
    m_se = sum(int(s.get('total_settlement') or 0) for s in stats)
    st.caption(f"📆 {sel_month} 합계 — 주문 {m_cnt}건 · 수익 {fmt(m_pf)}원 · 정산 {fmt(m_se)}원")
