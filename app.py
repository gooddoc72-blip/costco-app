"""
코스트코핫딜 주문 수익 관리 시스템 v3 (Multi-user Web Edition)
- 로그인 / 멀티유저 / 엑셀 비밀번호 자동해제 / 네이버 커머스API 연동
- 배포: Streamlit Cloud or self-hosted
"""
import streamlit as st
import pandas as pd
import sqlite3
import os
import re
import io
import json
import math
import hashlib
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, time as dtime
import plotly.graph_objects as go

# 네이버 커머스 API (선택)
try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False

# ─── 기본 설정 ───
APP_TITLE = "📦 코스트코핫딜 주문 수익 관리"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUTH_DB = os.path.join(DATA_DIR, "auth.db")
os.makedirs(DATA_DIR, exist_ok=True)

st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide")

st.markdown("""
<style>
/* 제품 목록 행 간격 축소 */
div[data-testid="stHorizontalBlock"] {
    margin-bottom: -0.4rem;
}
</style>
""", unsafe_allow_html=True)

EXTRACT_COLS = ['수취인명','상품명','옵션정보','수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']

# ═══════════════════════════════════════
# 인증 시스템
# ═══════════════════════════════════════
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
    # 앱 전역 설정
    conn.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('require_approval', '1')")
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('allow_signup', '1')")
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('costco_email', '')")
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('costco_password', '')")
    # 공유 제품 DB — 코스트코 구입 정보 (모든 판매자 공용)
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
    # 기존 auth.db 컬럼 추가 대응
    try: conn.execute("ALTER TABLE shared_products ADD COLUMN split_qty INTEGER DEFAULT 1")
    except: pass
    try: conn.execute("ALTER TABLE shared_products ADD COLUMN updated_by TEXT DEFAULT ''")
    except: pass
    try: conn.execute("ALTER TABLE shared_products ADD COLUMN price_type TEXT DEFAULT '매장'")
    except: pass
    # 크롤러 수집 = 온라인가 / 영수증 등 나머지 NULL = 매장가
    try: conn.execute("UPDATE shared_products SET price_type='온라인' WHERE updated_by='crawler'")
    except: pass
    try: conn.execute("UPDATE shared_products SET price_type='매장' WHERE price_type IS NULL OR price_type=''")
    except: pass
    try: conn.execute("ALTER TABLE shared_products ADD COLUMN image_url TEXT DEFAULT ''")
    except: pass
    try: conn.execute("ALTER TABLE shared_products ADD COLUMN local_image TEXT DEFAULT ''")
    except: pass
    try: conn.execute("ALTER TABLE shared_products ADD COLUMN naver_category_id TEXT DEFAULT ''")
    except: pass
    try: conn.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
    except: pass
    # 관리자 계정이 없으면 기본 생성
    admin = conn.execute("SELECT 1 FROM users WHERE is_admin=1").fetchone()
    if not admin:
        conn.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?)",
                     ("admin", hash_pw("admin1234"), "관리자", 1,
                      datetime.now().strftime("%Y-%m-%d %H:%M"), "active"))
    # 기존 admin은 항상 active 유지
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
    """회원가입 신청. require_approval=1이면 pending, 0이면 바로 active."""
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

def get_shared_products():
    """모든 판매자가 공유하는 코스트코 제품 목록 (auth.db)."""
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM shared_products ORDER BY costco_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def upsert_shared_product(costco_name, keyword, price, product_no='', split_qty=1, updated_by='', price_type='매장', image_url=''):
    """공유 제품 DB 저장/업데이트 (auth.db).
    match_keyword 기준으로 중복 방지, product_no가 있으면 우선 매칭.
    price_type: '매장' (영수증/오프라인) 또는 '온라인' (웹 크롤링)
    """
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    split_qty = max(1, int(split_qty or 1))
    price_type = price_type if price_type in ('매장', '온라인') else '매장'
    existing = None
    if product_no:
        existing = conn.execute("SELECT id FROM shared_products WHERE product_no=?", (product_no,)).fetchone()
    if not existing:
        existing = conn.execute("SELECT id FROM shared_products WHERE match_keyword=?", (keyword,)).fetchone()
    if existing:
        conn.execute("""UPDATE shared_products
                        SET unit_price=?, costco_name=?, product_no=?, split_qty=?,
                            updated_by=?, updated_at=?, price_type=?, image_url=?
                        WHERE id=?""",
                     (price, costco_name, product_no, split_qty, updated_by, now, price_type, image_url, existing['id']))
    else:
        conn.execute("""INSERT INTO shared_products
                        (product_no, costco_name, match_keyword, unit_price, split_qty, updated_by, updated_at, price_type, image_url)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (product_no, costco_name, keyword, price, split_qty, updated_by, now, price_type, image_url))
    conn.commit()
    conn.close()

def delete_shared_product(shared_id):
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("DELETE FROM shared_products WHERE id=?", (shared_id,))
    conn.commit()
    conn.close()

def match_shared_product(product_name, product_no=None):
    """이름·번호로 공유 제품 검색. _token_score 기반 유사도 매칭."""
    products = get_shared_products()
    if not products:
        return None
    # 1순위: 상품번호
    if product_no:
        for p in products:
            if str(p.get('product_no', '')) == str(product_no):
                return p
    # 2순위: 이름 토큰 유사도
    best_score, best_p = 0.0, None
    for p in products:
        for field in ('match_keyword', 'costco_name'):
            score = _token_score(product_name, p.get(field) or '')
            if score > best_score:
                best_score, best_p = score, p
    return best_p if best_score >= 0.5 else None

def get_all_products_merged(username):
    """공유 제품(shared_products) + 사용자 개인(sale_price, shipping_fee, naver product_no) 합산."""
    shared = get_shared_products()
    user_prods = get_all_products(username)
    # match_keyword 기준으로 user private 맵 생성
    user_map = {p['match_keyword']: p for p in user_prods}
    merged = []
    for sp in shared:
        kw = sp['match_keyword']
        up = user_map.get(kw, {})
        merged.append({
            # 공유 필드 (읽기 전용)
            'shared_id': sp['id'],
            'product_no': sp.get('product_no', ''),       # 코스트코 상품번호
            'costco_name': sp['costco_name'],
            'match_keyword': kw,
            'unit_price': sp['unit_price'],
            'split_qty': int(sp.get('split_qty', 1) or 1),
            'shared_updated_by': sp.get('updated_by', ''),
            'shared_updated_at': sp.get('updated_at', ''),
            # 개인 필드 (수정 가능)
            'naver_product_no': up.get('product_no', ''),  # 네이버 상품번호
            'sale_price': int(up.get('sale_price', 0) or 0),
            'shipping_fee': int(up.get('shipping_fee', 0) or 0),
            'private_id': up.get('id'),
        })
    # 공유에 없고 사용자 DB에만 있는 제품도 포함 (레거시 데이터)
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
                'shared_updated_by': '',
                'shared_updated_at': '',
                'naver_product_no': up.get('product_no', ''),
                'sale_price': int(up.get('sale_price', 0) or 0),
                'shipping_fee': int(up.get('shipping_fee', 0) or 0),
                'private_id': up.get('id'),
            })
    return merged

def upsert_user_private(username, match_keyword, costco_name, sale_price=None, shipping_fee=None, naver_product_no=None):
    """사용자 개인 DB — 판매가·배송비·네이버 상품번호만 저장/업데이트."""
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    existing = conn.execute("SELECT id, sale_price, shipping_fee, product_no FROM products WHERE match_keyword=?", (match_keyword,)).fetchone()
    if existing:
        sale  = sale_price   if sale_price   is not None else (existing['sale_price'] or 0)
        fee   = shipping_fee if shipping_fee is not None else (existing['shipping_fee'] or 0)
        nno   = naver_product_no if naver_product_no is not None else (existing['product_no'] or '')
        conn.execute("UPDATE products SET sale_price=?, shipping_fee=?, product_no=?, updated_at=? WHERE id=?",
                     (sale, fee, nno, now, existing['id']))
    else:
        sale = sale_price   or 0
        fee  = shipping_fee or 0
        nno  = naver_product_no or ''
        # unit_price=0 으로 삽입 (실제 가격은 shared_products 에서 읽음)
        conn.execute("""INSERT INTO products
                        (product_no, store_product_name, costco_name, match_keyword,
                         unit_price, split_qty, sale_price, shipping_fee, updated_at)
                        VALUES (?,?,?,?,0,1,?,?,?)""",
                     (nno, costco_name, costco_name, match_keyword, sale, fee, now))
    conn.commit()
    conn.close()

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
    row = conn.execute("SELECT username, expires_at FROM sessions WHERE token=?", (token,)).fetchone()
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

def get_user_info(username):
    conn = sqlite3.connect(AUTH_DB)
    row = conn.execute("SELECT username, display_name, is_admin FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if row:
        return {"username": row[0], "display_name": row[1], "is_admin": row[2]}
    return None

def _get_qparam(key, default=''):
    try:
        return st.query_params.get(key, default)
    except Exception:
        return st.experimental_get_query_params().get(key, [default])[0]

def _set_qparam(key, value):
    try:
        st.query_params[key] = value
    except Exception:
        st.experimental_set_query_params(**{key: value})

def _clear_qparams():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

init_auth_db()

# ═══════════════════════════════════════
# 사용자별 DB
# ═══════════════════════════════════════
def get_user_db(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    
    # 💡 [핵심 수정] timeout=15 와 check_same_thread=False 를 추가했습니다.
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
    c.execute("INSERT OR IGNORE INTO settings VALUES ('shipping_cost', '1800')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('box_cost', '300')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('excel_password', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('api_client_id', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('api_client_secret', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('telegram_token', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('telegram_chat_id', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('kakao_api_key', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('kakao_access_token', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('kakao_refresh_token', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('cj_api_id', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('cj_api_pw', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('cj_account_no', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('default_courier', 'CJGLS')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('target_margin', '10')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('max_increase_pct', '20')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('auto_shopping_enabled', '0')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('auto_shopping_time', '09:00')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('auto_shipping_enabled', '0')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('auto_shipping_time', '14:00')")
    c.execute("""CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT, origin_product_no TEXT,
        old_price INTEGER, new_price INTEGER,
        cost_price INTEGER, reason TEXT,
        status TEXT DEFAULT 'applied',
        created_at TEXT NOT NULL
    )""")
    
    # 컬럼 추가 (기존 DB 대응)
    try: c.execute("ALTER TABLE products ADD COLUMN product_no TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE daily_orders ADD COLUMN product_no TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE products ADD COLUMN split_qty INTEGER DEFAULT 1")
    except: pass
    try: c.execute("ALTER TABLE products ADD COLUMN shipping_fee INTEGER DEFAULT 0")
    except: pass
    # sale_price: 네이버 스마트스토어 판매가 (주문 목록에서 자동 업데이트)
    try: c.execute("ALTER TABLE products ADD COLUMN sale_price INTEGER DEFAULT 0")
    except: pass
    # price_change_history: 영수증 기반 가격 변동 감지 이력
    c.execute("""CREATE TABLE IF NOT EXISTS price_change_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        costco_name TEXT,
        old_cost INTEGER,
        new_cost INTEGER,
        diff INTEGER,
        diff_pct REAL,
        product_no TEXT DEFAULT '',
        shipping_fee INTEGER DEFAULT 0,
        notified INTEGER DEFAULT 0,
        naver_updated INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    )""")

    conn.commit()
    conn.close()

# ─── DB 헬퍼 (사용자별) ───
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


def _token_score(a: str, b: str) -> float:
    """두 문자열의 한글/영숫자 토큰 겹침 비율 (짧은 쪽 기준)."""
    ta = set(re.findall(r'[가-힣a-zA-Z0-9]+', a.lower()))
    tb = set(re.findall(r'[가-힣a-zA-Z0-9]+', b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _find_db_product(products, order_name: str, order_pno: str = '', pno_map: dict = None):
    """주문 상품을 제품 DB에서 찾는다.
    1순위: 상품번호(네이버) 직접 매칭
    2순위: 이름 토큰 유사도 ≥ 0.5 (store_product_name / match_keyword / costco_name 모두 시도)
    """
    # 1순위: 상품번호 매칭
    if order_pno and pno_map:
        p = pno_map.get(str(order_pno).strip())
        if p:
            return p

    # 2순위: 이름 토큰 유사도
    best_score, best_p = 0.0, None
    for p in products:
        for field in ('store_product_name', 'match_keyword', 'costco_name'):
            score = _token_score(order_name, p.get(field) or '')
            if score > best_score:
                best_score, best_p = score, p
    return best_p if best_score >= 0.5 else None


def update_product_info_from_orders(username, orders_df):
    """주문 목록 → 사용자 개인 DB의 shipping_fee·sale_price 동시 갱신.

    공유 DB(shared_products) + 개인 DB 모두 검색해 매칭.
    매칭된 제품의 판매가·배송비는 사용자 개인 DB(upsert_user_private)에만 저장.
    배송비: 0이 찍힌 건 제외, 비0 최댓값 (전부 무료면 0)
    판매가: 비0 최댓값
    """
    if '상품명' not in orders_df.columns:
        return 0, 0

    merged = get_all_products_merged(username)
    if not merged:
        return 0, 0

    pno_map = {str(p['product_no']): p for p in merged if p.get('product_no')}

    has_pno    = '상품번호' in orders_df.columns
    has_fee    = '배송비 합계' in orders_df.columns
    has_sprice = '상품가격' in orders_df.columns
    has_total  = '최종 상품별 총 주문금액' in orders_df.columns
    has_qty    = '수량' in orders_df.columns

    group_key = ['상품명'] + (['상품번호'] if has_pno else [])
    agg_map = {}

    for keys, grp in orders_df.groupby(group_key):
        if isinstance(keys, str):
            name, pno = keys, ''
        else:
            name = keys[0]
            pno  = str(keys[1]) if len(keys) > 1 else ''

        if has_fee:
            fees = pd.to_numeric(grp['배송비 합계'], errors='coerce').fillna(0).astype(int)
            nz = fees[fees > 0]
            fee_val = int(nz.max()) if len(nz) > 0 else 0
        else:
            fee_val = -1   # -1 = 컬럼 없음 → 업데이트 안 함

        if has_sprice:
            prices = pd.to_numeric(grp['상품가격'], errors='coerce').fillna(0).astype(int)
        elif has_total and has_qty:
            totals = pd.to_numeric(grp['최종 상품별 총 주문금액'], errors='coerce').fillna(0).astype(int)
            qtys   = pd.to_numeric(grp['수량'], errors='coerce').fillna(1).astype(int).replace(0, 1)
            prices = (totals // qtys)
        else:
            prices = pd.Series([], dtype=int)

        nz_p = prices[prices > 0] if len(prices) > 0 else pd.Series([], dtype=int)
        sale_val = int(nz_p.max()) if len(nz_p) > 0 else 0

        agg_map[(str(name), pno)] = {'fee': fee_val, 'sale': sale_val}

    fee_cnt = sale_cnt = 0

    for (name, pno), vals in agg_map.items():
        matched = _find_db_product(merged, name, pno, pno_map)
        if not matched:
            continue

        kw          = matched.get('match_keyword', '')
        costco_name = matched.get('costco_name', name)
        fee_val     = vals['fee']
        sale_val    = vals['sale']

        if fee_val >= 0 or sale_val > 0:
            upsert_user_private(
                username, kw, costco_name,
                sale_price=sale_val if sale_val > 0 else None,
                shipping_fee=fee_val if fee_val >= 0 else None,
            )
            if fee_val >= 0:
                fee_cnt += 1
            if sale_val > 0:
                sale_cnt += 1

    return fee_cnt, sale_cnt


# 하위 호환 래퍼 (기존 호출부가 개별 함수를 쓸 경우 대비)
def update_product_shipping_fees(username, orders_df):
    fee_cnt, _ = update_product_info_from_orders(username, orders_df)
    return fee_cnt

def update_product_sale_price(username, orders_df):
    _, sale_cnt = update_product_info_from_orders(username, orders_df)
    return sale_cnt


def save_order_history(username, full_df, cost_df=None):
    """주문 전체 이력 누적 저장 (상품주문번호 기준 중복 방지).
    full_df: order_full (상품주문번호 포함 전체 컬럼)
    cost_df: 구입가격이 계산된 df (선택 — profit 계산에 사용)
    """
    if full_df is None or full_df.empty:
        return 0

    conn = get_user_db(username)
    s_cost = 0
    b_cost = 0
    try:
        s_cost = int(conn.execute("SELECT value FROM settings WHERE key='shipping_cost'").fetchone()['value'])
        b_cost = int(conn.execute("SELECT value FROM settings WHERE key='box_cost'").fetchone()['value'])
    except Exception:
        pass

    # cost_df를 상품명+수취인 기준으로 빠르게 조회하기 위한 맵
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
            order_no = f"H{hashlib.md5(raw.encode()).hexdigest()[:14]}"

        order_date = str(r.get('결제일', '') or r.get('주문일', '') or now[:10])
        if 'T' in order_date:
            order_date = order_date[:10]

        qty = int(pd.to_numeric(r.get('수량', 1), errors='coerce') or 1)
        total = int(pd.to_numeric(r.get('최종 상품별 총 주문금액', 0), errors='coerce') or 0)
        ship = int(pd.to_numeric(r.get('배송비 합계', 0), errors='coerce') or 0)
        settle = int(pd.to_numeric(r.get('정산예정금액', 0), errors='coerce') or 0)

        if '상품가격' in r.index:
            unit_p = int(pd.to_numeric(r.get('상품가격', 0), errors='coerce') or 0)
        else:
            unit_p = total // qty if qty > 0 else 0

        cost_key = (str(r.get('상품명', '')), str(r.get('수취인명', '')))
        cost_price = cost_map.get(cost_key, 0)
        profit = (settle + ship) - (cost_price + s_cost + b_cost) if cost_price > 0 else 0

        try:
            conn.execute("""INSERT OR IGNORE INTO order_history
                (order_no, order_group_no, order_date, recipient, buyer,
                 product_name, product_no, option_info, qty, unit_price,
                 order_amount, shipping_fee, settlement, status,
                 tracking_no, courier, cost_price, profit, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (order_no,
                 str(r.get('주문번호', '') or ''),
                 order_date,
                 str(r.get('수취인명', '') or ''),
                 str(r.get('구매자명', '') or ''),
                 str(r.get('상품명', '') or ''),
                 str(r.get('상품번호', '') or ''),
                 str(r.get('옵션정보', '') or ''),
                 qty, unit_p, total, ship, settle,
                 str(r.get('주문상태', '') or ''),
                 str(r.get('송장번호', '') or ''),
                 str(r.get('택배사', '') or ''),
                 cost_price, profit, now))
            saved += 1
        except Exception:
            pass

    conn.commit()
    conn.close()
    return saved


def search_order_history(username, keyword='', product_name='', date_from='', date_to='', limit=300):
    """주문 이력 검색 (수취인/구매자/주문번호 키워드 + 상품명 + 날짜 범위)"""
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


def detect_price_changes(username, parsed_items):
    """영수증 파싱 결과와 공유 제품DB를 비교해 가격 변동 목록 반환.
    - 기준 가격: shared_products (공유 매입가)
    - 배송비: 사용자 개인 DB에서 조회
    반환: [{'costco_name', 'old_cost', 'new_cost', 'diff', 'diff_pct',
             'product_no', 'split_qty', 'shipping_fee'}, ...]
    """
    shared = get_shared_products()
    if not shared:
        return []

    user_prods = get_all_products(username)
    user_fee_map = {up['match_keyword']: int(up.get('shipping_fee', 0) or 0) for up in user_prods}

    changes = []
    for item in parsed_items:
        receipt_name = item.get('상품명', '')
        receipt_price = int(item.get('단가', 0))
        receipt_no = str(item.get('상품번호', ''))
        if not receipt_name or receipt_price <= 0:
            continue

        sp = match_shared_product(receipt_name, product_no=receipt_no if receipt_no else None)
        if sp is None:
            continue

        old_price = int(sp.get('unit_price', 0) or 0)
        if old_price <= 0 or old_price == receipt_price:
            continue

        diff = receipt_price - old_price
        diff_pct = round(diff / old_price * 100, 1)
        changes.append({
            'costco_name': sp.get('costco_name') or receipt_name,
            'old_cost': old_price,
            'new_cost': receipt_price,
            'diff': diff,
            'diff_pct': diff_pct,
            'product_no': sp.get('product_no', ''),
            'split_qty': int(sp.get('split_qty', 1) or 1),
            'shipping_fee': user_fee_map.get(sp['match_keyword'], 0),
            'shared_id': sp.get('id'),
        })

    return changes


def save_price_changes_to_history(username, changes):
    """가격 변동 이력을 price_change_history 테이블에 저장"""
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


def build_price_alert_msg(changes, today_str=None):
    """카카오/텔레그램 가격 변동 알림 메시지 생성"""
    if not today_str:
        today_str = datetime.now().strftime("%Y-%m-%d")

    up = [c for c in changes if c['diff'] > 0]
    down = [c for c in changes if c['diff'] < 0]

    lines = [f"[코스트코 가격 변동 알림] {today_str}", ""]

    def fee_str(f):
        return "무료" if f == 0 else f"{f:,}원"

    if up:
        lines.append(f"🔺 가격 인상 ({len(up)}건)")
        for c in up:
            lines.append(
                f"• {c['costco_name']}\n"
                f"  {c['old_cost']:,} → {c['new_cost']:,}원 "
                f"(+{c['diff']:,}원, +{c['diff_pct']}%)\n"
                f"  고객 배송비: {fee_str(c['shipping_fee'])}"
            )
        lines.append("")

    if down:
        lines.append(f"🔻 가격 인하 ({len(down)}건)")
        for c in down:
            lines.append(
                f"• {c['costco_name']}\n"
                f"  {c['old_cost']:,} → {c['new_cost']:,}원 "
                f"({c['diff']:,}원, {c['diff_pct']}%)\n"
                f"  고객 배송비: {fee_str(c['shipping_fee'])}"
            )
        lines.append("")

    lines.append("※ 앱 접속 후 네이버 판매가를 검토하고 적용해주세요.")
    return "\n".join(lines)


