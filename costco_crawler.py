"""
코스트코 쇼핑몰 크롤러 (www.costco.co.kr)
Playwright 영구 브라우저 프로필 기반
- 최초 1회: 사용자가 브라우저에서 직접 로그인 + OTP 인증 → 프로필 저장
- 이후 실행: 저장된 프로필 재사용 → 코스트코가 기존 브라우저로 인식
- 세션 만료 시: 이메일+비번으로 OTP 없이 자동 재로그인
"""

import re
import json
import sqlite3
import os
import sys
import time
import random
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import quote

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR    = os.path.join(BASE_DIR, "data")
AUTH_DB     = os.path.join(DATA_DIR, "auth.db")
PROFILE_DIR = os.path.join(DATA_DIR, "costco_browser_profile")

COSTCO_BASE      = "https://www.costco.co.kr"
COSTCO_LOGIN_URL = f"{COSTCO_BASE}/login"
COSTCO_HOME_URL  = f"{COSTCO_BASE}/c/cos_10"   # 식품 카테고리 (유효한 URL)
COSTCO_OCC_URL   = f"{COSTCO_BASE}/rest/v2/korea/products/search"

# ─── 카테고리 코드 (실제 cos_* 코드 기반) ─────────────────────────
CATEGORIES = {
    "전체":         f"{COSTCO_BASE}/c/cos_10",    # 식품 (대표)
    "식품":         f"{COSTCO_BASE}/c/cos_10",
    "신선식품":     f"{COSTCO_BASE}/c/cos_10.10",
    "냉동식품":     f"{COSTCO_BASE}/c/cos_10.14",
    "과자/간식":    f"{COSTCO_BASE}/c/cos_10.5",
    "커피/음료":    f"{COSTCO_BASE}/c/cos_10.2",
    "가공식품":     f"{COSTCO_BASE}/c/cos_10.3",
    "생활용품":     f"{COSTCO_BASE}/c/cos_9",
    "세제/청소":    f"{COSTCO_BASE}/c/cos_2.7",
    "화장지":       f"{COSTCO_BASE}/c/cos_2.6",
    "가전/디지털":  f"{COSTCO_BASE}/c/cos_1",
    "주방가전":     f"{COSTCO_BASE}/c/cos_1.9",
    "의류/패션":    f"{COSTCO_BASE}/c/cos_6",
    "스포츠/레저":  f"{COSTCO_BASE}/c/cos_4",
    "캠핑":         f"{COSTCO_BASE}/c/cos_4.2",
    "뷰티/화장품":  f"{COSTCO_BASE}/c/cos_8",
    "건강/영양제":  f"{COSTCO_BASE}/c/cos_12",
    "완구":         f"{COSTCO_BASE}/c/cos_3.5",
    "반려동물":     f"{COSTCO_BASE}/c/cos_10.9",
    "자동차용품":   f"{COSTCO_BASE}/c/cos_9.7",
    "가구/침구":    f"{COSTCO_BASE}/c/cos_2",
    "보석/시계":    f"{COSTCO_BASE}/c/cos_7",
    "커클랜드":     f"{COSTCO_BASE}/c/KirklandSignature",
    "신상품":       f"{COSTCO_BASE}/c/whatsnew",
    "스페셜할인":   f"{COSTCO_BASE}/c/SpecialPriceOffers",
}

