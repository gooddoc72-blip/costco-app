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


# ─── 이미지 포맷 우선순위 (작을수록 큰 이미지) ───────────────────
# 코스트코 실측: superZoom/zoom=1200px, product=740, results/carousel=350,
# thumbnail/cartIcon=160. 목록 API(products/search)는 thumbnail/results만 주고,
# 1200px superZoom은 상세 API(products/{code})에만 있다 → 흐릿함의 근본 원인.
_IMG_FMT = {"superzoom": 0, "zoom": 0, "product": 1,
            "carousel": 2, "results": 3, "thumbnail": 4, "carticon": 5}


def _pick_best_images(images: list) -> tuple[str, list]:
    """images 배열 → (대표 URL, 나머지 URL 리스트). 포맷별 최고해상도 1장씩만.

    같은 사진(imageType+galleryIndex)의 여러 포맷 중 가장 큰 것을 고른다.
    webp는 동해상도라 살짝 후순위(+0.5)로 두어 jpg를 기본 선호.
    """
    best = {}
    for img in (images or []):
        if not isinstance(img, dict):
            continue
        raw = img.get("url", "")
        if not raw:
            continue
        u = (COSTCO_BASE + raw) if raw.startswith("/") else raw
        fmt = str(img.get("format", "")).lower()
        score = _IMG_FMT.get(fmt.replace("-webp", ""), 9) + (0.5 if "webp" in fmt else 0.0)
        key = (img.get("imageType", "PRIMARY"), img.get("galleryIndex", 0))
        if key not in best or score < best[key][0]:
            best[key] = (score, u)
    primary = [v[1] for k, v in sorted(best.items()) if k[0] == "PRIMARY"]
    gallery = [v[1] for k, v in sorted(best.items()) if k[0] == "GALLERY"]
    ordered = primary + gallery
    if not ordered:
        return "", []
    return ordered[0], [u for u in ordered[1:] if u and u != ordered[0]]


def fetch_hires_images(product_no: str) -> tuple[str, list]:
    """상세 API(products/{code})에서 superZoom(1200px) 대표+갤러리 이미지 조회.

    목록 API에는 큰 이미지가 없어, 흐릿하면 이걸로 보완한다.
    urllib 직접 호출(브라우저 불필요). 실패 시 ("", []).
    """
    pno = str(product_no or "").strip()
    if not pno:
        return "", []
    import urllib.request
    url = f"{COSTCO_BASE}/rest/v2/korea/products/{pno}?fields=FULL&lang=ko&curr=KRW"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": COSTCO_BASE + "/",
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            if getattr(resp, "status", 200) != 200:
                return "", []
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return "", []
    return _pick_best_images(data.get("images") or [])


def _is_lowres_thumb(url: str) -> bool:
    """목록 API가 준 저해상도 썸네일 URL인지(=상세 API로 보완이 필요한지) 판단.

    코스트코 URL 자체엔 크기 표시가 없어, 파일 크기가 아니라 '큰 포맷을 못 골랐다'는
    사실로 판단한다. _pick_best_images가 thumbnail(4)/results(3)밖에 못 골랐으면
    큰 이미지가 목록에 안 온 것 → 보완 대상. (호출측에서 원본 images로 판정)
    """
    return not url


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
        product_no = str(item.get("code") or "")
        # 목록 API 이미지 중 최고해상도 선택
        image_url, extra_imgs = _pick_best_images(images)
        # 목록 API엔 큰 포맷(superZoom 1200px)이 오지 않는다 → 큰 게 없으면 상세 API로 보완.
        # 판정: superZoom/zoom(1200px)이 없으면 보완 대상. product(740px)도 네이버
        # 1000×1000 등록엔 살짝 늘어나므로 superZoom을 받는 게 낫다.
        _fmts = {str(i.get("format", "")).lower().replace("-webp", "")
                 for i in images if isinstance(i, dict)}
        _has_big = bool(_fmts & {"superzoom", "zoom"})
        if product_no and not _has_big:
            _hi_main, _hi_extra = fetch_hires_images(product_no)
            if _hi_main:
                image_url = _hi_main
                extra_imgs = _hi_extra
        # 상세설명(설명·요약) → 상세HTML(설명만; 이미지는 등록 시 네이버 CDN 업로드해 사용)
        _desc = (item.get("description") or "").strip()
        _summ = (item.get("summary") or "").strip()
        _feats = [_summ] if _summ else []
        _detail = build_detail_html([], _desc, _feats) if (_desc or _feats) else ""
        results.append({
            "name": name, "price": price,
            "image_url": image_url, "product_no": product_no,
            "extra_images": extra_imgs, "detail_html": _detail,
        })
    return results


