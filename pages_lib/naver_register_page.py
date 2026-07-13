"""🛍 네이버 등록 페이지 — pages_lib 자동 추출."""
import os
import io
import sys
import json
import subprocess
import sqlite3
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
try:
    import plotly.express as px
except ImportError:
    px = None

from db import (
    init_auth_db, hash_pw, check_login, get_global_setting, set_global_setting,
    register_user, get_pending_users, approve_user, reject_user, get_all_users,
    add_user, delete_user, change_password, get_user_info,
    create_session, get_session_user, delete_session,
    get_shared_products, upsert_shared_product, delete_shared_product, upsert_shared_store_price,
    get_user_db, init_user_db, get_setting, set_setting, get_all_settings, get_all_products,
    upsert_user_private, get_all_products_merged, upsert_product,
    get_product_detail,
    save_daily_orders, get_daily_orders, save_order_history, search_order_history,
    save_receipt_items, get_recent_receipt_items, delete_receipt_items_by_date, get_receipt_dates,
    get_date_range_stats, get_monthly_stats, get_product_ranking, get_saved_dates,
    get_dashboard_kpi, get_daily_profit_trend, get_week_best_products,
    get_price_history_monthly, save_price_changes_to_history, get_price_change_history,
    add_keyword_tracking, get_keyword_trackings, delete_keyword_tracking,
    save_rank_result, get_rank_history, get_latest_ranks,
    get_daily_ranks_in_month, get_yearly_rank_history, delete_trackings_bulk,
    get_rank_drops,
    AUTH_DB,
)
from services import (
    match_product_to_db, match_shared_product,
    update_product_info_from_orders, update_product_shipping_fees, update_product_sale_price,
    detect_price_changes, build_price_alert_msg,
    parse_costco_receipt_pdf, match_receipt_to_orders,
    match_receipt_to_naver_products, apply_receipt_pno_updates,
    decrypt_excel, read_excel_auto,
    _token_score,
)
from utils import (
    fmt, to_id_str, extract_pack_qty, clean_name, has_meaningful_char,
    get_ngrams, calc_match_score, MIN_MATCH_SCORE, get_week_range, get_month_range,
)
from ui_theme import (
    COLORS, CHART_COLORS, hero_section, section_header,
    kpi_card, chart_card_open, chart_card_close, quick_action_buttons,
)

try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False
    naver_api = None

# app.py 라우터에서 주입되는 cached wrapper들
cached_shared_products = None
cached_user_products = None
cached_merged = None
invalidate_data_cache = None


