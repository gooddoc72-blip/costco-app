"""
DB 접근 레이어 — SQLite 읽기/쓰기만 담당
UI(Streamlit)·비즈니스 로직 의존성 없음
"""
import sqlite3
import hashlib
import secrets
import os
import bcrypt
from datetime import datetime, timedelta

from utils import get_week_range, get_month_range

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUTH_DB  = os.path.join(DATA_DIR, "auth.db")
os.makedirs(DATA_DIR, exist_ok=True)


# ── 인증 ────────────────────────────────────────────────
def hash_pw(pw):
    """bcrypt 해시 생성 (신규 비밀번호 저장용)."""
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _sha256(pw):
    """레거시 SHA-256 — 마이그레이션 비교에만 사용."""
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
        # 매장가/온라인가 분리 (옵션 A 리팩토링)
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
    # 1회성 마이그레이션: 기존 unit_price를 price_type에 따라 store_price/online_price로 분배
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
    # shared_products 인덱스 (조회 성능 향상)
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
        # 이미 bcrypt 해시
        pw_ok = bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
    else:
        # 레거시 SHA-256 — 일치하면 bcrypt로 자동 업그레이드
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


# ── 세션 ────────────────────────────────────────────────
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


# ── 공유 제품 DB ─────────────────────────────────────────
def get_shared_products():
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM shared_products ORDER BY costco_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _upsert_shared_internal(costco_name, keyword, store_price=None, online_price=None,
                            product_no='', split_qty=1, updated_by='', image_url='',
                            receipt_date=''):
    """매장가/온라인가 분리 내부 upsert. None인 가격은 기존 값 보존.
    receipt_date(YYYY-MM-DD): 제공 시 DB의 store_updated_at보다 오래된 영수증이면 매장가 덮어쓰기 방지.
    """
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    split_qty = max(1, int(split_qty or 1))
    existing = None
    if product_no:
        existing = conn.execute(
            "SELECT id, store_price, online_price, store_updated_at, online_updated_at, price_type, unit_price "
            "FROM shared_products WHERE product_no=?", (product_no,)
        ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT id, store_price, online_price, store_updated_at, online_updated_at, price_type, unit_price "
            "FROM shared_products WHERE match_keyword=?", (keyword,)
        ).fetchone()

    if existing:
        cur_store    = existing['store_price'] or 0
        cur_online   = existing['online_price'] or 0
        cur_st_at    = existing['store_updated_at'] or ''
        cur_on_at    = existing['online_updated_at'] or ''
        # 영수증 날짜가 있고 DB에 저장된 매장가 날짜보다 오래되었으면 매장가 유지
        _skip_store = (
            store_price is not None
            and receipt_date
            and cur_st_at
            and receipt_date[:10] < cur_st_at[:10]
        )
        new_store    = cur_store if _skip_store else (int(store_price) if store_price is not None else cur_store)
        new_online   = int(online_price) if online_price is not None else cur_online
        st_at        = cur_st_at if _skip_store else (receipt_date or now[:10] if store_price is not None else cur_st_at)
        on_at        = now if online_price is not None else cur_on_at
        # 호환용 unit_price/price_type: 갱신된 쪽으로 (둘 다 있으면 매장가 우선 표기)
        if store_price is not None:
            new_unit, new_pt = new_store, '매장'
        elif online_price is not None:
            new_unit, new_pt = new_online, '온라인'
        else:
            new_unit, new_pt = existing['unit_price'] or 0, existing['price_type'] or '매장'
        conn.execute("""UPDATE shared_products
                        SET costco_name=?, product_no=?, split_qty=?,
                            updated_by=?, updated_at=?, image_url=?,
                            store_price=?, online_price=?,
                            store_updated_at=?, online_updated_at=?,
                            unit_price=?, price_type=?
                        WHERE id=?""",
                     (costco_name, product_no, split_qty, updated_by, now, image_url,
                      new_store, new_online, st_at, on_at,
                      new_unit, new_pt, existing['id']))
    else:
        st  = int(store_price)  if store_price  is not None else 0
        on  = int(online_price) if online_price is not None else 0
        st_at = now if store_price  is not None else ''
        on_at = now if online_price is not None else ''
        if store_price is not None:
            new_unit, new_pt = st, '매장'
        elif online_price is not None:
            new_unit, new_pt = on, '온라인'
        else:
            new_unit, new_pt = 0, '매장'
        conn.execute("""INSERT INTO shared_products
                        (product_no, costco_name, match_keyword, unit_price, split_qty,
                         updated_by, updated_at, price_type, image_url,
                         store_price, online_price, store_updated_at, online_updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (product_no, costco_name, keyword, new_unit, split_qty,
                      updated_by, now, new_pt, image_url,
                      st, on, st_at, on_at))
    conn.commit()
    conn.close()


def upsert_shared_store_price(costco_name, keyword, price, product_no='', split_qty=1,
                               updated_by='', image_url='', receipt_date=''):
    """매장가만 갱신 (영수증 업로드용). 온라인가는 기존 값 보존.
    receipt_date: 영수증 날짜(YYYY-MM-DD). DB보다 오래된 영수증이면 덮어쓰기 방지."""
    _upsert_shared_internal(costco_name, keyword,
                            store_price=price, online_price=None,
                            product_no=product_no, split_qty=split_qty,
                            updated_by=updated_by, image_url=image_url,
                            receipt_date=receipt_date)


def upsert_shared_online_price(costco_name, keyword, price, product_no='', split_qty=1,
                                updated_by='', image_url=''):
    """온라인가만 갱신 (코스트코몰 크롤러용). 매장가는 기존 값 보존."""
    _upsert_shared_internal(costco_name, keyword,
                            store_price=None, online_price=price,
                            product_no=product_no, split_qty=split_qty,
                            updated_by=updated_by, image_url=image_url)


def upsert_shared_product(costco_name, keyword, price, product_no='', split_qty=1,
                          updated_by='', price_type='매장', image_url=''):
    """호환 wrapper — price_type에 따라 store_price 또는 online_price만 갱신.
    구버전 호출 코드를 그대로 지원하면서 새 컬럼 분리에도 정합."""
    price_type = price_type if price_type in ('매장', '온라인') else '매장'
    if price_type == '온라인':
        _upsert_shared_internal(costco_name, keyword,
                                store_price=None, online_price=price,
                                product_no=product_no, split_qty=split_qty,
                                updated_by=updated_by, image_url=image_url)
    else:
        _upsert_shared_internal(costco_name, keyword,
                                store_price=price, online_price=None,
                                product_no=product_no, split_qty=split_qty,
                                updated_by=updated_by, image_url=image_url)


def delete_shared_product(shared_id):
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("DELETE FROM shared_products WHERE id=?", (shared_id,))
    conn.commit()
    conn.close()


# ── 사용자 DB ─────────────────────────────────────────────
def get_user_db(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_user_db(username):
    conn = get_user_db(username)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_no TEXT DEFAULT '',
        store_product_name TEXT DEFAULT '',
        costco_name TEXT DEFAULT '',
        match_keyword TEXT NOT NULL UNIQUE,
        unit_price INTEGER NOT NULL,
        split_qty INTEGER DEFAULT 1,
        shipping_fee INTEGER DEFAULT 0,
        sale_price INTEGER DEFAULT 0,
        updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS order_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT UNIQUE,
        order_group_no TEXT DEFAULT '',
        order_date TEXT,
        recipient TEXT DEFAULT '',
        buyer TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        product_no TEXT DEFAULT '',
        option_info TEXT DEFAULT '',
        qty INTEGER DEFAULT 1,
        unit_price INTEGER DEFAULT 0,
        order_amount INTEGER DEFAULT 0,
        shipping_fee INTEGER DEFAULT 0,
        settlement INTEGER DEFAULT 0,
        status TEXT DEFAULT '',
        tracking_no TEXT DEFAULT '',
        courier TEXT DEFAULT '',
        cost_price INTEGER DEFAULT 0,
        profit INTEGER DEFAULT 0,
        created_at TEXT,
        raw_json TEXT DEFAULT ''
    )""")
    # 기존 DB에 raw_json 컬럼 마이그레이션 (멱등)
    try:
        c.execute("ALTER TABLE order_history ADD COLUMN raw_json TEXT DEFAULT ''")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS daily_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_date TEXT NOT NULL, recipient TEXT, product_name TEXT,
        product_no TEXT DEFAULT '',
        option_info TEXT, qty INTEGER DEFAULT 1,
        order_amount INTEGER DEFAULT 0, shipping_fee INTEGER DEFAULT 0,
        extra_shipping INTEGER DEFAULT 0, settlement INTEGER DEFAULT 0,
        cost_price INTEGER DEFAULT 0, delivery_cost INTEGER DEFAULT 0,
        box_cost INTEGER DEFAULT 0, profit INTEGER DEFAULT 0,
        matched INTEGER DEFAULT 0, created_at TEXT NOT NULL
    )""")
    default_settings = [
        ('shipping_cost', '1800'), ('box_cost', '300'), ('excel_password', ''),
        ('api_client_id', ''), ('api_client_secret', ''),
        ('telegram_token', ''), ('telegram_chat_id', ''),
        ('kakao_api_key', ''), ('kakao_access_token', ''), ('kakao_refresh_token', ''),
        ('cj_api_id', ''), ('cj_api_pw', ''), ('cj_account_no', ''),
        ('default_courier', 'CJGLS'), ('target_margin', '10'),
        ('max_increase_pct', '20'),
        ('auto_shopping_enabled', '0'), ('auto_shopping_time', '09:00'),
        ('auto_shipping_enabled', '0'), ('auto_shipping_time', '14:00'),
    ]
    for k, v in default_settings:
        c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))
    c.execute("""CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT, origin_product_no TEXT,
        old_price INTEGER, new_price INTEGER,
        cost_price INTEGER, reason TEXT,
        status TEXT DEFAULT 'applied',
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS price_change_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        costco_name TEXT, old_cost INTEGER, new_cost INTEGER,
        diff INTEGER, diff_pct REAL, product_no TEXT DEFAULT '',
        shipping_fee INTEGER DEFAULT 0, notified INTEGER DEFAULT 0,
        naver_updated INTEGER DEFAULT 0, created_at TEXT NOT NULL
    )""")
    # 영수증 raw 항목 영구 저장 (수익계산 영수증 picker용)
    c.execute("""CREATE TABLE IF NOT EXISTS receipt_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_date TEXT NOT NULL,
        product_no TEXT DEFAULT '',
        product_name TEXT NOT NULL,
        qty INTEGER DEFAULT 1,
        unit_price INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(receipt_date, product_no, product_name)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_receipt_items_date ON receipt_items(receipt_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_receipt_items_pno ON receipt_items(product_no)")
    for col_sql in [
        "ALTER TABLE products ADD COLUMN product_no TEXT DEFAULT ''",
        "ALTER TABLE daily_orders ADD COLUMN product_no TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN split_qty INTEGER DEFAULT 1",
        "ALTER TABLE products ADD COLUMN shipping_fee INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN sale_price INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN status TEXT DEFAULT 'SALE'",
        "ALTER TABLE products ADD COLUMN from_naver INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN naver_origin_pno TEXT DEFAULT ''",
    ]:
        try:
            c.execute(col_sql)
        except Exception:
            pass
    conn.commit()
    conn.close()


def get_all_settings(username):
    """모든 설정을 한 번에 읽어 dict로 반환 — get_setting() 반복 호출 대신 사용."""
    conn = get_user_db(username)
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def get_setting(username, key):
    conn = get_user_db(username)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else ''


def set_setting(username, key, value):
    conn = get_user_db(username)
    conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_all_products(username):
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    rows = conn.execute("SELECT * FROM products ORDER BY costco_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _ensure_products_columns(conn):
    """products 테이블 컬럼 idempotent 마이그레이션 + 인덱스"""
    for col_sql in [
        "ALTER TABLE products ADD COLUMN product_no TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN split_qty INTEGER DEFAULT 1",
        "ALTER TABLE products ADD COLUMN shipping_fee INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN sale_price INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN status TEXT DEFAULT 'SALE'",
        "ALTER TABLE products ADD COLUMN from_naver INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN naver_origin_pno TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN category TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN linked_shared_id INTEGER DEFAULT NULL",
    ]:
        try:
            conn.execute(col_sql)
        except Exception:
            pass
    # 자주 조회되는 컬럼 인덱스 (idempotent)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_products_product_no ON products(product_no)",
        "CREATE INDEX IF NOT EXISTS idx_products_naver_origin ON products(naver_origin_pno)",
        "CREATE INDEX IF NOT EXISTS idx_products_from_naver ON products(from_naver)",
        "CREATE INDEX IF NOT EXISTS idx_products_status ON products(status)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass
    conn.commit()


def link_naver_to_shared(username: str, user_product_id: int, shared_id: int):
    """네이버 상품(user product)과 공유DB 상품(shared_product)을 수동 연결.

    연결 후:
    - linked_shared_id 설정
    - products.product_no = shared_products.product_no (코스트코 상품번호로 교체)
      → 이후 get_all_products_merged에서 product_no 기반 매칭이 동작함
    """
    # 공유 DB에서 코스트코 상품번호 조회
    conn_auth = sqlite3.connect(AUTH_DB)
    sp_row = conn_auth.execute(
        "SELECT product_no FROM shared_products WHERE id=?", (shared_id,)
    ).fetchone()
    conn_auth.close()
    costco_pno = (sp_row[0] or '').strip() if sp_row else ''

    conn = get_user_db(username)
    _ensure_products_columns(conn)
    conn.execute(
        "UPDATE products SET linked_shared_id=?, product_no=? WHERE id=?",
        (shared_id, costco_pno, user_product_id)
    )
    conn.commit()
    conn.close()


def unlink_naver_from_shared(username: str, user_product_id: int):
    """네이버-공유DB 연결 해제."""
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    conn.execute("UPDATE products SET linked_shared_id=NULL WHERE id=?", (user_product_id,))
    conn.commit()
    conn.close()


def bulk_update_category(username: str, id_category_map: dict):
    """products.category 일괄 업데이트.

    Args:
        id_category_map: {product_id: category_str}
    Returns:
        int — 업데이트된 건수
    """
    if not id_category_map:
        return 0
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    updated = 0
    for pid, cat in id_category_map.items():
        conn.execute("UPDATE products SET category=? WHERE id=?", (cat, pid))
        updated += 1
    conn.commit()
    conn.close()
    return updated


def upsert_user_private(username, match_keyword, costco_name,
                        sale_price=None, shipping_fee=None, naver_product_no=None,
                        status=None, from_naver=None, naver_origin_pno=None,
                        split_qty=None, category=None):
    """사용자 제품 upsert.
    매칭 우선순위:
      1) naver_origin_pno (네이버 originProductNo) — 같으면 동일 네이버 상품
      2) match_keyword — 기존 호환성
    재가져오기 시 동작:
      - product_no(코스트코 상품번호)는 보존 (덮어쓰지 않음)
      - 상품명(costco_name, match_keyword), 판매가, 택배비, 상태는 갱신
    """
    conn = get_user_db(username)
    _ensure_products_columns(conn)  # 누락 컬럼 자동 추가 (idempotent)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    existing = None
    # 1순위: naver_origin_pno로 매칭 (네이버 originProductNo 기반)
    if naver_origin_pno:
        existing = conn.execute(
            "SELECT id, sale_price, shipping_fee, product_no, status, from_naver, naver_origin_pno, split_qty, category "
            "FROM products WHERE naver_origin_pno=? AND naver_origin_pno != ''",
            (naver_origin_pno,)
        ).fetchone()
    # 2순위: match_keyword로 매칭 (기존 호환)
    if not existing:
        existing = conn.execute(
            "SELECT id, sale_price, shipping_fee, product_no, status, from_naver, naver_origin_pno, split_qty, category "
            "FROM products WHERE match_keyword=?",
            (match_keyword,)
        ).fetchone()

    if existing:
        sale = sale_price   if sale_price   is not None else (existing['sale_price'] or 0)
        fee  = shipping_fee if shipping_fee is not None else (existing['shipping_fee'] or 0)
        nno  = naver_product_no if naver_product_no is not None else (existing['product_no'] or '')
        st   = status if status is not None else (existing['status'] or 'SALE')
        fn   = int(from_naver) if from_naver is not None else int(existing['from_naver'] or 0)
        op   = naver_origin_pno if naver_origin_pno is not None else (existing['naver_origin_pno'] or '')
        sq   = max(1, int(split_qty)) if split_qty is not None else int(existing['split_qty'] or 1)
        cat  = category if category is not None else (existing['category'] or '')
        kw_to_use = match_keyword
        try:
            other = conn.execute(
                "SELECT id FROM products WHERE match_keyword=? AND id<>?",
                (match_keyword, existing['id'])
            ).fetchone()
            if other:
                kw_to_use = existing['match_keyword'] or match_keyword
        except Exception:
            kw_to_use = existing['match_keyword'] or match_keyword
        conn.execute(
            "UPDATE products SET match_keyword=?, costco_name=?, store_product_name=?, "
            "sale_price=?, shipping_fee=?, split_qty=?, product_no=?, status=?, from_naver=?, "
            "naver_origin_pno=?, category=?, updated_at=? WHERE id=?",
            (kw_to_use, costco_name, costco_name, sale, fee, sq, nno, st, fn, op, cat, now, existing['id'])
        )
    else:
        sale = sale_price or 0
        fee  = shipping_fee or 0
        nno  = naver_product_no or ''
        st   = status or 'SALE'
        fn   = int(from_naver) if from_naver is not None else 0
        op   = naver_origin_pno or ''
        sq   = max(1, int(split_qty)) if split_qty is not None else 1
        cat  = category or ''
        conn.execute("""INSERT INTO products
                        (product_no, store_product_name, costco_name, match_keyword,
                         unit_price, split_qty, sale_price, shipping_fee, status, from_naver,
                         naver_origin_pno, category, updated_at)
                        VALUES (?,?,?,?,0,?,?,?,?,?,?,?,?)""",
                     (nno, costco_name, costco_name, match_keyword, sq, sale, fee, st, fn, op, cat, now))
    conn.commit()
    conn.close()


def get_all_products_merged(username):
    shared = get_shared_products()
    user_prods = get_all_products(username)
    # 세 가지 인덱스: product_no / match_keyword / linked_shared_id
    user_by_pno    = {str(p.get('product_no') or '').strip(): p for p in user_prods
                      if str(p.get('product_no') or '').strip()}
    user_by_kw     = {p['match_keyword']: p for p in user_prods}
    user_by_linked = {str(p['linked_shared_id']): p for p in user_prods
                      if p.get('linked_shared_id') is not None}
    matched_user_ids = set()
    merged = []
    for sp in shared:
        kw = sp['match_keyword']
        sp_pno = str(sp.get('product_no') or '').strip()
        # 1순위: 코스트코 상품번호 매칭 (영수증 매칭 후 user.product_no가 채워진 경우)
        up = user_by_pno.get(sp_pno) if sp_pno else None
        # 2순위: match_keyword 매칭 (기존 호환)
        if up is None:
            up = user_by_kw.get(kw, {})
        # 3순위: linked_shared_id 수동 연결 (네이버 상품-공유DB 매칭)
        if not up:
            up = user_by_linked.get(str(sp['id']), {})
        if up and up.get('id'):
            matched_user_ids.add(up['id'])
        from_naver = int(up.get('from_naver') or 0) if up else 0
        # 네이버 등록 상품: user의 costco_name(=네이버 상품명)을 우선
        if from_naver and up.get('costco_name'):
            display_costco_name = up['costco_name']
            naver_name = up['costco_name']
        else:
            display_costco_name = sp['costco_name']
            naver_name = up.get('costco_name', '') if (up and from_naver) else ''
        merged.append({
            'shared_id': sp['id'],
            'product_no': sp.get('product_no', ''),
            'costco_name': display_costco_name,
            'naver_name': naver_name,
            'match_keyword': kw,
            'unit_price': sp['unit_price'],
            'store_price':  int(sp.get('store_price') or 0),
            'online_price': int(sp.get('online_price') or 0),
            'store_updated_at':  sp.get('store_updated_at') or '',
            'online_updated_at': sp.get('online_updated_at') or '',
            'split_qty': int(sp.get('split_qty', 1) or 1),
            'price_type': sp.get('price_type') or '매장',
            'image_url': sp.get('image_url', ''),
            'local_image': sp.get('local_image', ''),
            'category': sp.get('category', ''),
            'naver_category_id': sp.get('naver_category_id', '') or '',
            'extra_images': sp.get('extra_images', '') or '',
            'has_detail': bool(sp.get('detail_html', '')),
            'shared_updated_by': sp.get('updated_by', ''),
            'shared_updated_at': sp.get('updated_at', ''),
            'naver_product_no': up.get('naver_origin_pno') or up.get('product_no', '') if up else '',
            'naver_origin_pno': up.get('naver_origin_pno', '') if up else '',
            'sale_price': int(up.get('sale_price', 0) or 0),
            'shipping_fee': int(up.get('shipping_fee', 0) or 0),
            'status': up.get('status') or 'SALE',
            'from_naver': from_naver,
            'private_id': up.get('id'),
            'linked_shared_id': up.get('linked_shared_id') if up else None,
        })
    # 1단계 루프에서 매칭된 user_ids는 제외하고 나머지 user-only 상품 추가
    for up in user_prods:
        if up['id'] in matched_user_ids:
            continue
        from_naver = int(up.get('from_naver') or 0)
        naver_name = up['costco_name'] if from_naver else ''
        merged.append({
            'shared_id': None,
            'product_no': up.get('product_no', ''),
            'costco_name': up['costco_name'],
            'naver_name': naver_name,
            'match_keyword': up['match_keyword'],
            'unit_price': int(up.get('unit_price', 0) or 0),
            'store_price':  0,
            'online_price': 0,
            'store_updated_at':  '',
            'online_updated_at': '',
            'split_qty': int(up.get('split_qty', 1) or 1),
            'price_type': '매장',
            'image_url': '',
            'local_image': '',
            'category': up.get('category', ''),
            'naver_category_id': up.get('naver_category_id', '') or '',
            'extra_images': '',
            'has_detail': False,
            'shared_updated_by': '',
            'shared_updated_at': '',
            'naver_product_no': up.get('naver_origin_pno') or up.get('product_no', ''),
            'naver_origin_pno': up.get('naver_origin_pno', ''),
            'sale_price': int(up.get('sale_price', 0) or 0),
            'shipping_fee': int(up.get('shipping_fee', 0) or 0),
            'status': up.get('status') or 'SALE',
            'from_naver': from_naver,
            'private_id': up.get('id'),
            'linked_shared_id': up.get('linked_shared_id'),
        })
    return merged


def get_product_detail(shared_id):
    """네이버 등록 시 detail_html + extra_images 를 shared_id로 개별 조회."""
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT extra_images, detail_html FROM shared_products WHERE id=?", (shared_id,)
    ).fetchone()
    conn.close()
    if row:
        return row['extra_images'] or '', row['detail_html'] or ''
    return '', ''


def upsert_product(username, costco_name, keyword, price, product_no='', split_qty=1, shipping_fee=None):
    """제품 등록/갱신.

    안전장치: 기존 user 제품의 unit_price 가 있고, 새 price 가 기존의 5배를 초과하면
    박스 단가로 판단하여 unit_price 와 costco_name 을 보존 (덮어쓰지 않음).
    이는 자동 수집/매칭 경로에서 shared_products 의 박스 단가가 user 데이터를
    덮어쓰는 것을 방지함. 영수증 매칭은 별도의 apply_receipt_pno_updates 사용.
    """
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    split_qty = max(1, int(split_qty or 1))
    existing = None
    if product_no:
        existing = conn.execute(
            "SELECT id, shipping_fee, unit_price, costco_name, sale_price "
            "FROM products WHERE product_no=?", (product_no,)
        ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT id, shipping_fee, unit_price, costco_name, sale_price "
            "FROM products WHERE match_keyword=?", (keyword,)
        ).fetchone()
    if existing:
        fee = shipping_fee if shipping_fee is not None else (existing['shipping_fee'] or 0)
        # 박스 단가 보호: 기존 가격의 5배 초과하거나 기존 sale_price의 5배 초과면 기존 값 보존
        existing_price = int(existing['unit_price'] or 0)
        existing_sale  = int(existing['sale_price'] or 0)
        new_price = int(price or 0)
        new_name  = costco_name
        is_box_suspicion = False
        if new_price > 0 and existing_price > 0 and new_price > existing_price * 5:
            is_box_suspicion = True
        elif new_price > 0 and existing_sale > 0 and new_price > existing_sale * 5:
            is_box_suspicion = True
        if is_box_suspicion:
            new_price = existing_price  # 기존 단가 보존
            new_name  = existing['costco_name'] or costco_name  # 이름도 보존
        conn.execute("""UPDATE products
                        SET unit_price=?, costco_name=?, updated_at=?, product_no=?, split_qty=?, shipping_fee=?
                        WHERE id=?""",
                     (new_price, new_name, now, product_no, split_qty, fee, existing['id']))
    else:
        fee = shipping_fee if shipping_fee is not None else 0
        conn.execute("""INSERT INTO products
                        (product_no, store_product_name, costco_name, match_keyword,
                         unit_price, split_qty, shipping_fee, updated_at)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (product_no, costco_name, costco_name, keyword, price, split_qty, fee, now))
    conn.commit()
    conn.close()


# ── 주문 ─────────────────────────────────────────────────
def save_daily_orders(username, order_date, orders_df, shipping_cost, box_cost):
    """주문 데이터를 daily_orders에 저장.
    각 행의 '결제일' 컬럼이 있으면 실제 결제일자로 저장(분산),
    없거나 비어있는 행은 인자로 받은 default order_date 사용.
    DELETE는 영향받는 모든 날짜에 대해 실행.
    """
    import hashlib as _hl
    import pandas as pd
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 각 행의 실제 주문 날짜 추출 (결제일 → YYYY-MM-DD 형식)
    def _row_order_date(r):
        for col in ('결제일', '주문일시', '주문일', 'order_date'):
            v = r.get(col) if hasattr(r, 'get') else None
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            s = str(v).strip()
            if not s:
                continue
            if 'T' in s:
                s = s.split('T', 1)[0]
            elif ' ' in s:
                s = s.split(' ', 1)[0]
            # YYYY-MM-DD 패턴 검증
            if len(s) >= 10 and s[4] == '-' and s[7] == '-':
                return s[:10]
        return order_date  # 폴백: 인자 default

    # 영향받는 모든 날짜 수집 → 해당 날짜들만 DELETE
    affected_dates = set()
    for _, r in orders_df.iterrows():
        affected_dates.add(_row_order_date(r))
    for d in affected_dates:
        conn.execute("DELETE FROM daily_orders WHERE order_date=?", (d,))

    for _, r in orders_df.iterrows():
        row_date = _row_order_date(r)
        cost = r.get('구입가격', 0) or 0
        ship_fee = int(r['배송비 합계'])
        settlement = int(r['정산예정금액'])
        profit = (settlement + ship_fee) - (int(cost) + shipping_cost + box_cost)
        p_no = r.get('상품번호', '')
        conn.execute("""INSERT INTO daily_orders
            (order_date,recipient,product_name,product_no,option_info,qty,
             order_amount,shipping_fee,extra_shipping,settlement,
             cost_price,delivery_cost,box_cost,profit,matched,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row_date, r['수취인명'], r['상품명'], str(p_no), r.get('옵션정보', ''),
             int(r['수량']), int(r['최종 상품별 총 주문금액']), ship_fee,
             int(r.get('제주/도서 추가배송비', 0)), settlement,
             int(cost), shipping_cost, box_cost, profit, 1 if cost > 0 else 0, now))
    conn.commit()
    conn.close()


def get_daily_orders(username, order_date):
    conn = get_user_db(username)
    rows = conn.execute(
        "SELECT * FROM daily_orders WHERE order_date=? ORDER BY product_name", (order_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recalc_daily_orders_for_products(username, product_nos):
    """영수증 업로드 등으로 products.unit_price가 갱신된 상품번호에 대해
    저장된 daily_orders의 cost_price·profit을 현재 매입가로 재계산.

    Returns:
        업데이트된 daily_orders 행 수
    """
    if not product_nos:
        return 0
    pnos = [str(p).strip() for p in product_nos if p and str(p).strip()]
    if not pnos:
        return 0

    conn = get_user_db(username)

    # settings: 택배비·박스비
    try:
        s_row = conn.execute("SELECT value FROM settings WHERE key='shipping_cost'").fetchone()
        b_row = conn.execute("SELECT value FROM settings WHERE key='box_cost'").fetchone()
        shipping_cost = int(s_row[0]) if s_row and s_row[0] else 1800
        box_cost = int(b_row[0]) if b_row and b_row[0] else 300
    except Exception:
        shipping_cost, box_cost = 1800, 300

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cnt = 0
    for pno in pnos:
        # 현재 매입가 (가장 최신 unit_price>0인 항목 사용)
        p_row = conn.execute(
            "SELECT unit_price, split_qty FROM products "
            "WHERE product_no=? AND unit_price>0 ORDER BY updated_at DESC LIMIT 1",
            (pno,)
        ).fetchone()
        if not p_row:
            continue
        unit_price = int(p_row[0] or 0)
        split_qty  = max(1, int(p_row[1] or 1))
        if unit_price <= 0:
            continue

        # 동일 product_no를 갖는 모든 daily_orders 행 업데이트
        do_rows = conn.execute(
            "SELECT id, qty, settlement, shipping_fee, delivery_cost, box_cost "
            "FROM daily_orders WHERE product_no=?",
            (pno,)
        ).fetchall()
        for r in do_rows:
            qty        = int(r[1] or 1)
            settlement = int(r[2] or 0)
            ship_fee   = int(r[3] or 0)
            d_cost     = int(r[4] or shipping_cost)
            b_cost     = int(r[5] or box_cost)
            new_cost   = (unit_price // split_qty) * qty
            new_profit = (settlement + ship_fee) - (new_cost + d_cost + b_cost)
            conn.execute(
                "UPDATE daily_orders SET cost_price=?, profit=?, matched=1, created_at=? WHERE id=?",
                (new_cost, new_profit, now, r[0])
            )
            cnt += 1
    conn.commit()
    conn.close()
    return cnt


def save_order_history(username, full_df, cost_df=None):
    import hashlib as _hl
    import pandas as pd
    if full_df is None or full_df.empty:
        return 0
    conn = get_user_db(username)
    s_cost = b_cost = 0
    try:
        s_cost = int(conn.execute("SELECT value FROM settings WHERE key='shipping_cost'").fetchone()['value'])
        b_cost = int(conn.execute("SELECT value FROM settings WHERE key='box_cost'").fetchone()['value'])
    except Exception:
        pass
    cost_map = {}
    if cost_df is not None and '구입가격' in cost_df.columns:
        for _, cr in cost_df.iterrows():
            key = (str(cr.get('상품명', '')), str(cr.get('수취인명', '')))
            cost_map[key] = int(cr.get('구입가격', 0) or 0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    saved = 0
    for _, r in full_df.iterrows():
        order_no = str(r.get('상품주문번호', '') or '').strip()
        if not order_no:
            raw = f"{r.get('결제일','')}{r.get('수취인명','')}{r.get('상품명','')}{r.get('수량','')}"
            order_no = f"H{_hl.md5(raw.encode()).hexdigest()[:14]}"
        order_date = str(r.get('결제일', '') or r.get('주문일', '') or now[:10])
        if 'T' in order_date:
            order_date = order_date[:10]
        qty    = int(pd.to_numeric(r.get('수량', 1), errors='coerce') or 1)
        total  = int(pd.to_numeric(r.get('최종 상품별 총 주문금액', 0), errors='coerce') or 0)
        ship   = int(pd.to_numeric(r.get('배송비 합계', 0), errors='coerce') or 0)
        settle = int(pd.to_numeric(r.get('정산예정금액', 0), errors='coerce') or 0)
        unit_p = int(pd.to_numeric(r.get('상품가격', 0), errors='coerce') or 0) if '상품가격' in r.index else total // max(qty, 1)
        cost_key   = (str(r.get('상품명', '')), str(r.get('수취인명', '')))
        cost_price = cost_map.get(cost_key, 0)
        profit = (settle + ship) - (cost_price + s_cost + b_cost) if cost_price > 0 else 0
        # raw 72컬럼을 JSON으로 직렬화 (Excel 다운로드 시 원형 복원용)
        try:
            import json as _json
            _raw_dict = {k: ('' if pd.isna(v) else (int(v) if hasattr(v, 'item') else v))
                         for k, v in r.to_dict().items()}
            raw_json_str = _json.dumps(_raw_dict, default=str, ensure_ascii=False)
        except Exception:
            raw_json_str = ''
        try:
            # UPSERT: order_no가 이미 있으면 status/송장/택배사 등 갱신, 없으면 신규 INSERT
            conn.execute("""INSERT INTO order_history
                (order_no, order_group_no, order_date, recipient, buyer,
                 product_name, product_no, option_info, qty, unit_price,
                 order_amount, shipping_fee, settlement, status,
                 tracking_no, courier, cost_price, profit, created_at, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(order_no) DO UPDATE SET
                    status       = excluded.status,
                    tracking_no  = COALESCE(NULLIF(excluded.tracking_no, ''), order_history.tracking_no),
                    courier      = COALESCE(NULLIF(excluded.courier, ''),     order_history.courier),
                    settlement   = excluded.settlement,
                    shipping_fee = excluded.shipping_fee,
                    cost_price   = CASE WHEN excluded.cost_price > 0 THEN excluded.cost_price ELSE order_history.cost_price END,
                    profit       = CASE WHEN excluded.profit != 0     THEN excluded.profit     ELSE order_history.profit END,
                    raw_json     = COALESCE(NULLIF(excluded.raw_json, ''), order_history.raw_json)
                """,
                (order_no, str(r.get('주문번호', '') or ''), order_date,
                 str(r.get('수취인명', '') or ''), str(r.get('구매자명', '') or ''),
                 str(r.get('상품명', '') or ''), str(r.get('상품번호', '') or ''),
                 str(r.get('옵션정보', '') or ''), qty, unit_p, total, ship, settle,
                 str(r.get('주문상태', '') or ''), str(r.get('송장번호', '') or ''),
                 str(r.get('택배사', '') or ''), cost_price, profit, now, raw_json_str))
            saved += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return saved


# ── 미발송 주문(active) 조회 ──────────────────────────────
ACTIVE_ORDER_STATUSES = (
    # naver_api.py 수정 전: 영문 API 코드로 저장
    "PAYED",          # 결제완료
    "INSTRUCT",       # 발주확인
    "PRODUCT_READY",  # 배송준비
    # naver_api.py 수정 후: 한글로 저장됨
    "결제완료",        # PAYED 한글
    "발주확인",        # INSTRUCT 한글
    "발송대기",        # PRODUCT_READY 한글 + 옛 코드 호환
)

def get_active_orders(username):
    """order_history 테이블에서 아직 발송/완료/취소되지 않은 미발송 주문만 반환.

    화이트리스트 방식: status가 PAYED/INSTRUCT/PRODUCT_READY 중 하나이고 송장이 비어있는 주문.

    Returns:
        list[dict] - 화면 표시용 주문 행
    """
    conn = get_user_db(username)
    placeholders = ",".join("?" * len(ACTIVE_ORDER_STATUSES))
    rows = conn.execute(
        f"""SELECT * FROM order_history
            WHERE status IN ({placeholders})
              AND COALESCE(tracking_no, '') = ''
            ORDER BY order_date DESC, id DESC""",
        ACTIVE_ORDER_STATUSES,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


_NAVER_EXCEL_COLUMNS = [
    "상품주문번호", "주문번호", "배송속성", "풀필먼트사(주문 기준)", "택배사(주문 기준)", "배송방법(구매자 요청)",
    "배송방법", "택배사", "송장번호", "발송일", "판매채널", "구매자명", "구매자ID", "수취인명", "주문상태",
    "주문세부상태", "수량클레임 여부", "결제위치", "결제일", "상품번호", "상품명", "상품종류", "반품안심케어",
    "멤버십N배송", "옵션정보", "옵션관리코드", "수량", "옵션가격", "상품가격", "최종 상품별 할인액",
    "최초 상품별 할인액", "판매자 부담 할인액", "최종 상품별 총 주문금액", "최초 상품별 총 주문금액", "사은품",
    "발주확인일", "발송기한", "발송처리일", "송장출력일", "배송비 형태", "배송비 묶음번호", "배송비 유형",
    "배송비 합계", "제주/도서 추가배송비", "배송비 할인액", "판매자 상품코드", "판매자 내부코드1", "판매자 내부코드2",
    "수취인연락처1", "수취인연락처2", "통합배송지", "기본배송지", "상세배송지", "구매자연락처", "우편번호", "배송메세지",
    "출고지", "결제수단", "네이버페이 주문관리 수수료", "매출연동 수수료", "정산예정금액", "개인통관고유부호", "주문일시",
    "배송희망일", "구독신청회차", "구독진행회차", "구독배송희망일", "배송태그 유형", "출입방법 유형", "출입방법 내용",
    "수령위치 유형", "수령위치 내용",
]


def _db_row_to_naver_excel_row(r):
    """DB 행 한 개를 네이버 72컬럼 형식 dict로 변환 (raw_json 없는 옛 주문 폴백용)."""
    rec = {col: "" for col in _NAVER_EXCEL_COLUMNS}
    rec.update({
        "상품주문번호": str(r.get('order_no', '') or ''),
        "주문번호":     str(r.get('order_group_no', '') or ''),
        "수취인명":     str(r.get('recipient', '') or ''),
        "구매자명":     str(r.get('buyer', '') or ''),
        "상품명":       str(r.get('product_name', '') or ''),
        "상품번호":     str(r.get('product_no', '') or ''),
        "옵션정보":     str(r.get('option_info', '') or ''),
        "수량":         int(r.get('qty', 0) or 0),
        "상품가격":     int(r.get('unit_price', 0) or 0),
        "최종 상품별 총 주문금액": int(r.get('order_amount', 0) or 0),
        "최초 상품별 총 주문금액": int(r.get('order_amount', 0) or 0),
        "배송비 합계":  int(r.get('shipping_fee', 0) or 0),
        "정산예정금액": int(r.get('settlement', 0) or 0),
        "송장번호":     str(r.get('tracking_no', '') or ''),
        "택배사":       str(r.get('courier', '') or ''),
        "결제일":       str(r.get('order_date', '') or ''),
        "주문일시":     str(r.get('order_date', '') or ''),
        "주문상태":     "발송대기",
        "주문세부상태": "발주확인",
        "배송속성":     "당일발송",
        "배송방법":     "택배,등기,소포",
        "배송방법(구매자 요청)": "택배,등기,소포",
        "판매채널":     "스마트스토어",
    })
    return rec


def active_orders_to_naver_excel_df(username):
    """미발송 주문 → 네이버 발주발송관리 형식(72컬럼) DataFrame.

    raw_json 있으면 우선 사용 (완전한 72컬럼 데이터),
    없으면 DB 컬럼을 네이버 형식으로 매핑한 폴백 행 사용.
    → 모든 active 주문이 Excel에 포함됨.
    """
    import json as _json
    import pandas as _pd
    rows = get_active_orders(username)
    if not rows:
        return _pd.DataFrame()
    _STATUS_KO = {
        "PAYED": "결제완료", "INSTRUCT": "발주확인",
        "PRODUCT_READY": "발송대기", "DELIVERING": "배송중",
        "DELIVERED": "배송완료", "PURCHASE_DECIDED": "구매확정",
        "CANCELED": "취소완료", "RETURNED": "반품완료",
        "EXCHANGED": "교환완료", "CANCEL_NOPAY": "미결제취소",
    }
    _SUB_STATUS_KO = {"INSTRUCT": "발주확인", "CANCEL": "취소", "RETURN": "반품", "NOT_YET": "미발주", "OK": ""}

    records = []
    for r in rows:
        rj = r.get('raw_json') or ''
        if rj:
            try:
                rec = _json.loads(rj)
                # 기존 DB에 영문 코드로 저장된 경우 한글로 변환
                if rec.get("주문상태") in _STATUS_KO:
                    rec["주문상태"] = _STATUS_KO[rec["주문상태"]]
                if rec.get("주문세부상태") in _SUB_STATUS_KO:
                    rec["주문세부상태"] = _SUB_STATUS_KO[rec["주문세부상태"]]
                records.append(rec)
                continue
            except Exception:
                pass
        # 폴백: raw_json 없으면 DB 컬럼으로 네이버 형식 행 생성
        records.append(_db_row_to_naver_excel_row(r))
    return _pd.DataFrame(records)


def db_rows_to_orders_df(rows):
    """get_active_orders 결과 → 주문 업로드 화면 컬럼 형식 DataFrame."""
    import pandas as _pd
    if not rows:
        return _pd.DataFrame()
    df = _pd.DataFrame(rows)
    rename = {
        'recipient':    '수취인명',
        'buyer':        '구매자명',
        'product_name': '상품명',
        'product_no':   '상품번호',
        'option_info':  '옵션정보',
        'qty':          '수량',
        'unit_price':   '상품가격',
        'order_amount': '최종 상품별 총 주문금액',
        'shipping_fee': '배송비 합계',
        'settlement':   '정산예정금액',
        'status':       '주문상태',
        'tracking_no':  '송장번호',
        'courier':      '택배사',
        'cost_price':   '구입가격',
        'order_no':     '상품주문번호',
        'order_group_no':'주문번호',
        'order_date':   '결제일',
    }
    df = df.rename(columns=rename)
    df['제주/도서 추가배송비'] = 0
    return df


def search_order_history(username, keyword='', product_name='', date_from='', date_to='', limit=300):
    conn = get_user_db(username)
    query = "SELECT * FROM order_history WHERE 1=1"
    params = []
    if keyword:
        q = f'%{keyword}%'
        query += " AND (recipient LIKE ? OR buyer LIKE ? OR order_no LIKE ? OR order_group_no LIKE ?)"
        params.extend([q, q, q, q])
    if product_name:
        query += " AND product_name LIKE ?"
        params.append(f'%{product_name}%')
    if date_from:
        query += " AND order_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND order_date <= ?"
        params.append(date_to)
    query += " ORDER BY order_date DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 통계 ─────────────────────────────────────────────────
def get_date_range_stats(username, start_date, end_date):
    conn = get_user_db(username)
    rows = conn.execute("""SELECT order_date, COUNT(*) as cnt, SUM(qty) as total_qty,
        SUM(order_amount) as total_sales, SUM(profit) as total_profit
        FROM daily_orders WHERE order_date BETWEEN ? AND ?
        GROUP BY order_date ORDER BY order_date""", (start_date, end_date)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_monthly_stats(username):
    conn = get_user_db(username)
    rows = conn.execute("""SELECT substr(order_date, 1, 7) as month, COUNT(*) as cnt,
        SUM(order_amount) as total_sales, SUM(profit) as total_profit
        FROM daily_orders GROUP BY substr(order_date, 1, 7) ORDER BY month""").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product_ranking(username, year_month=None):
    conn = get_user_db(username)
    where = "WHERE substr(order_date, 1, 7) = ?" if year_month else ""
    params = (year_month,) if year_month else ()
    rows = conn.execute(f"""SELECT product_name, SUM(qty) as total_qty,
        SUM(order_amount) as total_sales, SUM(profit) as total_profit
        FROM daily_orders {where} GROUP BY product_name ORDER BY total_profit DESC LIMIT 10""",
        params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_saved_dates(username):
    conn = get_user_db(username)
    rows = conn.execute(
        "SELECT DISTINCT order_date FROM daily_orders ORDER BY order_date DESC"
    ).fetchall()
    conn.close()
    return [r['order_date'] for r in rows]


# ── 영수증 raw 항목 영구 저장 ────────────────────────────
def save_receipt_items(username, items):
    """영수증 PDF 파싱 결과를 DB에 영구 저장. 중복(date+pno+name)은 단가 갱신.
    items: [{'상품번호','상품명','수량','단가','receipt_date'}, ...]
    Returns: (saved_count, updated_count)
    """
    if not items:
        return 0, 0
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    updated = 0
    for it in items:
        rd = (it.get('receipt_date') or '').strip()
        pno = str(it.get('상품번호') or '').strip()
        name = (it.get('상품명') or '').strip()
        qty = int(it.get('수량') or 1)
        price = int(it.get('단가') or 0)
        if not name or not rd:
            continue
        existing = conn.execute(
            "SELECT id FROM receipt_items WHERE receipt_date=? AND product_no=? AND product_name=?",
            (rd, pno, name)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE receipt_items SET qty=?, unit_price=?, created_at=? WHERE id=?",
                (qty, price, now, existing['id'])
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO receipt_items (receipt_date, product_no, product_name, qty, unit_price, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (rd, pno, name, qty, price, now)
            )
            saved += 1
    conn.commit()
    conn.close()
    return saved, updated


def get_recent_receipt_items(username, days=90):
    """최근 N일치 영수증 항목 조회. picker용.
    Returns: [{'상품번호','상품명','수량','단가','receipt_date'}, ...]
    """
    conn = get_user_db(username)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT receipt_date, product_no, product_name, qty, unit_price "
        "FROM receipt_items WHERE receipt_date >= ? ORDER BY receipt_date DESC, id DESC",
        (cutoff,)
    ).fetchall()
    conn.close()
    return [
        {
            '상품번호': r['product_no'] or '',
            '상품명': r['product_name'] or '',
            '수량': int(r['qty'] or 1),
            '단가': int(r['unit_price'] or 0),
            'receipt_date': r['receipt_date'] or '',
        }
        for r in rows
    ]


def delete_receipt_items_by_date(username, receipt_date):
    """특정 날짜의 영수증 항목 삭제."""
    conn = get_user_db(username)
    cur = conn.execute("DELETE FROM receipt_items WHERE receipt_date=?", (receipt_date,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def get_receipt_dates(username):
    """저장된 영수증 날짜 목록."""
    conn = get_user_db(username)
    rows = conn.execute(
        "SELECT DISTINCT receipt_date, COUNT(*) as cnt FROM receipt_items "
        "GROUP BY receipt_date ORDER BY receipt_date DESC"
    ).fetchall()
    conn.close()
    return [(r['receipt_date'], r['cnt']) for r in rows]


def get_dashboard_kpi(username):
    today = __import__('datetime').datetime.today()
    w_start, w_end = get_week_range()
    m_start, m_end = get_month_range()
    lw_end   = (today - timedelta(days=today.weekday() + 1)).strftime("%Y-%m-%d")
    lw_start = (today - timedelta(days=today.weekday() + 7)).strftime("%Y-%m-%d")
    lm_last  = today.replace(day=1) - timedelta(days=1)
    lm_start = lm_last.replace(day=1).strftime("%Y-%m-%d")
    lm_end   = lm_last.strftime("%Y-%m-%d")
    conn = get_user_db(username)
    def q(s, e):
        r = conn.execute("""SELECT COUNT(*) as cnt, COALESCE(SUM(qty),0) as qty,
            COALESCE(SUM(order_amount),0) as sales, COALESCE(SUM(profit),0) as profit
            FROM daily_orders WHERE order_date BETWEEN ? AND ?""", (s, e)).fetchone()
        return dict(r) if r else {'cnt': 0, 'qty': 0, 'sales': 0, 'profit': 0}
    kpi = {
        'week': q(w_start, w_end), 'month': q(m_start, m_end),
        'last_week': q(lw_start, lw_end), 'last_month': q(lm_start, lm_end),
    }
    conn.close()
    return kpi


def get_cumulative_sales(username, until_date=None):
    """이달 1일부터 어제까지 누적 주문금액 및 건수 (매월 1일 초기화).

    Args:
        until_date: 기준일 (기본: 어제, "yyyy-mm-dd")
    Returns:
        {'total_sales': int, 'total_cnt': int, 'until': str, 'from': str}
    """
    today = datetime.today()
    if until_date is None:
        until_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    month_start = today.replace(day=1).strftime("%Y-%m-%d")
    conn = get_user_db(username)
    r = conn.execute(
        """SELECT COALESCE(SUM(order_amount), 0) as total_sales,
                  COALESCE(COUNT(*), 0) as total_cnt
           FROM daily_orders
           WHERE order_date BETWEEN ? AND ?""",
        (month_start, until_date)
    ).fetchone()
    conn.close()
    row = dict(r) if r else {'total_sales': 0, 'total_cnt': 0}
    row['until'] = until_date
    row['from']  = month_start
    return row


def get_daily_profit_trend(username, days=14):
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    conn  = get_user_db(username)
    rows  = conn.execute("""SELECT order_date, COUNT(*) as cnt, SUM(qty) as total_qty,
        SUM(order_amount) as total_sales, SUM(profit) as total_profit
        FROM daily_orders WHERE order_date BETWEEN ? AND ?
        GROUP BY order_date ORDER BY order_date""", (start, end)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_week_best_products(username):
    w_start, w_end = get_week_range()
    conn = get_user_db(username)
    rows = conn.execute("""SELECT product_name, SUM(qty) as total_qty,
        SUM(order_amount) as total_sales, SUM(profit) as total_profit
        FROM daily_orders WHERE order_date BETWEEN ? AND ?
        GROUP BY product_name ORDER BY total_profit DESC LIMIT 5""", (w_start, w_end)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_price_history_monthly(username):
    m_start = datetime.today().strftime("%Y-%m-01")
    conn = get_user_db(username)
    rows = conn.execute("""SELECT created_at, product_name, old_price, new_price, cost_price, reason, status
        FROM price_history WHERE created_at >= ? ORDER BY created_at DESC""", (m_start,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 가격 변동 이력 ────────────────────────────────────────
def save_price_changes_to_history(username, changes):
    if not changes:
        return
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for c in changes:
        conn.execute("""INSERT INTO price_change_history
            (costco_name, old_cost, new_cost, diff, diff_pct, product_no, shipping_fee, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (c['costco_name'], c['old_cost'], c['new_cost'], c['diff'], c['diff_pct'],
             c.get('product_no', ''), c.get('shipping_fee', 0), now))
    conn.commit()
    conn.close()


def get_price_change_history(username, limit=50):
    conn = get_user_db(username)
    rows = conn.execute(
        "SELECT * FROM price_change_history ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 키워드 순위 추적 ───────────────────────────────────────
def _ensure_rank_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS keyword_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_keyword TEXT NOT NULL,
            search_keyword   TEXT NOT NULL,
            naver_product_no TEXT DEFAULT '',
            store_name       TEXT DEFAULT '',
            active           INTEGER DEFAULT 1,
            created_at       TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rank_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id      INTEGER NOT NULL,
            rank_price_compare INTEGER,
            rank_total       INTEGER,
            rank_compare     INTEGER,
            checked_at       TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    # 마이그레이션: rank_compare 컬럼 추가 (3가지 순위 분리)
    try:
        conn.execute("ALTER TABLE rank_history ADD COLUMN rank_compare INTEGER")
    except Exception:
        pass
    # 인덱스 (rank_history는 rapid 조회되므로 필수)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_rank_tracking ON rank_history(tracking_id, checked_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_rank_checked ON rank_history(checked_at DESC)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass
    conn.commit()


def add_keyword_tracking(username, product_keyword, search_keyword,
                         naver_product_no='', store_name=''):
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    cur = conn.execute(
        """INSERT INTO keyword_tracking
           (product_keyword, search_keyword, naver_product_no, store_name)
           VALUES (?,?,?,?)""",
        (product_keyword, search_keyword, naver_product_no, store_name)
    )
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid


def get_keyword_trackings(username):
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    rows = conn.execute(
        "SELECT * FROM keyword_tracking WHERE active=1 ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_keyword_tracking(username, tracking_id):
    conn = get_user_db(username)
    conn.execute("UPDATE keyword_tracking SET active=0 WHERE id=?", (tracking_id,))
    conn.commit()
    conn.close()


def save_rank_result(username, tracking_id, rank_wonbu, rank_solo, rank_compare=None):
    """순위 3종 저장:
       rank_wonbu(원부) → rank_price_compare 컬럼
       rank_solo(단독)  → rank_total 컬럼
       rank_compare(가격비교 매칭 일반상품) → rank_compare 컬럼
    """
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute(
        """INSERT INTO rank_history
           (tracking_id, rank_price_compare, rank_total, rank_compare, checked_at)
           VALUES (?,?,?,?,?)""",
        (tracking_id, rank_wonbu, rank_solo, rank_compare, now)
    )
    conn.commit()
    conn.close()


def get_daily_ranks_in_month(username, tracking_id, year, month):
    """이번 달 1~31일 각 날짜의 best rank 반환 (3가지 순위 중 best)"""
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    rows = conn.execute("""
        SELECT
            CAST(SUBSTR(checked_at, 9, 2) AS INTEGER) as day,
            MIN(rank_price_compare) as wonbu,
            MIN(rank_compare) as compare,
            MIN(rank_total) as solo,
            MAX(checked_at) as last_check
        FROM rank_history
        WHERE tracking_id = ?
          AND SUBSTR(checked_at, 1, 7) = ?
        GROUP BY day
    """, (tracking_id, f"{year:04d}-{month:02d}")).fetchall()
    conn.close()
    result = {}
    for r in rows:
        day = r['day']
        ranks = {
            "wonbu":   r['wonbu'],
            "compare": r['compare'],
            "solo":    r['solo'],
        }
        # best 순위 찾기 (None 제외)
        valid = {k: v for k, v in ranks.items() if v is not None}
        if valid:
            best_type = min(valid, key=valid.get)
            result[day] = {
                "best": valid[best_type],
                "best_type": best_type,
                **ranks,
            }
    return result


def get_yearly_rank_history(username, tracking_id):
    """최근 1년치 rank_history 반환 (그래프용)"""
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    rows = conn.execute("""
        SELECT checked_at, rank_price_compare, rank_compare, rank_total
        FROM rank_history
        WHERE tracking_id = ?
          AND checked_at >= datetime('now', '-1 year', 'localtime')
        ORDER BY checked_at
    """, (tracking_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rank_drops(username, lookback_days=14, limit=20):
    """순위 하락한 추적 키워드 반환.
    각 추적 항목의 최신 2개 체크를 비교 → 순위가 떨어진 것만 (큰 폭 순)
    """
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    trackings = conn.execute(
        "SELECT id, product_keyword, search_keyword FROM keyword_tracking WHERE active=1"
    ).fetchall()

    def _best(r):
        vals = [r['rank_price_compare'], r['rank_compare'], r['rank_total']]
        vals = [v for v in vals if v is not None]
        return min(vals) if vals else None

    drops = []
    for t in trackings:
        rows = conn.execute("""
            SELECT rank_price_compare, rank_compare, rank_total, checked_at
            FROM rank_history
            WHERE tracking_id = ?
              AND checked_at >= datetime('now', ?, 'localtime')
            ORDER BY checked_at DESC
            LIMIT 2
        """, (t['id'], f"-{lookback_days} days")).fetchall()
        if len(rows) < 2:
            continue
        cur = _best(rows[0])
        prev = _best(rows[1])
        if cur is None or prev is None:
            continue
        if cur > prev:  # 숫자 증가 = 순위 하락
            drops.append({
                'tracking_id': t['id'],
                'product_keyword': t['product_keyword'],
                'search_keyword': t['search_keyword'],
                'current_rank': cur,
                'prev_rank': prev,
                'drop': cur - prev,
                'checked_at': rows[0]['checked_at'],
                'prev_checked_at': rows[1]['checked_at'],
            })
    conn.close()
    drops.sort(key=lambda x: (-x['drop'], x['current_rank']))
    return drops[:limit]


def delete_trackings_bulk(username, tracking_ids):
    """여러 추적 항목 일괄 삭제"""
    if not tracking_ids:
        return 0
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    placeholders = ','.join('?' for _ in tracking_ids)
    cur = conn.execute(
        f"DELETE FROM keyword_tracking WHERE id IN ({placeholders})",
        list(tracking_ids)
    )
    conn.execute(
        f"DELETE FROM rank_history WHERE tracking_id IN ({placeholders})",
        list(tracking_ids)
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def get_rank_history(username, tracking_id, days=30):
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    rows = conn.execute("""
        SELECT checked_at, rank_price_compare, rank_total
        FROM rank_history
        WHERE tracking_id=?
          AND checked_at >= datetime('now', ?, 'localtime')
        ORDER BY checked_at
    """, (tracking_id, f"-{days} days")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_ranks(username):
    """각 추적 항목의 최신 순위 + 메타 정보"""
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    rows = conn.execute("""
        SELECT kt.id, kt.product_keyword, kt.search_keyword,
               kt.naver_product_no, kt.store_name,
               rh.rank_price_compare, rh.rank_total, rh.rank_compare, rh.checked_at
        FROM keyword_tracking kt
        LEFT JOIN rank_history rh ON rh.id = (
            SELECT id FROM rank_history
            WHERE tracking_id = kt.id
            ORDER BY checked_at DESC LIMIT 1
        )
        WHERE kt.active=1
        ORDER BY kt.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