# ─── CSS 셀렉터 (사이트 변경 시 여기만 수정) ─────────────────────
SEL = {
    # 로그인 폼
    "email":    "input[type='email'], input[name='email'], input[name='loginId'], #email, input[placeholder*='이메일'], input[placeholder*='아이디']",
    "password": "input[type='password'], input[name='password'], input[name='passwd'], #password",
    "submit":   "button[type='submit'], input[type='submit'], .btn-login, .login-submit, button:has-text('로그인')",
    # 로그인 성공 확인
    "logged_in_marker": ".user-name, .my-account, .account-link, .member-name, a[href*='mypage'], a[href*='logout']",
    # 상품 카드 — SAP Spartacus (Angular) 컴포넌트 + 범용 폴백
    "product_card": (
        "cx-product-list-item, cx-product-grid-item, cx-product-card, "
        "app-product-list-item, .cx-product-container, .cx-item-list-row, "
        ".product-grid-item, .product-list__item, "
        "[data-product-code], [data-code], li.product-item"
    ),
    "name": (
        "cx-product-name a, .cx-product-name a, "
        "cx-product-name, .cx-product-name, "
        "a[href*='/ko/p/'], a[href*='/p/'], "
        ".product-description__name, .product-name, h2.name, h3.name"
    ),
    "price": (
        "cx-product-price .value, cx-product-price .price, "
        "cx-product-price, .cx-price, "
        ".value, .price-value, [itemprop='price'], .formatted-price, .price"
    ),
    "image": (
        "cx-media img, cx-product-image img, "
        "picture source, picture img, "
        ".product-primary-image, img[src*='costco']"
    ),
    "load_more": (
        "cx-pagination button[aria-label='next'], "
        "cx-pagination a[aria-label='다음'], "
        ".cx-pagination .page-link[aria-label='next'], "
        ".js-load-more-btn, .load-more__button, "
        "button[class*='load-more'], button:has-text('더 보기')"
    ),
    # Angular SPA 렌더링 완료 대기용
    "spa_ready": "cx-product-list-item, cx-product-grid-item, cx-product-card",
}


def _log(msg: str, cb: Optional[Callable] = None):
    if cb:
        cb(msg)
    else:
        print(msg)


def _clean_price(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def _extract_item_no(url: str) -> str:
    m = re.search(r"/p/(\w[\w-]*?)(?:\.product)?(?:[?#]|$)", url or "")
    return m.group(1) if m else ""


def _make_persistent_context(pw, headless: bool = True):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
        ],
        viewport={"width": 1280, "height": 900},
        locale="ko-KR",
        extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
    )


def is_profile_exists() -> bool:
    """저장된 브라우저 프로필이 있는지 확인"""
    return os.path.isdir(PROFILE_DIR) and bool(os.listdir(PROFILE_DIR))


def _check_logged_in(page) -> bool:
    """현재 페이지가 로그인 상태인지 확인 — URL 기반 (CSS 셀렉터 불필요)"""
    try:
        url = page.url.lower()
        if not url or "about:blank" in url:
            return False
        # 코스트코 도메인이면서 로그인/인증 페이지가 아닌 경우 → 로그인 완료
        if "costco.co.kr" not in url:
            return False
        login_keywords = ("/login", "/signin", "/auth", "login.do", "signin.do")
        if any(k in url for k in login_keywords):
            return False
        # 비밀번호 입력 필드가 여전히 있으면 로그인 안 된 것
        pw_field = page.query_selector("input[type='password']:visible")
        return pw_field is None
    except Exception:
        return False


