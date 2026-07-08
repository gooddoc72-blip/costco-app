"""네이버 API — 카카오톡 알림 (텔레그램은 2026-07 삭제 — 사용빈도 낮음)"""
import time, json, requests, bcrypt, pybase64, math
from datetime import datetime, timedelta, timezone
from .core import get_token

def send_kakao(access_token, msg, rest_api_key=None, refresh_token=None, client_secret=None):
    """카카오톡 메모. 1000자 이하면 한 건, 초과 시 줄 단위로 나눠 전부 발송
    → 긴 목록도 잘림 없이 전부 도착(카카오 실측 전달 한도 ~1000자). 실패 청크는 건너뛰고 계속.
    client_secret: 앱에 Client Secret '사용함'이면 토큰 갱신 시 함께 전송."""
    import time as _t
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    state = {"refreshed": None}

    def _post(text):
        """단건 발송. 401이면 토큰 갱신 후 1회 재시도. 반환: (resp, token_err)"""
        payload = {"template_object": json.dumps({
            "object_type": "text", "text": text,
            "link": {"web_url": "https://sell.smartstore.naver.com",
                     "mobile_web_url": "https://sell.smartstore.naver.com"},
            "button_title": "스마트스토어 바로가기",
        })}
        resp = requests.post(url, headers=headers, data=payload, timeout=15)
        if resp.status_code == 401 and refresh_token and rest_api_key:
            new_token, new_refresh, err = refresh_kakao_token(rest_api_key, refresh_token, client_secret)
            if not new_token:
                return resp, f"토큰 갱신 실패: {err}"
            headers["Authorization"] = f"Bearer {new_token}"
            state["refreshed"] = f"__TOKEN_REFRESHED__{new_token}||{new_refresh}"
            resp = requests.post(url, headers=headers, data=payload, timeout=15)
        return resp, None

    text = msg or ''

    # 카카오 텍스트 템플릿 실측 '전달' 한도 ~1000자(2000자부터 뒷부분 잘려서 도착).
    # MAX 이하면 1건, 초과 시 줄 단위로 나눠 전부 발송 → 긴 목록도 잘림 없이 전부 도착.
    MAX = 1000

    def _chunk_by_lines(s, limit):
        chunks, cur = [], ""
        for line in s.split('\n'):
            # 한 줄 자체가 limit 초과면 강제 분할
            while len(line) > limit:
                if cur:
                    chunks.append(cur); cur = ""
                chunks.append(line[:limit]); line = line[limit:]
            if not cur:
                cur = line
            elif len(cur) + 1 + len(line) <= limit:
                cur = cur + '\n' + line
            else:
                chunks.append(cur); cur = line
        if cur or not chunks:
            chunks.append(cur)
        return chunks

    # 파트 번호를 붙이려면 먼저 분할 → 헤더 길이만큼 여유를 두고 재분할
    _pre = _chunk_by_lines(text, MAX)
    if len(_pre) > 1:
        # "(i/N)\n" 헤더 자리 확보 후 재분할 → 헤더 포함해도 MAX 이하 보장
        chunks = _chunk_by_lines(text, MAX - 12)
        total = len(chunks)
        chunks = [f"({i+1}/{total})\n{c}" for i, c in enumerate(chunks)]
    else:
        chunks = _pre
    total = len(chunks)
    sent = 0
    fails = []
    for ci, chunk in enumerate(chunks):
        if ci > 0:
            _t.sleep(1.0)  # 청크 사이 sleep — rate limit/순서 보장(여유 있게)
        try:
            resp, tok_err = _post(chunk)
            if tok_err:
                fails.append(f"청크{ci+1}: {tok_err}")
                continue
            if resp.status_code == 200:
                sent += 1
            else:
                fails.append(f"청크{ci+1}({resp.status_code}): {resp.text[:80]}")
        except Exception as e:
            fails.append(f"청크{ci+1} 예외: {e}")
    if sent == total and total > 0:
        return True, state["refreshed"]
    return False, f"카카오 발송 {sent}/{total} 성공" + (f" — 실패: {'; '.join(fails)[:200]}" if fails else "")


def refresh_kakao_token(rest_api_key, refresh_token, client_secret=None):
    """카카오 refresh_token으로 새 access_token 발급.
    앱에 Client Secret이 '사용함'이면 client_secret도 함께 보내야 함(KOE010 방지)."""
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token
    }
    if client_secret:
        data["client_secret"] = client_secret
    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            new_access = result.get("access_token", "")
            new_refresh = result.get("refresh_token", refresh_token)  # 없으면 기존 것 유지
            return new_access, new_refresh, None
        else:
            return None, None, f"갱신 실패 ({resp.status_code}): {resp.text}"
    except Exception as e:
        return None, None, str(e)


def get_kakao_token_by_code(rest_api_key, auth_code, redirect_uri="http://localhost",
                            client_secret=None):
    """인가 코드로 카카오 access_token + refresh_token 발급.
    앱에 Client Secret이 '사용함'이면 client_secret도 함께 보내야 함(KOE010 방지)."""
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "code": auth_code
    }
    if client_secret:
        data["client_secret"] = client_secret
    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            return result.get("access_token"), result.get("refresh_token"), None
        else:
            return None, None, f"토큰 발급 실패 ({resp.status_code}): {resp.text}"
    except Exception as e:
        return None, None, str(e)

