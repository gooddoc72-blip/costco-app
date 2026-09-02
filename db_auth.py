"""
인증 / 세션 / 전역 설정 레이어
auth.db 에 있는 users, sessions, app_settings 테이블 전담.
"""
import sqlite3
import hashlib
import secrets
import os
import bcrypt
from datetime import datetime, timedelta

from db_core import AUTH_DB, DATA_DIR, get_user_db, get_auth_db


def hash_pw(pw):
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _sha256(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def init_auth_db():
    conn = get_auth_db()
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        display_name TEXT DEFAULT '',
        is_admin INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        status TEXT DEFAULT 'active'
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('require_approval', '1')")
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('allow_signup', '1')")
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('costco_email', '')")
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('costco_password', '')")
    conn.execute("""CREATE TABLE IF NOT EXISTS shared_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_no TEXT DEFAULT '',
        costco_name TEXT NOT NULL,
        match_keyword TEXT UNIQUE NOT NULL,
        unit_price INTEGER NOT NULL,
        split_qty INTEGER DEFAULT 1,
        updated_by TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    )""")
    for col_sql in [
        "ALTER TABLE shared_products ADD COLUMN split_qty INTEGER DEFAULT 1",
        "ALTER TABLE shared_products ADD COLUMN updated_by TEXT DEFAULT ''",
        "ALTER TABLE shared_products ADD COLUMN price_type TEXT DEFAULT '매장'",
        "ALTER TABLE shared_products ADD COLUMN image_url TEXT DEFAULT ''",
        "ALTER TABLE shared_products ADD COLUMN local_image TEXT DEFAULT ''",
        "ALTER TABLE shared_products ADD COLUMN naver_category_id TEXT DEFAULT ''",
        "ALTER TABLE shared_products ADD COLUMN category TEXT DEFAULT ''",
        "ALTER TABLE shared_products ADD COLUMN extra_images TEXT DEFAULT ''",
        "ALTER TABLE shared_products ADD COLUMN detail_html TEXT DEFAULT ''",
        "ALTER TABLE shared_products ADD COLUMN store_price INTEGER DEFAULT 0",
        "ALTER TABLE shared_products ADD COLUMN online_price INTEGER DEFAULT 0",
        "ALTER TABLE shared_products ADD COLUMN store_updated_at TEXT DEFAULT ''",
        "ALTER TABLE shared_products ADD COLUMN online_updated_at TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'",
    ]:
        try:
            conn.execute(col_sql)
        except Exception:
            pass
    try:
        conn.execute("UPDATE shared_products SET price_type='온라인' WHERE updated_by='crawler'")
    except Exception:
        pass
    try:
        conn.execute("UPDATE shared_products SET price_type='매장' WHERE price_type IS NULL OR price_type=''")
    except Exception:
        pass
    try:
        conn.execute("""
            UPDATE shared_products
            SET store_price = CASE WHEN price_type='매장' THEN unit_price ELSE store_price END,
                online_price = CASE WHEN price_type='온라인' THEN unit_price ELSE online_price END,
                store_updated_at = CASE WHEN price_type='매장' AND (store_updated_at IS NULL OR store_updated_at='')
                                        THEN updated_at ELSE store_updated_at END,
                online_updated_at = CASE WHEN price_type='온라인' AND (online_updated_at IS NULL OR online_updated_at='')
                                         THEN updated_at ELSE online_updated_at END
            WHERE (store_price=0 OR store_price IS NULL) AND (online_price=0 OR online_price IS NULL)
        """)
    except Exception:
        pass
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_shared_product_no ON shared_products(product_no)",
        "CREATE INDEX IF NOT EXISTS idx_shared_match_kw ON shared_products(match_keyword)",
        "CREATE INDEX IF NOT EXISTS idx_shared_category ON shared_products(category)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass
    conn.commit()
    admin = conn.execute("SELECT 1 FROM users WHERE is_admin=1").fetchone()
    if not admin:
        conn.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?)",
                     ("admin", hash_pw("admin1234"), "관리자", 1,
                      datetime.now().strftime("%Y-%m-%d %H:%M"), "active"))
    conn.execute("UPDATE users SET status='active' WHERE is_admin=1")
    conn.commit()
    conn.close()


def find_user_row(conn, username):
    """아이디를 대소문자·앞뒤공백 무시하고 찾는다.

    왜: 관리자가 만든 'Sueb' 같은 대문자 아이디를 사용자가 'sueb'로 입력하면
    기존 `WHERE username=?`(SQLite는 대소문자 구분)로는 행 자체를 못 찾아
    비밀번호를 아무리 초기화해도 '로그인 실패'만 반복됐다.
    정확히 일치하는 행이 있으면 그쪽을 우선(ORDER BY)해서, 만에 하나
    대소문자만 다른 두 계정이 공존해도 원래 계정이 밀리지 않게 한다.
    반환: (username, password, display_name, is_admin, status) 또는 None
    """
    username = (username or "").strip()
    if not username:
        return None
    return conn.execute(
        "SELECT username, password, display_name, is_admin, status FROM users "
        "WHERE username=? COLLATE NOCASE ORDER BY (username=?) DESC LIMIT 1",
        (username, username)
    ).fetchone()