def _goto(page, url: str, timeout: int = 30000):
    """페이지 이동 — networkidle 대기 없이 로드 완료 시점에서 반환"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception:
        pass  # 타임아웃/네트워크 오류 무시 — 페이지가 어느 정도 로드됐으면 진행


def _do_login(page, email: str, password: str, log_cb=None) -> bool:
    """자동 로그인 시도 (기존 프로필이므로 OTP 불필요)"""
    try:
        if "/login" not in page.url.lower():
            _goto(page, COSTCO_LOGIN_URL)

        if _check_logged_in(page):
            return True

        # 이메일 입력
        try:
            page.wait_for_selector(SEL["email"], timeout=5000)
            page.fill(SEL["email"], email)
            time.sleep(0.3)
        except Exception:
            _log("  이메일 필드를 찾지 못했습니다.", log_cb)
            return False

        # 비밀번호 입력
        try:
            page.fill(SEL["password"], password)
            time.sleep(0.3)
        except Exception:
            _log("  비밀번호 필드를 찾지 못했습니다.", log_cb)
            return False

        # 로그인 버튼 클릭
        try:
            page.click(SEL["submit"])
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        time.sleep(1)
        return _check_logged_in(page)

    except Exception as e:
        _log(f"  로그인 오류: {e}", log_cb)
        return False


# ─── 첫 로그인 설정 (브라우저 열기) ──────────────────────────────
def setup_browser_profile(email: str = "", password: str = ""):
    """
    최초 1회 실행 — 브라우저를 실제로 열어서 사용자가 직접 로그인.
    OTP 인증 포함. 완료 후 프로필이 저장되어 이후 headless 크롤링에서 재사용.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[오류] playwright 미설치: pip install playwright && python -m playwright install chromium")
        return False

    print()
    print("=" * 55)
    print("  코스트코 브라우저 첫 로그인 설정")
    print("=" * 55)
    print()
    print("  1. 아래 브라우저에서 코스트코에 로그인하세요.")
    print("  2. OTP/인증이 필요하면 완료하세요.")
    print("  3. 로그인 완료되면 이 창에 자동으로 표시됩니다.")
    print()

    success = False

    with sync_playwright() as pw:
        ctx = _make_persistent_context(pw, headless=False)
        page = ctx.new_page()

        try:
            _goto(page, COSTCO_LOGIN_URL)

            # 이메일/비밀번호 자동 채우기 시도 (사용자가 추가 인증만 하도록)
            if email:
                try:
                    page.wait_for_selector(SEL["email"], timeout=3000)
                    page.fill(SEL["email"], email)
                    time.sleep(0.3)
                except Exception:
                    pass
            if password:
                try:
                    page.fill(SEL["password"], password)
                    time.sleep(0.3)
                except Exception:
                    pass

            print("  브라우저가 열렸습니다. 로그인을 완료하세요...")
            print()

            # 1단계: 자동 감지 (30초)
            for i in range(30):
                time.sleep(1)
                try:
                    if _check_logged_in(page):
                        success = True
                        break
                except Exception:
                    pass

            # 2단계: 자동 감지 실패 시 → 사용자가 Enter로 직접 확인
            if not success:
                print()
                print("  ─" * 27)
                print("  로그인을 완료했으면 여기서 Enter 를 누르세요...")
                print("  ─" * 27)
                try:
                    input()
                    # Enter 누른 후 한 번 더 자동 확인
                    time.sleep(1)
                    success = _check_logged_in(page)
                    if not success:
                        # URL만으로 판단 (CSS 감지 실패 대비 최후 수단)
                        url = page.url.lower()
                        success = ("costco.co.kr" in url and "/login" not in url)
                except Exception:
                    pass

        except Exception as e:
            print(f"  오류: {e}")
        finally:
            if success:
                print()
                print("  ✅ 로그인 성공! 브라우저 프로필이 저장되었습니다.")
                print("     이제 앱에서 크롤링을 실행할 수 있습니다.")
                print()
                time.sleep(2)
            else:
                print()
                print("  ❌ 로그인 확인 실패.")
                print("     다시 '브라우저 열어서 코스트코 첫 로그인' 버튼을 누르세요.")
                print()
                time.sleep(3)
            ctx.close()

    return success


# ─── DOM 파싱 헬퍼 ───────────────────────────────────────────────
def _try_json_ld(page) -> list[dict]:
    results = []
    try:
        scripts = page.eval_on_selector_all(
            "script[type='application/ld+json']",
            "els => els.map(e => e.textContent)"
        )
        for s in scripts:
            try:
                data = json.loads(s)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    t = item.get("@type", "")
                    if t == "ItemList":
                        for el in item.get("itemListElement", []):
                            p = el.get("item", el)
                            name = p.get("name", "")
                            price = _clean_price(str(p.get("offers", {}).get("price", 0)))
                            if name and price:
                                results.append({
                                    "name": name, "price": price,
                                    "image_url": p.get("image", ""),
                                    "product_no": p.get("sku", ""),
                                })
                    elif t == "Product":
                        name = item.get("name", "")
                        price = _clean_price(str(item.get("offers", {}).get("price", 0)))
                        if name and price:
                            results.append({
                                "name": name, "price": price,
                                "image_url": item.get("image", ""),
                                "product_no": item.get("sku", ""),
                            })
            except Exception:
                pass
    except Exception:
        pass
    return results