def upsert_product(username, costco_name, keyword, price, product_no='', split_qty=1, shipping_fee=None):
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    split_qty = max(1, int(split_qty or 1))

    existing = None
    if product_no:
        existing = conn.execute("SELECT id, shipping_fee FROM products WHERE product_no=?", (product_no,)).fetchone()
    if not existing:
        existing = conn.execute("SELECT id, shipping_fee FROM products WHERE match_keyword=?", (keyword,)).fetchone()

    # shipping_fee=None이면 기존 값 유지
    if existing:
        fee = shipping_fee if shipping_fee is not None else (existing['shipping_fee'] or 0)
        conn.execute("""UPDATE products
                        SET unit_price=?, costco_name=?, updated_at=?, product_no=?, split_qty=?, shipping_fee=?
                        WHERE id=?""",
                     (price, costco_name, now, product_no, split_qty, fee, existing['id']))
    else:
        fee = shipping_fee if shipping_fee is not None else 0
        conn.execute("""INSERT INTO products
                        (product_no, store_product_name, costco_name, match_keyword, unit_price, split_qty, shipping_fee, updated_at)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (product_no, costco_name, costco_name, keyword, price, split_qty, fee, now))
    conn.commit()
    conn.close()

def save_daily_orders(username, order_date, orders_df, shipping_cost, box_cost):
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
            (order_date,recipient,product_name,product_no,option_info,qty,order_amount,shipping_fee,extra_shipping,settlement,cost_price,delivery_cost,box_cost,profit,matched,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (order_date, r['수취인명'], r['상품명'], str(p_no), r.get('옵션정보',''), int(r['수량']),
             int(r['최종 상품별 총 주문금액']), ship_fee, int(r.get('제주/도서 추가배송비',0)),
             settlement, int(cost), shipping_cost, box_cost, profit, 1 if cost > 0 else 0, now))
    conn.commit()
    conn.close()

def get_daily_orders(username, order_date):
    conn = get_user_db(username)
    rows = conn.execute("SELECT * FROM daily_orders WHERE order_date=? ORDER BY product_name", (order_date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

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
    rows = conn.execute(f"""SELECT product_name, SUM(qty) as total_qty, SUM(order_amount) as total_sales, SUM(profit) as total_profit
        FROM daily_orders {where} GROUP BY product_name ORDER BY total_profit DESC LIMIT 10""", params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_saved_dates(username):
    conn = get_user_db(username)
    rows = conn.execute("SELECT DISTINCT order_date FROM daily_orders ORDER BY order_date DESC").fetchall()
    conn.close()
    return [r['order_date'] for r in rows]

def get_week_range():
    today = datetime.today()
    mon = today - timedelta(days=today.weekday())
    return mon.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

def get_month_range():
    today = datetime.today()
    return today.strftime("%Y-%m-01"), today.strftime("%Y-%m-%d")

def get_dashboard_kpi(username):
    today = datetime.today()
    w_start, w_end = get_week_range()
    m_start, m_end = get_month_range()
    lw_end = (today - timedelta(days=today.weekday() + 1)).strftime("%Y-%m-%d")
    lw_start = (today - timedelta(days=today.weekday() + 7)).strftime("%Y-%m-%d")
    lm_last = today.replace(day=1) - timedelta(days=1)
    lm_start = lm_last.replace(day=1).strftime("%Y-%m-%d")
    lm_end = lm_last.strftime("%Y-%m-%d")
    conn = get_user_db(username)
    def q(s, e):
        r = conn.execute("""SELECT COUNT(*) as cnt, COALESCE(SUM(qty),0) as qty,
            COALESCE(SUM(order_amount),0) as sales, COALESCE(SUM(profit),0) as profit
            FROM daily_orders WHERE order_date BETWEEN ? AND ?""", (s, e)).fetchone()
        return dict(r) if r else {'cnt': 0, 'qty': 0, 'sales': 0, 'profit': 0}
    kpi = {
        'week': q(w_start, w_end),
        'month': q(m_start, m_end),
        'last_week': q(lw_start, lw_end),
        'last_month': q(lm_start, lm_end),
    }
    conn.close()
    return kpi

def get_daily_profit_trend(username, days=14):
    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    conn = get_user_db(username)
    rows = conn.execute("""SELECT order_date, COUNT(*) as cnt, SUM(qty) as total_qty,
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

# ═══════════════════════════════════════
# 엑셀 비밀번호 해제 + 읽기
# ═══════════════════════════════════════
def decrypt_excel(uploaded_file, password):
    try:
        import msoffcrypto
    except ImportError:
        st.warning("msoffcrypto 라이브러리가 설치되지 않았습니다. `pip install msoffcrypto-tool`")
        return uploaded_file, None

    try:
        # Streamlit UploadedFile을 BytesIO로 변환
        uploaded_file.seek(0)
        raw = io.BytesIO(uploaded_file.read())
        raw.seek(0)

        f = msoffcrypto.OfficeFile(raw)
        if not f.is_encrypted():
            raw.seek(0)
            return raw, None

        f.load_key(password=password)
        decrypted = io.BytesIO()
        f.decrypt(decrypted)
        decrypted.seek(0)
        return decrypted, None
    except Exception as e:
        uploaded_file.seek(0)
        return None, str(e)

def read_excel_auto(uploaded_file, password=""):
    decrypt_error = None
    if password:
        result, decrypt_error = decrypt_excel(uploaded_file, password)
        if result is None:
            return None, f"비밀번호 해제 실패: {decrypt_error}"
        uploaded_file = result

    for engine in ['openpyxl', 'xlrd']:
        for skip in [0, 1]:
            try:
                uploaded_file.seek(0)
                df = pd.read_excel(uploaded_file, engine=engine, header=skip)
                first_col = str(df.columns[0]) if len(df.columns) > 0 else ""
                if skip == 0 and len(first_col) > 50:
                    continue
                if len(df) > 0 and len(df.columns) > 3:
                    return df, None
            except Exception as e:
                last_error = str(e)

    for enc in ['utf-8', 'euc-kr']:
        try:
            uploaded_file.seek(0)
            dfs = pd.read_html(uploaded_file, encoding=enc)
            if dfs: return dfs[0], None
        except Exception:
            pass

    if decrypt_error:
        return None, f"비밀번호 해제 실패: {decrypt_error}"
    return None, f"파일 읽기 실패: {last_error if 'last_error' in dir() else '알 수 없는 오류'}"

# ═══════════════════════════════════════
# 제품명 매칭 (1차 3글자 → 2차 5글자)
# ═══════════════════════════════════════
def clean_name(name):
    if pd.isna(name) or not isinstance(name, str):
        return ""
    name = str(name).replace("'", "").replace("\u2018", "").replace("\u2019", "")
    return name.replace(" ", "").lower()

def has_meaningful_char(s):
    korean = sum(1 for c in s if '\uac00' <= c <= '\ud7a3')
    english = sum(1 for c in s if c.isalpha() and not ('\uac00' <= c <= '\ud7a3'))
    return korean >= 1 or english >= 3

def get_ngrams(name, n):
    cleaned = clean_name(name)
    if len(cleaned) < n: return set()
    return set(cleaned[i:i+n] for i in range(len(cleaned) - n + 1) if has_meaningful_char(cleaned[i:i+n]))

def calc_match_score(name_a, name_b):
    match_3 = get_ngrams(name_a, 3) & get_ngrams(name_b, 3)
    s3 = len(match_3)
    if s3 == 0: return 0
    s5 = len(get_ngrams(name_a, 5) & get_ngrams(name_b, 5))
    return s3 + s5 * 3

MIN_MATCH_SCORE = 1

def match_product_to_db(username, store_product_name, product_no=None):
    """제품 매칭: shared_products(공유 구입가) 우선, 없으면 사용자 DB 폴백.
    반환 dict에 unit_price / split_qty / costco_name / sale_price / shipping_fee 포함.
    """
    # ── 1순위: 공유 DB 매칭 ──
    sp = match_shared_product(store_product_name, product_no=product_no)
    if sp:
        # 개인 private 필드 병합 (sale_price, shipping_fee, naver product_no)
        user_prods = get_all_products(username)
        up = next((p for p in user_prods if p['match_keyword'] == sp['match_keyword']), {})
        return {
            **sp,
            'sale_price':   int(up.get('sale_price',   0) or 0),
            'shipping_fee': int(up.get('shipping_fee', 0) or 0),
            # product_no 는 공유(코스트코)번호, naver_product_no 는 개인(네이버)번호
            'naver_product_no': up.get('product_no', ''),
        }

    # ── 2순위: 사용자 개인 DB (레거시·미등록 공유 제품 대비) ──
    products = get_all_products(username)
    if not products:
        return None
    if product_no:
        for p in products:
            if p.get('product_no') == str(product_no):
                return p
    candidates = []
    for p in products:
        s1 = calc_match_score(p.get('costco_name', ''), store_product_name)
        s2 = calc_match_score(p['match_keyword'], store_product_name)
        score = max(s1, s2)
        if score >= MIN_MATCH_SCORE:
            candidates.append((p, score))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]

def match_receipt_to_orders(receipt_items, order_product_names):
    matches = {}
    for store_name in order_product_names:
        best_idx, best_score = None, 0
        for i, item in enumerate(receipt_items):
            score = calc_match_score(item['상품명'], store_name)
            if score > best_score:
                best_score, best_idx = score, i
        if best_idx is not None and best_score >= MIN_MATCH_SCORE:
            matches[store_name] = receipt_items[best_idx]
    return matches

# ═══════════════════════════════════════
# 영수증 PDF 파싱
# ═══════════════════════════════════════
def parse_costco_receipt_pdf(uploaded_pdf):
    """영수증 PDF 파싱. 반환: (items, error_msg) — 성공 시 error_msg=None"""
    try:
        import pdfplumber
    except ImportError:
        return None, "pdfplumber 미설치 (pip install pdfplumber)"

    # UploadedFile / BytesIO 모두 안전하게 BytesIO로 변환
    try:
        if hasattr(uploaded_pdf, 'read'):
            uploaded_pdf.seek(0)
            raw = io.BytesIO(uploaded_pdf.read())
        else:
            raw = uploaded_pdf
            raw.seek(0)
    except Exception as e:
        return None, f"파일 읽기 오류: {e}"

    try:
        with pdfplumber.open(raw) as pdf:
            full_text = ""
            for page in pdf.pages:
                full_text += (page.extract_text() or "")
    except Exception as e:
        return None, f"PDF 열기 오류: {e}"

    if not full_text.strip():
        return None, "텍스트 추출 실패 (스캔 이미지 PDF이거나 암호화된 파일)"

    items = []
    lines = full_text.split('\n')
    skip_next = False
    for i in range(len(lines) - 1):
        if skip_next:
            skip_next = False
            continue
        line, next_line = lines[i].strip(), lines[i + 1].strip()
        if line == '*** CPN':
            skip_next = True
            continue
        if 'CPN' in line or 'IRC' in line:
            continue
        m = re.match(r'^(\d{4,7})\s+(\d+)\s+([\d,]+)\s+([\d,\-\s]+)\s*[TFN]?\s*$', next_line)
        if m:
            p_no = m.group(1)
            name = line
            qty = int(m.group(2))
            unit_price = int(m.group(3).replace(',', ''))
            if any(x in name for x in ['코스트코코리아', '대표자', '부산시', '판매', '닫기', 'costco', 'http']):
                continue
            if not name or len(name) < 2:
                continue
            items.append({'상품번호': p_no, '상품명': name, '수량': qty, '단가': unit_price})
            skip_next = True

    if items:
        return items, None

    # 상품 미인식 — 추출된 원문을 오류 메시지에 포함
    preview = full_text[:800].strip()
    return None, f"상품 패턴 미인식 (텍스트 {len(full_text)}자 추출)\n\n--- 추출 원문 ---\n{preview}"

# ═══════════════════════════════════════
# 유틸
# ═══════════════════════════════════════
def fmt(n):
    if n is None: return "-"
    return f"{int(n):,}"

def to_id_str(val):
    try: return str(int(float(val)))
    except: return str(val).strip()

def extract_pack_qty(option_str, name_str=""):
    """옵션정보·상품명에서 묶음수량 추출 (예: '2구', '3개묶음', '1+1' → 2, 3, 2)"""
    text = f"{option_str or ''} {name_str or ''}".strip()
    if not text:
        return 1
    # 1+1, 2+1 등 덤 패턴
    m = re.search(r'(\d)\s*\+\s*(\d)', text)
    if m:
        v = int(m.group(1)) + int(m.group(2))
        if 1 < v <= 30:
            return v
    # N구, N개묶음, N개세트, NP, NSET, xN
    for pat in [r'(\d+)\s*구\b', r'(\d+)\s*개\s*묶음', r'(\d+)\s*개\s*세트',
                r'(\d+)\s*p(?:ack)?\b', r'(\d+)\s*set\b', r'x\s*(\d+)\b']:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = int(m.group(1))
            if 1 < v <= 30:
                return v
    return 1


# ═══════════════════════════════════════
# 로그인 화면
# ═══════════════════════════════════════
if 'user' not in st.session_state:
    st.session_state['user'] = None

if st.session_state['user'] is None:
    # URL 쿼리 파라미터로 저장된 세션 토큰 확인 → 자동 로그인
    _sid = _get_qparam('sid')
    if _sid:
        _auto_username = get_session_user(_sid)
        if _auto_username:
            _auto_user = get_user_info(_auto_username)
            if _auto_user:
                st.session_state['user'] = _auto_user
                st.session_state['_sid'] = _sid
                init_user_db(_auto_username)
                st.rerun()
        else:
            _clear_qparams()

    st.markdown("<h1 style='text-align:center;margin-top:60px'>📦 코스트코핫딜</h1>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align:center;color:gray'>주문 수익 관리 시스템</h3>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.6, 1])
    with col2:
        allow_signup = get_global_setting('allow_signup', '1')
        tab_labels = ["🔑 로그인", "📝 회원가입"] if allow_signup == '1' else ["🔑 로그인"]
        tabs = st.tabs(tab_labels)

        # ── 로그인 탭 ──
        with tabs[0]:
            with st.form("login_form"):
                username = st.text_input("아이디")
                password = st.text_input("비밀번호", type="password")
                remember_me = st.checkbox("자동 로그인 (30일간 유지)", value=True)
                submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")

                if submitted:
                    result = check_login(username, password)
                    if result == "pending":
                        st.warning("⏳ 관리자 승인 대기 중입니다. 잠시 후 다시 시도해주세요.")
                    elif result == "rejected":
                        st.error("❌ 가입 신청이 거절되었습니다. 관리자에게 문의하세요.")
                    elif result:
                        st.session_state['user'] = result
                        init_user_db(result['username'])
                        if remember_me:
                            _token = create_session(result['username'], days=30)
                            st.session_state['_sid'] = _token
                            _set_qparam('sid', _token)
                        st.rerun()
                    else:
                        st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

        # ── 회원가입 탭 ──
        if allow_signup == '1' and len(tabs) > 1:
            with tabs[1]:
                require_approval = get_global_setting('require_approval', '1')
                if require_approval == '1':
                    st.info("📋 가입 신청 후 관리자 승인이 완료되면 로그인 가능합니다.")
                else:
                    st.info("✅ 가입 즉시 로그인 가능합니다.")

                with st.form("signup_form"):
                    reg_id   = st.text_input("아이디 (영문·숫자, 4자 이상)")
                    reg_name = st.text_input("이름 / 업체명")
                    reg_pw   = st.text_input("비밀번호 (6자 이상)", type="password")
                    reg_pw2  = st.text_input("비밀번호 확인", type="password")
                    reg_ok   = st.form_submit_button("회원가입 신청", use_container_width=True, type="primary")

                    if reg_ok:
                        reg_id = reg_id.strip()
                        reg_name = reg_name.strip()
                        if len(reg_id) < 4 or not reg_id.isalnum():
                            st.error("아이디는 영문·숫자 4자 이상이어야 합니다.")
                        elif len(reg_pw) < 6:
                            st.error("비밀번호는 6자 이상이어야 합니다.")
                        elif reg_pw != reg_pw2:
                            st.error("비밀번호가 일치하지 않습니다.")
                        elif not reg_name:
                            st.error("이름/업체명을 입력해주세요.")
                        else:
                            ok, status = register_user(reg_id, reg_pw, reg_name)
                            if ok:
                                if status == 'active':
                                    st.success("✅ 가입 완료! 로그인 탭에서 로그인하세요.")
                                else:
                                    st.success("✅ 가입 신청 완료! 관리자 승인 후 로그인 가능합니다.")
                                init_user_db(reg_id)
                            else:
                                st.error("이미 사용 중인 아이디입니다.")

    st.stop()


# ═══════════════════════════════════════
# 로그인 후 메인 화면
# ═══════════════════════════════════════
user = st.session_state['user']
USERNAME = user['username']
IS_ADMIN = user['is_admin']
excel_pw = get_setting(USERNAME, 'excel_password')
api_id = get_setting(USERNAME, 'api_client_id')
api_secret = get_setting(USERNAME, 'api_client_secret')

# 사이드바
with st.sidebar:
    st.title(APP_TITLE)
    st.caption(f"👤 {user['display_name']} ({USERNAME})")
    st.divider()

    menus = ["🏠 홈", "📋 주문 업로드", "📮 송장번호 등록", "🧾 영수증 등록", "💰 수익 계산", "📊 대시보드", "📦 제품 DB", "⚙️ 설정", "🤖 자동화"]
    if IS_ADMIN:
        menus.append("👑 관리자")
    tab_choice = st.radio("메뉴", menus, label_visibility="collapsed", key="main_tab")

    st.divider()
    ship = get_setting(USERNAME, 'shipping_cost')
    box = get_setting(USERNAME, 'box_cost')
    st.caption(f"택배비: {fmt(int(ship) if ship else 0)}원 | 박스비: {fmt(int(box) if box else 0)}원")

    if st.button("🚪 로그아웃", use_container_width=True):
        _sid_to_del = st.session_state.get('_sid')
        if _sid_to_del:
            delete_session(_sid_to_del)
        _clear_qparams()
        st.session_state.clear()
        st.rerun()


# ═══════════════════════════════════════
# 홈 대시보드
# ═══════════════════════════════════════
if tab_choice == "🏠 홈":
    today = datetime.today()
    w_start, w_end = get_week_range()
    m_start, m_end = get_month_range()

    kpi = get_dashboard_kpi(USERNAME)
    wk = kpi['week']
    mk = kpi['month']
    lwk = kpi['last_week']
    lmk = kpi['last_month']

    # ── KPI 카드 4개 ──
    c1, c2, c3, c4 = st.columns(4)
    w_delta = f"{((wk['profit']-lwk['profit'])/abs(lwk['profit'])*100):+.1f}%" if lwk['profit'] != 0 else None
    m_delta = f"{((mk['profit']-lmk['profit'])/abs(lmk['profit'])*100):+.1f}%" if lmk['profit'] != 0 else None
    c1.metric("📅 이번 주 수익", f"{fmt(wk['profit'])}원", delta=w_delta, help=f"{w_start} ~ {w_end}")
    c2.metric("📆 이번 달 수익", f"{fmt(mk['profit'])}원", delta=m_delta, help=f"{m_start} ~ {m_end}")
    c3.metric("📦 주간 주문건수", f"{wk['cnt']}건", delta=f"전주 {lwk['cnt']}건" if lwk['cnt'] else None, delta_color="off")
    c4.metric("📦 월간 주문건수", f"{mk['cnt']}건", delta=f"전달 {lmk['cnt']}건" if lmk['cnt'] else None, delta_color="off")

    st.divider()

    # ── 일별 수익 추이 (최근 14일) ──
    st.subheader("📈 일별 수익 추이 (최근 14일)")
    daily = get_daily_profit_trend(USERNAME, days=14)
    if daily:
        all_dates = pd.date_range(
            start=(today - timedelta(days=13)).strftime("%Y-%m-%d"),
            end=today.strftime("%Y-%m-%d"), freq='D'
        )
        ddf = pd.DataFrame(daily)
        ddf['order_date'] = pd.to_datetime(ddf['order_date'])
        ddf = ddf.set_index('order_date').reindex(all_dates, fill_value=0).reset_index()
        ddf.rename(columns={'index': 'date'}, inplace=True)
        bar_colors = ['#E74C3C' if v < 0 else '#1D9E75' for v in ddf['total_profit']]
        fig_daily = go.Figure()
        fig_daily.add_trace(go.Bar(
            x=ddf['date'], y=ddf['total_profit'],
            name='순수익', marker_color=bar_colors,
            text=ddf['total_profit'].apply(lambda x: f"{x:,.0f}" if x != 0 else ''),
            textposition='outside', textfont=dict(size=10),
        ))
        fig_daily.add_trace(go.Scatter(
            x=ddf['date'], y=ddf['cnt'], name='주문건수', yaxis='y2',
            mode='lines+markers',
            line=dict(color='#7F77DD', width=2, dash='dot'),
            marker=dict(size=6),
        ))
        fig_daily.update_layout(
            height=380, margin=dict(l=10, r=10, t=20, b=40),
            plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            yaxis=dict(title='순수익 (원)', tickformat=',', gridcolor='rgba(0,0,0,0.06)'),
            yaxis2=dict(title='주문건수', overlaying='y', side='right', showgrid=False),
            xaxis=dict(tickformat='%m/%d', dtick='D1'), bargap=0.3,
        )
        st.plotly_chart(fig_daily, use_container_width=True)
    else:
        st.info("📋 저장된 데이터가 없습니다. 주문 업로드 → 수익 계산 → 저장 순으로 진행하세요.")

    # ── 주간 베스트 / 월간 수익 추이 ──
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader(f"🏆 주간 베스트 상품 ({w_start[5:]} ~ {w_end[5:]})")
        best = get_week_best_products(USERNAME)
        if best:
            bdf = pd.DataFrame(best)
            short_names = [n[:22] + ('…' if len(n) > 22 else '') for n in bdf['product_name']]
            fig_best = go.Figure(go.Bar(
                y=short_names, x=bdf['total_profit'], orientation='h',
                marker_color=['#1D9E75' if v >= 0 else '#E74C3C' for v in bdf['total_profit']],
                text=bdf['total_profit'].apply(lambda x: f"{x:,.0f}원"),
                textposition='inside', textfont=dict(color='white', size=11),
            ))
            fig_best.update_layout(
                height=240, margin=dict(l=10, r=10, t=10, b=10),
                plot_bgcolor='rgba(0,0,0,0)',
                yaxis=dict(autorange='reversed'),
                xaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)'),
            )
            st.plotly_chart(fig_best, use_container_width=True)
            # 상세 테이블 (HTML, 상품명 전체 표시)
            rows_b = []
            for rank, row in enumerate(best, 1):
                pc = '#1D9E75' if row['total_profit'] >= 0 else '#E74C3C'
                rows_b.append(
                    f'<tr style="border-bottom:1px solid #f0f0f0">'
                    f'<td style="padding:5px 8px;text-align:center;font-weight:bold;color:#888">{rank}</td>'
                    f'<td style="padding:5px 8px;white-space:normal;word-break:break-word">{row["product_name"]}</td>'
                    f'<td style="padding:5px 8px;text-align:right">{int(row["total_qty"]):,}개</td>'
                    f'<td style="padding:5px 8px;text-align:right;font-weight:bold;color:{pc}">{int(row["total_profit"]):,}원</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
                f'<thead><tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6">'
                f'<th style="padding:6px 8px;width:28px">#</th>'
                f'<th style="padding:6px 8px;text-align:left">상품명</th>'
                f'<th style="padding:6px 8px;text-align:right">수량</th>'
                f'<th style="padding:6px 8px;text-align:right">수익</th></tr></thead>'
                f'<tbody>{"".join(rows_b)}</tbody></table>',
                unsafe_allow_html=True
            )
        else:
            st.info("이번 주 데이터가 없습니다.")

    with col_right:
        st.subheader("📊 월간 수익 추이 (최근 6개월)")
        monthly = get_monthly_stats(USERNAME)
        if monthly:
            mdf = pd.DataFrame(monthly).tail(6)
            m_colors = ['#E74C3C' if v < 0 else '#7F77DD' for v in mdf['total_profit']]
            fig_month = go.Figure()
            fig_month.add_trace(go.Bar(
                x=mdf['month'], y=mdf['total_profit'], name='월 수익',
                marker_color=m_colors,
                text=mdf['total_profit'].apply(lambda x: f"{x/10000:.1f}만"),
                textposition='outside',
            ))
            fig_month.add_trace(go.Scatter(
                x=mdf['month'], y=mdf['cnt'], name='주문건수', yaxis='y2',
                mode='lines+markers',
                line=dict(color='#FF7F0E', width=2), marker=dict(size=8),
            ))
            fig_month.update_layout(
                height=300, margin=dict(l=10, r=10, t=10, b=40),
                plot_bgcolor='rgba(0,0,0,0)',
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                yaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)'),
                yaxis2=dict(overlaying='y', side='right', showgrid=False), bargap=0.35,
            )
            st.plotly_chart(fig_month, use_container_width=True)

            # 월별 합계 테이블
            mdf_disp = mdf[['month','cnt','total_sales','total_profit']].copy()
            mdf_disp.columns = ['월','주문건','매출','순수익']
            rows_m = []
            for _, row in mdf_disp.iterrows():
                pc = '#1D9E75' if row['순수익'] >= 0 else '#E74C3C'
                rows_m.append(
                    f'<tr style="border-bottom:1px solid #f0f0f0">'
                    f'<td style="padding:5px 10px">{row["월"]}</td>'
                    f'<td style="padding:5px 10px;text-align:right">{int(row["주문건"]):,}건</td>'
                    f'<td style="padding:5px 10px;text-align:right">{int(row["매출"]):,}원</td>'
                    f'<td style="padding:5px 10px;text-align:right;font-weight:bold;color:{pc}">{int(row["순수익"]):,}원</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
                f'<thead><tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6">'
                f'<th style="padding:6px 10px;text-align:left">월</th>'
                f'<th style="padding:6px 10px;text-align:right">주문건</th>'
                f'<th style="padding:6px 10px;text-align:right">매출</th>'
                f'<th style="padding:6px 10px;text-align:right">순수익</th></tr></thead>'
                f'<tbody>{"".join(rows_m)}</tbody></table>',
                unsafe_allow_html=True
            )
        else:
            st.info("월별 데이터가 없습니다.")

    # ── 이번 달 가격 변동 이력 ──
    st.divider()
    st.subheader(f"💹 이번 달 가격 변동 이력 ({today.strftime('%Y년 %m월')})")
    ph = get_price_history_monthly(USERNAME)
    if ph:
        rows_ph = []
        for row in ph:
            old_p, new_p = int(row['old_price']), int(row['new_price'])
            is_up = new_p > old_p
            arrow = '▲' if is_up else '▼'
            color = '#1D9E75' if is_up else '#E74C3C'
            pct = f"{((new_p - old_p) / old_p * 100):+.1f}%" if old_p > 0 else '-'
            rows_ph.append(
                f'<tr style="border-bottom:1px solid #f0f0f0">'
                f'<td style="padding:6px 10px;white-space:nowrap;font-size:12px;color:#888">{row["created_at"]}</td>'
                f'<td style="padding:6px 10px;white-space:normal;word-break:break-word">{row["product_name"]}</td>'
                f'<td style="padding:6px 10px;text-align:right">{old_p:,}원</td>'
                f'<td style="padding:6px 10px;text-align:right;font-weight:bold">{new_p:,}원</td>'
                f'<td style="padding:6px 10px;text-align:center;font-weight:bold;color:{color}">{arrow} {pct}</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#666">{row["reason"]}</td>'
                f'</tr>'
            )
        ths = ['일시', '상품명', '변경 전', '변경 후', '변동폭', '사유']
        thead = ''.join(
            f'<th style="padding:7px 10px;background:#f8f9fa;text-align:{"right" if h in ("변경 전","변경 후") else "center" if h=="변동폭" else "left"};'
            f'font-weight:600;white-space:nowrap;border-bottom:2px solid #dee2e6">{h}</th>'
            for h in ths
        )
        st.markdown(
            f'<div style="overflow-x:auto;border:1px solid #dee2e6;border-radius:4px">'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'<thead><tr>{thead}</tr></thead>'
            f'<tbody>{"".join(rows_ph)}</tbody></table></div>',
            unsafe_allow_html=True
        )
        # 가격변동 막대 차트 (최근 변동 top10)
        if len(ph) >= 2:
            ph_chart = ph[:10]
            names_ph = [r['product_name'][:18] + ('…' if len(r['product_name']) > 18 else '') for r in ph_chart]
            diffs = [int(r['new_price']) - int(r['old_price']) for r in ph_chart]
            fig_ph = go.Figure(go.Bar(
                y=names_ph, x=diffs, orientation='h',
                marker_color=['#1D9E75' if d > 0 else '#E74C3C' for d in diffs],
                text=[f"{d:+,}원" for d in diffs],
                textposition='inside', textfont=dict(color='white', size=11),
            ))
            fig_ph.update_layout(
                height=max(200, len(ph_chart) * 36),
                margin=dict(l=10, r=10, t=20, b=10),
                plot_bgcolor='rgba(0,0,0,0)',
                title=dict(text='가격 변동액 (최근 10건)', font=dict(size=13)),
                yaxis=dict(autorange='reversed'),
                xaxis=dict(tickformat=',', gridcolor='rgba(0,0,0,0.06)', title='변동액 (원)'),
            )
            st.plotly_chart(fig_ph, use_container_width=True)
    else:
        st.info("이번 달 가격 변동 이력이 없습니다.")


