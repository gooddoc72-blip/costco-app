"""🛒 카페24 — 대행 등록 + 코스트코 매칭·동기화 (관리자 전용)."""
import re as _re
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


def _fetch_image_bytes(url, timeout=15):
    """이미지 URL → (bytes, media_type). 실패 시 (None, None)."""
    import requests as _rq
    try:
        r = _rq.get(url, timeout=timeout)
        if r.status_code != 200 or not r.content:
            return None, None
        ct = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ct not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
            _u = str(url).lower()
            ct = ('image/png' if '.png' in _u else
                  'image/webp' if '.webp' in _u else 'image/jpeg')
        return r.content, ct
    except Exception:
        return None, None


def _cafe24_detail_images(full):
    """카페24 상품 상세 HTML/이미지에서 상세페이지 이미지 URL 목록 추출(순서 유지·절대경로).
    설명(description) 안의 <img>들 + 대표 상세이미지."""
    urls = []
    desc = str((full or {}).get('description') or '')
    for m in _re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', desc, _re.I):
        urls.append(m.group(1))
    for k in ('detail_image', 'list_image'):
        u = (full or {}).get(k)
        if u:
            urls.append(u)
    seen, out = set(), []
    for u in urls:
        u = str(u).strip()
        if u.startswith('//'):
            u = 'https:' + u
        if u and u.startswith('http') and u not in seen:
            seen.add(u); out.append(u)
    return out


