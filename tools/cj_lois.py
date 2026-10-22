"""CJ대한통운 기업고객(LOIS Parcel) — 저장된 브라우저 프로필로 '일별 배송상세' 엑셀 받기.

로그인은 사람이 한 번 한다(login_once). 이후 예약 실행은 같은 프로필을 쓴다.
"""
import os
import time


def _ctx(pw, profile, headed):
    os.makedirs(profile, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        profile, headless=not headed, accept_downloads=True, locale='ko-KR',
        viewport={'width': 1400, 'height': 900})


def login_once(url, profile):
    """브라우저를 띄워 사람이 로그인하게 한다. 창을 닫으면 끝."""
    from playwright.sync_api import sync_playwright
    print('CJ 사이트가 열립니다. 로그인한 뒤(아이디 저장·자동로그인이 있으면 체크) 창을 닫으세요.')
    with sync_playwright() as pw:
        ctx = _ctx(pw, profile, headed=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        while ctx.pages:
            time.sleep(1)
        ctx.close()