# ═══════════════════════════════════════
# 탭 1: 주문 업로드
# ═══════════════════════════════════════
elif tab_choice == "📋 주문 업로드":
    st.header("📋 주문 파일 업로드")

    # ── API 자동 조회 ──
    if HAS_NAVER_API and api_id and api_secret:
        c_api1, c_api2 = st.columns([2, 1])
        with c_api1:
            status_options = {"배송준비 (발주확인)": "READY", "결제완료 (신규주문)": "PAYED", "전체 (신규+배송준비)": "ALL"}
            status_label = st.selectbox("주문 상태", list(status_options.keys()), index=0)
            status_type = status_options[status_label]
        with c_api2:
            st.write("")
            st.write("")
            fetch_btn = st.button("🔄 API로 주문 자동 조회", type="primary", key="api_fetch")
        hours = 48  # 항상 최근 48시간 조회
        if fetch_btn:
            all_orders = []
            types_to_query = ["READY", "PAYED"] if status_type == "ALL" else [status_type]
            
            with st.spinner("네이버 커머스 API에서 주문을 조회 중..."):
                for st_type in types_to_query:
                    orders, err = naver_api.get_new_orders(api_id, api_secret, hours_back=hours, status_type=st_type)
                    if orders:
                        all_orders.extend(orders)
                    elif err:
                        if err.startswith("DEBUG_RESP:"):
                            st.caption(f"🔍 API 응답: {err[11:]}")
                        else:
                            st.warning(f"{st_type} 조회: {err}")
            
            if not all_orders:
                st.info("조회된 주문이 없습니다.")
            else:
                # 1. 원본 데이터 생성 및 중복 제거
                raw_df = pd.DataFrame(all_orders)
                raw_df = raw_df.drop_duplicates(subset=['상품주문번호'], keep='last')
                
                # 2. 송장등록/엑셀다운로드용 원본 저장 (1회만)
                st.session_state['order_full'] = raw_df.copy()
                
                # 3. 화면 출력용 df
                df = raw_df.copy()
                for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
                df = df.sort_values('상품명').reset_index(drop=True)
                
                # 4. 상품번호 기반 매칭 + 구입가격 계산
                costs = []
                for _, r in df.iterrows():
                    p_no = str(r.get('상품번호', '')) if r.get('상품번호') else ''
                    p = match_product_to_db(USERNAME, r['상품명'], product_no=p_no)
                    costs.append(p['unit_price'] * r['수량'] if p else 0)
                    # 상품번호가 있고 DB에 매칭된 제품이 있으면 상품번호 연결
                    if p_no and p:
                        upsert_product(USERNAME, p['costco_name'], p['match_keyword'], p['unit_price'], product_no=p_no)
                df['구입가격'] = costs

                st.session_state['orders'] = df
                st.session_state['order_date'] = datetime.today().strftime("%Y-%m-%d")
                
                s_cost = int(get_setting(USERNAME, 'shipping_cost') or 1800)
                b_cost = int(get_setting(USERNAME, 'box_cost') or 300)
                save_daily_orders(USERNAME, st.session_state['order_date'], df, s_cost, b_cost)
                # 주문 이력 누적 저장
                hist_saved = save_order_history(USERNAME, raw_df, cost_df=df)
                # 제품DB 배송비·판매가 자동 업데이트 (한 번에)
                fee_upd, sale_upd = update_product_info_from_orders(USERNAME, raw_df)
                notes = []
                if hist_saved: notes.append(f"이력 {hist_saved}건 저장")
                if fee_upd:    notes.append(f"배송비 {fee_upd}건 업데이트")
                if sale_upd:   notes.append(f"판매가 {sale_upd}건 업데이트")
                if notes:
                    st.caption(f"💡 제품 DB: {' / '.join(notes)}")

                st.success(f"✅ API에서 {len(df)}건 주문 조회 완료!")
                st.rerun()
        st.divider()
    elif not HAS_NAVER_API:
        st.caption("💡 naver_api.py 파일과 bcrypt, pybase64 패키지를 설치하면 API 자동 조회를 사용할 수 있습니다.")
    elif not api_id:
        st.caption("💡 설정에서 API 키를 등록하면 자동 주문 조회를 사용할 수 있습니다.")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        uploaded = st.file_uploader("네이버 스마트스토어 발주발송관리 xlsx 파일", type=['xlsx', 'xls'], key="order_upload")
    with col2:
        order_date = st.date_input("주문 날짜", value=datetime.today())
    with col3:
        input_pw = st.text_input("엑셀 비밀번호", value=excel_pw, type="password", key="upload_pw")

    if uploaded:
        use_pw = input_pw or excel_pw
        df, err = read_excel_auto(uploaded, use_pw)
        if df is None:
            st.error(f"❌ {err}")
            if "비밀번호" in str(err):
                st.info("비밀번호를 확인하고 오른쪽 입력란에 다시 입력해주세요.")
        else:
            missing = [c for c in EXTRACT_COLS if c not in df.columns]
            if missing:
                st.error(f"필요한 컬럼이 없습니다: {missing}")
            else:
                # 송장번호 등록용 전체 데이터 저장 (상품주문번호 포함)
                if '상품주문번호' in df.columns:
                    st.session_state['order_full'] = df.copy()

                df = df[EXTRACT_COLS].copy()
                for c in ['수량','최종 상품별 총 주문금액','배송비 합계','제주/도서 추가배송비','정산예정금액']:
                    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
                df = df.sort_values('상품명').reset_index(drop=True)
                costs = []
                for _, r in df.iterrows():
                    p_no = str(r.get('상품번호', '')) if '상품번호' in df.columns and r.get('상품번호') else ''
                    p = match_product_to_db(USERNAME, r['상품명'], product_no=p_no)
                    costs.append(p['unit_price'] * r['수량'] if p else 0)
                df['구입가격'] = costs

                st.session_state['orders'] = df
                st.session_state['order_date'] = order_date.strftime("%Y-%m-%d")

                s_cost = int(get_setting(USERNAME, 'shipping_cost') or 1800)
                b_cost = int(get_setting(USERNAME, 'box_cost') or 300)
                save_daily_orders(USERNAME, st.session_state['order_date'], df, s_cost, b_cost)
                # 주문 이력 누적 저장 (order_full 우선, 없으면 df 사용)
                full_src = st.session_state.get('order_full')
                src = full_src if full_src is not None else df
                hist_saved = save_order_history(USERNAME, src, cost_df=df)
                fee_upd, sale_upd = update_product_info_from_orders(USERNAME, src)
                notes = []
                if hist_saved: notes.append(f"이력 {hist_saved}건 저장")
                if fee_upd:    notes.append(f"배송비 {fee_upd}건 업데이트")
                if sale_upd:   notes.append(f"판매가 {sale_upd}건 업데이트")
                if notes:
                    st.caption(f"💡 제품 DB: {' / '.join(notes)}")

    if 'orders' in st.session_state and st.session_state['orders'] is not None:
        df = st.session_state['orders']
        order_date_str = st.session_state.get('order_date', datetime.today().strftime("%Y-%m-%d"))

        st.subheader(f"📦 주문 목록 ({len(df)}건)")
        
        if 'order_full' in st.session_state and st.session_state['order_full'] is not None:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                st.session_state['order_full'].to_excel(writer, index=False)
            output.seek(0)
            st.download_button(
                label="📥 배송준비건 엑셀 다운로드 (비밀번호 없음)",
                data=output,
                file_name=f"발주발송관리_{order_date_str}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="secondary"
            )

        st.dataframe(df[['수취인명','상품명','옵션정보','수량','최종 상품별 총 주문금액','배송비 합계','정산예정금액']],
                   use_container_width=True, hide_index=True)

        st.subheader("🛒 코스트코 장보기 목록")
        shop_cols = ['상품번호', '상품명', '옵션정보', '수량']
        available_cols = [c for c in shop_cols if c in df.columns]
        shopping = df[available_cols].copy()
        shopping['옵션정보'] = shopping['옵션정보'].fillna('') if '옵션정보' in shopping.columns else ''

        # ── 집계: 상품번호·상품명·옵션정보가 모두 같아야 한 묶음 ──
        group_cols = [c for c in ['상품번호', '상품명', '옵션정보'] if c in shopping.columns]
        shopping = shopping.groupby(group_cols, sort=True, dropna=False).agg({'수량': 'sum'}).reset_index()
        shopping.columns = group_cols + ['주문수량']

        # ── 묶음수량 추출 (옵션/상품명 기반) ──
        shopping['묶음수량'] = shopping.apply(
            lambda r: extract_pack_qty(r.get('옵션정보', ''), r['상품명']), axis=1)

        # ── DB 단가 + 분리수량 조회 ──
        db_prices, db_splits = [], []
        for _, r in shopping.iterrows():
            p = match_product_to_db(USERNAME, r['상품명'], product_no=r.get('상품번호', ''))
            if p:
                sq = max(1, int(p.get('split_qty', 1) or 1))
                db_prices.append(p['unit_price'])
                db_splits.append(sq)
            else:
                db_prices.append(None)
                db_splits.append(1)
        shopping['팩단가'] = db_prices      # 코스트코 팩 전체 가격
        shopping['분리수량'] = db_splits    # 팩 1개 → 몇 개 분리 판매

        # ── 코스트코 구매수량 계산 ──
        # 분리판매(split_qty>1): ceil(주문수량 / 분리수량) 팩
        # 묶음판매(pack_qty>1) : 주문수량 × 묶음수량 개
        # 일반            : 주문수량 개
        def _costco_qty(row):
            sq = int(row['분리수량'])
            pq = int(row['묶음수량'])
            if sq > 1:
                return math.ceil(int(row['주문수량']) / sq)
            return int(row['주문수량']) * pq
        shopping['코스트코구매수량'] = shopping.apply(_costco_qty, axis=1)

        # ── 예상금액 계산 ──
        # 분리판매: 코스트코팩수 × 팩단가
        # 묶음/일반: 코스트코구매수량 × (팩단가/분리수량=1)
        def _expected_cost(row):
            if pd.isna(row['팩단가']) or not row['팩단가']:
                return None
            sq = int(row['분리수량'])
            return int(row['코스트코구매수량']) * int(row['팩단가'])
        shopping['예상금액'] = shopping.apply(_expected_cost, axis=1)

        # ── 표시 컬럼 구성 ──
        has_split = (shopping['분리수량'] > 1).any()
        has_multi = (shopping['묶음수량'] > 1).any()
        disp_cols = [c for c in ['상품번호', '상품명', '옵션정보'] if c in shopping.columns]
        disp_cols += ['주문수량']
        if has_split:
            disp_cols += ['분리수량']
        if has_multi:
            disp_cols += ['묶음수량']
        if has_split or has_multi:
            disp_cols += ['코스트코구매수량']
        disp_cols += ['팩단가', '예상금액']

        # ── HTML 테이블로 렌더링 ──
        num_cols = {'주문수량', '분리수량', '묶음수량', '코스트코구매수량', '팩단가', '예상금액'}
        # 분리 행: 하늘색, 묶음 행: 노란색
        def _row_bg(row):
            if int(row.get('분리수량', 1)) > 1:
                return '#d6eaf8'  # 분리판매 → 하늘색
            if int(row.get('묶음수량', 1)) > 1:
                return '#fff3cd'  # 묶음판매 → 노란색
            return 'white'

        # 코스트코구매수량 헤더: 분리 시 "팩구매수", 묶음 시 "코스트코구매수량"
        col_labels = {}
        if has_split:
            col_labels['코스트코구매수량'] = '코스트코팩구매'
        if has_split:
            col_labels['팩단가'] = '팩단가'

        th_cells = ''.join(
            f'<th style="background:#f8f9fa;padding:7px 12px;border-bottom:2px solid #dee2e6;'
            f'font-weight:600;white-space:nowrap;text-align:{"right" if c in num_cols else "left"}">'
            f'{col_labels.get(c, c)}</th>'
            for c in disp_cols
        )
        row_htmls = []
        for _, row in shopping[disp_cols].iterrows():
            bg = _row_bg(row)
            sq = int(row.get('분리수량', 1))
            tds = []
            for c in disp_cols:
                v = row[c]
                is_num = c in num_cols
                if pd.isna(v) or v == '' or v is None:
                    display = '-'
                elif is_num:
                    try:
                        iv = int(v)
                        # 팩구매수에 단위 표시
                        if c == '코스트코구매수량' and sq > 1:
                            display = f'{iv:,}팩'
                        else:
                            display = f'{iv:,}'
                    except Exception:
                        display = str(v)
                else:
                    display = str(v)
                align = 'right' if is_num else 'left'
                tds.append(
                    f'<td style="background:{bg};padding:6px 12px;border-bottom:1px solid #e9ecef;'
                    f'white-space:normal;word-break:break-word;text-align:{align}">{display}</td>'
                )
            row_htmls.append(f'<tr>{"".join(tds)}</tr>')

        st.markdown(
            f'<div style="overflow-x:auto;border:1px solid #dee2e6;border-radius:4px;margin-bottom:8px">'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'<thead><tr>{th_cells}</tr></thead>'
            f'<tbody>{"".join(row_htmls)}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True
        )

        captions = []
        if has_split:
            captions.append("🔵 파란색 행 = 소분판매 (코스트코팩구매 = ceil(주문수량 ÷ 소분수량))")
        if has_multi:
            captions.append("🟡 노란색 행 = 묶음상품 (코스트코구매수량 = 주문수량 × 묶음수량)")
        for cap in captions:
            st.caption(cap)

        c1, c2 = st.columns(2)
        c1.metric("예상 구매 총액", f"{fmt(shopping['예상금액'].dropna().sum())}원")
        c2.metric("단가 미등록 상품", f"{shopping['팩단가'].isna().sum()}종")

        # 휴대폰으로 장보기 목록 전송
        kakao_token = get_setting(USERNAME, 'kakao_access_token')
        tg_token = get_setting(USERNAME, 'telegram_token')
        tg_chat = get_setting(USERNAME, 'telegram_chat_id')

        if st.button("📱 장보기 목록 휴대폰 전송", key="send_shopping"):
            order_date_obj = datetime.strptime(order_date_str, "%Y-%m-%d")
            lines = [f"🛒 코스트코 장보기 목록 ({order_date_obj.strftime('%m/%d')})", ""]
            for _, r in shopping.iterrows():
                opt = f"({r['옵션정보']})" if r.get('옵션정보') else ""
                sq = int(r.get('분리수량', 1))
                pq = int(r.get('묶음수량', 1))
                buy_qty = int(r['코스트코구매수량'])
                order_qty = int(r['주문수량'])
                if sq > 1:
                    qty_str = f"{buy_qty}팩 (주문{order_qty}건÷{sq}소분)"
                elif pq > 1:
                    qty_str = f"{buy_qty}개 (주문{order_qty}건×{pq}구)"
                else:
                    qty_str = f"{buy_qty}개"
                name_part = " ".join(p for p in [r['상품명'][:22], opt] if p)
                lines.append(f"▪ {name_part} × {qty_str}")
            lines.append(f"\n💰 예상 총액: {fmt(shopping['예상금액'].dropna().sum())}원")
            lines.append(f"📦 총 {len(df)}건")
            msg = "\n".join(lines)
            
            sent_ok = False
            if kakao_token:
                kakao_api_key = get_setting(USERNAME, 'kakao_api_key')
                kakao_refresh = get_setting(USERNAME, 'kakao_refresh_token')
                ok, kerr = naver_api.send_kakao(kakao_token, msg, rest_api_key=kakao_api_key, refresh_token=kakao_refresh)
                if ok:
                    sent_ok = True
                    if kerr and "__TOKEN_REFRESHED__" in str(kerr):
                        parts = str(kerr).replace("__TOKEN_REFRESHED__", "").split("||")
                        set_setting(USERNAME, 'kakao_access_token', parts[0])
                        if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                else:
                    st.error(f"❌ 카카오톡 실패: {kerr}")
            
            if not sent_ok and tg_token and tg_chat:
                ok, terr = naver_api.send_telegram(tg_token, tg_chat, msg)
                if ok: sent_ok = True
                else: st.error(f"❌ 텔레그램 실패: {terr}")
                
            if sent_ok:
                st.success("✅ 휴대폰으로 전송 완료!")
            elif not kakao_token and not tg_token:
                st.warning("💡 설정에서 카카오톡 또는 텔레그램을 설정해주세요.")

    # ── 주문 이력 검색 ──────────────────────────────────────────
    st.divider()
    st.subheader("🔍 주문 이력 검색")

    with st.form("order_search_form"):
        sc1, sc2, sc3 = st.columns([2, 1, 1])
        kw_input      = sc1.text_input("수취인 / 구매자 / 주문번호", placeholder="홍길동, 주문번호 입력")
        prod_input    = sc1.text_input("상품명", placeholder="상품명 일부 입력")
        date_from_in  = sc2.date_input("시작일", value=datetime.today() - timedelta(days=30))
        date_to_in    = sc3.date_input("종료일", value=datetime.today())
        search_btn    = st.form_submit_button("🔍 검색", use_container_width=True, type="primary")

    if search_btn or st.session_state.get('order_search_triggered'):
        st.session_state['order_search_triggered'] = True
        results = search_order_history(
            USERNAME,
            keyword=kw_input,
            product_name=prod_input,
            date_from=date_from_in.strftime("%Y-%m-%d"),
            date_to=date_to_in.strftime("%Y-%m-%d"),
        )
        if results:
            rdf = pd.DataFrame(results)
            show_cols = {
                'order_date': '주문일', 'recipient': '수취인', 'buyer': '구매자',
                'product_name': '상품명', 'option_info': '옵션',
                'qty': '수량', 'unit_price': '판매단가', 'shipping_fee': '배송비',
                'order_amount': '주문금액', 'settlement': '정산예정',
                'status': '주문상태', 'tracking_no': '송장번호',
                'cost_price': '구입가', 'profit': '수익',
            }
            disp = rdf[[c for c in show_cols if c in rdf.columns]].rename(columns=show_cols)
            for col in ['판매단가', '배송비', '주문금액', '정산예정', '구입가', '수익']:
                if col in disp.columns:
                    disp[col] = disp[col].apply(lambda x: f"{int(x):,}" if pd.notna(x) and x != 0 else ("-" if x == 0 else ""))
            st.caption(f"검색 결과 {len(results)}건")
            st.dataframe(disp, use_container_width=True, hide_index=True)

            # 다운로드
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine='openpyxl') as w:
                rdf.to_excel(w, index=False, sheet_name='주문이력')
            out.seek(0)
            st.download_button(
                "📥 검색 결과 엑셀 다운로드",
                data=out, file_name=f"주문이력_{date_from_in}_{date_to_in}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("조건에 맞는 주문이 없습니다.")


# ═══════════════════════════════════════
# 탭 1.5: 송장번호 등록
# ═══════════════════════════════════════
elif tab_choice == "📮 송장번호 등록":
    st.header("📮 송장번호 일괄 등록")
    st.caption("택배사 PIDPIC 파일을 업로드하면 네이버 스마트스토어 일괄 송장 등록 파일을 생성하거나 API로 자동 발송처리합니다.")

    pidpic_file = st.file_uploader("택배사 PIDPIC 파일 업로드 (주문번호 + 운송장번호)", type=['xlsx', 'xls'], key="track_pidpic")
    courier = st.selectbox("택배사", ["롯데택배", "한진택배", "CJ대한통운", "우체국택배", "로젠택배"])

    if pidpic_file:
        pidpic_df, err2 = read_excel_auto(pidpic_file)

        if pidpic_df is None:
            st.error(f"PIDPIC 파일 읽기 실패: {err2}")
        else:
            # 컬럼명 후보 탐색 (택배사마다 컬럼명 다를 수 있음)
            col_order = next((c for c in pidpic_df.columns if '주문번호' in str(c)), None)
            col_track = next((c for c in pidpic_df.columns if '운송장' in str(c) or '송장' in str(c)), None)

            if not col_order or not col_track:
                st.error(f"PIDPIC 파일에서 주문번호/운송장번호 컬럼을 찾을 수 없습니다.")
                st.write("파일의 컬럼 목록:", list(pidpic_df.columns))
            else:
                pidpic_df['_주문번호'] = pidpic_df[col_order].apply(to_id_str)
                pidpic_df['_운송장번호'] = pidpic_df[col_track].apply(to_id_str)

                valid = pidpic_df[
                    (pidpic_df['_주문번호'].str.len() > 5) &
                    (pidpic_df['_운송장번호'].str.len() > 5) &
                    (pidpic_df['_운송장번호'] != 'nan')
                ].copy()

                if valid.empty:
                    st.warning("유효한 주문번호/운송장번호 데이터가 없습니다.")
                else:
                    result_df = pd.DataFrame({
                        '상품주문번호': valid['_주문번호'].values,
                        '배송방법': '택배,등기,소포',
                        '택배사': courier,
                        '송장번호': valid['_운송장번호'].values,
                    })

                    st.metric("처리 가능 건수", f"{len(result_df)}건")
                    st.dataframe(result_df, use_container_width=True, hide_index=True)
                    st.divider()

                    # ── 반자동: XLS 다운로드 ──────────────────────────
                    st.subheader("📥 반자동 — 파일 다운로드 후 스마트스토어에 직접 업로드")
                    output = io.BytesIO()
                    import xlwt
                    wb = xlwt.Workbook(encoding='utf-8')
                    ws = wb.add_sheet('발송처리')
                    headers = ['상품주문번호', '배송방법', '택배사', '송장번호']
                    for ci, h in enumerate(headers):
                        ws.write(0, ci, h)
                    for ri, (_, row) in enumerate(result_df.iterrows(), 1):
                        ws.write(ri, 0, str(row['상품주문번호']))
                        ws.write(ri, 1, str(row['배송방법']))
                        ws.write(ri, 2, str(row['택배사']))
                        ws.write(ri, 3, str(row['송장번호']))
                    wb.save(output)
                    output.seek(0)

                    st.download_button(
                        label=f"📥 송장번호_일괄_등록.xls 다운로드 ({len(result_df)}건)",
                        data=output,
                        file_name=f"송장번호_일괄_등록_{datetime.today().strftime('%Y%m%d')}.xls",
                        mime="application/vnd.ms-excel",
                        use_container_width=True,
                    )

                    # ── 자동: 네이버 API 직접 전송 ───────────────────
                    st.divider()
                    st.subheader("🚀 자동 — 네이버 스마트스토어 API로 즉시 발송처리")

                    if not HAS_NAVER_API:
                        st.warning("naver_api.py 파일이 없습니다. 관리자에게 문의하세요.")
                    elif not api_id or not api_secret:
                        st.warning("⚙️ 설정 탭에서 네이버 API 키를 먼저 입력해주세요.")
                    else:
                        st.caption(f"API 연동 완료 · {len(result_df)}건 발송처리 준비됨")
                        if st.button(f"🚀 {len(result_df)}건 일괄 발송처리 (API 자동)", type="primary", key="api_ship", use_container_width=True):
                            ship_data = []
                            for _, row in result_df.iterrows():
                                p_id = str(row['상품주문번호']).split('.')[0].strip()
                                t_num = str(row['송장번호']).replace('-', '').strip()
                                ship_data.append({
                                    "productOrderId": p_id,
                                    "택배사": courier,
                                    "trackingNumber": t_num,
                                })
                            with st.spinner(f"네이버 스마트스토어에 {len(ship_data)}건 발송처리 중..."):
                                result, ship_err = naver_api.ship_orders(api_id, api_secret, ship_data)
                            if ship_err:
                                st.error(f"❌ 오류: {ship_err}")
                            elif result:
                                st.success(f"✅ 완료! 성공: {result.get('success', 0)}건 / 실패: {result.get('fail', 0)}건")
                                if result.get('fail', 0) > 0:
                                    with st.expander("실패 상세 사유"):
                                        for detail in result.get('fail_details', []):
                                            st.write(detail)

# ═══════════════════════════════════════
# 탭 2: 영수증 등록
# ═══════════════════════════════════════
elif tab_choice == "🧾 영수증 등록":
    st.header("🧾 코스트코 영수증 등록")

    st.subheader("📄 영수증 PDF 업로드 (여러 파일 동시 등록 가능)")
    receipt_files = st.file_uploader(
        "코스트코 영수증 PDF (여러 파일 선택 가능)",
        type=['pdf'], key="receipt_pdf", accept_multiple_files=True
    )

    if receipt_files:
        all_parsed = []
        fail_files = []   # [(filename, error_msg)]
        for rf in receipt_files:
            items, err = parse_costco_receipt_pdf(rf)
            if items:
                for p in items:
                    p['_file'] = rf.name
                all_parsed.extend(items)
            else:
                fail_files.append((rf.name, err))

        if all_parsed:
            # 같은 상품번호/상품명이면 최신 단가로 덮어쓰기 (중복 제거)
            merged = {}
            for p in all_parsed:
                key = p.get('상품번호') or p['상품명']
                merged[key] = p
            deduped = list(merged.values())

            st.success(f"✅ {len(receipt_files) - len(fail_files)}개 파일 / {len(deduped)}종 상품 인식")
            if fail_files:
                for fname, emsg in fail_files:
                    with st.expander(f"⚠️ 인식 실패: {fname}", expanded=False):
                        st.warning(emsg)

            # 파일별 탭으로 결과 표시
            if len(receipt_files) > 1:
                file_names = sorted(set(p['_file'] for p in all_parsed))
                tabs = st.tabs([f"📄 {n}" for n in file_names] + ["📋 전체 합산"])
                for ti, fname in enumerate(file_names):
                    with tabs[ti]:
                        file_items = [p for p in all_parsed if p['_file'] == fname]
                        st.dataframe(
                            pd.DataFrame(file_items)[['상품번호', '상품명', '수량', '단가']],
                            use_container_width=True, hide_index=True
                        )
                with tabs[-1]:
                    st.dataframe(
                        pd.DataFrame(deduped)[['상품번호', '상품명', '수량', '단가']],
                        use_container_width=True, hide_index=True
                    )
            else:
                st.dataframe(
                    pd.DataFrame(deduped)[['상품번호', '상품명', '수량', '단가']],
                    use_container_width=True, hide_index=True
                )

            st.session_state['receipt_items'] = [
                {"상품명": p['상품명'], "수량": p['수량'], "단가": p['단가'], "상품번호": p.get('상품번호', '')}
                for p in deduped
            ]

            # ── 가격 변동 감지 ──────────────────────────────────────
            price_changes = detect_price_changes(USERNAME, deduped)

            if price_changes:
                st.divider()
                up_cnt = sum(1 for c in price_changes if c['diff'] > 0)
                dn_cnt = sum(1 for c in price_changes if c['diff'] < 0)
                st.warning(f"⚠️ 가격 변동 감지: 🔺인상 {up_cnt}건 / 🔻인하 {dn_cnt}건")

                # 변동 내역 테이블
                def _fee_str(f):
                    return "무료" if f == 0 else f"{int(f):,}원"

                change_rows = []
                for c in price_changes:
                    arrow = "🔺" if c['diff'] > 0 else "🔻"
                    change_rows.append({
                        "": arrow,
                        "코스트코 상품명": c['costco_name'],
                        "기존 매입가": f"{c['old_cost']:,}원",
                        "새 매입가": f"{c['new_cost']:,}원",
                        "변동": f"{'+' if c['diff']>0 else ''}{c['diff']:,}원 ({'+' if c['diff']>0 else ''}{c['diff_pct']}%)",
                        "고객 배송비": _fee_str(c['shipping_fee']),
                    })
                st.dataframe(pd.DataFrame(change_rows), use_container_width=True, hide_index=True)

                # ── 카카오/텔레그램 알림 ──
                kakao_token = get_setting(USERNAME, 'kakao_access_token')
                tg_token = get_setting(USERNAME, 'telegram_token')
                tg_chat = get_setting(USERNAME, 'telegram_chat_id')

                col_notif, col_save = st.columns([1, 1])
                if col_notif.button("📲 가격변동 알림 카톡/텔레그램 발송", key="send_price_alert", use_container_width=True):
                    alert_msg = build_price_alert_msg(price_changes)
                    sent_ok = False
                    if HAS_NAVER_API and kakao_token:
                        kakao_key = get_setting(USERNAME, 'kakao_api_key')
                        kakao_refresh = get_setting(USERNAME, 'kakao_refresh_token')
                        ok, kerr = naver_api.send_kakao(kakao_token, alert_msg, rest_api_key=kakao_key, refresh_token=kakao_refresh)
                        if ok:
                            sent_ok = True
                            if kerr and "__TOKEN_REFRESHED__" in str(kerr):
                                parts = str(kerr).replace("__TOKEN_REFRESHED__", "").split("||")
                                set_setting(USERNAME, 'kakao_access_token', parts[0])
                                if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                        else:
                            st.error(f"카카오 실패: {kerr}")
                    if not sent_ok and HAS_NAVER_API and tg_token and tg_chat:
                        ok, terr = naver_api.send_telegram(tg_token, tg_chat, alert_msg)
                        if ok:
                            sent_ok = True
                        else:
                            st.error(f"텔레그램 실패: {terr}")
                    if sent_ok:
                        # 알림 발송 이력 저장
                        save_price_changes_to_history(USERNAME, price_changes)
                        st.success("✅ 가격 변동 알림 발송 완료!")
                    elif not kakao_token and not tg_token:
                        st.warning("설정에서 카카오톡 또는 텔레그램을 먼저 설정해주세요.")

                # ── 네이버 가격 자동 적용 ──
                st.divider()
                st.subheader("🛒 네이버 판매가 검토 및 적용")
                st.caption("새 매입가를 기준으로 판매가를 조정합니다. 적용할 상품을 선택하고 새 판매가를 입력 후 적용하세요.")

                api_id = get_setting(USERNAME, 'api_client_id')
                api_secret = get_setting(USERNAME, 'api_client_secret')
                shipping_cost_set = int(get_setting(USERNAME, 'shipping_cost') or 1800)
                box_cost_set = int(get_setting(USERNAME, 'box_cost') or 300)
                margin_rate = int(get_setting(USERNAME, 'target_margin') or 10) / 100

                if not api_id:
                    st.info("💡 설정 탭에서 네이버 커머스 API 키를 등록하면 자동 가격 적용이 가능합니다.")

                apply_targets = []
                for idx, c in enumerate(price_changes):
                    sq = max(1, c.get('split_qty', 1))
                    unit_cost = c['new_cost'] // sq
                    cust_fee = int(c.get('shipping_fee', 0) or 0)
                    # 권장 판매가: 원가 + 택배비 + 박스비 + 마진, 네이버 수수료 5.5% 고려
                    suggested = int(
                        (unit_cost + shipping_cost_set + box_cost_set) * (1 + margin_rate) / 0.945 / 100
                    ) * 100

                    with st.expander(
                        f"{'🔺' if c['diff']>0 else '🔻'} {c['costco_name']}  "
                        f"{c['old_cost']:,} → {c['new_cost']:,}원  |  고객배송비 {_fee_str(cust_fee)}",
                        expanded=True
                    ):
                        col_a, col_b, col_c = st.columns([1, 2, 1])
                        do_apply = col_a.checkbox("적용", value=True, key=f"chk_{idx}")
                        new_sale_price = col_b.number_input(
                            "새 네이버 판매가 (원)",
                            value=suggested, min_value=100, step=100,
                            key=f"nsp_{idx}", label_visibility="collapsed"
                        )
                        col_c.caption(f"권장가\n**{suggested:,}원**")
                        p_no_input = st.text_input(
                            "네이버 원상품번호 (originProductNo)",
                            value=c.get('product_no', ''),
                            key=f"pno_{idx}",
                            placeholder="미입력 시 API 적용 불가"
                        )
                        if do_apply:
                            apply_targets.append({
                                **c,
                                'product_no': p_no_input,
                                'new_sale_price': new_sale_price,
                            })

                if st.button("✅ 선택 상품 네이버 판매가 적용", type="primary", key="apply_naver_price", use_container_width=True):
                    if not api_id or not api_secret:
                        st.error("네이버 API 키가 설정되지 않았습니다. 설정 탭에서 입력해주세요.")
                    elif not HAS_NAVER_API:
                        st.error("naver_api.py 모듈이 없습니다.")
                    else:
                        ok_list, fail_list = [], []
                        for t in apply_targets:
                            if not t['product_no']:
                                fail_list.append(f"{t['costco_name']}: 상품번호 미입력")
                                continue
                            ok, err = naver_api.update_product_price(
                                api_id, api_secret, t['product_no'], t['new_sale_price']
                            )
                            if ok:
                                ok_list.append(t['costco_name'])
                                # 가격 변동 이력 저장 (네이버 적용 완료 표시)
                                conn = get_user_db(USERNAME)
                                conn.execute("""INSERT INTO price_change_history
                                    (costco_name, old_cost, new_cost, diff, diff_pct,
                                     product_no, shipping_fee, naver_updated, created_at)
                                    VALUES (?,?,?,?,?,?,?,1,?)""",
                                    (t['costco_name'], t['old_cost'], t['new_cost'],
                                     t['diff'], t['diff_pct'], t['product_no'],
                                     t.get('shipping_fee', 0),
                                     datetime.now().strftime("%Y-%m-%d %H:%M")))
                                conn.commit()
                                conn.close()
                            else:
                                fail_list.append(f"{t['costco_name']}: {err}")

                        if ok_list:
                            st.success(f"✅ 네이버 가격 적용 완료: {', '.join(ok_list)}")
                            # 완료 알림 카톡 발송
                            if HAS_NAVER_API and (kakao_token or (tg_token and tg_chat)):
                                done_msg = (
                                    f"[가격 적용 완료] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                                    + "\n".join(
                                        f"✅ {t['costco_name']}: {t['new_sale_price']:,}원으로 변경"
                                        for t in apply_targets if t['costco_name'] in ok_list
                                    )
                                )
                                if kakao_token:
                                    kakao_key = get_setting(USERNAME, 'kakao_api_key')
                                    naver_api.send_kakao(kakao_token, done_msg, rest_api_key=kakao_key)
                                elif tg_token and tg_chat:
                                    naver_api.send_telegram(tg_token, tg_chat, done_msg)
                        if fail_list:
                            for f in fail_list:
                                st.error(f"❌ {f}")

            else:
                st.info("✅ 가격 변동 없음 — DB에 저장된 가격과 동일합니다.")

            st.divider()
            if st.button("💾 공유 DB 저장 (전체 판매자 매입가 업데이트)", type="primary", key="save_parsed"):
                cnt = 0
                for p in deduped:
                    upsert_shared_product(
                        costco_name=p['상품명'],
                        keyword=p['상품명'],
                        price=p['단가'],
                        product_no=p.get('상품번호', ''),
                        updated_by=USERNAME,
                    )
                    cnt += 1
                st.success(f"✅ {cnt}종 공유 DB 저장 완료! 모든 판매자에게 반영됩니다.")
        else:
            st.warning("업로드한 파일 모두 인식 실패. 아래에서 직접 입력해주세요.")
            for fname, emsg in fail_files:
                with st.expander(f"⚠️ {fname} — 실패 원인", expanded=True):
                    st.code(emsg, language=None)

    st.divider()
    st.subheader("✏️ 수동 입력")
    if 'manual_items' not in st.session_state:
        st.session_state['manual_items'] = [{"상품명": "", "단가": 0}]

    m_items = st.session_state['manual_items']
    to_delete = None
    for i, item in enumerate(m_items):
        cols = st.columns([4, 2, 1])
        m_items[i]['상품명'] = cols[0].text_input(f"mn_{i}", value=item['상품명'], label_visibility="collapsed", key=f"mn_{i}", placeholder="코스트코 상품명")
        m_items[i]['단가'] = cols[1].number_input(f"mp_{i}", value=item['단가'], min_value=0, step=100, label_visibility="collapsed", key=f"mp_{i}")
        if cols[2].button("🗑", key=f"md_{i}") and len(m_items) > 1:
            to_delete = i
    if to_delete is not None:
        m_items.pop(to_delete)
        st.rerun()

    c1, c2 = st.columns([1, 3])
    if c1.button("➕ 행 추가"):
        m_items.append({"상품명": "", "단가": 0})
        st.rerun()
    if c2.button("💾 수동 입력 저장 (공유 DB)"):
        cnt = 0
        for item in m_items:
            name = item['상품명'].strip()
            if name and item['단가'] > 0:
                upsert_shared_product(name, name, item['단가'], updated_by=USERNAME)
                cnt += 1
        if cnt: st.success(f"✅ {cnt}건 공유 DB 저장!")

    st.divider()
    st.subheader("📦 저장된 제품 가격 DB")
    products = get_all_products(USERNAME)
    if products:
        pdf = pd.DataFrame(products)[['product_no','costco_name','match_keyword','unit_price','updated_at']]
        pdf.columns = ['코스트코 상품번호', '코스트코 상품명', '매칭키', '단가', '최종 업데이트']
        st.dataframe(pdf, use_container_width=True, hide_index=True)
    else:
        st.info("등록된 제품이 없습니다.")