# ─── 크롤링 실제 실행 (subprocess에서 직접 호출) ─────────────────
def _safe_handle(handler, response):
    try:
        handler(response)
    except Exception:
        pass


def _crawl_direct(url, email, password, max_products, progress_cb=None):
    """
    subprocess에서 실행.
    ① OCC REST API 직접 호출 (여러 엔드포인트 시도)
    ② 실패 시 네트워크 인터셉터로 수집된 응답 사용
    ③ 그래도 없으면 DOM 스크래핑 + JSON-LD 폴백
    """
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlparse, parse_qs

    results: list[dict] = []
    intercepted: list[dict] = []   # 네트워크 인터셉터 폴백

    _log("  브라우저 시작 중...", progress_cb)
    with sync_playwright() as pw:
        ctx = _make_persistent_context(pw, headless=True)
        page = ctx.new_page()
        _log("  브라우저 시작 완료", progress_cb)

        # ① 네트워크 인터셉터 — 페이지 탐색 전에 등록
        _handler = _make_response_handler(intercepted)
        page.on("response", lambda r: _safe_handle(_handler, r))

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

            # URL 파싱 (원본 URL 기반으로 검색어/카테고리 코드 추출)
            parsed = urlparse(url)
            is_search = "search" in parsed.path and bool(parsed.query)
            if is_search:
                params  = parse_qs(parsed.query)
                keyword = params.get("text", params.get("q", [""]))[0]
                _log(f"  검색어: {keyword}", progress_cb)
            else:
                m = re.search(r"/c/([^/?#]+)", parsed.path)
                cat_code = m.group(1) if m else "cos_10"
                _log(f"  카테고리 코드: {cat_code}", progress_cb)

            # ② 카테고리 페이지 방문 (인터셉터가 자동으로 API 응답 수집)
            _goto(page, url, timeout=30000)
            _log(f"  이동 후 URL: {page.url}", progress_cb)
            time.sleep(4)   # Angular SPA 렌더링 대기

            # ③ OCC API 직접 호출 — 여러 엔드포인트 순서대로 시도
            _occ_bases = [
                f"{COSTCO_BASE}/rest/v2/korea/products/search",
                f"{COSTCO_BASE}/occ/v2/korea/products/search",
                f"{COSTCO_BASE}/rest/v2/costco_kr/products/search",
                f"{COSTCO_BASE}/rest/v2/costco/products/search",
            ]
            page_size = min(max_products, 100)

            for _base in _occ_bases:
                if results:
                    break
                if is_search:
                    base_api = (f"{_base}?fields=FULL"
                                f"&query={quote(keyword, safe='')}&lang=ko&curr=KRW")
                else:
                    base_api = (f"{_base}?fields=FULL"
                                f"&query=&category={cat_code}&lang=ko&curr=KRW")

                cur_pg = 0
                tot_pg = 1
                endpoint_label = _base.split("/rest/v2/")[-1].split("/occ/v2/")[-1]
                while len(results) < max_products and cur_pg < tot_pg:
                    api_url = f"{base_api}&pageSize={page_size}&currentPage={cur_pg}"
                    _log(f"  OCC({endpoint_label}) page {cur_pg}...", progress_cb)
                    js_code = f"""
                        async () => {{
                            try {{
                                const r = await fetch("{api_url}", {{
                                    credentials: "include",
                                    headers: {{"Accept": "application/json",
                                               "X-Requested-With": "XMLHttpRequest"}}
                                }});
                                if (!r.ok) return {{error: r.status}};
                                return await r.json();
                            }} catch(e) {{
                                return {{error: String(e)}};
                            }}
                        }}
                    """
                    api_data = page.evaluate(js_code)
                    if not isinstance(api_data, dict) or api_data.get("error"):
                        _log(f"    오류: {api_data}", progress_cb)
                        break
                    page_items = _parse_occ_products(api_data)
                    if not page_items:
                        _log(f"    상품 없음 — 다음 엔드포인트 시도", progress_cb)
                        break
                    results.extend(page_items)
                    _log(f"    page {cur_pg}: {len(page_items)}개 → 누적 {len(results)}개", progress_cb)
                    pagination = api_data.get("pagination", {})
                    tot_pg = pagination.get("totalPages", 1)
                    cur_pg += 1

            # ④ 폴백: 인터셉터로 수집된 응답 사용
            if not results and intercepted:
                seen: set = set()
                for item in intercepted:
                    key = item.get("product_no") or item.get("name", "")
                    if key and key not in seen:
                        seen.add(key)
                        results.append(item)
                _log(f"  인터셉터 폴백: {len(results)}개 수집", progress_cb)

            # ⑤ 폴백: DOM 스크래핑
            if not results:
                _log("  DOM 스크래핑 시도...", progress_cb)
                try:
                    page.wait_for_selector(SEL["spa_ready"], timeout=10000)
                    time.sleep(2)
                except Exception:
                    pass
                dom_items = _parse_dom(page) or _try_json_ld(page)
                if dom_items:
                    results = dom_items
                    _log(f"  DOM 폴백: {len(results)}개 수집", progress_cb)
                else:
                    _log("  ❌ 모든 방법 실패 — 상품 수집 불가", progress_cb)

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

    # 이미 있고 '충분히 큰' 파일이면 재다운로드 안 함.
    # 과거 160px 썸네일로 캐시된 것(약 4KB)은 다시 받아 고해상도로 교체한다.
    # (superZoom 1200px = 보통 50KB 이상, 160px 썸네일 = 5KB 미만)
    _LOWRES_BYTES = 15_000
    if os.path.exists(local_path):
        try:
            if os.path.getsize(local_path) >= _LOWRES_BYTES:
                return local_path
        except OSError:
            return local_path   # 크기 확인 불가 시 기존 파일 유지

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
        # 새로 받은 게 기존보다 작거나 비었으면 덮어쓰지 않음(품질 하락 방지)
        if not data:
            return local_path if os.path.exists(local_path) else ""
        if os.path.exists(local_path):
            try:
                if len(data) < os.path.getsize(local_path):
                    return local_path
            except OSError:
                pass
        with open(local_path, "wb") as f:
            f.write(data)
        return local_path
    except Exception:
        return local_path if os.path.exists(local_path) else ""


