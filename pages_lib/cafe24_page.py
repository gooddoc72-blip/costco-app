"""🛒 카페24 — 대행 등록 + 코스트코 매칭·동기화 (관리자 전용)."""
import streamlit as st
import pandas as pd

from db import (
    get_global_setting, set_global_setting,
    get_all_users, get_all_settings, get_shared_products,
)
from utils import fmt, calc_match_score

try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False
    naver_api = None


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    st.title("🛒 카페24")
    # 관리자 또는 관리자가 이 사용자에게 '카페24 메뉴'를 오픈한 경우만.
    #   (관리자 페이지 › 사용자 목록에서 cafe24_menu_open 체크로 오픈/숨김)
    from db import get_setting
    if not (IS_ADMIN or get_setting(USERNAME, 'cafe24_menu_open') == '1'):
        st.warning("접근 권한이 없는 메뉴입니다. 관리자에게 문의하세요.")
        return

    # ── 🛒 카페24 → 사용자 스토어 대행 등록 ──────────────────────────
    st.subheader("🛒 카페24 → 사용자 스토어 대행 등록")
    st.caption("대상 사용자를 고르고, 공용 카페24 카탈로그에서 상품을 불러와 그 사용자의 네이버 스토어에 대행 등록합니다.")

    _ag_cf = {_k: (get_global_setting('cafe24_' + _k) or '') for _k in
              ('mall_id', 'client_id', 'client_secret', 'access_token', 'refresh_token', 'token_expires_at')}
    if not (_ag_cf['mall_id'] and _ag_cf['client_id'] and _ag_cf['access_token']):
        st.info("공용 카페24 자격증명이 없습니다. 설정 탭에서 카페24를 먼저 연결하세요(관리자 계정).")
    elif not HAS_NAVER_API:
        st.error("naver_api 없음 — 관리자에게 문의하세요.")
    else:
        import cafe24_api
        import cafe24_register_service as c24reg
        import db_cafe24_queue as _c24q
        _ag_creds = {'mall_id': _ag_cf['mall_id'], 'client_id': _ag_cf['client_id'],
                     'client_secret': _ag_cf['client_secret'], 'access_token': _ag_cf['access_token'],
                     'refresh_token': _ag_cf['refresh_token'], 'expires_at': _ag_cf['token_expires_at']}

        def _ag_save(t):
            for _k, _v in (('cafe24_access_token', t.get('access_token', '')),
                           ('cafe24_refresh_token', t.get('refresh_token', '')),
                           ('cafe24_token_expires_at', t.get('expires_at', ''))):
                set_global_setting(_k, _v)

        # 대상 후보 — 빈/손상 계정(username 공백) 제외. 각 사용자 커머스 API 보유 여부를
        # 미리 확인해 라벨에 표시하고 '키 있는 사용자'를 위로 정렬(기본 선택이 등록 가능 계정).
        _ag_all = [u for u in get_all_users()
                   if (not u.get('is_admin')) and u.get('status', 'active') == 'active'
                   and str(u.get('username') or '').strip()]
        _ag_meta = []
        for u in _ag_all:
            _us = get_all_settings(u['username']) or {}
            _has = bool(str(_us.get('api_client_id') or '').strip()
                        and str(_us.get('api_client_secret') or '').strip())
            _ag_meta.append((u['username'], u.get('display_name', '') or '', _has, _us))
        _ag_meta.sort(key=lambda t: (not t[2], t[0]))  # 키 보유자 먼저, 그다음 이름순
        if not _ag_meta:
            st.info("등록 대상이 될 일반 사용자가 없습니다.")
        else:
            _ag_labelmap = {}  # 표시라벨 → (username, settings)
            _ag_opts = []
            for _un, _dn, _has, _us in _ag_meta:
                _lbl = (f"{'✅' if _has else '⚠️'} {_un} · {_dn}"
                        + ('' if _has else ' — 커머스API 없음'))
                _ag_labelmap[_lbl] = (_un, _us)
                _ag_opts.append(_lbl)
            _n_ready = sum(1 for t in _ag_meta if t[2])
            _agc1, _agc2 = st.columns([2, 1])
            _ag_pick = _agc1.selectbox(
                f"🎯 등록 대상 사용자 (커머스API 보유 {_n_ready}/{len(_ag_meta)}명)",
                _ag_opts, key="ag_target",
                help="✅ = 네이버 커머스 API 등록됨(대행등록 가능). ⚠️ = 키 없음(그 사용자 설정 탭에서 입력 필요).")
            _ag_margin = _agc2.number_input("마진율 %", min_value=0, max_value=300, step=5,
                                            value=int(get_global_setting('cafe24_naver_margin') or 10),
                                            key="ag_margin")
            _ag_tuser, _ag_ts = _ag_labelmap[_ag_pick]
            _ag_tid = _ag_ts.get('api_client_id', ''); _ag_tsecret = _ag_ts.get('api_client_secret', '')
            _ag_tas = _ag_ts.get('naver_as_tel') or '1588-1234'
            _ag_oc = settings.get('naver_open_client_id', ''); _ag_os = settings.get('naver_open_client_secret', '')
            _ag_ai = get_global_setting('anthropic_api_key') or settings.get('anthropic_api_key', '')
            _ag_gai = get_global_setting('gemini_api_key') or settings.get('gemini_api_key', '')
            # 검색광고 API (연관키워드 조회수 기반 상품명용) — 관리자 키 글로벌 우선
            _ad_key = get_global_setting('naver_ad_api_key') or settings.get('naver_ad_api_key', '')
            _ad_sec = get_global_setting('naver_ad_secret') or settings.get('naver_ad_secret', '')
            _ad_cust = get_global_setting('naver_ad_customer_id') or settings.get('naver_ad_customer_id', '')
            _ad_creds = (_ad_key, _ad_sec, _ad_cust) if all((_ad_key, _ad_sec, _ad_cust)) else None

            if not (_ag_tid and _ag_tsecret):
                st.warning(f"⚠️ '{_ag_tuser}'의 네이버 커머스 API 키가 없어 등록할 수 없습니다. "
                           "그 사용자 설정 탭에 네이버 키를 먼저 입력하세요.")
            else:
                if not (_ag_oc and _ag_os):
                    st.info("💡 카테고리 자동판단에 관리자 네이버 Open API 키가 필요합니다(설정 탭).")
                _kwmark = "✅ 연관키워드 상품명(저경쟁 100~300+대표어)" if _ad_creds else \
                    "⚠️ 연관키워드 상품명 OFF — 검색광고 API 키 미설정(설정 탭). 카페24 원본명으로 등록"
                st.caption(f"등록 시 자동 적용: {_kwmark} · ✅ 태그ID 자동 · ✅ 카페24 속성(제조사/모델/원산지)")
                # 상세페이지 방식 — 기본은 '카페24 원본 그대로'.
                # 이미지 스택은 원본 레이아웃(텍스트·표·순서)을 잃는다.
                _ag_dmode = st.radio(
                    "🖼 상세페이지 방식", ["카페24 원본 그대로", "이미지만 쌓기"],
                    horizontal=True, key="ag_dmode",
                    help="원본 그대로: 카페24 상세 HTML의 구조·텍스트를 유지하고 이미지만 "
                         "네이버 CDN으로 옮깁니다(이미지는 리사이즈 없이 원본 업로드). "
                         "이미지만 쌓기: 상세이미지를 순서대로 나열합니다(편집은 쉽지만 원본 레이아웃 손실).")
                _ag_detail_mode = 'html' if _ag_dmode == "카페24 원본 그대로" else 'image'
                # 공통 상단/하단 고정 이미지 — 대상 사용자 설정 우선, 없으면 관리자 것
                _ag_top = (str(_ag_ts.get('naver_detail_top_img') or '').strip()
                           or str(settings.get('naver_detail_top_img') or '').strip())
                _ag_bot = (str(_ag_ts.get('naver_detail_bottom_img') or '').strip()
                           or str(settings.get('naver_detail_bottom_img') or '').strip())
                st.caption(
                    f"상단 고정이미지 {'✅' if _ag_top else '— 없음'} · "
                    f"하단 고정이미지 {'✅' if _ag_bot else '— 없음'}"
                    + ("" if (_ag_top or _ag_bot) else
                       "  (‘네이버 등록’ 탭 › 공통 이미지에서 등록하면 여기에도 적용됩니다)"))
                _ag_photo_ai = st.checkbox(
                    "📷 AI 제품사진 분석으로 상품명·속성 생성 (제품사진 등록 방식)",
                    value=bool(_ag_ai or _ag_gai), key="ag_photoai",
                    disabled=not (_ag_ai or _ag_gai),
                    help="대표 제품이미지를 AI 비전(Gemini 우선·Claude 폴백)으로 분석해 상품명·원산지·브랜드를 뽑고, "
                         "그 상품명으로 카테고리 판단·연관키워드 최적화까지 진행합니다.")
                # 조회 방식: 상품명 검색 / 카테고리 (카페24 분류)
                _ag_mode = st.radio("조회 방식",
                                    ["상품명 검색", "카테고리", "메인 진열"],
                                    horizontal=True,
                                    key="ag_mode", label_visibility="collapsed")
                _ag_q, _ag_cat_no, _ag_disp_no = "", None, None
                if _ag_mode == "메인 진열":
                    # 쇼핑몰 메인의 진열 영역(신상품·MD추천·베스트 등).
                    # 몰마다 이름·번호가 달라 하드코딩하지 않고 실제 목록을 읽어 고르게 한다.
                    _disps = st.session_state.get('_ag_disps')
                    if _disps is None:
                        with st.spinner("카페24 메인 진열 목록 불러오는 중..."):
                            _disps, _derr = cafe24_api.list_main_displays(
                                _ag_creds, save_tokens=_ag_save)
                        if _derr:
                            st.warning(f"진열 목록을 못 불러왔습니다: {_derr}")
                            _disps = []
                        st.session_state['_ag_disps'] = _disps
                    _dc1, _dc2 = st.columns([4, 1])
                    if _disps:
                        _dmap = {f"{d['name']} (#{d['display_group']})": d['display_group']
                                 for d in _disps}
                        _dpick = _dc1.selectbox("메인 진열 영역", list(_dmap.keys()),
                                                key="ag_disp")
                        _ag_disp_no = _dmap.get(_dpick)
                    else:
                        _ag_disp_no = _dc1.number_input("진열그룹 번호(직접 입력)",
                                                        min_value=0, step=1,
                                                        key="ag_disp_no") or None
                    if _dc2.button("🔄 새로고침", key="ag_disp_reload"):
                        st.session_state.pop('_ag_disps', None)
                        st.rerun()
                    st.caption("⚠️ 이름이 비슷한 영역이 여럿일 수 있습니다(예: '신상품'과 "
                               "'이번주 신상품'). 조회해서 건수를 보고 고르세요.")
                elif _ag_mode == "카테고리":
                    _cats = st.session_state.get('_ag_cats')
                    if not _cats:                      # 실패는 캐시하지 않음(재인증 후 바로 반영)
                        with st.spinner("카페24 분류 불러오는 중..."):
                            _cats, _cerr = cafe24_api.list_categories(_ag_creds, save_tokens=_ag_save)
                        if _cerr:
                            st.warning(f"분류 목록을 못 불러왔습니다: {_cerr}")
                            if 'insufficient_scope' in str(_cerr) or '403' in str(_cerr):
                                st.caption("→ 설정 탭에서 **카페24 인증**을 다시 하면 분류 조회 권한이 추가됩니다. "
                                           "(카페24 개발자센터 앱 권한에 '상품분류(Category) 읽기'가 추가되어 있어야 합니다)")
                            _cats = []
                        else:
                            st.session_state['_ag_cats'] = _cats
                    elif st.button("🔄 분류 목록 새로고침", key="ag_cat_reload"):
                        st.session_state.pop('_ag_cats', None)
                        st.rerun()
                    if _cats:
                        _cmap = {f"{c['name']} (#{c['category_no']})": c['category_no'] for c in _cats}
                        _cpick = st.selectbox("카페24 분류", list(_cmap.keys()), key="ag_cat")
                        _ag_cat_no = _cmap.get(_cpick)
                    else:
                        _ag_cat_no = st.number_input("분류 번호(직접 입력)", min_value=0, step=1,
                                                     key="ag_cat_no") or None
                else:
                    _ag_q = st.text_input("카페24 상품명 검색(비우면 최근)", key="ag_q",
                                          label_visibility="collapsed",
                                          placeholder="카페24 상품명 일부 — 예: 콩국, 소갈비찜")

                # ── 📦 배치 대기열 — 300건 규모는 여기로 ──────────────
                # UI 루프는 브라우저를 켜둬야 진행되고 건당 15~60초라
                # 수백 건엔 못 쓴다. 분류째로 대기열에 담아 크론이 나눠 소화한다.
                with st.expander("📦 대기열 배치 등록 (수백 건은 이쪽)", expanded=False):
                    _qc = _c24q.counts(_ag_tuser)
                    _qm1, _qm2, _qm3, _qm4 = st.columns(4)
                    _qm1.metric("대기", _qc['pending'])
                    _qm2.metric("등록완료", _qc['done'])
                    _qm3.metric("중복스킵", _qc['skipped'])
                    _qm4.metric("실패", _qc['failed'])

                    _q_on = st.checkbox(
                        "🟢 자동 배치 등록 켜기 (크론이 매시간 대기열을 소화)",
                        value=get_global_setting('cafe24_register_enabled') == '1',
                        key="ag_q_on",
                        help="꺼두면 대기열에 담아만 두고 등록은 진행되지 않습니다. "
                             "실제 스토어에 상품이 올라가므로 기본은 꺼짐입니다.")
                    _q_max = st.number_input(
                        "회당 최대 등록 건수", min_value=5, max_value=200, step=5,
                        value=int(get_global_setting('cafe24_register_max') or 30),
                        key="ag_q_max",
                        help="한 번에 몰아치면 네이버 API 한도와 서버 메모리에 걸립니다. "
                             "매시간 30건이면 300건을 약 10시간에 소화합니다.")
                    if st.button("💾 배치 설정 저장", key="ag_q_save"):
                        set_global_setting('cafe24_register_enabled', '1' if _q_on else '0')
                        set_global_setting('cafe24_register_max', str(int(_q_max)))
                        set_global_setting('cafe24_naver_margin', str(int(_ag_margin)))
                        set_global_setting('cafe24_register_detail_mode', _ag_detail_mode)
                        set_global_setting('cafe24_register_photo_ai',
                                           '1' if _ag_photo_ai else '0')
                        st.success("저장했습니다. 마진·상세이미지·AI사진분석은 위 설정을 따릅니다.")

                    st.markdown("**통째로 대기열에 담기**")
                    if _ag_mode == "상품명 검색":
                        st.caption("↑ 조회 방식을 '카테고리' 또는 '메인 진열'로 바꾸면 "
                                   "그 묶음 전체를 한 번에 담을 수 있습니다.")
                    else:
                        _is_disp = (_ag_mode == "메인 진열")
                        _qcap = st.number_input("최대 담을 건수", min_value=10, max_value=3000,
                                                step=50, value=300, key="ag_q_cap")
                        _qlabel = ("➕ 이 진열영역 전체를" if _is_disp else "➕ 이 분류 전체를")
                        if st.button(f"{_qlabel} '{_ag_tuser}' 대기열에 담기",
                                     key="ag_q_fill",
                                     disabled=not (_ag_disp_no if _is_disp else _ag_cat_no)):
                            with st.spinner("카페24 상품 조회 중..."):
                                if _is_disp:
                                    _qp, _qerr = cafe24_api.get_main_display_products(
                                        _ag_creds, _ag_disp_no, save_tokens=_ag_save,
                                        max_total=int(_qcap))
                                else:
                                    _qp, _qerr = cafe24_api.search_all_products(
                                        _ag_creds, category_no=_ag_cat_no,
                                        save_tokens=_ag_save, max_total=int(_qcap))
                            if _qerr:
                                st.error(f"조회 실패: {_qerr}")
                            else:
                                _na, _ns = _c24q.enqueue(_ag_tuser, _qp or [])
                                st.success(f"조회 {len(_qp or [])}개 → 새로 담음 {_na}건 "
                                           f"/ 이미 대기열에 있음 {_ns}건")
                                st.rerun()

                    # ── 실패 사유별 분류 — '실패 12건'만으로는 조치를 못 한다 ──
                    if _qc['failed']:
                        st.markdown("**❌ 실패 사유별 분류**")
                        _rc = _c24q.fail_reason_counts(_ag_tuser)
                        st.dataframe(pd.DataFrame([
                            {'사유': c24reg.reason_label(_code), '건수': _n,
                             '조치': c24reg.reason_hint(_code)}
                            for _code, _n in _rc]),
                            use_container_width=True, hide_index=True)
                        _rmap = {f"{c24reg.reason_label(c)} ({n}건)": c for c, n in _rc}
                        _rpick = st.selectbox("사유 선택 — 목록 보기 / 그 사유만 재시도",
                                              list(_rmap.keys()), key="ag_q_reason")
                        _rcode = _rmap.get(_rpick)
                        _rr1, _rr2 = st.columns(2)
                        if _rr1.button(f"🔁 이 사유만 다시 대기로", key="ag_q_retry_one"):
                            _n = _c24q.requeue_failed(_ag_tuser, reason=_rcode)
                            st.success(f"{_n}건을 대기 상태로 되돌렸습니다."); st.rerun()
                        _frows = _c24q.list_rows(_ag_tuser, 'failed', limit=500,
                                                 reason=_rcode)
                        if _frows:
                            _fdf = pd.DataFrame([
                                {'상품번호': r['product_no'],
                                 '상품명': r['product_name'],
                                 '사유': (r['detail'] or ''), '갱신': r['updated_at']}
                                for r in _frows])
                            st.dataframe(_fdf, use_container_width=True, hide_index=True)
                            _rr2.download_button(
                                "⬇️ 이 목록 CSV", _fdf.to_csv(index=False).encode('utf-8-sig'),
                                file_name=f"실패_{_rcode}_{_ag_tuser}.csv",
                                mime="text/csv", key="ag_q_csv")
                        else:
                            st.caption("사유 코드가 없는 과거 기록입니다 — 아래 '대기열 보기'에서 확인하세요.")
                        st.divider()

                    _qb1, _qb2, _qb3 = st.columns(3)
                    if _qb1.button(f"🔁 실패 {_qc['failed']}건 전부 다시 대기로", key="ag_q_retry",
                                   disabled=not _qc['failed']):
                        _n = _c24q.requeue_failed(_ag_tuser)
                        st.success(f"{_n}건을 대기 상태로 되돌렸습니다."); st.rerun()
                    if _qb2.button("🧹 처리완료분 비우기", key="ag_q_clean",
                                   disabled=not (_qc['done'] or _qc['skipped'])):
                        _n = _c24q.clear(_ag_tuser, 'done') + _c24q.clear(_ag_tuser, 'skipped')
                        st.success(f"{_n}건 삭제했습니다."); st.rerun()
                    if _qb3.button("🗑 대기열 전체 삭제", key="ag_q_wipe",
                                   disabled=not _qc['total']):
                        _n = _c24q.clear(_ag_tuser)
                        st.warning(f"{_n}건 삭제했습니다."); st.rerun()

                    _qview = st.selectbox("대기열 보기", ["failed", "pending", "done", "skipped"],
                                          key="ag_q_view",
                                          format_func=lambda v: {
                                              'failed': '❌ 실패', 'pending': '⏳ 대기',
                                              'done': '✅ 완료', 'skipped': '⏭ 중복스킵'}[v])
                    _qrows = _c24q.list_rows(_ag_tuser, _qview, limit=300)
                    if _qrows:
                        st.dataframe(pd.DataFrame([
                            {'상품번호': r['product_no'], '상품명': r['product_name'][:40],
                             '분류': c24reg.reason_label(r.get('reason')) if r.get('reason') else '',
                             '사유': (r['detail'] or '')[:70], '갱신': r['updated_at']}
                            for r in _qrows]), use_container_width=True, hide_index=True)
                    else:
                        st.caption("해당 상태의 건이 없습니다.")

                if st.button("🔎 카페24 조회", key="ag_search"):
                    set_global_setting('cafe24_naver_margin', str(int(_ag_margin)))
                    with st.spinner("카페24 상품 조회 중..."):
                        if _ag_mode == "메인 진열":
                            # 진열 영역은 페이징이 없다 — 그 영역 전부를 한 번에 받는다.
                            _prods, _perr = cafe24_api.get_main_display_products(
                                _ag_creds, _ag_disp_no, save_tokens=_ag_save)
                            _tot = len(_prods or [])
                        else:
                            _prods, _perr = cafe24_api.search_products(
                                _ag_creds, _ag_q, limit=100, save_tokens=_ag_save,
                                category_no=_ag_cat_no)
                            _tot, _ = cafe24_api.count_products(
                                _ag_creds, save_tokens=_ag_save, category_no=_ag_cat_no)
                    st.session_state['_ag_prods'] = [] if _perr else (_prods or [])
                    st.session_state['_ag_total'] = _tot
                    st.session_state['_ag_last_q'] = (_ag_q, _ag_cat_no)
                    if _perr:
                        st.error(f"조회 실패: {_perr}")
                _ag_list = st.session_state.get('_ag_prods') or []
                if _ag_list:
                    _ag_show = _ag_list[:100]
                    _tot_n = st.session_state.get('_ag_total')
                    st.caption(f"{len(_ag_list)}개 표시"
                               + (f" / 조건에 맞는 전체 {_tot_n}개" if _tot_n else "")
                               + f" — 체크 후 아래 버튼으로 '{_ag_tuser}' 스토어에 등록")
                    if _ag_mode != "메인 진열" and _tot_n and len(_ag_list) < _tot_n:
                        if st.button(f"⬇️ 다음 100개 더 불러오기 ({len(_ag_list)}/{_tot_n})",
                                     key="ag_more"):
                            _lq, _lc = st.session_state.get('_ag_last_q', ("", None))
                            with st.spinner("추가 조회 중..."):
                                _more, _merr = cafe24_api.search_products(
                                    _ag_creds, _lq, limit=100, save_tokens=_ag_save,
                                    category_no=_lc, offset=len(_ag_list))
                            if _merr:
                                st.error(f"추가 조회 실패: {_merr}")
                            else:
                                st.session_state['_ag_prods'] = _ag_list + (_more or [])
                                st.rerun()
                    # 전체 선택 토글 — 변화 감지 시 개별 체크박스 일괄 설정(위젯 생성 전에 세팅)
                    _ag_all = st.checkbox(f"✅ 전체 선택 ({len(_ag_show)}개)", key="ag_all_toggle")
                    if _ag_all != st.session_state.get('_ag_all_prev'):
                        for _p in _ag_show:
                            st.session_state[f"ag_ck_{_p['product_no']}"] = _ag_all
                        st.session_state['_ag_all_prev'] = _ag_all
                    _ag_sel = []
                    for _p in _ag_show:
                        _pno = _p['product_no']; _pr = int(_p.get('price') or 0)
                        _npr = c24reg.calc_sale_price(_pr, _ag_margin)
                        if st.checkbox(
                                f"{str(_p.get('product_name', ''))[:42]} · 카페24 {fmt(_pr)}원 → 네이버 {fmt(_npr)}원",
                                key=f"ag_ck_{_pno}"):
                            _ag_sel.append((_p, _npr))
                    if st.button(f"🚀 선택 {len(_ag_sel)}개 → '{_ag_tuser}' 스토어 등록", type="primary",
                                 key="ag_reg", disabled=not (_ag_sel and _ag_oc and _ag_os)):
                        _ag_rows = []; _agprog = st.progress(0.0)
                        _ag_prog = _agprog                  # 완료 후 정리용
                        _ag_live = st.empty()               # 진행 중 건별 결과 표시

                        def _ag_tick(_idx, _label, _status):
                            """건별 처리 결과를 진행 중에 바로 보여준다(마지막에 몰아 보지 않게)."""
                            _n_ok = sum(1 for _r in _ag_rows if str(_r.get('상태', '')).startswith('✅'))
                            _ag_live.markdown(
                                f"`{_idx}/{len(_ag_sel)}` {_status} **{_label[:34]}** "
                                f"— 누적 성공 {_n_ok} / 실패 {len(_ag_rows) - _n_ok}")

                        # 중복 등록 방지 — 대상 스토어의 기존 상품을 1회 조회해
                        # 판매자상품코드(코스트코 번호)와 상품명을 대조한다.
                        _ag_have_code, _ag_have_name = set(), set()
                        with st.spinner(f"'{_ag_tuser}' 스토어 기존 상품 확인 중..."):
                            _exist, _eerr = naver_api.get_product_list(_ag_tid, _ag_tsecret)
                        if _eerr:
                            st.warning(f"기존 상품 조회 실패 — 중복 검사 없이 진행합니다: {str(_eerr)[:80]}")
                        else:
                            for _e in (_exist or []):
                                _sc = str(_e.get('sellerManagementCode') or '').strip()
                                if _sc:
                                    _ag_have_code.add(_sc)
                                _nm2 = str(_e.get('productName') or '').strip()
                                if _nm2:
                                    _ag_have_name.add(_nm2)
                            st.caption(f"기존 상품 {len(_exist or [])}개 확인 — 중복은 건너뜁니다.")
                        # 판매자상품코드(코스트코 번호) 결정은 register_one이 한다.
                        # 상품명 매칭에 쓸 공용 코스트코 DB만 여기서 1회 읽어 넘긴다.
                        _ag_shared = get_shared_products() or []

                        _ag_opts = {
                            'detail_mode': _ag_detail_mode,
                            'top_img': _ag_top, 'bottom_img': _ag_bot,
                            'photo_ai': _ag_photo_ai,
                            'gen_tags': True, 'opt_name': True,
                            'ai_key': _ag_ai, 'gemini_key': _ag_gai,
                            'ad_creds': _ad_creds,
                        }
                        _ag_target = {'api_id': _ag_tid, 'api_secret': _ag_tsecret,
                                      'as_tel': _ag_tas}
                        for _i, (_p, _npr) in enumerate(_ag_sel):
                            _agprog.progress((_i + 1) / len(_ag_sel))
                            _res = c24reg.register_one(
                                _ag_creds, _ag_save, _p, _ag_margin, _ag_target, _ag_opts,
                                have_code=_ag_have_code, have_name=_ag_have_name,
                                shared=_ag_shared)
                            _icon = {'ok': '✅', 'skip': '⏭'}.get(_res['status'], '❌')
                            _ag_rows.append({
                                '상품': _res['name'][:24], '코드': _res['code'],
                                '코드출처': _res['code_src'],
                                '카테고리': _res['category'][:20], '태그': _res['tags'],
                                '판매가': _res['price'],
                                '상태': f"{_icon} {_res['detail'][:120]}"})
                            _ag_tick(_i + 1, _res['name'], _icon)
                        _ag_prog.empty()
                        _ok = sum(1 for r in _ag_rows if str(r.get('상태', '')).startswith('✅'))
                        _fail_rows = [r for r in _ag_rows if not str(r.get('상태', '')).startswith('✅')]
                        _msg = (f"🛒 '{_ag_tuser}' 스토어 대행 등록 — "
                                f"성공 {_ok}건 / 실패 {len(_fail_rows)}건 (전체 {len(_ag_rows)}건)")
                        if not _fail_rows:
                            st.success(_msg)
                        elif _ok:
                            st.warning(_msg)
                        else:
                            st.error(_msg)
                        if _fail_rows:
                            with st.expander(f"❌ 실패 {len(_fail_rows)}건 — 사유 보기", expanded=True):
                                # 사유별로 묶어 원인 파악이 쉽도록 표시
                                _by_reason = {}
                                for _r in _fail_rows:
                                    _by_reason.setdefault(str(_r.get('상태', '')), []).append(
                                        str(_r.get('상품', '')))
                                for _reason, _names in sorted(
                                        _by_reason.items(), key=lambda kv: -len(kv[1])):
                                    st.markdown(f"**{_reason}** — {len(_names)}건")
                                    st.caption(" · ".join(_names[:20])
                                               + (f" 외 {len(_names) - 20}건" if len(_names) > 20 else ""))
                        st.dataframe(pd.DataFrame(_ag_rows), use_container_width=True, hide_index=True)

    # ── 🔗 카페24 ↔ 코스트코 매칭 & 동기화 ──────────────────────────
    st.divider()
    st.subheader("🔗 카페24 ↔ 코스트코 매칭 & 동기화")
    st.caption("카페24 전 상품에 코스트코 번호 매칭(자체상품코드) → 매입가 동기화 → 품절/판매종료 반영. "
               "상시 자동 반영은 스케줄 태스크(--task cafe24sync)로 설정하세요.")

    # ── ⏱ 자동 동기화 스케줄 (품절·매입가 상시 반영) ──
    with st.expander("⏱ 자동 동기화 스케줄 — 품절/판매종료·매입가 상시 반영", expanded=False):
        _sc_en = st.checkbox("자동 동기화 활성화", key="sc_en",
                             value=(get_global_setting('cafe24sync_enabled') == '1'))
        _sc_iv = st.number_input("실행 간격(시간)", min_value=1, max_value=24, step=1,
                                 value=int(get_global_setting('cafe24sync_interval_hours') or 3), key="sc_iv")
        st.caption(f"마지막 실행: {get_global_setting('cafe24sync_last_run') or '없음'} · "
                   "서버 크론이 매시간 확인하여 설정한 간격마다 자동 실행합니다.")
        if st.button("💾 스케줄 저장", key="sc_save"):
            set_global_setting('cafe24sync_enabled', '1' if _sc_en else '0')
            set_global_setting('cafe24sync_interval_hours', str(int(_sc_iv)))
            st.success(f"저장됨 — {'활성' if _sc_en else '비활성'} · {int(_sc_iv)}시간 간격")

    _sy_cf = {_k: (get_global_setting('cafe24_' + _k) or '') for _k in
              ('mall_id', 'client_id', 'client_secret', 'access_token', 'refresh_token', 'token_expires_at')}
    if not (_sy_cf['mall_id'] and _sy_cf['client_id'] and _sy_cf['access_token']):
        st.info("공용 카페24 자격증명이 없습니다. 설정 탭에서 카페24를 먼저 연결하세요.")
    else:
        import cafe24_api as _c24
        import costco_crawler as _cc
        _sy_creds = {'mall_id': _sy_cf['mall_id'], 'client_id': _sy_cf['client_id'],
                     'client_secret': _sy_cf['client_secret'], 'access_token': _sy_cf['access_token'],
                     'refresh_token': _sy_cf['refresh_token'], 'expires_at': _sy_cf['token_expires_at']}

        def _sy_save(t):
            for _k, _v in (('cafe24_access_token', t.get('access_token', '')),
                           ('cafe24_refresh_token', t.get('refresh_token', '')),
                           ('cafe24_token_expires_at', t.get('expires_at', ''))):
                set_global_setting(_k, _v)

        if st.button("📥 카페24 전체 불러오기 + 자동매칭", key="sy_load"):
            with st.spinner("카페24 전체 상품 조회 중..."):
                _prods, _e = _c24.get_all_products(_sy_creds, save_tokens=_sy_save, max_total=3000)
            if _e:
                st.error(f"조회 실패: {_e}")
            else:
                _shared = get_shared_products()
                _by_no = {str(s['product_no'] or '').strip(): s
                          for s in _shared if str(s['product_no'] or '').strip()}
                with st.spinner(f"{len(_prods or [])}개 자동 매칭 중..."):
                    _rows = []
                    for _p in (_prods or []):
                        _cur = str(_p.get('custom_product_code') or '').strip()
                        if _cur and _cur in _by_no:
                            _s = _by_no[_cur]
                            _no, _nm, _pr, _scv = _cur, str(_s['costco_name']), int(_s['unit_price'] or 0), 999
                        else:
                            _best, _bs = None, 0
                            for _s in _shared:
                                _sc = calc_match_score(_p['product_name'], _s['costco_name'])
                                if _sc > _bs:
                                    _bs, _best = _sc, _s
                            if _best and _bs >= 2:
                                _no, _nm, _pr, _scv = (str(_best['product_no'] or ''),
                                                       str(_best['costco_name']),
                                                       int(_best['unit_price'] or 0), _bs)
                            else:
                                _no, _nm, _pr, _scv = '', '', 0, 0
                        _rows.append({
                            'cafe24번호': _p['product_no'],
                            '카페24상품명': str(_p['product_name'])[:40],
                            '현재코드': _cur,
                            '코스트코번호': (_cur or _no),
                            '코스트코명': _nm[:30],
                            '원가': _pr,
                            '카페24매입가': int(_p.get('supply_price') or 0),
                            '점수': _scv,
                        })
                st.session_state['_sy_rows'] = _rows
                st.success(f"{len(_rows)}개 불러옴 · 자동매칭 "
                           f"{sum(1 for r in _rows if r['코스트코번호'])}건")

        _sy_rows = st.session_state.get('_sy_rows') or []
        if _sy_rows:
            _flt = st.radio("보기", ["전체", "미매칭만", "매칭만"], horizontal=True, key="sy_flt")
            if _flt == "미매칭만":
                _view = [r for r in _sy_rows if not r['코스트코번호']]
            elif _flt == "매칭만":
                _view = [r for r in _sy_rows if r['코스트코번호']]
            else:
                _view = _sy_rows
            st.caption(f"{len(_view)}행 · '코스트코번호' 열 직접 수정 가능(점수=이름유사도, 999=코드일치). "
                       "표에 보이는 행만 저장·동기화됩니다.")
            _edited = st.data_editor(
                pd.DataFrame(_view), use_container_width=True, hide_index=True, height=420,
                key="sy_editor",
                disabled=['cafe24번호', '카페24상품명', '현재코드', '코스트코명', '원가', '카페24매입가', '점수'])

            _o1, _o2 = st.columns(2)
            _do_price = _o1.checkbox("매입가를 코스트코 현재가로 동기화", value=True, key="sy_doprice")
            _do_stock = _o2.checkbox("품절/판매종료면 판매중지 처리", value=True, key="sy_dostock")

            if st.button("💾 매칭 저장 + 동기화 실행", type="primary", key="sy_run"):
                _res, _prog = [], st.progress(0.0)
                _tot = max(1, len(_edited))
                for _i, _r in _edited.reset_index(drop=True).iterrows():
                    _prog.progress((_i + 1) / _tot)
                    _no = str(_r['코스트코번호'] or '').strip()
                    _c24no = _r['cafe24번호']
                    if not _no:
                        continue
                    _msg = []
                    if _no != str(_r['현재코드'] or '').strip():
                        _ok, _er = _c24.update_custom_product_code(_sy_creds, _c24no, _no, save_tokens=_sy_save)
                        _msg.append('코드저장' if _ok else f'코드실패({str(_er)[:20]})')
                    if _do_price or _do_stock:
                        _cs = _cc.fetch_costco_status(_no)
                        if _cs['exists'] is False:
                            if _do_stock:
                                _ok, _er = _c24.update_selling_status(_sy_creds, _c24no, selling=False, save_tokens=_sy_save)
                                _msg.append('판매종료→중지' if _ok else f'중지실패({str(_er)[:20]})')
                        elif _cs['exists'] is True:
                            if _do_stock and _cs['available'] is False:
                                _ok, _er = _c24.update_selling_status(_sy_creds, _c24no, selling=False, save_tokens=_sy_save)
                                _msg.append('품절→중지' if _ok else f'중지실패({str(_er)[:20]})')
                            if _do_price and int(_cs['price'] or 0) > 0:
                                _ok, _er = _c24.update_supply_price(_sy_creds, _c24no, _cs['price'], save_tokens=_sy_save)
                                _msg.append(f"매입가={fmt(_cs['price'])}" if _ok else f'매입가실패({str(_er)[:20]})')
                        else:
                            _msg.append('상태확인불가(건너뜀)')
                    _res.append({'카페24': str(_r['카페24상품명'])[:24], '코스트코': _no,
                                 '결과': ' · '.join(_msg) or '변경없음'})
                st.session_state.pop('_sy_rows', None)   # 다음 조회 시 최신값 반영
                st.success(f"동기화 완료 — {len(_res)}건 처리 (다시 불러오면 최신 반영)")
                if _res:
                    st.dataframe(pd.DataFrame(_res), use_container_width=True, hide_index=True)
