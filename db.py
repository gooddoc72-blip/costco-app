"""
DB 접근 레이어 — SQLite 읽기/쓰기만 담당
UI(Streamlit)·비즈니스 로직 의존성 없음
"""
import sqlite3
import hashlib
import secrets
import os
from datetime import datetime, timedelta

from utils import get_week_range, get_month_range

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUTH_DB  = os.path.join(DATA_DIR, "auth.db")
os.makedirs(DATA_DIR, exist_ok=True)


# ── 인증 ────────────────────────────────────────────────
def hash_pw(pw):
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
    conn.close()
    if not row or row[0] != hash_pw(password):
        return None
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


def upsert_shared_product(costco_name, keyword, price, product_no='', split_qty=1,
                          updated_by='', price_type='매장', image_url=''):
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    split_qty = max(1, int(split_qty or 1))
    price_type = price_type if price_type in ('매장', '온라인') else '매장'
    existing = None
    if product_no:
        existing = conn.execute(
            "SELECT id FROM shared_products WHERE product_no=?", (product_no,)
        ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT id FROM shared_products WHERE match_keyword=?", (keyword,)
        ).fetchone()
    if existing:
        conn.execute("""UPDATE shared_products
                        SET unit_price=?, costco_name=?, product_no=?, split_qty=?,
                            updated_by=?, updated_at=?, price_type=?, image_url=?
                        WHERE id=?""",
                     (price, costco_name, product_no, split_qty,
                      updated_by, now, price_type, image_url, existing['id']))
    else:
        conn.execute("""INSERT INTO shared_products
                        (product_no, costco_name, match_keyword, unit_price, split_qty,
                         updated_by, updated_at, price_type, image_url)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (product_no, costco_name, keyword, price, split_qty,
                      updated_by, now, price_type, image_url))
    conn.commit()
    conn.close()


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
        created_at TEXT
    )""")
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
    for col_sql in [
        "ALTER TABLE products ADD COLUMN product_no TEXT DEFAULT ''",
        "ALTER TABLE daily_orders ADD COLUMN product_no TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN split_qty INTEGER DEFAULT 1",
        "ALTER TABLE products ADD COLUMN shipping_fee INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN sale_price INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(col_sql)
        except Exception:
            pass
    conn.commit()
    conn.close()


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
    rows = conn.execute("SELECT * FROM products ORDER BY costco_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_user_private(username, match_keyword, costco_name,
                        sale_price=None, shipping_fee=None, naver_product_no=None):
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    existing = conn.execute(
        "SELECT id, sale_price, shipping_fee, product_no FROM products WHERE match_keyword=?",
        (match_keyword,)
    ).fetchone()
    if existing:
        sale = sale_price   if sale_price   is not None else (existing['sale_price'] or 0)
        fee  = shipping_fee if shipping_fee is not None else (existing['shipping_fee'] or 0)
        nno  = naver_product_no if naver_product_no is not None else (existing['product_no'] or '')
        conn.execute(
            "UPDATE products SET sale_price=?, shipping_fee=?, product_no=?, updated_at=? WHERE id=?",
            (sale, fee, nno, now, existing['id'])
        )
    else:
        sale = sale_price or 0
        fee  = shipping_fee or 0
        nno  = naver_product_no or ''
        conn.execute("""INSERT INTO products
                        (product_no, store_product_name, costco_name, match_keyword,
                         unit_price, split_qty, sale_price, shipping_fee, updated_at)
                        VALUES (?,?,?,?,0,1,?,?,?)""",
                     (nno, costco_name, costco_name, match_keyword, sale, fee, now))
    conn.commit()
    conn.close()


def get_all_products_merged(username):
    shared = get_shared_products()
    user_prods = get_all_products(username)
    user_map = {p['match_keyword']: p for p in user_prods}
    merged = []
    for sp in shared:
        kw = sp['match_keyword']
        up = user_map.get(kw, {})
        merged.append({
            'shared_id': sp['id'],
            'product_no': sp.get('product_no', ''),
            'costco_name': sp['costco_name'],
            'match_keyword': kw,
            'unit_price': sp['unit_price'],
            'split_qty': int(sp.get('split_qty', 1) or 1),
            'price_type': sp.get('price_type') or '매장',
            'image_url': sp.get('image_url', ''),
            'local_image': sp.get('local_image', ''),
            'category': sp.get('category', ''),
            'shared_updated_by': sp.get('updated_by', ''),
            'shared_updated_at': sp.get('updated_at', ''),
            'naver_product_no': up.get('product_no', ''),
            'sale_price': int(up.get('sale_price', 0) or 0),
            'shipping_fee': int(up.get('shipping_fee', 0) or 0),
            'private_id': up.get('id'),
        })
    shared_kws = {sp['match_keyword'] for sp in shared}
    for up in user_prods:
        if up['match_keyword'] not in shared_kws:
            merged.append({
                'shared_id': None,
                'product_no': up.get('product_no', ''),
                'costco_name': up['costco_name'],
                'match_keyword': up['match_keyword'],
                'unit_price': int(up.get('unit_price', 0) or 0),
                'split_qty': int(up.get('split_qty', 1) or 1),
                'price_type': '매장',
                'image_url': '',
                'local_image': '',
                'category': '',
                'shared_updated_by': '',
                'shared_updated_at': '',
                'naver_product_no': up.get('product_no', ''),
                'sale_price': int(up.get('sale_price', 0) or 0),
                'shipping_fee': int(up.get('shipping_fee', 0) or 0),
                'private_id': up.get('id'),
            })
    return merged


def upsert_product(username, costco_name, keyword, price, product_no='', split_qty=1, shipping_fee=None):
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    split_qty = max(1, int(split_qty or 1))
    existing = None
    if product_no:
        existing = conn.execute(
            "SELECT id, shipping_fee FROM products WHERE product_no=?", (product_no,)
        ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT id, shipping_fee FROM products WHERE match_keyword=?", (keyword,)
        ).fetchone()
    if existing:
        fee = shipping_fee if shipping_fee is not None else (existing['shipping_fee'] or 0)
        conn.execute("""UPDATE products
                        SET unit_price=?, costco_name=?, updated_at=?, product_no=?, split_qty=?, shipping_fee=?
                        WHERE id=?""",
                     (price, costco_name, now, product_no, split_qty, fee, existing['id']))
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
    import hashlib as _hl
    import pandas as pd
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute("DELETE FROM daily_orders WHERE order_date=?", (order_date,))
    for _, r in orders_df.iterrows():
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
            (order_date, r['수취인명'], r['상품명'], str(p_no), r.get('옵션정보', ''),
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
        try:
            conn.execute("""INSERT OR IGNORE INTO order_history
                (order_no, order_group_no, order_date, recipient, buyer,
                 product_name, product_no, option_info, qty, unit_price,
                 order_amount, shipping_fee, settlement, status,
                 tracking_no, courier, cost_price, profit, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (order_no, str(r.get('주문번호', '') or ''), order_date,
                 str(r.get('수취인명', '') or ''), str(r.get('구매자명', '') or ''),
                 str(r.get('상품명', '') or ''), str(r.get('상품번호', '') or ''),
                 str(r.get('옵션정보', '') or ''), qty, unit_p, total, ship, settle,
                 str(r.get('주문상태', '') or ''), str(r.get('송장번호', '') or ''),
                 str(r.get('택배사', '') or ''), cost_price, profit, now))
            saved += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return saved


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
