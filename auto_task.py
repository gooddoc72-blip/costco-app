"""
코스트코핫딜 자동화 스크립트 v2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Task 1 (shopping) - 장보기 목록 발송
  · 네이버 READY 주문 조회
  · 장보기 목록 생성 → 카카오톡 발송

Task 2 (shipping) - 자동 발송처리
  · 네이버 READY 주문 조회
  · CJ 택배 API 접수 → 송장번호 확보
  · 송장 파일(.xls) data/ 폴더에 저장
  · 네이버 스마트스토어 일괄 발송처리
  · 완료 알림 발송

실행 예시:
  python auto_task.py                          # 전체(shopping + shipping)
  python auto_task.py --task shopping          # 장보기만
  python auto_task.py --task shipping          # 발송처리만
  python auto_task.py --task shopping --user admin
"""
import sqlite3, os, sys, argparse, re, json, logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import naver_api
from utils import fmt, extract_pack_qty
from db import (
    get_global_setting,
    set_global_setting,
    get_all_settings,
    get_all_products as _db_get_all_products,
    set_setting,
    get_setting,
    get_user_db,
    get_user_info,
    submit_shopping_list,
    log_dispatch_success,
    get_return_due_lots,
)
from services import match_product_to_db, calc_cost

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_PATH = os.path.join(DATA_DIR, "auto_task.log")

# ── 로그 설정 (5MB × 3개 로테이션) ───────────────
os.makedirs(DATA_DIR, exist_ok=True)
_logger = logging.getLogger("auto_task")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    _fh = RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _fh.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_fh)


# ── 공통 유틸 ─────────────────────────────────
# 현재 태스크를 실행 중인 사용자 (사용자별 로그 분리 → 타 사용자 로그 노출 방지)
_CTX_USER = None
USER_LOG_DIR = os.path.join(DATA_DIR, "user_logs")


def set_log_user(username):
    """이후 log() 호출을 해당 사용자 전용 로그에도 기록. 태스크 시작 시 호출."""
    global _CTX_USER
    _CTX_USER = (username or "").strip() or None


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except Exception:
        pass
    _logger.info(line)  # 전체 로그(관리자 전용 조회)
    # 사용자별 로그 — 일반 사용자는 자동화 페이지에서 본인 것만 조회
    if _CTX_USER:
        try:
            os.makedirs(USER_LOG_DIR, exist_ok=True)
            _uf = os.path.join(USER_LOG_DIR, f"auto_task_{_CTX_USER}.log")
            # 과도한 증가 방지: 2MB 초과 시 뒤쪽 절반만 유지
            try:
                if os.path.exists(_uf) and os.path.getsize(_uf) > 2 * 1024 * 1024:
                    with open(_uf, "r", encoding="utf-8", errors="replace") as _rf:
                        _keep = _rf.readlines()[-2000:]
                    with open(_uf, "w", encoding="utf-8") as _wf:
                        _wf.writelines(_keep)
            except Exception:
                pass
            with open(_uf, "a", encoding="utf-8", errors="replace") as _af:
                _af.write(line + "\n")
        except Exception:
            pass


