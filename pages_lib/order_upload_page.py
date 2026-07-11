"""📋 주문 업로드 페이지 — pages_lib 자동 추출."""
import os
import io
import sys
import json
import math
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
    get_active_orders, db_rows_to_orders_df, active_orders_to_naver_excel_df,
    save_receipt_items, get_recent_receipt_items, delete_receipt_items_by_date, get_receipt_dates,
    get_date_range_stats, get_monthly_stats, get_product_ranking, get_saved_dates,
    get_dashboard_kpi, get_daily_profit_trend, get_week_best_products,
    get_price_history_monthly, save_price_changes_to_history, get_price_change_history,
    add_keyword_tracking, get_keyword_trackings, delete_keyword_tracking,
    save_rank_result, get_rank_history, get_latest_ranks,
    get_daily_ranks_in_month, get_yearly_rank_history, delete_trackings_bulk,
    get_rank_drops,
    submit_shopping_list, get_recent_shopping_submissions, delete_shopping_submission,
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

try:
    import coupang_api
    HAS_COUPANG_API = True
except ImportError:
    HAS_COUPANG_API = False
    coupang_api = None

# 네이버 엑셀 / API 공통 컬럼 (save_daily_orders 입력 스펙)
EXTRACT_COLS = [
    '수취인명', '상품명', '옵션정보', '수량',
    '최종 상품별 총 주문금액', '배송비 합계',
    '제주/도서 추가배송비', '정산예정금액',
    '결제일',  # save_daily_orders가 행별 실제 결제일자로 분산 저장하기 위함
]

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
    """📋 주문 업로드 탭 렌더링."""
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    st.header("📋 일일 주문 수집")
    st.caption("주문을 가져온 뒤 검토하고 **💾 저장** 버튼을 눌러야 수익계산에 반영됩니다.")

    # ── (관리자 전용) 타 사용자가 제출한 장보기 목록 — 당일건만 노출 (전날건은 제외) ──
    if IS_ADMIN:
        _today_str_sub = datetime.today().strftime("%Y-%m-%d")
        _subs = [s for s in get_recent_shopping_submissions(limit=50)
                 if s.get('order_date') == _today_str_sub]
        with st.expander(f"🛒 사용자별 장보기 목록 (오늘 제출 {len(_subs)}건)", expanded=bool(_subs)):
            if not _subs:
                st.caption(f"오늘({_today_str_sub}) 제출된 장보기 목록이 없습니다. "
                           "(사용자가 주문 수집 시 자동 제출되거나 '📋 관리자에게 발송' 클릭 시 표시)")
            else:
                import html as _hl
                import streamlit.components.v1 as _cmp
                for _sub in _subs:
                    _lbl = (f"📅 {_sub['order_date']} | 👤 {_sub['username']} | "
                            f"📦 {_sub['total_items']}개 | 💰 {fmt(_sub['total_amount'])}원 | ⏰ {_sub['submitted_at']}")
                    with st.expander(_lbl, expanded=False):
                        try:
                            _its = json.loads(_sub['items_json'])
                        except Exception:
                            _its = []
                        if not _its:
                            st.warning("항목이 비어있습니다.")
                            continue
                        st.dataframe(pd.DataFrame(_its), use_container_width=True, hide_index=True)
                        _pr = []
                        for _it in _its:
                            _pno = str(_it.get('코스트코상품번호') or _it.get('상품번호') or '')
                            _nm  = _hl.escape(str(_it.get('상품명', '')))
                            _opt = _hl.escape(str(_it.get('옵션정보', '') or ''))
                            _qy  = int(_it.get('코스트코구매수량') or _it.get('주문수량') or 0)
                            _cn  = int(_it.get('주문건수') or 0)
                            _se  = int(_it.get('정산금액') or 0)
                            _sh  = int(_it.get('배송비') or 0)
                            _pr.append(
                                f'<tr><td>{_pno}</td><td>{_nm}</td><td>{_opt}</td>'
                                f'<td style="text-align:right">{_qy}개({_cn}건)</td>'
                                f'<td style="text-align:right">{fmt(_se)}</td>'
                                f'<td style="text-align:right">{fmt(_sh)}</td></tr>'
                            )
                        _ph = (
                            '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
                            f'<title>장보기 {_sub["username"]}</title><style>'
                            'body{font-family:"맑은 고딕",sans-serif;padding:24px}h1{font-size:20px;margin:0 0 4px}'
                            '.meta{color:#666;font-size:13px;margin-bottom:12px}table{width:100%;border-collapse:collapse;font-size:13px}'
                            'th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}th{background:#f4f4f4}'
                            '.tot{margin-top:16px;font-size:15px;font-weight:600}@media print{body{padding:8px}.noprint{display:none}}'
                            '</style></head><body>'
                            f'<h1>🛒 장보기 — {_hl.escape(str(_sub["username"]))} ({_sub["order_date"]})</h1>'
                            f'<div class="meta">총 {len(_its)}종 · 정산 총액 {fmt(_sub["total_amount"])}원 · 제출 {_sub["submitted_at"]}</div>'
                            '<table><thead><tr><th>상품번호</th><th>상품명</th><th>옵션</th>'
                            '<th style="text-align:right">수량</th><th style="text-align:right">정산금액</th>'
                            '<th style="text-align:right">택배비</th></tr></thead><tbody>'
                            + ''.join(_pr) +
                            f'</tbody></table><div class="tot">💰 정산 총액: {fmt(_sub["total_amount"])}원</div>'
                            '<button class="noprint" onclick="window.print()" '
                            'style="margin-top:20px;padding:10px 24px;font-size:14px;cursor:pointer">🖨 인쇄</button></body></html>'
                        )
                        _esc = _hl.escape(_ph, quote=True)
                        _x1, _x2, _x3 = st.columns([2, 2, 1])
                        try:
                            _xb = io.BytesIO()
                            with pd.ExcelWriter(_xb, engine='openpyxl') as _w:
                                pd.DataFrame(_its).to_excel(_w, index=False)
                            _x1.download_button(
                                "📥 엑셀", data=_xb.getvalue(),
                                file_name=f"shopping_{_sub['username']}_{_sub['order_date']}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key=f"ou_dl_{_sub['id']}", use_container_width=True)
                        except Exception:
                            pass
                        with _x2:
                            _cmp.html(
                                f'''<button onclick="(function(){{var f=document.getElementById('ouf_{_sub['id']}');
                                if(f&&f.contentWindow){{f.contentWindow.focus();f.contentWindow.print();}}}})()"
                                style="width:100%;padding:7px 0;background:white;border:1px solid rgba(49,51,63,0.2);
                                border-radius:8px;cursor:pointer;font-size:14px;color:rgb(49,51,63)">🖨 프린트</button>
                                <iframe id="ouf_{_sub['id']}" srcdoc="{_esc}" style="display:none"></iframe>''',
                                height=44)
                        if _x3.button("🗑", key=f"ou_del_{_sub['id']}", use_container_width=True):
                            delete_shopping_submission(_sub['id'])
                            st.rerun()
        st.divider()

    # ── API 자동 조회 ──
    if HAS_NAVER_API and api_id and api_secret:
        _nav_r1c1, _nav_r1c2, _nav_r1c3, _nav_r1c4 = st.columns([2, 1.2, 1.2, 1])
        with _nav_r1c1:
            status_options = {"배송준비 (발주확인)": "PAYED", "결제완료 (신규주문)": "PAYED", "전체 (신규+배송준비)": "ALL"}
            status_label = st.selectbox("주문 상태", list(status_options.keys()), index=0)
            status_type = status_options[status_label]
        with _nav_r1c2:
            _nav_date_from = st.date_input(
                "시작일", value=datetime.today() - timedelta(days=2),
                key="nav_date_from",
            )
        with _nav_r1c3:
            _nav_date_to = st.date_input(
                "종료일", value=datetime.today(),
                key="nav_date_to",
            )
        with _nav_r1c4:
            st.write("")
            st.write("")
            fetch_btn = st.button("🔄 네이버 주문 조회", type="primary", key="api_fetch")

        # 🚚 발송상태 동기화 — 미발송으로 잡힌 주문의 실제 네이버 상태를 받아와 갱신
        if st.button("🚚 발송상태 동기화 (이미 발송된 건 미발송에서 제외)", key="sync_ship_status",
                     help="미발송으로 표시된 주문의 실제 네이버 상태를 조회해 갱신합니다. 이미 발송/완료된 건은 목록에서 빠집니다."):
            from services import sync_active_order_status
            with st.spinner("발송상태 동기화 중... (주문 수가 많으면 시간이 걸립니다)"):
                _sync = sync_active_order_status(USERNAME, api_id, api_secret)
            if _sync.get('error'):
                st.error(f"❌ 동기화 실패: {_sync['error']}")
            else:
                st.success(f"✅ 동기화 완료 — 조회 {_sync['checked']}건 / 갱신 {_sync['updated']}건 / "
                           f"발송완료로 제외 {_sync['cleared']}건")
                for _k in ['orders', 'orders_unsaved', 'orders_api_count']:
                    st.session_state.pop(_k, None)
                if invalidate_data_cache:
                    try: invalidate_data_cache()
                    except Exception: pass
                st.rerun()

        # 선택 날짜 범위 → hours_back 변환 (안전 마진 +24h)
        from datetime import datetime as _dt
        _nav_delta_h = int((_dt.now() - _dt.combine(_nav_date_from, _dt.min.time())).total_seconds() / 3600) + 24
        hours = max(48, _nav_delta_h)
        if fetch_btn:
            all_orders = []
            types_to_query = ["READY", "PAYED"] if status_type == "ALL" else [status_type]

            with st.spinner(f"네이버 커머스 API 조회 중... ({hours}시간 범위)"):
                for st_type in types_to_query:
                    orders, err = naver_api.get_new_orders(api_id, api_secret, hours_back=hours, status_type=st_type)
                    if orders:
                        all_orders.extend(orders)
                    elif err:
                        if err.startswith("DEBUG_RESP:"):
                            st.caption(f"🔍 API 응답: {err[11:]}")
                        else:
                            st.warning(f"{st_type} 조회: {err}")

            # ── 1. API raw 주문 → DB에 UPSERT (status 누적 갱신용) ──
            api_count = 0
            fetched_df = None
            if all_orders:
                fetched_df = pd.DataFrame(all_orders).drop_duplicates(subset=['상품주문번호'], keep='last')
                api_count = len(fetched_df)
                save_order_history(USERNAME, fetched_df)

            # 동기화 시점 기록 → 다음 호출 시 증분 윈도우 계산
            try:
                set_setting(USERNAME, 'last_order_sync', _dt.now().isoformat())
            except Exception:
                pass

            # ── 1-B. raw_json 없거나 '주소 빠진' 주문 보완 (주소/연락처 복원) ──
            _no_rj_ids = [r['order_no'] for r in get_active_orders(USERNAME)
                          if not r.get('raw_json') or '통합배송지' not in (r.get('raw_json') or '')]
            if _no_rj_ids:
                with st.spinner(f"주소/연락처 보완 중... ({len(_no_rj_ids)}건)"):
                    _detail_rows, _ = naver_api.fetch_order_details_by_ids(api_id, api_secret, _no_rj_ids)
                if _detail_rows:
                    save_order_history(USERNAME, pd.DataFrame(_detail_rows))

            # ── 1-C. 이미 발송·완료된 건 자동 제외 (앱 발송 안 했어도, 네이버 직접 발송 포함) ──
            #   수집 시마다 미발송 주문의 실제 네이버 상태를 조회해 완료건을 목록에서 제거.
            try:
                from services import sync_active_order_status
                _autosync = sync_active_order_status(USERNAME, api_id, api_secret)
                if _autosync and not _autosync.get('error') and _autosync.get('cleared'):
                    st.caption(f"🔄 이미 발송·완료된 {_autosync['cleared']}건 자동 제외됨")
            except Exception:
                pass

            # ── 2. DB에서 미발송 주문(active)만 추려 화면용 df 구성 ──
            active_rows = get_active_orders(USERNAME)
            # 이번 API 응답에 있는 주문만 화면 표시 — DB 옛 stale 누적은 제외
            if all_orders:
                _api_ids = {str(o.get('상품주문번호', '')) for o in all_orders}
                active_rows = [r for r in active_rows if str(r.get('order_no', '')) in _api_ids]
            df = None  # 미발송 0건이면 else 미실행 → 아래 len(df) 대비 초기화
            if not active_rows:
                st.info(f"미발송 주문이 없습니다. (API 수집 {api_count}건)")
            else:
                df = db_rows_to_orders_df(active_rows)
                df['플랫폼'] = '🟢 네이버'
                for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
                df = df.sort_values('상품명').reset_index(drop=True)

                # 구입가격 재계산 (DB 1회 로드)
                import re as _re_ord
                _all_shared = get_shared_products()
                _all_user   = get_all_products(USERNAME)
                costs = []
                sqtys_ord = []
                for _, r in df.iterrows():
                    p_no = str(r.get('상품번호', '')) if r.get('상품번호') else ''
                    _nm_o = str(r.get('상품명', '') or '')
                    _sm_o = _re_ord.search(r'x\s*(\d+)\s*개', _nm_o, _re_ord.IGNORECASE)
                    _sf_o = (int(_sm_o.group(1)) if _sm_o else 1)
                    _sf_o = _sf_o if 1 < _sf_o <= 50 else 1
                    p = match_product_to_db(USERNAME, _nm_o, product_no=p_no,
                                            _user_prods=_all_user, _shared_prods=_all_shared)
                    if p:
                        _sq = max(1, int(p.get('split_qty', 1) or 1))
                        costs.append((p['unit_price'] // _sq) * int(r['수량']) * _sf_o)
                        sqtys_ord.append(_sq)
                    else:
                        costs.append(0)
                        sqtys_ord.append(1)
                    if p_no and p:
                        try:
                            upsert_product(USERNAME, p['costco_name'], p['match_keyword'], p['unit_price'], product_no=p_no)
                        except Exception:
                            pass  # UNIQUE 충돌 등 — 기존 매칭 유지하고 무시
                df['구입가격'] = costs
                df['소분단위'] = sqtys_ord

                # 기존 쿠팡 주문 보존 후 병합 (상품주문번호에 '-' 포함 = 쿠팡)
                _prev_orders = st.session_state.get('orders')
                if _prev_orders is not None and not _prev_orders.empty:
                    _coupang_prev = _prev_orders[
                        _prev_orders['상품주문번호'].astype(str).str.contains('-', na=False)
                    ]
                    if not _coupang_prev.empty:
                        df = pd.concat([df, _coupang_prev], ignore_index=True).sort_values('상품명').reset_index(drop=True)

                # 화면용 df 저장
                st.session_state['orders'] = df
                st.session_state['order_date'] = datetime.today().strftime("%Y-%m-%d")
                st.session_state['orders_api_count'] = api_count

                # ── 송장등록/Excel 다운로드용: DB의 raw_json에서 active 주문 복원 (72컬럼) ──
                # 화면과 동일하게 API 응답에 있는 주문만 포함 (stale 누적 제외)
                _excel_df = active_orders_to_naver_excel_df(USERNAME)
                if _excel_df is not None and not _excel_df.empty:
                    if all_orders:
                        _excel_df = _excel_df[
                            _excel_df['상품주문번호'].astype(str).isin(_api_ids)
                        ].reset_index(drop=True)
                    st.session_state['order_full'] = _excel_df if not _excel_df.empty else df.copy()
                    st.session_state['order_full_naver'] = st.session_state['order_full']
                else:
                    # raw_json이 아직 없는 옛 데이터만 있는 경우 → DB 변환 df라도 사용
                    st.session_state['order_full'] = df.copy()
                    st.session_state['order_full_naver'] = df.copy()
                # Excel bytes는 렌더 시 lazy 생성
                st.session_state['order_excel_bytes'] = None

                # 저장은 사용자가 명시적으로 저장 버튼을 눌러야 함
                st.session_state['orders_unsaved'] = True

                # 수집 직후 자동발송 예약 (본인 카톡/텔레그램 + 관리자 제출/카톡)
                # 아래 장보기 목록 블록에서 shopping df가 완성된 뒤 1회 실행
                st.session_state['_auto_shop_send'] = datetime.today().strftime("%Y-%m-%d")

                # status 분포 디버그
                try:
                    _dist = naver_api.get_last_status_dist()
                    if _dist:
                        _dist_str = ", ".join(f"{k}={v}" for k, v in sorted(_dist.items(), key=lambda x: -x[1]))
                        st.session_state['_naver_status_dist'] = _dist_str
                except Exception:
                    pass

            # ── 3. 매입가 계산만 수행 (수익계산 저장은 사용자가 명시적으로) ──
            if fetched_df is not None and not fetched_df.empty:
                _s_cost = int(_gs('shipping_cost') or 1800)
                _b_cost = int(_gs('box_cost') or 300)
                try:
                    from services import process_and_save_orders
                    _r = process_and_save_orders(
                        USERNAME, fetched_df,
                        datetime.today().strftime("%Y-%m-%d"),
                        _s_cost, _b_cost,
                        save_history=False,    # 위에서 이미 save_order_history 호출됨
                        save_daily=False,      # 사용자가 저장 버튼 누를 때만 daily_orders 저장
                    )
                    if _r.get('error_orders'):
                        st.error(f"매입가 계산 일부 실패: {_r['error_orders']}")
                except Exception as _se:
                    st.error(f"매입가 계산 실패: {_se}")

            _mi_cnt = 0 if df is None else len(df)
            st.success(f"✅ API 수집 {api_count}건 / DB 미발송 {_mi_cnt}건 표시 — 💾 저장 버튼을 눌러 수익계산에 반영하세요")
            st.rerun()
        st.divider()
    elif not HAS_NAVER_API:
        st.caption("💡 naver_api.py 파일과 bcrypt, pybase64 패키지를 설치하면 API 자동 조회를 사용할 수 있습니다.")
    elif not api_id:
        st.caption("💡 설정에서 네이버 API 키를 등록하면 자동 주문 조회를 사용할 수 있습니다.")

    # ── 쿠팡 주문 자동 조회 ─────────────────────────────────
    cq_access = _gs('coupang_access_key')
    cq_secret = _gs('coupang_secret_key')
    cq_vendor = _gs('coupang_vendor_id')

    if HAS_COUPANG_API and cq_access and cq_secret and cq_vendor:
        cq_c1, cq_c2, cq_c3, cq_c4 = st.columns([2, 1.2, 1.2, 1])
        with cq_c1:
            _cq_status_opts = {
                "신규 + 상품준비중 (전체) ⭐":  "ALL",
                "결제완료 (신규주문)":           "ACCEPT",
                "상품준비중 (발주확인 후)":      "INSTRUCT",
            }
            _cq_status_label = st.selectbox(
                "쿠팡 주문 상태", list(_cq_status_opts.keys()),
                index=0, key="cq_status_sel"
            )
            _cq_status = _cq_status_opts[_cq_status_label]
        with cq_c2:
            _cq_date_from = st.date_input(
                "시작일", value=datetime.today() - timedelta(days=30),
                key="cq_date_from",
            )
        with cq_c3:
            _cq_date_to = st.date_input(
                "종료일", value=datetime.today(),
                key="cq_date_to",
            )
        with cq_c4:
            st.write("")
            st.write("")
            cq_fetch_btn = st.button("🛒 쿠팡 주문 조회", type="primary", key="cq_fetch")

        if cq_fetch_btn:
            _cq_from_str = _cq_date_from.strftime("%Y-%m-%d")
            _cq_to_str   = _cq_date_to.strftime("%Y-%m-%d")
            with st.spinner(f"쿠팡 Wing API 조회 중... ({_cq_from_str} ~ {_cq_to_str})"):
                cq_rows, cq_err, cq_debug, cq_excel_rows = coupang_api.get_orders(
                    cq_access, cq_secret, cq_vendor,
                    status=_cq_status,
                    date_from=_cq_from_str,
                    date_to=_cq_to_str,
                )
            if cq_debug:
                st.session_state['_cq_last_debug'] = cq_debug
            if cq_err:
                st.error(f"❌ {cq_err}")
            elif not cq_rows:
                st.info("조회된 쿠팡 주문이 없습니다.")
            else:
                cq_df = pd.DataFrame(cq_rows)
                # 숫자 컬럼 정수 변환
                for _c in ['수량', '최종 상품별 총 주문금액', '배송비 합계',
                            '제주/도서 추가배송비', '정산예정금액']:
                    if _c in cq_df.columns:
                        cq_df[_c] = pd.to_numeric(cq_df[_c], errors='coerce').fillna(0).astype(int)
                cq_df = cq_df.sort_values('상품명').reset_index(drop=True)

                # 통합 진입점으로 매입가 계산만 수행 (daily_orders는 사용자 저장 시점에)
                from services import process_and_save_orders
                _s_cost = int(_gs('shipping_cost') or 1800)
                _b_cost = int(_gs('box_cost') or 300)
                _cq_result = process_and_save_orders(
                    USERNAME, cq_df,
                    datetime.today().strftime("%Y-%m-%d"),
                    _s_cost, _b_cost,
                    save_history=True,
                    save_daily=False,  # 사용자가 💾 저장 버튼 눌러야 daily_orders 반영
                )
                cq_df = _cq_result['df']  # 구입가격 채워진 df
                cq_df['플랫폼'] = '🟡 쿠팡'

                # 송장용 전체 저장
                st.session_state['order_full'] = cq_df.copy()
                # 쿠팡 Wing 배송준비 리스트 형식으로 Excel 생성
                if cq_excel_rows:
                    _cq_excel_df = pd.DataFrame(cq_excel_rows)[coupang_api._COUPANG_EXCEL_COLS]
                    _cq_xl = io.BytesIO()
                    with pd.ExcelWriter(_cq_xl, engine='openpyxl') as _w:
                        _cq_excel_df.to_excel(_w, index=False)
                    st.session_state['order_full_coupang'] = _cq_excel_df
                    st.session_state['order_excel_bytes_coupang'] = _cq_xl.getvalue()
                else:
                    st.session_state['order_full_coupang'] = cq_df.copy()
                # 택배사 등록(송장) 엑셀은 네이버/쿠팡 분리 유지 —
                # 네이버 엑셀은 order_full_naver(네이버 전용)에서 생성하므로 쿠팡 데이터로 덮지 않는다.
                # (order_excel_bytes는 네이버 다운로드 전용 → None으로 두면 네이버 전용으로 재생성)
                st.session_state['order_excel_bytes'] = None

                # 기존 네이버 주문 보존 후 병합 (상품주문번호에 '-' 없음 = 네이버)
                _prev_orders = st.session_state.get('orders')
                if _prev_orders is not None and not _prev_orders.empty:
                    _naver_prev = _prev_orders[
                        ~_prev_orders['상품주문번호'].astype(str).str.contains('-', na=False)
                    ]
                    if not _naver_prev.empty:
                        cq_df = pd.concat([_naver_prev, cq_df], ignore_index=True).sort_values('상품명').reset_index(drop=True)

                st.session_state['orders'] = cq_df
                st.session_state['order_date'] = datetime.today().strftime("%Y-%m-%d")
                st.session_state['orders_unsaved'] = True  # 저장 대기 상태

                _notes = []
                if _cq_result['history']:    _notes.append(f"이력 {_cq_result['history']}건 저장")
                if _cq_result['fee_updates']: _notes.append(f"배송비 {_cq_result['fee_updates']}건 업데이트")
                if _cq_result['sale_updates']: _notes.append(f"판매가 {_cq_result['sale_updates']}건 업데이트")
                if _notes:
                    st.caption(f"💡 제품 DB: {' / '.join(_notes)}")

                st.success(f"✅ 쿠팡 주문 {len(cq_df)}건 조회 완료!")
                st.rerun()

        # 로드된 쿠팡 주문 수 표시
        _cq_loaded = st.session_state.get('orders')
        if _cq_loaded is not None and not _cq_loaded.empty:
            _cq_cnt = int(('플랫폼' in _cq_loaded.columns and _cq_loaded['플랫폼'].str.contains('쿠팡', na=False).sum()) or 0)
            if _cq_cnt > 0:
                st.success(f"✅ 쿠팡 주문 {_cq_cnt}건 로드됨 — 아래 **📦 주문 목록** 섹션에서 확인·저장하세요")

        # 진단 정보 (rerun 후에도 유지 — session_state 기반)
        _last_dbg = st.session_state.get('_cq_last_debug')
        if _last_dbg:
            _total_found = sum(v.get("count", 0) for v in _last_dbg.values())
            with st.expander("🔍 쿠팡 API 조회 진단", expanded=(_total_found == 0)):
                for _st_k, _st_v in _last_dbg.items():
                    _cnt = _st_v.get("count", 0)
                    _err = _st_v.get("error")
                    _frm = _st_v.get("from", "")
                    _to  = _st_v.get("to", "")
                    if _err:
                        st.error(f"**{_st_k}**: ❌ {_err} ({_frm}~{_to})")
                    else:
                        st.write(f"**{_st_k}**: {_cnt}건 ({_frm} ~ {_to})")
                    _dbg = _st_v.get("debug") or {}
                    if _dbg:
                        st.caption(
                            f"응답 code={_dbg.get('code')} | "
                            f"data_type={_dbg.get('data_type')} | "
                            f"data_count={_dbg.get('data_count')} | "
                            f"items_field={_dbg.get('items_field')}"
                        )
                        if not _cnt:
                            st.code(_dbg.get("raw_snippet", ""), language="json")

        st.divider()
    elif HAS_COUPANG_API and not cq_access:
        st.caption("💡 설정에서 쿠팡 Wing API 키를 등록하면 쿠팡 주문도 자동 조회됩니다.")

    # ── 카페24 주문 자동 조회 ─────────────────────────────────
    _cf_mall = _gs('cafe24_mall_id')
    _cf_cid  = _gs('cafe24_client_id')
    _cf_tok  = _gs('cafe24_access_token')
    if _cf_mall and _cf_cid:
        if not _cf_tok:
            st.info("🛒 카페24 키는 등록됐지만 인증 전입니다. **설정 탭 > 🛒 카페24 연동 > 카페24 인증하기**를 먼저 진행하세요.")
        else:
            _cfc1, _cfc2, _cfc3 = st.columns([2.4, 2.4, 1])
            _cf_from = _cfc1.date_input("카페24 시작일", value=datetime.today() - timedelta(days=14),
                                        key="cf_date_from")
            _cf_to   = _cfc2.date_input("카페24 종료일", value=datetime.today(), key="cf_date_to")
            _cfc3.write(""); _cfc3.write("")
            _cf_fetch = _cfc3.button("🛒 카페24 주문 조회", type="primary", key="cf_fetch",
                                     use_container_width=True)
            if _cf_fetch:
                import cafe24_api
                _creds = {
                    'mall_id': _cf_mall, 'client_id': _cf_cid,
                    'client_secret': _gs('cafe24_client_secret'),
                    'access_token': _cf_tok, 'refresh_token': _gs('cafe24_refresh_token'),
                    'expires_at': _gs('cafe24_token_expires_at'),
                }
                def _cf_save_tokens(t):
                    set_setting(USERNAME, 'cafe24_access_token', t.get('access_token', ''))
                    set_setting(USERNAME, 'cafe24_refresh_token', t.get('refresh_token', ''))
                    set_setting(USERNAME, 'cafe24_token_expires_at', t.get('expires_at', ''))
                with st.spinner(f"카페24 주문 조회 중... ({_cf_from} ~ {_cf_to})"):
                    _cf_rows, _cf_err = cafe24_api.get_orders(
                        _creds, _cf_from.strftime("%Y-%m-%d"), _cf_to.strftime("%Y-%m-%d"),
                        save_tokens=_cf_save_tokens)
                if _cf_err:
                    st.error(f"❌ 카페24 조회 실패: {_cf_err}")
                elif not _cf_rows:
                    st.info("조회된 카페24 주문이 없습니다.")
                else:
                    _cf_df = pd.DataFrame(_cf_rows)
                    for _c in ['수량', '최종 상품별 총 주문금액', '배송비 합계', '정산예정금액', '상품가격']:
                        if _c in _cf_df.columns:
                            _cf_df[_c] = pd.to_numeric(_cf_df[_c], errors='coerce').fillna(0).astype(int)
                    _cf_df = _cf_df.sort_values('상품명').reset_index(drop=True)
                    from services import process_and_save_orders
                    _s_cost = int(_gs('shipping_cost') or 1800)
                    _b_cost = int(_gs('box_cost') or 300)
                    _cf_result = process_and_save_orders(
                        USERNAME, _cf_df, datetime.today().strftime("%Y-%m-%d"),
                        _s_cost, _b_cost, save_history=True, save_daily=False)
                    _cf_df = _cf_result['df']
                    _cf_df['플랫폼'] = '🔵 카페24'
                    st.session_state['order_full'] = _cf_df.copy()
                    # 기존 주문 보존 후 병합
                    _prev = st.session_state.get('orders')
                    if _prev is not None and not _prev.empty:
                        _cf_df = pd.concat([_prev, _cf_df], ignore_index=True).sort_values('상품명').reset_index(drop=True)
                    st.session_state['orders'] = _cf_df
                    st.session_state['order_date'] = datetime.today().strftime("%Y-%m-%d")
                    st.session_state['orders_unsaved'] = True
                    st.success(f"✅ 카페24 주문 {len(_cf_rows)}건 조회 완료! (아래 📦 주문 목록에서 확인·저장)")
                    st.rerun()
        st.divider()

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        uploaded = st.file_uploader("네이버 스마트스토어 발주발송관리 xlsx 파일", type=['xlsx', 'xls'], key="order_upload")
    with col2:
        order_date = st.date_input("주문 날짜", value=datetime.today())
    with col3:
        input_pw = st.text_input("엑셀 비밀번호", value=excel_pw, type="password", key="upload_pw")

    if uploaded:
        use_pw = input_pw or excel_pw
        df, err = read_excel_auto(uploaded, use_pw)
        if df is None:
            st.error(f"❌ {err}")
            if "비밀번호" in str(err):
                st.info("비밀번호를 확인하고 오른쪽 입력란에 다시 입력해주세요.")
        else:
            # 결제일은 옵셔널 (옛 엑셀 형식 호환)
            _required_cols = [c for c in EXTRACT_COLS if c != '결제일']
            missing = [c for c in _required_cols if c not in df.columns]
            if missing:
                st.error(f"필요한 컬럼이 없습니다: {missing}")
            else:
                # 송장번호 등록용 전체 데이터 저장 + Excel bytes 미리 생성
                if '상품주문번호' in df.columns:
                    st.session_state['order_full'] = df.copy()
                    st.session_state['order_full_naver'] = df.copy()
                    _ful_xl = io.BytesIO()
                    with pd.ExcelWriter(_ful_xl, engine='openpyxl') as _w:
                        df.to_excel(_w, index=False)
                    st.session_state['order_excel_bytes'] = _ful_xl.getvalue()

                # '상품번호'·'결제일' 옵션 컬럼은 있으면 보존
                _extra_cols = [c for c in ['상품번호'] if c in df.columns]
                _cols_to_use = _required_cols + (['결제일'] if '결제일' in df.columns else []) + _extra_cols
                df = df[_cols_to_use].copy()
                for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
                df = df.sort_values('상품명').reset_index(drop=True)

                # 매칭 + 매입가 계산만 (daily_orders는 사용자가 명시적으로 💾 저장 버튼 눌러야 반영)
                from services import process_and_save_orders
                _s_cost = int(_gs('shipping_cost') or 1800)
                _b_cost = int(_gs('box_cost') or 300)
                _xl_result = process_and_save_orders(
                    USERNAME, df,
                    order_date.strftime("%Y-%m-%d"),
                    _s_cost, _b_cost,
                    save_history=True,
                    save_daily=False,
                )
                df = _xl_result['df']  # 구입가격 채워진 df

                st.session_state['orders'] = df
                st.session_state['order_date'] = order_date.strftime("%Y-%m-%d")
                st.session_state['orders_unsaved'] = True  # 저장 대기

                if _xl_result.get('error_orders'):
                    st.error(f"매입가 계산 일부 실패: {_xl_result['error_orders']}")
                else:
                    st.success(f"✅ {_xl_result['orders']}건 수집 완료 — 💾 저장 버튼을 눌러 수익계산에 반영하세요")

                notes = []
                if _xl_result['history']:    notes.append(f"이력 {_xl_result['history']}건 저장")
                if _xl_result['fee_updates']: notes.append(f"배송비 {_xl_result['fee_updates']}건 업데이트")
                if _xl_result['sale_updates']: notes.append(f"판매가 {_xl_result['sale_updates']}건 업데이트")
                if notes:
                    st.caption(f"💡 제품 DB: {' / '.join(notes)}")

    # ── 세션 없으면 DB에서 미발송 주문 자동 복원 (페이지 재진입 / 서버 재시작 대비) ──
    # _orders_cleared: 지우기 버튼으로 명시적으로 초기화한 경우 — DB 재복원 건너뜀
    if st.session_state.get('orders') is None and not st.session_state.get('_orders_cleared'):
        _db_rows = get_active_orders(USERNAME)
        if _db_rows:
            _db_df = db_rows_to_orders_df(_db_rows)
            _db_df['플랫폼'] = _db_df['상품주문번호'].apply(
                lambda x: '🟡 쿠팡' if '-' in str(x) else '🟢 네이버'
            )
            for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                if c in _db_df.columns:
                    _db_df[c] = pd.to_numeric(_db_df[c], errors='coerce').fillna(0).astype(int)
            _db_df = _db_df.sort_values('상품명').reset_index(drop=True)
            st.session_state['orders']        = _db_df
            st.session_state['orders_unsaved'] = False
            st.session_state.pop('orders_api_count', None)
            if 'order_date' not in st.session_state:
                st.session_state['order_date'] = datetime.today().strftime("%Y-%m-%d")
            # 엑셀 다운로드용 full df 복원 (raw_json 기반)
            if st.session_state.get('order_full') is None:
                _xl_df = active_orders_to_naver_excel_df(USERNAME)
                if _xl_df is not None and not _xl_df.empty:
                    st.session_state['order_full'] = _xl_df
                    st.session_state['order_full_naver'] = _xl_df
                    st.session_state['order_excel_bytes'] = None  # lazy 재생성

    if st.session_state.get('orders') is not None:
        df = st.session_state['orders']
        _default_date_str = st.session_state.get('order_date', datetime.today().strftime("%Y-%m-%d"))

        _unsaved = st.session_state.get('orders_unsaved', False)
        # 메뉴 이동 시 저장 확인 팝업용 — 이 페이지의 미저장 상태 등록/해제
        _up_pages = st.session_state.setdefault('_unsaved_pages', {})
        if _unsaved:
            _up_pages['일일 주문 수집'] = "수집한 주문이 아직 저장되지 않았습니다. 저장하지 않으면 새로고침 시 사라집니다."
        else:
            _up_pages.pop('일일 주문 수집', None)
        _api_cnt = st.session_state.get('orders_api_count')
        _hdr_cnt = (f"미발송 {len(df)}건 · API 수집 {_api_cnt}건"
                    if _api_cnt is not None and _api_cnt != len(df)
                    else f"{len(df)}건")
        st.subheader(f"📦 주문 목록 ({_hdr_cnt})" + (" — 💾 저장 대기" if _unsaved else " — ✅ 저장됨"))

        # 저장 행: 날짜 선택 + 저장 버튼 + 지우기 버튼
        _sc_date, _sc_save, _sc_clear, _ = st.columns([2, 2, 1.5, 3])
        with _sc_date:
            try:
                _default_dt = datetime.strptime(_default_date_str, "%Y-%m-%d")
            except Exception:
                _default_dt = datetime.today()
            _save_date = st.date_input(
                "저장할 주문 날짜", value=_default_dt, key="save_date_input",
                help="이 날짜로 daily_orders 에 저장됩니다 (수익계산에서 이 날짜를 선택하면 불러옴)"
            )
            order_date_str = _save_date.strftime("%Y-%m-%d")
        with _sc_save:
            st.write("")
            st.write("")
            _save_clicked = st.button(
                "💾 저장하기" + (f" ({order_date_str})" if _unsaved else " (재저장)"),
                key="save_orders_btn",
                type="primary" if _unsaved else "secondary",
                use_container_width=True,
            )
        with _sc_clear:
            st.write("")
            st.write("")
            _clear_clicked = st.button("🗑 지우기", key="cancel_orders_btn", use_container_width=True)

        if _save_clicked:
            _s_cost = int(_gs('shipping_cost') or 1800)
            _b_cost = int(_gs('box_cost') or 300)
            # 수집한 미발송 주문 '전체'를 선택한 날짜에 저장 (결제일과 무관 — 오늘 발송 대상 기준).
            # save_daily_orders도 '결제일 무시하고 받은 날짜에 일괄 저장' 설계라 일관됨.
            _save_df = df
            try:
                from services import process_and_save_orders
                _r = process_and_save_orders(
                    USERNAME, _save_df, order_date_str, _s_cost, _b_cost,
                    save_history=True, save_daily=True,
                )
                if _r.get('error_orders'):
                    st.error(f"저장 실패: {_r['error_orders']}")
                else:
                    # 해당 날짜의 옛 정산저장(profit_settlements) 제거 →
                    # 수익계산이 방금 저장한 daily_orders(전체)를 그대로 불러오도록.
                    try:
                        from db import delete_profit_settlements
                        delete_profit_settlements(USERNAME, order_date_str)
                    except Exception:
                        pass
                    st.session_state['orders'] = _r['df']
                    st.session_state['order_date'] = order_date_str
                    st.session_state['orders_unsaved'] = False
                    # 💡 캐시 무효화 — 수익계산이 즉시 최신 데이터 보이도록
                    try:
                        if invalidate_data_cache:
                            invalidate_data_cache()
                    except Exception:
                        pass
                    st.success(f"✅ {order_date_str} 주문 {_r['orders']}건 저장 완료 — 수익계산에서 확인하세요")
                    st.rerun()
            except Exception as _e:
                st.error(f"저장 실패: {_e}")
        if _clear_clicked:
            for _k in ['orders', 'order_date', 'order_full', 'order_full_naver', 'order_full_coupang',
                        'order_excel_bytes', 'order_excel_bytes_coupang', 'orders_unsaved', '_naver_status_dist']:
                st.session_state.pop(_k, None)
            st.session_state['_orders_cleared'] = True  # DB 자동복원 방지
            st.rerun()

        # 디버그: API status 분포 (수량 안 맞을 때 원인 추적용)
        _dist_str = st.session_state.get('_naver_status_dist')
        if _dist_str:
            st.caption(f"🔍 네이버 API 상태 분포: {_dist_str}")

        # ── 플랫폼별 분리 ────────────────────────────────────────────
        if '소분단위' in df.columns:
            df['소분'] = df['소분단위'].apply(lambda x: f'÷{int(x)}' if pd.notna(x) and int(x) > 1 else '')
        _disp_base = ['수취인명','상품명','옵션정보','수량','최종 상품별 총 주문금액','배송비 합계','정산예정금액']
        if '소분' in df.columns:
            _disp_base = ['소분'] + _disp_base

        _naver_df = df[~df['상품주문번호'].astype(str).str.contains('-', na=False)].copy() if '상품주문번호' in df.columns else df.copy()
        _coupang_df = df[df['상품주문번호'].astype(str).str.contains('-', na=False)].copy() if '상품주문번호' in df.columns else pd.DataFrame()

        _nv_cnt = len(_naver_df)
        _cq_cnt_tab = len(_coupang_df)
        _tab_labels = [f"🟢 네이버 {_nv_cnt}건", f"🟡 쿠팡 {_cq_cnt_tab}건"]
        _tab_naver, _tab_coupang = st.tabs(_tab_labels)

        # ── 네이버 탭 ─────────────────────────────────────────────────
        with _tab_naver:
            _nv_disp_cols = [c for c in _disp_base if c in _naver_df.columns]
            _prep_naver = _naver_df[_nv_disp_cols].copy()

            # 네이버 엑셀 bytes — DB의 미발송 주문(표준 72컬럼)에서 항상 최신 생성.
            # (옛 세션 캐시/스토어 변경으로 컬럼·형식이 어긋나 '안 열리는' 문제 방지)
            _excel_bytes_nv = None
            try:
                _src_nv = active_orders_to_naver_excel_df(USERNAME)
            except Exception:
                _src_nv = None
            if _src_nv is None or getattr(_src_nv, 'empty', True):
                _src_nv = st.session_state.get('order_full_naver')
            if _src_nv is None or getattr(_src_nv, 'empty', True):
                _src_nv = st.session_state.get('order_full')
            # 네이버 전용 + 오늘 수집한 배치만 — 현재 화면 네이버 주문(상품주문번호에 '-' 없는 것)으로 제한
            # (쿠팡 주문은 '-' 포함 → 자동 제외되어 네이버 엑셀에 안 섞임)
            if (_src_nv is not None and not getattr(_src_nv, 'empty', True)
                    and '상품주문번호' in getattr(_src_nv, 'columns', [])):
                if _naver_df is not None and '상품주문번호' in getattr(_naver_df, 'columns', []):
                    _nv_ids = set(_naver_df['상품주문번호'].astype(str))
                    _src_nv = _src_nv[_src_nv['상품주문번호'].astype(str).isin(_nv_ids)].reset_index(drop=True)
                else:
                    # 네이버 표시분이 없으면 최소한 쿠팡('-')만이라도 제외
                    _src_nv = _src_nv[~_src_nv['상품주문번호'].astype(str).str.contains('-', na=False)].reset_index(drop=True)
            if _src_nv is not None and not getattr(_src_nv, 'empty', True):
                _tmp_nv = io.BytesIO()
                with pd.ExcelWriter(_tmp_nv, engine='openpyxl') as _w:
                    _src_nv.to_excel(_w, index=False)
                _excel_bytes_nv = _tmp_nv.getvalue()
                st.session_state['order_excel_bytes'] = _excel_bytes_nv

            # 인쇄용 HTML
            _prep_rows_html = []
            for _, _pr in _prep_naver.iterrows():
                _prep_rows_html.append(
                    '<tr>'
                    f'<td>{str(_pr.get("수취인명",""))}</td>'
                    f'<td>{str(_pr.get("상품명",""))}</td>'
                    f'<td>{str(_pr.get("옵션정보","") or "-")}</td>'
                    f'<td style="text-align:right">{int(_pr.get("수량",0))}</td>'
                    f'<td style="text-align:right">{fmt(int(_pr.get("최종 상품별 총 주문금액",0) or 0))}원</td>'
                    f'<td style="text-align:right;color:#555">{fmt(int(_pr.get("배송비 합계",0) or 0))}원</td>'
                    f'<td style="text-align:right;font-weight:600">{fmt(int(_pr.get("정산예정금액",0) or 0))}원</td>'
                    '</tr>'
                )
            _prep_html = (
                '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
                f'<title>배송준비건 — {order_date_str}</title>'
                '<style>'
                'body{font-family:"맑은 고딕",sans-serif;padding:24px;}'
                'h1{font-size:20px;margin:0 0 4px}'
                '.meta{color:#666;font-size:13px;margin-bottom:12px}'
                'table{width:100%;border-collapse:collapse;font-size:13px}'
                'th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}'
                'th{background:#f4f4f4;font-weight:600}'
                '@media print{body{padding:8px} .noprint{display:none}}'
                '</style></head><body>'
                f'<h1>📋 네이버 배송준비건 — {order_date_str}</h1>'
                f'<div class="meta">총 {len(_prep_naver)}건</div>'
                '<table><thead><tr>'
                '<th>수취인명</th><th>상품명</th><th>옵션정보</th>'
                '<th style="text-align:right">수량</th>'
                '<th style="text-align:right">총 주문금액</th>'
                '<th style="text-align:right">배송비</th>'
                '<th style="text-align:right">정산예정금액</th>'
                '</tr></thead><tbody>' + ''.join(_prep_rows_html) + '</tbody></table>'
                '<button class="noprint" onclick="window.print()" '
                'style="margin-top:20px;padding:10px 24px;font-size:14px;cursor:pointer">🖨 인쇄</button>'
                '</body></html>'
            )
            import html as _html_lib_prep
            import streamlit.components.v1 as _components_prep
            _escaped_prep = _html_lib_prep.escape(_prep_html, quote=True)

            _nv_b1, _nv_b2, _ = st.columns([2, 2, 4])
            if _excel_bytes_nv:
                _nv_b1.download_button(
                    label="📥 네이버 엑셀 다운로드",
                    data=_excel_bytes_nv,
                    file_name=f"naver_orders_{order_date_str}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="naver_excel_dl",
                )
            with _nv_b2:
                _components_prep.html(
                    f'''
                    <button onclick="(function(){{
                        var f=document.getElementById('pframe_prep');
                        if(f && f.contentWindow){{f.contentWindow.focus();f.contentWindow.print();}}
                    }})()" style="
                        width:100%;padding:7px 0;background:white;
                        border:1px solid rgba(49,51,63,0.2);border-radius:8px;
                        cursor:pointer;font-family:'Source Sans Pro',sans-serif;
                        font-size:14px;color:rgb(49,51,63);
                    " onmouseover="this.style.borderColor='#ff4b4b';this.style.color='#ff4b4b'"
                      onmouseout="this.style.borderColor='rgba(49,51,63,0.2)';this.style.color='rgb(49,51,63)'">
                        🖨 배송준비건 바로 인쇄
                    </button>
                    <iframe id="pframe_prep" srcdoc="{_escaped_prep}" style="display:none"></iframe>
                    ''',
                    height=44,
                )
            st.dataframe(_prep_naver, use_container_width=True, hide_index=True)

        # ── 쿠팡 탭 ─────────────────────────────────────────────────
        with _tab_coupang:
            if _coupang_df.empty:
                st.info("조회된 쿠팡 주문이 없습니다. 위 **쿠팡 주문 조회** 버튼을 눌러주세요.")
            else:
                _cq_disp_cols = [c for c in _disp_base if c in _coupang_df.columns]
                _prep_coupang = _coupang_df[_cq_disp_cols].copy()

                # 쿠팡 엑셀 bytes (수집된 원본 형식)
                _excel_bytes_cq = st.session_state.get('order_excel_bytes_coupang')
                if not _excel_bytes_cq and st.session_state.get('order_full_coupang') is not None:
                    _tmp_cq = io.BytesIO()
                    with pd.ExcelWriter(_tmp_cq, engine='openpyxl') as _w:
                        st.session_state['order_full_coupang'].to_excel(_w, index=False)
                    _excel_bytes_cq = _tmp_cq.getvalue()
                    st.session_state['order_excel_bytes_coupang'] = _excel_bytes_cq
                elif not _excel_bytes_cq:
                    # fallback: 현재 화면 데이터로 생성
                    _tmp_cq = io.BytesIO()
                    with pd.ExcelWriter(_tmp_cq, engine='openpyxl') as _w:
                        _coupang_df.to_excel(_w, index=False)
                    _excel_bytes_cq = _tmp_cq.getvalue()

                if _excel_bytes_cq:
                    st.download_button(
                        label="📥 쿠팡 엑셀 다운로드",
                        data=_excel_bytes_cq,
                        file_name=f"coupang_orders_{order_date_str}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=False,
                        key="coupang_excel_dl",
                    )
                st.dataframe(_prep_coupang, use_container_width=True, hide_index=True)

        st.subheader("🛒 코스트코 장보기 목록")
        shop_cols = ['상품번호', '상품명', '옵션정보', '수취인명', '수량', '정산예정금액', '배송비 합계']
        available_cols = [c for c in shop_cols if c in df.columns]
        shopping = df[available_cols].copy()
        shopping['옵션정보'] = shopping['옵션정보'].fillna('') if '옵션정보' in shopping.columns else ''
        if '정산예정금액' in shopping.columns:
            shopping['정산예정금액'] = pd.to_numeric(shopping['정산예정금액'], errors='coerce').fillna(0).astype(int)
        if '배송비 합계' in shopping.columns:
            shopping['배송비 합계'] = pd.to_numeric(shopping['배송비 합계'], errors='coerce').fillna(0).astype(int)

        # ── 집계: 상품번호·상품명·옵션정보가 모두 같아야 한 묶음 ──
        group_cols = [c for c in ['상품번호', '상품명', '옵션정보'] if c in shopping.columns]
        # 주문건수: 동일 상품의 고객 수 (row 수)
        _order_cnt = shopping.groupby(group_cols, sort=True, dropna=False).size().reset_index(name='주문건수')
        # 주문자(수취인) 명단 — 상품별로 주문한 사람들 (중복 제거·순서 유지). 집계 전에 산출.
        _recips = None
        if '수취인명' in shopping.columns:
            _recips = (shopping.groupby(group_cols, sort=True, dropna=False)['수취인명']
                       .apply(lambda s: ', '.join(dict.fromkeys(
                           str(x).strip() for x in s if pd.notna(x) and str(x).strip())))
                       .reset_index(name='주문자'))
        agg_map = {'수량': 'sum'}
        if '정산예정금액' in shopping.columns:
            agg_map['정산예정금액'] = 'sum'
        if '배송비 합계' in shopping.columns:
            # 배송비는 건당 (같은 상품 N건 주문해도 1건당 배송비만 표시)
            agg_map['배송비 합계'] = 'mean'
        shopping = shopping.groupby(group_cols, sort=True, dropna=False).agg(agg_map).reset_index()
        rename_cols = list(group_cols) + ['주문수량']
        if '정산예정금액' in agg_map:
            rename_cols.append('정산금액')
        if '배송비 합계' in agg_map:
            rename_cols.append('배송비')
        shopping.columns = rename_cols
        if '배송비' in shopping.columns:
            shopping['배송비'] = shopping['배송비'].round().astype(int)
        shopping = shopping.merge(_order_cnt, on=group_cols, how='left')
        if _recips is not None:
            shopping = shopping.merge(_recips, on=group_cols, how='left')

        # ── 묶음수량 추출 (옵션/상품명 기반) ──
        if not shopping.empty:
            shopping['묶음수량'] = shopping.apply(
                lambda r: extract_pack_qty(r.get('옵션정보', ''), r['상품명']), axis=1)
        else:
            shopping['묶음수량'] = 0

        # ── DB 단가 + 분리수량 조회 (DB 1회 로드) ──
        _rnd_shared = get_shared_products()
        _rnd_user   = get_all_products(USERNAME)
        db_prices, db_splits = [], []
        for _, r in shopping.iterrows():
            p = match_product_to_db(USERNAME, r['상품명'], product_no=r.get('상품번호', ''),
                                    _user_prods=_rnd_user, _shared_prods=_rnd_shared)
            if p:
                sq = max(1, int(p.get('split_qty', 1) or 1))
                db_prices.append(p['unit_price'])
                db_splits.append(sq)
            else:
                db_prices.append(None)
                db_splits.append(1)
        shopping['팩단가'] = db_prices      # 코스트코 팩 전체 가격
        shopping['분리수량'] = db_splits    # 팩 1개 → 몇 개 분리 판매

        # ── 코스트코 구매수량 계산 ──
        # 분리판매(split_qty>1): ceil(주문수량 / 분리수량) 팩
        # 묶음판매(pack_qty>1) : 주문수량 × 묶음수량 개
        # 일반            : 주문수량 개
        def _costco_qty(row):
            sq = int(row['분리수량'])
            pq = int(row['묶음수량'])
            if sq > 1:
                return math.ceil(int(row['주문수량']) / sq)
            return int(row['주문수량']) * pq
        shopping['코스트코구매수량'] = shopping.apply(_costco_qty, axis=1) if not shopping.empty else 0

        # ── 예상금액 계산 ──
        # 분리판매: 코스트코팩수 × 팩단가
        # 묶음/일반: 코스트코구매수량 × (팩단가/분리수량=1)
        def _expected_cost(row):
            if pd.isna(row['팩단가']) or not row['팩단가']:
                return None
            sq = int(row['분리수량'])
            return int(row['코스트코구매수량']) * int(row['팩단가'])
        shopping['예상금액'] = shopping.apply(_expected_cost, axis=1) if not shopping.empty else None

        # ── 표시 컬럼 구성 (요청: 수량/정산금액/택배비 중심) ──
        has_split = (shopping['분리수량'] > 1).any()
        has_multi = (shopping['묶음수량'] > 1).any()
        disp_cols = [c for c in ['상품번호', '상품명', '옵션정보'] if c in shopping.columns]
        if '주문자' in shopping.columns:
            disp_cols += ['주문자']
        disp_cols += ['주문수량']
        if '정산금액' in shopping.columns:
            disp_cols += ['정산금액']
        if '배송비' in shopping.columns:
            disp_cols += ['배송비']

        # ── HTML 테이블로 렌더링 ──
        num_cols = {'주문수량', '정산금액', '배송비'}
        # 분리/묶음 행은 배경색으로 시각 구분만 유지 (해당 컬럼은 표시 안 함)
        def _row_bg(row):
            if int(row.get('분리수량', 1)) > 1:
                return '#d6eaf8'  # 분리판매 → 하늘색
            if int(row.get('묶음수량', 1)) > 1:
                return '#fff3cd'  # 묶음판매 → 노란색
            return 'white'

        col_labels = {}

        th_cells = ''.join(
            f'<th style="background:#f8f9fa;padding:7px 12px;border-bottom:2px solid #dee2e6;'
            f'font-weight:600;white-space:nowrap;text-align:{"right" if c in num_cols else "left"}">'
            f'{col_labels.get(c, c)}</th>'
            for c in disp_cols
        )
        row_htmls = []
        # 전체 row로 iterate해서 _row_bg가 분리/묶음수량을 읽을 수 있도록 함
        for _, row in shopping.iterrows():
            bg = _row_bg(row)
            tds = []
            for c in disp_cols:
                v = row[c]
                is_num = c in num_cols
                if pd.isna(v) or v == '' or v is None:
                    display = '-'
                elif is_num:
                    try:
                        display = f'{int(v):,}'
                    except Exception:
                        display = str(v)
                else:
                    display = str(v)
                align = 'right' if is_num else 'left'
                tds.append(
                    f'<td style="background:{bg};padding:6px 12px;border-bottom:1px solid #e9ecef;'
                    f'white-space:normal;word-break:break-word;text-align:{align}">{display}</td>'
                )
            row_htmls.append(f'<tr>{"".join(tds)}</tr>')

        st.markdown(
            f'<div style="overflow-x:auto;border:1px solid #dee2e6;border-radius:4px;margin-bottom:8px">'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'<thead><tr>{th_cells}</tr></thead>'
            f'<tbody>{"".join(row_htmls)}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True
        )

        captions = []
        if has_split:
            captions.append("🔵 파란색 행 = 소분판매 (코스트코팩구매 = ceil(주문수량 ÷ 소분수량))")
        if has_multi:
            captions.append("🟡 노란색 행 = 묶음상품 (코스트코구매수량 = 주문수량 × 묶음수량)")
        for cap in captions:
            st.caption(cap)

        c1, c2 = st.columns(2)
        _total_settle = int(shopping['정산금액'].sum()) if '정산금액' in shopping.columns else 0
        c1.metric("정산 총액", f"{fmt(_total_settle)}원")
        c2.metric("종 수", f"{len(shopping)}종")

        # 휴대폰으로 장보기 목록 전송 (카카오톡 — 텔레그램은 2026-07 삭제)
        kakao_token = _gs('kakao_access_token')

        # ── 장보기 목록 items 빌더 (자동발송·관리자발송 공용) ──
        def _build_shop_items():
            _items_b = []
            _total_b = 0
            for _, _r in shopping.iterrows():
                _est = _r.get('예상금액')
                _est_v = int(_est) if pd.notna(_est) else 0
                _total_b += _est_v
                _items_b.append({
                    "코스트코상품번호": str(_r.get('코스트코상품번호') or _r.get('상품번호') or ''),
                    "상품명": str(_r.get('상품명', '')),
                    "옵션정보": str(_r.get('옵션정보', '') or ''),
                    "주문건수": int(_r.get('주문건수', 1) or 1),
                    "주문수량": int(_r.get('주문수량', 0) or 0),
                    "분리수량": int(_r.get('분리수량', 1) or 1),
                    "묶음수량": int(_r.get('묶음수량', 1) or 1),
                    "코스트코구매수량": int(_r.get('코스트코구매수량', 0) or 0),
                    "팩단가": int(_r['팩단가']) if pd.notna(_r.get('팩단가')) else 0,
                    "예상금액": _est_v,
                    "정산금액": int(_r['정산금액']) if pd.notna(_r.get('정산금액')) else 0,
                    "배송비": int(_r.get('배송비', 0) or 0),
                })
            return _items_b, _total_b

        # ── 본인 휴대폰용 카톡 메시지 빌더 (자동발송·수동발송 공용) ──
        def _build_self_msg():
            _od = datetime.strptime(order_date_str, "%Y-%m-%d")
            _lines = [f"🛒 코스트코 장보기 ({_od.strftime('%m/%d')})", ""]
            for _, _r in shopping.iterrows():
                _nm = str(_r.get('상품명', ''))[:40]
                _opt = str(_r.get('옵션정보', '') or '').strip()
                _qty = int(_r.get('코스트코구매수량', _r.get('주문수량', 0)) or 0)
                _cnt = int(_r.get('주문건수', 0) or 0)
                _settle = int(_r.get('정산금액', 0) or 0)
                _ship = int(_r.get('배송비', 0) or 0)
                _lines.append(f"• {_nm} × {_qty}개 ({_cnt}건)")
                _dd = ([f"옵션 {_opt}"] if _opt else []) + [f"정산 {fmt(_settle)}원", f"택배 {fmt(_ship)}원"]
                _lines.append("  " + " · ".join(_dd))
            _tot = int(shopping['정산금액'].sum()) if '정산금액' in shopping.columns else 0
            _lines += ["", f"💰 정산 총액: {fmt(_tot)}원 / 📦 {len(df)}건"]
            return "\n".join(_lines)

        # ── 수집 직후 자동발송 (본인 + 관리자) — 세션당 예약 1회 소비 ──
        _auto_flag = st.session_state.pop('_auto_shop_send', None)
        if _auto_flag and _auto_flag == order_date_str and not shopping.empty:
            _auto_msgs = []
            # (1) 본인 휴대폰(카톡)
            _self_msg = _build_self_msg()
            if kakao_token:
                _ok, _ke = naver_api.send_kakao(
                    kakao_token, _self_msg, rest_api_key=_gs('kakao_api_key'),
                    refresh_token=_gs('kakao_refresh_token'), client_secret=_gs('kakao_client_secret'))
                if _ok:
                    _auto_msgs.append("📱 본인 카톡")
                    if _ke and "__TOKEN_REFRESHED__" in str(_ke):
                        _pp = str(_ke).replace("__TOKEN_REFRESHED__", "").split("||")
                        set_setting(USERNAME, 'kakao_access_token', _pp[0])
                        if len(_pp) > 1: set_setting(USERNAME, 'kakao_refresh_token', _pp[1])
            # (2) 관리자 제출 — 하루 1회만 (예약/최초 수집 1회).
            #     이후 수동 재수집의 자동발송은 생략 (관리자는 최초 제출본 유지).
            if get_setting(USERNAME, 'admin_shop_sent_date') == order_date_str:
                _auto_msgs.append("📋 관리자 제출 생략(오늘 이미 발송됨)")
            else:
                _items_a, _total_a = _build_shop_items()
                try:
                    submit_shopping_list(USERNAME, order_date_str, _items_a,
                                         total_items=len(_items_a), total_amount=_total_a)
                    set_setting(USERNAME, 'admin_shop_sent_date', order_date_str)
                    _auto_msgs.append("📋 관리자 제출")
                except Exception as _ae:
                    st.caption(f"⚠️ 자동 관리자 발송 일부 실패: {_ae}")
            if _auto_msgs:
                st.success("🚀 수집 후 자동발송 완료 — " + " · ".join(_auto_msgs))

        # ── 프린트용 HTML (수량/정산금액/택배비) ───────────
        _print_rows = []
        for _, r in shopping.iterrows():
            _ship_v = int(r.get('배송비', 0) or 0)
            _settle = int(r.get('정산금액', 0) or 0)
            _print_rows.append(
                '<tr>'
                f'<td>{r.get("상품번호","")}</td>'
                f'<td>{str(r.get("상품명",""))}</td>'
                f'<td>{str(r.get("옵션정보","") or "-")}</td>'
                f'<td>{str(r.get("주문자","") or "-")}</td>'
                f'<td style="text-align:right">{int(r.get("주문수량",0))}</td>'
                f'<td style="text-align:right;font-weight:600">{fmt(_settle)}원</td>'
                f'<td style="text-align:right;color:#555">{fmt(_ship_v)}원</td>'
                '</tr>'
            )
        _total_settle_print = int(shopping['정산금액'].sum()) if '정산금액' in shopping.columns else 0
        _print_html = (
            '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
            f'<title>코스트코 장보기 — {order_date_str}</title>'
            '<style>'
            'body{font-family:"맑은 고딕",sans-serif;padding:24px;}'
            'h1{font-size:20px;margin:0 0 4px}'
            '.meta{color:#666;font-size:13px;margin-bottom:12px}'
            'table{width:100%;border-collapse:collapse;font-size:13px}'
            'th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}'
            'th{background:#f4f4f4;font-weight:600}'
            '.tot{margin-top:16px;font-size:15px;font-weight:600}'
            '@media print{body{padding:8px} .noprint{display:none}}'
            '</style></head><body>'
            f'<h1>🛒 코스트코 장보기 — {order_date_str}</h1>'
            f'<div class="meta">총 {len(shopping)}종 · 정산 총액 {fmt(_total_settle_print)}원</div>'
            '<table><thead><tr>'
            '<th>상품번호</th><th>상품명</th><th>옵션정보</th><th>주문자</th>'
            '<th style="text-align:right">수량</th>'
            '<th style="text-align:right">정산금액</th>'
            '<th style="text-align:right">택배비</th>'
            '</tr></thead><tbody>' + ''.join(_print_rows) + '</tbody></table>'
            f'<div class="tot">💰 정산 총액: {fmt(_total_settle_print)}원</div>'
            '<button class="noprint" onclick="window.print()" '
            'style="margin-top:20px;padding:10px 24px;font-size:14px;cursor:pointer">🖨 인쇄</button>'
            '</body></html>'
        )

        _ship_b1, _ship_b2, _ship_b3, _ship_b4 = st.columns(4)
        if _ship_b3.button("💾 장보기 저장", key="save_shopping_local",
                            use_container_width=True,
                            help="이 날짜의 장보기 목록을 daily_orders에 저장 (수익계산 페이지에서 불러옴)"):
            _s_cost = int(_gs('shipping_cost') or 1800)
            _b_cost = int(_gs('box_cost') or 300)
            try:
                from services import process_and_save_orders
                _save_r = process_and_save_orders(
                    USERNAME, df, order_date_str, _s_cost, _b_cost,
                    save_history=True, save_daily=True,
                )
                if _save_r.get('error_orders'):
                    st.error(f"저장 실패: {_save_r['error_orders']}")
                else:
                    # 옛 정산저장 제거 → 수익계산이 방금 저장한 전체를 불러오도록
                    try:
                        from db import delete_profit_settlements
                        delete_profit_settlements(USERNAME, order_date_str)
                    except Exception:
                        pass
                    st.session_state['orders_unsaved'] = False
                    try:
                        if invalidate_data_cache:
                            invalidate_data_cache()
                    except Exception:
                        pass
                    st.success(f"✅ {order_date_str} 장보기 {_save_r['orders']}건 저장 완료")
            except Exception as _e:
                st.error(f"저장 실패: {_e}")

        # 🖨 바로 인쇄: 숨김 iframe에 HTML 주입 후 contentWindow.print() 호출
        import html as _html_lib
        import streamlit.components.v1 as _components
        _escaped_print = _html_lib.escape(_print_html, quote=True)
        with _ship_b4:
            _components.html(
                f'''
                <button onclick="(function(){{
                    var f=document.getElementById('pframe_shop');
                    if(f && f.contentWindow){{f.contentWindow.focus();f.contentWindow.print();}}
                }})()" style="
                    width:100%;padding:7px 0;background:white;
                    border:1px solid rgba(49,51,63,0.2);border-radius:8px;
                    cursor:pointer;font-family:'Source Sans Pro',sans-serif;
                    font-size:14px;color:rgb(49,51,63);
                " onmouseover="this.style.borderColor='#ff4b4b';this.style.color='#ff4b4b'"
                  onmouseout="this.style.borderColor='rgba(49,51,63,0.2)';this.style.color='rgb(49,51,63)'">
                    🖨 바로 인쇄
                </button>
                <iframe id="pframe_shop" srcdoc="{_escaped_print}" style="display:none"></iframe>
                ''',
                height=44,
            )

        if _ship_b2.button("📋 장보기 목록 관리자에게 발송", key="send_shopping_admin",
                            use_container_width=True):
            _items = []
            _total_amount = 0
            for _, r in shopping.iterrows():
                _est = r.get('예상금액')
                _est_v = int(_est) if pd.notna(_est) else 0
                _total_amount += _est_v
                _items.append({
                    "코스트코상품번호": str(r.get('코스트코상품번호') or r.get('상품번호') or ''),
                    "상품명": str(r.get('상품명', '')),
                    "옵션정보": str(r.get('옵션정보', '') or ''),
                    "주문건수": int(r.get('주문건수', 1) or 1),
                    "주문수량": int(r.get('주문수량', 0) or 0),
                    "분리수량": int(r.get('분리수량', 1) or 1),
                    "묶음수량": int(r.get('묶음수량', 1) or 1),
                    "코스트코구매수량": int(r.get('코스트코구매수량', 0) or 0),
                    "팩단가": int(r['팩단가']) if pd.notna(r.get('팩단가')) else 0,
                    "예상금액": _est_v,
                    "정산금액": int(r['정산금액']) if pd.notna(r.get('정산금액')) else 0,
                    "배송비": int(r.get('배송비', 0) or 0),
                })
            try:
                _already = get_setting(USERNAME, 'admin_shop_sent_date') == order_date_str
                submit_shopping_list(USERNAME, order_date_str, _items,
                                     total_items=len(_items),
                                     total_amount=_total_amount)
                set_setting(USERNAME, 'admin_shop_sent_date', order_date_str)
                # 관리자 카톡 발송은 하지 않음 — 관리자 페이지 목록에서 확인
                _msg_re = " (오늘 이미 발송된 건을 수동으로 갱신)" if _already else ""
                st.success(f"✅ 관리자 페이지에 저장 완료 — {len(_items)}개 상품 ({fmt(_total_amount)}원){_msg_re}")
            except Exception as _se:
                st.error(f"❌ 전송 실패: {_se}")

        if _ship_b1.button("📱 장보기 목록 휴대폰 전송", key="send_shopping",
                            use_container_width=True):
            order_date_obj = datetime.strptime(order_date_str, "%Y-%m-%d")
            # 카톡 포맷: 2줄 카드형 — • 제품명 × 총수량(건) / 옵션 · 정산 · 택배
            lines = [f"🛒 코스트코 장보기 ({order_date_obj.strftime('%m/%d')})", ""]
            for _, r in shopping.iterrows():
                _name = str(r.get('상품명', ''))[:40]
                _opt  = str(r.get('옵션정보', '') or '').strip()
                _qty  = int(r.get('코스트코구매수량', r.get('주문수량', 0)) or 0)
                _cnt  = int(r.get('주문건수', 0) or 0)
                _settle = int(r.get('정산금액', 0) or 0)
                _ship = int(r.get('배송비', 0) or 0)
                lines.append(f"• {_name} × {_qty}개 ({_cnt}건)")
                _detail = []
                if _opt:
                    _detail.append(f"옵션 {_opt}")
                _detail.append(f"정산 {fmt(_settle)}원")
                _detail.append(f"택배 {fmt(_ship)}원")
                lines.append("  " + " · ".join(_detail))
            lines.append("")
            _total_settle_msg = int(shopping['정산금액'].sum()) if '정산금액' in shopping.columns else 0
            lines.append(f"💰 정산 총액: {fmt(_total_settle_msg)}원 / 📦 {len(df)}건")
            msg = "\n".join(lines)

            sent_ok = False
            kakao_api_key = _gs('kakao_api_key')
            kakao_refresh = _gs('kakao_refresh_token')
            kakao_secret = _gs('kakao_client_secret')

            # 카카오 '전체' 발송 — 7500자 초과 시 자동으로 줄 단위 분할해 전부 발송 → 잘림 없음.
            if kakao_token:
                ok, kerr = naver_api.send_kakao(kakao_token, msg, rest_api_key=kakao_api_key, refresh_token=kakao_refresh, client_secret=kakao_secret)
                if ok:
                    sent_ok = True
                    if kerr and "__TOKEN_REFRESHED__" in str(kerr):
                        parts = str(kerr).replace("__TOKEN_REFRESHED__", "").split("||")
                        set_setting(USERNAME, 'kakao_access_token', parts[0])
                        if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                else:
                    st.error(f"❌ 카카오톡 실패: {kerr}")

            if sent_ok:
                st.success("✅ 휴대폰으로 전송 완료!")
            elif not kakao_token:
                st.warning("💡 설정에서 카카오톡을 설정해주세요.")

    # ── 주문 이력 검색 ──────────────────────────────────────────
    st.divider()
    st.subheader("🔍 주문 이력 검색")

    with st.form("order_search_form"):
        sc1, sc2, sc3 = st.columns([2, 1, 1])
        kw_input      = sc1.text_input("수취인 / 구매자 / 주문번호", placeholder="홍길동, 주문번호 입력")
        prod_input    = sc1.text_input("상품명", placeholder="상품명 일부 입력")
        date_from_in  = sc2.date_input("시작일", value=datetime.today() - timedelta(days=30))
        date_to_in    = sc3.date_input("종료일", value=datetime.today())
        search_btn    = st.form_submit_button("🔍 검색", use_container_width=True, type="primary")

    if search_btn or st.session_state.get('order_search_triggered'):
        st.session_state['order_search_triggered'] = True
        results = search_order_history(
            USERNAME,
            keyword=kw_input,
            product_name=prod_input,
            date_from=date_from_in.strftime("%Y-%m-%d"),
            date_to=date_to_in.strftime("%Y-%m-%d"),
        )
        if results:
            rdf = pd.DataFrame(results)
            show_cols = {
                'order_date': '주문일', 'recipient': '수취인', 'buyer': '구매자',
                'product_name': '상품명', 'option_info': '옵션',
                'qty': '수량', 'unit_price': '판매단가', 'shipping_fee': '배송비',
                'order_amount': '주문금액', 'settlement': '정산예정',
                'status': '주문상태', 'tracking_no': '송장번호',
                'cost_price': '구입가', 'profit': '수익',
            }
            disp = rdf[[c for c in show_cols if c in rdf.columns]].rename(columns=show_cols)
            for col in ['판매단가', '배송비', '주문금액', '정산예정', '구입가', '수익']:
                if col in disp.columns:
                    disp[col] = disp[col].apply(lambda x: f"{int(x):,}" if pd.notna(x) and x != 0 else ("-" if x == 0 else ""))
            st.caption(f"검색 결과 {len(results)}건")
            st.dataframe(disp, use_container_width=True, hide_index=True)

            # 다운로드
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine='openpyxl') as w:
                rdf.to_excel(w, index=False, sheet_name='주문이력')
            out.seek(0)
            st.download_button(
                "📥 검색 결과 엑셀 다운로드",
                data=out, file_name=f"order_history_{date_from_in}_{date_to_in}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("조건에 맞는 주문이 없습니다.")


    # ═══════════════════════════════════════
    # 탭 1.5: 송장번호 등록
    # ═══════════════════════════════════════