# ═══════════════════════════════════════
# 탭 3: 수익 계산
# ═══════════════════════════════════════
elif tab_choice == "💰 수익 계산":
    st.header("💰 수익 계산")
    shipping_cost = int(get_setting(USERNAME, 'shipping_cost') or 1800)
    box_cost = int(get_setting(USERNAME, 'box_cost') or 300)

    st.info(f"📐 수익 = (정산예정 + 고객택배비) - (구입가 + 택배비 {fmt(shipping_cost)} + 박스비 {fmt(box_cost)})")

    col_date, _ = st.columns([1, 3])
    with col_date:
        calc_date = st.date_input("계산할 주문 날짜 선택", value=datetime.today())
        calc_date_str = calc_date.strftime("%Y-%m-%d")

    # 기존 DB에서 데이터 불러오기
    saved_rows = get_daily_orders(USERNAME, calc_date_str)
    if saved_rows:
        df = pd.DataFrame(saved_rows)
        # DB 컬럼명을 UI용 컬럼명으로 매핑
        rename_map = {
            'recipient': '수취인명',
            'product_name': '상품명',
            'option_info': '옵션정보',
            'qty': '수량',
            'order_amount': '최종 상품별 총 주문금액',
            'shipping_fee': '배송비 합계',
            'settlement': '정산예정금액',
            'cost_price': '구입가격'
        }
        df = df.rename(columns=rename_map)
    else:
        df = None

    if df is not None and not df.empty:
        receipt_items = st.session_state.get('receipt_items', [])
        unique_products = df['상품명'].unique().tolist()
        receipt_matches = match_receipt_to_orders(receipt_items, unique_products) if receipt_items else {}

        costs, match_sources, matched_names = [], [], []
        for _, r in df.iterrows():
            product, qty = r['상품명'], r['수량']
            saved_cost = int(r.get('구입가격', 0) or 0)
            if product in receipt_matches:
                item = receipt_matches[product]
                costs.append(item['단가'] * qty)
                match_sources.append("영수증")
                matched_names.append(item['상품명'])
            else:
                p_no = str(r.get('product_no', '')) if 'product_no' in r.index else ''
                p = match_product_to_db(USERNAME, product, product_no=p_no)
                if p:
                    sq = max(1, int(p.get('split_qty', 1) or 1))
                    unit_cost = p['unit_price'] // sq  # 분리판매 시 1개 원가
                    costs.append(unit_cost * qty)
                    match_sources.append("DB")
                    matched_names.append(p['costco_name'])
                elif saved_cost > 0:
                    # 제품DB 미매칭이지만 이전에 저장된 구입가 사용
                    costs.append(saved_cost)
                    match_sources.append("DB")
                    matched_names.append(product)
                else:
                    costs.append(0)
                    match_sources.append("미매칭")
                    matched_names.append("")

        df['구입가격'] = costs
        df['매칭출처'] = match_sources
        df['매칭제품'] = matched_names

        if 'cost_overrides' not in st.session_state:
            st.session_state['cost_overrides'] = {}
        for idx in df.index:
            key = f"{df.loc[idx,'수취인명']}_{df.loc[idx,'상품명']}_{idx}_{calc_date_str}"
            if key in st.session_state['cost_overrides']:
                df.loc[idx, '구입가격'] = st.session_state['cost_overrides'][key]
                if st.session_state['cost_overrides'][key] > 0 and df.loc[idx, '매칭출처'] == '미매칭':
                    df.loc[idx, '매칭출처'] = '수동입력'

        df['수입'] = df.apply(
            lambda r: (r['정산예정금액'] + r['배송비 합계']) - (r['구입가격'] + shipping_cost + box_cost) if r['구입가격'] > 0 else None, axis=1)

        st.caption(f"📅 {calc_date_str}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🟢 영수증", f"{len(df[df['매칭출처']=='영수증'])}건")
        c2.metric("🔵 DB", f"{len(df[df['매칭출처']=='DB'])}건")
        c3.metric("✏️ 수동", f"{len(df[df['매칭출처']=='수동입력'])}건")
        c4.metric("🟡 미매칭", f"{len(df[df['매칭출처']=='미매칭'])}건")

        st.subheader("📊 일별 정산표")
        st.caption("🟢=영수증 | 🔵=DB | ⬜=수동 | 🟡=미매칭")

        hcols = st.columns([1.5, 3.5, 0.7, 1.5, 1.5, 1.5, 2.3, 0.5, 1.5])
        for h, c in zip(['수취인','상품명','수량','정산예정','고객택배비','구입가격✏️','매칭키워드✏️','','수입'], hcols):
            c.markdown(f"**{h}**")

        for idx, r in df.iterrows():
            key = f"{r['수취인명']}_{r['상품명']}_{idx}_{calc_date_str}"
            bg = "#fff3cd" if r['매칭출처']=='미매칭' else "#d4edda" if r['매칭출처']=='영수증' else "#d6eaf8" if r['매칭출처']=='DB' else "#ffffff"
            cols = st.columns([1.5, 3.5, 0.7, 1.5, 1.5, 1.5, 2.3, 0.5, 1.5])
            cols[0].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;font-size:12px'>{r['수취인명']}</div>", unsafe_allow_html=True)
            cols[1].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;font-size:11px'>{r['상품명'][:40]}</div>", unsafe_allow_html=True)
            cols[2].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;text-align:center'>{int(r['수량'])}</div>", unsafe_allow_html=True)
            cols[3].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;text-align:right;font-size:12px'>{fmt(r['정산예정금액'])}</div>", unsafe_allow_html=True)
            cols[4].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;text-align:right;font-size:12px'>{fmt(r['배송비 합계'])}</div>", unsafe_allow_html=True)

            current_cost = int(r['구입가격'])
            new_cost = cols[5].number_input(f"c_{idx}", value=current_cost, min_value=0, step=100, label_visibility="collapsed", key=f"c_{idx}")
            if new_cost != current_cost:
                st.session_state['cost_overrides'][key] = new_cost

            current_kw = r['매칭제품'] if r['매칭제품'] else ""
            new_kw = cols[6].text_input(f"k_{idx}", value=current_kw, label_visibility="collapsed", key=f"k_{idx}", placeholder="매칭키워드")
            if cols[7].button("💾", key=f"s_{idx}"):
                kw = new_kw.strip()
                price = new_cost if new_cost > 0 else current_cost
                unit_price = price // int(r['수량']) if int(r['수량']) > 1 else price
                if kw and unit_price > 0:
                    upsert_product(USERNAME, kw, kw, unit_price)
                    st.success(f"✅ '{kw}' → {fmt(unit_price)}원 저장!")
                    st.session_state['cost_overrides'] = {}
                    st.rerun()

            pv = r['수입']
            cols[8].markdown(f"<div style='background:{bg};padding:4px;border-radius:4px;text-align:right'>{fmt(pv) if pd.notna(pv) else '-'}</div>", unsafe_allow_html=True)

        if st.button("🔄 수정사항 반영", key="recalc"):
            st.session_state['cost_overrides'] = {}
            st.rerun()

        # 합계
        st.subheader("📋 합계")
        matched_df = df[df['구입가격'] > 0]
        total_settlement = matched_df['정산예정금액'].sum()
        total_cust_ship = matched_df['배송비 합계'].sum()
        total_cost = matched_df['구입가격'].sum()
        total_ship = len(matched_df) * shipping_cost
        total_box = len(matched_df) * box_cost
        total_profit = matched_df['수입'].sum() if len(matched_df) > 0 else 0

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**수입**")
            st.write(f"정산예정: {fmt(total_settlement)}원 + 고객택배비: {fmt(total_cust_ship)}원 = **{fmt(total_settlement + total_cust_ship)}원**")
        with c2:
            st.markdown("**지출**")
            st.write(f"구입가: {fmt(total_cost)}원 + 택배: {fmt(total_ship)}원 + 박스: {fmt(total_box)}원 = **{fmt(total_cost + total_ship + total_box)}원**")
        st.markdown(f"### 순수익: {'🟢' if total_profit >= 0 else '🔴'} {fmt(total_profit)}원")

        st.divider()
        if st.button("💾 정산 데이터 저장", type="primary"):
            save_daily_orders(USERNAME, calc_date_str, df, shipping_cost, box_cost)
            st.success(f"✅ {calc_date_str} 저장 완료!")

        # ── 적자 상품 가격 자동 조정 ──
        loss_df = df[(df['구입가격'] > 0) & (df['수입'] < 0)].copy()
        if len(loss_df) > 0:
            st.divider()
            st.subheader("🔴 적자 상품 가격 조정")

            target_margin = int(get_setting(USERNAME, 'target_margin') or 10)
            max_increase = int(get_setting(USERNAME, 'max_increase_pct') or 20)
            tg_token = get_setting(USERNAME, 'telegram_token')
            tg_chat = get_setting(USERNAME, 'telegram_chat_id')

            # 적자 상품별로 권장 가격 계산
            loss_products = loss_df.drop_duplicates(subset='상품명')[['상품명','구입가격','수량','정산예정금액','수입']].reset_index(drop=True)

            st.caption(f"목표 마진: {target_margin}% | 최대 인상폭: {max_increase}%")

            adjust_list = []
            for i, r in loss_products.iterrows():
                unit_cost = int(r['구입가격'] / r['수량']) if r['수량'] > 1 else int(r['구입가격'])
                current_price = int(r['정산예정금액'] / r['수량']) if r['수량'] > 1 else int(r['정산예정금액'])

                if HAS_NAVER_API:
                    min_price = naver_api.calc_min_price(unit_cost, shipping_cost, box_cost, target_margin / 100)
                else:
                    min_price = int((unit_cost + shipping_cost + box_cost) * (1 + target_margin / 100) / 0.945 / 100) * 100

                increase_pct = ((min_price - current_price) / current_price * 100) if current_price > 0 else 0
                over_limit = increase_pct > max_increase

                cols = st.columns([3, 1.5, 1.5, 1.5, 1.5, 1])
                cols[0].markdown(f"**{r['상품명'][:30]}**")
                cols[1].markdown(f"원가: {fmt(unit_cost)}")
                cols[2].markdown(f"현재가: {fmt(current_price)}")
                cols[3].markdown(f"권장가: **{fmt(min_price)}**")

                if over_limit:
                    cols[4].markdown(f"🔴 +{increase_pct:.0f}% (상한초과)")
                else:
                    cols[4].markdown(f"🟡 +{increase_pct:.0f}%")

                adjust_list.append({
                    '상품명': r['상품명'],
                    '원가': unit_cost,
                    '현재가': current_price,
                    '권장가': min_price,
                    '인상률': increase_pct,
                    '상한초과': over_limit,
                    '손익': int(r['수입']),
                })

            if adjust_list and HAS_NAVER_API and api_id and api_secret:
                st.divider()
                safe_list = [a for a in adjust_list if not a['상한초과'] and a['권장가'] > a['현재가']]
                over_list = [a for a in adjust_list if a['상한초과']]

                if safe_list:
                    st.markdown(f"**✅ 자동 조정 가능: {len(safe_list)}개** (인상폭 {max_increase}% 이내)")
                    if st.button(f"💰 {len(safe_list)}개 상품 가격 자동 조정", type="primary", key="auto_price"):
                        # 텔레그램 알림
                        if tg_token and tg_chat:
                            msg_lines = ["🔴 적자 상품 가격 조정 알림\n"]
                            for a in safe_list:
                                msg_lines.append(f"▪ {a['상품명'][:20]}")
                                msg_lines.append(f"  {fmt(a['현재가'])} → {fmt(a['권장가'])} (+{a['인상률']:.0f}%)")
                            msg_lines.append(f"\n⏰ 10분 내 '취소' 입력 시 취소됩니다.")
                            naver_api.send_telegram(tg_token, tg_chat, "\n".join(msg_lines))
                            st.info("📱 텔레그램 알림 전송 완료. 10분 내 '취소' 응답이 없으면 자동 적용됩니다.")

                        # 상품 목록 조회 → 매칭 → 가격 변경
                        with st.spinner("스마트스토어 상품 조회 중..."):
                            store_products, err = naver_api.get_product_list(api_id, api_secret)

                        if err:
                            st.error(f"❌ 상품 조회 실패: {err}")
                        elif store_products:
                            success_cnt, fail_cnt = 0, 0
                            conn = get_user_db(USERNAME)
                            now = datetime.now().strftime("%Y-%m-%d %H:%M")

                            for adj in safe_list:
                                # 스토어 상품 매칭 (이름 유사도)
                                matched_product = None
                                for sp in store_products:
                                    score = calc_match_score(adj['상품명'], sp['productName'])
                                    if score >= MIN_MATCH_SCORE:
                                        matched_product = sp
                                        break

                                if matched_product:
                                    ok, err = naver_api.update_product_price(
                                        api_id, api_secret,
                                        matched_product['originProductNo'],
                                        adj['권장가']
                                    )
                                    if ok:
                                        success_cnt += 1
                                        conn.execute("""INSERT INTO price_history
                                            (product_name, origin_product_no, old_price, new_price, cost_price, reason, status, created_at)
                                            VALUES (?,?,?,?,?,?,?,?)""",
                                            (adj['상품명'], str(matched_product['originProductNo']),
                                             adj['현재가'], adj['권장가'], adj['원가'],
                                             f"적자 자동조정 (+{adj['인상률']:.0f}%)", "applied", now))
                                    else:
                                        fail_cnt += 1
                                        st.warning(f"⚠️ {adj['상품명'][:20]}: {err}")
                                else:
                                    fail_cnt += 1
                                    st.warning(f"⚠️ {adj['상품명'][:20]}: 스토어 상품 매칭 실패")

                            conn.commit()
                            conn.close()

                            st.success(f"✅ 가격 조정 완료! 성공: {success_cnt}건, 실패: {fail_cnt}건")

                            # 결과 텔레그램 전송
                            if tg_token and tg_chat:
                                naver_api.send_telegram(tg_token, tg_chat,
                                    f"✅ 가격 조정 완료\n성공: {success_cnt}건, 실패: {fail_cnt}건")
                        else:
                            st.error("스토어에 판매중인 상품이 없습니다.")

                if over_list:
                    st.warning(f"⚠️ 수동 확인 필요: {len(over_list)}개 (인상폭 {max_increase}% 초과)")
                    for a in over_list:
                        st.caption(f"  {a['상품명'][:30]}: {fmt(a['현재가'])} → {fmt(a['권장가'])} (+{a['인상률']:.0f}%)")
            elif not HAS_NAVER_API:
                st.caption("💡 naver_api.py와 API 키가 설정되면 자동 가격 조정이 가능합니다.")
    else:
        st.info("📋 '주문 업로드' 탭에서 먼저 주문 파일을 업로드해주세요.")

    # ── 정산 이력 (항상 표시) ──
    st.divider()
    st.subheader("📅 정산 이력")
    saved_dates_list = get_saved_dates(USERNAME)
    if saved_dates_list:
        col_d, col_del = st.columns([2, 1])
        sel_date = col_d.selectbox("날짜 선택", saved_dates_list, key="profit_hist_date")
        if sel_date:
            hist_orders = get_daily_orders(USERNAME, sel_date)
            if hist_orders:
                hodf = pd.DataFrame(hist_orders)[['recipient','product_name','option_info','qty','settlement','shipping_fee','cost_price','profit']]
                hodf.columns = ['수취인','상품명','옵션','수량','정산예정','고객택배비','구입가','수입']
                st.dataframe(hodf, use_container_width=True, hide_index=True)
                hc1, hc2, hc3 = st.columns(3)
                hc1.metric("당일 순수익", f"{fmt(sum(o['profit'] for o in hist_orders))}원")
                hc2.metric("주문건수", f"{len(hist_orders)}건")
                hc3.metric("정산 합계", f"{fmt(sum(o['settlement'] for o in hist_orders))}원")
        if col_del.button(f"🗑 {sel_date} 삭제", key="del_hist_date", use_container_width=True):
            conn = get_user_db(USERNAME)
            conn.execute("DELETE FROM daily_orders WHERE order_date=?", (sel_date,))
            conn.commit(); conn.close()
            st.rerun()
    else:
        st.info("저장된 정산 이력이 없습니다.")


