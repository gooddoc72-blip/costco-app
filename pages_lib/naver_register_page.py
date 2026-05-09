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
