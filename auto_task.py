"""
코스트코핫딜 자동화 스크립트 v2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Task 1 (shopping) - 장보기 목록 발송
  · 네이버 READY 주문 조회
  · 장보기 목록 생성 → 카카오톡/텔레그램 발송

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
    get_all_settings,
    get_all_products as _db_get_all_products,
    set_setting,
    get_setting,
    get_user_db,
    get_user_info,
    submit_shopping_list,
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
    """카카오 + 텔레그램 둘 다에 '전체' 메시지 발송.
    카카오는 길면(7500자 초과) 자동으로 나눠 전부 발송(잘림 없음)."""
    kakao_token = settings.get("kakao_access_token", "")
    kakao_api_key = settings.get("kakao_api_key", "")
    kakao_refresh = settings.get("kakao_refresh_token", "")
    kakao_secret = settings.get("kakao_client_secret", "")
    tg_token = settings.get("telegram_token", "")
    tg_chat = settings.get("telegram_chat_id", "")

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

    # 카카오 + 텔레그램 둘 다에 '전체' 발송.
    # 카카오는 7500자 초과 시 자동으로 줄 단위 분할해 전부 발송 → 잘림 없음.
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
    if tg_token and tg_chat:
        ok_t, terr = naver_api.send_telegram(tg_token, tg_chat, msg)
        if ok_t:
            sent_any = True
        else:
            log(f"  텔레그램 실패: {terr}")
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
    if not api_id or not api_secret:
        log("❌ 네이버 API 키 미설정 → 앱 설정에서 등록 필요")
        return False

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
    log("📋 주문 조회 중 (결제완료 + 발송대기)...")
    orders, err = [], None
    _seen_ono = set()
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
            log("⚠️ 알림 채널 미설정 (카카오/텔레그램 설정 필요)")

        # ── 관리자에게 장보기 목록 자동 제출 (관리자 카톡 발송은 안 함) ──
        #    관리자는 관리자 페이지 '사용자별 장보기 목록'에서 확인
        try:
            _order_date = now.strftime("%Y-%m-%d")
            submit_shopping_list(username, _order_date, _admin_items,
                                 total_items=len(_admin_items),
                                 total_amount=int(total_cost))
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

    # 정산 매칭 자동 (주문 유무와 무관) — 최근 정산건 수집·역추적 매칭·실정산 반영
    try:
        _sd, _sm, _su = auto_settlement_match(username, api_id, api_secret, days=10)
        if _sm or _su:
            log(f"💳 정산매칭 자동: {_sd}일 처리 / 매칭 {_sm}건 / 실정산 수익반영 {_su}건")
    except Exception as _se:
        log(f"⚠️ 정산매칭 자동 실패(계속): {_se}")

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
        saved = _save_hist(username, _df)
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


# ── 진입점 ────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="코스트코핫딜 자동화 실행")
    parser.add_argument("--task",
                        choices=["shopping", "shipping", "crawl", "rank", "orders", "products", "all"],
                        default="all",
                        help="실행할 작업 (기본: all)")
    parser.add_argument("--user",
                        default="admin",
                        help="실행 대상 사용자명 (기본: admin)")
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
    else:
        run_fetch_orders_task(args.user)
        run_shopping_task(args.user)
        run_shipping_task(args.user)
        run_rank_check_task(args.user)