# ═══════════════════════════════════════
# 탭 4: 대시보드
# ═══════════════════════════════════════
elif tab_choice == "📊 대시보드":
    st.header("📊 대시보드")
    today = datetime.today()

    period = st.radio("기간", ["최근 7일", "최근 14일", "최근 30일"], horizontal=True, label_visibility="collapsed")
    days = {"최근 7일": 7, "최근 14일": 14, "최근 30일": 30}[period]
    start = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    stats = get_date_range_stats(USERNAME, start, end)

    if not stats:
        st.info("저장된 데이터가 없습니다. 주문 업로드 → 수익 계산 → 저장 순서로 진행하세요.")
    else:
        today_str = today.strftime("%Y-%m-%d")
        today_stat = next((s for s in stats if s['order_date'] == today_str), None)

        c1, c2, c3 = st.columns(3)
        c1.metric("오늘 주문", f"{today_stat['cnt']}건" if today_stat else "0건")
        c2.metric("오늘 매출", f"{fmt(today_stat['total_sales'])}원" if today_stat else "0원")
        c3.metric("오늘 순수익", f"{fmt(today_stat['total_profit'])}원" if today_stat else "0원")

        st.subheader("일별 수익 추이")
        chart_df = pd.DataFrame(stats)
        chart_df['order_date'] = pd.to_datetime(chart_df['order_date'])
        fig = go.Figure()
        fig.add_trace(go.Bar(x=chart_df['order_date'], y=chart_df['total_profit'],
            name='순수익', marker_color='#1D9E75',
            text=chart_df['total_profit'].apply(lambda x: f"{x:,.0f}"), textposition='outside'))
        fig.update_layout(height=350, margin=dict(l=20,r=20,t=20,b=40), yaxis_tickformat=",",
                         xaxis_dtick="D1", xaxis_tickformat="%m/%d", plot_bgcolor='rgba(0,0,0,0)')
        fig.update_yaxes(gridcolor='rgba(0,0,0,0.05)')
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("주간 요약")
        tw_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        lw_start = (today - timedelta(days=today.weekday()+7)).strftime("%Y-%m-%d")
        lw_end = (today - timedelta(days=today.weekday()+1)).strftime("%Y-%m-%d")
        tw = get_date_range_stats(USERNAME, tw_start, end)
        lw = get_date_range_stats(USERNAME, lw_start, lw_end)
        tw_profit = sum(s['total_profit'] for s in tw)
        lw_profit = sum(s['total_profit'] for s in lw)
        c1, c2 = st.columns(2)
        c1.metric("이번 주 순수익", f"{fmt(tw_profit)}원",
                  delta=f"{((tw_profit/lw_profit-1)*100):.1f}%" if lw_profit > 0 else None)
        c2.metric("이번 주 주문건수", f"{sum(s['cnt'] for s in tw)}건")

        st.subheader("월별 수익 추이")
        monthly = get_monthly_stats(USERNAME)
        if monthly:
            mdf = pd.DataFrame(monthly)
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=mdf['month'], y=mdf['total_profit'], mode='lines+markers+text',
                line=dict(color='#7F77DD', width=3), marker=dict(size=10),
                text=mdf['total_profit'].apply(lambda x: f"{x/10000:.1f}만"), textposition='top center'))
            fig2.update_layout(height=300, margin=dict(l=20,r=20,t=20,b=40), yaxis_tickformat=",", plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig2, use_container_width=True)

        st.subheader("상품별 수익 순위")
        ranking = get_product_ranking(USERNAME, today.strftime("%Y-%m"))
        if ranking:
            rdf = pd.DataFrame(ranking)
            rdf.columns = ['상품명', '판매수량', '매출', '순수익']
            rdf.index = range(1, len(rdf) + 1)
            st.dataframe(rdf.style.format({'매출': '{:,.0f}', '순수익': '{:,.0f}'}), use_container_width=True)