def _parse_dom(page) -> list[dict]:
    results = []
    try:
        cards = page.query_selector_all(SEL["product_card"])
        for card in cards:
            try:
                name_el = card.query_selector(SEL["name"])
                name = (name_el.inner_text() if name_el else "").strip()

                price_el = card.query_selector(SEL["price"])
                price = _clean_price(price_el.inner_text() if price_el else "")

                img_el = card.query_selector(SEL["image"])
                image_url = ""
                if img_el:
                    for attr in ("src", "data-src", "srcset"):
                        val = img_el.get_attribute(attr) or ""
                        if val:
                            image_url = val.split(",")[0].split(" ")[0]
                            break
                if image_url.startswith("//"):
                    image_url = "https:" + image_url

                product_no = card.get_attribute("data-product-code") or ""
                if not product_no:
                    link_el = card.query_selector("a[href*='/ko/p/']")
                    if link_el:
                        href = link_el.get_attribute("href") or ""
                        product_no = _extract_item_no(href)

                if name and price:
                    results.append({
                        "name": name, "price": price,
                        "image_url": image_url, "product_no": product_no,
                    })
            except Exception:
                continue
    except Exception:
        pass
    return results


# ─── API 응답 인터셉터 ────────────────────────────────────────────
def _make_response_handler(api_products: list):
    def handle_response(response):
        try:
            url_lower = response.url.lower()
            # OCC API (/occ/v2/...), PLP, category, search API 모두 포함
            if not any(k in url_lower for k in (
                "product", "search", "catalog", "item",
                "occ", "plp", "/c/", "v2/", "category"
            )):
                return
            if response.status != 200:
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            body = response.json()
            items = []
            if isinstance(body, list):
                items = body
            elif isinstance(body, dict):
                for key in ("products", "results", "items", "data", "content"):
                    val = body.get(key)
                    if isinstance(val, list):
                        items = val
                        break
                    if isinstance(val, dict):
                        for k2 in ("products", "results", "items"):
                            if isinstance(val.get(k2), list):
                                items = val[k2]
                                break
                        if items:
                            break

            for item in items:
                if not isinstance(item, dict):
                    continue
                name = (
                    item.get("name") or item.get("nameKo") or
                    item.get("title") or item.get("productName") or ""
                ).strip()
                price_raw = (
                    item.get("price") or item.get("salePrice") or
                    item.get("sellingPrice") or item.get("currentPrice") or 0
                )
                price = _clean_price(str(price_raw))
                if not name or not price:
                    continue
                image_url = (
                    item.get("imageUrl") or item.get("image") or
                    item.get("thumbnailUrl") or item.get("mainImage") or ""
                )
                product_no = str(
                    item.get("code") or item.get("productCode") or
                    item.get("itemNumber") or item.get("sku") or
                    item.get("productNo") or ""
                )
                api_products.append({
                    "name": name, "price": price,
                    "image_url": image_url, "product_no": product_no,
                })
        except Exception:
            pass
    return handle_response


# ─── OCC API 상품 파싱 ───────────────────────────────────────────
def _parse_occ_products(api_data: dict) -> list[dict]:
    """SAP OCC REST API v2 응답에서 상품 목록 추출"""
    results = []
    products = api_data.get("products", [])
    if not isinstance(products, list):
        return results
    for item in products:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        price_obj = item.get("price", {})
        if isinstance(price_obj, dict):
            price = int(price_obj.get("value", 0) or 0)
        else:
            price = _clean_price(str(price_obj))
        if not name or not price:
            continue
        images = item.get("images", [])
        image_url = ""
        for img in (images or []):
            if isinstance(img, dict):
                if img.get("imageType") == "PRIMARY":
                    raw = img.get("url", "")
                    image_url = (COSTCO_BASE + raw) if raw.startswith("/") else raw
                    break
        if not image_url and images:
            raw = images[0].get("url", "") if isinstance(images[0], dict) else ""
            image_url = (COSTCO_BASE + raw) if raw.startswith("/") else raw
        product_no = str(item.get("code") or "")
        results.append({
            "name": name, "price": price,
            "image_url": image_url, "product_no": product_no,
        })
    return results


