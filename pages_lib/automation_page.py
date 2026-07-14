"""🤖 자동화 페이지 — pages_lib 자동 추출."""
import os
import io
import sys
import json
import platform
import subprocess
import sqlite3
from datetime import datetime, timedelta

_IS_WIN = platform.system() == "Windows"

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
    """🤖 자동화 탭 렌더링."""
    import re
    from datetime import time as dtime
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.join(BASE_DIR, "data")

    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    st.header("🤖 자동화 설정")
    st.caption(
        ("Windows 작업 스케줄러" if _IS_WIN else "Linux cron")
        + " 을 통해 매일 지정된 시간에 자동 실행됩니다."
    )

    SCRIPT_PATH = os.path.join(BASE_DIR, "auto_task.py")
    PYTHON_PATH = sys.executable

    def _cron_get():
        try:
            r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""

    def _cron_set(content):
        try:
            r = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as e:
            return False, str(e)

    def _schtasks_args_to_cron(args_list):
        """schtasks args(/create /delete /query) → crontab 명령으로 변환."""
        if not args_list:
            return False, "empty args"
        action = args_list[0].lower()
        opts = {}
        i = 1
        while i < len(args_list):
            a = args_list[i]
            if a.startswith("/") and i + 1 < len(args_list) and not args_list[i + 1].startswith("/"):
                opts[a.lower()] = args_list[i + 1]
                i += 2
            else:
                i += 1
        name = opts.get("/tn", "")
        if not name:
            return False, "task name required"
        marker = f"# COSTCO_TASK:{name}"
        if action == "/create":
            import shlex as _shlex
            raw_cmd = opts.get("/tr", "")
            try:
                cmd_str = " ".join(_shlex.split(raw_cmd))
            except Exception:
                cmd_str = raw_cmd.replace('"', '')
            time_str = opts.get("/st", "00:00")
            try:
                hh, mm = time_str.split(":")
            except ValueError:
                return False, f"invalid time: {time_str}"
            cron_line = f"{int(mm)} {int(hh)} * * * {cmd_str} {marker}"
            cur = _cron_get()
            kept = [ln for ln in cur.splitlines() if marker not in ln and ln.strip()]
            new = "\n".join(kept + [cron_line]) + "\n"
            return _cron_set(new)
        if action == "/delete":
            cur = _cron_get()
            kept = [ln for ln in cur.splitlines() if marker not in ln and ln.strip()]
            new = ("\n".join(kept) + "\n") if kept else ""
            return _cron_set(new)
        if action == "/query":
            matching = [ln for ln in _cron_get().splitlines() if marker in ln]
            if matching:
                return True, "\n".join(matching)
            return False, ""
        return False, f"unsupported action: {action}"

    def _schtasks_run(args_list):
        if not _IS_WIN:
            return _schtasks_args_to_cron(args_list)
        try:
            r = subprocess.run(
                ["schtasks"] + args_list,
                capture_output=True, text=True, encoding="cp949", errors="replace"
            )
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as e:
            return False, str(e)

    def _register_task(task_name, task_type, time_str, user):
        cmd = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task {task_type} --user {user}'
        return _schtasks_run([
            "/create", "/tn", task_name, "/tr", cmd,
            "/sc", "daily", "/st", time_str, "/f",
        ])

    def _delete_task(task_name):
        return _schtasks_run(["/delete", "/tn", task_name, "/f"])

    def _query_task(task_name):
        return _schtasks_run(["/query", "/tn", task_name, "/fo", "LIST"])

    TASK1_NAME = f"CostcoHotdeal_Shopping_{USERNAME}"
    TASK2_NAME = f"CostcoHotdeal_Shipping_{USERNAME}"
    TASK3_NAME = "CostcoHotdeal_Crawl"

    TASK4_NAME = f"CostcoRank_{USERNAME}"
    TASK5_NAME_STATUS = f"CostcoOrders_{USERNAME}"

    # ── 현재 스케줄 상태 ──
    with st.expander("📌 현재 등록된 작업 스케줄러 상태", expanded=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        t1_ok, t1_out = _query_task(TASK1_NAME)
        t2_ok, t2_out = _query_task(TASK2_NAME)
        t3_ok, t3_out = _query_task(TASK3_NAME)
        t4_ok, t4_out = _query_task(TASK4_NAME)
        t5_ok, t5_out = _query_task(TASK5_NAME_STATUS)
        with c1:
            if t1_ok:
                st.success("✅ Task 1 (장보기) 등록됨")
                st.code(t1_out[:400], language=None)
            else:
                st.warning("⚠️ Task 1 미등록")
        with c2:
            if t2_ok:
                st.success("✅ Task 2 (발송처리) 등록됨")
                st.code(t2_out[:400], language=None)
            else:
                st.warning("⚠️ Task 2 미등록")
        with c3:
            if t3_ok:
                st.success("✅ Task 3 (크롤링) 등록됨")
                st.code(t3_out[:400], language=None)
            else:
                st.warning("⚠️ Task 3 미등록")
        with c4:
            if t4_ok:
                st.success("✅ Task 4 (순위체크) 등록됨")
                st.code(t4_out[:400], language=None)
            else:
                st.warning("⚠️ Task 4 미등록")
        with c5:
            if t5_ok:
                st.success("✅ Task 5 (주문수집) 등록됨")
                st.code(t5_out[:400], language=None)
            else:
                st.warning("⚠️ Task 5 미등록")

        # ── 등록 실패 진단 ──
        if not _IS_WIN:
            with st.expander("🔍 cron 진단 (등록 실패 시 확인)", expanded=False):
                import pwd as _pwd, os as _os
                try:
                    _proc_user = _pwd.getpwuid(_os.getuid()).pw_name
                except Exception:
                    _proc_user = str(_os.getuid())
                _diag = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
                st.caption(f"**프로세스 실행 유저: `{_proc_user}`**")
                st.caption(f"Python: `{PYTHON_PATH}`")
                st.caption(f"Script: `{SCRIPT_PATH}`")
                st.caption(f"crontab -l returncode: {_diag.returncode}")
                st.code(_diag.stdout or _diag.stderr or "(비어있음)", language=None)
                _sample = f'0 12 * * * {PYTHON_PATH} {SCRIPT_PATH} --task orders --user {USERNAME}'
                st.caption("등록될 cron line 형식:")
                st.code(_sample, language=None)

    st.divider()

    # ── Task 1: 장보기 목록 발송 ──
    st.subheader("📋 Task 1 — 장보기 목록 카카오 발송")
    st.caption("매일 지정 시간에 배송준비 주문을 조회하고 장보기 목록을 카카오톡으로 전송합니다.")

    task1_en = _gs('auto_shopping_enabled') == '1'
    task1_time_str = _gs('auto_shopping_time') or '09:00'
    t1h, t1m = [int(x) for x in task1_time_str.split(':')]

    c1, c2 = st.columns([1, 2])
    new_t1_en = c1.checkbox("활성화", value=task1_en, key="t1_en")
    new_t1_time = c2.time_input("실행 시간", value=dtime(t1h, t1m), key="t1_time")

    col_s1, col_d1, col_run1 = st.columns(3)
    if col_s1.button("💾 Task 1 저장 & 등록", key="save_t1", type="primary", use_container_width=True):
        t1_str = new_t1_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_shopping_enabled', '1' if new_t1_en else '0')
        set_setting(USERNAME, 'auto_shopping_time', t1_str)
        if new_t1_en:
            ok, out = _register_task(TASK1_NAME, "shopping", t1_str, USERNAME)
            if ok:
                st.success(f"✅ Task 1 등록 완료 — 매일 {t1_str} 자동 실행")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
        else:
            _delete_task(TASK1_NAME)
            st.info("Task 1 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if col_d1.button("🗑 Task 1 삭제", key="del_t1", use_container_width=True):
        ok, out = _delete_task(TASK1_NAME)
        set_setting(USERNAME, 'auto_shopping_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if col_run1.button("▶ 지금 테스트 실행", key="run_t1", use_container_width=True):
        with st.spinner("Task 1 실행 중..."):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "shopping", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120
            )
        output = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            st.success("✅ 실행 완료")
        else:
            st.error("❌ 실행 중 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── Task 2: 자동 발송처리 ──
    st.subheader("🚀 Task 2 — CJ 접수 + 네이버 일괄 발송처리")
    st.caption("매일 지정 시간에 배송준비 주문을 CJ 택배에 접수하고 네이버 스마트스토어에 자동 발송처리합니다.")

    cj_id_check = _gs('cj_api_id')
    if not cj_id_check:
        st.warning("⚠️ CJ API 미설정 — 설정 탭 > 택배사 설정에서 CJ ID/PW/고객번호를 먼저 입력하세요.")

    task2_en = _gs('auto_shipping_enabled') == '1'
    task2_time_str = _gs('auto_shipping_time') or '14:00'
    t2h, t2m = [int(x) for x in task2_time_str.split(':')]

    c1, c2 = st.columns([1, 2])
    new_t2_en = c1.checkbox("활성화", value=task2_en, key="t2_en")
    new_t2_time = c2.time_input("실행 시간", value=dtime(t2h, t2m), key="t2_time")

    col_s2, col_d2, col_run2 = st.columns(3)
    if col_s2.button("💾 Task 2 저장 & 등록", key="save_t2", type="primary", use_container_width=True):
        t2_str = new_t2_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_shipping_enabled', '1' if new_t2_en else '0')
        set_setting(USERNAME, 'auto_shipping_time', t2_str)
        if new_t2_en:
            ok, out = _register_task(TASK2_NAME, "shipping", t2_str, USERNAME)
            if ok:
                st.success(f"✅ Task 2 등록 완료 — 매일 {t2_str} 자동 실행")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
        else:
            _delete_task(TASK2_NAME)
            st.info("Task 2 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if col_d2.button("🗑 Task 2 삭제", key="del_t2", use_container_width=True):
        ok, out = _delete_task(TASK2_NAME)
        set_setting(USERNAME, 'auto_shipping_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if col_run2.button("▶ 지금 테스트 실행", key="run_t2", use_container_width=True):
        with st.spinner("Task 2 실행 중 (CJ 접수 + 발송처리)..."):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "shipping", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=180
            )
        output = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            st.success("✅ 실행 완료")
        else:
            st.error("❌ 실행 중 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── Task 3: 정기 크롤링 (admin 전용) ──
    if IS_ADMIN:
        st.subheader("🕐 Task 3 — 코스트코 정기 크롤링")
        st.caption("매일 지정 시간에 코스트코 상품을 자동 크롤링하여 공유 제품 DB를 최신 상태로 유지합니다.")

        _CRAWL_PRESETS = {
            "🔄 정기갱신": ["신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품"],
            "🔥 핫딜시즌": ["스페셜할인", "커클랜드", "신상품"],
            "🆕 새상품탐색": ["신상품", "스페셜할인"],
            "🏗️ 전체카테고리": ["식품", "신선식품", "냉동식품", "과자/간식", "커피/음료",
                                "가공식품", "생활용품", "세제/청소", "화장지", "가전/디지털",
                                "주방가전", "뷰티/화장품", "건강/영양제", "의류/패션",
                                "완구", "반려동물", "자동차용품"],
        }

        task3_en = _gs('auto_crawl_enabled') == '1'
        task3_time_str = _gs('auto_crawl_time') or '06:00'
        t3h, t3m = [int(x) for x in task3_time_str.split(':')]
        _saved_cats_json = _gs('auto_crawl_categories') or '[]'
        try:
            _saved_cats = json.loads(_saved_cats_json)
        except Exception:
            _saved_cats = []
        _saved_max = int(_gs('auto_crawl_max') or 200)

        cr1, cr2 = st.columns([1, 2])
        new_t3_en   = cr1.checkbox("활성화", value=task3_en, key="t3_en")
        new_t3_time = cr2.time_input("실행 시간", value=dtime(t3h, t3m), key="t3_time")

        st.markdown("**크롤링 카테고리 선택**")
        _preset_cols = st.columns(4)
        for _pi, (_plabel, _pcats) in enumerate(_CRAWL_PRESETS.items()):
            if _preset_cols[_pi].button(_plabel, key=f"t3_preset_{_pi}", use_container_width=True):
                _saved_cats = list(set(_saved_cats) | set(_pcats))

        from costco_crawler import CATEGORIES as _ALL_CATS
        _cat_names = [c for c in _ALL_CATS if c not in ("전체",)]
        _sel_cats = st.multiselect("크롤링 대상 카테고리",
                                   options=_cat_names,
                                   default=[c for c in _saved_cats if c in _cat_names],
                                   key="t3_cats")
        _new_max = st.number_input("카테고리당 최대 수집 수", value=_saved_max,
                                   min_value=50, max_value=500, step=50, key="t3_max")

        col_s3, col_d3, col_run3 = st.columns(3)
        if col_s3.button("💾 Task 3 저장 & 등록", key="save_t3", type="primary", use_container_width=True):
            t3_str = new_t3_time.strftime("%H:%M")
            set_setting(USERNAME, 'auto_crawl_enabled', '1' if new_t3_en else '0')
            set_setting(USERNAME, 'auto_crawl_time', t3_str)
            set_setting(USERNAME, 'auto_crawl_categories', json.dumps(_sel_cats, ensure_ascii=False))
            set_setting(USERNAME, 'auto_crawl_max', str(int(_new_max)))
            if new_t3_en:
                _cmd3 = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task crawl --user {USERNAME}'
                ok, out = _schtasks_run(["/create", "/tn", TASK3_NAME, "/tr", _cmd3,
                                         "/sc", "daily", "/st", t3_str, "/f"])
                if ok:
                    st.success(f"✅ Task 3 등록 완료 — 매일 {t3_str} 자동 크롤링")
                else:
                    st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
            else:
                _schtasks_run(["/delete", "/tn", TASK3_NAME, "/f"])
                st.info("Task 3 비활성화 — 스케줄 삭제됨")
            st.rerun()

        if col_d3.button("🗑 Task 3 삭제", key="del_t3", use_container_width=True):
            ok, out = _schtasks_run(["/delete", "/tn", TASK3_NAME, "/f"])
            set_setting(USERNAME, 'auto_crawl_enabled', '0')
            st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
            st.rerun()

        if col_run3.button("▶ 지금 테스트 실행", key="run_t3", use_container_width=True):
            if not _sel_cats:
                st.warning("카테고리를 선택하세요.")
            else:
                set_setting(USERNAME, 'auto_crawl_categories',
                            json.dumps(_sel_cats, ensure_ascii=False))
                with st.spinner(f"크롤링 실행 중 ({len(_sel_cats)}개 카테고리)... 수 분 소요"):
                    r = subprocess.run(
                        [PYTHON_PATH, SCRIPT_PATH, "--task", "crawl", "--user", USERNAME],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=600
                    )
                output = (r.stdout + r.stderr).strip()
                if r.returncode == 0:
                    st.success("✅ 크롤링 완료")
                else:
                    st.error("❌ 크롤링 오류")
                st.code(output, language=None)

        st.divider()

    # ── Task 4: 키워드 순위 체크 (일 1회) ──
    st.subheader("📈 Task 4 — 키워드 순위 자동 체크")
    st.caption("매일 지정 시간에 네이버 쇼핑 검색 결과에서 우리 상품 순위를 자동으로 기록합니다.")

    task4_en = _gs('auto_rank_enabled') == '1'
    task4_time_str = _gs('auto_rank_time') or '12:00'
    t4h, t4m = [int(x) for x in task4_time_str.split(':')]

    c1, c2 = st.columns([1, 2])
    new_t4_en = c1.checkbox("활성화", value=task4_en, key="t4_en")
    new_t4_time = c2.time_input("실행 시간", value=dtime(t4h, t4m), key="t4_time")

    _t4_c1, _t4_c2, _t4_c3 = st.columns(3)
    if _t4_c1.button("💾 Task 4 저장 & 등록", key="save_t4", type="primary", use_container_width=True):
        t4_str = new_t4_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_rank_enabled', '1' if new_t4_en else '0')
        set_setting(USERNAME, 'auto_rank_time', t4_str)
        if new_t4_en:
            _cmd4 = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task rank --user {USERNAME}'
            ok, out = _schtasks_run(["/create", "/tn", TASK4_NAME, "/tr", _cmd4,
                                     "/sc", "daily", "/st", t4_str, "/f"])
            if ok:
                st.success(f"✅ Task 4 등록 완료 — 매일 {t4_str} 순위 체크")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
        else:
            _delete_task(TASK4_NAME)
            st.info("Task 4 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if _t4_c2.button("🗑 Task 4 삭제", key="del_t4", use_container_width=True):
        ok, out = _delete_task(TASK4_NAME)
        set_setting(USERNAME, 'auto_rank_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if _t4_c3.button("▶ 지금 순위 체크", key="run_t4", use_container_width=True):
        open_cid  = _gs('naver_open_client_id')
        open_csec = _gs('naver_open_client_secret')
        if not open_cid or not open_csec:
            st.warning("설정 탭에서 네이버 Open API 키를 먼저 등록해주세요.")
        else:
            with st.spinner("순위 체크 중..."):
                r = subprocess.run(
                    [PYTHON_PATH, SCRIPT_PATH, "--task", "rank", "--user", USERNAME],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
                )
            output = (r.stdout + r.stderr).strip()
            if r.returncode == 0:
                st.success("✅ 순위 체크 완료")
            else:
                st.error("❌ 오류 발생")
            st.code(output, language=None)

    st.divider()

    # ── Task 5: 주문 자동 수집 ──
    st.subheader("📥 Task 5 — 네이버/쿠팡 주문 자동 수집")
    st.caption("매일 지정 시간에 네이버 스마트스토어와 쿠팡 주문을 자동 조회하여 DB에 저장합니다.")

    TASK5_NAME = f"CostcoOrders_{USERNAME}"

    task5_en = _gs('auto_orders_enabled') == '1'
    task5_time_str = _gs('auto_orders_time') or '08:00'
    t5h, t5m = [int(x) for x in task5_time_str.split(':')]

    _t5_c1, _t5_c2 = st.columns([1, 2])
    new_t5_en   = _t5_c1.checkbox("활성화", value=task5_en, key="t5_en")
    new_t5_time = _t5_c2.time_input("실행 시간", value=dtime(t5h, t5m), key="t5_time")
    st.caption("자동 수집 기간: 네이버 48시간 / 쿠팡 7일 고정. 수동 수집은 일일주문 탭에서 기간 선택.")

    _naver_ok   = bool(_gs('api_client_id'))
    _coupang_ok = bool(_gs('coupang_access_key'))
    if not _naver_ok and not _coupang_ok:
        st.warning("⚠️ 네이버 API 또는 쿠팡 API 키를 설정 탭에서 먼저 입력해주세요.")

    _s5c1, _s5c2, _s5c3 = st.columns(3)
    if _s5c1.button("💾 Task 5 저장 & 등록", key="save_t5", type="primary", use_container_width=True):
        t5_str = new_t5_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_orders_enabled', '1' if new_t5_en else '0')
        set_setting(USERNAME, 'auto_orders_time', t5_str)
        if new_t5_en:
            _cmd5 = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task orders --user {USERNAME}'
            ok, out = _schtasks_run(["/create", "/tn", TASK5_NAME, "/tr", _cmd5,
                                     "/sc", "daily", "/st", t5_str, "/f"])
            if ok:
                st.success(f"✅ Task 5 등록 완료 — 매일 {t5_str} 주문 자동 수집")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
        else:
            _schtasks_run(["/delete", "/tn", TASK5_NAME, "/f"])
            st.info("Task 5 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if _s5c2.button("🗑 Task 5 삭제", key="del_t5", use_container_width=True):
        ok, out = _schtasks_run(["/delete", "/tn", TASK5_NAME, "/f"])
        set_setting(USERNAME, 'auto_orders_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if _s5c3.button("▶ 지금 테스트 실행", key="run_t5", use_container_width=True):
        with st.spinner("주문 수집 중..."):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "orders", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
            )
        output = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            st.success("✅ 주문 수집 완료")
        else:
            st.error("❌ 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── Task 6: 네이버 등록상품 정기수집 ──
    st.subheader("🛍 Task 6 — 네이버 등록상품 정기수집")
    st.caption("매일 지정 시간에 스마트스토어 등록상품 전체를 API로 가져와 제품DB(origin+channel 번호·판매가)를 자동 갱신합니다. → 주문 매칭 정확도 유지.")

    TASK6_NAME = f"CostcoProducts_{USERNAME}"
    t6_ok, t6_out = _query_task(TASK6_NAME)
    if t6_ok:
        st.success("✅ Task 6 (네이버 상품 정기수집) 등록됨")
        st.code(t6_out[:300], language=None)

    task6_en = _gs('auto_products_enabled') == '1'
    task6_time_str = _gs('auto_products_time') or '05:00'
    t6h, t6m = [int(x) for x in task6_time_str.split(':')]

    _t6_c1, _t6_c2 = st.columns([1, 2])
    new_t6_en   = _t6_c1.checkbox("활성화", value=task6_en, key="t6_en")
    new_t6_time = _t6_c2.time_input("실행 시간", value=dtime(t6h, t6m), key="t6_time")

    if not bool(_gs('api_client_id')):
        st.warning("⚠️ 네이버 API 키를 설정 탭에서 먼저 입력해주세요.")

    _s6c1, _s6c2, _s6c3 = st.columns(3)
    if _s6c1.button("💾 Task 6 저장 & 등록", key="save_t6", type="primary", use_container_width=True):
        t6_str = new_t6_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_products_enabled', '1' if new_t6_en else '0')
        set_setting(USERNAME, 'auto_products_time', t6_str)
        if new_t6_en:
            _cmd6 = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task products --user {USERNAME}'
            ok, out = _schtasks_run(["/create", "/tn", TASK6_NAME, "/tr", _cmd6,
                                     "/sc", "daily", "/st", t6_str, "/f"])
            if ok:
                st.success(f"✅ Task 6 등록 완료 — 매일 {t6_str} 네이버 상품 정기수집")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한 필요)\n{out}")
        else:
            _schtasks_run(["/delete", "/tn", TASK6_NAME, "/f"])
            st.info("Task 6 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if _s6c2.button("🗑 Task 6 삭제", key="del_t6", use_container_width=True):
        ok, out = _schtasks_run(["/delete", "/tn", TASK6_NAME, "/f"])
        set_setting(USERNAME, 'auto_products_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if _s6c3.button("▶ 지금 테스트 실행", key="run_t6", use_container_width=True):
        with st.spinner("네이버 등록상품 수집 중..."):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "products", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180
            )
        output = (r.stdout + r.stderr).strip()
        st.success("✅ 수집 완료") if r.returncode == 0 else st.error("❌ 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── Task 7: 코스트코 수집상품 → 네이버 자동 등록 ──
    st.subheader("🛍 Task 7 — 코스트코 수집상품 네이버 자동 등록")
    st.caption("크롤링한 코스트코 상품 중 미등록 상품을 매일 자동으로 스마트스토어에 등록합니다. "
               "AI가 카테고리를 자동 판단하고, 판단하지 못한 건은 '네이버 등록' 탭 장바구니로 남겨 수동 확인합니다. "
               "⚠️ 실제 스토어에 상품이 올라가므로 마진율·회당 최대 수를 확인하고 사용하세요.")

    TASK7_NAME = f"CostcoAutoRegister_{USERNAME}"
    t7_ok, t7_out = _query_task(TASK7_NAME)
    if t7_ok:
        st.success("✅ Task 7 (네이버 자동 등록) 등록됨")
        st.code(t7_out[:300], language=None)

    task7_en = _gs('auto_register_enabled') == '1'
    task7_time_str = _gs('auto_register_time') or '07:00'
    t7h, t7m = [int(x) for x in task7_time_str.split(':')]

    _t7_c1, _t7_c2, _t7_c3, _t7_c4 = st.columns([1, 2, 1, 1])
    new_t7_en     = _t7_c1.checkbox("활성화", value=task7_en, key="t7_en")
    new_t7_time   = _t7_c2.time_input("실행 시간", value=dtime(t7h, t7m), key="t7_time")
    new_t7_margin = _t7_c3.number_input(
        "마진율 %", min_value=0, max_value=300, step=5,
        value=int(_gs('auto_register_margin') or _gs('cafe24_naver_margin') or 10),
        key="t7_margin")
    new_t7_max    = _t7_c4.number_input(
        "회당 최대", min_value=1, max_value=100, step=1,
        value=int(_gs('auto_register_max') or 20), key="t7_max")
    st.caption("판매가 = 코스트코가 ×(1+마진%) ÷0.945(수수료 5.5%). "
               "회당 최대 = 1회 실행당 등록 상한(AI 호출·등록 폭주 방지). 등록된 상품은 다음 실행 시 자동 제외됩니다.")

    _need7 = []
    if not bool(_gs('api_client_id')):
        _need7.append("네이버 커머스 API")
    if not (bool(_gs('naver_open_client_id')) and bool(_gs('naver_open_client_secret'))):
        _need7.append("네이버 Open API(쇼핑검색·AI카테고리)")
    if _need7:
        st.warning("⚠️ " + " / ".join(_need7) + " 키를 설정 탭에서 입력해야 자동 카테고리 판단이 됩니다. "
                   "(없으면 카테고리 매핑/저장값 있는 건만 등록되고 나머지는 장바구니로 남습니다.)")

    _s7c1, _s7c2, _s7c3 = st.columns(3)
    if _s7c1.button("💾 Task 7 저장 & 등록", key="save_t7", type="primary", use_container_width=True):
        t7_str = new_t7_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_register_enabled', '1' if new_t7_en else '0')
        set_setting(USERNAME, 'auto_register_time', t7_str)
        set_setting(USERNAME, 'auto_register_margin', str(int(new_t7_margin)))
        set_setting(USERNAME, 'auto_register_max', str(int(new_t7_max)))
        if new_t7_en:
            _cmd7 = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task register --user {USERNAME}'
            ok, out = _schtasks_run(["/create", "/tn", TASK7_NAME, "/tr", _cmd7,
                                     "/sc", "daily", "/st", t7_str, "/f"])
            if ok:
                st.success(f"✅ Task 7 등록 완료 — 매일 {t7_str} 네이버 자동 등록")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한 필요)\n{out}")
        else:
            _schtasks_run(["/delete", "/tn", TASK7_NAME, "/f"])
            st.info("Task 7 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if _s7c2.button("🗑 Task 7 삭제", key="del_t7", use_container_width=True):
        ok, out = _schtasks_run(["/delete", "/tn", TASK7_NAME, "/f"])
        set_setting(USERNAME, 'auto_register_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if _s7c3.button("▶ 지금 테스트 실행", key="run_t7", use_container_width=True):
        st.caption("※ '활성화' 저장 후 실행해야 실제 등록됩니다 (미활성 시 로그에 '건너뜀' 표시).")
        with st.spinner("네이버 자동 등록 실행 중... (상품 수·AI 호출에 따라 수 분 소요될 수 있음)"):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "register", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800
            )
        output = (r.stdout + r.stderr).strip()
        st.success("✅ 실행 완료") if r.returncode == 0 else st.error("❌ 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── 실행 로그 ──
    st.subheader("📄 자동화 실행 로그")
    # 관리자는 전체 로그, 일반 사용자는 본인 로그만 조회 (타 사용자 로그 노출 방지)
    if IS_ADMIN:
        LOG_PATH = os.path.join(DATA_DIR, "auto_task.log")
        st.caption("👑 관리자 — 전체 사용자 로그")
    else:
        LOG_PATH = os.path.join(DATA_DIR, "user_logs", f"auto_task_{USERNAME}.log")
        st.caption("👤 내 자동화 로그만 표시됩니다.")
    col_log1, col_log2 = st.columns([3, 1])
    log_lines = 50
    with col_log1:
        log_lines = st.slider("최근 줄 수", min_value=20, max_value=200, value=50, step=10, key="log_lines")
    with col_log2:
        st.write("")
        st.write("")
        if st.button("🗑 로그 초기화", key="clear_log"):
            try:
                if os.path.exists(LOG_PATH):
                    open(LOG_PATH, "w", encoding="utf-8").close()
            except Exception:
                pass
            st.rerun()

    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        recent = "".join(all_lines[-log_lines:]) if all_lines else "(로그 없음)"
        st.code(recent, language=None)
    else:
        st.info("아직 실행 로그가 없습니다. (자동화 작업이 실행되면 기록됩니다)")

    st.divider()

    # ── 서버 관리 (네트워크 서버 모드) ──
    st.subheader("🖥️ 서버 관리")
    st.caption("이 PC를 Streamlit 네트워크 서버로 운영할 때 사용하는 설정입니다.")

    # 현재 서버 IP 목록 조회
    def _get_local_ips():
        try:
            r = subprocess.run(
                ["ipconfig"],
                capture_output=True, text=True, encoding="cp949", errors="replace"
            )
            ips = re.findall(r"IPv4.*?:\s*([\d.]+)", r.stdout)
            return [ip for ip in ips if not ip.startswith("127.")]
        except Exception:
            return []

    local_ips = _get_local_ips()

    with st.expander("📡 현재 서버 접속 주소", expanded=True):
        if local_ips:
            for ip in local_ips:
                st.markdown(f"**내부 네트워크:** `http://{ip}:8501`")
        else:
            st.info("IP 주소를 가져올 수 없습니다.")
        st.markdown("**이 컴퓨터:** `http://localhost:8501`")
        st.caption("외부(인터넷) 접속은 공유기 포트포워딩 + DDNS 설정이 필요합니다.")

    with st.expander("⚙️ 부팅 자동시작 설정 방법", expanded=False):
        st.markdown("""
    **1단계: 서버 부팅 자동시작 등록**
    ```
    setup_server_boot.bat  →  관리자 권한으로 실행
    ```
    - Windows 작업 스케줄러에 로그인 시 자동 서버 시작 등록
    - 방화벽 포트 8501 자동 개방

    **2단계: 공유기 포트포워딩**
    - 공유기 관리 페이지 접속 (보통 192.168.0.1 또는 192.168.1.1)
    - 포트포워딩 메뉴 → 외부포트 **8501** → 내부 IP:{port} **8501** 추가

    **3단계: DDNS 설정 (고정 도메인)**
    - https://www.duckdns.org 접속 → 무료 도메인 등록
    - `yourname.duckdns.org` 형태로 외부에서 접속 가능
    - 30분마다 IP 업데이트 자동화 스크립트 실행

    **4단계 이후 접속 주소**
    ```
    http://yourname.duckdns.org:8501
    ```
    """)

    c_start, c_stop = st.columns(2)
    if c_start.button("▶ 서버 시작 (start_server.bat)", key="btn_start_server", use_container_width=True):
        server_bat = os.path.join(BASE_DIR, "start_server.bat")
        if os.path.exists(server_bat):
            subprocess.Popen(["cmd", "/c", "start", "", server_bat], cwd=BASE_DIR)
            st.success("서버 시작 명령을 보냈습니다. 새 창이 열립니다.")
        else:
            st.error("start_server.bat 파일을 찾을 수 없습니다.")

    if c_stop.button("⏹ 서버 중지 (stop_server.bat)", key="btn_stop_server", use_container_width=True):
        stop_bat = os.path.join(BASE_DIR, "stop_server.bat")
        if os.path.exists(stop_bat):
            subprocess.Popen(["cmd", "/c", "start", "", stop_bat], cwd=BASE_DIR)
            st.warning("서버 중지 명령을 보냈습니다.")
        else:
            st.error("stop_server.bat 파일을 찾을 수 없습니다.")

    st.divider()

    # ── 코스트코 크롤링 ───────────────────────────────────────────
    st.divider()
    st.subheader("🔍 코스트코 쇼핑몰 크롤링")
    st.caption("수집 결과는 공유 제품 DB에 가격구분='온라인'으로 저장됩니다.")

    try:
        import costco_crawler as _cc
        _crawler_ok = True
    except ImportError:
        _crawler_ok = False

    if not _crawler_ok:
        st.error("costco_crawler.py 파일을 찾을 수 없습니다.")
    else:
        # ── 코스트코 계정 설정 ────────────────────────────────
        with st.expander("🔑 코스트코 계정 설정", expanded=not _cc.is_profile_exists()):
            st.caption("크롤링 시 로그인에 사용됩니다. 앱 서버(이 PC)에만 저장됩니다.")
            saved_email = get_global_setting('costco_email', '')
            saved_pw    = get_global_setting('costco_password', '')

            cx1, cx2 = st.columns(2)
            c_email = cx1.text_input(
                "코스트코 이메일",
                value=saved_email,
                placeholder="example@email.com",
                key="costco_email_input",
            )
            c_pw = cx2.text_input(
                "비밀번호",
                value=saved_pw,
                type="password",
                key="costco_pw_input",
            )
            cs1, cs2 = st.columns(2)
            if cs1.button("💾 계정 저장", key="save_costco_cred", use_container_width=True):
                set_global_setting('costco_email',    c_email.strip())
                set_global_setting('costco_password', c_pw.strip())
                st.success("✅ 계정 저장 완료!")
                st.rerun()

            profile_exists = _cc.is_profile_exists()
            if profile_exists:
                cs2.success("✅ 브라우저 프로필 저장됨")
            else:
                cs2.warning("⚠️ 첫 로그인 설정 필요")

            st.divider()
            st.markdown("**첫 로그인 설정** — OTP 포함 최초 1회만 필요")
            st.caption(
                "버튼 클릭 시 브라우저가 열립니다. "
                "코스트코에 로그인하고 OTP 인증을 완료하면 자동으로 저장됩니다."
            )
            if st.button(
                "🌐 브라우저 열어서 코스트코 첫 로그인",
                key="btn_setup_profile",
                use_container_width=True,
                type="primary" if not profile_exists else "secondary",
            ):
                # playwright 설치 여부 먼저 확인
                try:
                    import playwright as _pw_check
                    _pw_installed = True
                except ImportError:
                    _pw_installed = False

                if not _pw_installed:
                    st.error(
                        "playwright가 설치되지 않았습니다.\n"
                        "터미널에서 실행:\n"
                        "pip install playwright\n"
                        "python -m playwright install chromium"
                    )
                else:
                    _setup_email = get_global_setting('costco_email', '')
                    _setup_pw    = get_global_setting('costco_password', '')
                    _script = os.path.join(BASE_DIR, "costco_crawler.py")
                    try:
                        # Windows: CREATE_NEW_CONSOLE — 새 콘솔 창에서 실행
                        subprocess.Popen(
                            [sys.executable, _script, "--setup-auto",
                             _setup_email, _setup_pw],
                            cwd=BASE_DIR,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                        )
                        st.success(
                            "✅ 새 창이 열립니다!\n\n"
                            "1. 열린 브라우저에서 코스트코 이메일 / 비밀번호 입력\n"
                            "2. OTP 인증 완료\n"
                            "3. 로그인 완료 후 콘솔 창이 자동으로 닫힙니다\n"
                            "4. 이 페이지를 새로고침(F5)하면 상태가 업데이트됩니다."
                        )
                    except Exception as _e:
                        st.error(f"실행 오류: {_e}")

        # ── 크롤링 실행 ───────────────────────────────────────
        profile_ok = _cc.is_profile_exists()
        _c_email   = get_global_setting('costco_email', '')
        _c_pw      = get_global_setting('costco_password', '')

        if not profile_ok:
            st.warning("위 '코스트코 계정 설정'에서 첫 로그인을 먼저 완료해주세요.")
        else:
            crawl_tab1, crawl_tab2 = st.tabs(["카테고리 크롤링", "키워드 검색"])

            with crawl_tab1:
                # ── 빠른 선택 프리셋 ──
                PRESETS = {
                    "🏗️ 최초구축": ["식품", "신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품",
                                     "생활용품", "세제/청소", "화장지", "가전/디지털", "주방가전",
                                     "뷰티/화장품", "건강/영양제", "의류/패션", "완구", "반려동물", "자동차용품"],
                    "🔄 정기갱신": ["신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품"],
                    "🔥 핫딜시즌": ["스페셜할인", "커클랜드", "신상품"],
                    "🆕 새상품탐색": ["신상품", "스페셜할인"],
                }
                st.markdown("**빠른 선택**")
                p_cols = st.columns(4)
                for pi, (label, cats) in enumerate(PRESETS.items()):
                    if p_cols[pi].button(label, key=f"preset_{pi}", use_container_width=True):
                        for c in cats:
                            st.session_state[f"cat_{c}"] = True

                st.markdown("**수집할 카테고리 선택**")
                cat_names = list(_cc.CATEGORIES.keys())
                cat_cols = st.columns(3)
                sel_cats = []
                for i, cat in enumerate(cat_names):
                    if cat_cols[i % 3].checkbox(cat, key=f"cat_{cat}"):
                        sel_cats.append(cat)

                max_cat = st.number_input(
                    "카테고리당 최대 수집 수", min_value=10, max_value=1000,
                    value=300, step=10, key="crawl_max_cat"
                )
                if st.button(
                    f"🚀 카테고리 크롤링 시작 ({len(sel_cats)}개 선택)",
                    type="primary", key="btn_crawl_cat",
                    disabled=len(sel_cats) == 0,
                    use_container_width=True,
                ):
                    targets = [{"type": "category", "name": c} for c in sel_cats]
                    progress_box = st.empty()
                    log_lines = []

                    def _cb_cat(msg):
                        log_lines.append(msg)
                        progress_box.code("\n".join(log_lines[-20:]))

                    _crawl_ok = False
                    with st.spinner("크롤링 중... (수 분 소요될 수 있습니다)"):
                        try:
                            result = _cc.run_crawl(
                                targets=targets,
                                email=_c_email,
                                password=_c_pw,
                                max_products=int(max_cat),
                                progress_cb=_cb_cat,
                                updated_by='crawler',
                            )
                            if result["errors"]:
                                st.warning("오류:\n" + "\n".join(result["errors"]))
                            st.session_state['last_crawl_result'] = result
                            _crawl_ok = True
                        except RuntimeError as e:
                            st.error(f"❌ {e}")
                    if _crawl_ok:
                        r = st.session_state['last_crawl_result']
                        st.success(
                            f"✅ 크롤링 완료!\n\n"
                            f"수집 **{r['total_crawled']}**개  →  "
                            f"신규 **{r['new']}**개 / 업데이트 **{r['updated']}**개"
                        )
                        st.balloons()
                        if st.button("📦 결과 보기 (제품 DB)", type="primary",
                                     key="go_db_cat", use_container_width=True):
                            st.session_state['_pending_tab'] = "📦 제품 DB"
                            st.rerun()

            with crawl_tab2:
                kw_input = st.text_input(
                    "검색 키워드 (쉼표로 여러 개 입력 가능)",
                    placeholder="예: 그릭요거트, 올리브오일, 커클랜드",
                    key="crawl_kw_input",
                )
                max_kw = st.number_input(
                    "키워드당 최대 수집 수", min_value=10, max_value=500,
                    value=100, step=10, key="crawl_max_kw"
                )
                if st.button(
                    "🔍 키워드 크롤링 시작",
                    type="primary", key="btn_crawl_kw",
                    disabled=not kw_input.strip(),
                    use_container_width=True,
                ):
                    keywords = [k.strip() for k in kw_input.split(",") if k.strip()]
                    targets = [{"type": "keyword", "keyword": k} for k in keywords]
                    progress_box2 = st.empty()
                    log_lines2 = []

                    def _cb_kw(msg):
                        log_lines2.append(msg)
                        progress_box2.code("\n".join(log_lines2[-20:]))

                    _crawl_ok2 = False
                    with st.spinner("크롤링 중..."):
                        try:
                            result2 = _cc.run_crawl(
                                targets=targets,
                                email=_c_email,
                                password=_c_pw,
                                max_products=int(max_kw),
                                progress_cb=_cb_kw,
                                updated_by='crawler',
                            )
                            if result2["errors"]:
                                st.warning("오류:\n" + "\n".join(result2["errors"]))
                            st.session_state['last_crawl_result'] = result2
                            _crawl_ok2 = True
                        except RuntimeError as e:
                            st.error(f"❌ {e}")
                    if _crawl_ok2:
                        r2 = st.session_state['last_crawl_result']
                        st.success(
                            f"✅ 크롤링 완료!\n\n"
                            f"수집 **{r2['total_crawled']}**개  →  "
                            f"신규 **{r2['new']}**개 / 업데이트 **{r2['updated']}**개"
                        )
                        st.balloons()
                        if st.button("📦 결과 보기 (제품 DB)", type="primary",
                                     key="go_db_kw", use_container_width=True):
                            st.session_state['_pending_tab'] = "📦 제품 DB"
                            st.rerun()

        # 온라인 수집 제품 현황 — 카테고리별 분류 통계
        online_prods = [p for p in cached_shared_products() if p.get('price_type') == '온라인']
        if online_prods:
            st.divider()
            st.markdown(f"**🌐 온라인 수집 제품 현황: {len(online_prods)}개**")

            # 카테고리별 집계
            from collections import Counter
            cat_counter = Counter(p.get('category', '') or '' for p in online_prods)
            no_cat_cnt = cat_counter.pop('', 0)

            # 카테고리별 통계 표시
            if cat_counter:
                _stat_rows = sorted(cat_counter.items(), key=lambda x: -x[1])
                stat_cols = st.columns(min(len(_stat_rows), 5))
                for _ci, (_cname, _ccnt) in enumerate(_stat_rows[:5]):
                    stat_cols[_ci].metric(_cname, f"{_ccnt}개")
                if len(_stat_rows) > 5:
                    with st.expander(f"나머지 {len(_stat_rows)-5}개 카테고리 보기"):
                        for _cname, _ccnt in _stat_rows[5:]:
                            st.caption(f"• {_cname}: {_ccnt}개")

            # 미분류 상품 경고 + 재분류 버튼
            if no_cat_cnt > 0:
                _nc_col1, _nc_col2 = st.columns([3, 1])
                _nc_col1.warning(
                    f"⚠️ 카테고리 미분류 상품 **{no_cat_cnt}개** — "
                    "카테고리 크롤링을 다시 실행하면 자동으로 분류됩니다."
                )
                if IS_ADMIN and _nc_col2.button("🏷️ 전체 카테고리 재크롤링",
                                                key="btn_recrawl_cat",
                                                use_container_width=True):
                    _all_known_cats = [c for c in _cc.CATEGORIES if c != "전체"]
                    set_setting(USERNAME, 'auto_crawl_categories',
                                json.dumps(_all_known_cats, ensure_ascii=False))
                    with st.spinner(f"전체 카테고리 재크롤링 중 ({len(_all_known_cats)}개)... 수 분 소요"):
                        _rc = subprocess.run(
                            [PYTHON_PATH, SCRIPT_PATH, "--task", "crawl", "--user", USERNAME],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=900,
                        )
                    if _rc.returncode == 0:
                        st.success("✅ 재크롤링 완료 — 카테고리 분류가 업데이트됐습니다.")
                    else:
                        st.error("❌ 재크롤링 오류")
                    st.code((_rc.stdout + _rc.stderr).strip()[-3000:], language=None)
                    st.rerun()

            # 상품 목록 미리보기 (카테고리 포함)
            _view_cat = st.selectbox(
                "카테고리별 보기",
                options=["전체"] + sorted(cat_counter.keys()) + (["(미분류)"] if no_cat_cnt else []),
                key="online_preview_cat",
            )
            if _view_cat == "전체":
                _preview_src = online_prods
            elif _view_cat == "(미분류)":
                _preview_src = [p for p in online_prods if not p.get('category', '')]
            else:
                _preview_src = [p for p in online_prods if p.get('category', '') == _view_cat]

            preview_df = pd.DataFrame([{
                "카테고리": p.get("category", "") or "(미분류)",
                "상품번호": p.get("product_no", ""),
                "상품명":   p.get("costco_name", ""),
                "가격(원)": f"{int(p.get('unit_price', 0)):,}",
                "업데이트": (p.get("updated_at") or "")[:10],
            } for p in _preview_src[:100]])
            st.dataframe(preview_df, use_container_width=True, height=300)
            if len(_preview_src) > 100:
                st.caption(f"상위 100개만 표시 (선택 {len(_preview_src)}개 / 전체 {len(online_prods)}개)")

            # 상세 수집 (추가이미지 + 상세페이지 HTML)
            if IS_ADMIN:
                st.divider()
                _det_prods     = [p for p in online_prods if p.get("product_no")]
                _det_no_detail = [p for p in _det_prods
                                  if not p.get("extra_images") and not p.get("detail_html")]
                _det_c1, _det_c2 = st.columns([3, 1])
                _det_c1.markdown(
                    f"**📷 상세 수집** (추가이미지 + 상세내용) — "
                    f"미수집 {len(_det_no_detail)}개 / 전체 {len(_det_prods)}개"
                )
                if _det_c2.button("📷 전체 상세 수집", key="btn_crawl_detail",
                                   use_container_width=True, disabled=not _det_no_detail):
                    _all_pnos     = [p["product_no"] for p in _det_no_detail]
                    _costco_email = _gs("costco_email") or ""
                    _costco_pw    = _gs("costco_password") or ""
                    import costco_crawler as _cc_det
                    with st.spinner(f"상세 수집 중 ({len(_all_pnos)}개)... 수 분 소요"):
                        _det_res = _cc_det.crawl_product_details(
                            _all_pnos, _costco_email, _costco_pw)
                    if _det_res["fail"] == 0:
                        st.success(f"✅ 상세 수집 완료 — {_det_res['ok']}개 성공!")
                    else:
                        st.warning(
                            f"상세 수집 완료 — 성공 {_det_res['ok']}개 / 실패 {_det_res['fail']}개"
                        )
                        for _em in _det_res["errors"][:5]:
                            st.caption(f"• {_em}")
                    st.rerun()
