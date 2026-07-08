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

            # 영수증 → 네이버 등록상품 자동 매칭 (확실/유력 자동 저장)
            # 매칭 결과 session_state 캐시 — receipt_items 변동 시에만 재계산 (화면 속도 개선)
            _rcm_key = hash(tuple((it.get('상품번호','') or '', str(it.get('단가',0)))
                                  for it in deduped))
            _rcm_state = '_receipt_match_cache'
            if st.session_state.get(_rcm_state + '_key') == _rcm_key:
                _match_result = st.session_state[_rcm_state]
            else:
                _match_result = match_receipt_to_naver_products(USERNAME, deduped, threshold=0.30)
                _auto_certain = [m for m in _match_result['matched'] if m['tier'] in ('확실', '유력')]
                if _auto_certain:
                    apply_receipt_pno_updates(USERNAME, _auto_certain)
                    invalidate_data_cache()
                    _match_result = match_receipt_to_naver_products(USERNAME, deduped, threshold=0.30)
                st.session_state[_rcm_state] = _match_result
                st.session_state[_rcm_state + '_key'] = _rcm_key

            # ── 미매칭 영수증 → 공유DB 등록 (영수증 매칭) ──
            #  퍼지 '주문 교차매칭'(이름 유사도로 주문에 억지 링크 → 오매칭 다발)을 폐기.
            #  영수증의 코스트코번호·상품명·매입가를 공유DB에 그대로 등록 → 주문은 번호로 자동 매칭.
            _unmatched_after = _match_result.get('unmatched_receipt', []) or []
            if embedded and order_date and _unmatched_after:
                st.divider()
                st.subheader("🧾 영수증 매칭 — 미등록 신규 상품 공유DB 등록")
                st.caption(
                    f"네이버 상품 DB에 없는 영수증 항목 **{len(_unmatched_after)}건**을 "
                    "영수증의 **코스트코 상품번호·상품명·매입가** 그대로 공유DB에 등록합니다. "
                    "이름 유사도로 주문에 억지로 링크하지 않으므로 오매칭이 없고, "
                    "이후 주문은 공유DB 상품번호로 자동 매칭됩니다."
                )
                if st.button("🧾 공유DB에 영수증 등록", key="receipt_to_shared_btn", type="primary"):
                    _reg_ok, _reg_skip = 0, []
                    for _um in _unmatched_after:
                        _um_name = (_um.get('상품명') or '').strip()
                        _um_pno  = str(_um.get('상품번호') or '').strip()
                        try:
                            _um_price = int(float(_um.get('단가') or 0))
                        except Exception:
                            _um_price = 0
                        # 공유DB는 코스트코 상품번호 기준 → 번호·단가 없으면 건너뜀
                        if not _um_pno or _um_price <= 0:
                            _reg_skip.append(_um_name or _um_pno or '?')
                            continue
                        upsert_shared_store_price(
                            costco_name=_um_name, keyword=_um_name,
                            price=_um_price, product_no=_um_pno,
                            updated_by=USERNAME,
                            receipt_date=_um.get('receipt_date', ''),
                        )
                        _reg_ok += 1
                    if _reg_ok:
                        st.session_state['_shared_cache_dirty'] = True
                        try:
                            invalidate_data_cache()
                        except Exception:
                            pass
                        st.success(
                            f"✅ {_reg_ok}종 공유DB 등록 완료 (코스트코번호·상품명·매입가). "
                            "주문은 상품번호로 자동 매칭됩니다."
                        )
                    if _reg_skip:
                        st.warning(
                            f"⚠️ 상품번호/단가 없음 {len(_reg_skip)}건 건너뜀: "
                            f"{', '.join(_reg_skip[:5])}"
                        )
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
                        kakao_secret = _gs('kakao_client_secret')
                        ok, kerr = naver_api.send_kakao(kakao_token, alert_msg, rest_api_key=kakao_key, refresh_token=kakao_refresh, client_secret=kakao_secret)
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
