"""
로컬 설치형 클라이언트 라이선스 모듈 (Phase 2).
- 로컬 모드(COSTCO_LOCAL=1)에서만 동작. 웹(서버)에선 비활성.
- MachineGuid + 저장된 키로 서버(/license/verify) 검증.
"""
import os
import json

LICENSE_SERVER = os.environ.get("COSTCO_LICENSE_SERVER", "https://cocobiz.shop")
_KEY_PATH = os.path.join(os.path.expanduser("~"), ".costcobiz_license.json")


def is_local_mode():
    """로컬 설치형 실행 여부. 인스톨러가 COSTCO_LOCAL=1 설정."""
    return os.environ.get("COSTCO_LOCAL", "") == "1"


def get_machine_id():
    """이 PC의 고유 식별자 — Windows MachineGuid 우선, 실패 시 MAC 기반."""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Cryptography")
        v, _ = winreg.QueryValueEx(k, "MachineGuid")
        if v:
            return str(v).strip()
    except Exception:
        pass
    import uuid
    return f"NODE-{uuid.getnode():x}"


def get_stored_key():
    try:
        with open(_KEY_PATH, encoding="utf-8") as f:
            return (json.load(f).get("key") or "").strip()
    except Exception:
        return ""


def save_key(key):
    try:
        with open(_KEY_PATH, "w", encoding="utf-8") as f:
            json.dump({"key": (key or "").strip()}, f)
        return True
    except Exception:
        return False


def clear_key():
    try:
        os.remove(_KEY_PATH)
    except Exception:
        pass


def verify_license(key, machine_id=None):
    """서버 검증. 반환: dict(ok, code, message, ...). stdlib urllib 사용(의존성 없음)."""
    mid = machine_id or get_machine_id()
    if not (key or "").strip():
        return {"ok": False, "code": "no_key", "message": "라이선스키가 없습니다."}
    import urllib.request
    body = json.dumps({"key": key.strip(), "machine_id": mid}).encode("utf-8")
    req = urllib.request.Request(
        f"{LICENSE_SERVER}/license/verify", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "code": "network", "message": f"인증 서버 연결 실패: {e}"}
