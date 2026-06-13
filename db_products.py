"""
제품 DB 레이어 — 공유 제품(auth.db) + 개인 제품(user.db)
"""
import sqlite3
import os
from datetime import datetime

from db_core import AUTH_DB, DATA_DIR, get_user_db


# ── 공유 제품 (auth.db/shared_products) ──────────────────

def get_shared_products():
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM shared_products ORDER BY costco_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _upsert_shared_internal(costco_name, keyword, store_price=None, online_price=None,
                            product_no='', split_qty=1, updated_by='', image_url='',
                            receipt_date=''):
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
        cur_store  = existing['store_price'] or 0
        cur_online = existing['online_price'] or 0
        cur_st_at  = existing['store_updated_at'] or ''
        cur_on_at  = existing['online_updated_at'] or ''
        _skip_store = (
            store_price is not None
            and receipt_date
            and cur_st_at
            and receipt_date[:10] < cur_st_at[:10]
        )
        new_store  = cur_store if _skip_store else (int(store_price) if store_price is not None else cur_store)
        new_online = int(online_price) if online_price is not None else cur_online
        st_at = cur_st_at if _skip_store else (receipt_date or now[:10] if store_price is not None else cur_st_at)
        on_at = now if online_price is not None else cur_on_at
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
    _upsert_shared_internal(costco_name, keyword,
                            store_price=price, online_price=None,
                            product_no=product_no, split_qty=split_qty,
                            updated_by=updated_by, image_url=image_url,
                            receipt_date=receipt_date)


def upsert_shared_online_price(costco_name, keyword, price, product_no='', split_qty=1,
                                updated_by='', image_url=''):
    _upsert_shared_internal(costco_name, keyword,
                            store_price=None, online_price=price,
                            product_no=product_no, split_qty=split_qty,
                            updated_by=updated_by, image_url=image_url)


def upsert_shared_product(costco_name, keyword, price, product_no='', split_qty=1,
                          updated_by='', price_type='매장', image_url=''):
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


def get_product_detail(shared_id):
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT extra_images, detail_html FROM shared_products WHERE id=?", (shared_id,)
    ).fetchone()
    conn.close()
    if row:
        return row['extra_images'] or '', row['detail_html'] or ''
    return '', ''


# ── 개인 DB 초기화 ──────────────────────────────────────

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
    # profit_settlements + settlement_overrides 테이블
    try:
        from db_profit_calc import _ensure_tables as _pf_ensure
        _pf_ensure(conn)
    except Exception:
        pass
    conn.commit()
    conn.close()


# ── 설정 ────────────────────────────────────────────────

def get_all_settings(username):
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


# ── 개인 제품 ──────────────────────────────────────────

