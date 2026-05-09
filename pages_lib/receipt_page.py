"""🧾 영수증 등록 페이지 — pages_lib 자동 추출."""
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
    apply_receipt_to_unmatched_daily_orders,
    get_last_skipped_box_prices,
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


def render(USERNAME: str, IS_ADMIN: bool, settings: dict, embedded: bool = False, order_date: str = ""):
    """🧾 영수증 등록 탭 렌더링.

    Args:
        embedded: True면 다른 페이지 내부에서 호출됨 → st.header() 생략
        order_date: 수익계산 선택 날짜 (embedded 시 미매칭 교차매칭에 사용)
    """
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    if not embedded:
        st.header("🧾 코스트코 영수증 등록")

    st.subheader("📄 영수증 PDF 업로드 (여러 파일 동시 등록 가능)")
    receipt_files = st.file_uploader(
        "코스트코 영수증 PDF (여러 파일 선택 가능)",
        type=['pdf'], key="receipt_pdf", accept_multiple_files=True
    )

    if receipt_files:
        all_parsed = []
        fail_files = []   # [(filename, error_msg)]
        for rf in receipt_files:
            items, err = parse_costco_receipt_pdf(rf)
            if items:
                for p in items:
                    p['_file'] = rf.name
                all_parsed.extend(items)
            else:
                fail_files.append((rf.name, err))

        if all_parsed:
            # 같은 상품번호/상품명이면 영수증 날짜가 최신인 항목 우선
            merged = {}
            for p in all_parsed:
                key = p.get('상품번호') or p['상품명']
                existing = merged.get(key)
                if existing is None:
                    merged[key] = p
                else:
                    # receipt_date가 있으면 최신 날짜 우선, 없으면 나중에 파싱된 것 우선
                    if (p.get('receipt_date', '') or '') >= (existing.get('receipt_date', '') or ''):
                        merged[key] = p
            deduped = list(merged.values())

            st.success(f"✅ {len(receipt_files) - len(fail_files)}개 파일 / {len(deduped)}종 상품 인식")
            if fail_files:
                for fname, emsg in fail_files:
                    with st.expander(f"⚠️ 인식 실패: {fname}", expanded=False):
                        st.warning(emsg)

            # 파일별 탭으로 결과 표시
            if len(receipt_files) > 1:
                file_names = sorted(set(p['_file'] for p in all_parsed))
                tabs = st.tabs([f"📄 {n}" for n in file_names] + ["📋 전체 합산"])
                for ti, fname in enumerate(file_names):
                    with tabs[ti]:
                        file_items = [p for p in all_parsed if p['_file'] == fname]
                        st.dataframe(
                            pd.DataFrame(file_items)[['상품번호', '상품명', '수량', '단가']],
                            use_container_width=True, hide_index=True
                        )
                with tabs[-1]:
                    st.dataframe(
                        pd.DataFrame(deduped)[['상품번호', '상품명', '수량', '단가']],
                        use_container_width=True, hide_index=True
                    )
            else:
                st.dataframe(
                    pd.DataFrame(deduped)[['상품번호', '상품명', '수량', '단가']],
                    use_container_width=True, hide_index=True
                )

            st.session_state['receipt_items'] = [
                {"상품명": p['상품명'], "수량": p['수량'], "단가": p['단가'],
                 "상품번호": p.get('상품번호', ''), "receipt_date": p.get('receipt_date', '')}
                for p in deduped
            ]

            # 인식된 영수증 날짜 표시
            _dates = sorted({p.get('receipt_date', '') for p in deduped if p.get('receipt_date')})
            if _dates:
                st.caption(f"📅 영수증 날짜: {', '.join(_dates)}")

            # ── 🔗 네이버 등록 상품과 코스트코 상품번호 매칭 ──────────
            st.divider()
            st.subheader("🔗 네이버 등록 상품 ↔ 영수증 매칭")
            st.caption("점수 = 0.30×토큰유사도 + 0.25×**키워드매칭** + 0.25×사이즈일치 + 0.20×브랜드일치")
            st.caption("🟢 **확실** ≥70% → **자동 저장** ⚡  |  🟡 **유력** 50~70% → **자동 저장** ⚡  |  🟠 **참고** 30~50% (검토 필요)")

            _match_result = match_receipt_to_naver_products(USERNAME, deduped, threshold=0.30)
            _matched_full = _match_result['matched']

            # 1단계: 확실(≥70%) + 유력(50~70%) 자동 저장
            _auto_certain = [m for m in _matched_full if m['tier'] in ('확실', '유력')]
            _auto_count = 0
            if _auto_certain:
                _auto_count = apply_receipt_pno_updates(USERNAME, _auto_certain)
                invalidate_data_cache()
                # 자동 저장 후 후보가 줄었으니 매칭 다시 (남은 참고만 표시)
                _match_result = match_receipt_to_naver_products(USERNAME, deduped, threshold=0.30)

            _matched = _match_result['matched']
            _cand_n = _match_result['candidates_count']
            _skipped_n = len(_match_result.get('skipped_already', []))
            _t_likely = sum(1 for m in _matched if m['tier'] == '유력')
            _t_ref    = sum(1 for m in _matched if m['tier'] == '참고')

            if _auto_count:
                st.success(
                    f"⚡ **확실·유력 매칭 {_auto_count}건 자동 저장 완료!** "
                    f"코스트코 상품번호 + 매입가가 제품 DB에 즉시 반영되었습니다."
                )
            # 박스 단가 의심으로 매입가 적용 거부된 항목 안내
            _skipped_box = get_last_skipped_box_prices()
            if _skipped_box:
                with st.expander(
                    f"⚠️ 박스 단가 의심으로 매입가 미적용 {len(_skipped_box)}건 (상품번호만 갱신됨)",
                    expanded=True,
                ):
                    st.caption(
                        "영수증 단가가 판매가의 5배 초과 또는 200,000원 초과로 박스 단위로 추정되어 "
                        "1팩 단가로 적용되지 않았습니다. **수동으로 1팩 단가를 입력해주세요.**"
                    )
                    for _sb in _skipped_box:
                        _msg = (
                            f"• `{_sb['pno']}` {_sb['receipt_name'][:55]}\n"
                            f"  영수증단가: **{int(_sb['rejected_price']):,}원** | "
                            f"판매가: {int(_sb['sale_price']):,}원 | "
                            f"기존단가: {int(_sb['kept_price']):,}원 (유지됨)"
                        )
                        st.markdown(_msg)
            if _skipped_n:
                st.info(
                    f"ℹ️ 이미 DB에 등록된 상품번호 **{_skipped_n}건**은 매칭에서 제외되었습니다. (중복 방지)"
                )

            _mc1, _mc2, _mc3, _mc4, _mc5 = st.columns(5)
            _mc1.metric("⚡ 자동 저장됨",      f"{_auto_count}건")
            _mc2.metric("🔁 이미 매칭됨",      f"{_skipped_n}건", help="이미 DB에 코스트코 상품번호가 있어서 매칭 제외")
            _mc3.metric("매칭 후보 (남음)",   f"{_cand_n}개")
            _mc4.metric("🟡 유력 (검토)",     f"{_t_likely}건")
            _mc5.metric("🟠 참고 (검토)",     f"{_t_ref}건")

            if _matched:
                # 🔍 검색 필터
                _flt_q = st.text_input(
                    "🔍 매칭 결과 검색 (네이버 상품명, 영수증 상품명, 키워드)",
                    placeholder="예: 메르시 / 감자칩 / 675g",
                    key="receipt_match_search",
                    label_visibility="visible"
                ).strip().lower()

                # 필터 적용
                if _flt_q:
                    _filtered_matched = [
                        m for m in _matched
                        if _flt_q in (m.get('user_kw') or '').lower()
                        or _flt_q in (m.get('receipt_name') or '').lower()
                        or _flt_q in (m.get('costco_pno') or '').lower()
                    ]
                    st.caption(f"🔎 '{_flt_q}' 검색 결과: {len(_filtered_matched)} / {len(_matched)} 건")
                else:
                    _filtered_matched = _matched

                _hdr = st.columns([0.4, 2.8, 2.8, 1.4, 0.9, 1.7])
                _hdr[0].markdown("**✓**")
                _hdr[1].markdown("**네이버 상품**")
                _hdr[2].markdown("**영수증 상품**")
                _hdr[3].markdown("**상품번호**")
                _hdr[4].markdown("**점수**")
                _hdr[5].markdown("**세부 (J/K/S/B)**")
                st.markdown("<hr style='margin:2px 0 4px 0'>", unsafe_allow_html=True)

                _selected_matches = []
                for _mi, _m in enumerate(_filtered_matched):
                    _r = st.columns([0.4, 2.8, 2.8, 1.4, 0.9, 1.7])
                    _default_on = _m['tier'] in ('확실', '유력')
                    _ck = _r[0].checkbox("", key=f"rmt_{_m['user_id']}", value=_default_on, label_visibility="collapsed")
                    _tier_color = {"확실": "#27ae60", "유력": "#f1c40f", "참고": "#e67e22"}[_m['tier']]
                    _tier_icon  = {"확실": "🟢", "유력": "🟡", "참고": "🟠"}[_m['tier']]
                    _r[1].markdown(
                        f"<small>{_tier_icon} {_m['user_kw'][:48]}</small>",
                        unsafe_allow_html=True
                    )
                    _r[2].markdown(f"<small>{_m['receipt_name'][:48]}</small>", unsafe_allow_html=True)
                    _r[3].markdown(f"`{_m['costco_pno']}`")
                    _r[4].markdown(
                        f"<span style='color:{_tier_color};font-weight:700;font-size:14px'>{_m['score']*100:.0f}%</span>",
                        unsafe_allow_html=True
                    )
                    _r[5].markdown(
                        f"<span style='color:#888;font-size:11px'>"
                        f"J:{_m['jaccard']*100:.0f} / "
                        f"K:{_m['keyword_score']*100:.0f} / "
                        f"S:{_m['size_score']*100:.0f} / "
                        f"B:{_m['brand_score']*100:.0f}</span>",
                        unsafe_allow_html=True
                    )
                    if _ck:
                        _selected_matches.append(_m)

                st.markdown("<hr style='margin:6px 0'>", unsafe_allow_html=True)
                if st.button(
                    f"💾 검토한 {len(_selected_matches)}건 추가 저장 (유력/참고 매칭)",
                    type="primary",
                    disabled=not _selected_matches,
                    key="apply_receipt_pno",
                    use_container_width=True
                ):
                    n = apply_receipt_pno_updates(USERNAME, _selected_matches)
                    invalidate_data_cache()
                    st.success(f"✅ {n}개 네이버 등록 상품에 코스트코 상품번호가 추가 저장되었습니다.")
                    st.rerun()

            # ── 매칭 안 된 영수증 항목: 일괄 검색 매칭 ──────────
            _unmatched = _match_result.get('unmatched_receipt', []) or []
            if _unmatched:
                st.markdown("<hr style='margin:14px 0 8px 0'>", unsafe_allow_html=True)
                with st.expander(f"🔎 매칭 안 된 영수증 {len(_unmatched)}건 — 일괄 검색 매칭", expanded=False):
                    st.caption("네이버 상품명/키워드로 한 번 검색 → 각 영수증마다 매칭할 상품 선택 → 일괄 적용")

                    # 후보 풀 (product_no 비어있는 user products)
                    from db import get_user_db as _gud
                    _conn_um = _gud(USERNAME)
                    _um_cand = _conn_um.execute("""
                        SELECT id, match_keyword, costco_name FROM products
                        WHERE (product_no IS NULL OR product_no = '')
                    """).fetchall()
                    _conn_um.close()
                    _um_cand = [dict(c) for c in _um_cand]

                    # 🔍 단일 일괄 검색창
                    _gcol1, _gcol2, _gcol3 = st.columns([5, 1, 1])
                    _global_q = _gcol1.text_input(
                        "네이버 상품 일괄 검색",
                        key="mm_global_q",
                        placeholder="키워드 입력 (예: 메르시 / 감자칩 / 식빵) → 모든 영수증에 같은 후보 풀 적용",
                        label_visibility="collapsed"
                    )
                    _search_btn = _gcol2.button("🔍 검색", key="mm_global_search", use_container_width=True)
                    _clear_btn  = _gcol3.button("✖ 초기화", key="mm_global_clear", use_container_width=True)

                    if _search_btn:
                        _q = (_global_q or "").strip().lower()
                        st.session_state['_mm_global_q'] = _q if _q else None
                    if _clear_btn:
                        st.session_state.pop('_mm_global_q', None)

                    _q_active = st.session_state.get('_mm_global_q')
                    if _q_active:
                        _hits = [c for c in _um_cand
                                 if _q_active in (c['match_keyword'] or '').lower()
                                 or _q_active in (c['costco_name'] or '').lower()]
                    else:
                        _hits = _um_cand  # 검색 안 했으면 전체

                    if _q_active:
                        st.caption(f"🔎 **'{_q_active}'** 검색 결과 후보 {len(_hits)}건 / 전체 {len(_um_cand)}개")
                    else:
                        st.caption(f"전체 후보 {len(_um_cand)}개 (검색 시 좁힐 수 있음)")

                    if not _hits:
                        st.warning("검색된 후보가 없습니다. 다른 키워드로 검색하거나 초기화하세요.")
                    else:
                        # 일괄 매칭 폼: 각 영수증별 dropdown으로 선택
                        st.markdown("<hr style='margin:8px 0'>", unsafe_allow_html=True)
                        _opt_labels = ["(매칭 안 함)"] + [f"{c['match_keyword'][:55]}" for c in _hits]

                        _hdr2 = st.columns([3.5, 4.5])
                        _hdr2[0].markdown("**📄 영수증 상품**")
                        _hdr2[1].markdown("**매칭할 네이버 상품**")
                        st.markdown("<hr style='margin:2px 0'>", unsafe_allow_html=True)

                        _sel_per_receipt = {}  # receipt index → (picked, name, pno, price)
                        for _ui, _ur in enumerate(_unmatched):
                            _r_name = (_ur.get('상품명') or '').strip()
                            _r_pno  = str(_ur.get('상품번호') or '').strip()
                            try:
                                _r_price = int(float(_ur.get('단가') or 0))
                            except Exception:
                                _r_price = 0
                            if not _r_name:
                                continue
                            _rc = st.columns([3.5, 4.5])
                            _rc[0].markdown(
                                f"<small><code>{_r_pno}</code><br>{_r_name[:50]}</small>",
                                unsafe_allow_html=True
                            )
                            _idx = _rc[1].selectbox(
                                f"매칭 선택 #{_ui}",
                                options=range(len(_opt_labels)),
                                format_func=lambda i: _opt_labels[i],
                                key=f"mm_bulk_sel_{_ui}",
                                label_visibility="collapsed"
                            )
                            if _idx > 0:
                                _picked = _hits[_idx - 1]
                                _sel_per_receipt[_ui] = (_picked, _r_name, _r_pno, _r_price)

                        st.markdown("<hr style='margin:8px 0'>", unsafe_allow_html=True)
                        if st.button(
                            f"💾 선택한 {len(_sel_per_receipt)}건 일괄 매칭 저장",
                            key="mm_bulk_apply",
                            type="primary",
                            disabled=not _sel_per_receipt,
                            use_container_width=True
                        ):
                            _to_apply = []
                            _used_user_ids = set()
                            for _ui, (_p, _rn, _rpno, _rprice) in _sel_per_receipt.items():
                                if _p['id'] in _used_user_ids:
                                    continue  # 같은 네이버 상품 중복 매칭 방지
                                _to_apply.append({
                                    'user_id':     _p['id'],
                                    'user_kw':     _p['match_keyword'],
                                    'receipt_name': _rn,
                                    'costco_pno':  _rpno,
                                    'unit_price':  _rprice,
                                })
                                _used_user_ids.add(_p['id'])
                            n = apply_receipt_pno_updates(USERNAME, _to_apply)
                            invalidate_data_cache()
                            st.success(f"✅ {n}건 일괄 매칭 완료! (상품번호 + 매입가)")
                            st.session_state.pop('_mm_global_q', None)
                            for _k in list(st.session_state.keys()):
                                if _k.startswith('mm_bulk_sel_'):
                                    st.session_state.pop(_k, None)
                            st.rerun()

            elif _cand_n == 0 and _auto_count == 0:
                st.info("ℹ️ 상품번호가 비어있는 네이버 등록 상품이 없습니다.")
            elif _cand_n > 0:
                st.info("ℹ️ 검토할 매칭 후보가 없습니다. (확실 매칭은 모두 자동 저장됨, 유력/참고 매칭 없음)")

            # ── 미매칭 영수증 → 주문 교차매칭 (수익계산 탭에서 embedded 시에만) ──
            _unmatched_after = _match_result.get('unmatched_receipt', []) or []
            if embedded and order_date and _unmatched_after:
                st.divider()
                st.subheader("📦 주문 교차매칭 — 미등록 신규 상품 자동 등록")
                st.caption(
                    f"네이버 상품 DB에 없는 영수증 항목 **{len(_unmatched_after)}건**을 "
                    f"**{order_date}** 주문 내역과 교차 매칭합니다. "
                    "매칭 성공 시 상품번호·매입가가 제품 DB에 등록되고 수익정산이 갱신됩니다."
                )
                if st.button("🔗 주문 교차매칭 실행", key="cross_match_orders_btn", type="primary"):
                    _cross_results = apply_receipt_to_unmatched_daily_orders(
                        USERNAME, _unmatched_after, order_date
                    )
                    _ok   = [r for r in _cross_results if r['status'] == '등록완료']
                    _skip = [r for r in _cross_results if r['status'] != '등록완료']
                    if _ok:
                        st.success(f"✅ {len(_ok)}개 신규 상품 등록 완료 (product_no + 단가 저장, 수익정산 갱신)")
                        _cross_df = pd.DataFrame([{
                            '영수증상품명': r['receipt_name'],
                            '주문상품명':   r['order_name'],
                            '상품번호':     r['product_no'],
                            '단가':         r['unit_price'],
                        } for r in _ok])
                        st.dataframe(_cross_df, use_container_width=True, hide_index=True)
                    for r in _skip:
                        st.warning(f"⚠️ {r['receipt_name'][:40]}: {r['status']}")
                    if not _ok and not _skip:
                        st.info("교차매칭 결과가 없습니다. (주문 내역에서 유사한 상품을 찾지 못했습니다)")
                    try:
                        invalidate_data_cache()
                    except Exception:
                        pass
                    st.rerun()

            # ── 가격 변동 감지 ──────────────────────────────────────
            price_changes = detect_price_changes(USERNAME, deduped)

            if price_changes:
                st.divider()
                up_cnt = sum(1 for c in price_changes if c['diff'] > 0)
                dn_cnt = sum(1 for c in price_changes if c['diff'] < 0)
                st.warning(f"⚠️ 가격 변동 감지: 🔺인상 {up_cnt}건 / 🔻인하 {dn_cnt}건")

                # 변동 내역 테이블
                def _fee_str(f):
                    return "무료" if f == 0 else f"{int(f):,}원"

                change_rows = []
                for c in price_changes:
                    arrow = "🔺" if c['diff'] > 0 else "🔻"
                    change_rows.append({
                        "": arrow,
                        "코스트코 상품명": c['costco_name'],
                        "기존 매입가": f"{c['old_cost']:,}원",
                        "새 매입가": f"{c['new_cost']:,}원",
                        "변동": f"{'+' if c['diff']>0 else ''}{c['diff']:,}원 ({'+' if c['diff']>0 else ''}{c['diff_pct']}%)",
                        "고객 배송비": _fee_str(c['shipping_fee']),
                    })
                st.dataframe(pd.DataFrame(change_rows), use_container_width=True, hide_index=True)

                # ── 카카오/텔레그램 알림 ──
                kakao_token = _gs('kakao_access_token')
                tg_token = _gs('telegram_token')
                tg_chat = _gs('telegram_chat_id')

                col_notif, col_save = st.columns([1, 1])
                if col_notif.button("📲 가격변동 알림 카톡/텔레그램 발송", key="send_price_alert", use_container_width=True):
                    alert_msg = build_price_alert_msg(price_changes)
                    sent_ok = False
                    if HAS_NAVER_API and kakao_token:
                        kakao_key = _gs('kakao_api_key')
                        kakao_refresh = _gs('kakao_refresh_token')
                        ok, kerr = naver_api.send_kakao(kakao_token, alert_msg, rest_api_key=kakao_key, refresh_token=kakao_refresh)
                        if ok:
                            sent_ok = True
                            if kerr and "__TOKEN_REFRESHED__" in str(kerr):
                                parts = str(kerr).replace("__TOKEN_REFRESHED__", "").split("||")
                                set_setting(USERNAME, 'kakao_access_token', parts[0])
                                if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                        else:
                            st.error(f"카카오 실패: {kerr}")
                    if not sent_ok and HAS_NAVER_API and tg_token and tg_chat:
                        ok, terr = naver_api.send_telegram(tg_token, tg_chat, alert_msg)
                        if ok:
                            sent_ok = True
                        else:
                            st.error(f"텔레그램 실패: {terr}")
                    if sent_ok:
                        # 알림 발송 이력 저장
                        save_price_changes_to_history(USERNAME, price_changes)
                        st.success("✅ 가격 변동 알림 발송 완료!")
                    elif not kakao_token and not tg_token:
                        st.warning("설정에서 카카오톡 또는 텔레그램을 먼저 설정해주세요.")


            else:
                st.info("✅ 가격 변동 없음 — DB에 저장된 가격과 동일합니다.")

            st.divider()
            if st.button("💾 공유 DB 저장 (전체 판매자 매입가 업데이트)", type="primary", key="save_parsed"):
                cnt = 0
                skipped = 0
                for p in deduped:
                    _rd = p.get('receipt_date', '')
                    upsert_shared_store_price(
                        costco_name=p['상품명'],
                        keyword=p['상품명'],
                        price=p['단가'],
                        product_no=p.get('상품번호', ''),
                        updated_by=USERNAME,
                        receipt_date=_rd,
                    )
                    cnt += 1
                st.session_state['_shared_cache_dirty'] = True; invalidate_data_cache()
                st.success(f"✅ {cnt}종 공유 DB 저장 완료! 모든 판매자에게 반영됩니다.")
        else:
            st.warning("업로드한 파일 모두 인식 실패. 아래에서 직접 입력해주세요.")
            for fname, emsg in fail_files:
                with st.expander(f"⚠️ {fname} — 실패 원인", expanded=True):
                    st.code(emsg, language=None)

    # 새 파일 업로드 없이 기존 로드된 영수증 항목 표시
    elif st.session_state.get('receipt_items'):
        _existing = st.session_state['receipt_items']
        _dates_ex = sorted({it.get('receipt_date', '') for it in _existing if it.get('receipt_date')})
        if _dates_ex:
            st.caption(f"📅 영수증 날짜: {', '.join(_dates_ex)}")
        st.dataframe(
            pd.DataFrame(_existing)[['상품번호', '상품명', '수량', '단가']],
            use_container_width=True, hide_index=True
        )
        if st.button("🗑 영수증 초기화", key="clear_receipt_items"):
            st.session_state['receipt_items'] = []
            st.rerun()



    # ═══════════════════════════════════════
    # 탭 3: 수익 계산
    # ═══════════════════════════════════════
