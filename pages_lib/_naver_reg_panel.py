"""사용자별 네이버 등록 집계 패널 — 관리자 탭과 네이버 등록 탭이 공유.

관리자가 등록 작업을 하는 곳은 '네이버 등록' 탭인데 집계는 관리자 탭에만 있어
확인하려면 화면을 옮겨야 했다. 같은 표를 두 곳에 복사하면 한쪽만 고쳐지므로
렌더 코드를 여기 한 곳에 둔다.

key_prefix: Streamlit 위젯 키 접두사. 같은 세션에서 두 화면이 각자 필터 상태를
            갖도록 분리한다(한쪽에서 고른 기간이 다른 쪽에 끌려가지 않게).
nested:     호출측이 이미 expander 안이면 True. Streamlit은 expander 중첩을
            거부하므로(StreamlitAPIException) 건별 로그를 체크박스로 연다.
"""
import streamlit as st
import pandas as pd

from db_naver_reg import (naver_reg_summary, list_naver_registrations,
                          backfill_all, SOURCE_LABELS)


def render(key_prefix="nvlog", show_backfill=True, nested=False):
    """집계 표 + 건별 로그. 반환: 집계 행 리스트(호출측이 건수를 쓸 수 있게)."""
    _c1, _c2, _c3 = st.columns([1, 1, 1.4])
    _from = _c1.date_input("기간 시작", value=None, key=f"{key_prefix}_from")
    _to = _c2.date_input("기간 끝", value=None, key=f"{key_prefix}_to")
    if show_backfill:
        with _c3:
            st.write("")
            # 이 기능 이전 등록분은 로그에 없어 한도가 0에서 시작한다. products에
            # 남은 원상품번호로 과거분을 한 번 채워 실사용량에 맞춘다.
            if st.button("📥 과거 등록분 소급 집계", key=f"{key_prefix}_backfill",
                         help="사용자 상품DB의 네이버 원상품번호를 근거로, 이 기능 "
                              "이전에 등록된 건을 '과거분(소급)'으로 채웁니다. "
                              "여러 번 눌러도 중복되지 않습니다."):
                _bf = backfill_all()
                if _bf:
                    st.success("✅ 소급 완료 — " + ", ".join(
                        f"{_k} {_v}건" for _k, _v in _bf.items()))
                else:
                    st.info("소급할 과거 등록분이 없습니다 (이미 반영됨).")

    _df = str(_from) if _from else ''
    _dt = str(_to) if _to else ''
    _rows = naver_reg_summary(_df, _dt)
    if not _rows:
        st.info("아직 기록된 네이버 등록이 없습니다."
                + (" (이 기능 이전 등록분은 '과거 등록분 소급 집계'로 채울 수 있습니다)"
                   if show_backfill else ""))
        return _rows

    _tbl = []
    for _r in _rows:
        _bs = _r['by_source']
        _tbl.append({
            "사용자": _r['username'] + (f" ({_r['display_name']})"
                                      if _r['display_name'] else ''),
            "누적": _r['total'],
            "기간내": _r['period'] if (_df or _dt) else _r['total'],
            "수동": _bs.get('manual', 0),
            "무인": _bs.get('auto', 0),
            "카페24대행": _bs.get('cafe24', 0),
            "과거분": _bs.get('backfill', 0),
            "한도": _r['limit'] or '무제한',
            "남음": ('—' if _r['remaining'] is None
                    else ('🚫 소진' if _r['blocked'] else _r['remaining'])),
            "최근등록": _r['last_at'],
        })
    st.dataframe(pd.DataFrame(_tbl), use_container_width=True, hide_index=True)
    st.caption(f"총 {sum(r['total'] for r in _rows):,}건 · "
               f"사용자 {len(_rows)}명  ·  집계 주체는 **토큰 소유자**입니다 "
               "(관리자가 대행등록해도 그 사용자 몫으로 셉니다).")

    if nested:
        # expander 안 — 중첩이 금지되므로 체크박스로 여닫는다
        if not st.checkbox("📋 최근 등록 로그 (건별) 보기",
                           key=f"{key_prefix}_showlog"):
            return _rows
        _log_box = st.container()
    else:
        _log_box = st.expander("📋 최근 등록 로그 (건별)", expanded=False)

    with _log_box:
        _users = [''] + [r['username'] for r in _rows]
        _lc1, _lc2 = st.columns([1, 1])
        _u = _lc1.selectbox("사용자", _users, key=f"{key_prefix}_user",
                            format_func=lambda v: v or "전체")
        _s = _lc2.selectbox(
            "경로", ['', 'manual', 'auto', 'cafe24', 'backfill'],
            key=f"{key_prefix}_src",
            format_func=lambda v: SOURCE_LABELS.get(v, v) if v else "전체")
        _lg = list_naver_registrations(_u, source=_s, limit=500)
        if not _lg:
            st.caption("해당 조건의 로그가 없습니다.")
        else:
            st.dataframe(pd.DataFrame([{
                "일시": _x['created_at'], "사용자": _x['username'],
                "상품명": _x['product_name'], "원상품번호": _x['origin_no'],
                "판매가": _x['sale_price'],
                "경로": SOURCE_LABELS.get(_x['source'], _x['source'] or '기타'),
                "대행자": _x['actor'] or '—',
                "토큰": _x['client_tag'] or '—',
            } for _x in _lg]), use_container_width=True, hide_index=True)
            st.caption(f"최근 {len(_lg)}건 (최대 500건)")
    return _rows
