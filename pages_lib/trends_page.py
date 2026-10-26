"""🔥 SNS 트렌드 TOP 30 (관리자) — 코코비즈 메뉴에서 여는 입구.

조사·에이전트 회의·승인은 새 화면(/m/trends)에서 한다. 조사는 몇 분 걸리고 진행 로그를
계속 봐야 해서, 버튼 하나에 화면 전체를 다시 그리는 Streamlit에는 맞지 않는다.
여기서는 마지막 보고서를 읽기만 하고, 새 화면으로 같은 로그인 그대로 넘긴다.
"""
import pandas as pd
import streamlit as st

from utils import fmt


def render(USERNAME, IS_ADMIN, settings=None):
    if not IS_ADMIN:
        st.error("관리자 전용 화면입니다.")
        return
    st.header("🔥 SNS 트렌드 TOP 30")
    st.caption("블로그·카페·유튜브에서 뜨는 코스트코 상품을 찾아 점수를 매기고, "
               "**에이전트 회의**(조사원 → 분석가 A·B → 토론 → 검증관 → 규칙 검사)가 "
               "주력·시도·관찰 등급과 판매 제안을 정합니다. 조사·승인은 새 화면에서 합니다.")
    _sid = str(st.session_state.get('_sid') or '')
    st.link_button("🆕 새 화면에서 열기 — 지금 조사 · 회의록 · ✅ 승인",
                   "/m/trends" + (f"?sid={_sid}" if _sid else ""), type="primary")
    try:
        import trend_research as tr
        rep = tr.get_report()
        dec = tr.get_decisions(rep['id']) if rep else {}
    except Exception as e:
        st.warning(f"보고서를 읽지 못했습니다: {e}")
        return
    if not rep:
        st.info("아직 조사 결과가 없습니다 — 새 화면에서 **지금 조사**를 누르세요.")
        return
    c = rep.get('council') or {}
    st.markdown(f"**마지막 조사 {rep.get('created_at', '')}** · 글 {fmt(rep.get('docs', 0))}건 · "
                f"후보 {rep.get('candidates', 0)}개"
                + (f" · 🧑‍⚖️ 합의 {c.get('agreed', 0)} · 판정 {c.get('disputed', 0)} · "
                   f"제외 {len(c.get('excluded') or [])}" if c else ""))
    _lab = {'approve': '✅ 승인', 'hold': '⏸ 보류'}
    rows = []
    for x in rep.get('items') or []:
        ai = x.get('ai') or {}
        rows.append({
            '순위': x.get('rank'), '상품': x.get('name', ''), '등급': ai.get('grade', ''),
            '판정': (x.get('council') or {}).get('how', ''),
            '월 검색수': max(int(x.get('volume') or 0), int(x.get('volume_checked') or 0)),
            '우리 판매 14일': x.get('sales_recent', 0), '추천가': int(ai.get('price') or x.get('suggest_price') or 0),
            '예상 마진': x.get('margin', 0), '승인': _lab.get((dec.get(x.get('costco_no')) or {}).get('decision'), ''),
            '이유': ai.get('reason', ''),
        })
    _g = st.segmented_control("등급", ['전체', '주력', '시도', '관찰'], default='전체',
                              key='trend_grade', label_visibility='collapsed') or '전체'
    if _g != '전체':
        rows = [r for r in rows if r['등급'] == _g]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={'월 검색수': st.column_config.NumberColumn(format='%d'),
                                '추천가': st.column_config.NumberColumn(format='%d'),
                                '예상 마진': st.column_config.NumberColumn(format='%d'),
                                '이유': st.column_config.TextColumn(width='large')})
    st.caption("회의록(분석가 의견·토론·검증관 판정·규칙 적용)과 승인은 새 화면에서 상품 줄을 눌러 봅니다.")