def get_user_settings(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    if not os.path.exists(db_path):
        return None
    return get_all_settings(username)


def get_all_products(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    if not os.path.exists(db_path):
        return []
    return _db_get_all_products(username)


def save_daily_orders(username, orders, settings):
    """조회된 주문을 daily_orders + order_history에 저장.
    services.process_and_save_orders 통합 진입점 사용 — 매입가 계산/매칭 일관성 보장.
    """
    import pandas as _pd
    today = datetime.now().strftime("%Y-%m-%d")
    shipping_cost = int(settings.get("shipping_cost") or 1800)
    box_cost = int(settings.get("box_cost") or 300)

    if not orders:
        return

    df = _pd.DataFrame(orders)
    from services import process_and_save_orders
    process_and_save_orders(
        username, df, today, shipping_cost, box_cost, save_history=True,
    )


def auto_save_profit(username, date):
    """daily_orders(profit 계산 포함) → profit_settlements 자동 저장(order_no 기준) → 홈달력/통계·수익계산 반영.
    · '자동' 저장분은 매번 daily에서 재생성(삭제 후 재삽입) → 옛 데이터 누적 방지
    · 수동 편집/저장분(match_source != '자동')은 order_no로 보존
    반환: 저장 건수.
    """
    import re as _re2
    from datetime import datetime as _dt2, timedelta as _td2
    from db import (get_daily_orders, get_profit_settlements, save_profit_settlements,
                    search_order_history, get_user_db)
    rows = get_daily_orders(username, date)
    if not rows:
        return 0
    # order_no 매핑(daily에 order_no 없는 옛 데이터 보조) — 최근 30일 주문이력에서 (수취인,상품)→상품주문번호
    _ono_map = {}
    try:
        _df_from = (_dt2.strptime(date, "%Y-%m-%d") - _td2(days=30)).strftime("%Y-%m-%d")
        for _h in (search_order_history(username, date_from=_df_from, date_to=date, limit=3000) or []):
            _hk = (str(_h.get('recipient', '') or ''), str(_h.get('product_name', '') or ''))
            if _hk not in _ono_map and _h.get('order_no'):
                _ono_map[_hk] = str(_h.get('order_no'))
    except Exception:
        _ono_map = {}
    # 수동 저장분(사용자 편집)은 order_no로 보존, '자동' 저장분은 삭제 후 재생성
    _existing = get_profit_settlements(username, date) or []
    _manual = {str(s.get('order_no', '') or '')
               for s in _existing if s.get('match_source') != '자동' and s.get('order_no')}
    try:
        _dc = get_user_db(username)
        _dc.execute("DELETE FROM profit_settlements WHERE settlement_date=? AND match_source='자동'",
                    (date,))
        _dc.commit(); _dc.close()
    except Exception:
        pass
    _exist = _manual
    ps_rows = []
    for r in rows:
        _rec = str(r.get('recipient', '') or '')
        _pnm = str(r.get('product_name', '') or '')
        # order_no(상품주문번호): daily_orders 저장값 우선, 없으면 주문이력에서 매핑
        _ono = str(r.get('order_no', '') or '').strip() or _ono_map.get((_rec, _pnm), '')
        if _ono and _ono in _exist:
            continue  # 이미 저장됨(수동 편집 포함) → 보존
        _sm = _re2.search(r'x\s*(\d+)\s*개', _pnm, _re2.IGNORECASE)
        _sf = int(_sm.group(1)) if _sm and 1 < int(_sm.group(1)) <= 50 else 1
        ps_rows.append({
            'order_no': _ono, 'recipient': _rec, 'product_name': _pnm,
            'product_no': str(r.get('product_no', '') or ''),
            'option_info': str(r.get('option_info', '') or ''),
            'qty': int(r.get('qty', 1) or 1),
            'order_amount': int(r.get('order_amount', 0) or 0),
            'shipping_fee': int(r.get('shipping_fee', 0) or 0),
            'extra_shipping': int(r.get('extra_shipping', 0) or 0),
            'settlement_amount': int(r.get('settlement', 0) or 0),
            'cost_price': int(r.get('cost_price', 0) or 0),
            'delivery_cost': int(r.get('delivery_cost', 0) or 0),
            'box_cost': int(r.get('box_cost', 0) or 0),
            'profit': int(r.get('profit', 0) or 0),
            'matched_keyword': '', 'matched_product_no': str(r.get('product_no', '') or ''),
            'match_source': '자동', 'split_qty': 1, 'sell_factor': _sf,
        })
    if not ps_rows:
        return 0
    return save_profit_settlements(username, date, ps_rows)


def auto_settlement_match(username, api_id, api_secret, days=10):
    """최근 N일 네이버 정산을 자동 수집·역추적 매칭·저장하고, 실제 정산액을
    profit_settlements에 반영한다(홈 달력/통계 '실정산' 반영). 반환: (처리일수, 매칭건, 수익반영건)."""
    if not (api_id and api_secret):
        return 0, 0, 0
    from db import (save_naver_settlements, delete_naver_settlements_by_date,
                    get_naver_settlements_by_date, get_dispatch_by_order_nos,
                    save_settlement_matches, apply_actual_settlements_to_profit)
    from settlement_service import match_settled_to_dispatch
    _dates = _n_match = _n_upd = 0
    _yesterday = datetime.now().date() - timedelta(days=1)
    for _i in range(days):
        _d = (_yesterday - timedelta(days=_i)).strftime("%Y-%m-%d")
        try:
            _res = naver_api.get_settlement_history(api_id, api_secret, _d, _d)
            recs, err = _res[0], _res[1]
        except Exception:
            continue
        if err or not recs:
            continue
        try:
            delete_naver_settlements_by_date(username, _d)
            save_naver_settlements(username, _d, recs)
        except Exception:
            continue
        _settled = get_naver_settlements_by_date(username, _d)
        if not _settled:
            continue
        _po = [str(r.get('product_order_no', '')) for r in _settled]
        _disp = get_dispatch_by_order_nos(username, _po, platform='naver')
        _rt = match_settled_to_dispatch(_settled, _disp)
        _rows = ([{**r, 'match_status': 'matched'} for r in _rt['matched']]
                 + [{**r, 'match_status': 'mismatched'} for r in _rt['mismatched']]
                 + [{**r, 'match_status': 'no_dispatch'} for r in _rt['no_dispatch']])
        if _rows:
            _n_match += save_settlement_matches(username, _d, _rows)
            _actuals = {r['product_order_no']: {'actual': int(r.get('actual') or 0)}
                        for r in _rows if int(r.get('actual') or 0) > 0}
            if _actuals:
                _n_upd += apply_actual_settlements_to_profit(username, _actuals)
        _dates += 1
    return _dates, _n_match, _n_upd


def send_notification(settings, msg, username=None):
    """카카오톡으로 '전체' 메시지 발송. (텔레그램은 2026-07 삭제 — 사용빈도 낮음)
    카카오는 길면(7500자 초과) 자동으로 나눠 전부 발송(잘림 없음)."""
    kakao_token = settings.get("kakao_access_token", "")
    kakao_api_key = settings.get("kakao_api_key", "")
    kakao_refresh = settings.get("kakao_refresh_token", "")
    kakao_secret = settings.get("kakao_client_secret", "")

    def _save_refreshed_token(err):
        if err and "__TOKEN_REFRESHED__" in str(err) and username:
            parts = str(err).replace("__TOKEN_REFRESHED__", "").split("||")
            try:
                set_setting(username, "kakao_access_token", parts[0])
                if len(parts) > 1:
                    set_setting(username, "kakao_refresh_token", parts[1])
                log("🔄 카카오 토큰 자동 갱신")
            except Exception as e:
                log(f"⚠️ 카카오 토큰 갱신 저장 실패: {e}")

    sent_any = False
    if kakao_token:
        ok_k, kerr = naver_api.send_kakao(kakao_token, msg,
                                          rest_api_key=kakao_api_key,
                                          refresh_token=kakao_refresh,
                                          client_secret=kakao_secret)
        if ok_k:
            _save_refreshed_token(kerr)
            sent_any = True
        else:
            log(f"  카카오톡 실패: {kerr}")
    return sent_any


# ── Task 3: 정기 크롤링 ──────────────────────────
def run_crawl_task(username="admin"):
    log("=" * 50)
    log(f"[Task 3] 정기 크롤링 시작 (사용자: {username})")

    settings = get_user_settings(username)
    if not settings:
        log(f"❌ '{username}' 사용자 DB 없음")
        return False

    cats_json = settings.get("auto_crawl_categories", "[]")
    try:
        categories = json.loads(cats_json)
    except Exception:
        categories = []

    if not categories:
        log("❌ 크롤링 카테고리 미설정 — 자동화 탭 > Task 3에서 카테고리를 설정하세요.")
        return False

    max_per = int(settings.get("auto_crawl_max", 200) or 200)
    email    = get_global_setting("costco_email")
    password = get_global_setting("costco_password")

    log(f"  카테고리 {len(categories)}개, 카테고리당 최대 {max_per}개")

    try:
        import costco_crawler
    except ImportError:
        log("❌ costco_crawler.py를 찾을 수 없습니다.")
        return False

    targets = [{"type": "category", "name": c} for c in categories]
    result = costco_crawler.run_crawl(
        targets, email, password, max_per,
        progress_cb=lambda m: log(m),
        updated_by="crawler",
    )

    summary = (
        f"🕐 정기 크롤링 완료\n"
        f"카테고리: {', '.join(categories)}\n"
        f"신규: {result['new']}개 / 업데이트: {result['updated']}개"
    )
    if result.get("errors"):
        summary += f"\n오류 {len(result['errors'])}건: " + "; ".join(result["errors"][:3])

    log(summary)
    send_notification(settings, summary, username)
    log("[Task 3] 완료")
    return True


# ── Task 1: 장보기 목록 발송 ─────────────────────
def run_shopping_task(username="admin"):
    now = datetime.now()
    log("=" * 50)
    log(f"[Task 1] 장보기 목록 발송 시작 (사용자: {username})")

    settings = get_user_settings(username)
    if not settings:
        log(f"❌ '{username}' 사용자 DB 없음")
        return False

    api_id = settings.get("api_client_id", "")
    api_secret = settings.get("api_client_secret", "")
    cpg_access = settings.get("coupang_access_key", "")
    cpg_secret = settings.get("coupang_secret_key", "")
    cpg_vendor = settings.get("coupang_vendor_id", "")
    _has_naver = bool(api_id and api_secret)
    _has_coupang = bool(cpg_access and cpg_secret and cpg_vendor)
    if not _has_naver and not _has_coupang:
        log("❌ 네이버·쿠팡 API 키 모두 미설정 → 앱 설정에서 등록 필요")
        return False

    orders, err = [], None
    _seen_ono = set()

    # ── 네이버 주문 수집 ──
    if _has_naver:
        # 발송상태 동기화: 미발송으로 잡힌 주문의 실제 상태를 갱신(이미 발송된 건 제외)
        try:
            from services import sync_active_order_status
            _sy = sync_active_order_status(username, api_id, api_secret)
            if _sy.get('error'):
                log(f"⚠️ 발송상태 동기화 경고: {_sy['error']}")
            elif _sy.get('checked'):
                log(f"🚚 발송상태 동기화 — 조회 {_sy['checked']} / 갱신 {_sy['updated']} / 발송완료 제외 {_sy['cleared']}")
        except Exception as e:
            log(f"⚠️ 발송상태 동기화 실패(계속 진행): {e}")

        # 장보기 대상 = 결제완료(신규) + 발송대기(READY) 모두. 발주확인 전(PAYED) 주문도 사야 하므로 포함.
        log("📋 네이버 주문 조회 중 (결제완료 + 발송대기)...")
        for _stt in ("READY", "PAYED"):
            _o, _e = naver_api.get_new_orders(api_id, api_secret, hours_back=48, status_type=_stt)
            if _o:
                for _od in _o:
                    _ono = str(_od.get("상품주문번호", ""))
                    if _ono and _ono in _seen_ono:
                        continue
                    _seen_ono.add(_ono)
                    orders.append(_od)
            elif _e:
                err = _e

    # ── 쿠팡 주문 수집 (쿠팡 셀러도 장보기 목록 자동 발송) ──
    if _has_coupang:
        log("🛒 쿠팡 주문 조회 중 (결제완료 + 발주확인, 7일)...")
        try:
            import coupang_api
            _crows, _cerr, _, _ = coupang_api.get_orders(
                cpg_access, cpg_secret, cpg_vendor, status="ALL", days_back=7)
            if _cerr and not _crows:
                log(f"⚠️ 쿠팡 오류: {_cerr}")
                if not orders:
                    err = _cerr
            # 쿠팡 상태(영문) → 네이버식 한글 매핑. DEPARTURE(출고완료) 이후는 이미 구매분이라 제외.
            _CPG_KR = {"ACCEPT": "결제완료", "INSTRUCT": "발주확인"}
            _cadd = 0
            for _od in (_crows or []):
                _st = str(_od.get("주문상태", "")).upper()
                if _st not in _CPG_KR:
                    continue
                _od["주문상태"] = _CPG_KR[_st]
                _ono = str(_od.get("상품주문번호", ""))
                if _ono and _ono in _seen_ono:
                    continue
                _seen_ono.add(_ono)
                orders.append(_od)
                _cadd += 1
            log(f"  ✅ 쿠팡 {_cadd}건 (장보기 대상)")
        except Exception as _ce:
            log(f"⚠️ 쿠팡 수집 예외(계속 진행): {_ce}")

    if err and not orders:
        log(f"❌ API 오류: {err}")
        return False

    if not orders:
        msg = (f"📦 {now.strftime('%m/%d %H:%M')} 마감\n\n"
               f"주문 0건 - 오늘 장볼 것 없습니다! 🎉")
        log(msg)
        send_notification(settings, msg, username)
        return True

    log(f"✅ {len(orders)}건 조회 완료")
    try:
        save_daily_orders(username, orders, settings)
        # 수익계산 자동 저장 (profit_settlements) → 홈달력/통계 수익 반영
        try:
            _pn = auto_save_profit(username, datetime.now().strftime("%Y-%m-%d"))
            if _pn:
                log(f"💰 수익계산 자동 저장: {_pn}건 (신규)")
        except Exception as _pe:
            log(f"⚠️ 수익계산 자동 저장 실패(계속): {_pe}")
    except Exception as e:
        log(f"⚠️ 주문 DB 저장 실패 (계속 진행): {e}")

    try:
        # ── 장보기 대상: 발주확인(오늘 처리해야 할 주문)만 집계 ──
        # 배송중·배송완료·취소 등 이미 처리된 건은 제외
        SHOPPING_STATUSES = {'발주확인', '결제완료', '발송대기'}
        shopping_orders = [o for o in orders
                           if o.get('주문상태', '') in SHOPPING_STATUSES]
        if not shopping_orders:
            # 대상 건 없으면 전체 orders로 폴백 (상태 매핑 이슈 대비)
            shopping_orders = orders

        from collections import defaultdict
        shopping = defaultdict(lambda: {"주문수량": 0, "정산금액": 0, "배송비합": 0, "상품명": "", "옵션": "", "상품번호": ""})
        for o in shopping_orders:
            pno = str(o.get("상품번호", ""))
            name = o.get("상품명", "")
            opt = o.get("옵션정보", "") or ""
            key = (pno, name, opt)
            shopping[key]["주문수량"] += int(o.get("수량", 1))
            shopping[key]["주문건수"] = shopping[key].get("주문건수", 0) + 1
            shopping[key]["정산금액"] += int(o.get("정산예정금액") or 0)
            shopping[key]["배송비합"] += int(o.get("배송비 합계") or 0)
            shopping[key]["상품명"] = name
            shopping[key]["옵션"] = opt
            shopping[key]["상품번호"] = pno

        products = get_all_products(username)
        total_cost = 0
        total_settlement = 0
        total_costco_qty = 0

        # 항목 조립
        sorted_items = sorted(shopping.items(), key=lambda x: x[1]["상품명"])
        item_lines = []
        _admin_items = []  # 관리자 제출용 구조화 항목
        for idx, (_, item) in enumerate(sorted_items, 1):
            name       = item["상품명"]
            order_qty  = item["주문수량"]
            opt        = item["옵션"]
            pno        = item["상품번호"]
            settlement = item["정산금액"]

            pack       = extract_pack_qty(opt, name)
            costco_qty = order_qty * pack

            matched_p = match_product_to_db(username, name,
                                              product_no=pno or None,
                                              _user_prods=products)
            _pack_price = int(matched_p.get('unit_price') or 0) if matched_p else 0
            _est_cost = calc_cost(matched_p, costco_qty) if matched_p else 0
            if matched_p:
                total_cost += _est_cost
            total_settlement += settlement
            total_costco_qty += costco_qty

            # 카드 형식: 상품명 줄(• 제품명 × 총수량) + 상세 줄(옵션 · 정산 · 택배)
            order_cnt  = item.get("주문건수", order_qty)
            ship_total = item.get("배송비합", 0)
            ship_each  = round(ship_total / order_cnt) if order_cnt else ship_total
            name_line = f"• {name} × {costco_qty}개 ({order_cnt}건)"
            detail_parts = []
            if opt:
                detail_parts.append(f"옵션 {opt}")
            if settlement:
                detail_parts.append(f"정산 {fmt(settlement)}원")
            detail_parts.append(f"택배 {fmt(ship_each)}원")
            item_lines.append(name_line)
            item_lines.append("  " + " · ".join(detail_parts))

            _admin_items.append({
                "코스트코상품번호": str(pno or ''),
                "상품명": name,
                "옵션정보": opt or '',
                "주문건수": int(order_cnt),
                "주문수량": int(order_qty),
                "코스트코구매수량": int(costco_qty),
                "팩단가": _pack_price,
                "예상금액": int(_est_cost),
                "정산금액": int(settlement),
                "배송비": int(ship_each),
            })

        divider = "─" * 24
        lines = [
            "🛒 코스트코 장보기 목록",
            f"📅 {now.strftime('%Y-%m-%d %H:%M')}  │  주문 {len(shopping_orders)}건 / {len(sorted_items)}종",
            divider,
        ]
        lines += item_lines
        lines.append(divider)
        if total_cost > 0:
            lines.append(f"💰 예상 구매액: {fmt(total_cost)}원")
        if total_settlement > 0:
            lines.append(f"💳 총 정산예정: {fmt(total_settlement)}원")
        lines.append(f"🛒 코스트코 총 {total_costco_qty}개 구매 필요")

        msg = "\n".join(lines)
        log(msg)

        if send_notification(settings, msg, username):
            log("✅ 알림 전송 완료")
        else:
            log("⚠️ 알림 채널 미설정 (카카오 설정 필요)")

        # ── 관리자에게 장보기 목록 자동 제출 (관리자 카톡 발송은 안 함) ──
        #    관리자는 관리자 페이지 '사용자별 장보기 목록'에서 확인
        try:
            _order_date = now.strftime("%Y-%m-%d")
            # 하루 1회만 관리자 제출 (예약 최초 1회). 이미 오늘 발송됐으면 생략.
            if get_setting(username, 'admin_shop_sent_date') == _order_date:
                log("📋 관리자 제출 생략 (오늘 이미 발송됨)")
            else:
                submit_shopping_list(username, _order_date, _admin_items,
                                     total_items=len(_admin_items),
                                     total_amount=int(total_cost))
                set_setting(username, 'admin_shop_sent_date', _order_date)
                log(f"📋 관리자 제출 완료 ({len(_admin_items)}종 / {fmt(int(total_cost))}원)")
        except Exception as _ae:
            log(f"⚠️ 관리자 제출 실패(계속 진행): {_ae}")

    except Exception as e:
        import traceback
        log(f"❌ 장보기 목록 생성/발송 중 오류: {e}")
        log(traceback.format_exc())
        send_notification(settings, f"❌ Task 1 오류\n{e}", username)
        return False

    log(f"[Task 1] 완료")
    return True


# ── Task 2: CJ 접수 + 네이버 일괄 발송처리 ─────────
def run_shipping_task(username="admin"):
    now = datetime.now()
    log("=" * 50)
    log(f"[Task 2] 자동 발송처리 시작 (사용자: {username})")

    settings = get_user_settings(username)
    if not settings:
        log(f"❌ '{username}' 사용자 DB 없음")
        return False

    api_id = settings.get("api_client_id", "")
    api_secret = settings.get("api_client_secret", "")
    cj_id = settings.get("cj_api_id", "")
    cj_pw = settings.get("cj_api_pw", "")
    cj_acc = settings.get("cj_account_no", "")
    default_courier = settings.get("default_courier", "CJGLS")
    courier_name_map = {"CJGLS": "CJ대한통운", "HYUNDAI": "롯데택배",
                        "HANJIN": "한진택배", "EPOST": "우체국택배"}
    courier_display = courier_name_map.get(default_courier, "CJ대한통운")

    if not api_id or not api_secret:
        log("❌ 네이버 API 키 미설정")
        return False

    log("📋 배송준비(READY) 주문 조회 중...")
    orders, err = naver_api.get_new_orders(api_id, api_secret,
                                           hours_back=48, status_type="READY")
    if err:
        log(f"❌ API 오류: {err}")
        return False

    if not orders:
        log("ℹ️  발송대기 주문 없음 → 종료")
        return True

    log(f"✅ {len(orders)}건 조회 완료")

    # ── CJ API 접수 → 송장번호 수집 ──
    ship_data = []
    if cj_id and cj_pw and cj_acc:
        log(f"📦 CJ 택배 접수 중 ({len(orders)}건)...")
        order_input = [{"productOrderId": o.get("상품주문번호", "")}
                       for o in orders if o.get("상품주문번호")]
        tracking_results, cj_err = naver_api.register_cj_order(
            cj_id, cj_pw, cj_acc, order_input)
        if cj_err:
            log(f"❌ CJ 접수 실패: {cj_err}")
            send_notification(settings, f"❌ CJ 접수 실패\n{cj_err}", username)
            return False
        if tracking_results:
            log(f"✅ CJ 접수 완료 ({len(tracking_results)}건)")
            for t in tracking_results:
                ship_data.append({
                    "productOrderId": t["productOrderId"],
                    "택배사": courier_display,
                    "trackingNumber": t["trackingNumber"],
                })
    else:
        log("⚠️  CJ API 미설정 → 발송처리 건너뜀 (설정 > 택배사 설정에서 CJ 정보 입력)")
        return True

    if not ship_data:
        log("⚠️  송장 데이터 없음 → 종료")
        return True

    # ── 송장 파일(.xlsx) 저장 (openpyxl — xlwt 대체) ──
    try:
        from openpyxl import Workbook as _WB
        wb = _WB()
        ws = wb.active
        ws.title = "발송처리"
        ws.append(["상품주문번호", "배송방법", "택배사", "송장번호"])
        for row in ship_data:
            ws.append([
                str(row["productOrderId"]),
                "택배,등기,소포",
                str(row["택배사"]),
                str(row["trackingNumber"]),
            ])
        fname = f"발송처리_{now.strftime('%Y%m%d_%H%M')}.xlsx"
        fpath = os.path.join(DATA_DIR, fname)
        wb.save(fpath)
        log(f"💾 송장 파일 저장: {fname}")
    except Exception as e:
        log(f"⚠️  파일 저장 실패: {e}")

    # ── 네이버 일괄 발송처리 ──
    log(f"🚀 네이버 발송처리 요청 중 ({len(ship_data)}건)...")
    result, ship_err = naver_api.ship_orders(api_id, api_secret, ship_data)

    if ship_err:
        log(f"❌ 발송처리 실패: {ship_err}")
        send_notification(settings, f"❌ 자동 발송처리 실패\n{ship_err}", username)
        return False

    success = result.get("success", 0)
    fail = result.get("fail", 0)
    log(f"✅ 발송처리 완료 — 성공: {success}건  실패: {fail}건")
    for d in result.get("fail_details", []):
        log(f"  실패: {d}")

    # ── dispatch_log 기록 (+ 재고 차감) ──
    # 이 태스크는 원래 발송처리만 하고 dispatch_log를 남기지 않았다. 그래서 자동 발송분은
    # 정산매칭 기준 데이터에서 누락됐고, 재고도 차감되지 않았다. 실제 성공한 주문만 기록한다.
    try:
        _track_map = {str(s["productOrderId"]): str(s.get("trackingNumber") or "")
                      for s in ship_data}
        _ok_ids = {str(x) for x in (result.get("success_order_ids") or [])}
        if not _ok_ids and fail == 0:
            _ok_ids = set(_track_map)      # 구버전 응답 폴백 — 전건 성공일 때만
        _rows = []
        for o in orders:
            _ono = str(o.get("상품주문번호") or "").strip()
            if not _ono or _ono not in _ok_ids:
                continue
            _row = dict(o)                 # 한글 키 그대로 — log_dispatch_success가 둘 다 읽음
            _row["tracking_no"] = _track_map.get(_ono, "")
            _row["courier"] = courier_display
            _rows.append(_row)
        if _rows:
            _n = log_dispatch_success(username, _rows,
                                      now.strftime("%Y-%m-%d"), platform="naver")
            log(f"📝 발송이력 저장 {_n}건 (재고 차감 포함)")
    except Exception as _de:
        log(f"⚠️  발송이력/재고 차감 실패: {_de}")

    msg_lines = [
        f"✅ 자동 발송처리 완료",
        f"📅 {now.strftime('%m/%d %H:%M')}",
        f"📦 총 {len(ship_data)}건",
        f"  ✔ 성공: {success}건",
        f"  ✘ 실패: {fail}건",
    ]
    if fail > 0 and result.get("fail_details"):
        msg_lines.append("\n실패 목록:")
        msg_lines += [f"  {d}" for d in result["fail_details"][:5]]
    send_notification(settings, "\n".join(msg_lines), username)

    log("[Task 2] 완료")
    return True


# ── Task 5: 주문 자동 수집 (네이버 + 쿠팡) ──────
def run_fetch_orders_task(username="admin"):
    log("=" * 50)
    log(f"[Task 5] 주문 자동 수집 시작 (사용자: {username})")

    settings = get_user_settings(username)
    if not settings:
        log(f"❌ '{username}' 사용자 DB 없음")
        return False

    all_orders = []
    errors = []

    # ── 네이버 주문 조회 (증분 동기화) ──
    api_id     = settings.get("api_client_id", "")
    api_secret = settings.get("api_client_secret", "")
    if api_id and api_secret:
        # 마지막 동기화 시점 → 증분 hours_back 계산. 자동 수집은 48시간 고정.
        _last_iso = settings.get('last_order_sync', '')
        if _last_iso:
            try:
                _delta = (datetime.now() - datetime.fromisoformat(_last_iso)).total_seconds() / 3600
                _hours = max(48, int(_delta) + 6)
            except Exception:
                _hours = 48
        else:
            _hours = 48  # 첫 실행
        log(f"📋 네이버 주문 조회 중... ({_hours}h 범위)")
        try:
            orders, err = naver_api.get_new_orders(api_id, api_secret,
                                                    hours_back=_hours, status_type="ALL")
            if err:
                log(f"  ⚠️ 네이버 오류: {err}")
                errors.append(f"네이버: {err}")
            elif orders:
                for o in orders:
                    o.setdefault("플랫폼", "네이버")
                all_orders.extend(orders)
                log(f"  ✅ 네이버 {len(orders)}건 조회 완료")
                # 동기화 시점 기록
                try:
                    set_setting(username, 'last_order_sync', datetime.now().isoformat())
                except Exception:
                    pass
            else:
                log("  ℹ️ 네이버 주문 없음")
        except Exception as e:
            log(f"  ❌ 네이버 예외: {e}")
            errors.append(f"네이버: {e}")
    else:
        log("  ⏭ 네이버 API 키 미설정 — 건너뜀")

    # ── 쿠팡 주문 조회 ──
    cpg_access = settings.get("coupang_access_key", "")
    cpg_secret = settings.get("coupang_secret_key", "")
    cpg_vendor = settings.get("coupang_vendor_id", "")
    if cpg_access and cpg_secret and cpg_vendor:
        _cpg_days = 7  # 자동 수집 7일 고정
        log(f"🛒 쿠팡 주문 조회 중... ({_cpg_days}일 범위, ACCEPT+INSTRUCT+DEPARTURE)")
        try:
            sys.path.insert(0, BASE_DIR)
            import coupang_api
            rows, err, _, _ = coupang_api.get_orders(cpg_access, cpg_secret, cpg_vendor,
                                                     status="ALL", days_back=_cpg_days)
            if err:
                log(f"  ⚠️ 쿠팡 오류: {err}")
                errors.append(f"쿠팡: {err}")
            elif rows:
                all_orders.extend(rows)
                log(f"  ✅ 쿠팡 {len(rows)}건 조회 완료")
            else:
                d_from = (datetime.now() - timedelta(days=_cpg_days)).strftime("%Y-%m-%d")
                log(f"  ℹ️ 쿠팡 주문 없음 ({d_from} ~ 오늘, ACCEPT+INSTRUCT)")
        except Exception as e:
            log(f"  ❌ 쿠팡 예외: {e}")
            errors.append(f"쿠팡: {e}")
    else:
        log("  ⏭ 쿠팡 API 키 미설정 — 건너뜀")

    # 정산 매칭 자동 (주문 유무와 무관) — 최근 정산건 수집·역추적 매칭·실정산 반영 (네이버)
    try:
        _sd, _sm, _su = auto_settlement_match(username, api_id, api_secret, days=10)
        if _sm or _su:
            log(f"💳 정산매칭 자동: {_sd}일 처리 / 매칭 {_sm}건 / 실정산 수익반영 {_su}건")
    except Exception as _se:
        log(f"⚠️ 정산매칭 자동 실패(계속): {_se}")

    # 쿠팡 정산 자동 수집 (revenue-history, 최근 40일) → coupang_settlements 저장
    #   (기존엔 정산 페이지 수동 버튼에서만 수집됐음 — 자동 파이프라인에 편입)
    if cpg_access and cpg_secret and cpg_vendor:
        try:
            import coupang_api as _cpapi
            from db import save_coupang_settlements as _save_cps
            # 쿠팡 API 규정: 종료일은 '어제'까지만 허용(오늘/미래 불가) → 400 방지
            _cp_to = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            _cp_from = (datetime.now() - timedelta(days=41)).strftime("%Y-%m-%d")
            _crev, _cerr = _cpapi.get_revenue_history(cpg_access, cpg_secret, cpg_vendor,
                                                      _cp_from, _cp_to)
            if _crev:
                _cn = _save_cps(username, _crev)
                log(f"💳 쿠팡 정산 자동수집: {_cn}건 ({_cp_from}~{_cp_to})")
            elif _cerr:
                log(f"⚠️ 쿠팡 정산 수집 오류(계속): {_cerr}")
        except Exception as _cse:
            log(f"⚠️ 쿠팡 정산 수집 실패(계속): {_cse}")

    # 🤖 AI 정산 브리핑 자동 발송 (설정: anthropic_api_key + ai_briefing_auto='1')
    try:
        _ai_key = settings.get('anthropic_api_key', '')
        if _ai_key and settings.get('ai_briefing_auto', '') == '1':
            from ai_service import generate_settlement_briefing
            _btxt, _berr = generate_settlement_briefing(username, _ai_key)
            if _btxt:
                _sent_b = send_notification(
                    settings,
                    f"🤖 정산 브리핑 ({datetime.now().strftime('%m/%d')})\n\n{_btxt}",
                    username)
                log(f"🤖 AI 정산 브리핑 {'발송 완료' if _sent_b else '발송 실패(카카오 미설정?)'}")
            elif _berr:
                log(f"⚠️ AI 브리핑 생성 실패(계속): {_berr}")
    except Exception as _ae:
        log(f"⚠️ AI 브리핑 실패(계속): {_ae}")

    if not all_orders:
        log("ℹ️ 수집된 주문 없음 → 종료")
        if errors:
            send_notification(settings, "❌ 주문 자동 수집 오류\n" + "\n".join(errors), username)
        return True

    # ── DB 저장 (order_history UPSERT — 매일 누적되어 미발송 주문 추적) ──
    try:
        import pandas as _pd
        from db import save_order_history as _save_hist
        _df = _pd.DataFrame(all_orders)
        # 코스트코 상품가격이 DB에 있는 주문은 구매금액(구입가격)을 매칭해 order_history에 채움
        _cost_df = None
        try:
            from db import get_all_products as _gap
            _uprods_c = _gap(username)
            _cost_rows = []
            for _o in all_orders:
                _nm = str(_o.get('상품명', '') or '')
                if not _nm:
                    continue
                _mp = match_product_to_db(
                    username, _nm, product_no=(str(_o.get('상품번호', '') or '') or None),
                    _user_prods=_uprods_c)
                if not _mp:
                    continue
                _pack_c = extract_pack_qty(str(_o.get('옵션정보', '') or ''), _nm)
                _cost_c = calc_cost(_mp, int(_o.get('수량', 1) or 1), pack_qty=_pack_c)
                if _cost_c > 0:
                    _cost_rows.append({'상품명': _nm,
                                       '수취인명': str(_o.get('수취인명', '') or ''),
                                       '구입가격': _cost_c})
            if _cost_rows:
                _cost_df = _pd.DataFrame(_cost_rows)
                log(f"💰 코스트코 구매금액 매칭: {len(_cost_rows)}건")
        except Exception as _ce:
            log(f"⚠️ 구매금액 매칭 실패(계속): {_ce}")
        saved = _save_hist(username, _df, cost_df=_cost_df)
        log(f"💾 order_history UPSERT: {saved}건")
        # daily_orders(수익계산·홈달력용)에는 '오늘 처리할' 주문만 저장 → 항상 자동 저장.
        #   네이버: 결제완료/발주확인/발송대기 (발송완료 등 제외), 쿠팡: 수집분 전체(신규).
        _ACTIONABLE = {'발주확인', '결제완료', '발송대기'}
        _daily = [o for o in all_orders
                  if o.get('플랫폼') == '쿠팡' or o.get('주문상태', '') in _ACTIONABLE]
        save_daily_orders(username, _daily, settings)
        log(f"💾 daily_orders 자동 저장: {len(_daily)}건 (처리대상)")
        # 수익계산 자동 저장 (profit_settlements) → 홈달력/통계 수익 반영
        try:
            _pn = auto_save_profit(username, datetime.now().strftime("%Y-%m-%d"))
            if _pn:
                log(f"💰 수익계산 자동 저장: {_pn}건 (신규)")
        except Exception as _pe:
            log(f"⚠️ 수익계산 자동 저장 실패(계속): {_pe}")
    except Exception as e:
        log(f"❌ DB 저장 실패: {e}")
        return False

    today = datetime.now().strftime("%m/%d")
    msg = f"📥 주문 자동 수집 완료 ({today})\n총 {len(all_orders)}건"
    if errors:
        msg += "\n⚠️ 오류: " + ", ".join(errors)
    send_notification(settings, msg, username)

    log(f"[Task 5] 완료")
    return True


# ── Task 4: 키워드 순위 자동 체크 ────────────────
def run_rank_check_task(username="admin"):
    log("=" * 50)
    log(f"[Task 4] 키워드 순위 체크 시작 (사용자: {username})")

    settings = get_user_settings(username)
    if not settings:
        log(f"❌ '{username}' 사용자 DB 없음")
        return False

    open_cid  = settings.get('naver_open_client_id', '')
    open_csec = settings.get('naver_open_client_secret', '')
    if not open_cid or not open_csec:
        log("⚠️ 네이버 Open API 키 미설정 — 앱 설정 탭에서 등록 필요")
        return False

    db_path = os.path.join(DATA_DIR, f"{username}.db")
    if not os.path.exists(db_path):
        return False

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        trackings = conn.execute(
            "SELECT * FROM keyword_tracking WHERE active=1"
        ).fetchall()
    except Exception:
        conn.close()
        log("⚠️ 순위 추적 테이블 없음 — 앱에서 키워드를 먼저 추가해주세요.")
        return False
    conn.close()

    if not trackings:
        log("ℹ️ 추적 중인 키워드 없음 → 종료")
        return True

    results = []
    for t in trackings:
        kw       = t['search_keyword']
        prod_kw  = t['product_keyword']
        naver_pno = t['naver_product_no'] or ''
        store_nm  = t['store_name'] or ''

        r_wonbu, r_compare, r_solo, err = naver_api.check_keyword_rank(
            open_cid, open_csec, kw,
            our_product_name=prod_kw,
            naver_product_no=naver_pno,
            store_name=store_nm,
        )
        if err:
            log(f"  '{kw}': 오류 — {err}")
            continue

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("ALTER TABLE rank_history ADD COLUMN rank_compare INTEGER")
        except Exception:
            pass
        conn.execute(
            "INSERT INTO rank_history (tracking_id, rank_price_compare, rank_total, rank_compare, checked_at) VALUES (?,?,?,?,?)",
            (t['id'], r_wonbu, r_solo, r_compare, now)
        )
        conn.commit()
        conn.close()

        wb_s = f"원부 {r_wonbu}위" if r_wonbu else "원부 미발견"
        cp_s = f"가격비교 {r_compare}위" if r_compare else "가격비교 미발견"
        sl_s = f"단독 {r_solo}위" if r_solo else "단독 미발견"
        line = f"  [{prod_kw}] '{kw}': {wb_s} / {cp_s} / {sl_s}"
        log(line)
        results.append(line)

    if results:
        today = datetime.now().strftime("%m/%d")
        msg = f"📈 키워드 순위 업데이트 ({today})\n" + "\n".join(results)
        send_notification(settings, msg, username)

    log(f"[Task 4] 완료 ({len(results)}건 처리)")
    return True


# ── Task 6: 네이버 등록상품 정기수집 (origin+channel 번호/가격 자동 갱신) ──
def run_naver_products_task(username="admin"):
    """네이버 스마트스토어 등록상품 전체를 API로 가져와 제품DB를 갱신.
    origin/channel 상품번호 + 판매가를 최신화 → 주문 매칭 정확도 유지."""
    log("=" * 50)
    log(f"[Task 6] 네이버 등록상품 정기수집 시작 (사용자: {username})")
    settings = get_user_settings(username)
    cid = settings.get("api_client_id", "")
    cs  = settings.get("api_client_secret", "")
    seller = settings.get("channel_seller_id", "")
    if not (cid and cs):
        log("❌ 네이버 API 키 미설정 → 건너뜀")
        return False
    try:
        from db import upsert_user_private
        lst, err = naver_api.get_product_list(cid, cs, seller or "")
        if err and not lst:
            log(f"❌ 상품 조회 실패: {err}")
            return False
        if not lst:
            log("ℹ️ 조회된 상품 없음")
            return True

        def _ns(s):
            s = (s or 'SALE').upper()
            if s in ('OUTOFSTOCK', 'SOLD_OUT', 'SOLDOUT'):
                return 'OUTOFSTOCK'
            if s in ('SUSPENSION', 'STOP', 'PAUSE', 'CLOSE', 'PROHIBITION'):
                return 'SUSPENSION'
            return 'SALE'

        saved = 0
        for p in lst:
            name = (p.get('productName') or '').strip()
            if not name:
                continue
            origin  = str(p.get('originProductNo') or '').strip()
            channel = str(p.get('channelProductNo') or '').strip()
            try:
                upsert_user_private(
                    username, name, name,
                    sale_price=int(p.get('salePrice') or 0),
                    shipping_fee=int(p.get('deliveryFee') or 0),
                    naver_product_no=channel or None,   # channelProductNo
                    naver_origin_pno=origin,             # originProductNo
                    status=_ns(p.get('status')),
                    from_naver=1,
                )
                saved += 1
            except Exception as e:
                log(f"  ⚠️ 저장 오류({name[:20]}): {e}")
        log(f"✅ 네이버 등록상품 {saved}건 수집·갱신 완료 (origin+channel 번호/가격)")
        log("[Task 6] 완료")
        return True
    except Exception as e:
        log(f"❌ 네이버 상품 수집 오류: {e}")
        return False


# ── Task 7: 코스트코 수집상품 → 네이버 자동 등록 ──
def run_naver_register_task(username="admin"):
    """크롤링해 shared_products에 쌓인 미등록 상품을 네이버 스마트스토어에 자동 등록.
    - AI로 카테고리 자동판단(쇼핑검색→suggest), 미해결 건은 등록 안 하고 남겨 UI 장바구니로 회수.
    - 라이브 등록이므로 auto_register_enabled='1' 인 사용자만 실행(안전 opt-in).
    설정 키: auto_register_enabled / auto_register_margin / auto_register_max."""
    log("=" * 50)
    log(f"[Task 7] 네이버 자동 등록 시작 (사용자: {username})")

    settings = get_user_settings(username)
    if not settings:
        log(f"❌ '{username}' 사용자 DB 없음")
        return False

    # ── 안전 게이트: 켜져 있어야만 라이브 등록 진행 ──
    if settings.get("auto_register_enabled", "") != "1":
        log("⏭ auto_register_enabled 미설정 — 자동 등록 건너뜀 (자동화 탭에서 활성화 필요)")
        return True

    api_id = settings.get("api_client_id", "")
    api_secret = settings.get("api_client_secret", "")
    if not api_id or not api_secret:
        log("❌ 네이버 커머스 API 키 미설정 → 건너뜀")
        return False

    try:
        margin = float(settings.get("auto_register_margin") or 10)
    except Exception:
        margin = 10.0
    try:
        max_count = int(settings.get("auto_register_max") or 20)
    except Exception:
        max_count = 20

    open_id = settings.get("naver_open_client_id", "")
    open_secret = settings.get("naver_open_client_secret", "")
    if not (open_id and open_secret):
        log("⚠️ 네이버 Open API(쇼핑검색) 키 미설정 — AI 카테고리 자동판단 불가. "
            "카테고리 매핑/상품 저장값 있는 건만 등록되고 나머지는 장바구니로 남습니다.")
    # AI 키: 관리자 공용키 우선, 없으면 본인 키
    ai_key = get_global_setting("anthropic_api_key") or settings.get("anthropic_api_key", "")
    as_tel = settings.get("naver_as_tel", "") or "1588-1234"

    # 코스트코 카테고리 → 네이버 기본 카테고리 매핑
    cat_map = {}
    try:
        cat_map = json.loads(settings.get("naver_cat_mappings") or "{}")
    except Exception:
        cat_map = {}

    try:
        import naver_register_service
    except ImportError:
        log("❌ naver_register_service.py를 찾을 수 없습니다.")
        return False

    log(f"  마진 {margin:g}% · 회당 최대 {max_count}건 · "
        f"AI키 {'있음' if ai_key else '없음'} · 쇼핑검색 {'가능' if (open_id and open_secret) else '불가'}")

    try:
        _gen_tags = settings.get("auto_register_gen_tags", "1") == "1"
        _ai_name = settings.get("auto_register_ai_name", "1") == "1"
        _ai_desc = settings.get("auto_register_ai_desc", "1") == "1"
        _with_spec = settings.get("auto_register_spec", "1") == "1"
        log(f"  AI 태그 {'ON' if (_gen_tags and ai_key) else 'OFF'} · "
            f"상품명 {'ON' if (_ai_name and ai_key) else 'OFF'} · "
            f"상세설명 {'ON' if (_ai_desc and ai_key) else 'OFF'} · "
            f"한글표시사항 {'ON' if _with_spec else 'OFF'}")
        res = naver_register_service.auto_register(
            username, api_id, api_secret,
            margin=margin, max_count=max_count,
            open_creds=(open_id, open_secret),
            ai_key=ai_key, cat_map=cat_map, as_tel=as_tel,
            gen_tags=_gen_tags, optimize_name=_ai_name,
            ai_desc=_ai_desc, with_spec=_with_spec,
            log=lambda m: log(m),
        )
    except Exception as e:
        import traceback
        log(f"❌ 자동 등록 중 오류: {e}")
        log(traceback.format_exc())
        send_notification(settings, f"❌ 네이버 자동 등록 오류\n{e}", username)
        return False

    today = datetime.now().strftime("%m/%d")
    summary = (
        f"🛍 네이버 자동 등록 완료 ({today})\n"
        f"✅ 등록 {res['ok']}건 / ❌ 실패 {res['fail']}건\n"
        f"⏭ 스킵 — 카테고리미해결 {res['skipped_no_category']} · "
        f"가격없음 {res['skipped_no_price']} · 이미지없음 {res['skipped_no_image']} · "
        f"판매종료/품절 {res.get('skipped_soldout', 0)}"
    )
    if res["skipped_no_category"]:
        summary += "\n※ 카테고리 미해결 건은 앱 '네이버 등록' 탭 장바구니에서 수동 확인하세요."
    log(summary)
    send_notification(settings, summary, username)
    log("[Task 7] 완료")
    return True


def run_cafe24_sync_task(username="admin"):
    """카페24 자체상품코드(코스트코번호)로 매칭된 상품을 코스트코 상태와 동기화.
    - 코스트코 현재가 → 카페24 매입가(supply_price)
    - 코스트코 품절/판매종료 → 카페24 판매중지(selling=F)
    상시 자동 반영용(스케줄 등록). 공용 카페24 자격증명 사용."""
    log("=" * 50)
    log(f"[Task 8] 카페24↔코스트코 동기화 시작 (사용자: {username})")
    settings = get_user_settings(username) or {}

    # ── 스케줄 게이트: 활성화 + 설정 간격 경과 확인(크론은 매시간 실행) ──
    if get_global_setting('cafe24sync_enabled') != '1':
        log("⏭ cafe24sync 비활성 — 건너뜀 (카페24 메뉴에서 활성화 필요)")
        return True
    try:
        _interval_h = float(get_global_setting('cafe24sync_interval_hours') or 3)
    except Exception:
        _interval_h = 3.0
    _last = get_global_setting('cafe24sync_last_run') or ''
    if _last:
        try:
            _lt = datetime.strptime(_last, "%Y-%m-%d %H:%M:%S")
            _elapsed_h = (datetime.now() - _lt).total_seconds() / 3600.0
            if _elapsed_h < _interval_h - 0.05:
                log(f"⏭ 간격 미도달({_elapsed_h:.1f}h < {_interval_h}h) — 건너뜀")
                return True
        except Exception:
            pass
    set_global_setting('cafe24sync_last_run', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    _cf = {k: (get_global_setting('cafe24_' + k) or '') for k in
           ('mall_id', 'client_id', 'client_secret', 'access_token', 'refresh_token', 'token_expires_at')}
    if not (_cf['mall_id'] and _cf['client_id'] and _cf['access_token']):
        log("❌ 공용 카페24 자격증명 없음 → 건너뜀")
        return False
    try:
        import cafe24_api
        import costco_crawler
    except Exception as e:
        log(f"❌ 모듈 로드 실패: {e}")
        return False
    creds = {'mall_id': _cf['mall_id'], 'client_id': _cf['client_id'],
             'client_secret': _cf['client_secret'], 'access_token': _cf['access_token'],
             'refresh_token': _cf['refresh_token'], 'expires_at': _cf['token_expires_at']}

    def _save(t):
        for k, v in (('cafe24_access_token', t.get('access_token', '')),
                     ('cafe24_refresh_token', t.get('refresh_token', '')),
                     ('cafe24_token_expires_at', t.get('expires_at', ''))):
            set_global_setting(k, v)

    prods, err = cafe24_api.get_all_products(creds, save_tokens=_save, max_total=3000)
    if err:
        log(f"❌ 카페24 조회 실패: {err}")
        return False
    matched = [p for p in (prods or []) if str(p.get('custom_product_code') or '').strip()]
    log(f"전체 {len(prods or [])}개 · 코스트코 매칭 {len(matched)}개 동기화")

    n_price = n_stop = n_err = 0
    for p in matched:
        no = str(p.get('custom_product_code') or '').strip()
        c24no = p.get('product_no')
        cs = costco_crawler.fetch_costco_status(no)
        if cs.get('exists') is None:
            continue  # 조회 불확실 → 건너뜀(오탐 방지)
        if cs.get('exists') is False or cs.get('available') is False:
            ok, _e = cafe24_api.update_selling_status(creds, c24no, selling=False, save_tokens=_save)
            if ok:
                n_stop += 1
                log(f"⛔ 판매중지: {str(p.get('product_name'))[:24]} ({cs.get('reason')})")
            else:
                n_err += 1
            continue
        # 판매중 → 매입가 동기화(값이 다를 때만)
        _price = int(cs.get('price') or 0)
        if _price > 0 and _price != int(p.get('supply_price') or 0):
            ok, _e = cafe24_api.update_supply_price(creds, c24no, _price, save_tokens=_save)
            if ok:
                n_price += 1
            else:
                n_err += 1

    summary = (f"🛒 카페24 동기화 완료\n매칭 {len(matched)}건\n"
               f"매입가 갱신 {n_price} · 판매중지 {n_stop} · 실패 {n_err}")
    log(summary)
    send_notification(settings, summary, username)
    log("[Task 8] 완료")
    return True


# ── Task 9: 재고 반품 대상 알림 ───────────────────
RETURN_DAYS = 30


def run_inventory_return_task(username="admin"):
    """입고 후 30일이 지나도 남은 재고를 찾아 알린다.

    코스트코 반품 API는 없다 → 목록만 알리고 실제 반품은 사람이 한다.
    재고 상태를 자동으로 바꾸지 않는다(판매 차감 대상에서 빠지면 안 되므로).

    관리자에게는 전체를, 각 보유자에게는 본인 것만 보낸다.
    """
    now = datetime.now()
    log("=" * 50)
    log(f"[Task 9] 재고 반품 대상 알림 시작 (사용자: {username})")

    try:
        due = get_return_due_lots(days=RETURN_DAYS)
    except Exception as e:
        log(f"❌ 재고 조회 실패: {e}")
        return False
    if not due:
        log(f"ℹ️  {RETURN_DAYS}일 경과 재고 없음 → 종료")
        return True

    user_info = get_user_info(username) or {}
    is_admin = bool(user_info.get('is_admin'))

    def _fmt_lines(rows):
        out = []
        for r in rows[:20]:
            tied = int(r['unit_cost'] or 0) * int(r['qty_left'] or 0)
            out.append(f"• {r['product_name'] or r['product_no']}"
                       f"\n  {r['qty_left']}개 남음 · {r['age_days']}일 경과 · {tied:,}원")
        if len(rows) > 20:
            out.append(f"…외 {len(rows) - 20}건")
        return out

    sent = 0
    # 보유자별 개인 알림
    owners = sorted({str(r['owner']) for r in due})
    for ow in owners:
        rows = [r for r in due if str(r['owner']) == ow]
        st_ = get_user_settings(ow)
        if not st_:
            continue
        msg = [f"↩️ 반품 권장 재고 {len(rows)}건",
               f"📅 {now.strftime('%m/%d')} · 입고 {RETURN_DAYS}일 경과"] + _fmt_lines(rows)
        msg.append("\n코스트코 반품 기한을 확인하세요.")
        try:
            send_notification(st_, "\n".join(msg), ow)
            sent += 1
        except Exception as e:
            log(f"⚠️  {ow} 알림 실패: {e}")

    # 관리자에게 전체 요약
    if is_admin:
        tied_all = sum(int(r['unit_cost'] or 0) * int(r['qty_left'] or 0) for r in due)
        adm = get_user_settings(username)
        if adm:
            msg = [f"↩️ [전체] 반품 권장 {len(due)}건 · 보유자 {len(owners)}명",
                   f"💰 묶인 금액 {tied_all:,}원", ""] + _fmt_lines(due)
            try:
                send_notification(adm, "\n".join(msg), username)
            except Exception as e:
                log(f"⚠️  관리자 알림 실패: {e}")

    log(f"✅ 반품 대상 {len(due)}건 · 보유자 {sent}명에게 알림 발송")
    log("[Task 9] 완료")
    return True


# ── 진입점 ────────────────────────────────────────
def run_naver_stock_sync_task(username="admin", force=False):
    """등록된 네이버 상품 중 코스트코 판매종료/품절 → 네이버 판매중지.
    재입고(다시 판매중) → 우리가 중지한 것만 판매재개. per-user opt-in: naver_stock_sync_enabled='1'.
    force=True면 opt-in 게이트 무시(수동 '지금 실행'용).
    상태 추적: 설정 'naver_auto_stopped'(우리가 자동중지한 origin/channel 번호 목록)."""
    log("=" * 50)
    log(f"[Task 9] 네이버↔코스트코 재고 동기화 시작 (사용자: {username})")
    settings = get_user_settings(username) or {}
    if not force and settings.get("naver_stock_sync_enabled", "") != "1":
        log("⏭ naver_stock_sync_enabled 미설정 — 건너뜀 (자동화 탭에서 활성화 필요)")
        return True
    api_id = settings.get("api_client_id", "")
    api_secret = settings.get("api_client_secret", "")
    if not (api_id and api_secret):
        log("❌ 네이버 커머스 API 키 미설정 → 건너뜀")
        return False
    try:
        import time as _time
        import costco_crawler
        import naver_api
        import json as _json
        from db import get_all_products, get_shared_products, get_setting, set_setting
    except Exception as e:
        log(f"❌ 모듈 로드 실패: {e}")
        return False

    # 코스트코 온라인몰에서 크롤링해 온 제품만 대상 (online_updated_at/online_price = 크롤러가 설정).
    #   매장·소분·기타 경로 제품은 온라인 API 조회 시 오탐(404→판매종료)될 수 있어 제외.
    _online_nos = {
        str(sp.get('product_no') or '').strip()
        for sp in (get_shared_products() or [])
        if str(sp.get('product_no') or '').strip()
        and (str(sp.get('online_updated_at') or '').strip()
             or int(sp.get('online_price') or 0) > 0)
    }

    prods = get_all_products(username) or []
    targets = [p for p in prods
               if str(p.get('product_no') or '').strip() in _online_nos
               and str(p.get('naver_origin_pno') or p.get('naver_channel_pno') or '').strip()]
    log(f"코스트코 온라인 크롤 제품 {len(_online_nos)}종 · 등록된 대상 {len(targets)}개 점검")

    try:
        auto_stopped = set(_json.loads(get_setting(username, 'naver_auto_stopped') or '[]'))
    except Exception:
        auto_stopped = set()
    _reenable = settings.get("naver_stock_reenable", "1") == "1"

    n_stop = n_resume = n_err = 0
    dirty = False
    for p in targets:
        cno = str(p['product_no']).strip()
        pno = str(p.get('naver_origin_pno') or p.get('naver_channel_pno') or '').strip()
        cs = costco_crawler.fetch_costco_status(cno)
        if cs.get('exists') is None:
            continue  # 조회 불확실 → 건너뜀(오탐 방지)
        dead = cs.get('exists') is False or cs.get('available') is False
        if dead and pno not in auto_stopped:
            _time.sleep(0.5)   # 네이버 API 429(요청과다) 방지
            ok, e = naver_api.update_product_status(api_id, api_secret, pno, 'SUSPENSION')
            if ok:
                n_stop += 1
                auto_stopped.add(pno)
                dirty = True
                log(f"⛔ 판매중지: {str(p.get('costco_name'))[:24]} ({cs.get('reason')})")
            else:
                n_err += 1
                log(f"  ❌ 중지실패 {cno}: {e}")
        elif (not dead) and _reenable and pno in auto_stopped:
            _time.sleep(0.5)   # 네이버 API 429 방지
            ok, e = naver_api.update_product_status(api_id, api_secret, pno, 'SALE')
            if ok:
                n_resume += 1
                auto_stopped.discard(pno)
                dirty = True
                log(f"▶ 판매재개: {str(p.get('costco_name'))[:24]} (재입고)")
            else:
                n_err += 1

    if dirty:
        try:
            set_setting(username, 'naver_auto_stopped',
                        _json.dumps(sorted(auto_stopped), ensure_ascii=False))
        except Exception:
            pass

    summary = (f"🔄 네이버 재고 동기화 완료\n"
               f"⛔ 판매중지 {n_stop} · ▶ 판매재개 {n_resume} · ❌ 실패 {n_err} "
               f"(점검 {len(targets)}개)")
    log(summary)
    if n_stop or n_resume or n_err:
        send_notification(settings, summary, username)
    log("[Task 9] 완료")
    return True


def run_hires_image_task(username="admin"):
    """온라인 크롤 제품 대표이미지를 상세 API 하이레스(1200px)로 일괄 교체 (브라우저 불필요)."""
    log("=" * 50)
    log("[하이레스] 대표이미지 재수집 시작")
    try:
        import costco_crawler
    except Exception as e:
        log(f"❌ costco_crawler 로드 실패: {e}")
        return False
    res = costco_crawler.refresh_hires_images(progress_cb=lambda m: log(m))
    log(f"[하이레스] 완료 — 교체 {res['upgraded']} · 이미하이레스 {res['skipped']} · "
        f"실패 {res['failed']} (점검 {res['checked']})")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="코스트코핫딜 자동화 실행")
    parser.add_argument("--task",
                        choices=["shopping", "shipping", "crawl", "rank", "orders",
                                 "products", "register", "cafe24sync", "naverstock",
                                 "hiresimg", "invreturn", "all"],
                        default="all",
                        help="실행할 작업 (기본: all)")
    parser.add_argument("--user",
                        default="admin",
                        help="실행 대상 사용자명 (기본: admin)")
    parser.add_argument("--force", action="store_true",
                        help="opt-in 게이트 무시(수동 실행용, 현재 naverstock에서 사용)")
    args = parser.parse_args()
    # 이 실행의 모든 로그를 해당 사용자 전용 로그에도 기록 (타 사용자 로그 노출 방지)
    set_log_user(args.user)

    if args.task == "shopping":
        run_shopping_task(args.user)
    elif args.task == "shipping":
        run_shipping_task(args.user)
    elif args.task == "crawl":
        run_crawl_task(args.user)
    elif args.task == "rank":
        run_rank_check_task(args.user)
    elif args.task == "orders":
        run_fetch_orders_task(args.user)
    elif args.task == "products":
        run_naver_products_task(args.user)
    elif args.task == "register":
        run_naver_register_task(args.user)
    elif args.task == "cafe24sync":
        run_cafe24_sync_task(args.user)
    elif args.task == "naverstock":
        run_naver_stock_sync_task(args.user, force=args.force)
    elif args.task == "hiresimg":
        run_hires_image_task(args.user)
    elif args.task == "invreturn":
        run_inventory_return_task(args.user)
    else:
        run_fetch_orders_task(args.user)
        run_shopping_task(args.user)
        run_shipping_task(args.user)
        run_rank_check_task(args.user)
        # 네이버 자동 등록은 opt-in(auto_register_enabled='1')일 때만 run_naver_register_task 내부에서 진행
        run_naver_register_task(args.user)