def _set_cache_helpers(shared_fn, user_fn, merged_fn, invalidate_fn, **kwargs):
    global cached_shared_products, cached_user_products, cached_merged, invalidate_data_cache
    cached_shared_products = shared_fn
    cached_user_products = user_fn
    cached_merged = merged_fn
    invalidate_data_cache = invalidate_fn


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    """🛍 네이버 등록 탭 렌더링."""
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    st.header("🛍 네이버 스마트스토어 상품 등록")

    if not HAS_NAVER_API:
        st.error("naver_api.py 없음 — 관리자에게 문의하세요.")
        st.stop()
    if not api_id or not api_secret:
        st.warning("⚙️ 설정 탭에서 네이버 API 키를 먼저 입력하세요.")
        st.stop()

    import json as _nr_json, re as _nr_re
    _nr_all   = cached_merged(USERNAME)
    _nr_unreg = [p for p in _nr_all if not p.get("naver_product_no")]
    _nr_reg   = [p for p in _nr_all if p.get("naver_product_no")]

    # ── 통계 ────────────────────────────────────────────────────────
    _st1, _st2, _st3 = st.columns(3)
    _st1.metric("전체",    f"{len(_nr_all)}개")
    _st2.metric("등록완료", f"{len(_nr_reg)}개")
    _st3.metric("미등록",   f"{len(_nr_unreg)}개")
    st.divider()

    # ── 카테고리 경로(A>B>C>D) → 네이버 리프카테고리ID 공용 변환 ──
    def _nr_resolve_leaf(path):
        _leaf = str(path).split(">")[-1].strip()
        if not _leaf:
            return None, None
        _cr, _ = naver_api.search_naver_categories(api_id, api_secret, _leaf)
        if not _cr:
            return None, None
        _pt = set(str(path).replace(">", " ").replace("/", " ").split())
        _best, _bs = None, -1
        for _c in _cr:
            _ct = set(str(_c.get('full_name', '')).replace(">", " ").replace("/", " ").split())
            _s = len(_pt & _ct)
            if _s > _bs:
                _bs, _best = _s, _c
        return (_best.get('id'), _best.get('full_name')) if _best else (None, None)

    # ── 📷 제품사진 + 가격사진으로 신상품 등록 (건별) ──
    #   AI 키: 본인 설정 > 관리자 공용키(전역) 순. 공용키가 있으면 모든 사용자에게 이 메뉴가 열림.
    _ph_aikey = _gs('anthropic_api_key') or get_global_setting('anthropic_api_key')
    _ph_oc = _gs('naver_open_client_id'); _ph_os = _gs('naver_open_client_secret')

    def _ph_mt(_f):
        _m = getattr(_f, 'type', None) or ''
        if str(_m).startswith('image/'):
            return _m
        _e = _f.name.rsplit('.', 1)[-1].lower()
        return {'png': 'image/png', 'webp': 'image/webp', 'gif': 'image/gif'}.get(_e, 'image/jpeg')

    def _ph_upload_cdn(_f):
        """업로드 파일 → 네이버 CDN URL."""
        if not _f:
            return None
        import tempfile, os as _oscd
        _ex = {'image/png': '.png', 'image/webp': '.webp'}.get(_ph_mt(_f), '.jpg')
        _fd, _tp = tempfile.mkstemp(suffix=_ex); _oscd.close(_fd)
        with open(_tp, 'wb') as _w:
            _w.write(_f.getvalue())
        _u, _ = naver_api.upload_product_image(api_id, api_secret, _tp)
        try: _oscd.remove(_tp)
        except Exception: pass
        return _u

    # ── 공통 상세 이미지(상단·하단) + 상세HTML 빌더 ──
    _top_img = _gs('naver_detail_top_img'); _bottom_img = _gs('naver_detail_bottom_img')

    def _food_info_html(d):
        """식품 표시사항 dict → 상세페이지용 '제품 상세정보' 표 HTML. 빈 값 행은 생략."""
        if not d:
            return ""
        import html as _hm
        _rows = [
            ("식품유형", d.get("food_type")), ("내용량", d.get("volume")),
            ("원재료명", d.get("ingredients")), ("보관방법", d.get("storage")),
            ("원산지", d.get("origin")), ("제조사", d.get("manufacturer")),
            ("수입원", d.get("importer")), ("소비기한", d.get("expiration")),
            ("열량", d.get("calories")), ("영양성분", d.get("nutrition")),
        ]
        _tr = []
        for _lbl, _val in _rows:
            _v = str(_val or "").strip()
            if not _v:
                continue
            _tr.append(
                '<tr>'
                '<th style="background:#f5f5f5;border:1px solid #ddd;padding:10px 12px;'
                'text-align:center;width:28%;font-weight:700;color:#333;white-space:nowrap">'
                f'{_hm.escape(_lbl)}</th>'
                '<td style="border:1px solid #ddd;padding:10px 12px;text-align:left;'
                f'color:#333;line-height:1.6">{_hm.escape(_v)}</td></tr>')
        if not _tr:
            return ""
        return ('<div style="max-width:720px;margin:24px auto 8px;padding:0 12px">'
                '<div style="font-size:20px;font-weight:800;text-align:center;'
                'padding:12px 0;color:#222">제품 상세정보</div>'
                '<table style="width:100%;border-collapse:collapse;font-size:15px">'
                + ''.join(_tr) + '</table></div>')

    def _build_detail(name, imgs, desc="", food_html=""):
        """상세HTML: [공통상단] + 상품명 + [제품설명] + 제품이미지들 + [제품정보표] + [공통하단]."""
        _p = []
        if _top_img:
            _p.append(f'<img src="{_top_img}" style="max-width:100%;display:block;margin:0 auto">')
        _p.append(f'<div style="font-size:32px;font-weight:800;text-align:center;'
                  f'padding:20px 12px;line-height:1.35">{name}</div>')
        if desc and str(desc).strip():
            import html as _htmlmod
            _safe = _htmlmod.escape(str(desc).strip()).replace('\n', '<br>')
            _p.append(f'<div style="font-size:17px;line-height:1.75;text-align:center;'
                      f'padding:4px 16px 22px;color:#333">{_safe}</div>')
        for _u in imgs:
            _p.append(f'<img src="{_u}" style="max-width:100%;display:block;'
                      f'margin:0 auto 20px;border:1px solid #cccccc">')
        if food_html:
            _p.append(food_html)
        if _bottom_img:
            _p.append(f'<img src="{_bottom_img}" style="max-width:100%;display:block;margin:0 auto">')
        return '<div style="text-align:center">' + ''.join(_p) + '</div>'

    def _wrap_common(inner):
        """기존 상세HTML(inner) 위·아래에 공통 상단/하단 이미지만 감쌈 (카페24 상세 보존용)."""
        _p = []
        if _top_img:
            _p.append(f'<img src="{_top_img}" style="max-width:100%;display:block;margin:0 auto">')
        _p.append(inner or '')
        if _bottom_img:
            _p.append(f'<img src="{_bottom_img}" style="max-width:100%;display:block;margin:0 auto">')
        return ''.join(_p)

    def _cafe24_imgs_to_cdn(html, max_n=15):
        """카페24 상세HTML에서 <img> URL 추출 → 네이버 CDN 업로드 → CDN URL 리스트.
        (c) 이미지형: 편집 가능한 이미지 블록으로 재구성용."""
        import re as _reimg
        _srcs = _reimg.findall(r'''<img[^>]+src=["']([^"']+)["']''', html or '', _reimg.I)
        _out = []
        for _s in _srcs[:max_n]:
            _s = _s.strip()
            if not _s.startswith('http'):
                continue
            _u, _ = naver_api.upload_product_image(api_id, api_secret, _s)
            if _u:
                _out.append(_u)
        return _out

    def _cafe24_detail(mode, name, rep_cdn, description):
        """카페24 상세 생성. mode='a': 카페24 상세 그대로+공통 / mode='c': 이미지형(편집 쉬움)."""
        if mode == 'c':
            _imgs = _cafe24_imgs_to_cdn(description)
            return _build_detail(name, _imgs or [rep_cdn])
        return _wrap_common(description or f"<p>{name}</p>")

    with st.expander("🖼 공통 상세 이미지 설정 (모든 상품 상단·하단에 자동 삽입)", expanded=False):
        st.caption("상단 이미지 = 상품명 위 / 하단 이미지 = 제품사진 다음. 한 번 저장하면 이후 등록되는 모든 상품 상세에 공통 삽입됩니다.")
        _dc1, _dc2 = st.columns(2)
        if _top_img:
            _dc1.image(_top_img, caption="현재 상단 공통", width=160)
        if _bottom_img:
            _dc2.image(_bottom_img, caption="현재 하단 공통", width=160)
        _up_top = _dc1.file_uploader("상단 공통 이미지 (교체)", accept_multiple_files=False, key="detail_top_up")
        _up_bot = _dc2.file_uploader("하단 공통 이미지 (교체)", accept_multiple_files=False, key="detail_bot_up")
        _sd1, _sd2 = st.columns(2)
        if _sd1.button("💾 공통 이미지 저장", key="save_detail_imgs", type="primary", use_container_width=True):
            _msgs = []
            with st.spinner("네이버 CDN 업로드 중..."):
                if _up_top:
                    _u = _ph_upload_cdn(_up_top)
                    if _u:
                        set_setting(USERNAME, 'naver_detail_top_img', _u); _msgs.append('상단')
                if _up_bot:
                    _u = _ph_upload_cdn(_up_bot)
                    if _u:
                        set_setting(USERNAME, 'naver_detail_bottom_img', _u); _msgs.append('하단')
            if _msgs:
                st.success(f"✅ 공통 {'·'.join(_msgs)} 이미지 저장 완료"); st.rerun()
            else:
                st.warning("업로드할 이미지를 선택하세요.")
        if _sd2.button("🗑 공통 이미지 해제", key="clear_detail_imgs", use_container_width=True):
            set_setting(USERNAME, 'naver_detail_top_img', '')
            set_setting(USERNAME, 'naver_detail_bottom_img', '')
            st.success("공통 이미지 해제됨"); st.rerun()

    if _ph_aikey:
        with st.expander("📷 제품사진(여러 장) + 가격사진으로 신상품 등록 (건별)", expanded=False):
            if not (_ph_oc and _ph_os):
                st.info("카테고리 자동판단에 **네이버 Open API(쇼핑검색)** 키가 필요합니다. (설정 탭)")
            _ph_margin = st.number_input("마진율 %", min_value=0, max_value=300, step=5,
                                         value=int(_gs('cafe24_naver_margin') or 10), key="ph_margin",
                                         help="네이버 판매가 = 코스트코가 ×(1+마진%) ÷0.945 (수수료 5.5% 감안)")
            _uc1, _uc2 = st.columns(2)
            _prod_imgs = _uc1.file_uploader("① 제품 사진들 (여러 장 — 첫 장이 대표이미지)",
                                            accept_multiple_files=True, key="ph_prod")
            _price_img = _uc2.file_uploader("② 가격 사진 (코스트코 가격표)", accept_multiple_files=False, key="ph_price")
            _label_img = st.file_uploader("③ 표시사항(라벨) 사진 — 식품 상세정보(내용량·원재료·보관·영양성분) 자동입력용 (선택)",
                                          accept_multiple_files=False, key="ph_label")
            if _prod_imgs:
                _uc1.image([im for im in _prod_imgs[:9]], width=90)
                if _uc1.checkbox("🔲 1000×1000 변환 미리보기 (네이버에 올라가는 실제 형태)", key="ph_sq_prev"):
                    _sq_imgs = []
                    for _im in _prod_imgs[:9]:
                        _b = naver_api.resize_square_bytes(_im.getvalue())
                        if _b:
                            _sq_imgs.append(_b)
                    if _sq_imgs:
                        _uc1.image(_sq_imgs, width=90)
                        _uc1.caption("↑ 가운데 기준 정사각 크롭된 모습 = 네이버 등록 대표/추가 이미지")
            if _price_img:
                _uc2.image(_price_img, width=150)
            st.caption("제품사진 = 리스팅 이미지(여러 장: 대표+추가) + 상품명·카테고리 / "
                       "가격사진 = 코스트코가 판독 → 마진 붙여 판매가 산정.")

            if st.button("🔎 사진 분석 (미리보기)", type="primary", key="ph_analyze",
                         disabled=not (_prod_imgs and _price_img), use_container_width=True):
                import ai_service
                set_setting(USERNAME, 'cafe24_naver_margin', str(int(_ph_margin)))
                with st.spinner("제품사진·가격사진 분석 중..."):
                    _i1, _e1 = ai_service.analyze_product_photo(_ph_aikey, _prod_imgs[0].getvalue(), _ph_mt(_prod_imgs[0]))
                    _i2, _e2 = ai_service.analyze_price_tag(_ph_aikey, _price_img.getvalue(), _ph_mt(_price_img))
                if _e1 or not _i1:
                    st.error(f"제품사진 분석 실패: {_e1}")
                elif _e2 or not _i2:
                    st.error(f"가격사진 분석 실패: {_e2}")
                else:
                    _nm = _i1.get('name') or _i2.get('product_name', '')
                    _cost = int(_i2.get('price') or 0)
                    _sale = int(round(_cost * (1 + _ph_margin / 100.0) / 0.945 / 10) * 10) if _cost > 0 else 0
                    _cid = None; _cfull = ''
                    if _ph_oc and _ph_os and _nm:
                        _its, _ = naver_api.naver_shopping_search(_ph_oc, _ph_os, _nm)
                        _pth = [">".join([x for x in (it.get('category1'), it.get('category2'),
                                                      it.get('category3'), it.get('category4')) if x])
                                for it in (_its or [])]
                        _pth = [p for p in _pth if p]
                        if _pth:
                            _ch, _ = ai_service.suggest_naver_category(_ph_aikey, _nm, _pth)
                            _cid, _cfull = _nr_resolve_leaf(_ch or _pth[0])
                    if not _cid and _i1.get('category'):
                        _cr2, _ = naver_api.search_naver_categories(api_id, api_secret, _i1['category'])
                        if _cr2:
                            _cid, _cfull = _cr2[0]['id'], _cr2[0]['full_name']
                    # 새 상품 분석 시 이전 제품의 편집값·태그 초기화 (위젯 생성 전이라 안전)
                    for _rk in ('ph_en', 'ph_es', 'ph_ec', 'ph_costco_no', 'ph_desc',
                                'ph_sq_prev', '_ph_tags', '_ph_tags_info', 'ph_tag_editor', '_ph_food'):
                        st.session_state.pop(_rk, None)
                    st.session_state['_ph_pv'] = {
                        'name': _nm, 'cost': _cost, 'sale': _sale,
                        'cat_id': str(_cid or ''), 'cat_full': _cfull or '',
                        'origin': _i1.get('origin', '국산'), 'brand': _i1.get('brand', ''),
                        'costco_no': str((_i2 or {}).get('product_no', '') or ''),
                    }

            _pv = st.session_state.get('_ph_pv')
            if _pv:
                st.markdown(f"**미리보기** — 코스트코가 {fmt(_pv['cost'])}원 → 네이버 판매가 **{fmt(_pv['sale'])}원** "
                            f"(마진 {_ph_margin}%)")
                _en = st.text_input("상품명", value=_pv['name'], key="ph_en")
                _dgc1, _dgc2 = st.columns([1, 3])
                if _dgc1.button("🤖 AI 상세설명 생성", key="ph_desc_gen",
                                use_container_width=True, disabled=not _prod_imgs):
                    import ai_service
                    with st.spinner("상품 사진 분석 → 상세설명 작성 중..."):
                        _dtxt, _derr = ai_service.generate_product_description(
                            _ph_aikey, _prod_imgs[0].getvalue(), _ph_mt(_prod_imgs[0]),
                            _en.strip(), _pv.get('cat_full', ''))
                    if _dtxt:
                        st.session_state['ph_desc'] = _dtxt.strip()
                        st.rerun()
                    else:
                        st.warning(f"상세설명 생성 실패: {_derr}")
                _dgc2.caption("사진·상품명 기반 상세설명 자동 작성 → 아래에서 자유롭게 수정")
                _desc = st.text_area(
                    "제품 설명 (상세페이지 상품명 하단에 표시)", key="ph_desc", height=120,
                    placeholder="예: 코스트코 프리미엄 커피 원액 260mL x 3개입 / 시그니처 캐러멜향 … "
                                "(또는 위 🤖 버튼으로 자동 생성)")

                # ── 🍱 식품 표시사항(라벨) 자동입력 → 상세페이지 제품정보 표 ──
                _fgc1, _fgc2 = st.columns([1, 3])
                if _fgc1.button("🍱 라벨 분석 → 제품정보", key="ph_food_gen",
                                use_container_width=True, disabled=_label_img is None):
                    import ai_service
                    with st.spinner("표시사항(라벨) 분석 중 — 내용량·원재료·보관·영양성분..."):
                        _fdd, _fderr = ai_service.analyze_food_label(
                            _ph_aikey, _label_img.getvalue(), _ph_mt(_label_img))
                    if _fdd:
                        st.session_state['_ph_food'] = _fdd
                        st.rerun()
                    else:
                        st.warning(f"라벨 분석 실패: {_fderr}")
                _fgc2.caption("③ 라벨 사진을 올리고 누르면 제품정보가 상세페이지에 표로 삽입됩니다.")
                _food = st.session_state.get('_ph_food')
                if _food and any(_food.values()):
                    with st.expander("🍱 추출된 제품 정보 (상세페이지 표로 삽입)", expanded=True):
                        _flabels = [('food_type', '식품유형'), ('volume', '내용량'),
                                    ('ingredients', '원재료명'), ('storage', '보관방법'),
                                    ('origin', '원산지'), ('manufacturer', '제조사'),
                                    ('importer', '수입원'), ('expiration', '소비기한'),
                                    ('calories', '열량'), ('nutrition', '영양성분')]
                        for _fk, _flbl in _flabels:
                            if _food.get(_fk):
                                st.markdown(f"- **{_flbl}**: {_food[_fk]}")
                        if st.button("🗑 제품정보 제거", key="ph_food_clear"):
                            st.session_state.pop('_ph_food', None)
                            st.rerun()

                _ecols = st.columns(2)
                _es = _ecols[0].number_input("네이버 판매가", value=int(_pv['sale']), min_value=0, step=100, key="ph_es")
                _ec = _ecols[1].text_input("카테고리ID (자동판단)", value=_pv['cat_id'],
                                           key="ph_ec", help=f"{_pv['cat_full'] or '카테고리 자동판단 실패 — 직접 입력'}")
                if _pv['cat_full']:
                    st.caption(f"📂 {_pv['cat_full']}")

                # ── 코스트코 상품번호 = 판매자 자체코드(sellerManagementCode) — 필수 ──
                _costco_no = st.text_input(
                    "코스트코 상품번호 (판매자 자체코드) *", value=_pv.get('costco_no', ''),
                    key="ph_costco_no",
                    help="네이버 sellerManagementCode로 등록됩니다. 가격사진에서 자동 판독된 값 — 틀리면 수정하세요.")
                if not _costco_no.strip():
                    st.warning("⚠️ 코스트코 상품번호가 비어 있습니다 — 입력해야 등록됩니다.")

                # ── 🏷 AI 연관태그 (생성 → 검토 → 등록) ─────────────────
                st.markdown("**🏷 연관태그 (검색 노출용 · 최대 10개)**")
                _adc = (_gs('naver_ad_api_key'), _gs('naver_ad_secret'), _gs('naver_ad_customer_id'))
                _tgc1, _tgc2 = st.columns([1, 2])
                if _tgc1.button("🤖 AI 태그 10개 생성", key="ph_tag_gen", use_container_width=True):
                    with st.spinner("AI 후보 → 태그사전 검증 → 제한태그 제거..."):
                        _tags, _tinfo = naver_api.build_seller_tags(
                            api_id, api_secret, _ph_aikey,
                            _en.strip(), _pv.get('cat_full', ''), _en.strip(),
                            ad_creds=_adc if all(_adc) else None,
                        )
                    st.session_state['_ph_tags'] = _tags
                    st.session_state['_ph_tags_info'] = _tinfo
                    if not _tags:
                        st.warning(f"검증된 사전 태그 없음 (후보 {_tinfo.get('candidates',0)}개). "
                                   + (f"오류: {_tinfo['err']}" if _tinfo.get('err')
                                      else "상품명을 더 일반적인 키워드로 바꿔 재시도해보세요."))
                if not all(_adc):
                    _tgc2.caption("💡 설정 탭에 검색광고 API 키 넣으면 **검색량순** 정렬. 지금은 관련도순.")

                _sel_tags = []
                _cur_tags = st.session_state.get('_ph_tags') or []
                if _cur_tags:
                    _volmap = (st.session_state.get('_ph_tags_info', {}) or {}).get('volumes', {}) or {}
                    _tdf = pd.DataFrame([
                        {"사용": True, "태그": t["text"],
                         "월검색량": int(_volmap.get(t["text"], 0)), "태그ID": t.get("code")}
                        for t in _cur_tags
                    ])
                    _ed = st.data_editor(
                        _tdf, key="ph_tag_editor", hide_index=True, use_container_width=True,
                        num_rows="dynamic",
                        column_config={
                            "사용": st.column_config.CheckboxColumn("사용", default=True),
                            "태그": st.column_config.TextColumn("태그", required=True),
                            "월검색량": st.column_config.NumberColumn("월검색량", disabled=True),
                            "태그ID": st.column_config.NumberColumn(
                                "태그ID", disabled=True,
                                help="사전 등록 태그 ID(숫자 有 = 검색 반영). 빈 값 = 직접입력 태그."),
                        },
                    )
                    st.caption("체크 해제=제외 · 행 추가=직접입력 태그(ID 없음) · 등록 시 체크된 것만 반영.")
                    try:
                        for _r in _ed.to_dict("records"):
                            _txt = str(_r.get("태그") or "").strip()
                            if _r.get("사용") and _txt:
                                _e = {"text": _txt}
                                _cd = _r.get("태그ID")
                                if _cd not in (None, "", 0) and not pd.isna(_cd):
                                    _e["code"] = int(_cd)
                                _sel_tags.append(_e)
                    except Exception:
                        _sel_tags = [{"code": t.get("code"), "text": t["text"]} for t in _cur_tags]

                if st.button("🛍 네이버 등록", type="primary", key="ph_reg1",
                             disabled=not (_prod_imgs and _en.strip() and _ec.strip()
                                           and _es > 0 and _costco_no.strip())):
                    import tempfile, os as _os3
                    with st.spinner(f"제품사진 {len(_prod_imgs)}장 업로드 → 네이버 등록 중..."):
                        _cdns = []
                        for _im in _prod_imgs[:10]:   # 대표+추가 최대 10장
                            _ex = {'image/png': '.png', 'image/webp': '.webp'}.get(_ph_mt(_im), '.jpg')
                            _fd, _tp = tempfile.mkstemp(suffix=_ex); _os3.close(_fd)
                            with open(_tp, 'wb') as _w:
                                _w.write(_im.getvalue())
                            _cu, _ue = naver_api.upload_product_image(api_id, api_secret, _tp)
                            try: _os3.remove(_tp)
                            except Exception: pass
                            if _cu:
                                _cdns.append(_cu)
                        if not _cdns:
                            st.error("이미지 업로드 실패 — 대표이미지를 못 올렸습니다.")
                        else:
                            _res, _re2 = naver_api.register_product(api_id, api_secret, {
                                "name": _en.strip(), "sale_price": int(_es),
                                "image_url": _cdns[0], "extra_image_urls": _cdns[1:],
                                "category_id": _ec.strip(),
                                "seller_code": _costco_no.strip(),
                                "seller_tags": _sel_tags,
                                "food_notice": _food,
                                "detail_html": _build_detail(_en.strip(), _cdns, _desc,
                                                             _food_info_html(_food)),
                                "shipping_fee": 0, "origin_code": "03",
                                "after_service_tel": _gs("naver_as_tel") or "1588-1234",
                                "manufacturer": _pv.get('brand') or "상품 상세페이지 참조",
                            })
                            if _res and _res.get('origin_product_no'):
                                st.success(f"✅ 네이버 등록 완료! (origin #{_res.get('origin_product_no')}) "
                                           f"— {_en.strip()[:20]} / {fmt(int(_es))}원 / 이미지 {len(_cdns)}장"
                                           + (f" / 태그 {len(_sel_tags)}개" if _sel_tags else ""))
                                if _re2:   # 태그만 거부되고 등록은 성공한 경우 경고
                                    st.warning(_re2)
                                st.image(_cdns[0], width=220,
                                         caption="네이버에 등록된 대표이미지 (1000×1000 정사각)")
                                st.session_state.pop('_ph_pv', None)
                                st.session_state.pop('_ph_tags', None)
                                st.session_state.pop('_ph_tags_info', None)
                                st.session_state.pop('_ph_food', None)
                            else:
                                st.error(f"❌ 등록 실패: {_re2}")
        st.divider()

    # ── 📷 여러 개 한번에 등록 (일괄) — 제품사진들 + 가격사진들 1:1 순서매칭 ──
    if _ph_aikey:
        with st.expander("📷 여러 개 한번에 등록 (일괄) — 제품사진들 + 가격사진들", expanded=False):
            if not (_ph_oc and _ph_os):
                st.info("카테고리 자동판단에 **네이버 Open API(쇼핑검색)** 키가 필요합니다. (설정 탭)")
            _bm = st.number_input("마진율 %", min_value=0, max_value=300, step=5,
                                  value=int(_gs('cafe24_naver_margin') or 10), key="ph_bmargin",
                                  help="네이버 판매가 = 코스트코가 ×(1+마진%) ÷0.945")
            _bc1, _bc2 = st.columns(2)
            _bprod = _bc1.file_uploader("① 제품 사진들 (여러 장)", accept_multiple_files=True, key="ph_bprod")
            _bprice = _bc2.file_uploader("② 가격 사진들 (여러 장 · 같은 순서)", accept_multiple_files=True, key="ph_bprice")
            st.caption("제품사진·가격사진을 **같은 개수·같은 순서(파일명 순)** 로 올리세요. 순서대로 1:1 짝지어 일괄 등록합니다.")
            _np = len(_bprod or []); _npr = len(_bprice or [])
            if _bprod or _bprice:
                st.caption(f"제품 {_np}장 · 가격 {_npr}장 — "
                           + ("✅ 짝 맞음" if _np == _npr and _np > 0 else "⚠️ 개수가 다릅니다 (같아야 등록)"))
            _bgo = st.button(f"🤖 일괄 분석·등록 ({min(_np, _npr)}개)", type="primary", key="ph_bgo",
                             disabled=not (_bprod and _bprice and _np == _npr), use_container_width=True)
            if _bgo and _bprod and _bprice and _np == _npr:
                import ai_service, tempfile, os as _os4
                set_setting(USERNAME, 'cafe24_naver_margin', str(int(_bm)))
                _bp = sorted(_bprod, key=lambda f: f.name)
                _bpr = sorted(_bprice, key=lambda f: f.name)
                _brows = []; _bprog = st.progress(0.0)
                for _bi in range(len(_bp)):
                    _bprog.progress((_bi + 1) / len(_bp))
                    _pf = _bp[_bi]; _rf = _bpr[_bi]
                    _i1, _e1 = ai_service.analyze_product_photo(_ph_aikey, _pf.getvalue(), _ph_mt(_pf))
                    _i2, _e2 = ai_service.analyze_price_tag(_ph_aikey, _rf.getvalue(), _ph_mt(_rf))
                    if _e1 or not _i1:
                        _brows.append({'제품파일': _pf.name[:14], '상태': '❌ 제품사진 분석실패'}); continue
                    _nm = _i1.get('name') or (_i2 or {}).get('product_name', '')
                    if not _nm:
                        _brows.append({'제품파일': _pf.name[:14], '상태': '❌ 상품명 판독실패'}); continue
                    _cost = int((_i2 or {}).get('price') or 0)
                    if _cost <= 0:
                        _brows.append({'제품파일': _pf.name[:14], '상품': _nm[:16], '상태': '❌ 가격 판독실패(0원)'}); continue
                    _sale = int(round(_cost * (1 + _bm / 100.0) / 0.945 / 10) * 10)
                    _cid = None; _cfull = ''
                    if _ph_oc and _ph_os:
                        _its, _ = naver_api.naver_shopping_search(_ph_oc, _ph_os, _nm)
                        _pth = [">".join([x for x in (it.get('category1'), it.get('category2'),
                                                      it.get('category3'), it.get('category4')) if x])
                                for it in (_its or [])]
                        _pth = [p for p in _pth if p]
                        if _pth:
                            _ch, _ = ai_service.suggest_naver_category(_ph_aikey, _nm, _pth)
                            _cid, _cfull = _nr_resolve_leaf(_ch or _pth[0])
                    if not _cid and _i1.get('category'):
                        _cr, _ = naver_api.search_naver_categories(api_id, api_secret, _i1['category'])
                        if _cr:
                            _cid, _cfull = _cr[0]['id'], _cr[0]['full_name']
                    if not _cid:
                        _brows.append({'제품파일': _pf.name[:14], '상품': _nm[:16], '상태': '❌ 카테고리 판단실패'}); continue
                    _ex = {'image/png': '.png', 'image/webp': '.webp'}.get(_ph_mt(_pf), '.jpg')
                    _fd, _tp = tempfile.mkstemp(suffix=_ex); _os4.close(_fd)
                    with open(_tp, 'wb') as _w:
                        _w.write(_pf.getvalue())
                    _cdn, _ue = naver_api.upload_product_image(api_id, api_secret, _tp)
                    try: _os4.remove(_tp)
                    except Exception: pass
                    if not _cdn:
                        _brows.append({'제품파일': _pf.name[:14], '상품': _nm[:16], '상태': '❌ 이미지업로드 실패'}); continue
                    _res, _re2 = naver_api.register_product(api_id, api_secret, {
                        "name": _nm, "sale_price": _sale, "image_url": _cdn, "category_id": _cid,
                        "seller_code": str((_i2 or {}).get('product_no', '') or ''),
                        "detail_html": _build_detail(_nm, [_cdn]),
                        "shipping_fee": 0, "origin_code": "03",
                        "after_service_tel": _gs("naver_as_tel") or "1588-1234",
                        "manufacturer": _i1.get('brand') or "상품 상세페이지 참조",
                    })
                    _brows.append({'제품파일': _pf.name[:14], '상품': _nm[:18],
                                   '카테고리': str(_cfull)[:16], '판매가': _sale,
                                   '상태': '✅ 등록' if not _re2 else f'❌ {str(_re2)[:20]}'})
                _bok = sum(1 for r in _brows if r.get('상태', '').startswith('✅'))
                st.success(f"📷 일괄 등록 완료 — 성공 {_bok} / 전체 {len(_brows)}건")
                st.dataframe(pd.DataFrame(_brows), use_container_width=True, hide_index=True)
                st.caption("💡 짝이 안 맞으면 제품·가격 사진을 같은 순서·개수로 다시 올리세요. 실패건은 위 건별로 재등록.")
        st.divider()

    # ── ✏️ 기존 상품 불러와서 수정 (전체 편집: 상품명·판매가·카테고리·태그·상세·사진) ──
    with st.expander("✏️ 기존 상품 불러와서 수정 — 등록된 네이버 상품 편집", expanded=False):
        st.caption("네이버에 이미 등록된 상품을 불러와 상품명·판매가·카테고리·연관태그·상세설명·사진을 수정합니다. "
                   "본인 커머스 API 키의 스토어 상품만 수정할 수 있습니다.")
        _edl1, _edl2 = st.columns([4, 1])
        _ed_q = _edl1.text_input("상품명 검색(부분 일치 — 비우면 전체)", key="ed_q",
                                 placeholder="예: 커피, 새우", label_visibility="collapsed")
        if _edl2.button("🔄 목록 불러오기", key="ed_load", use_container_width=True):
            with st.spinner("네이버 등록 상품 조회 중..."):
                _elst, _elerr = naver_api.get_product_list(api_id, api_secret, channel_seller_id)
            if _elerr:
                st.error(f"조회 실패: {_elerr}")
                st.session_state['_ed_list'] = []
            else:
                st.session_state['_ed_list'] = _elst or []
                st.success(f"✅ {len(_elst or [])}개 조회됨")
        _ed_list = st.session_state.get('_ed_list') or []
        if _ed_list:
            _edq = (_ed_q or '').strip().lower()
            _ed_fil = [p for p in _ed_list
                       if not _edq or _edq in str(p.get('productName', '')).lower()]
            st.caption(f"검색 결과 {len(_ed_fil)}개 / 전체 {len(_ed_list)}개 (최대 300개 표시)")
            _ed_opts = ['— 상품 선택 —'] + [
                f"{p.get('originProductNo') or p.get('channelProductNo')} · "
                f"{str(p.get('productName', ''))[:44]} · {fmt(int(p.get('salePrice') or 0))}원"
                for p in _ed_fil[:300]
            ]
            _edc1, _edc2 = st.columns([4, 1])
            _ed_sel = _edc1.selectbox("수정할 상품", _ed_opts, key="ed_sel",
                                      label_visibility="collapsed")
            if _edc2.button("📥 불러오기", key="ed_pull", use_container_width=True,
                            disabled=(_ed_sel == '— 상품 선택 —')):
                _ed_pno = _ed_sel.split(' · ', 1)[0].strip()
                with st.spinner("상품 상세 불러오는 중..."):
                    _ed_full, _ed_ono, _ed_ferr = naver_api.get_origin_product_full(
                        api_id, api_secret, _ed_pno)
                if _ed_ferr or not _ed_full:
                    st.error(f"불러오기 실패: {_ed_ferr}")
                else:
                    _op = _ed_full.get('originProduct') or {}
                    _da = _op.get('detailAttribute') or {}
                    _imgs = _op.get('images') or {}
                    _rep = ((_imgs.get('representativeImage') or {}) or {}).get('url') or ''
                    _extra = [(o or {}).get('url') for o in (_imgs.get('optionalImages') or [])
                              if (o or {}).get('url')]
                    _tags_cur = ((_da.get('seoInfo') or {}).get('sellerTags')) or []
                    _code_cur = ((_da.get('sellerCodeInfo') or {}).get('sellerManagementCode')) or ''
                    # 위젯 상태 초기화 (이전 편집 잔상 제거)
                    for _k in ('ed_name', 'ed_sale', 'ed_cat', 'ed_code', 'ed_desc',
                               'ed_tag_editor', 'ed_newimgs', '_ed_tags',
                               'ed_cat_kw', 'ed_cat_results', 'ed_cat_sel'):
                        st.session_state.pop(_k, None)
                    st.session_state['_ed_cur'] = {
                        'origin_no': _ed_ono,
                        'name': _op.get('name', ''),
                        'sale': int(_op.get('salePrice') or 0),
                        'cat_id': str(_op.get('leafCategoryId') or ''),
                        'rep': _rep, 'extra': _extra,
                        'seller_code': _code_cur,
                    }
                    st.session_state['_ed_tags'] = [
                        {'text': t.get('text'), 'code': t.get('code')}
                        for t in _tags_cur if isinstance(t, dict) and t.get('text')
                    ]
                    st.rerun()

        _ed_cur = st.session_state.get('_ed_cur')
        if _ed_cur:
            st.divider()
            st.markdown(f"**✏️ 수정 중** — 원상품번호 `{_ed_cur['origin_no']}`")
            _all_imgs = ([_ed_cur['rep']] if _ed_cur['rep'] else []) + _ed_cur['extra']
            if _all_imgs:
                st.image(_all_imgs[:6], width=84)
                st.caption(f"현재 등록 이미지 {len(_all_imgs)}장 — 아래에서 새 사진을 올리면 교체됩니다(안 올리면 유지).")

            _ed_name = st.text_input("상품명", value=_ed_cur['name'], key="ed_name")
            _edcc = st.columns(2)
            _ed_sale = _edcc[0].number_input("판매가", value=int(_ed_cur['sale']),
                                             min_value=0, step=100, key="ed_sale")
            _ed_code = _edcc[1].text_input("코스트코 상품번호(자체코드)",
                                           value=_ed_cur.get('seller_code', ''), key="ed_code")

            # 카테고리 — 현재 ID 표시 + 검색으로 변경
            _ed_cat = st.text_input("카테고리ID", value=_ed_cur['cat_id'], key="ed_cat",
                                    help="그대로 두면 기존 카테고리 유지. 아래 검색으로 변경 가능.")
            _edk1, _edk2 = st.columns([4, 1])
            _ed_ckw = _edk1.text_input("네이버 카테고리 검색", key="ed_cat_kw",
                                       placeholder="예: 냉동식품", label_visibility="collapsed")
            if _edk2.button("🔍", key="ed_cat_btn") and _ed_ckw.strip():
                _edcr, _edce = naver_api.search_naver_categories(api_id, api_secret, _ed_ckw.strip())
                st.session_state['ed_cat_results'] = _edcr or []
                if _edce:
                    st.error(_edce)
            _edcats = st.session_state.get('ed_cat_results') or []
            if _edcats:
                _edcopts = [f"{c['id']} — {c['full_name']}" for c in _edcats]
                _edcsel = st.selectbox("카테고리 선택 → ID 반영", _edcopts, key="ed_cat_sel")
                if st.button("✅ 이 카테고리로 변경", key="ed_cat_apply"):
                    _ed_cur['cat_id'] = _edcsel.split(' — ')[0].strip()
                    st.session_state['_ed_cur'] = _ed_cur
                    st.session_state.pop('ed_cat', None)   # 위젯 재초기화 → 새 ID 반영
                    st.rerun()

            # 새 사진 업로드 (교체용)
            _ed_newimgs = st.file_uploader(
                "제품 사진 교체 (여러 장 — 첫 장이 대표. 안 올리면 기존 유지)",
                accept_multiple_files=True, key="ed_newimgs")
            if _ed_newimgs:
                st.image([im for im in _ed_newimgs[:9]], width=84)
                st.caption("↑ 새 사진으로 교체됩니다. (상세페이지 사진도 함께 갱신)")

            # 제품 설명 (상세 재구성용) — 입력하거나 새 사진 있으면 상세 재생성
            _ed_desc = st.text_area(
                "제품 설명 (입력하거나 새 사진을 올리면 상세페이지를 재구성. 둘 다 비우면 기존 상세 유지)",
                key="ed_desc", height=90,
                placeholder="예: 코스트코 프리미엄 원두 1kg / 진한 풍미 …")
            if _ph_aikey and _ed_newimgs:
                _edg1, _edg2 = st.columns([1, 3])
                if _edg1.button("🤖 AI 상세설명 생성", key="ed_desc_gen", use_container_width=True):
                    import ai_service
                    with st.spinner("사진 분석 → 상세설명 작성 중..."):
                        _edt, _ederr = ai_service.generate_product_description(
                            _ph_aikey, _ed_newimgs[0].getvalue(), _ph_mt(_ed_newimgs[0]),
                            _ed_name.strip(), '')
                    if _edt:
                        st.session_state['ed_desc'] = _edt.strip(); st.rerun()
                    else:
                        st.warning(f"생성 실패: {_ederr}")
                _edg2.caption("새로 올린 첫 사진 기반 상세설명 자동 작성")

            # 연관태그 편집 (현재 태그 프리필 + AI 재생성)
            st.markdown("**🏷 연관태그 (최대 10개)**")
            if _ph_aikey:
                if st.button("🤖 AI 태그 10개 재생성", key="ed_tag_gen"):
                    _adc = (_gs('naver_ad_api_key'), _gs('naver_ad_secret'), _gs('naver_ad_customer_id'))
                    with st.spinner("AI 후보 → 사전 검증..."):
                        _edtags, _edtinfo = naver_api.build_seller_tags(
                            api_id, api_secret, _ph_aikey, _ed_name.strip(),
                            "", _ed_name.strip(),
                            ad_creds=_adc if all(_adc) else None)
                    if _edtags:
                        st.session_state['_ed_tags'] = [
                            {'text': t['text'], 'code': t.get('code')} for t in _edtags]
                        st.session_state.pop('ed_tag_editor', None)
                        st.rerun()
                    else:
                        st.warning(f"검증된 태그 없음 ({(_edtinfo or {}).get('candidates', 0)}개 후보)")
            _ed_cur_tags = st.session_state.get('_ed_tags') or []
            _ed_tag_df = pd.DataFrame(
                [{"사용": True, "태그": t['text'], "태그ID": t.get('code')} for t in _ed_cur_tags]
                or [{"사용": True, "태그": "", "태그ID": None}])
            _ed_ted = st.data_editor(
                _ed_tag_df, key="ed_tag_editor", hide_index=True, use_container_width=True,
                num_rows="dynamic",
                column_config={
                    "사용": st.column_config.CheckboxColumn("사용", default=True),
                    "태그": st.column_config.TextColumn("태그", required=False),
                    "태그ID": st.column_config.NumberColumn(
                        "태그ID", disabled=True,
                        help="사전 등록 태그 ID(검색 반영). 빈 값 = 직접입력 태그."),
                })
            _ed_sel_tags = []
            try:
                for _r in _ed_ted.to_dict("records"):
                    _t = str(_r.get("태그") or "").strip()
                    if _r.get("사용") and _t:
                        _e = {"text": _t}
                        _cd = _r.get("태그ID")
                        if _cd not in (None, "", 0) and not pd.isna(_cd):
                            _e["code"] = int(_cd)
                        _ed_sel_tags.append(_e)
            except Exception:
                _ed_sel_tags = [{"text": t['text'], "code": t.get('code')} for t in _ed_cur_tags]
            st.caption("체크 해제=제외 · 행 추가=직접입력 태그 · 저장 시 체크된 것만 반영. (빈 태그표면 그대로 저장 시 태그 제거)")

            _edb1, _edb2 = st.columns([1, 3])
            if _edb2.button("✖ 편집 취소", key="ed_cancel"):
                st.session_state.pop('_ed_cur', None)
                st.session_state.pop('_ed_tags', None)
                st.rerun()
            if _edb1.button("💾 수정 저장", type="primary", key="ed_save",
                            disabled=not _ed_name.strip()):
                _updates = {
                    'name': _ed_name.strip(),
                    'sale_price': int(_ed_sale),
                    'seller_tags': _ed_sel_tags,
                }
                if _ed_cat.strip():
                    _updates['category_id'] = _ed_cat.strip()
                if _ed_code.strip():
                    _updates['seller_code'] = _ed_code.strip()
                _new_cdns = []
                _upl_ok = True
                if _ed_newimgs:
                    import tempfile, os as _oed
                    with st.spinner(f"새 사진 {len(_ed_newimgs)}장 업로드 중..."):
                        for _im in _ed_newimgs[:10]:
                            _ex = {'image/png': '.png', 'image/webp': '.webp'}.get(_ph_mt(_im), '.jpg')
                            _fd, _tp = tempfile.mkstemp(suffix=_ex); _oed.close(_fd)
                            with open(_tp, 'wb') as _w:
                                _w.write(_im.getvalue())
                            _cu, _ue = naver_api.upload_product_image(api_id, api_secret, _tp)
                            try: _oed.remove(_tp)
                            except Exception: pass
                            if _cu:
                                _new_cdns.append(_cu)
                    if not _new_cdns:
                        _upl_ok = False
                        st.error("새 사진 업로드 실패 — 이미지 교체를 중단했습니다.")
                if _upl_ok:
                    if _new_cdns:
                        _updates['image_url'] = _new_cdns[0]
                        _updates['extra_image_urls'] = _new_cdns[1:]
                        _updates['detail_html'] = _build_detail(
                            _ed_name.strip(), _new_cdns, _ed_desc.strip())
                    elif _ed_desc.strip():
                        _keep = ([_ed_cur['rep']] if _ed_cur['rep'] else []) + _ed_cur['extra']
                        _updates['detail_html'] = _build_detail(
                            _ed_name.strip(), _keep, _ed_desc.strip())
                    with st.spinner("네이버 상품 수정 중..."):
                        _ok, _uerr, _uno = naver_api.update_product_full(
                            api_id, api_secret, _ed_cur['origin_no'], _updates)
                    if _ok:
                        st.success(
                            f"✅ 수정 완료! (원상품번호 {_uno}) — {_ed_name.strip()[:20]} / "
                            f"{fmt(int(_ed_sale))}원"
                            + (f" / 사진 {len(_new_cdns)}장 교체" if _new_cdns else "")
                            + (f" / 태그 {len(_ed_sel_tags)}개" if _ed_sel_tags else ""))
                        st.session_state.pop('_ed_cur', None)
                        st.session_state.pop('_ed_tags', None)
                        st.session_state.pop('_ed_list', None)
                        if invalidate_data_cache:
                            invalidate_data_cache()
                    else:
                        st.error(f"❌ 수정 실패: {_uerr}")
    st.divider()

    # ── 🛒→N 카페24 상품을 네이버에 등록 (건별 카테고리 선택 + 마진율 판매가) ──
    _cf_mall = _gs('cafe24_mall_id'); _cf_cid = _gs('cafe24_client_id'); _cf_tok = _gs('cafe24_access_token')
    if _cf_mall and _cf_cid and _cf_tok:
        import cafe24_api
        with st.expander("🛒→N 카페24 상품을 네이버에 등록", expanded=False):
            _cf_creds = {'mall_id': _cf_mall, 'client_id': _cf_cid,
                         'client_secret': _gs('cafe24_client_secret'),
                         'access_token': _cf_tok, 'refresh_token': _gs('cafe24_refresh_token'),
                         'expires_at': _gs('cafe24_token_expires_at')}
            def _cf_save(t):
                set_setting(USERNAME, 'cafe24_access_token', t.get('access_token', ''))
                set_setting(USERNAME, 'cafe24_refresh_token', t.get('refresh_token', ''))
                set_setting(USERNAME, 'cafe24_token_expires_at', t.get('expires_at', ''))
            _cf_dmode = st.radio("상세페이지 형식", ['a', 'c'], horizontal=True, key="cf_dmode",
                                 format_func=lambda x: "a) 카페24 상세 그대로 + 공통이미지"
                                 if x == 'a' else "c) 이미지형(카페24 이미지 추출 → 네이버에서 편집 쉬움)")
            _mc1, _mc2, _mc3 = st.columns([1, 3, 1])
            _margin = _mc1.number_input("마진율 %", min_value=0, max_value=300, step=5,
                                        value=int(_gs('cafe24_naver_margin') or 10), key="cf2n_margin",
                                        help="네이버 판매가 = 카페24가 ×(1+마진%) ÷0.945 (수수료 5.5% 감안)")
            _cf2n_q = _mc2.text_input("카페24 상품명 검색", key="cf2n_q",
                                      placeholder="상품명 일부 (비우면 최근 상품)", label_visibility="collapsed")
            if _mc3.button("🔎 조회", key="cf2n_search", use_container_width=True):
                set_setting(USERNAME, 'cafe24_naver_margin', str(int(_margin)))
                with st.spinner("카페24 상품 조회 중..."):
                    _cf2n_prods, _cf2n_err = cafe24_api.search_products(_cf_creds, _cf2n_q, save_tokens=_cf_save)
                st.session_state['_cf2n_prods'] = [] if _cf2n_err else (_cf2n_prods or [])
                if _cf2n_err:
                    st.error(f"조회 실패: {_cf2n_err}")
            if st.button("📋 전체 제품 가져오기 (카페24 카탈로그 전부)", key="cf2n_all",
                         use_container_width=True):
                set_setting(USERNAME, 'cafe24_naver_margin', str(int(_margin)))
                with st.spinner("카페24 전체 제품 가져오는 중... (수백 개면 시간이 걸립니다)"):
                    _cf_all, _cf_allerr = cafe24_api.get_all_products(_cf_creds, save_tokens=_cf_save)
                st.session_state['_cf2n_prods'] = [] if _cf_allerr else (_cf_all or [])
                if _cf_allerr:
                    st.error(f"전체 가져오기 실패: {_cf_allerr}")
                else:
                    st.success(f"✅ 카페24 전체 {len(_cf_all or [])}개 가져옴")
            _cf2n_list = st.session_state.get('_cf2n_prods') or []
            if _cf2n_list:
                st.caption(f"{len(_cf2n_list)}개 — 상품 펼쳐 카테고리 검색·선택 후 '네이버 등록', "
                           "또는 아래 🤖 버튼으로 AI가 카테고리 자동판단 후 일괄 등록.")

                # ── 🤖 AI 자동 카테고리·등록 (상품명 → 쇼핑검색 → AI 카테고리 → 등록) ──
                _oc = _gs('naver_open_client_id'); _os = _gs('naver_open_client_secret')
                _ai_key = _gs('anthropic_api_key') or get_global_setting('anthropic_api_key')
                if not (_oc and _os):
                    st.info("🤖 AI 자동등록은 **설정 탭 > 네이버 Open API**(쇼핑검색) 키가 필요합니다.")
                else:
                    def _cf2n_resolve_leaf(path):
                        """카테고리 경로(A>B>C>D) → 네이버 리프카테고리ID. (leaf명으로 검색 후 경로 최다일치 선택)"""
                        _leaf = str(path).split(">")[-1].strip()
                        if not _leaf:
                            return None, None
                        _cr, _ = naver_api.search_naver_categories(api_id, api_secret, _leaf)
                        if not _cr:
                            return None, None
                        _pt = set(str(path).replace(">", " ").replace("/", " ").split())
                        _best, _bs = None, -1
                        for _c in _cr:
                            _ct = set(str(_c.get('full_name', '')).replace(">", " ").replace("/", " ").split())
                            _s = len(_pt & _ct)
                            if _s > _bs:
                                _bs, _best = _s, _c
                        return (_best.get('id'), _best.get('full_name')) if _best else (None, None)

                    if st.button(f"🤖 AI 자동 카테고리·등록 (검색된 {min(30, len(_cf2n_list))}개 일괄)",
                                 key="cf2n_ai_auto", type="primary"):
                        import ai_service
                        _rows = []
                        _prog = st.progress(0.0)
                        _targets = _cf2n_list[:30]
                        for _i, _p in enumerate(_targets):
                            _prog.progress((_i + 1) / len(_targets))
                            _name = str(_p['product_name']); _cfprice = int(_p['price'])
                            _sale = int(round(_cfprice * (1 + _margin / 100.0) / 0.945 / 10) * 10)
                            _items, _serr = naver_api.naver_shopping_search(_oc, _os, _name)
                            _paths = [">".join([x for x in (it.get('category1'), it.get('category2'),
                                                            it.get('category3'), it.get('category4')) if x])
                                      for it in (_items or [])]
                            _paths = [p for p in _paths if p]
                            if not _paths:
                                _rows.append({'상품': _name[:26], '상태': '❌ 쇼핑검색 카테고리 없음'}); continue
                            _chosen, _ = ai_service.suggest_naver_category(_ai_key, _name, _paths)
                            _cat_id, _cat_full = _cf2n_resolve_leaf(_chosen or _paths[0])
                            if not _cat_id:
                                _rows.append({'상품': _name[:26], '카테고리': _chosen or '', '상태': '❌ 카테고리ID 변환실패'}); continue
                            _full, _fe = cafe24_api.get_product(_cf_creds, _p['product_no'], save_tokens=_cf_save)
                            _rep = (_full or {}).get('detail_image') or (_full or {}).get('list_image') or ''
                            if not _rep:
                                _rows.append({'상품': _name[:26], '상태': '❌ 이미지 없음'}); continue
                            _cdn, _ue = naver_api.upload_product_image(api_id, api_secret, _rep)
                            if not _cdn:
                                _rows.append({'상품': _name[:26], '상태': f'❌ 이미지업로드 실패'}); continue
                            _res, _re2 = naver_api.register_product(api_id, api_secret, {
                                "name": (_full or {}).get('product_name', _name), "sale_price": _sale,
                                "image_url": _cdn, "category_id": _cat_id,
                                "detail_html": _cafe24_detail(_cf_dmode,
                                    (_full or {}).get('product_name', _name), _cdn,
                                    (_full or {}).get('description')),
                                "shipping_fee": 0, "origin_code": "03",
                                "after_service_tel": _gs("naver_as_tel") or "1588-1234",
                            })
                            _rows.append({
                                '상품': _name[:26], '카테고리': str(_cat_full or '')[:24],
                                '판매가': _sale,
                                '상태': ('✅ 등록완료' if not _re2 else f'❌ {str(_re2)[:28]}'),
                            })
                        _ok_n = sum(1 for r in _rows if r.get('상태', '').startswith('✅'))
                        st.success(f"🤖 AI 자동등록 완료 — 성공 {_ok_n} / 전체 {len(_rows)}건")
                        st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
                        st.caption("💡 카테고리가 잘못 잡힌 건은 아래에서 상품 펼쳐 수동 카테고리로 다시 등록하세요.")
            for _p in _cf2n_list[:20]:
                _pno = _p['product_no']; _cfprice = int(_p['price'])
                _nprice_default = int(round(_cfprice * (1 + _margin / 100.0) / 0.945 / 10) * 10)
                with st.expander(f"{str(_p['product_name'])[:44]} · 카페24 {fmt(_cfprice)}원 "
                                 f"→ 네이버 {fmt(_nprice_default)}원", expanded=False):
                    _rc1, _rc2 = st.columns([1, 2])
                    _sale = _rc1.number_input("네이버 판매가", value=_nprice_default, min_value=0,
                                              step=100, key=f"cf2n_price_{_pno}")
                    _rcc1, _rcc2 = _rc2.columns([4, 1])
                    _catkw = _rcc1.text_input("네이버 카테고리 검색", key=f"cf2n_catkw_{_pno}",
                                              placeholder="예: 어묵, 반찬, 냉동식품",
                                              label_visibility="collapsed")
                    if _rcc2.button("🔍", key=f"cf2n_catbtn_{_pno}") and _catkw.strip():
                        _cr, _ce = naver_api.search_naver_categories(api_id, api_secret, _catkw.strip())
                        st.session_state[f'_cf2n_cats_{_pno}'] = _cr or []
                        if _ce:
                            st.error(_ce)
                    _cats = st.session_state.get(f'_cf2n_cats_{_pno}') or []
                    if _cats:
                        _opts = [f"{c['id']} — {c['full_name']}" for c in _cats]
                        _sel = st.selectbox("카테고리 선택", _opts, key=f"cf2n_catsel_{_pno}")
                        _cat_id = _sel.split(" — ")[0].strip()
                        if st.button("🛍 네이버 등록", key=f"cf2n_reg_{_pno}", type="primary"):
                            with st.spinner("카페24 상세 조회 → 이미지 업로드 → 네이버 등록 중..."):
                                _full, _fe = cafe24_api.get_product(_cf_creds, _pno, save_tokens=_cf_save)
                                if _fe or not _full:
                                    st.error(f"카페24 상세 조회 실패: {_fe}")
                                else:
                                    _rep = _full.get('detail_image') or _full.get('list_image') or ''
                                    if not _rep:
                                        st.error("대표 이미지가 없어 등록할 수 없습니다.")
                                    else:
                                        _cdn, _ue = naver_api.upload_product_image(api_id, api_secret, _rep)
                                        if not _cdn:
                                            st.error(f"이미지 업로드 실패: {_ue}")
                                        else:
                                            _res, _re2 = naver_api.register_product(api_id, api_secret, {
                                                "name": _full.get('product_name', _p['product_name']),
                                                "sale_price": int(_sale), "image_url": _cdn,
                                                "category_id": _cat_id,
                                                "detail_html": _cafe24_detail(_cf_dmode,
                                                    _full.get('product_name', _p['product_name']),
                                                    _cdn, _full.get('description')),
                                                "shipping_fee": 0, "origin_code": "03",
                                                "after_service_tel": _gs("naver_as_tel") or "1588-1234",
                                            })
                                            if _re2:
                                                st.error(f"❌ 네이버 등록 실패: {_re2}")
                                            else:
                                                st.success(f"✅ 네이버 등록 완료! (origin #{_res.get('origin_product_no')}) "
                                                           f"— 스마트스토어에서 확인하세요.")
                    else:
                        st.caption("네이버 카테고리를 검색·선택하면 '네이버 등록' 버튼이 나타납니다.")
        st.divider()

    # ── 개별 등록 폼 (제품 DB 탭 🛍 버튼 클릭 시) ──────────────────
    _nr_kw_sel = st.session_state.get("nreg2_kw")
    _nr_prod   = next((p for p in _nr_all if p["match_keyword"] == _nr_kw_sel), None) if _nr_kw_sel else None
    _cat_map   = {}
    try:
        _cat_map = _nr_json.loads(_gs("naver_cat_mappings") or "{}")
    except Exception:
        pass

    if _nr_prod:
        _nr_sp_id  = _nr_prod.get("shared_id")
        _saved_cat = _nr_prod.get("naver_category_id") or ""
        if not _saved_cat:
            _prod_ccat = _nr_prod.get("category", "")
            _saved_cat = (_cat_map.get(_prod_ccat) or {}).get("id", "") if _prod_ccat else ""
        _saved_cat = _saved_cat or _gs("naver_default_category") or ""
        with st.expander(f"✏️ 개별 등록 — {_nr_prod['costco_name']}", expanded=True):
            _nr_name = st.text_input("상품명", value=_nr_prod["costco_name"][:100], key="nr2_name")
            _nr_cc1, _nr_cc2 = st.columns([4, 1])
            _nr_cat_kw = _nr_cc1.text_input("네이버 카테고리 검색", placeholder="예: 냉동피자",
                                             key="nr2_cat_kw", label_visibility="collapsed")
            if _nr_cc2.button("🔍", key="nr2_cat_search") and _nr_cat_kw.strip():
                with st.spinner():
                    _nr_cr, _ = naver_api.search_naver_categories(api_id, api_secret, _nr_cat_kw.strip())
                st.session_state["nr2_cat_results"] = _nr_cr
            _nr_catlist = st.session_state.get("nr2_cat_results", [])
            if _nr_catlist:
                _nr_catopts = [f"{c['id']} — {c['full_name']}" for c in _nr_catlist]
                _nr_catchosen = st.selectbox("카테고리", _nr_catopts, key="nr2_cat_sel")
                _nr_cat = _nr_catchosen.split(" — ")[0].strip()
            else:
                _nr_cat = st.text_input("카테고리 ID", value=_saved_cat,
                                        placeholder="예: 50000803", key="nr2_cat",
                                        label_visibility="collapsed")
            _nr_c3, _nr_c4, _nr_c5 = st.columns(3)
            _nr_defprice = int(_nr_prod.get("sale_price") or 0) or int(_nr_prod.get("unit_price") or 0)
            _nr_price = _nr_c3.number_input("판매가", value=_nr_defprice, step=100, key="nr2_price")
            _nr_fee   = _nr_c4.number_input("배송비", value=int(_nr_prod.get("shipping_fee") or 0), step=500, key="nr2_fee")
            _nr_stock = _nr_c5.number_input("재고",   value=100, step=10, key="nr2_stock")
            _nr_as    = st.text_input("A/S 전화번호", value=_gs("naver_as_tel") or "",
                                      placeholder="010-0000-0000", key="nr2_as")
            _nr_img = _nr_prod.get("local_image") or _nr_prod.get("image_url") or ""
            if _nr_img: st.image(_nr_img, width=80)
            _nr_b1, _nr_b2 = st.columns([1, 4])
            if _nr_b2.button("✖ 취소", key="nr2_cancel"):
                st.session_state.pop("nreg2_kw", None); st.session_state.pop("nr2_cat_results", None); st.rerun()
            if _nr_b1.button("🛍 등록", key="nr2_submit", type="primary"):
                if not _nr_cat or not _nr_price or not _nr_img:
                    st.error("카테고리·판매가·이미지를 확인하세요.")
                else:
                    with st.spinner("업로드 중..."):
                        _nr_cdn, _e1 = naver_api.upload_product_image(api_id, api_secret, _nr_img)
                    if _e1 or not _nr_cdn:
                        st.error(f"이미지 실패: {_e1}")
                    else:
                        _nr_extra_imgs = []
                        _nr_xraw = _nr_prod.get("extra_images") or ""
                        if _nr_xraw:
                            try: _nr_extra_imgs = _nr_json.loads(_nr_xraw)
                            except Exception: pass
                        _nr_xcdn = []
                        if _nr_extra_imgs:
                            _nr_xcdn, _ = naver_api.upload_images_batch(api_id, api_secret, _nr_extra_imgs)
                        _nr_det = ""
                        if _nr_prod.get("has_detail") and _nr_sp_id:
                            _, _nr_det = get_product_detail(_nr_sp_id)
                        _nr_res, _e2 = naver_api.register_product(api_id, api_secret, {
                            "name": _nr_name, "sale_price": _nr_price,
                            "image_url": _nr_cdn, "category_id": _nr_cat,
                            "stock": _nr_stock, "shipping_fee": _nr_fee,
                            "after_service_tel": _nr_as or "1588-1234",
                            "extra_image_urls": _nr_xcdn, "detail_html": _nr_det,
                        })
                        if _e2 or not _nr_res:
                            st.error(f"등록 실패: {_e2}")
                        else:
                            _npno = _nr_res.get("origin_product_no", "")
                            upsert_user_private(USERNAME, _nr_kw_sel, _nr_prod["costco_name"], naver_product_no=_npno)
                            if _nr_sp_id:
                                try:
                                    _ca = sqlite3.connect(AUTH_DB)
                                    _ca.execute("UPDATE shared_products SET naver_category_id=? WHERE id=?", (_nr_cat, _nr_sp_id))
                                    _ca.commit(); _ca.close()
                                except Exception: pass
                            set_setting(USERNAME, "naver_as_tel", _nr_as)
                            st.success(f"✅ 등록 완료! 상품번호: {_npno}")
                            st.session_state.pop("nreg2_kw", None); st.rerun()
        st.divider()

    if not _nr_unreg:
        st.success("🎉 모든 상품이 등록 완료되었습니다!")
    else:
        # ══ 메인 등록 플로우 ══════════════════════════════════════════

        # ── STEP 1: 코스트코 카테고리 필터 ──────────────────────────
        _costco_cats = sorted({p.get("category","") for p in _nr_unreg if p.get("category","")})
        _uncat_cnt   = sum(1 for p in _nr_unreg if not p.get("category",""))

        st.markdown("#### STEP 1 — 코스트코 카테고리 선택")
        st.caption("먼저 코스트코 카테고리를 선택해 후보 상품을 좁힙니다.")

        _nr4_cc = st.session_state.get("nr4_costco_cat", "")
        _cc_opts = ["전체"] + _costco_cats + (["(미분류)"] if _uncat_cnt else [])
        _cc_cols = st.columns(min(len(_cc_opts), 6))
        for _ci, _cn in enumerate(_cc_opts):
            _cnt_here = (
                len(_nr_unreg) if _cn == "전체"
                else sum(1 for p in _nr_unreg if not p.get("category","")) if _cn == "(미분류)"
                else sum(1 for p in _nr_unreg if p.get("category","") == _cn)
            )
            _is_sel = _cn == _nr4_cc
            if _cc_cols[_ci % 6].button(
                f"{'▶ ' if _is_sel else ''}{_cn}\n{_cnt_here}개",
                key=f"nr4_cc_{_ci}", use_container_width=True,
                type="primary" if _is_sel else "secondary",
            ):
                st.session_state["nr4_costco_cat"] = _cn
                st.session_state.pop("nr4_ai_results", None)
                st.rerun()

        # 코스트코 카테고리 필터 적용
        if not _nr4_cc or _nr4_cc == "전체":
            _cc_pool = _nr_unreg
        elif _nr4_cc == "(미분류)":
            _cc_pool = [p for p in _nr_unreg if not p.get("category","")]
        else:
            _cc_pool = [p for p in _nr_unreg if p.get("category","") == _nr4_cc]

        st.caption(f"후보 상품: {len(_cc_pool)}개")
        st.divider()

        # ── STEP 2: 네이버 카테고리 선택 ─────────────────────────────
        st.markdown("#### STEP 2 — 네이버 카테고리 선택")

        _nr4_ncat_id   = st.session_state.get("nr4_naver_cat_id", "")
        _nr4_ncat_name = st.session_state.get("nr4_naver_cat_name", "")

        _ns1, _ns2, _ns3 = st.columns([4, 1, 2])
        _nr4_srch_kw = _ns1.text_input(
            "네이버 카테고리 검색", placeholder="예: 냉동피자, 과자, 건강기능식품",
            key="nr4_ncat_kw", label_visibility="collapsed",
        )
        if _ns2.button("🔍 검색", key="nr4_ncat_search", use_container_width=True) and _nr4_srch_kw.strip():
            with st.spinner("검색 중..."):
                _nr4_sr, _nr4_se = naver_api.search_naver_categories(api_id, api_secret, _nr4_srch_kw.strip())
            st.session_state["nr4_ncat_results"] = _nr4_sr if not _nr4_se else []
            if _nr4_se: st.warning(f"검색 오류: {_nr4_se}")
            elif not _nr4_sr: st.info("검색 결과 없음")

        _nr4_catlist = st.session_state.get("nr4_ncat_results", [])
        if _nr4_catlist:
            _nr4_catopts = [f"{c['id']} — {c['full_name']}" for c in _nr4_catlist]
            _nr4_chosen  = st.selectbox("검색 결과에서 카테고리 선택", _nr4_catopts, key="nr4_ncat_sel")
            if st.button("✅ 이 카테고리로 설정", key="nr4_ncat_confirm", type="primary"):
                _nr4_cid   = _nr4_chosen.split(" — ")[0].strip()
                _nr4_cname = _nr4_chosen.split(" — ", 1)[1] if " — " in _nr4_chosen else ""
                st.session_state["nr4_naver_cat_id"]   = _nr4_cid
                st.session_state["nr4_naver_cat_name"] = _nr4_cname
                st.session_state.pop("nr4_ai_results", None)
                st.rerun()

        if _nr4_ncat_id:
            _ns3.success(f"선택됨: {_nr4_ncat_name.split(' > ')[-1]}")
            _ns3.caption(_nr4_ncat_name)
        else:
            _ns3.info("카테고리를 선택하세요")

        st.divider()

        # ── STEP 3: 상품명 검색 + AI 추천 ──────────────────────────
        st.markdown("#### STEP 3 — 상품명 검색 + AI 추천")

        _nk1, _nk2, _nk3 = st.columns([3, 1, 1])
        _nr4_kw = _nk1.text_input(
            "상품명 키워드 (선택사항)", placeholder="예: 피자, 치즈, 새우",
            key="nr4_prod_kw", label_visibility="collapsed",
        )

        _can_ai = bool(_nr4_ncat_id and _cc_pool)
        if _nk2.button("🤖 AI 추천", key="nr4_ai_btn", type="primary",
                        use_container_width=True, disabled=not _can_ai):
            # 점수 계산 함수
            def _nr4_score(prod_name, cat_name, kw):
                _cat_terms = set()
                for _part in cat_name.split(" > ")[-2:]:
                    for _w in _nr_re.sub(r'[/·,]', ' ', _part).split():
                        if len(_w) > 1: _cat_terms.add(_w.lower())
                _prod_lower = prod_name.lower()
                _prod_terms = set(_nr_re.sub(r'[()[\]/,\s]', ' ', _prod_lower).split())
                _overlap    = len(_cat_terms & _prod_terms)
                _kw_bonus   = 2 if kw and kw.lower() in _prod_lower else 0
                return _overlap + _kw_bonus

            # 키워드 필터
            _kw_stripped = (_nr4_kw or "").strip()
            _pool_filtered = [
                p for p in _cc_pool
                if not _kw_stripped or _kw_stripped.lower() in p["costco_name"].lower()
            ]

            # 점수 계산 및 정렬
            _scored = []
            for _p in _pool_filtered:
                _s = _nr4_score(_p["costco_name"], _nr4_ncat_name, _kw_stripped)
                _scored.append((_s, _p))
            _scored.sort(key=lambda x: -x[0])

            # 점수 없어도 전체 표시 (낮은 관련도 포함)
            st.session_state["nr4_ai_results"] = [
                {**p, "_score": s} for s, p in _scored
            ]
            st.rerun()

        if _nk3.button("🔄 초기화", key="nr4_reset", use_container_width=True):
            for _k in ["nr4_ai_results","nr4_naver_cat_id","nr4_naver_cat_name",
                        "nr4_ncat_results","nr4_costco_cat"]:
                st.session_state.pop(_k, None)
            st.rerun()

        # ── STEP 4: 체크박스 목록 + 업로드 ──────────────────────────
        _nr4_results = st.session_state.get("nr4_ai_results", [])

        if not _nr4_results:
            if not _can_ai:
                st.info("네이버 카테고리를 선택한 후 🤖 AI 추천 버튼을 클릭하세요.")
        else:
            st.divider()
            _high  = [p for p in _nr4_results if p["_score"] >= 2]
            _mid   = [p for p in _nr4_results if p["_score"] == 1]
            _low   = [p for p in _nr4_results if p["_score"] == 0]

            st.markdown(
                f"**추천 결과** — "
                f"<span style='color:#1a7a4a'>높음 {len(_high)}개</span> · "
                f"<span style='color:#b8860b'>보통 {len(_mid)}개</span> · "
                f"<span style='color:#888'>낮음 {len(_low)}개</span>  "
                f"(총 {len(_nr4_results)}개)",
                unsafe_allow_html=True,
            )

            _reg_r1, _reg_r2 = st.columns(2)
            _nr4_as  = _reg_r1.text_input("A/S 전화번호", value=_gs("naver_as_tel") or "",
                                           placeholder="010-0000-0000", key="nr4_as")
            _nr4_stk = _reg_r2.number_input("재고 수량", value=100, step=10, key="nr4_stk")

            # 헤더
            _rh = st.columns([0.5, 0.8, 0.8, 4, 2])
            for _hc, _ht in zip(_rh, ["선택","관련도","이미지","상품명","판매가"]):
                _hc.markdown(f"**{_ht}**")
            st.markdown("<hr style='margin:2px 0'>", unsafe_allow_html=True)

            _sel_all4 = st.checkbox("전체 선택/해제", value=True, key="nr4_chk_all")
            _checked4 = []

            def _score_badge(s):
                if s >= 2: return "🟢"
                if s == 1: return "🟡"
                return "⚪"

            for _ri4, _rp4 in enumerate(_nr4_results):
                _rrow4 = st.columns([0.5, 0.8, 0.8, 4, 2])
                _chk4  = _rrow4[0].checkbox(
                    "", value=(_sel_all4 and _rp4["_score"] >= 1),
                    key=f"nr4_chk_{_ri4}", label_visibility="collapsed",
                )
                _rrow4[1].markdown(_score_badge(_rp4["_score"]))
                _rimg4 = _rp4.get("image_url") or _rp4.get("local_image") or ""
                if _rimg4:
                    _rrow4[2].markdown(
                        f"<img src='{_rimg4}' width='44' height='44' "
                        f"style='object-fit:cover;border-radius:4px'>",
                        unsafe_allow_html=True,
                    )
                else:
                    _rrow4[2].caption("없음")
                _rrow4[3].markdown(_rp4["costco_name"])
                _rp4_price = int(_rp4.get("sale_price") or 0) or int(_rp4.get("unit_price") or 0)
                _rrow4[4].markdown(
                    f"{fmt(_rp4_price)}원" if _rp4_price
                    else "<span style='color:red'>가격 없음</span>",
                    unsafe_allow_html=True,
                )
                if _chk4:
                    _checked4.append(_rp4)
                st.markdown(
                    "<hr style='margin:-2px 0 -4px 0;border-color:#f0f0f0'>",
                    unsafe_allow_html=True,
                )

            # 경고
            _no_price4 = [p["costco_name"][:18] for p in _checked4
                           if not (int(p.get("sale_price") or 0) or int(p.get("unit_price") or 0))]
            _no_img4   = [p["costco_name"][:18] for p in _checked4
                           if not (p.get("image_url") or p.get("local_image"))]
            if _no_price4: st.warning(f"가격 없음: {', '.join(_no_price4[:3])}")
            if _no_img4:   st.warning(f"이미지 없음: {', '.join(_no_img4[:3])}")

            # 장바구니 추가 버튼
            _CART_MAX  = 30
            _cart_now  = st.session_state.get("nr4_cart", [])
            _cart_mks  = {i["product"]["match_keyword"] for i in _cart_now}
            _new_items = [p for p in _checked4 if p["match_keyword"] not in _cart_mks]
            _slots_left = _CART_MAX - len(_cart_now)
            _can_add    = bool(_new_items) and bool(_nr4_ncat_id) and _slots_left > 0

            _add_c1, _add_c2 = st.columns([2, 3])
            if _add_c1.button(
                f"➕ 장바구니에 {min(len(_new_items), max(_slots_left,0))}개 추가",
                key="nr4_add_cart", type="primary",
                disabled=not _can_add,
            ):
                _to_add = _new_items[:_slots_left]
                for _ap in _to_add:
                    _cart_now.append({
                        "product":  _ap,
                        "cat_id":   _nr4_ncat_id,
                        "cat_name": _nr4_ncat_name,
                    })
                st.session_state["nr4_cart"] = _cart_now
                _added_mks = {p["match_keyword"] for p in _to_add}
                st.session_state["nr4_ai_results"] = [
                    p for p in _nr4_results if p["match_keyword"] not in _added_mks
                ]
                st.rerun()

            if len(_cart_now) >= _CART_MAX:
                _add_c2.warning(f"장바구니 {_CART_MAX}개 한도 초과 — 먼저 일괄 등록 후 추가하세요.")
            elif _new_items:
                _add_c2.caption(
                    f"장바구니 {len(_cart_now)}/{_CART_MAX}개 · "
                    f"추가 가능 {min(len(_new_items), _slots_left)}개"
                )
            if not _new_items and _checked4:
                _add_c2.info("선택한 상품이 이미 모두 장바구니에 있습니다.")

    # ── 카테고리 기본 매핑 (보조) ────────────────────────────────────
    st.divider()
    _nr_all_costco_cats = sorted({p.get("category","") for p in _nr_all if p.get("category","")})
    _cat_map_cnt = sum(1 for c in _nr_all_costco_cats if (_cat_map.get(c) or {}).get("id"))
    with st.expander(f"⚙️ 카테고리 기본 매핑 — {_cat_map_cnt}/{len(_nr_all_costco_cats)} 완료",
                     expanded=False):
        st.caption("코스트코 카테고리별 네이버 기본 카테고리 ID입니다. 제품별 ID 없을 때 보조로 사용됩니다.")
        _mh1, _mh2 = st.columns([4, 1])
        _map_kw = _mh1.text_input("검색", placeholder="예: 냉동, 건강식품", key="map_srch_kw")
        if _mh2.button("🔍 검색", key="map_srch_btn") and _map_kw.strip():
            _msr, _ = naver_api.search_naver_categories(api_id, api_secret, _map_kw.strip())
            st.session_state["map_srch_res"] = _msr
        if st.session_state.get("map_srch_res"):
            _ms_opts = ["— 선택하세요 —"] + [f"{c['id']} — {c['full_name']}" for c in st.session_state["map_srch_res"]]
            _ms_sel  = st.selectbox("결과", _ms_opts, key="map_srch_sel")
            if _ms_sel != "— 선택하세요 —":
                st.info(f"ID: **{_ms_sel.split(' — ')[0].strip()}**")
        st.divider()
        for _ccat in _nr_all_costco_cats:
            _cur = _cat_map.get(_ccat) or {}
            _cur_id = _cur.get("id","") if isinstance(_cur, dict) else str(_cur or "")
            _mc1, _mc2 = st.columns([2, 3])
            _mc1.markdown(f"**{_ccat}**")
            if _cur_id: _mc1.caption(f"현재: `{_cur_id}`")
            _mc2.text_input(f"ID ({_ccat})", value=_cur_id, placeholder="예: 50001234",
                            key=f"mapid_{_ccat}", label_visibility="collapsed")
        if st.button("💾 저장", key="map_save_btn", type="primary"):
            _new_map = {}
            for _ccat in _nr_all_costco_cats:
                _v = (st.session_state.get(f"mapid_{_ccat}") or "").strip()
                if _v:
                    _new_map[_ccat] = {"id": _v, "name": ""}
            set_setting(USERNAME, "naver_cat_mappings", _nr_json.dumps(_new_map, ensure_ascii=False))
            st.success(f"✅ {len(_new_map)}개 저장 완료!")
            st.rerun()

    # ── 등록 완료 목록 ────────────────────────────────────────────────
    if _nr_reg:
        with st.expander(f"✅ 등록 완료 ({len(_nr_reg)}개)", expanded=False):
            st.dataframe(pd.DataFrame([{
                "상품명": p["costco_name"], "카테고리": p.get("category",""),
                "판매가": f"{fmt(int(p.get('sale_price') or 0))}원",
                "네이버번호": p.get("naver_product_no",""),
            } for p in _nr_reg]), use_container_width=True, hide_index=True)