# ═══════════════════════════════════════
# 탭 5: 설정
# ═══════════════════════════════════════
elif tab_choice == "⚙️ 설정":
    st.header("⚙️ 설정")

    st.subheader("🔓 엑셀 비밀번호")
    st.caption("네이버 스마트스토어에서 다운받은 엑셀 파일의 비밀번호를 저장하면 자동으로 해제됩니다.")
    current_pw = get_setting(USERNAME, 'excel_password')
    new_pw = st.text_input("엑셀 비밀번호", value=current_pw, type="password", key="excel_pw_input")
    if st.button("비밀번호 저장", key="save_pw"):
        set_setting(USERNAME, 'excel_password', new_pw)
        st.success("✅ 엑셀 비밀번호 저장 완료!")

    st.divider()
    st.subheader("🔗 네이버 커머스 API")
    st.caption("커머스API센터에서 발급받은 키를 입력하면 주문 자동 조회 + 발송 자동 처리가 가능합니다.")
    api_id_val = get_setting(USERNAME, 'api_client_id')
    api_secret_val = get_setting(USERNAME, 'api_client_secret')
    c1, c2 = st.columns(2)
    new_api_id = c1.text_input("애플리케이션 ID", value=api_id_val, key="api_id_input")
    new_api_secret = c2.text_input("애플리케이션 시크릿", value=api_secret_val, type="password", key="api_secret_input")
    if st.button("API 키 저장", key="save_api"):
        set_setting(USERNAME, 'api_client_id', new_api_id)
        set_setting(USERNAME, 'api_client_secret', new_api_secret)
        st.success("✅ API 키 저장 완료!")
        if HAS_NAVER_API and new_api_id and new_api_secret:
            with st.spinner("API 연결 테스트 중..."):
                token, err = naver_api.get_token(new_api_id, new_api_secret)
            if token:
                st.success("✅ API 연결 성공!")
            else:
                st.error(f"❌ API 연결 실패: {err}")
    if not HAS_NAVER_API:
        st.warning("naver_api.py 파일이 프로그램 폴더에 없습니다. 관리자에게 문의하세요.")

    st.divider()
    st.subheader("🛍 네이버 상품 등록 기본값")
    st.caption("제품 DB에서 '🛍등록' 버튼 클릭 시 자동 입력되는 기본값입니다.")
    _nc1, _nc2 = st.columns(2)
    _def_cat = _nc1.text_input("기본 카테고리 ID",
                                value=get_setting(USERNAME, 'naver_default_category'),
                                placeholder="예: 50000803",
                                key="set_naver_cat")
    _def_as  = _nc2.text_input("A/S 전화번호",
                                value=get_setting(USERNAME, 'naver_as_tel'),
                                placeholder="010-0000-0000",
                                key="set_naver_as")
    if st.button("상품 등록 기본값 저장", key="save_naver_reg_defaults"):
        set_setting(USERNAME, 'naver_default_category', _def_cat.strip())
        set_setting(USERNAME, 'naver_as_tel', _def_as.strip())
        st.success("✅ 저장 완료!")

    st.divider()
    st.subheader("📱 카카오톡 알림")
    st.caption("장보기 목록을 카카오톡(나에게 보내기)으로 전송합니다.")
    
    kakao_api_key = get_setting(USERNAME, 'kakao_api_key')
    kakao_token = get_setting(USERNAME, 'kakao_access_token')
    kakao_refresh = get_setting(USERNAME, 'kakao_refresh_token')
    
    new_kakao_api_key = st.text_input("REST API 키", value=kakao_api_key, key="kakao_api_key_input",
                                       help="카카오 개발자 콘솔 > 플랫폼 키에서 확인")
    
    if st.button("REST API 키 저장", key="save_kakao_api_key"):
        set_setting(USERNAME, 'kakao_api_key', new_kakao_api_key)
        st.success("✅ REST API 키 저장!")
        kakao_api_key = new_kakao_api_key
    
    if kakao_api_key:
        # 인가 코드 발급 링크
        auth_url = f"https://kauth.kakao.com/oauth/authorize?client_id={kakao_api_key}&redirect_uri=http://localhost&response_type=code&scope=talk_message"
        st.markdown(f"**1단계:** [여기를 클릭하여 카카오 로그인]({auth_url}) → 동의 후 브라우저 주소창에서 `code=` 뒤의 값을 복사하세요.")
        st.caption("예: http://localhost?code=**abc123xyz** → `abc123xyz` 부분을 아래에 붙여넣기")
        
        auth_code = st.text_input("2단계: 인가 코드 붙여넣기", key="kakao_auth_code", placeholder="인가 코드를 여기에 붙여넣으세요")
        if st.button("🔑 토큰 발급받기", key="kakao_get_token"):
            if auth_code and HAS_NAVER_API:
                with st.spinner("토큰 발급 중..."):
                    access, refresh, err = naver_api.get_kakao_token_by_code(kakao_api_key, auth_code)
                if access:
                    set_setting(USERNAME, 'kakao_access_token', access)
                    set_setting(USERNAME, 'kakao_refresh_token', refresh or '')
                    st.success(f"✅ 토큰 발급 성공! (길이: {len(access)}자)")
                    kakao_token = access
                    kakao_refresh = refresh or ''
                else:
                    st.error(f"❌ {err}")
            else:
                st.warning("인가 코드를 입력해주세요.")
    
    # 현재 토큰 상태 표시
    if kakao_token:
        st.info(f"✅ 액세스 토큰 설정됨 (길이: {len(kakao_token)}자)")
    else:
        st.warning("⚠️ 아직 토큰이 없습니다. 위의 과정을 진행해주세요.")
    
    if st.button("🔔 카카오톡 테스트 전송", key="test_kakao"):
        if kakao_token and HAS_NAVER_API:
            ok, err = naver_api.send_kakao(kakao_token, "✅ 코스트코핫딜 알림 테스트 성공!", 
                                            rest_api_key=kakao_api_key, refresh_token=kakao_refresh)
            if ok:
                if err and "__TOKEN_REFRESHED__" in str(err):
                    parts = str(err).replace("__TOKEN_REFRESHED__", "").split("||")
                    set_setting(USERNAME, 'kakao_access_token', parts[0])
                    if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                    st.success("✅ 카카오톡 전송 성공! (토큰 자동 갱신됨)")
                else:
                    st.success("✅ 카카오톡 전송 성공!")
            else:
                st.error(f"❌ {err}")
        else:
            st.warning("토큰을 먼저 발급받아 주세요.")

    st.divider()
    st.subheader("🚛 택배사 설정")
    current_courier = get_setting(USERNAME, 'default_courier') or 'CJGLS'
    courier_options = {"CJ대한통운": "CJGLS", "롯데택배": "HYUNDAI"}
    sel_courier = st.selectbox("기본 택배사", list(courier_options.keys()), index=0 if current_courier == 'CJGLS' else 1)
    
    st.caption("CJ대한통운 API 접수 설정 (자동 송장 발급용)")
    cj_id = get_setting(USERNAME, 'cj_api_id')
    cj_pw = get_setting(USERNAME, 'cj_api_pw')
    cj_acc = get_setting(USERNAME, 'cj_account_no')
    col1, col2, col3 = st.columns(3)
    new_cj_id = col1.text_input("CJ ID", value=cj_id)
    new_cj_pw = col2.text_input("CJ PW", value=cj_pw, type="password")
    new_cj_acc = col3.text_input("고객번호", value=cj_acc)
    
    if st.button("택배사 설정 저장", key="save_courier"):
        set_setting(USERNAME, 'default_courier', courier_options[sel_courier])
        set_setting(USERNAME, 'cj_api_id', new_cj_id)
        set_setting(USERNAME, 'cj_api_pw', new_cj_pw)
        set_setting(USERNAME, 'cj_account_no', new_cj_acc)
        st.success(f"✅ 택배사 설정 저장 완료! (기본: {sel_courier})")

    st.divider()
    st.subheader("📱 텔레그램 알림 (백업)")
    tg_token = get_setting(USERNAME, 'telegram_token')
    tg_chat = get_setting(USERNAME, 'telegram_chat_id')
    c1, c2 = st.columns(2)
    new_tg_token = c1.text_input("봇 토큰", value=tg_token, type="password", key="tg_token_input")
    new_tg_chat = c2.text_input("Chat ID", value=tg_chat, key="tg_chat_input")
    if st.button("텔레그램 저장", key="save_tg"):
        set_setting(USERNAME, 'telegram_token', new_tg_token)
        set_setting(USERNAME, 'telegram_chat_id', new_tg_chat)
        st.success("✅ 텔레그램 설정 저장!")

    st.divider()
    st.subheader("📦 고정 비용")
    c1, c2 = st.columns(2)
    new_ship = c1.number_input("택배비 (원)", value=int(get_setting(USERNAME, 'shipping_cost') or 1800), step=100)
    new_box = c2.number_input("박스비 (원)", value=int(get_setting(USERNAME, 'box_cost') or 300), step=50)
    if st.button("비용 저장", key="save_cost"):
        set_setting(USERNAME, 'shipping_cost', new_ship)
        set_setting(USERNAME, 'box_cost', new_box)
        st.success(f"✅ 택배비 {fmt(new_ship)}원, 박스비 {fmt(new_box)}원 저장")

    st.divider()
    st.subheader("💰 가격 자동 조정")
    st.caption("적자 상품 감지 시 스마트스토어 판매가를 자동으로 조정합니다.")
    c1, c2 = st.columns(2)
    new_margin = c1.number_input("목표 마진율 (%)", value=int(get_setting(USERNAME, 'target_margin') or 10), min_value=1, max_value=50, step=1)
    new_max_inc = c2.number_input("최대 인상폭 (%)", value=int(get_setting(USERNAME, 'max_increase_pct') or 20), min_value=5, max_value=50, step=5)
    st.caption(f"예시: 원가 10,000원 + 택배비 {fmt(new_ship)}원 + 박스비 {fmt(new_box)}원 → 최소 판매가 약 {fmt(int((10000+new_ship+new_box) * (1+new_margin/100) / 0.945 / 100) * 100)}원")
    if st.button("마진 설정 저장", key="save_margin"):
        set_setting(USERNAME, 'target_margin', new_margin)
        set_setting(USERNAME, 'max_increase_pct', new_max_inc)
        st.success(f"✅ 목표 마진 {new_margin}%, 최대 인상폭 {new_max_inc}% 저장")

    # 가격 변경 이력
    conn = get_user_db(USERNAME)
    history = conn.execute("SELECT * FROM price_history ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()
    if history:
        st.divider()
        st.subheader("📋 가격 변경 이력")
        hdf = pd.DataFrame([dict(h) for h in history])[['created_at','product_name','old_price','new_price','cost_price','reason','status']]
        hdf.columns = ['일시','상품명','변경전','변경후','원가','사유','상태']
        st.dataframe(hdf, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🔑 비밀번호 변경")
    new_login_pw = st.text_input("새 로그인 비밀번호", type="password", key="new_login_pw")
    new_login_pw2 = st.text_input("비밀번호 확인", type="password", key="new_login_pw2")
    if st.button("비밀번호 변경", key="change_pw"):
        if new_login_pw and new_login_pw == new_login_pw2:
            change_password(USERNAME, new_login_pw)
            st.success("✅ 비밀번호 변경 완료!")
        elif new_login_pw != new_login_pw2:
            st.error("비밀번호가 일치하지 않습니다.")



# ═══════════════════════════════════════
# 제품 DB 탭
# ═══════════════════════════════════════
elif tab_choice == "📦 제품 DB":
    st.header("📦 제품 가격 DB 관리")
    st.caption("🔗 공유 필드(매입가·상품명)는 읽기전용 — 영수증 업로드 또는 관리자 탭에서 수정 | ✏️ 판매가·배송비는 개인별 수정 가능")

    # ── 네이버 상품 등록 폼 ─────────────────────────────────────────
    _nreg_sp_id = st.session_state.get('naver_reg_sp_id')
    if _nreg_sp_id is not None:
        _nreg_kw = st.session_state.get('naver_reg_kw', '')
        conn_auth = sqlite3.connect(AUTH_DB)
        conn_auth.row_factory = sqlite3.Row
        _sp = conn_auth.execute("SELECT * FROM shared_products WHERE id=?", (_nreg_sp_id,)).fetchone()
        conn_auth.close()

        if _sp:
            _sp = dict(_sp)
            with st.expander(f"🛍 네이버 스마트스토어 상품 등록 — {_sp['costco_name']}", expanded=True):
                if not HAS_NAVER_API:
                    st.error("naver_api.py 없음")
                elif not api_id or not api_secret:
                    st.warning("⚙️ 설정 탭에서 네이버 API 키를 먼저 입력하세요.")
                else:
                    _saved_cat = _sp.get('naver_category_id') or get_setting(USERNAME, 'naver_default_category') or ''
                    _saved_as  = get_setting(USERNAME, 'naver_as_tel') or ''

                    rc1, rc2 = st.columns(2)
                    _reg_name  = rc1.text_input("상품명", value=_sp['costco_name'][:100], key="nreg_name")
                    _reg_cat   = rc2.text_input("네이버 카테고리 ID",
                                                value=_saved_cat,
                                                placeholder="예: 50000803 (냉동식품)",
                                                key="nreg_cat",
                                                help="스마트스토어 센터 > 상품관리 > 카테고리에서 리프 카테고리 ID 확인")

                    rc3, rc4, rc5 = st.columns(3)
                    _up = next((x for x in get_all_products_merged(USERNAME) if x.get('shared_id') == _nreg_sp_id), {})
                    _def_price = int(_up.get('sale_price') or 0) or int(_sp.get('unit_price') or 0)
                    _def_fee   = int(_up.get('shipping_fee') or 0)
                    _reg_price = rc3.number_input("판매가 (원)", value=_def_price, step=100, key="nreg_price")
                    _reg_fee   = rc4.number_input("배송비 (0=무료)", value=_def_fee, step=500, key="nreg_fee")
                    _reg_stock = rc5.number_input("재고 수량", value=100, step=10, key="nreg_stock")

                    rc6, rc7 = st.columns(2)
                    _reg_as   = rc6.text_input("A/S 전화번호", value=_saved_as, placeholder="010-0000-0000", key="nreg_as")
                    _reg_orig = rc7.selectbox("원산지", ["국내산 (03)", "해외산 (04)"], key="nreg_orig")
                    _orig_code = "03" if "03" in _reg_orig else "04"

                    _image_src = _sp.get('local_image') or _sp.get('image_url') or ''
                    if _image_src:
                        st.image(_image_src, width=80, caption="등록 이미지")
                    else:
                        st.warning("이미지 없음 — 크롤링 후 재시도 권장")

                    btn_c1, btn_c2 = st.columns([1, 4])
                    if btn_c2.button("✖ 취소", key="nreg_cancel"):
                        st.session_state.pop('naver_reg_sp_id', None)
                        st.session_state.pop('naver_reg_kw', None)
                        st.rerun()

                    if btn_c1.button("🛍 네이버 등록", key="nreg_submit", type="primary"):
                        if not _reg_cat.strip():
                            st.error("카테고리 ID를 입력하세요.")
                        elif not _reg_price:
                            st.error("판매가를 입력하세요.")
                        elif not _image_src:
                            st.error("이미지가 없습니다. 먼저 크롤링을 실행하세요.")
                        else:
                            with st.spinner("이미지 업로드 중..."):
                                _cdn_url, _err = naver_api.upload_product_image(api_id, api_secret, _image_src)
                            if _err or not _cdn_url:
                                st.error(f"이미지 업로드 실패: {_err}")
                            else:
                                with st.spinner("네이버 상품 등록 중..."):
                                    _result, _err2 = naver_api.register_product(api_id, api_secret, {
                                        "name": _reg_name,
                                        "sale_price": _reg_price,
                                        "image_url": _cdn_url,
                                        "category_id": _reg_cat.strip(),
                                        "stock": _reg_stock,
                                        "shipping_fee": _reg_fee,
                                        "after_service_tel": _reg_as,
                                        "origin_code": _orig_code,
                                    })
                                if _err2 or not _result:
                                    st.error(f"상품 등록 실패: {_err2}")
                                else:
                                    _npno = _result.get("origin_product_no", "")
                                    # 사용자 products 테이블에 네이버 상품번호 저장
                                    upsert_user_private(USERNAME, _nreg_kw,
                                                        _sp['costco_name'],
                                                        naver_product_no=_npno)
                                    # 카테고리 ID를 shared_products 및 사용자 기본값으로 저장
                                    try:
                                        _ca = sqlite3.connect(AUTH_DB)
                                        _ca.execute("UPDATE shared_products SET naver_category_id=? WHERE id=?",
                                                    (_reg_cat.strip(), _nreg_sp_id))
                                        _ca.commit(); _ca.close()
                                    except Exception:
                                        pass
                                    set_setting(USERNAME, 'naver_default_category', _reg_cat.strip())
                                    set_setting(USERNAME, 'naver_as_tel', _reg_as)
                                    st.success(f"✅ 등록 완료! 네이버 상품번호: {_npno}")
                                    st.session_state.pop('naver_reg_sp_id', None)
                                    st.session_state.pop('naver_reg_kw', None)
                                    st.rerun()
        else:
            st.session_state.pop('naver_reg_sp_id', None)
            st.session_state.pop('naver_reg_kw', None)

    products = get_all_products_merged(USERNAME)
    if products:
        # ── 검색 ──
        s_col, _ = st.columns([2, 3])
        search_q = s_col.text_input("🔍 검색", placeholder="상품명 또는 상품번호", key="product_search")

        filtered_products = products
        if search_q:
            sq_low = search_q.strip().lower()
            filtered_products = [p for p in products if
                sq_low in p.get('costco_name', '').lower() or
                sq_low in p.get('match_keyword', '').lower() or
                sq_low in str(p.get('product_no', ''))]

        total_count = len(filtered_products)
        per_page = 30
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        if 'product_page' not in st.session_state:
            st.session_state['product_page'] = 1
        if st.session_state['product_page'] > total_pages:
            st.session_state['product_page'] = 1
        page = st.session_state['product_page']

        start_idx = (page - 1) * per_page
        end_idx = min(start_idx + per_page, total_count)
        page_products = filtered_products[start_idx:end_idx]

        st.caption(f"총 {total_count}개 제품 (페이지 {page}/{total_pages})  |  🔗 공유 DB  👤 개인 DB")

        # ── 테이블 헤더 ──
        # 공유(읽기전용): 상품번호 | 코스트코 상품명 | 매칭키 | 매입가 | 분리
        # 개인(수정가능): 판매가(네이버) | 고객배송비
        # 기타: 공유여부 | 업데이트 | 수정 | 삭제
        HDR = [0.9, 2.8, 2.0, 1.05, 1.05, 0.6, 1.2, 1.1, 1.0, 0.6, 0.6, 0.55]
        HDR_LABELS = ['상품번호', '코스트코 상품명', '매칭키', '매장가🔒', '온라인가🔒', '소분🔒', '판매가(네이버)✏️', '고객배송비✏️', '업데이트', '수정', '🛍등록', '삭제']
        hdr_cols = st.columns(HDR)
        for lbl, col in zip(HDR_LABELS, hdr_cols):
            col.markdown(f"<span style='font-size:15px;font-weight:600;color:#555'>{lbl}</span>",
                         unsafe_allow_html=True)
        st.markdown("<hr style='margin:4px 0 2px 0;border-color:#dee2e6'>", unsafe_allow_html=True)

        editing_kw = st.session_state.get('editing_product_kw')

        for p in page_products:
            kw        = p['match_keyword']
            is_shared = p.get('shared_id') is not None
            sq_val    = int(p.get('split_qty', 1) or 1)
            fee_val   = int(p.get('shipping_fee', 0) or 0)
            sale_val  = int(p.get('sale_price', 0) or 0)

            if editing_kw == kw:
                st.markdown(
                    "<div style='background:#eaf4fb;border:1px solid #aed6f1;border-radius:6px;"
                    "padding:10px 12px;margin:4px 0'>",
                    unsafe_allow_html=True
                )
                if is_shared:
                    # 공유 제품: 판매가·배송비만 수정 가능
                    st.caption(f"🔗 공유 제품 — 매입가·상품명은 읽기전용 (관리자 탭에서 수정)")
                    fc = st.columns([3.0, 1.5, 1.5, 1.2, 1.0])
                    fc[0].markdown(
                        f"**{p['costco_name']}**  "
                        f"<span style='color:#888;font-size:14px'>({p.get('product_no','') or '-'})</span><br>"
                        f"<span style='color:#555;font-size:14px'>매입가: {fmt(p.get('unit_price',0))}원  |  소분: {sq_val}</span>",
                        unsafe_allow_html=True
                    )
                    e_sale = fc[1].number_input("판매가(네이버)", value=sale_val, min_value=0, step=100,
                                                key=f"e_sale_{kw}", label_visibility="visible")
                    e_fee  = fc[2].number_input("고객배송비 (0=무료)", value=fee_val, min_value=0, step=100,
                                                key=f"e_fee_{kw}", label_visibility="visible")
                    if fc[3].button("✅ 저장", key=f"e_save_{kw}", use_container_width=True, type="primary"):
                        upsert_user_private(USERNAME, kw, p['costco_name'],
                                            sale_price=e_sale, shipping_fee=e_fee)
                        st.session_state.pop('editing_product_kw', None)
                        st.rerun()
                    if fc[4].button("✖ 취소", key=f"e_cancel_{kw}", use_container_width=True):
                        st.session_state.pop('editing_product_kw', None)
                        st.rerun()
                else:
                    # 레거시 개인 제품: 모든 필드 수정 가능
                    fc = st.columns([0.9, 2.8, 2.0, 1.3, 0.8, 1.2, 1.1, 1.0, 0.8])
                    pid_legacy = p.get('private_id')
                    e_pno  = fc[0].text_input("상품번호", value=p.get('product_no', ''), key=f"e_pno_{kw}",
                                              label_visibility="collapsed", placeholder="상품번호")
                    e_name = fc[1].text_input("상품명",   value=p['costco_name'],        key=f"e_name_{kw}",
                                              label_visibility="collapsed")
                    e_kw2  = fc[2].text_input("매칭키",   value=kw,                       key=f"e_kw2_{kw}",
                                              label_visibility="collapsed")
                    e_price= fc[3].number_input("매입가", value=int(p.get('unit_price', 0) or 0),
                                                step=100, key=f"e_price_{kw}", label_visibility="collapsed")
                    e_sq   = fc[4].number_input("소분", value=sq_val, min_value=1, max_value=20,
                                                key=f"e_sq_{kw}", label_visibility="collapsed")
                    e_sale = fc[5].number_input("판매가", value=sale_val, min_value=0, step=100,
                                                key=f"e_sale2_{kw}", label_visibility="collapsed")
                    e_fee  = fc[6].number_input("배송비", value=fee_val, min_value=0, step=100,
                                                key=f"e_fee2_{kw}", label_visibility="collapsed")
                    if fc[7].button("✅ 저장", key=f"e_save2_{kw}", use_container_width=True, type="primary"):
                        if pid_legacy:
                            conn_u = get_user_db(USERNAME)
                            conn_u.execute(
                                "UPDATE products SET product_no=?, costco_name=?, match_keyword=?, "
                                "unit_price=?, split_qty=?, sale_price=?, shipping_fee=?, updated_at=? WHERE id=?",
                                (e_pno, e_name, e_kw2, e_price, e_sq, e_sale, e_fee,
                                 datetime.now().strftime("%Y-%m-%d %H:%M"), pid_legacy)
                            )
                            conn_u.commit(); conn_u.close()
                        st.session_state.pop('editing_product_kw', None)
                        st.rerun()
                    if fc[8].button("✖", key=f"e_cancel2_{kw}", use_container_width=True):
                        st.session_state.pop('editing_product_kw', None)
                        st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

            else:
                # ── 일반 표시 행 ──
                row_cols = st.columns(HDR)
                pt_cur    = p.get('price_type') or '매장'
                price_fmt = f"{fmt(p.get('unit_price', 0))}원"
                if pt_cur == '온라인':
                    store_disp  = "<span style='font-size:17px;color:#ccc'>-</span>"
                    online_disp = f"<span style='font-size:17px;font-weight:600;color:#1565c0'>🌐 {price_fmt}</span>"
                else:
                    store_disp  = f"<span style='font-size:17px;font-weight:600;color:#2e7d32'>{price_fmt}</span>"
                    online_disp = "<span style='font-size:17px;color:#ccc'>-</span>"
                sq_label   = f"÷{sq_val}" if sq_val > 1 else "-"
                sq_color   = "color:#1565C0;font-weight:bold" if sq_val > 1 else "color:#888"
                fee_label  = "무료" if fee_val == 0 else f"{fee_val:,}원"
                fee_color  = "color:#2e7d32;font-weight:600" if fee_val == 0 else "color:#555"
                sale_label = f"{sale_val:,}원" if sale_val > 0 else "-"
                sale_color = "color:#1a237e;font-weight:600" if sale_val > 0 else "color:#ccc"
                updated_str = (p.get('shared_updated_at') or '')[:10]
                row_cols[0].markdown(
                    f"<span style='font-size:16px;color:#888'>{p.get('product_no','') or '-'}</span>",
                    unsafe_allow_html=True)
                _thumb = p.get('image_url', '')
                _img_html = (
                    f"<img src='{_thumb}' width='38' height='38' "
                    f"style='object-fit:cover;border-radius:4px;"
                    f"vertical-align:middle;margin-right:6px;border:1px solid #eee'>"
                    if _thumb else ""
                )
                row_cols[1].markdown(
                    f"{_img_html}<span style='font-size:17px'>{p['costco_name']}</span>",
                    unsafe_allow_html=True)
                row_cols[2].markdown(
                    f"<span style='font-size:16px;color:#555'>{kw}</span>",
                    unsafe_allow_html=True)
                row_cols[3].markdown(store_disp, unsafe_allow_html=True)
                row_cols[4].markdown(online_disp, unsafe_allow_html=True)
                row_cols[5].markdown(
                    f"<span style='font-size:17px;{sq_color}'>{sq_label}</span>",
                    unsafe_allow_html=True)
                row_cols[6].markdown(
                    f"<span style='font-size:17px;{sale_color}'>{sale_label}</span>",
                    unsafe_allow_html=True)
                row_cols[7].markdown(
                    f"<span style='font-size:17px;{fee_color}'>{fee_label}</span>",
                    unsafe_allow_html=True)
                row_cols[8].markdown(
                    f"<span style='font-size:15px;color:#888'>{updated_str}</span>",
                    unsafe_allow_html=True)
                if row_cols[9].button("✏️", key=f"edit_btn_{kw}", use_container_width=True):
                    st.session_state['editing_product_kw'] = kw
                    st.rerun()
                _n_registered = bool(p.get('naver_product_no'))
                _n_label = "✅" if _n_registered else "🛍"
                if row_cols[10].button(_n_label, key=f"nreg_btn_{kw}", use_container_width=True,
                                       help="네이버 스마트스토어 등록" if not _n_registered else f"등록됨 ({p.get('naver_product_no')})"):
                    st.session_state['naver_reg_sp_id'] = p.get('shared_id')
                    st.session_state['naver_reg_kw'] = kw
                    st.rerun()
                if row_cols[11].button("🗑", key=f"del_btn_{kw}", use_container_width=True):
                    pid_del = p.get('private_id')
                    if pid_del:
                        conn_u = get_user_db(USERNAME)
                        conn_u.execute("DELETE FROM products WHERE id=?", (pid_del,))
                        conn_u.commit(); conn_u.close()
                    st.session_state.pop('editing_product_kw', None)
                    st.rerun()

            st.markdown("<hr style='margin:-4px 0 -6px 0;border-color:#f0f0f0'>", unsafe_allow_html=True)

        # ── 페이지 번호 — 하단 중앙 ──
        if total_pages > 1:
            max_vis = 10
            s_pg = max(1, page - max_vis // 2)
            e_pg = min(total_pages, s_pg + max_vis - 1)
            if e_pg - s_pg < max_vis - 1:
                s_pg = max(1, e_pg - max_vis + 1)
            pg_range = list(range(s_pg, e_pg + 1))
            pad = max(1, (20 - len(pg_range)) // 2)
            pg_cols = st.columns([pad] + [1] * len(pg_range) + [pad])
            for i, p_num in enumerate(pg_range):
                label = f":red[**{p_num}**]" if p_num == page else str(p_num)
                if pg_cols[i + 1].button(label, key=f"p_btn_{p_num}", use_container_width=True):
                    st.session_state['product_page'] = p_num
                    st.rerun()
    else:
        st.info("등록된 제품이 없습니다. 영수증 등록 메뉴에서 추가하세요.")

    # ── 가격 변동 이력 ──────────────────────────────────────────
    st.divider()
    st.subheader("📈 코스트코 가격 변동 이력")
    ph_rows = get_price_change_history(USERNAME, limit=100)
    if ph_rows:
        ph_df = pd.DataFrame(ph_rows)
        ph_df['변동'] = ph_df.apply(
            lambda r: f"{'🔺' if r['diff'] > 0 else '🔻'} {int(r['diff']):+,}원 ({r['diff_pct']:+.1f}%)", axis=1
        )
        ph_df['고객배송비'] = ph_df['shipping_fee'].apply(lambda f: "무료" if int(f or 0) == 0 else f"{int(f):,}원")
        ph_df['알림'] = ph_df['notified'].apply(lambda x: "✅" if x else "-")
        ph_df['네이버적용'] = ph_df['naver_updated'].apply(lambda x: "✅" if x else "-")
        display_cols = {
            'created_at': '일시', 'costco_name': '상품명',
            'old_cost': '이전가', 'new_cost': '새 매입가',
            '변동': '변동', '고객배송비': '고객배송비', '알림': '알림발송', '네이버적용': '네이버적용'
        }
        ph_show = ph_df[[c for c in display_cols.keys() if c in ph_df.columns] + ['변동', '고객배송비', '알림', '네이버적용']].copy()
        ph_show = ph_show.rename(columns=display_cols)
        if '이전가' in ph_show.columns:
            ph_show['이전가'] = ph_show['이전가'].apply(lambda x: f"{int(x):,}원")
        if '새 매입가' in ph_show.columns:
            ph_show['새 매입가'] = ph_show['새 매입가'].apply(lambda x: f"{int(x):,}원")
        st.dataframe(ph_show, use_container_width=True, hide_index=True)

        if st.button("🗑 이력 전체 삭제", key="del_price_history"):
            conn = get_user_db(USERNAME)
            conn.execute("DELETE FROM price_change_history")
            conn.commit(); conn.close()
            st.success("이력 삭제 완료")
            st.rerun()
    else:
        st.info("아직 가격 변동 이력이 없습니다. 영수증을 업로드하면 자동으로 감지됩니다.")


# ═══════════════════════════════════════
# 탭 6: 관리자
# ═══════════════════════════════════════
elif tab_choice == "👑 관리자" and IS_ADMIN:
    st.header("👑 관리자 페이지")

    # ── 회원가입 승인 대기 ──────────────────────────────────────────
    pending_users = get_pending_users()
    if pending_users:
        st.subheader(f"⏳ 승인 대기 ({len(pending_users)}명)")
        st.warning("아래 신청자를 승인하거나 거절하세요.")
        for u in pending_users:
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.markdown(f"**{u['display_name']}** (`{u['username']}`) — {u['created_at']}")
            if c2.button("✅ 승인", key=f"approve_{u['username']}", use_container_width=True, type="primary"):
                approve_user(u['username'])
                st.success(f"✅ '{u['display_name']}' 승인 완료!")
                st.rerun()
            if c3.button("❌ 거절", key=f"reject_{u['username']}", use_container_width=True):
                reject_user(u['username'])
                st.warning(f"'{u['display_name']}' 거절됨")
                st.rerun()
        st.divider()

    # ── 회원가입 설정 ───────────────────────────────────────────────
    st.subheader("⚙️ 회원가입 설정")
    cur_allow   = get_global_setting('allow_signup', '1')
    cur_approve = get_global_setting('require_approval', '1')
    c1, c2 = st.columns(2)
    new_allow   = c1.toggle("회원가입 허용", value=(cur_allow == '1'), key="toggle_allow_signup")
    new_approve = c2.toggle("신규 가입 시 관리자 승인 필요", value=(cur_approve == '1'), key="toggle_require_approval")
    if st.button("설정 저장", key="save_signup_settings"):
        set_global_setting('allow_signup', '1' if new_allow else '0')
        set_global_setting('require_approval', '1' if new_approve else '0')
        st.success("✅ 설정 저장 완료!")
        st.rerun()

    st.divider()
    st.subheader("👥 사용자 목록")
    users = get_all_users()
    status_labels = {'active': '✅ 활성', 'pending': '⏳ 대기', 'rejected': '❌ 거절'}
    for u in users:
        role = "👑 관리자" if u['is_admin'] else "👤 일반"
        status_txt = status_labels.get(u.get('status', 'active'), '✅')
        with st.expander(f"{role} {u['display_name']} ({u['username']}) — {status_txt} | {u['created_at']}"):
            if not u['is_admin']:
                c1, c2, c3 = st.columns(3)
                if u.get('status') == 'pending':
                    if c1.button(f"✅ 승인", key=f"approve2_{u['username']}", use_container_width=True):
                        approve_user(u['username'])
                        st.rerun()
                if c2.button(f"🗑 삭제", key=f"del_{u['username']}", use_container_width=True):
                    delete_user(u['username'])
                    st.success(f"✅ '{u['username']}' 삭제 완료!")
                    st.rerun()
                reset_pw = c3.text_input("새 비밀번호", key=f"reset_{u['username']}", type="password")
                if c3.button("비밀번호 초기화", key=f"resetbtn_{u['username']}", use_container_width=True):
                    if reset_pw:
                        change_password(u['username'], reset_pw)
                        st.success(f"✅ '{u['username']}' 비밀번호 변경 완료!")

    st.divider()
    st.subheader("➕ 사용자 직접 추가")
    c1, c2, c3 = st.columns(3)
    new_id = c1.text_input("아이디", key="new_user_id")
    new_name = c2.text_input("이름", key="new_user_name")
    new_pw_admin = c3.text_input("초기 비밀번호", type="password", key="new_user_pw")

    if st.button("사용자 추가", type="primary", key="add_user"):
        if new_id and new_pw_admin:
            if add_user(new_id, new_pw_admin, new_name):
                init_user_db(new_id)
                st.success(f"✅ '{new_id}' 계정 생성 완료!")
                st.rerun()
            else:
                st.error("이미 존재하는 아이디입니다.")
        else:
            st.warning("아이디와 비밀번호를 입력해주세요.")

    # ── 공유 제품 DB 관리 ──────────────────────────────────────────
    st.divider()
    st.subheader("🏪 공유 제품 DB 관리 (모든 판매자 공용)")
    st.caption("영수증 업로드로 자동 등록되거나 아래에서 직접 추가·수정·삭제할 수 있습니다.")

    shared_all = get_shared_products()
    if shared_all:
        sp_search = st.text_input("🔍 공유 제품 검색", placeholder="상품명 또는 상품번호", key="admin_sp_search")
        disp_shared = shared_all
        if sp_search:
            sl = sp_search.strip().lower()
            disp_shared = [s for s in shared_all if
                sl in s.get('costco_name', '').lower() or
                sl in s.get('match_keyword', '').lower() or
                sl in str(s.get('product_no', ''))]

        # 페이지네이션
        SP_PER_PAGE = 30
        sp_total = len(disp_shared)
        sp_total_pages = max(1, math.ceil(sp_total / SP_PER_PAGE))
        if 'admin_sp_page' not in st.session_state:
            st.session_state['admin_sp_page'] = 1
        if st.session_state['admin_sp_page'] > sp_total_pages:
            st.session_state['admin_sp_page'] = 1
        sp_page = st.session_state['admin_sp_page']
        sp_start = (sp_page - 1) * SP_PER_PAGE
        page_shared = disp_shared[sp_start: sp_start + SP_PER_PAGE]

        st.caption(f"총 {sp_total}개 (전체 {len(shared_all)}개)  |  페이지 {sp_page}/{sp_total_pages}")

        # 헤더 — 매장가 / 온라인가 분리, 구분 컬럼 제거
        SP_HDR = [0.8, 2.6, 1.7, 1.05, 1.05, 0.6, 1.2, 1.0, 0.7, 0.6]
        SP_LABELS = ['상품번호', '코스트코 상품명', '매칭키', '매장가', '온라인가', '소분', '최종수정자', '업데이트', '수정', '삭제']
        sp_hdr_cols = st.columns(SP_HDR)
        for lbl, col in zip(SP_LABELS, sp_hdr_cols):
            col.markdown(f"<span style='font-size:16px;font-weight:600;color:#555'>{lbl}</span>",
                         unsafe_allow_html=True)
        st.markdown("<hr style='margin:4px 0 2px 0;border-color:#dee2e6'>", unsafe_allow_html=True)

        editing_sp_id = st.session_state.get('admin_editing_sp_id')

        for sp in page_shared:
            spid  = sp['id']
            sq_v  = int(sp.get('split_qty', 1) or 1)
            pt_cur = sp.get('price_type') or '매장'
            price_fmt = f"{fmt(sp['unit_price'])}원"

            # 매장가 / 온라인가 분리 표시
            if pt_cur == '온라인':
                store_disp  = "<span style='font-size:17px;color:#ccc'>-</span>"
                online_disp = f"<span style='font-size:17px;font-weight:600;color:#1565c0'>🌐 {price_fmt}</span>"
            else:
                store_disp  = f"<span style='font-size:17px;font-weight:600;color:#2e7d32'>{price_fmt}</span>"
                online_disp = "<span style='font-size:17px;color:#ccc'>-</span>"

            if editing_sp_id == spid:
                st.markdown(
                    "<div style='background:#fff8e1;border:1px solid #ffe082;border-radius:6px;"
                    "padding:10px 12px;margin:4px 0'>",
                    unsafe_allow_html=True
                )
                fc = st.columns([0.8, 2.6, 1.7, 1.2, 0.6, 0.8, 1.0, 0.8])
                sp_e_pno   = fc[0].text_input("상품번호", value=sp.get('product_no', ''),
                                              key=f"sp_pno_{spid}", label_visibility="collapsed")
                sp_e_name  = fc[1].text_input("상품명",   value=sp['costco_name'],
                                              key=f"sp_name_{spid}", label_visibility="collapsed")
                sp_e_kw    = fc[2].text_input("매칭키",   value=sp['match_keyword'],
                                              key=f"sp_kw_{spid}", label_visibility="collapsed")
                sp_e_price = fc[3].number_input("가격", value=int(sp['unit_price']),
                                                step=100, key=f"sp_price_{spid}", label_visibility="collapsed")
                sp_e_sq    = fc[4].number_input("소분", value=sq_v, min_value=1, max_value=20,
                                                key=f"sp_sq_{spid}", label_visibility="collapsed")
                sp_e_pt    = fc[5].selectbox("구분", ['매장', '온라인'],
                                             index=0 if pt_cur == '매장' else 1,
                                             key=f"sp_pt_{spid}", label_visibility="collapsed")
                if fc[6].button("✅ 저장", key=f"sp_save_{spid}", use_container_width=True, type="primary"):
                    upsert_shared_product(
                        costco_name=sp_e_name, keyword=sp_e_kw,
                        price=sp_e_price, product_no=sp_e_pno,
                        split_qty=sp_e_sq, updated_by=USERNAME, price_type=sp_e_pt
                    )
                    st.session_state.pop('admin_editing_sp_id', None)
                    st.rerun()
                if fc[7].button("✖", key=f"sp_cancel_{spid}", use_container_width=True):
                    st.session_state.pop('admin_editing_sp_id', None)
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            else:
                row = st.columns(SP_HDR)
                row[0].markdown(f"<span style='font-size:16px;color:#888'>{sp.get('product_no','') or '-'}</span>",
                                unsafe_allow_html=True)
                row[1].markdown(f"<span style='font-size:17px'>{sp['costco_name']}</span>",
                                unsafe_allow_html=True)
                row[2].markdown(f"<span style='font-size:16px;color:#555'>{sp['match_keyword']}</span>",
                                unsafe_allow_html=True)
                row[3].markdown(store_disp,  unsafe_allow_html=True)
                row[4].markdown(online_disp, unsafe_allow_html=True)
                row[5].markdown(f"<span style='font-size:17px;color:#888'>{sq_v if sq_v > 1 else '-'}</span>",
                                unsafe_allow_html=True)
                row[6].markdown(f"<span style='font-size:16px;color:#888'>{sp.get('updated_by','')}</span>",
                                unsafe_allow_html=True)
                row[7].markdown(f"<span style='font-size:15px;color:#aaa'>{(sp.get('updated_at','') or '')[:10]}</span>",
                                unsafe_allow_html=True)
                if row[8].button("✏️", key=f"sp_edit_{spid}", use_container_width=True):
                    st.session_state['admin_editing_sp_id'] = spid
                    st.rerun()
                if row[9].button("🗑", key=f"sp_del_{spid}", use_container_width=True):
                    delete_shared_product(spid)
                    st.session_state.pop('admin_editing_sp_id', None)
                    st.rerun()
            st.markdown("<hr style='margin:-4px 0 -6px 0;border-color:#f0f0f0'>", unsafe_allow_html=True)

        # 페이지 번호 — 하단 중앙
        if sp_total_pages > 1:
            max_vis = 10
            s_pg = max(1, sp_page - max_vis // 2)
            e_pg = min(sp_total_pages, s_pg + max_vis - 1)
            if e_pg - s_pg < max_vis - 1:
                s_pg = max(1, e_pg - max_vis + 1)
            pg_range = list(range(s_pg, e_pg + 1))
            pad = max(1, (20 - len(pg_range)) // 2)
            pg_cols = st.columns([pad] + [1] * len(pg_range) + [pad])
            for i, p_num in enumerate(pg_range):
                label = f":red[**{p_num}**]" if p_num == sp_page else str(p_num)
                if pg_cols[i + 1].button(label, key=f"sp_pg_{p_num}", use_container_width=True):
                    st.session_state['admin_sp_page'] = p_num
                    st.rerun()
    else:
        st.info("공유 제품이 없습니다. 아래에서 추가하거나 영수증 등록 탭에서 업로드하세요.")

    st.divider()
    st.subheader("➕ 공유 제품 직접 추가")
    with st.form("admin_add_shared_product"):
        ac1, ac2, ac3, ac4, ac5, ac6 = st.columns([1.2, 2.8, 1.8, 1.3, 0.7, 0.8])
        a_pno   = ac1.text_input("코스트코 상품번호", placeholder="1234567")
        a_name  = ac2.text_input("코스트코 상품명")
        a_kw    = ac3.text_input("매칭키 (비워두면 상품명 사용)")
        a_price = ac4.number_input("가격 (원)", min_value=0, step=100)
        a_sq    = ac5.number_input("소분", min_value=1, max_value=20, value=1)
        a_pt    = ac6.selectbox("구분", ['매장', '온라인'])
        if st.form_submit_button("➕ 추가", type="primary"):
            a_name = a_name.strip()
            a_kw   = a_kw.strip() or a_name
            if a_name and a_price > 0:
                upsert_shared_product(a_name, a_kw, a_price, a_pno, a_sq, USERNAME, price_type=a_pt)
                st.success(f"✅ '{a_name}' 공유 DB에 추가 완료! ({a_pt}가)")
                st.rerun()
            else:
                st.warning("상품명과 가격을 입력해주세요.")

    # ── 공유 제품 내보내기 / 가져오기 ────────────────────────────────
    st.divider()
    st.subheader("📤 공유 제품 DB 내보내기 / 가져오기")
    st.caption("다른 컴퓨터에 설치된 프로그램과 제품 DB를 동기화할 때 사용합니다.")

    # 개인 DB → 공유 DB 이전
    my_prods = get_all_products(USERNAME)
    shared_cnt = len(get_shared_products())
    if my_prods and shared_cnt == 0:
        st.warning(f"⚠️ 공유 DB가 비어있습니다. 내 개인 DB에 제품 {len(my_prods)}개가 있습니다.")
    if my_prods:
        if st.button(f"⬆️ 내 개인 DB({len(my_prods)}개) → 공유 DB로 이전", key="migrate_to_shared", use_container_width=True, type="primary"):
            migrated, skipped = 0, 0
            for p in my_prods:
                kw = (p.get('match_keyword') or '').strip()
                name = (p.get('costco_name') or '').strip()
                if not kw or not name:
                    skipped += 1
                    continue
                upsert_shared_product(
                    costco_name=name,
                    keyword=kw,
                    price=int(p.get('unit_price', 0) or 0),
                    product_no=p.get('product_no', '') or '',
                    split_qty=int(p.get('split_qty', 1) or 1),
                    updated_by=USERNAME,
                )
                migrated += 1
            st.success(f"✅ {migrated}개 공유 DB로 이전 완료! (건너뜀: {skipped}개)")
            st.rerun()

    st.divider()
    exp_col, imp_col = st.columns(2)

    with exp_col:
        st.markdown("**📤 내보내기**")
        if st.button("JSON 파일로 내보내기", key="export_shared", use_container_width=True):
            all_sp = get_shared_products()
            if all_sp:
                import json as _json
                export_data = []
                for sp in all_sp:
                    export_data.append({
                        "product_no":   sp.get("product_no", ""),
                        "costco_name":  sp["costco_name"],
                        "match_keyword": sp["match_keyword"],
                        "unit_price":   int(sp["unit_price"]),
                        "split_qty":    int(sp.get("split_qty", 1) or 1),
                        "updated_by":   sp.get("updated_by", ""),
                        "updated_at":   sp.get("updated_at", ""),
                    })
                json_bytes = _json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")
                st.download_button(
                    label=f"⬇️ 다운로드 ({len(export_data)}개 제품)",
                    data=json_bytes,
                    file_name=f"shared_products_{datetime.now().strftime('%Y%m%d')}.json",
                    mime="application/json",
                    key="download_shared_json",
                    use_container_width=True,
                )
            else:
                st.warning("내보낼 제품이 없습니다.")

    with imp_col:
        st.markdown("**📥 가져오기**")
        up_json = st.file_uploader("JSON 파일 선택", type=["json"], key="import_shared_json")
        overwrite = st.checkbox("기존 동일 키 제품 덮어쓰기", value=True, key="import_overwrite")
        if up_json and st.button("가져오기 실행", key="do_import_shared", use_container_width=True, type="primary"):
            import json as _json
            try:
                items = _json.loads(up_json.read().decode("utf-8"))
                ok_cnt = 0
                skip_cnt = 0
                conn_imp = sqlite3.connect(AUTH_DB)
                for it in items:
                    kw = it.get("match_keyword", "").strip()
                    name = it.get("costco_name", "").strip()
                    if not kw or not name:
                        skip_cnt += 1
                        continue
                    exists = conn_imp.execute(
                        "SELECT id FROM shared_products WHERE match_keyword=?", (kw,)
                    ).fetchone()
                    if exists and not overwrite:
                        skip_cnt += 1
                        continue
                    conn_imp.execute("""
                        INSERT INTO shared_products
                            (product_no, costco_name, match_keyword, unit_price, split_qty, updated_by, updated_at)
                        VALUES (?,?,?,?,?,?,?)
                        ON CONFLICT(match_keyword) DO UPDATE SET
                            costco_name=excluded.costco_name,
                            unit_price=excluded.unit_price,
                            split_qty=excluded.split_qty,
                            product_no=excluded.product_no,
                            updated_by=excluded.updated_by,
                            updated_at=excluded.updated_at
                    """, (
                        it.get("product_no", ""),
                        name, kw,
                        int(it.get("unit_price", 0)),
                        int(it.get("split_qty", 1) or 1),
                        it.get("updated_by", USERNAME),
                        it.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M")),
                    ))
                    ok_cnt += 1
                conn_imp.commit()
                conn_imp.close()
                st.success(f"✅ {ok_cnt}개 가져오기 완료! (건너뜀: {skip_cnt}개)")
                st.rerun()
            except Exception as e:
                st.error(f"❌ 가져오기 실패: {e}")


# ═══════════════════════════════════════
# 탭 7: 자동화
# ═══════════════════════════════════════
elif tab_choice == "🤖 자동화":
    st.header("🤖 자동화 설정")
    st.caption("Windows 작업 스케줄러를 통해 매일 지정된 시간에 자동 실행됩니다.")

    SCRIPT_PATH = os.path.join(BASE_DIR, "auto_task.py")
    PYTHON_PATH = sys.executable

    def _schtasks_run(args_list):
        try:
            r = subprocess.run(
                ["schtasks"] + args_list,
                capture_output=True, text=True, encoding="cp949", errors="replace"
            )
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as e:
            return False, str(e)

    def _register_task(task_name, task_type, time_str, user):
        cmd = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task {task_type} --user {user}'
        ok, out = _schtasks_run([
            "/create", "/tn", task_name,
            "/tr", cmd,
            "/sc", "daily", "/st", time_str,
            "/f"
        ])
        return ok, out

    def _delete_task(task_name):
        ok, out = _schtasks_run(["/delete", "/tn", task_name, "/f"])
        return ok, out

    def _query_task(task_name):
        ok, out = _schtasks_run(["/query", "/tn", task_name, "/fo", "LIST"])
        return ok, out

    TASK1_NAME = f"CostcoHotdeal_Shopping_{USERNAME}"
    TASK2_NAME = f"CostcoHotdeal_Shipping_{USERNAME}"
    TASK3_NAME = "CostcoHotdeal_Crawl"

    # ── 현재 스케줄 상태 ──
    with st.expander("📌 현재 등록된 작업 스케줄러 상태", expanded=True):
        c1, c2, c3 = st.columns(3)
        t1_ok, t1_out = _query_task(TASK1_NAME)
        t2_ok, t2_out = _query_task(TASK2_NAME)
        t3_ok, t3_out = _query_task(TASK3_NAME)
        with c1:
            if t1_ok:
                st.success("✅ Task 1 (장보기) 등록됨")
                st.code(t1_out[:400], language=None)
            else:
                st.warning("⚠️ Task 1 미등록")
        with c2:
            if t2_ok:
                st.success("✅ Task 2 (발송처리) 등록됨")
                st.code(t2_out[:400], language=None)
            else:
                st.warning("⚠️ Task 2 미등록")
        with c3:
            if t3_ok:
                st.success("✅ Task 3 (크롤링) 등록됨")
                st.code(t3_out[:400], language=None)
            else:
                st.warning("⚠️ Task 3 미등록")

    st.divider()

    # ── Task 1: 장보기 목록 발송 ──
    st.subheader("📋 Task 1 — 장보기 목록 카카오 발송")
    st.caption("매일 지정 시간에 배송준비 주문을 조회하고 장보기 목록을 카카오톡/텔레그램으로 전송합니다.")

    task1_en = get_setting(USERNAME, 'auto_shopping_enabled') == '1'
    task1_time_str = get_setting(USERNAME, 'auto_shopping_time') or '09:00'
    t1h, t1m = [int(x) for x in task1_time_str.split(':')]

    c1, c2 = st.columns([1, 2])
    new_t1_en = c1.checkbox("활성화", value=task1_en, key="t1_en")
    new_t1_time = c2.time_input("실행 시간", value=dtime(t1h, t1m), key="t1_time")

    col_s1, col_d1, col_run1 = st.columns(3)
    if col_s1.button("💾 Task 1 저장 & 등록", key="save_t1", type="primary", use_container_width=True):
        t1_str = new_t1_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_shopping_enabled', '1' if new_t1_en else '0')
        set_setting(USERNAME, 'auto_shopping_time', t1_str)
        if new_t1_en:
            ok, out = _register_task(TASK1_NAME, "shopping", t1_str, USERNAME)
            if ok:
                st.success(f"✅ Task 1 등록 완료 — 매일 {t1_str} 자동 실행")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
        else:
            _delete_task(TASK1_NAME)
            st.info("Task 1 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if col_d1.button("🗑 Task 1 삭제", key="del_t1", use_container_width=True):
        ok, out = _delete_task(TASK1_NAME)
        set_setting(USERNAME, 'auto_shopping_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if col_run1.button("▶ 지금 테스트 실행", key="run_t1", use_container_width=True):
        with st.spinner("Task 1 실행 중..."):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "shopping", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120
            )
        output = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            st.success("✅ 실행 완료")
        else:
            st.error("❌ 실행 중 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── Task 2: 자동 발송처리 ──
    st.subheader("🚀 Task 2 — CJ 접수 + 네이버 일괄 발송처리")
    st.caption("매일 지정 시간에 배송준비 주문을 CJ 택배에 접수하고 네이버 스마트스토어에 자동 발송처리합니다.")

    cj_id_check = get_setting(USERNAME, 'cj_api_id')
    if not cj_id_check:
        st.warning("⚠️ CJ API 미설정 — 설정 탭 > 택배사 설정에서 CJ ID/PW/고객번호를 먼저 입력하세요.")

    task2_en = get_setting(USERNAME, 'auto_shipping_enabled') == '1'
    task2_time_str = get_setting(USERNAME, 'auto_shipping_time') or '14:00'
    t2h, t2m = [int(x) for x in task2_time_str.split(':')]

    c1, c2 = st.columns([1, 2])
    new_t2_en = c1.checkbox("활성화", value=task2_en, key="t2_en")
    new_t2_time = c2.time_input("실행 시간", value=dtime(t2h, t2m), key="t2_time")

    col_s2, col_d2, col_run2 = st.columns(3)
    if col_s2.button("💾 Task 2 저장 & 등록", key="save_t2", type="primary", use_container_width=True):
        t2_str = new_t2_time.strftime("%H:%M")
        set_setting(USERNAME, 'auto_shipping_enabled', '1' if new_t2_en else '0')
        set_setting(USERNAME, 'auto_shipping_time', t2_str)
        if new_t2_en:
            ok, out = _register_task(TASK2_NAME, "shipping", t2_str, USERNAME)
            if ok:
                st.success(f"✅ Task 2 등록 완료 — 매일 {t2_str} 자동 실행")
            else:
                st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
        else:
            _delete_task(TASK2_NAME)
            st.info("Task 2 비활성화 — 스케줄 삭제됨")
        st.rerun()

    if col_d2.button("🗑 Task 2 삭제", key="del_t2", use_container_width=True):
        ok, out = _delete_task(TASK2_NAME)
        set_setting(USERNAME, 'auto_shipping_enabled', '0')
        st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
        st.rerun()

    if col_run2.button("▶ 지금 테스트 실행", key="run_t2", use_container_width=True):
        with st.spinner("Task 2 실행 중 (CJ 접수 + 발송처리)..."):
            r = subprocess.run(
                [PYTHON_PATH, SCRIPT_PATH, "--task", "shipping", "--user", USERNAME],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=180
            )
        output = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            st.success("✅ 실행 완료")
        else:
            st.error("❌ 실행 중 오류 발생")
        st.code(output, language=None)

    st.divider()

    # ── Task 3: 정기 크롤링 (admin 전용) ──
    if IS_ADMIN:
        st.subheader("🕐 Task 3 — 코스트코 정기 크롤링")
        st.caption("매일 지정 시간에 코스트코 상품을 자동 크롤링하여 공유 제품 DB를 최신 상태로 유지합니다.")

        _CRAWL_PRESETS = {
            "🔄 정기갱신": ["신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품"],
            "🔥 핫딜시즌": ["스페셜할인", "커클랜드", "신상품"],
            "🆕 새상품탐색": ["신상품", "스페셜할인"],
            "🏗️ 전체카테고리": ["식품", "신선식품", "냉동식품", "과자/간식", "커피/음료",
                                "가공식품", "생활용품", "세제/청소", "화장지", "가전/디지털",
                                "주방가전", "뷰티/화장품", "건강/영양제", "의류/패션",
                                "완구", "반려동물", "자동차용품"],
        }

        task3_en = get_setting(USERNAME, 'auto_crawl_enabled') == '1'
        task3_time_str = get_setting(USERNAME, 'auto_crawl_time') or '06:00'
        t3h, t3m = [int(x) for x in task3_time_str.split(':')]
        _saved_cats_json = get_setting(USERNAME, 'auto_crawl_categories') or '[]'
        try:
            _saved_cats = json.loads(_saved_cats_json)
        except Exception:
            _saved_cats = []
        _saved_max = int(get_setting(USERNAME, 'auto_crawl_max') or 200)

        cr1, cr2 = st.columns([1, 2])
        new_t3_en   = cr1.checkbox("활성화", value=task3_en, key="t3_en")
        new_t3_time = cr2.time_input("실행 시간", value=dtime(t3h, t3m), key="t3_time")

        st.markdown("**크롤링 카테고리 선택**")
        _preset_cols = st.columns(4)
        for _pi, (_plabel, _pcats) in enumerate(_CRAWL_PRESETS.items()):
            if _preset_cols[_pi].button(_plabel, key=f"t3_preset_{_pi}", use_container_width=True):
                _saved_cats = list(set(_saved_cats) | set(_pcats))

        from costco_crawler import CATEGORIES as _ALL_CATS
        _cat_names = [c for c in _ALL_CATS if c not in ("전체",)]
        _sel_cats = st.multiselect("크롤링 대상 카테고리",
                                   options=_cat_names,
                                   default=[c for c in _saved_cats if c in _cat_names],
                                   key="t3_cats")
        _new_max = st.number_input("카테고리당 최대 수집 수", value=_saved_max,
                                   min_value=50, max_value=500, step=50, key="t3_max")

        col_s3, col_d3, col_run3 = st.columns(3)
        if col_s3.button("💾 Task 3 저장 & 등록", key="save_t3", type="primary", use_container_width=True):
            t3_str = new_t3_time.strftime("%H:%M")
            set_setting(USERNAME, 'auto_crawl_enabled', '1' if new_t3_en else '0')
            set_setting(USERNAME, 'auto_crawl_time', t3_str)
            set_setting(USERNAME, 'auto_crawl_categories', json.dumps(_sel_cats, ensure_ascii=False))
            set_setting(USERNAME, 'auto_crawl_max', str(int(_new_max)))
            if new_t3_en:
                _cmd3 = f'"{PYTHON_PATH}" "{SCRIPT_PATH}" --task crawl --user {USERNAME}'
                ok, out = _schtasks_run(["/create", "/tn", TASK3_NAME, "/tr", _cmd3,
                                         "/sc", "daily", "/st", t3_str, "/f"])
                if ok:
                    st.success(f"✅ Task 3 등록 완료 — 매일 {t3_str} 자동 크롤링")
                else:
                    st.error(f"❌ 등록 실패 (관리자 권한으로 실행 필요)\n{out}")
            else:
                _schtasks_run(["/delete", "/tn", TASK3_NAME, "/f"])
                st.info("Task 3 비활성화 — 스케줄 삭제됨")
            st.rerun()

        if col_d3.button("🗑 Task 3 삭제", key="del_t3", use_container_width=True):
            ok, out = _schtasks_run(["/delete", "/tn", TASK3_NAME, "/f"])
            set_setting(USERNAME, 'auto_crawl_enabled', '0')
            st.success("삭제됨") if ok else st.error(f"삭제 실패: {out}")
            st.rerun()

        if col_run3.button("▶ 지금 테스트 실행", key="run_t3", use_container_width=True):
            if not _sel_cats:
                st.warning("카테고리를 선택하세요.")
            else:
                set_setting(USERNAME, 'auto_crawl_categories',
                            json.dumps(_sel_cats, ensure_ascii=False))
                with st.spinner(f"크롤링 실행 중 ({len(_sel_cats)}개 카테고리)... 수 분 소요"):
                    r = subprocess.run(
                        [PYTHON_PATH, SCRIPT_PATH, "--task", "crawl", "--user", USERNAME],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=600
                    )
                output = (r.stdout + r.stderr).strip()
                if r.returncode == 0:
                    st.success("✅ 크롤링 완료")
                else:
                    st.error("❌ 크롤링 오류")
                st.code(output, language=None)

        st.divider()

    # ── 실행 로그 ──
    st.subheader("📄 자동화 실행 로그")
    LOG_PATH = os.path.join(DATA_DIR, "auto_task.log")
    col_log1, col_log2 = st.columns([3, 1])
    log_lines = 50
    with col_log1:
        log_lines = st.slider("최근 줄 수", min_value=20, max_value=200, value=50, step=10, key="log_lines")
    with col_log2:
        st.write("")
        st.write("")
        if st.button("🗑 로그 초기화", key="clear_log"):
            open(LOG_PATH, "w", encoding="utf-8").close()
            st.rerun()

    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        recent = "".join(all_lines[-log_lines:]) if all_lines else "(로그 없음)"
        st.code(recent, language=None)
    else:
        st.info("아직 실행 로그가 없습니다.")

    st.divider()

    # ── 서버 관리 (네트워크 서버 모드) ──
    st.subheader("🖥️ 서버 관리")
    st.caption("이 PC를 Streamlit 네트워크 서버로 운영할 때 사용하는 설정입니다.")

    # 현재 서버 IP 목록 조회
    def _get_local_ips():
        try:
            r = subprocess.run(
                ["ipconfig"],
                capture_output=True, text=True, encoding="cp949", errors="replace"
            )
            ips = re.findall(r"IPv4.*?:\s*([\d.]+)", r.stdout)
            return [ip for ip in ips if not ip.startswith("127.")]
        except Exception:
            return []

    local_ips = _get_local_ips()

    with st.expander("📡 현재 서버 접속 주소", expanded=True):
        if local_ips:
            for ip in local_ips:
                st.markdown(f"**내부 네트워크:** `http://{ip}:8501`")
        else:
            st.info("IP 주소를 가져올 수 없습니다.")
        st.markdown("**이 컴퓨터:** `http://localhost:8501`")
        st.caption("외부(인터넷) 접속은 공유기 포트포워딩 + DDNS 설정이 필요합니다.")

    with st.expander("⚙️ 부팅 자동시작 설정 방법", expanded=False):
        st.markdown("""
**1단계: 서버 부팅 자동시작 등록**
```
setup_server_boot.bat  →  관리자 권한으로 실행
```
- Windows 작업 스케줄러에 로그인 시 자동 서버 시작 등록
- 방화벽 포트 8501 자동 개방

**2단계: 공유기 포트포워딩**
- 공유기 관리 페이지 접속 (보통 192.168.0.1 또는 192.168.1.1)
- 포트포워딩 메뉴 → 외부포트 **8501** → 내부 IP:{port} **8501** 추가

**3단계: DDNS 설정 (고정 도메인)**
- https://www.duckdns.org 접속 → 무료 도메인 등록
- `yourname.duckdns.org` 형태로 외부에서 접속 가능
- 30분마다 IP 업데이트 자동화 스크립트 실행

**4단계 이후 접속 주소**
```
http://yourname.duckdns.org:8501
```
""")

    c_start, c_stop = st.columns(2)
    if c_start.button("▶ 서버 시작 (start_server.bat)", key="btn_start_server", use_container_width=True):
        server_bat = os.path.join(BASE_DIR, "start_server.bat")
        if os.path.exists(server_bat):
            subprocess.Popen(["cmd", "/c", "start", "", server_bat], cwd=BASE_DIR)
            st.success("서버 시작 명령을 보냈습니다. 새 창이 열립니다.")
        else:
            st.error("start_server.bat 파일을 찾을 수 없습니다.")

    if c_stop.button("⏹ 서버 중지 (stop_server.bat)", key="btn_stop_server", use_container_width=True):
        stop_bat = os.path.join(BASE_DIR, "stop_server.bat")
        if os.path.exists(stop_bat):
            subprocess.Popen(["cmd", "/c", "start", "", stop_bat], cwd=BASE_DIR)
            st.warning("서버 중지 명령을 보냈습니다.")
        else:
            st.error("stop_server.bat 파일을 찾을 수 없습니다.")

    st.divider()

    # ── 코스트코 크롤링 ───────────────────────────────────────────
    st.divider()
    st.subheader("🔍 코스트코 쇼핑몰 크롤링")
    st.caption("수집 결과는 공유 제품 DB에 가격구분='온라인'으로 저장됩니다.")

    try:
        import costco_crawler as _cc
        _crawler_ok = True
    except ImportError:
        _crawler_ok = False

    if not _crawler_ok:
        st.error("costco_crawler.py 파일을 찾을 수 없습니다.")
    else:
        # ── 코스트코 계정 설정 ────────────────────────────────
        with st.expander("🔑 코스트코 계정 설정", expanded=not _cc.is_profile_exists()):
            st.caption("크롤링 시 로그인에 사용됩니다. 앱 서버(이 PC)에만 저장됩니다.")
            saved_email = get_global_setting('costco_email', '')
            saved_pw    = get_global_setting('costco_password', '')

            cx1, cx2 = st.columns(2)
            c_email = cx1.text_input(
                "코스트코 이메일",
                value=saved_email,
                placeholder="example@email.com",
                key="costco_email_input",
            )
            c_pw = cx2.text_input(
                "비밀번호",
                value=saved_pw,
                type="password",
                key="costco_pw_input",
            )
            cs1, cs2 = st.columns(2)
            if cs1.button("💾 계정 저장", key="save_costco_cred", use_container_width=True):
                set_global_setting('costco_email',    c_email.strip())
                set_global_setting('costco_password', c_pw.strip())
                st.success("✅ 계정 저장 완료!")
                st.rerun()

            profile_exists = _cc.is_profile_exists()
            if profile_exists:
                cs2.success("✅ 브라우저 프로필 저장됨")
            else:
                cs2.warning("⚠️ 첫 로그인 설정 필요")

            st.divider()
            st.markdown("**첫 로그인 설정** — OTP 포함 최초 1회만 필요")
            st.caption(
                "버튼 클릭 시 브라우저가 열립니다. "
                "코스트코에 로그인하고 OTP 인증을 완료하면 자동으로 저장됩니다."
            )
            if st.button(
                "🌐 브라우저 열어서 코스트코 첫 로그인",
                key="btn_setup_profile",
                use_container_width=True,
                type="primary" if not profile_exists else "secondary",
            ):
                # playwright 설치 여부 먼저 확인
                try:
                    import playwright as _pw_check
                    _pw_installed = True
                except ImportError:
                    _pw_installed = False

                if not _pw_installed:
                    st.error(
                        "playwright가 설치되지 않았습니다.\n"
                        "터미널에서 실행:\n"
                        "pip install playwright\n"
                        "python -m playwright install chromium"
                    )
                else:
                    _setup_email = get_global_setting('costco_email', '')
                    _setup_pw    = get_global_setting('costco_password', '')
                    _script = os.path.join(BASE_DIR, "costco_crawler.py")
                    try:
                        # Windows: CREATE_NEW_CONSOLE — 새 콘솔 창에서 실행
                        subprocess.Popen(
                            [sys.executable, _script, "--setup-auto",
                             _setup_email, _setup_pw],
                            cwd=BASE_DIR,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                        )
                        st.success(
                            "✅ 새 창이 열립니다!\n\n"
                            "1. 열린 브라우저에서 코스트코 이메일 / 비밀번호 입력\n"
                            "2. OTP 인증 완료\n"
                            "3. 로그인 완료 후 콘솔 창이 자동으로 닫힙니다\n"
                            "4. 이 페이지를 새로고침(F5)하면 상태가 업데이트됩니다."
                        )
                    except Exception as _e:
                        st.error(f"실행 오류: {_e}")

        # ── 크롤링 실행 ───────────────────────────────────────
        profile_ok = _cc.is_profile_exists()
        _c_email   = get_global_setting('costco_email', '')
        _c_pw      = get_global_setting('costco_password', '')

        if not profile_ok:
            st.warning("위 '코스트코 계정 설정'에서 첫 로그인을 먼저 완료해주세요.")
        else:
            crawl_tab1, crawl_tab2 = st.tabs(["카테고리 크롤링", "키워드 검색"])

            with crawl_tab1:
                # ── 빠른 선택 프리셋 ──
                PRESETS = {
                    "🏗️ 최초구축": ["식품", "신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품",
                                     "생활용품", "세제/청소", "화장지", "가전/디지털", "주방가전",
                                     "뷰티/화장품", "건강/영양제", "의류/패션", "완구", "반려동물", "자동차용품"],
                    "🔄 정기갱신": ["신선식품", "냉동식품", "과자/간식", "커피/음료", "가공식품"],
                    "🔥 핫딜시즌": ["스페셜할인", "커클랜드", "신상품"],
                    "🆕 새상품탐색": ["신상품", "스페셜할인"],
                }
                st.markdown("**빠른 선택**")
                p_cols = st.columns(4)
                for pi, (label, cats) in enumerate(PRESETS.items()):
                    if p_cols[pi].button(label, key=f"preset_{pi}", use_container_width=True):
                        for c in cats:
                            st.session_state[f"cat_{c}"] = True

                st.markdown("**수집할 카테고리 선택**")
                cat_names = list(_cc.CATEGORIES.keys())
                cat_cols = st.columns(3)
                sel_cats = []
                for i, cat in enumerate(cat_names):
                    if cat_cols[i % 3].checkbox(cat, key=f"cat_{cat}"):
                        sel_cats.append(cat)

                max_cat = st.number_input(
                    "카테고리당 최대 수집 수", min_value=10, max_value=1000,
                    value=300, step=10, key="crawl_max_cat"
                )
                if st.button(
                    f"🚀 카테고리 크롤링 시작 ({len(sel_cats)}개 선택)",
                    type="primary", key="btn_crawl_cat",
                    disabled=len(sel_cats) == 0,
                    use_container_width=True,
                ):
                    targets = [{"type": "category", "name": c} for c in sel_cats]
                    progress_box = st.empty()
                    log_lines = []

                    def _cb_cat(msg):
                        log_lines.append(msg)
                        progress_box.code("\n".join(log_lines[-20:]))

                    _crawl_ok = False
                    with st.spinner("크롤링 중... (수 분 소요될 수 있습니다)"):
                        try:
                            result = _cc.run_crawl(
                                targets=targets,
                                email=_c_email,
                                password=_c_pw,
                                max_products=int(max_cat),
                                progress_cb=_cb_cat,
                                updated_by=USERNAME,
                            )
                            if result["errors"]:
                                st.warning("오류:\n" + "\n".join(result["errors"]))
                            st.session_state['last_crawl_result'] = result
                            _crawl_ok = True
                        except RuntimeError as e:
                            st.error(f"❌ {e}")
                    if _crawl_ok:
                        r = st.session_state['last_crawl_result']
                        st.success(
                            f"✅ 크롤링 완료!\n\n"
                            f"수집 **{r['total_crawled']}**개  →  "
                            f"신규 **{r['new']}**개 / 업데이트 **{r['updated']}**개"
                        )
                        st.balloons()
                        if st.button("📦 결과 보기 (제품 DB)", type="primary",
                                     key="go_db_cat", use_container_width=True):
                            st.session_state['main_tab'] = "📦 제품 DB"
                            st.rerun()

            with crawl_tab2:
                kw_input = st.text_input(
                    "검색 키워드 (쉼표로 여러 개 입력 가능)",
                    placeholder="예: 그릭요거트, 올리브오일, 커클랜드",
                    key="crawl_kw_input",
                )
                max_kw = st.number_input(
                    "키워드당 최대 수집 수", min_value=10, max_value=500,
                    value=100, step=10, key="crawl_max_kw"
                )
                if st.button(
                    "🔍 키워드 크롤링 시작",
                    type="primary", key="btn_crawl_kw",
                    disabled=not kw_input.strip(),
                    use_container_width=True,
                ):
                    keywords = [k.strip() for k in kw_input.split(",") if k.strip()]
                    targets = [{"type": "keyword", "keyword": k} for k in keywords]
                    progress_box2 = st.empty()
                    log_lines2 = []

                    def _cb_kw(msg):
                        log_lines2.append(msg)
                        progress_box2.code("\n".join(log_lines2[-20:]))

                    _crawl_ok2 = False
                    with st.spinner("크롤링 중..."):
                        try:
                            result2 = _cc.run_crawl(
                                targets=targets,
                                email=_c_email,
                                password=_c_pw,
                                max_products=int(max_kw),
                                progress_cb=_cb_kw,
                                updated_by=USERNAME,
                            )
                            if result2["errors"]:
                                st.warning("오류:\n" + "\n".join(result2["errors"]))
                            st.session_state['last_crawl_result'] = result2
                            _crawl_ok2 = True
                        except RuntimeError as e:
                            st.error(f"❌ {e}")
                    if _crawl_ok2:
                        r2 = st.session_state['last_crawl_result']
                        st.success(
                            f"✅ 크롤링 완료!\n\n"
                            f"수집 **{r2['total_crawled']}**개  →  "
                            f"신규 **{r2['new']}**개 / 업데이트 **{r2['updated']}**개"
                        )
                        st.balloons()
                        if st.button("📦 결과 보기 (제품 DB)", type="primary",
                                     key="go_db_kw", use_container_width=True):
                            st.session_state['main_tab'] = "📦 제품 DB"
                            st.rerun()

        # 온라인 수집 제품 현황
        online_prods = [p for p in get_shared_products() if p.get('price_type') == '온라인']
        if online_prods:
            st.divider()
            st.markdown(f"**🌐 온라인 수집 제품: {len(online_prods)}개**")
            preview_df = pd.DataFrame([{
                "상품번호": p.get("product_no", ""),
                "상품명":  p.get("costco_name", ""),
                "가격(원)": f"{int(p.get('unit_price', 0)):,}",
                "업데이트": (p.get("updated_at") or "")[:10],
            } for p in online_prods[:50]])
            st.dataframe(preview_df, use_container_width=True, height=300)
            if len(online_prods) > 50:
                st.caption(f"상위 50개만 표시 (전체 {len(online_prods)}개)")