def _build_image_detail(full, tid, tsecret, limit=20):
    """카페24 상세이미지들을 네이버 CDN에 업로드 → <img> 스택 HTML 반환.
    업로드 성공분이 없으면 '' 반환(호출측에서 기존 HTML 폴백)."""
    _cdns = []
    for _iu in _cafe24_detail_images(full)[:limit]:
        _dc, _de = naver_api.upload_product_image(tid, tsecret, _iu)
        if _dc:
            _cdns.append(_dc)
    if not _cdns:
        return ''
    return ''.join(
        f'<img src="{_u}" style="display:block;max-width:100%;margin:0 auto">' for _u in _cdns)


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    st.title("🛒 카페24")
    if not IS_ADMIN:
        st.warning("관리자 전용 메뉴입니다.")
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
        import cafe24_api, ai_service
        _ag_creds = {'mall_id': _ag_cf['mall_id'], 'client_id': _ag_cf['client_id'],
                     'client_secret': _ag_cf['client_secret'], 'access_token': _ag_cf['access_token'],
                     'refresh_token': _ag_cf['refresh_token'], 'expires_at': _ag_cf['token_expires_at']}

        def _ag_save(t):
            for _k, _v in (('cafe24_access_token', t.get('access_token', '')),
                           ('cafe24_refresh_token', t.get('refresh_token', '')),
                           ('cafe24_token_expires_at', t.get('expires_at', ''))):
                set_global_setting(_k, _v)

        _ag_users = [u for u in get_all_users()
                     if (not u.get('is_admin')) and u.get('status', 'active') == 'active']
        if not _ag_users:
            st.info("등록 대상이 될 일반 사용자가 없습니다.")
        else:
            _agc1, _agc2 = st.columns([2, 1])
            _ag_pick = _agc1.selectbox(
                "🎯 등록 대상 사용자",
                [f"{u['username']} · {u.get('display_name', '')}" for u in _ag_users], key="ag_target")
            _ag_margin = _agc2.number_input("마진율 %", min_value=0, max_value=300, step=5,
                                            value=int(get_global_setting('cafe24_naver_margin') or 10),
                                            key="ag_margin")
            _ag_tuser = _ag_pick.split(' · ')[0].strip()
            _ag_ts = get_all_settings(_ag_tuser) or {}
            _ag_tid = _ag_ts.get('api_client_id', ''); _ag_tsecret = _ag_ts.get('api_client_secret', '')
            _ag_tas = _ag_ts.get('naver_as_tel') or '1588-1234'
            _ag_oc = settings.get('naver_open_client_id', ''); _ag_os = settings.get('naver_open_client_secret', '')
            _ag_ai = get_global_setting('anthropic_api_key') or settings.get('anthropic_api_key', '')
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
                _ag_img_detail = st.checkbox(
                    "🖼 상세페이지를 이미지로 등록 (HTML 대신 카페24 상세이미지를 네이버 CDN 업로드)",
                    value=True, key="ag_imgdetail")
                _ag_photo_ai = st.checkbox(
                    "📷 AI 제품사진 분석으로 상품명·속성 생성 (제품사진 등록 방식)",
                    value=bool(_ag_ai), key="ag_photoai", disabled=not _ag_ai,
                    help="대표 제품이미지를 Claude 비전으로 분석해 상품명·원산지·브랜드를 뽑고, "
                         "그 상품명으로 카테고리 판단·연관키워드 최적화까지 진행합니다.")
                _agq1, _agq2 = st.columns([3, 1])
                _ag_q = _agq1.text_input("카페24 상품명 검색(비우면 최근)", key="ag_q",
                                         label_visibility="collapsed", placeholder="카페24 상품명 일부")
                if _agq2.button("🔎 카페24 조회", key="ag_search", use_container_width=True):
                    set_global_setting('cafe24_naver_margin', str(int(_ag_margin)))
                    with st.spinner("카페24 상품 조회 중..."):
                        _prods, _perr = cafe24_api.search_products(_ag_creds, _ag_q, save_tokens=_ag_save)
                    st.session_state['_ag_prods'] = [] if _perr else (_prods or [])
                    if _perr:
                        st.error(f"조회 실패: {_perr}")
                _ag_list = st.session_state.get('_ag_prods') or []
                if _ag_list:
                    _ag_show = _ag_list[:30]
                    st.caption(f"{len(_ag_list)}개 조회 — 체크 후 아래 버튼으로 '{_ag_tuser}' 스토어에 등록")
                    # 전체 선택 토글 — 변화 감지 시 개별 체크박스 일괄 설정(위젯 생성 전에 세팅)
                    _ag_all = st.checkbox(f"✅ 전체 선택 ({len(_ag_show)}개)", key="ag_all_toggle")
                    if _ag_all != st.session_state.get('_ag_all_prev'):
                        for _p in _ag_show:
                            st.session_state[f"ag_ck_{_p['product_no']}"] = _ag_all
                        st.session_state['_ag_all_prev'] = _ag_all
                    _ag_sel = []
                    for _p in _ag_show:
                        _pno = _p['product_no']; _pr = int(_p.get('price') or 0)
                        _npr = int(round(_pr * (1 + _ag_margin / 100.0) / 0.945 / 10) * 10)
                        if st.checkbox(
                                f"{str(_p.get('product_name', ''))[:42]} · 카페24 {fmt(_pr)}원 → 네이버 {fmt(_npr)}원",
                                key=f"ag_ck_{_pno}"):
                            _ag_sel.append((_p, _npr))
                    if st.button(f"🚀 선택 {len(_ag_sel)}개 → '{_ag_tuser}' 스토어 등록", type="primary",
                                 key="ag_reg", disabled=not (_ag_sel and _ag_oc and _ag_os)):
                        _ag_rows = []; _agprog = st.progress(0.0)
                        for _i, (_p, _npr) in enumerate(_ag_sel):
                            _agprog.progress((_i + 1) / len(_ag_sel))
                            _name = str(_p.get('product_name', ''))
                            # 0) 카페24 상세 조회 + 대표 이미지
                            _full, _fe = cafe24_api.get_product(_ag_creds, _p['product_no'], save_tokens=_ag_save)
                            _full = _full or {}
                            _cf_name = _full.get('product_name') or _name
                            _rep = _full.get('detail_image') or _full.get('list_image') or ''
                            if not _rep:
                                _ag_rows.append({'상품': _name[:24], '상태': '❌ 이미지 없음'}); continue
                            _cdn, _ue = naver_api.upload_product_image(_ag_tid, _ag_tsecret, _rep)
                            if not _cdn:
                                _ag_rows.append({'상품': _name[:24], '상태': '❌ 이미지업로드 실패'}); continue
                            # ① AI 제품사진 분석(비전) → 상품명·원산지·브랜드
                            _ai_photo = {}
                            if _ag_photo_ai and _ag_ai:
                                _imgb, _mt = _fetch_image_bytes(_rep)
                                if _imgb:
                                    _apr, _ape = ai_service.analyze_product_photo(_ag_ai, _imgb, _mt)
                                    if _apr:
                                        _ai_photo = _apr
                            _base_name = str(_ai_photo.get('name') or '').strip() or _cf_name
                            # ② 카테고리 자동판단 — 분석 상품명 기준(폴백: 카페24명, 그다음 AI category 키워드)
                            _items, _ = naver_api.naver_shopping_search(_ag_oc, _ag_os, _base_name)
                            _paths = [">".join([x for x in (it.get('category1'), it.get('category2'),
                                                            it.get('category3'), it.get('category4')) if x])
                                      for it in (_items or [])]
                            _paths = [p for p in _paths if p]
                            _cid = None; _cfull = ''
                            if _paths:
                                _ch, _ = ai_service.suggest_naver_category(_ag_ai, _base_name, _paths)
                                _chosen = _ch or _paths[0]
                                _cr, _ = naver_api.search_naver_categories(
                                    _ag_tid, _ag_tsecret, str(_chosen).split('>')[-1].strip())
                                if _cr:
                                    _pt = set(str(_chosen).replace('>', ' ').split())
                                    _best, _bs = None, -1
                                    for _c in _cr:
                                        _ct = set(str(_c.get('full_name', '')).replace('>', ' ').split())
                                        _sc = len(_pt & _ct)
                                        if _sc > _bs:
                                            _bs, _best = _sc, _c
                                    if _best:
                                        _cid, _cfull = _best.get('id'), _best.get('full_name')
                            if not _cid and _ai_photo.get('category'):
                                _cr2, _ = naver_api.search_naver_categories(
                                    _ag_tid, _ag_tsecret, str(_ai_photo['category']).strip())
                                if _cr2:
                                    _cid, _cfull = _cr2[0].get('id'), _cr2[0].get('full_name')
                            if not _cid:
                                _ag_rows.append({'상품': _base_name[:24], '상태': '❌ 카테고리 판단실패'}); continue
                            # ③ 최종 상품명: 연관키워드(저경쟁 100~300+대표어) 최적화 — 분석 상품명을 seed로
                            _final_name = _base_name
                            if _ad_creds:
                                _kn, _ki = naver_api.keyword_optimized_name(
                                    _ad_key, _ad_sec, _ad_cust, _base_name, ai_key=_ag_ai, category=_cfull)
                                if _kn and len(_kn) >= 4:
                                    _final_name = _kn
                            # 안전장치: 상품명 절대 비우지 않음
                            if not str(_final_name or '').strip():
                                _final_name = _cf_name or _name or _base_name
                            # ④ 태그ID(검색 반영되는 사전등록 태그만)
                            _desc_txt = str(_full.get('description') or '')
                            _tags, _ = naver_api.build_seller_tags(
                                _ag_tid, _ag_tsecret, _ag_ai, _final_name, _cfull, _desc_txt, _ad_creds)
                            # ⑤ 속성: AI 분석값 우선, 없으면 카페24 값
                            _manuf = (str(_ai_photo.get('brand') or '').strip()
                                      or str(_full.get('manufacturer_name') or _full.get('brand_name') or '').strip())
                            _model = str(_full.get('model_name') or '').strip()
                            _origin = (str(_ai_photo.get('origin') or '').strip()
                                       or str(_full.get('origin_place_value') or '').strip())
                            # ⑥ 상세페이지: 상단에 상품명 + (이미지 등록 옵션) 카페24 상세이미지 스택
                            import html as _html
                            _name_block = (
                                '<div style="text-align:center;font-size:22px;font-weight:800;'
                                'padding:18px 12px;color:#222;line-height:1.45">'
                                + _html.escape(str(_final_name)) + '</div>')
                            _body_html = _desc_txt or ''
                            if _ag_img_detail:
                                _img_html = _build_image_detail(_full, _ag_tid, _ag_tsecret)
                                if _img_html:
                                    _body_html = _img_html
                            _detail_html = _name_block + (_body_html or f"<p>{_html.escape(str(_final_name))}</p>")
                            _res, _re2 = naver_api.register_product(_ag_tid, _ag_tsecret, {
                                "name": _final_name, "sale_price": _npr,
                                "image_url": _cdn, "category_id": _cid,
                                "detail_html": _detail_html,
                                "shipping_fee": 0, "origin_code": "03", "after_service_tel": _ag_tas,
                                "seller_tags": _tags, "manufacturer": _manuf or None,
                                "model_name": _model or None, "origin_content": _origin or None,
                                "seller_code": str(_p['product_no']),
                            })
                            _ag_rows.append({'상품': _final_name[:24], '카테고리': str(_cfull or '')[:20],
                                             '태그': len(_tags or []), '판매가': _npr,
                                             '상태': '✅ 등록' if not _re2 else f'❌ {str(_re2)[:24]}'})
                        _ok = sum(1 for r in _ag_rows if str(r.get('상태', '')).startswith('✅'))
                        st.success(f"🛒 '{_ag_tuser}' 스토어 대행 등록 — 성공 {_ok} / 전체 {len(_ag_rows)}건")
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