def check_login(username, password):
    """성공 시 dict, 실패 시 사유 문자열.

    사유를 구분해서 돌려주는 이유: 예전엔 '없는 아이디'와 '틀린 비밀번호'가
    똑같이 None이라 화면에 '로그인 실패'만 떠서, 오타인지 승인 대기인지
    관리자도 사용자도 구분할 수 없었다.
    """
    conn = get_auth_db()
    row = find_user_row(conn, username)
    if not row:
        conn.close()
        return "no_user"

    real_username, stored = row[0], row[1]
    pw_ok = False
    needs_upgrade = False

    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        pw_ok = bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
    else:
        if stored == _sha256(password):
            pw_ok = True
            needs_upgrade = True

    if not pw_ok:
        conn.close()
        return "bad_pw"

    if needs_upgrade:
        new_hash = hash_pw(password)
        conn.execute("UPDATE users SET password=? WHERE username=?", (new_hash, real_username))
        conn.commit()

    conn.close()
    if row[4] == 'pending':
        return "pending"
    if row[4] == 'rejected':
        return "rejected"
    # username은 반드시 DB에 저장된 원본을 돌려준다.
    # 입력값(sueb)을 그대로 쓰면 data/{username}.db 가 새로 생겨 데이터가 갈라진다.
    return {"username": real_username, "display_name": row[2], "is_admin": row[3]}


def get_global_setting(key, default=''):
    conn = get_auth_db()
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_global_setting(key, value):
    conn = get_auth_db()
    conn.execute("INSERT OR REPLACE INTO app_settings VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


def register_user(username, password, display_name=""):
    require_approval = get_global_setting('require_approval', '1')
    status = 'pending' if require_approval == '1' else 'active'
    username = (username or "").strip()
    conn = get_auth_db()
    # 로그인이 대소문자를 무시하므로, 가입도 무시해야 'Sueb'/'sueb' 두 계정이
    # 생겨 로그인 시 어느 쪽이 잡힐지 모호해지는 상황을 막을 수 있다.
    if not username or find_user_row(conn, username):
        conn.close()
        return False, None
    try:
        conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
                     (username, hash_pw(password), display_name or username, 0,
                      datetime.now().strftime("%Y-%m-%d %H:%M"), status))
        conn.commit()
        conn.close()
        return True, status
    except Exception:
        conn.close()
        return False, None


def ensure_local_user(username, display_name=""):
    """로컬 설치판 전용: 라이선스 계정을 로컬 auth.db에 자동 생성(없으면)하고 active 보장.
    반환: {username, display_name, is_admin} (자동 로그인용). DB 파일명 안전성 위해 영문/숫자만."""
    import re as _re
    import secrets as _secrets
    safe = _re.sub(r'[^A-Za-z0-9_\-]', '', (username or '').strip()) or 'local'
    conn = get_auth_db()
    row = conn.execute("SELECT display_name, is_admin FROM users WHERE username=?", (safe,)).fetchone()
    if not row:
        conn.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?)",
                     (safe, hash_pw(_secrets.token_hex(16)), display_name or safe, 0,
                      datetime.now().strftime("%Y-%m-%d %H:%M"), "active"))
        conn.commit()
        dn, adm = (display_name or safe), 0
    else:
        conn.execute("UPDATE users SET status='active' WHERE username=?", (safe,))
        if display_name:
            conn.execute("UPDATE users SET display_name=? WHERE username=?", (display_name, safe))
        conn.commit()
        dn, adm = (display_name or row[0]), row[1]
    conn.close()
    return {"username": safe, "display_name": dn, "is_admin": adm}


def get_pending_users():
    conn = get_auth_db()
    rows = conn.execute(
        "SELECT username, display_name, created_at FROM users WHERE status='pending' ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [{"username": r[0], "display_name": r[1], "created_at": r[2]} for r in rows]


def approve_user(username):
    conn = get_auth_db()
    conn.execute("UPDATE users SET status='active' WHERE username=?", (username,))
    conn.commit()
    conn.close()


def reject_user(username):
    conn = get_auth_db()
    conn.execute("UPDATE users SET status='rejected' WHERE username=?", (username,))
    conn.commit()
    conn.close()


def get_all_users():
    conn = get_auth_db()
    rows = conn.execute(
        "SELECT username, display_name, is_admin, created_at, status FROM users ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [{"username": r[0], "display_name": r[1], "is_admin": r[2],
             "created_at": r[3], "status": r[4] or 'active'} for r in rows]


def add_user(username, password, display_name=""):
    username = (username or "").strip()
    conn = get_auth_db()
    if not username or find_user_row(conn, username):
        conn.close()
        return False
    try:
        conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
                     (username, hash_pw(password), display_name or username, 0,
                      datetime.now().strftime("%Y-%m-%d %H:%M"), "active"))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def delete_user(username):
    conn = get_auth_db()
    conn.execute("DELETE FROM users WHERE username=? AND is_admin=0", (username,))
    conn.commit()
    conn.close()
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    if os.path.exists(db_path):
        os.remove(db_path)


def change_password(username, new_password):
    """성공 여부를 반환. 예전엔 대상이 없어도 조용히 성공한 것처럼 보였다."""
    conn = get_auth_db()
    row = find_user_row(conn, username)
    if not row:
        conn.close()
        return False
    cur = conn.execute("UPDATE users SET password=? WHERE username=?",
                       (hash_pw(new_password), row[0]))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def get_user_info(username):
    conn = get_auth_db()
    row = conn.execute(
        "SELECT username, display_name, is_admin FROM users WHERE username=?", (username,)
    ).fetchone()
    conn.close()
    if row:
        return {"username": row[0], "display_name": row[1], "is_admin": row[2]}
    return None


def create_session(username, days=30):
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires = (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    conn = get_auth_db()
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                 (token, username, now.strftime("%Y-%m-%d %H:%M"), expires))
    conn.commit()
    conn.close()
    return token


def get_session_user(token):
    if not token:
        return None
    conn = get_auth_db()
    row = conn.execute(
        "SELECT username, expires_at FROM sessions WHERE token=?", (token,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    username, expires_at = row
    if datetime.strptime(expires_at, "%Y-%m-%d %H:%M") < datetime.now():
        delete_session(token)
        return None
    return username


def delete_session(token):
    if not token:
        return
    conn = get_auth_db()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()
