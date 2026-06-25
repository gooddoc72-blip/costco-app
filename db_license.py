"""
로컬 설치형 1-PC 사용 인증 — 라이선스 관리 레이어 (auth.db / licenses).
- 관리자: 라이선스키 발급/조회/철회/바인딩 해제
- 검증: (key, machine_id) → 최초엔 바인딩, 이후 동일 PC만 허용 (1키=1PC)
"""
import sqlite3
import secrets
from datetime import datetime

from db_core import AUTH_DB


def _conn():
    c = sqlite3.connect(AUTH_DB)
    c.row_factory = sqlite3.Row
    _ensure_table(c)
    return c


def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        license_key TEXT UNIQUE NOT NULL,
        username TEXT DEFAULT '',
        bound_machine_id TEXT DEFAULT '',
        status TEXT DEFAULT 'active',          -- active | revoked
        memo TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        activated_at TEXT DEFAULT '',
        last_seen_at TEXT DEFAULT ''
    )""")
    conn.commit()


def gen_license_key():
    """COCO-XXXX-XXXX-XXXX 형식 키 생성."""
    body = secrets.token_hex(6).upper()  # 12 hex chars
    return f"COCO-{body[0:4]}-{body[4:8]}-{body[8:12]}"


def create_license(username="", memo="", key=None):
    """라이선스 발급. key 미지정 시 자동 생성. 반환: 발급된 key."""
    c = _conn()
    k = (key or gen_license_key()).strip().upper()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        "INSERT OR IGNORE INTO licenses (license_key, username, memo, status, created_at) "
        "VALUES (?,?,?,'active',?)",
        (k, username, memo, now),
    )
    c.commit()
    c.close()
    return k


def verify_and_bind(key, machine_id):
    """로컬 앱 검증. 반환: dict(ok, code, message, username).
    code: ok | activated | invalid_key | revoked | machine_mismatch | empty
    """
    k = (key or "").strip().upper()
    mid = (machine_id or "").strip()
    if not k or not mid:
        return {"ok": False, "code": "empty", "message": "키 또는 머신ID 누락"}
    c = _conn()
    row = c.execute("SELECT * FROM licenses WHERE license_key=?", (k,)).fetchone()
    if not row:
        c.close()
        return {"ok": False, "code": "invalid_key", "message": "등록되지 않은 라이선스키입니다."}
    if row["status"] != "active":
        c.close()
        return {"ok": False, "code": "revoked", "message": "정지된 라이선스입니다. 관리자에게 문의하세요."}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bound = (row["bound_machine_id"] or "").strip()
    if not bound:
        # 최초 활성화 → 이 PC에 바인딩
        c.execute("UPDATE licenses SET bound_machine_id=?, activated_at=?, last_seen_at=? WHERE id=?",
                  (mid, now, now, row["id"]))
        c.commit(); c.close()
        return {"ok": True, "code": "activated", "message": "이 PC에 활성화 완료",
                "username": row["username"], "display": (row["memo"] or row["username"])}
    if bound == mid:
        c.execute("UPDATE licenses SET last_seen_at=? WHERE id=?", (now, row["id"]))
        c.commit(); c.close()
        return {"ok": True, "code": "ok", "message": "인증됨",
                "username": row["username"], "display": (row["memo"] or row["username"])}
    c.close()
    return {"ok": False, "code": "machine_mismatch",
            "message": "이미 다른 PC에 등록된 라이선스입니다. (1키=1PC) 관리자에게 문의하세요."}


def list_licenses(limit=200):
    c = _conn()
    rows = c.execute("SELECT * FROM licenses ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def revoke_license(key, revoke=True):
    c = _conn()
    c.execute("UPDATE licenses SET status=? WHERE license_key=?",
              ("revoked" if revoke else "active", (key or "").strip().upper()))
    c.commit(); c.close()


def unbind_license(key):
    """PC 교체 시 바인딩 해제 → 다음 실행 PC에 재바인딩 가능."""
    c = _conn()
    c.execute("UPDATE licenses SET bound_machine_id='', activated_at='' WHERE license_key=?",
              ((key or "").strip().upper(),))
    c.commit(); c.close()


def delete_license(key):
    c = _conn()
    c.execute("DELETE FROM licenses WHERE license_key=?", ((key or "").strip().upper(),))
    c.commit(); c.close()
