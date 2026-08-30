# -*- coding: utf-8 -*-
"""네이버 로그인 세션 생성기 (로컬 PC에서 1회 실행).

네이버 쇼핑 검색이 2026년부터 비로그인 접근을 차단해, 순위 체크는 로그인 세션이
필요하다. 이 스크립트는 실제 크롬 창을 띄워 사용자가 직접 로그인하게 하고,
쿠키만 data/naver_session.json 으로 저장한다. (비밀번호는 저장하지 않는다)

사용법:
    cd /d "f:\\1 코스트코\\001 코스트코 자동화\\코스트코 자동화 프로그램"
    .venv\\Scripts\\python.exe naver_session_setup.py

주의:
  - 판매자 본계정 대신 **별도 네이버 계정** 사용을 권장한다.
  - 저장된 세션은 서버로 옮겨 순위 체크에 재사용한다(계정 1개면 전체 사용자 공용).
  - 세션이 만료되면 순위 체크가 '세션 만료' 오류를 내므로 다시 실행하면 된다.
"""
import os
import sys
import json

BASE = os.path.dirname(os.path.abspath(__file__))
SESSION_PATH = os.path.join(BASE, "data", "naver_session.json")
DIAG_PATH = os.path.join(BASE, "data", "naver_session_diag.json")
TEST_KEYWORD = "검은콩"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright가 설치돼 있지 않습니다:  pip install playwright && playwright install chromium")
        return 1

    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    print("=" * 60)
    print(" 네이버 로그인 세션 생성")
    print("=" * 60)
    print(" 1) 잠시 후 크롬 창이 열립니다.")
    print(" 2) 창에서 네이버에 로그인해 주세요 (2단계 인증까지 완료).")
    print(" 3) 로그인이 감지되면 자동으로 저장됩니다. 콘솔 입력은 필요 없습니다.")
    print(" ※ 판매자 본계정 대신 별도 계정을 권장합니다.")
    print("-" * 60)

    diag = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 900},
                                  user_agent=UA)
        page = ctx.new_page()
        page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded")

        # 로그인 쿠키(NID_AUT/NID_SES)가 생길 때까지 최대 10분 대기 — 콘솔 입력 불필요
        print("\n로그인 대기 중... (최대 10분, 로그인하면 자동 진행)")
        _waited, _limit = 0, 600
        while _waited < _limit:
            try:
                _names = {c["name"] for c in ctx.cookies()}
            except Exception:
                print("\n창이 닫혔습니다. 다시 실행해 주세요.")
                return 1
            if "NID_AUT" in _names and "NID_SES" in _names:
                print("\n로그인 감지됨 ✅  (3초 후 저장)")
                page.wait_for_timeout(3000)
                break
            page.wait_for_timeout(2000)
            _waited += 2
            if _waited % 20 == 0:
                print("   ... 대기 %d초" % _waited)
        else:
            print("\n10분 안에 로그인이 감지되지 않아 종료합니다.")
            browser.close()
            return 1

        # 1) 무슨 일이 있어도 세션부터 저장 (검증 실패해도 쿠키는 남긴다)
        ctx.storage_state(path=SESSION_PATH)
        cookies = ctx.cookies()
        names = {c["name"] for c in cookies}
        logged_in = "NID_AUT" in names and "NID_SES" in names
        diag["cookie_count"] = len(cookies)
        diag["logged_in"] = logged_in
        print("\n세션 저장: %s (쿠키 %d개)" % (SESSION_PATH, len(cookies)))
        print("로그인 쿠키(NID_AUT/NID_SES): %s" % ("있음 ✅" if logged_in else "없음 ❌"))
        if not logged_in:
            print("→ 로그인이 완료되지 않았습니다. 창에서 로그인 후 다시 실행해 주세요.")
            browser.close()
            _save_diag(diag)
            return 1

        # 2) 쇼핑 검색 페이지 접근 확인 (리다이렉트돼도 예외로 죽지 않게)
        print("\n[검증1] 쇼핑 검색 페이지 접근...")
        try:
            page.goto("https://search.shopping.naver.com/search/all?query=" + TEST_KEYWORD,
                      wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(2500)
            url, title = page.url, (page.title() or "")
        except Exception as e:
            page.wait_for_timeout(2500)
            url, title = page.url, (page.title() or "")
            diag["page_exception"] = repr(e)[:200]
        blocked = "nidlogin" in url
        diag["page_url"] = url[:200]
        diag["page_title"] = title[:80]
        print("   url  : %s" % url[:100])
        print("   title: %s" % title[:60])
        print("   → %s" % ("차단(로그인 요구) ❌" if blocked else "접근 성공 ✅"))

        # 3) 내부 검색 API 응답 구조 확인 (세션 쿠키 그대로 사용)
        print("\n[검증2] 내부 검색 API 호출...")
        try:
            r = ctx.request.get(
                "https://search.shopping.naver.com/api/search/all",
                params={"sort": "rel", "pagingIndex": 1, "pagingSize": 40,
                        "productSet": "total", "query": TEST_KEYWORD},
                headers={"Referer": "https://search.shopping.naver.com/search/all?query=" + TEST_KEYWORD,
                         "User-Agent": UA, "Accept": "application/json, text/plain, */*",
                         "Accept-Language": "ko-KR,ko;q=0.9"},
                timeout=30000)
            diag["api_status"] = r.status
            print("   status: %s" % r.status)
            body = r.text()
            diag["api_body_head"] = body[:300]
            if r.status == 200 and body.lstrip().startswith("{"):
                j = json.loads(body)
                diag["api_top_keys"] = list(j.keys())[:10]
                prods = ((j.get("shoppingResult") or {}).get("products")
                         or j.get("products") or [])
                diag["api_product_count"] = len(prods)
                if prods:
                    diag["api_product_keys"] = sorted(prods[0].keys())
                    diag["api_product_sample"] = {k: prods[0][k] for k in list(prods[0].keys())[:40]}
                print("   상품 %d개, 응답 구조를 data/naver_session_diag.json 에 저장했습니다." % len(prods))
            else:
                print("   JSON이 아닙니다. 앞부분: %s" % body[:120].replace("\n", " "))
        except Exception as e:
            diag["api_exception"] = repr(e)[:200]
            print("   호출 실패: %s" % repr(e)[:120])

        browser.close()

    _save_diag(diag)
    ok = diag.get("api_status") == 200 and diag.get("api_product_count")
    print("\n" + "=" * 60)
    if ok:
        print(" 성공 — 순위 체크에 사용할 수 있는 세션입니다.")
    else:
        print(" 세션은 저장했지만 검색 접근이 확인되지 않았습니다.")
        print(" data/naver_session_diag.json 내용을 알려주시면 원인을 잡겠습니다.")
    print("=" * 60)
    return 0


def _save_diag(diag):
    try:
        with open(DIAG_PATH, "w", encoding="utf-8") as f:
            json.dump(diag, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