# ─── 크롤링 실제 실행 (subprocess에서 직접 호출) ─────────────────
def _crawl_direct(url, email, password, max_products, progress_cb=None):
    """
    subprocess에서 실행. 브라우저 세션 쿠키로 OCC REST API를 직접 fetch().
    DOM/CSS 셀렉터 불필요 — API JSON 응답을 그대로 파싱.
    """
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlparse, parse_qs

    results: list[dict] = []

    _log("  브라우저 시작 중...", progress_cb)
    with sync_playwright() as pw:
        ctx = _make_persistent_context(pw, headless=True)
        page = ctx.new_page()
        _log("  브라우저 시작 완료", progress_cb)

        try:
            _goto(page, COSTCO_HOME_URL)
            time.sleep(2)
            _log(f"  현재 URL: {page.url}", progress_cb)

            if not _check_logged_in(page):
                if not email or not password:
                    raise RuntimeError(
                        "세션이 만료됐습니다.\n"
                        "앱 설정 > 코스트코 계정 설정에서 이메일·비밀번호를 입력하세요."
                    )
                _log("  세션 만료 — 자동 재로그인 중...", progress_cb)
                if not _do_login(page, email, password, progress_cb):
                    raise RuntimeError(
                        "자동 재로그인 실패. '코스트코 브라우저 첫 로그인 설정'을 다시 실행하세요."
                    )
                _log("  재로그인 성공", progress_cb)
            else:
                _log("  로그인 상태 확인 OK", progress_cb)

            # OCC API URL 구성
            parsed = urlparse(url)
            if "search" in parsed.path and parsed.query:
                params = parse_qs(parsed.query)
                keyword = params.get("text", params.get("q", [""]))[0]
                base_api = (
                    f"{COSTCO_OCC_URL}?fields=FULL"
                    f"&query={quote(keyword, safe='')}"
                    f"&lang=ko&curr=KRW"
                )
                _log(f"  검색어: {keyword}", progress_cb)
            else:
                m = re.search(r"/c/([^/?#]+)", parsed.path)
                cat_code = m.group(1) if m else "cos_10"
                base_api = (
                    f"{COSTCO_OCC_URL}?fields=FULL"
                    f"&query=&category={cat_code}"
                    f"&lang=ko&curr=KRW"
                )
                _log(f"  카테고리 코드: {cat_code}", progress_cb)

            # 카테고리 페이지 방문으로 세션 쿠키 확립
            _goto(page, url, timeout=30000)
            time.sleep(2)

            # OCC API 페이지 단위 fetch (pageSize 최대 100)
            page_size = min(max_products, 100)
            current_page = 0
            total_pages = 1

            while len(results) < max_products and current_page < total_pages:
                api_url = f"{base_api}&pageSize={page_size}&currentPage={current_page}"
                _log(f"  OCC API 호출 (page {current_page})...", progress_cb)

                js_code = f"""
                    async () => {{
                        try {{
                            const r = await fetch("{api_url}", {{
                                credentials: "include",
                                headers: {{"Accept": "application/json"}}
                            }});
                            if (!r.ok) return {{error: r.status}};
                            return await r.json();
                        }} catch(e) {{
                            return {{error: String(e)}};
                        }}
                    }}
                """
                api_data = page.evaluate(js_code)

                if not isinstance(api_data, dict):
                    _log(f"  API 응답 오류: {api_data}", progress_cb)
                    break
                if api_data.get("error"):
                    _log(f"  API 오류: {api_data['error']}", progress_cb)
                    break

                page_items = _parse_occ_products(api_data)
                if not page_items:
                    _log(f"  페이지 {current_page}: 상품 없음", progress_cb)
                    break

                results.extend(page_items)
                _log(f"  페이지 {current_page}: {len(page_items)}개 → 누적 {len(results)}개", progress_cb)

                pagination = api_data.get("pagination", {})
                total_pages = pagination.get("totalPages", 1)
                current_page += 1

        finally:
            ctx.close()

    return results[:max_products]


