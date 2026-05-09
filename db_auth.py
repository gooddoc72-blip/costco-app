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

from db_core import AUTH_DB, DATA_DIR, get_user_db


def hash_pw(pw):
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _sha256(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def init_auth_db():
    conn = sqlite3.connect(AUTH_DB)
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


def check_login(username, password):
    conn = sqlite3.connect(AUTH_DB)
    row = conn.execute(
        "SELECT password, display_name, is_admin, status FROM users WHERE username=?",
        (username,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    stored = row[0]
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
        return None

    if needs_upgrade:
        new_hash = hash_pw(password)
        conn.execute("UPDATE users SET password=? WHERE username=?", (new_hash, username))
        conn.commit()

    conn.close()
    if row[3] == 'pending':
        return "pending"
    if row[3] == 'rejected':
        return "rejected"
    return {"username": username, "display_name": row[1], "is_admin": row[2]}


def get_global_setting(key, default=''):
    conn = sqlite3.connect(AUTH_DB)
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_global_setting(key, value):
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("INSERT OR REPLACE INTO app_settings VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


def register_user(username, password, display_name=""):
    require_approval = get_global_setting('require_approval', '1')
    status = 'pending' if require_approval == '1' else 'active'
    conn = sqlite3.connect(AUTH_DB)
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


def get_pending_users():
    conn = sqlite3.connect(AUTH_DB)
    rows = conn.execute(
        "SELECT username, display_name, created_at FROM users WHERE status='pending' ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [{"username": r[0], "display_name": r[1], "created_at": r[2]} for r in rows]


def approve_user(username):
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("UPDATE users SET status='active' WHERE username=?", (username,))
    conn.commit()
    conn.close()


def reject_user(username):
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("UPDATE users SET status='rejected' WHERE username=?", (username,))
    conn.commit()
    conn.close()


def get_all_users():
    conn = sqlite3.connect(AUTH_DB)
    rows = conn.execute(
        "SELECT username, display_name, is_admin, created_at, status FROM users ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [{"username": r[0], "display_name": r[1], "is_admin": r[2],
             "created_at": r[3], "status": r[4] or 'active'} for r in rows]


def add_user(username, password, display_name=""):
    conn = sqlite3.connect(AUTH_DB)
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
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("DELETE FROM users WHERE username=? AND is_admin=0", (username,))
    conn.commit()
    conn.close()
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    if os.path.exists(db_path):
        os.remove(db_path)


def change_password(username, new_password):
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("UPDATE users SET password=? WHERE username=?", (hash_pw(new_password), username))
    conn.commit()
    conn.close()


def get_user_info(username):
    conn = sqlite3.connect(AUTH_DB)
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
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                 (token, username, now.strftime("%Y-%m-%d %H:%M"), expires))
    conn.commit()
    conn.close()
    return token


def get_session_user(token):
    if not token:
        return None
    conn = sqlite3.connect(AUTH_DB)
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
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()
