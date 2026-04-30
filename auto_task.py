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
import sqlite3, os, sys, argparse, re, json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import naver_api
from utils import fmt, extract_pack_qty

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_PATH = os.path.join(DATA_DIR, "auto_task.log")


# ── 공통 유틸 ─────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except Exception:
        pass
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_global_setting(key, default=''):
    """auth.db app_settings에서 전역 설정값 읽기 (코스트코 이메일/비번 등)"""
    db_path = os.path.join(DATA_DIR, "auth.db")
    if not os.path.exists(db_path):
        return default
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def get_user_settings(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def save_setting(username, key, value):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_all_products(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM products ORDER BY costco_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_daily_orders(username, orders, settings):
    """조회된 주문을 daily_orders 테이블에 저장"""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    shipping_cost = int(settings.get("shipping_cost") or 1800)
    box_cost = int(settings.get("box_cost") or 300)
    products = get_all_products(username)

    def find_unit_price(order):
        pno = str(order.get("상품번호", ""))
        name = order.get("상품명", "")
        if pno:
            for p in products:
                if str(p.get("product_no", "")) == pno:
                    return p["unit_price"]
        for p in products:
            kw = p.get("match_keyword", "")
            if kw and (kw in name or name in p.get("costco_name", "")):
                return p["unit_price"]
        return 0

    db_path = os.path.join(DATA_DIR, f"{username}.db")
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM daily_orders WHERE order_date=?", (today,))
    for o in orders:
        qty = int(o.get("수량", 1))
        unit_price = find_unit_price(o)
        cost = unit_price * qty
        settlement = int(o.get("정산예정금액") or 0)
        ship_fee = int(o.get("배송비 합계") or 0)
        profit = (settlement + ship_fee) - (cost + shipping_cost + box_cost)
        conn.execute(
            """INSERT INTO daily_orders
               (order_date, recipient, product_name, product_no, option_info, qty,
                order_amount, shipping_fee, extra_shipping, settlement,
                cost_price, delivery_cost, box_cost, profit, matched, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (today, o.get("수취인명", ""), o.get("상품명", ""),
             str(o.get("상품번호", "")), o.get("옵션정보", ""), qty,
             int(o.get("최종 상품별 총 주문금액") or 0), ship_fee,
             int(o.get("제주/도서 추가배송비") or 0), settlement,
             cost, shipping_cost, box_cost, profit,
             1 if cost > 0 else 0, now)
        )
    conn.commit()
    conn.close()


def send_notification(settings, msg, username=None):
    """카카오톡 우선, 실패 시 텔레그램 발송"""
    kakao_token = settings.get("kakao_access_token", "")
    kakao_api_key = settings.get("kakao_api_key", "")
    kakao_refresh = settings.get("kakao_refresh_token", "")
    tg_token = settings.get("telegram_token", "")
    tg_chat = settings.get("telegram_chat_id", "")

    if kakao_token:
        ok, err = naver_api.send_kakao(kakao_token, msg,
                                       rest_api_key=kakao_api_key,
                                       refresh_token=kakao_refresh)
        if ok:
            if err and "__TOKEN_REFRESHED__" in str(err) and username:
                parts = str(err).replace("__TOKEN_REFRESHED__", "").split("||")
                try:
                    save_setting(username, "kakao_access_token", parts[0])
                    if len(parts) > 1:
                        save_setting(username, "kakao_refresh_token", parts[1])
                    log("🔄 카카오 토큰 자동 갱신")
                except Exception as e:
                    log(f"⚠️ 카카오 토큰 갱신 저장 실패 (발송은 완료): {e}")
            return True
        log(f"  카카오톡 실패: {err}")

    if tg_token and tg_chat:
        ok, err = naver_api.send_telegram(tg_token, tg_chat, msg)
        if ok:
            return True
        log(f"  텔레그램 실패: {err}")

    return False


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

    log("📋 배송준비(READY) 주문 조회 중...")
    orders, err = naver_api.get_new_orders(api_id, api_secret,
                                           hours_back=48, status_type="READY")
    if err:
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
    except Exception as e:
        log(f"⚠️ 주문 DB 저장 실패 (계속 진행): {e}")

    try:
        # ── 상품별 집계: (상품번호, 상품명, 옵션정보) 조합별로 구분 ──
        from collections import defaultdict
        shopping = defaultdict(lambda: {"주문수량": 0, "상품명": "", "옵션": "", "상품번호": ""})
        for o in orders:
            pno = str(o.get("상품번호", ""))
            name = o.get("상품명", "")
            opt = o.get("옵션정보", "") or ""
            key = (pno, name, opt)
            shopping[key]["주문수량"] += int(o.get("수량", 1))
            shopping[key]["상품명"] = name
            shopping[key]["옵션"] = opt
            shopping[key]["상품번호"] = pno

        products = get_all_products(username)
        total_cost = 0
        total_costco_qty = 0
        lines = [
            f"🛒 코스트코 장보기 목록",
            f"📅 {now.strftime('%Y-%m-%d %H:%M')}",
            f"📦 주문 {len(orders)}건",
            "",
        ]
        for idx, (_, item) in enumerate(
                sorted(shopping.items(), key=lambda x: x[1]["상품명"]), 1):
            name = item["상품명"]
            order_qty = item["주문수량"]
            opt = item["옵션"]
            pno = item["상품번호"]

            pack = extract_pack_qty(opt, name)
            costco_qty = order_qty * pack

            unit_price = None
            for p in products:
                if pno and str(p.get("product_no", "")) == pno:
                    unit_price = p["unit_price"]
                    break
            if not unit_price:
                for p in products:
                    kw = p.get("match_keyword", "")
                    if kw and (kw in name or name in p.get("costco_name", "")):
                        unit_price = p["unit_price"]
                        break

            if unit_price:
                total_cost += unit_price * costco_qty
            total_costco_qty += costco_qty

            opt_str = f" ({opt[:15]})" if opt else ""
            name_short = name[:22]
            if pack > 1:
                qty_str = f"{costco_qty}개 (주문{order_qty}건×{pack}구)"
            else:
                qty_str = f"{costco_qty}개"
            price_str = f" @{fmt(unit_price)}" if unit_price else ""
            lines.append(f"{idx}. {name_short}{opt_str} × {qty_str}{price_str}")

        lines.append("")
        if total_cost > 0:
            lines.append(f"💰 예상 구매액: {fmt(total_cost)}원")
        lines.append(f"🛒 코스트코 총 {total_costco_qty}개 구매 필요")

        msg = "\n".join(lines)
        log(msg)

        if send_notification(settings, msg, username):
            log("✅ 알림 전송 완료")
        else:
            log("⚠️ 알림 채널 미설정 (카카오/텔레그램 설정 필요)")

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

    # ── 송장 파일(.xls) 저장 ──
    try:
        import xlwt
        wb = xlwt.Workbook(encoding="utf-8")
        ws = wb.add_sheet("발송처리")
        for ci, h in enumerate(["상품주문번호", "배송방법", "택배사", "송장번호"]):
            ws.write(0, ci, h)
        for ri, row in enumerate(ship_data, 1):
            ws.write(ri, 0, str(row["productOrderId"]))
            ws.write(ri, 1, "택배,등기,소포")
            ws.write(ri, 2, str(row["택배사"]))
            ws.write(ri, 3, str(row["trackingNumber"]))
        fname = f"발송처리_{now.strftime('%Y%m%d_%H%M')}.xls"
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


# ── 진입점 ────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="코스트코핫딜 자동화 실행")
    parser.add_argument("--task",
                        choices=["shopping", "shipping", "crawl", "all"],
                        default="all",
                        help="실행할 작업 (기본: all)")
    parser.add_argument("--user",
                        default="admin",
                        help="실행 대상 사용자명 (기본: admin)")
    args = parser.parse_args()

    if args.task == "shopping":
        run_shopping_task(args.user)
    elif args.task == "shipping":
        run_shipping_task(args.user)
    elif args.task == "crawl":
        run_crawl_task(args.user)
    else:
        run_crawl_task(args.user)
        run_shopping_task(args.user)
        run_shipping_task(args.user)