# ─── 카테고리 크롤링 — subprocess로 Playwright 격리 실행 ──────────
def crawl_category(
    url: str,
    email: str = "",
    password: str = "",
    max_products: int = 300,
    progress_cb: Optional[Callable] = None,
) -> list[dict]:
    """
    Playwright를 별도 subprocess에서 실행하여 Streamlit 이벤트 루프 충돌 방지.
    params → temp JSON 파일로 전달, results ← temp JSON 파일로 수신.
    """
    import tempfile, subprocess

    if not is_profile_exists():
        raise RuntimeError(
            "브라우저 프로필이 없습니다.\n"
            "앱 설정에서 '코스트코 브라우저 첫 로그인 설정'을 먼저 실행하세요."
        )

    _log(f"크롤링 시작: {url}", progress_cb)

    params_file = tempfile.mktemp(suffix="_crawl_params.json")
    output_file = tempfile.mktemp(suffix="_crawl_result.json")

    try:
        with open(params_file, "w", encoding="utf-8") as f:
            json.dump({
                "url": url, "email": email,
                "password": password, "max_products": max_products,
            }, f)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"    # 실시간 stdout flush
        env["PYTHONIOENCODING"] = "utf-8"  # subprocess stdout → UTF-8 강제

        proc = subprocess.Popen(
            [sys.executable, __file__, "--do-crawl", params_file, output_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=env,
        )

        # 진행 로그 실시간 전달
        for line in proc.stdout:
            _log(line.rstrip(), progress_cb)

        proc.wait(timeout=600)

        if proc.returncode != 0:
            raise RuntimeError(f"크롤링 프로세스가 오류로 종료됐습니다 (코드 {proc.returncode}).")

        if not os.path.exists(output_file):
            raise RuntimeError("크롤링 결과 파일이 생성되지 않았습니다.")

        with open(output_file, encoding="utf-8") as f:
            results = json.load(f)

        _log(f"수집 완료: {len(results)}개", progress_cb)
        return results

    finally:
        for fp in (params_file, output_file):
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass


def crawl_search(
    keyword: str,
    email: str = "",
    password: str = "",
    max_products: int = 100,
    progress_cb: Optional[Callable] = None,
) -> list[dict]:
    encoded = quote(keyword, safe="")
    # OCC 검색 URL (실제 확인된 패턴)
    search_url = f"{COSTCO_BASE}/search?text={encoded}"
    _log(f"검색 키워드: {keyword}", progress_cb)
    return crawl_category(search_url, email, password, max_products, progress_cb)


# ─── 이미지 다운로드 ──────────────────────────────────────────────
def download_product_image(product_no: str, image_url: str) -> str:
    """
    코스트코 CDN 이미지 → data/images/{product_no}.jpg 저장.
    이미 있으면 재다운로드 없이 기존 경로 반환. 실패 시 빈 문자열 반환.
    """
    if not product_no or not image_url:
        return ""

    images_dir = os.path.join(DATA_DIR, "images")
    os.makedirs(images_dir, exist_ok=True)

    ext = os.path.splitext(image_url.split("?")[0])[-1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    local_path = os.path.join(images_dir, f"{product_no}{ext}")

    if os.path.exists(local_path):
        return local_path

    try:
        import urllib.request
        req = urllib.request.Request(
            image_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.costco.co.kr/",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        with open(local_path, "wb") as f:
            f.write(data)
        return local_path
    except Exception:
        return ""


# ─── DB 저장 ─────────────────────────────────────────────────────
def save_to_shared_products(
    products: list[dict],
    updated_by: str = "crawler",
) -> tuple[int, int]:
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    conn = sqlite3.connect(AUTH_DB)
    saved = updated = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        for col_sql in [
            "ALTER TABLE shared_products ADD COLUMN image_url TEXT DEFAULT ''",
            "ALTER TABLE shared_products ADD COLUMN local_image TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(col_sql)
                conn.commit()
            except Exception:
                pass

        for p in products:
            name = (p.get("name") or "").strip()
            if not name or not p.get("price"):
                continue
            product_no = str(p.get("product_no") or "").strip()
            image_url  = str(p.get("image_url") or "").strip()

            # 이미지 로컬 다운로드
            local_image = download_product_image(product_no, image_url)

            existing = None
            if product_no:
                existing = conn.execute(
                    "SELECT id FROM shared_products WHERE product_no=?", (product_no,)
                ).fetchone()
            if not existing:
                existing = conn.execute(
                    "SELECT id FROM shared_products WHERE match_keyword=?", (name,)
                ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE shared_products
                       SET costco_name=?, unit_price=?, product_no=?,
                           updated_by=?, updated_at=?, price_type='온라인',
                           image_url=?, local_image=?
                       WHERE id=?""",
                    (name, p["price"], product_no, updated_by, now,
                     image_url, local_image, existing[0])
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO shared_products
                       (product_no, costco_name, match_keyword, unit_price,
                        split_qty, updated_by, updated_at, price_type,
                        image_url, local_image)
                       VALUES (?,?,?,?,1,?,?,'온라인',?,?)""",
                    (product_no, name, name, p["price"], updated_by, now,
                     image_url, local_image)
                )
                saved += 1

        conn.commit()
    finally:
        conn.close()

    return saved, updated


# ─── 통합 실행 ───────────────────────────────────────────────────
def run_crawl(
    targets: list[dict],
    email: str = "",
    password: str = "",
    max_products: int = 300,
    progress_cb: Optional[Callable] = None,
    updated_by: str = "crawler",
) -> dict:
    """
    targets 형식:
      [{"type": "category", "name": "식품"},
       {"type": "keyword",  "keyword": "그릭요거트"},
       {"type": "url",      "url": "https://..."}]
    """
    total_crawled = total_new = total_updated = 0
    errors = []

    for t in targets:
        mode = t.get("type", "category")
        try:
            if mode == "category":
                cat_name = t["name"]
                url = CATEGORIES.get(cat_name)
                if not url:
                    errors.append(f"알 수 없는 카테고리: {cat_name}")
                    continue
                label = f"[카테고리: {cat_name}]"
                products = crawl_category(url, email, password, max_products, progress_cb)
            elif mode == "keyword":
                kw = t["keyword"]
                label = f"[키워드: {kw}]"
                products = crawl_search(kw, email, password, max_products, progress_cb)
            elif mode == "url":
                url = t["url"]
                label = f"[URL]"
                products = crawl_category(url, email, password, max_products, progress_cb)
            else:
                continue

            _log(f"{label} {len(products)}개 → DB 저장...", progress_cb)
            new_cnt, upd_cnt = save_to_shared_products(products, updated_by)
            total_crawled += len(products)
            total_new     += new_cnt
            total_updated += upd_cnt
            _log(f"{label} 완료 — 신규 {new_cnt}개 / 업데이트 {upd_cnt}개", progress_cb)

        except Exception as e:
            err_msg = str(e) if str(e) else repr(e)
            err = f"{t.get('name', t.get('keyword', 'URL'))}: {err_msg}"
            errors.append(err)
            _log(f"오류: {err}", progress_cb)

    return {
        "total_crawled": total_crawled,
        "new":           total_new,
        "updated":       total_updated,
        "errors":        errors,
    }


# ─── CLI ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--do-crawl" in sys.argv:
        # app.py의 crawl_category()가 subprocess로 이 모드를 호출
        # 인자: --do-crawl <params_json> <output_json>
        idx = sys.argv.index("--do-crawl")
        _params_file = sys.argv[idx + 1]
        _output_file = sys.argv[idx + 2]
        try:
            with open(_params_file, encoding="utf-8") as _f:
                _p = json.load(_f)
            _results = _crawl_direct(
                url=_p["url"],
                email=_p.get("email", ""),
                password=_p.get("password", ""),
                max_products=_p.get("max_products", 300),
                progress_cb=lambda m: print(m, flush=True),
            )
            with open(_output_file, "w", encoding="utf-8") as _f:
                json.dump(_results, _f, ensure_ascii=False)
            sys.exit(0)
        except Exception as _e:
            print(f"ERROR: {_e}", flush=True)
            sys.exit(1)

    elif "--setup" in sys.argv:
        # 인터랙티브 설정 (터미널에서 직접 실행)
        email    = input("코스트코 이메일: ").strip()
        password = input("비밀번호: ").strip()
        setup_browser_profile(email, password)

    elif "--setup-auto" in sys.argv:
        # 앱에서 subprocess로 호출할 때 — 인자로 이메일/비번 전달
        idx = sys.argv.index("--setup-auto")
        email    = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else ""
        password = sys.argv[idx + 2] if len(sys.argv) > idx + 2 else ""
        setup_browser_profile(email, password)
        # Playwright 내부 스레드가 프로세스를 붙잡아 창이 안 닫히는 현상 방지
        os._exit(0)

    else:
        kw = sys.argv[1] if len(sys.argv) > 1 else "그릭요거트"
        print(f"키워드 크롤링 테스트: '{kw}'")
        result = run_crawl(
            targets=[{"type": "keyword", "keyword": kw}],
            max_products=20,
            progress_cb=print,
        )
        print(f"\n수집: {result['total_crawled']}개 / 신규: {result['new']}개 / 업데이트: {result['updated']}개")
        if result["errors"]:
            print("오류:", result["errors"])
