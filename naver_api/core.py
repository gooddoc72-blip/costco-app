"""네이버 API — 커머스 API 토큰 발급"""
import time, json, requests, bcrypt, pybase64, math
from datetime import datetime, timedelta, timezone

def get_token(client_id, client_secret):
    timestamp = str(int((time.time() - 10) * 1000))
    password = client_id + "_" + timestamp
    last_err = None
    # 일시적 네트워크 지연/장애에 대비해 2회 재시도 (총 3회), timeout 30s
    for _attempt in range(3):
        try:
            hashed = bcrypt.hashpw(password.encode('utf-8'), client_secret.encode('utf-8'))
            sign = pybase64.standard_b64encode(hashed).decode('utf-8')
            resp = requests.post("https://api.commerce.naver.com/external/v1/oauth2/token", data={
                "client_id": client_id, "timestamp": timestamp, "client_secret_sign": sign,
                "grant_type": "client_credentials", "type": "SELF"
            }, timeout=30)
            try:
                _body = resp.json()
            except Exception:
                return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
            _token = _body.get("access_token")
            if _token:
                return _token, None
            _msg = _body.get("message") or _body.get("error_description") or _body.get("error") or str(_body)[:200]
            return None, f"HTTP {resp.status_code} — {_msg}"
        except Exception as e:
            last_err = e
            if _attempt < 2:
                time.sleep(1.5)  # 짧은 backoff 후 재시도
                continue
    return None, f"토큰 실패: {last_err}"