# ─── DB 저장 ─────────────────────────────────────────────────────
def save_to_shared_products(
    products: list[dict],
    updated_by: str = "crawler",
    category: str = "",
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
            "ALTER TABLE shared_products ADD COLUMN category TEXT DEFAULT ''",
            "ALTER TABLE shared_products ADD COLUMN extra_images TEXT DEFAULT ''",
            "ALTER TABLE shared_products ADD COLUMN detail_html TEXT DEFAULT ''",
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
                # 크롤러 → 온라인가만 갱신 (매장가 보존)
                conn.execute(
                    """UPDATE shared_products
                       SET costco_name=?, unit_price=?, product_no=?,
                           updated_by=?, updated_at=?, price_type='온라인',
                           image_url=?, local_image=?, category=?,
                           online_price=?, online_updated_at=?
                       WHERE id=?""",
                    (name, p["price"], product_no, updated_by, now,
                     image_url, local_image, category,
                     p["price"], now, existing[0])
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO shared_products
                       (product_no, costco_name, match_keyword, unit_price,
                        split_qty, updated_by, updated_at, price_type,
                        image_url, local_image, category,
                        store_price, online_price, store_updated_at, online_updated_at)
                       VALUES (?,?,?,?,1,?,?,'온라인',?,?,?,0,?,'',?)""",
                    (product_no, name, name, p["price"], updated_by, now,
                     image_url, local_image, category,
                     p["price"], now)
                )
                saved += 1

            # 상세(설명·추가이미지) 함께 저장 — 검색 API가 제공한 경우 (같은 conn UPDATE)
            _px = p.get("extra_images")
            _pd = p.get("detail_html")
            if product_no and (_px or _pd):
                try:
                    conn.execute(
                        "UPDATE shared_products SET extra_images=?, detail_html=? "
                        "WHERE product_no=?",
                        (json.dumps(_px or [], ensure_ascii=False), _pd or "", product_no),
                    )
                except Exception:
                    pass

        conn.commit()
    finally:
        conn.close()

    return saved, updated


# ─── 상품 상세 수집 ──────────────────────────────────────────────

def build_detail_html(image_urls: list, description: str = "", features: list = None) -> str:
    """코스트코 상품 정보 → 네이버 상세페이지 HTML (이미지는 원본 URL 그대로 저장)"""
    parts = []
    for url in (image_urls or []):
        if url:
            parts.append(
                f'<img src="{url}" '
                f'style="max-width:100%;display:block;margin:8px auto">'
            )
    if description:
        parts.append('<div style="padding:16px 0">')
        for line in description.split('\n'):
            line = line.strip()
            if line:
                parts.append(f'<p style="margin:6px 0;line-height:1.7">{line}</p>')
        parts.append('</div>')
    if features:
        valid = [f.strip() for f in features if isinstance(f, str) and f.strip()]
        if valid:
            parts.append('<ul style="padding-left:20px;margin:8px 0">')
            for feat in valid:
                parts.append(f'<li style="margin:4px 0;line-height:1.6">{feat}</li>')
            parts.append('</ul>')
    return '\n'.join(parts)


def fetch_costco_spec(product_no: str) -> dict:
    """코스트코 상세 API(classifications)에서 한글표시사항 스펙을 dict로 추출.
    urllib 직접 호출(브라우저 불필요). 반환: {필드명: 값}. 실패 시 {}.
    예: {'제조자/수입자':'영인정공','제조국 또는 원산지':'대한민국','A/S ...':'... 032-...'}"""
    pno = str(product_no or "").strip()
    if not pno:
        return {}
    import urllib.request
    url = f"{COSTCO_BASE}/rest/v2/korea/products/{pno}?fields=FULL&lang=ko&curr=KRW"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            if getattr(resp, "status", 200) != 200:
                return {}
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}
    spec = {}
    for c in (data.get("classifications") or []):
        for f in (c.get("features") or []):
            nm = str(f.get("name") or "").strip()
            vals = [str((x or {}).get("value", "")).strip() for x in (f.get("featureValues") or [])]
            vals = [v for v in vals if v]
            if nm and vals:
                spec[nm] = ", ".join(vals)
    return spec


def fetch_costco_status(product_no: str) -> dict:
    """코스트코 상세 API로 상품 상태·가격 조회 (브라우저 불필요).
    반환: {exists, available, price, reason}.
      exists=False → 판매종료(삭제/404). available=False → 품절.
      네트워크 오류 등 불확실 시 exists=None → 호출측에서 건너뜀(오탐 방지)."""
    pno = str(product_no or "").strip()
    if not pno:
        return {"exists": None, "available": None, "price": 0, "reason": "번호없음"}
    import urllib.request, urllib.error
    url = f"{COSTCO_BASE}/rest/v2/korea/products/{pno}?fields=FULL&lang=ko&curr=KRW"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            if getattr(resp, "status", 200) != 200:
                return {"exists": None, "available": None, "price": 0, "reason": "조회실패"}
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if getattr(e, "code", 0) in (404, 410):
            return {"exists": False, "available": False, "price": 0, "reason": "판매종료"}
        return {"exists": None, "available": None, "price": 0, "reason": f"HTTP{getattr(e, 'code', '')}"}
    except Exception:
        return {"exists": None, "available": None, "price": 0, "reason": "조회오류"}
    if not data or not data.get("code"):
        return {"exists": False, "available": False, "price": 0, "reason": "판매종료"}
    price_obj = data.get("price", {})
    price = (int(price_obj.get("value", 0) or 0) if isinstance(price_obj, dict)
             else _clean_price(str(price_obj)))
    stock = data.get("stock", {}) or {}
    status = str(stock.get("stockLevelStatus", "")).lower()
    purchasable = data.get("purchasable")
    if status in ("outofstock", "out_of_stock") or purchasable is False:
        return {"exists": True, "available": False, "price": price, "reason": "품절"}
    return {"exists": True, "available": True, "price": price, "reason": "판매중"}


def build_spec_table_html(spec: dict) -> str:
    """한글표시사항 스펙 dict → 상세페이지용 '제품 상세정보' 표 HTML. 빈 값 생략."""
    if not spec:
        return ""
    import html as _h
    rows = []
    for k, v in spec.items():
        v = str(v or "").strip()
        if not v:
            continue
        rows.append(
            '<tr><th style="background:#f5f5f5;border:1px solid #ddd;padding:10px 12px;'
            'text-align:center;width:32%;font-weight:700;color:#333;white-space:nowrap">'
            + _h.escape(str(k)) + '</th>'
            '<td style="border:1px solid #ddd;padding:10px 12px;text-align:left;'
            'color:#333;line-height:1.6">' + _h.escape(v) + '</td></tr>')
    if not rows:
        return ""
    return ('<div style="max-width:720px;margin:28px auto 8px;padding:0 12px">'
            '<div style="font-size:20px;font-weight:800;text-align:center;'
            'padding:12px 0;color:#222">제품 상세정보</div>'
            '<table style="width:100%;border-collapse:collapse;font-size:15px">'
            + "".join(rows) + "</table></div>")


def save_product_detail(product_no: str, extra_images: list, detail_html: str) -> bool:
    """shared_products에 extra_images / detail_html 업데이트"""
    if not os.path.exists(AUTH_DB):
        return False
    try:
        conn = sqlite3.connect(AUTH_DB)
        for sql in [
            "ALTER TABLE shared_products ADD COLUMN extra_images TEXT DEFAULT ''",
            "ALTER TABLE shared_products ADD COLUMN detail_html TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(sql); conn.commit()
            except Exception:
                pass
        conn.execute(
            "UPDATE shared_products SET extra_images=?, detail_html=? WHERE product_no=?",
            (json.dumps(extra_images, ensure_ascii=False), detail_html, product_no),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def _fetch_detail_direct(product_nos: list, email: str, password: str, progress_cb=None) -> dict:
    """subprocess에서 실행 — OCC 단품 API로 갤러리 이미지 + 설명 + 특징 수집"""
    from playwright.sync_api import sync_playwright

    result: dict = {}
    _log("  브라우저 시작...", progress_cb)
    with sync_playwright() as pw:
        ctx = _make_persistent_context(pw, headless=True)
        page = ctx.new_page()
        _log("  브라우저 완료", progress_cb)
        try:
            _goto(page, COSTCO_HOME_URL)
            time.sleep(2)
            if not _check_logged_in(page):
                if not email or not password:
                    raise RuntimeError("세션 만료 — 이메일/비밀번호 설정 필요")
                if not _do_login(page, email, password, progress_cb):
                    raise RuntimeError("재로그인 실패")
                _log("  재로그인 성공", progress_cb)

            # 코스트코 카테고리 페이지를 먼저 방문해 세션 쿠키 확립
            _goto(page, COSTCO_HOME_URL, timeout=20000)
            time.sleep(2)

            for i, pno in enumerate(product_nos, 1):
                _log(f"  [{i}/{len(product_nos)}] {pno} 상세 조회...", progress_cb)
                api_url = (
                    f"{COSTCO_BASE}/rest/v2/korea/products/{pno}"
                    f"?fields=FULL&lang=ko&curr=KRW"
                )
                js = f"""
                    async () => {{
                        try {{
                            const r = await fetch("{api_url}", {{
                                credentials: "include",
                                headers: {{"Accept": "application/json"}}
                            }});
                            if (!r.ok) return {{error: r.status}};
                            return await r.json();
                        }} catch(e) {{ return {{error: String(e)}}; }}
                    }}
                """
                data = page.evaluate(js)
                if not isinstance(data, dict) or data.get("error"):
                    _log(f"    오류: {data}", progress_cb)
                    continue

                # 이미지 추출 (PRIMARY → GALLERY 순, 중복 제거)
                seen_urls: set = set()
                img_urls: list = []
                for img in (data.get("images") or []):
                    if not isinstance(img, dict):
                        continue
                    if img.get("imageType") not in ("PRIMARY", "GALLERY"):
                        continue
                    raw = img.get("url", "")
                    if not raw:
                        continue
                    url = (COSTCO_BASE + raw) if raw.startswith("/") else raw
                    if url not in seen_urls:
                        seen_urls.add(url)
                        img_urls.append(url)

                # 설명 추출
                desc = (data.get("description") or "").strip()

                # 특징 추출 (featureList + summary)
                features: list = []
                summary = (data.get("summary") or "").strip()
                if summary:
                    features.append(summary)
                for feat in (data.get("featureList") or []):
                    if not isinstance(feat, dict):
                        continue
                    for fv in (feat.get("featureValues") or []):
                        if isinstance(fv, dict):
                            val = (fv.get("value") or "").strip()
                            if val and val not in features:
                                features.append(val)

                result[pno] = {
                    "extra_images": img_urls,
                    "description":  desc,
                    "features":     features,
                }
                _log(f"    → 이미지 {len(img_urls)}개, 특징 {len(features)}개", progress_cb)
                time.sleep(random.uniform(0.4, 1.0))

        finally:
            ctx.close()
    return result


def crawl_product_details(
    product_nos: list,
    email: str = "",
    password: str = "",
    progress_cb=None,
) -> dict:
    """
    Playwright subprocess로 상세 수집 → DB 저장.
    반환: {"ok": N, "fail": N, "errors": [...]}
    """
    import tempfile, subprocess

    if not is_profile_exists():
        raise RuntimeError(
            "브라우저 프로필이 없습니다.\n'코스트코 브라우저 첫 로그인 설정'을 먼저 실행하세요."
        )
    if not product_nos:
        return {"ok": 0, "fail": 0, "errors": []}

    params_file = tempfile.mktemp(suffix="_detail_params.json")
    output_file = tempfile.mktemp(suffix="_detail_result.json")

    try:
        with open(params_file, "w", encoding="utf-8") as f:
            json.dump({"product_nos": product_nos,
                       "email": email, "password": password}, f)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        proc = subprocess.Popen(
            [sys.executable, __file__, "--do-detail", params_file, output_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
        )
        for line in proc.stdout:
            _log(line.rstrip(), progress_cb)
        proc.wait(timeout=1800)

        if not os.path.exists(output_file):
            raise RuntimeError("상세 결과 파일 생성 실패")

        with open(output_file, encoding="utf-8") as f:
            detail_map = json.load(f)

        ok = fail = 0
        errors = []
        for pno, detail in detail_map.items():
            extra = detail.get("extra_images", [])
            html  = build_detail_html(
                extra,
                detail.get("description", ""),
                detail.get("features", []),
            )
            if save_product_detail(pno, extra, html):
                ok += 1
            else:
                fail += 1
                errors.append(f"{pno}: DB 저장 실패")

        _log(f"상세 수집 완료 — 성공 {ok}개 / 실패 {fail}개", progress_cb)
        return {"ok": ok, "fail": fail, "errors": errors}

    finally:
        for fp in (params_file, output_file):
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass


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

            cat_label = cat_name if mode == "category" else (kw if mode == "keyword" else "")
            _log(f"{label} {len(products)}개 → DB 저장...", progress_cb)
            new_cnt, upd_cnt = save_to_shared_products(products, updated_by, category=cat_label)
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


def refresh_hires_images(progress_cb=None, limit=None) -> dict:
    """온라인 크롤 제품의 대표 이미지를 상세 API superZoom(1200px)으로 일괄 교체.
    브라우저 불필요(urllib 상세 API). 이미 하이레스면 건너뜀(URL 동일).
    반환: {checked, upgraded, failed, skipped}."""
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, product_no, image_url FROM shared_products "
        "WHERE TRIM(COALESCE(product_no,'')) != '' "
        "AND (TRIM(COALESCE(online_updated_at,'')) != '' OR COALESCE(online_price,0) > 0)"
    ).fetchall()
    conn.close()
    rows = list(rows)
    if limit:
        rows = rows[:int(limit)]
    total = len(rows)
    _log(f"하이레스 재수집 대상 {total}개 (온라인 크롤 제품)", progress_cb)
    checked = upgraded = failed = skipped = 0
    for i, r in enumerate(rows):
        pno = str(r["product_no"]).strip()
        cur_url = str(r["image_url"] or "")
        checked += 1
        try:
            hi_main, hi_extra = fetch_hires_images(pno)
        except Exception:
            hi_main, hi_extra = "", []
        if not hi_main:
            failed += 1
        elif hi_main == cur_url:
            skipped += 1   # 이미 하이레스
        else:
            try:
                local = download_product_image(pno, hi_main)
                c2 = sqlite3.connect(AUTH_DB)
                c2.execute(
                    "UPDATE shared_products SET image_url=?, local_image=?, extra_images=? WHERE id=?",
                    (hi_main, local, json.dumps(hi_extra or [], ensure_ascii=False), r["id"]))
                c2.commit()
                c2.close()
                upgraded += 1
            except Exception:
                failed += 1
        if (i + 1) % 100 == 0:
            _log(f"  {i+1}/{total} … 교체 {upgraded} · 이미하이레스 {skipped} · 실패 {failed}", progress_cb)
    _log(f"하이레스 재수집 완료 — 점검 {checked} · 교체 {upgraded} · "
         f"이미하이레스 {skipped} · 실패 {failed}", progress_cb)
    return {"checked": checked, "upgraded": upgraded, "failed": failed, "skipped": skipped}


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

    elif "--do-detail" in sys.argv:
        # crawl_product_details()가 subprocess로 이 모드를 호출
        # 인자: --do-detail <params_json> <output_json>
        idx = sys.argv.index("--do-detail")
        _params_file = sys.argv[idx + 1]
        _output_file = sys.argv[idx + 2]
        try:
            with open(_params_file, encoding="utf-8") as _f:
                _p = json.load(_f)
            _results = _fetch_detail_direct(
                product_nos=_p["product_nos"],
                email=_p.get("email", ""),
                password=_p.get("password", ""),
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