def _ensure_products_columns(conn):
    for col_sql in [
        "ALTER TABLE products ADD COLUMN product_no TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN split_qty INTEGER DEFAULT 1",
        "ALTER TABLE products ADD COLUMN shipping_fee INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN sale_price INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN status TEXT DEFAULT 'SALE'",
        "ALTER TABLE products ADD COLUMN from_naver INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN naver_origin_pno TEXT DEFAULT ''",
        # 채널상품번호 — SmartStore 관리자 화면에 보이는 상품번호 (originProductNo와 다름)
        "ALTER TABLE products ADD COLUMN naver_channel_pno TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN category TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN linked_shared_id INTEGER DEFAULT NULL",
        # 코스트코 번호 분리: product_no를 비우는 대신 원본은 여기에 보존 (표시·매장 식별용)
        "ALTER TABLE products ADD COLUMN costco_no_display TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(col_sql)
        except Exception:
            pass
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_products_product_no ON products(product_no)",
        "CREATE INDEX IF NOT EXISTS idx_products_naver_origin ON products(naver_origin_pno)",
        "CREATE INDEX IF NOT EXISTS idx_products_naver_channel ON products(naver_channel_pno)",
        "CREATE INDEX IF NOT EXISTS idx_products_from_naver ON products(from_naver)",
        "CREATE INDEX IF NOT EXISTS idx_products_status ON products(status)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass
    conn.commit()


def get_all_products(username):
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    rows = conn.execute("SELECT * FROM products ORDER BY costco_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def link_naver_to_shared(username: str, user_product_id: int, shared_id: int):
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
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    conn.execute("UPDATE products SET linked_shared_id=NULL WHERE id=?", (user_product_id,))
    conn.commit()
    conn.close()


def set_naver_origin_pno(username: str, user_product_id: int, origin_pno: str):
    """가격수정 성공 시 변환된 originProductNo를 영구 저장 (다음부터 변환 생략)."""
    if not user_product_id or not origin_pno:
        return False
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    conn.execute("UPDATE products SET naver_origin_pno=? WHERE id=?",
                 (str(origin_pno), user_product_id))
    conn.commit()
    conn.close()
    return True


def bulk_update_category(username: str, id_category_map: dict):
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
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    existing = None
    if naver_origin_pno:
        existing = conn.execute(
            "SELECT id, match_keyword, sale_price, shipping_fee, product_no, status, from_naver, "
            "naver_origin_pno, naver_channel_pno, split_qty, category "
            "FROM products WHERE naver_origin_pno=? AND naver_origin_pno != ''",
            (naver_origin_pno,)
        ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT id, match_keyword, sale_price, shipping_fee, product_no, status, from_naver, "
            "naver_origin_pno, naver_channel_pno, split_qty, category "
            "FROM products WHERE match_keyword=?",
            (match_keyword,)
        ).fetchone()

    if existing:
        sale = sale_price   if sale_price   is not None else (existing['sale_price'] or 0)
        fee  = shipping_fee if shipping_fee is not None else (existing['shipping_fee'] or 0)
        # ⚠️ 의미 정정: naver_product_no 파라미터는 채널상품번호 (SmartStore 화면 번호)
        # product_no 컬럼(코스트코 번호) 은 절대 덮어쓰지 않음 — 별도 보존
        ch   = naver_product_no if naver_product_no is not None else (existing['naver_channel_pno'] or '')
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
            "sale_price=?, shipping_fee=?, split_qty=?, status=?, from_naver=?, "
            "naver_origin_pno=?, naver_channel_pno=?, category=?, updated_at=? WHERE id=?",
            (kw_to_use, costco_name, costco_name, sale, fee, sq, st, fn, op, ch, cat, now, existing['id'])
        )
    else:
        sale = sale_price or 0
        fee  = shipping_fee or 0
        ch   = naver_product_no or ''   # channelProductNo
        st   = status or 'SALE'
        fn   = int(from_naver) if from_naver is not None else 0
        op   = naver_origin_pno or ''
        sq   = max(1, int(split_qty)) if split_qty is not None else 1
        cat  = category or ''
        conn.execute("""INSERT INTO products
                        (product_no, store_product_name, costco_name, match_keyword,
                         unit_price, split_qty, sale_price, shipping_fee, status, from_naver,
                         naver_origin_pno, naver_channel_pno, category, updated_at)
                        VALUES ('', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (costco_name, costco_name, match_keyword, sq, sale, fee, st, fn, op, ch, cat, now))
    conn.commit()
    conn.close()


def get_all_products_merged(username):
    shared = get_shared_products()
    user_prods = get_all_products(username)
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
        up = user_by_pno.get(sp_pno) if sp_pno else None
        if up is None:
            up = user_by_kw.get(kw, {})
        if not up:
            up = user_by_linked.get(str(sp['id']), {})
        if up and up.get('id'):
            matched_user_ids.add(up['id'])
        from_naver = int(up.get('from_naver') or 0) if up else 0
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
            'naver_product_no': (up.get('naver_channel_pno') or up.get('naver_origin_pno') or up.get('product_no', '')) if up else '',
            'naver_channel_pno': up.get('naver_channel_pno', '') if up else '',
            'naver_origin_pno': up.get('naver_origin_pno', '') if up else '',
            'sale_price': int(up.get('sale_price', 0) or 0),
            'shipping_fee': int(up.get('shipping_fee', 0) or 0),
            'status': up.get('status') or 'SALE',
            'from_naver': from_naver,
            'private_id': up.get('id'),
            'linked_shared_id': up.get('linked_shared_id') if up else None,
        })
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
            'naver_product_no': up.get('naver_channel_pno') or up.get('naver_origin_pno') or up.get('product_no', ''),
            'naver_channel_pno': up.get('naver_channel_pno', ''),
            'naver_origin_pno': up.get('naver_origin_pno', ''),
            'sale_price': int(up.get('sale_price', 0) or 0),
            'shipping_fee': int(up.get('shipping_fee', 0) or 0),
            'status': up.get('status') or 'SALE',
            'from_naver': from_naver,
            'private_id': up.get('id'),
            'linked_shared_id': up.get('linked_shared_id'),
        })
    return merged


def upsert_product(username, costco_name, keyword, price, product_no='', split_qty=1,
                   shipping_fee=None, naver_origin_pno='', auto_split_costco_no=False,
                   manual=False):
    """제품 가격/정보 upsert.
    조회 우선순위: naver_origin_pno (네이버 원상품번호) > product_no (코스트코) > match_keyword
    → 같은 코스트코 상품번호로 여러 네이버 상품이 있어도 각각 별도 가격 저장 가능.

    auto_split_costco_no=True 면 가격 수정 후, 같은 product_no를 가진 다른 행이 존재할 때
    이 행만 product_no를 비우고 원본을 costco_no_display로 옮겨 매칭에서 분리한다.
    (수익계산 화면 등에서 한 행만 가격 수정할 때 다른 행과 섞이지 않도록.)
    """
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    split_qty = max(1, int(split_qty or 1))
    existing = None
    # 1순위: naver_origin_pno (네이버 상품별로 고유)
    if naver_origin_pno:
        existing = conn.execute(
            "SELECT id, shipping_fee, unit_price, costco_name, sale_price "
            "FROM products WHERE naver_origin_pno=? AND naver_origin_pno != ''",
            (naver_origin_pno,)
        ).fetchone()
    # 2순위: 코스트코 product_no (네이버 매핑 없는 경우)
    if not existing and product_no:
        existing = conn.execute(
            "SELECT id, shipping_fee, unit_price, costco_name, sale_price "
            "FROM products WHERE product_no=? AND (naver_origin_pno='' OR naver_origin_pno IS NULL)",
            (product_no,)
        ).fetchone()
    # 3순위: match_keyword
    if not existing:
        existing = conn.execute(
            "SELECT id, shipping_fee, unit_price, costco_name, sale_price "
            "FROM products WHERE match_keyword=?", (keyword,)
        ).fetchone()
    if existing:
        fee = shipping_fee if shipping_fee is not None else (existing['shipping_fee'] or 0)
        existing_price = int(existing['unit_price'] or 0)
        existing_sale  = int(existing['sale_price'] or 0)
        new_price = int(price or 0)
        new_name  = costco_name
        # 박스단가 보호: 자동(크롤링/영수증) 갱신만 적용. manual=True(사용자 직접 수정)면
        # 기존값이 비정상으로 낮아도 사용자가 입력한 값을 그대로 반영(보호 우회).
        is_box_suspicion = False
        if not manual:
            if new_price > 0 and existing_price > 0 and new_price > existing_price * 5:
                is_box_suspicion = True
            elif new_price > 0 and existing_sale > 0 and new_price > existing_sale * 5:
                is_box_suspicion = True
        if is_box_suspicion:
            new_price = existing_price
            new_name  = existing['costco_name'] or costco_name
        conn.execute("""UPDATE products
                        SET unit_price=?, costco_name=?, updated_at=?, product_no=?, split_qty=?, shipping_fee=?,
                            naver_origin_pno=COALESCE(NULLIF(?, ''), naver_origin_pno)
                        WHERE id=?""",
                     (new_price, new_name, now, product_no, split_qty, fee,
                      naver_origin_pno or '', existing['id']))

        # ⭐ 자동 분리: 같은 product_no를 가진 다른 행이 있으면 이 행만 매칭에서 격리
        if auto_split_costco_no and product_no:
            _siblings = conn.execute(
                "SELECT COUNT(*) FROM products WHERE product_no=? AND id<>?",
                (product_no, existing['id'])
            ).fetchone()
            if _siblings and _siblings[0] > 0:
                conn.execute(
                    "UPDATE products SET costco_no_display=?, product_no='' WHERE id=?",
                    (product_no, existing['id'])
                )
    else:
        fee = shipping_fee if shipping_fee is not None else 0
        conn.execute("""INSERT INTO products
                        (product_no, store_product_name, costco_name, match_keyword,
                         unit_price, split_qty, shipping_fee, naver_origin_pno, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (product_no, costco_name, costco_name, keyword, price, split_qty, fee,
                      naver_origin_pno or '', now))
    conn.commit()
    conn.close()
