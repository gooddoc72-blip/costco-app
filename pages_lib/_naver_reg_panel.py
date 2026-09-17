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
                          backfill_all, set_naver_reg_limit, naver_reg_quota,
                          SOURCE_LABELS)

#: 토큰 주인을 못 찾은 등록분이 모이는 가짜 행 — 사용자가 아니라 한도를 걸 수 없다
UNMATCHED = '(미매칭 토큰)'


def _with_all_users(rows):
    """등록 이력이 없는 사용자도 표에 넣는다.

    naver_reg_summary는 **로그가 있는 사용자만** 돌려준다. 그런데 한도는
    올리기 **전에** 걸어야 의미가 있다 — 한 건도 안 올린 사람에게 상한을 줄
    방법이 없으면 "각 사용자 한도"가 아니라 "이미 올린 사람 한도"가 된다.
    """
    try:
        from db_auth import get_all_users
        # 등록 이력이 있으면 정지·탈퇴 계정도 위 집계에 이미 들어 있다. 여기서
        # 새로 더하는 것은 '앞으로 올릴 사람'이므로 활성 계정만 넣는다.
        _users = [u for u in (get_all_users() or [])
                  if not u.get('is_admin') and str(u.get('status') or 'active') == 'active']
    except Exception:
        return rows
    _have = {r['username'] for r in rows}
    for u in _users:
        if u['username'] in _have:
            continue
        q = naver_reg_quota(u['username'])
        rows.append({
            'username': u['username'], 'display_name': u.get('display_name') or '',
            'total': 0, 'period': 0, 'by_source': {}, 'agency': 0, 'last_at': '',
            'limit': q['limit'], 'remaining': q['remaining'], 'blocked': q['blocked'],
        })
    rows.sort(key=lambda d: -d['total'])
    return rows


def render(key_prefix="nvlog", show_backfill=True, nested=False, editable=False):
    """집계 표 + 건별 로그. 반환: 집계 행 리스트(호출측이 건수를 쓸 수 있게).

    editable: 표 안에서 사용자별 등록 한도를 고칠 수 있게 한다. **관리자 화면에서만**
              켠다. 기본값을 False로 둔 이유는, 이 패널이 '네이버 등록' 탭에도
              붙어 있어서 나중에 관리자 게이트 밖으로 새어 나가면 사용자가 자기
              한도를 스스로 올리게 되기 때문이다 — 기본이 안전한 쪽이어야 한다.
    """
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
    _rows = _with_all_users(naver_reg_summary(_df, _dt))
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
            # 편집 가능한 열이라 숫자로 통일한다 — '무제한'을 글자로 넣으면
            # 숫자 열이 되지 못해 표 안에서 고칠 수 없다. 0이 곧 무제한이다.
            "한도(0=무제한)": int(_r['limit'] or 0),
            "남음": ('—' if _r['remaining'] is None
                    else ('🚫 소진' if _r['blocked'] else _r['remaining'])),
            "최근등록": _r['last_at'],
        })

    if not editable:
        _ro = pd.DataFrame(_tbl).rename(columns={"한도(0=무제한)": "한도"})
        _ro["한도"] = _ro["한도"].apply(lambda v: int(v) if int(v or 0) else '무제한')
        st.dataframe(_ro, use_container_width=True, hide_index=True)
        st.caption(f"총 {sum(r['total'] for r in _rows):,}건 · "
                   f"사용자 {len(_rows)}명  ·  집계 주체는 **토큰 소유자**입니다 "
                   "(관리자가 대행등록해도 그 사용자 몫으로 셉니다).")
        return _render_log(_rows, key_prefix, nested)

    # ── 한도를 이 표에서 바로 고친다 ──────────────────────────
    #   한도 설정은 회원 관리에 사용자별로 하나씩 있었다. 누구를 얼마로 잡을지는
    #   **다른 사람 사용량과 비교해서** 정하는 일이라, 사용량이 보이는 이 표에서
    #   고칠 수 있어야 한다. 사람마다 화면을 열고 닫으면 비교가 안 된다.
    _ed = st.data_editor(
        pd.DataFrame(_tbl), use_container_width=True, hide_index=True,
        key=f"{key_prefix}_limit_ed",
        disabled=[c for c in _tbl[0] if c != "한도(0=무제한)"] if _tbl else True,
        column_config={
            "한도(0=무제한)": st.column_config.NumberColumn(
                "한도(0=무제한)", format='%d', min_value=0, max_value=1000000, step=50,
                help="이 사용자의 네이버 토큰으로 등록할 수 있는 **누적** 상품 수. "
                     "수동등록·무인자동·카페24 대행을 모두 합산합니다. 0이면 제한 없음."),
            **{_k: st.column_config.NumberColumn(_k, format='%d')
               for _k in ("누적", "기간내", "수동", "무인", "카페24대행", "과거분")},
        })

    _new = _ed.to_dict('records')
    _chg = []
    for _i, _r in enumerate(_new):
        _u = _rows[_i]['username']
        if _u == UNMATCHED:          # 사용자가 아니라 한도를 걸 대상이 아니다
            continue
        _lv = max(0, int(_r.get("한도(0=무제한)") or 0))
        if _lv != int(_rows[_i]['limit'] or 0):
            _chg.append((_u, _lv))
    _sc1, _sc2 = st.columns([1.4, 3])
    if _sc1.button(f"💾 한도 {len(_chg)}명 저장", key=f"{key_prefix}_limit_save",
                   type="primary" if _chg else "secondary", disabled=not _chg,
                   use_container_width=True):
        for _u, _lv in _chg:
            set_naver_reg_limit(_u, _lv)
        st.success("✅ 한도 저장 — " + " · ".join(
            f"{_u} {_lv if _lv else '무제한'}" for _u, _lv in _chg[:8])
            + (f" 외 {len(_chg) - 8}명" if len(_chg) > 8 else ""))
        st.rerun()
    if _chg:
        _sc2.caption("✏️ 고친 한도 " + " · ".join(
            f"**{_u}** → {_lv if _lv else '무제한'}" for _u, _lv in _chg[:6])
            + " — **저장을 눌러야 적용됩니다.**")
    else:
        _sc2.caption("한도 칸을 고치고 저장을 누르면 즉시 적용됩니다. "
                     "소진되면 수동등록·무인자동·카페24 대행이 모두 막히고, "
                     "사용자에게는 관리자에게 상향을 요청하라는 안내가 나갑니다.")

    st.caption(f"총 {sum(r['total'] for r in _rows):,}건 · "
               f"사용자 {len(_rows)}명  ·  집계 주체는 **토큰 소유자**입니다 "
               "(관리자가 대행등록해도 그 사용자 몫으로 셉니다).")

    return _render_log(_rows, key_prefix, nested)


def _render_log(_rows, key_prefix, nested):
    """📋 건별 등록 로그 — 집계 표가 읽기 전용이든 편집 가능이든 같은 화면이다."""
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
