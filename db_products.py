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
        # 분리 행이 어떤 sell_factor용인지 힌트 (분리 호출 시 매칭된 주문의 'x N개' 패턴)
        "ALTER TABLE products ADD COLUMN split_sell_factor INTEGER DEFAULT 0",
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


def upsert_product_unified(username, match_keyword, costco_name=None,
                            unit_price=None, sale_price=None, shipping_fee=None,
                            product_no=None, naver_origin_pno=None, naver_channel_pno=None,
                            split_qty=None, category=None, status=None, from_naver=None,
                            auto_split_costco_no=False, sell_factor_hint=0):
    """
    [통합 상품 정보 업데이트]
    - 조회 순위: naver_origin_pno > product_no > match_keyword
    - 안전장치: unit_price 업데이트 시 기존 가격(또는 판매가) 대비 5배 초과 시 박스 가격으로 의심하여 차단.
    - auto_split_costco_no: 가격 수정 시 동일한 product_no를 가진 다른 행이 있으면 이 행만 격리.
    """
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 1. 대상 찾기
    existing = None
    if naver_origin_pno:
        existing = conn.execute(
            "SELECT * FROM products WHERE naver_origin_pno=? AND naver_origin_pno != ''",
            (naver_origin_pno,)
        ).fetchone()
    if not existing and product_no:
        existing = conn.execute(
            "SELECT * FROM products WHERE product_no=? AND (naver_origin_pno='' OR naver_origin_pno IS NULL)",
            (product_no,)
        ).fetchone()
    if not existing:
        existing = conn.execute(
            "SELECT * FROM products WHERE match_keyword=?", (match_keyword,)
        ).fetchone()

    # 2. 값 결정 (None이면 기존값 유지)
    def _v(new, key, default=None):
        if new is not None: return new
        return existing[key] if existing else default

    final_costco_name = costco_name or (existing['costco_name'] if existing else match_keyword)
    final_sale        = _v(sale_price, 'sale_price', 0)
    final_fee         = _v(shipping_fee, 'shipping_fee', 0)
    final_sq          = max(1, int(_v(split_qty, 'split_qty', 1)))
    final_st          = _v(status, 'status', 'SALE')
    final_fn          = int(_v(from_naver, 'from_naver', 0))
    final_op          = naver_origin_pno or (existing['naver_origin_pno'] if existing else '')
    final_ch          = naver_channel_pno or (existing['naver_channel_pno'] if existing else '')
    final_cat         = _v(category, 'category', '')
    final_pno         = product_no or (existing['product_no'] if existing else '')
    
    # 매입가(unit_price) 결정 및 안전장치
    new_unit = unit_price
    if existing and new_unit is not None:
        old_unit = int(existing['unit_price'] or 0)
        old_sale = int(existing['sale_price'] or 0)
        # 5배 초과 시 박스 가격 의심 -> 기존 가격 유지
        if old_unit > 0 and new_unit > old_unit * 5:
            new_unit = old_unit
        elif old_sale > 0 and new_unit > old_sale * 5:
            new_unit = old_unit
    final_unit = _v(new_unit, 'unit_price', 0)

    if existing:
        conn.execute("""
            UPDATE products SET 
                match_keyword=?, costco_name=?, store_product_name=?, 
                unit_price=?, sale_price=?, shipping_fee=?, split_qty=?, 
                product_no=?, naver_origin_pno=?, naver_channel_pno=?, 
                category=?, status=?, from_naver=?, updated_at=?
            WHERE id=?
        """, (match_keyword, final_costco_name, final_costco_name,
              final_unit, final_sale, final_fee, final_sq,
              final_pno, final_op, final_ch,
              final_cat, final_st, final_fn, now, existing['id']))
        
        # 자동 분리 (隔離): 같은 base product_no를 가진 다른 행이 존재할 때
        # 이 행만 격리 — product_no에 " (N)" suffix 부여하고 원본은 costco_no_display로 보존.
        # 예: 599369 → 599369 (1), 다음 분리는 599369 (2).
        if auto_split_costco_no and final_pno:
            # sibling = (base product_no가 같은 행) + (이미 분리된 행: costco_no_display=base)
            # 둘 중 하나라도 있으면 이 행도 분리해 격리한다.
            _siblings = conn.execute(
                "SELECT COUNT(*) FROM products "
                "WHERE (product_no=? OR costco_no_display=?) AND id<>?",
                (final_pno, final_pno, existing['id'])
            ).fetchone()
            if _siblings and _siblings[0] > 0:
                # 이미 분리된 동일 base의 (N) 행 중 가장 큰 N + 1
                import re as _re_split
                _split_rows = conn.execute(
                    "SELECT product_no FROM products "
                    "WHERE costco_no_display=? AND product_no LIKE ? AND id<>?",
                    (final_pno, f'{final_pno} (%)', existing['id'])
                ).fetchall()
                _next_idx = 1
                for _row in _split_rows:
                    _pn = _row['product_no'] if hasattr(_row, '__getitem__') else _row[0]
                    _m = _re_split.search(r'\((\d+)\)\s*$', str(_pn or ''))
                    if _m:
                        _n = int(_m.group(1))
                        if _n >= _next_idx:
                            _next_idx = _n + 1
                _new_pno = f"{final_pno} ({_next_idx})"
                conn.execute(
                    "UPDATE products SET product_no=?, costco_no_display=?, split_sell_factor=? WHERE id=?",
                    (_new_pno, final_pno, int(sell_factor_hint or 0), existing['id'])
                )
    else:
        conn.execute("""
            INSERT INTO products (
                product_no, store_product_name, costco_name, match_keyword,
                unit_price, split_qty, sale_price, shipping_fee, status, from_naver,
                naver_origin_pno, naver_channel_pno, category, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (final_pno, final_costco_name, final_costco_name, match_keyword,
              final_unit, final_sq, final_sale, final_fee, final_st, final_fn,
              final_op, final_ch, final_cat, now))
    
    conn.commit()
    conn.close()


def upsert_user_private(username, match_keyword, costco_name,
                        sale_price=None, shipping_fee=None, naver_product_no=None,
                        status=None, from_naver=None, naver_origin_pno=None,
                        split_qty=None, category=None):
    """[하위 호환]"""
    upsert_product_unified(
        username, match_keyword, costco_name=costco_name,
        sale_price=sale_price, shipping_fee=shipping_fee, naver_channel_pno=naver_product_no,
        status=status, from_naver=from_naver, naver_origin_pno=naver_origin_pno,
        split_qty=split_qty, category=category
    )



def get_all_products_merged(username):
    shared = get_shared_products()
    user_prods = get_all_products(username)
    
    # 최적화: 번호·키워드·링크 기반 맵 구축
    user_by_pno    = {str(p.get('product_no') or '').strip(): p for p in user_prods if str(p.get('product_no') or '').strip()}
    user_by_kw     = {p['match_keyword']: p for p in user_prods}
    user_by_linked = {str(p['linked_shared_id']): p for p in user_prods if p.get('linked_shared_id') is not None}

    matched_user_ids = set()
    merged = []
    for sp in shared:
        kw = sp['match_keyword']
        sp_pno = str(sp.get('product_no') or '').strip()
        
        # 매칭: 번호 우선 -> 키워드 -> 링크
        up = user_by_pno.get(sp_pno)
        if up is None: up = user_by_kw.get(kw)
        if up is None: up = user_by_linked.get(str(sp['id']), {})
        
        if up and up.get('id'):
            matched_user_ids.add(up['id'])
        
        from_naver = int(up.get('from_naver') or 0) if up else 0
        display_costco_name = (up.get('costco_name') if up and from_naver and up.get('costco_name') else sp['costco_name'])
        
        merged.append({
            'shared_id': sp['id'],
            'product_no': sp.get('product_no', ''),
            'costco_name': display_costco_name,
            'naver_name': up.get('costco_name', '') if (up and from_naver) else '',
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
        merged.append({
            'shared_id': None,
            'product_no': up.get('product_no', ''),
            'costco_name': up['costco_name'],
            'naver_name': up['costco_name'] if from_naver else '',
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
                   sell_factor_hint=0):
    """[하위 호환]"""
    upsert_product_unified(
        username, keyword, costco_name=costco_name,
        unit_price=price, product_no=product_no, split_qty=split_qty,
        shipping_fee=shipping_fee, naver_origin_pno=naver_origin_pno,
        auto_split_costco_no=auto_split_costco_no,
        sell_factor_hint=sell_factor_hint,
    )
def upsert_user_private(username, costco_name, keyword, price, product_no='', split_qty=1, shipping_fee=None):
    """[하위 호환]"""
    return upsert_product(username, costco_name, keyword, price, product_no, split_qty, shipping_fee)
