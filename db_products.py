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


# ── 공유 네이버↔코스트코 매핑 (auth.db/shared_naver_map) ────────
#  공유DB의 코스트코 상품번호에 "각 사용자의 네이버 상품번호"를 매칭해 누적 저장.
#  네이버번호는 판매자마다 다르므로 (username, naver_pno) 단위로 저장하되,
#  코스트코번호는 공유 → 한 명이 수동매칭하면 그 사용자의 주문은 이후 자동 해석된다.

def _ensure_shared_naver_map(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS shared_naver_map (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        costco_pno TEXT NOT NULL,
        username TEXT DEFAULT '',
        naver_pno TEXT DEFAULT '',
        naver_origin_pno TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        updated_at TEXT DEFAULT '',
        UNIQUE(username, naver_pno)
    )""")
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_snm_naver ON shared_naver_map(naver_pno)",
        "CREATE INDEX IF NOT EXISTS idx_snm_origin ON shared_naver_map(naver_origin_pno)",
        "CREATE INDEX IF NOT EXISTS idx_snm_costco ON shared_naver_map(costco_pno)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass


def upsert_shared_naver_map(costco_pno, username, naver_pno='', naver_origin_pno='',
                            product_name=''):
    """공유DB에 (코스트코번호 ↔ 사용자 네이버번호) 매핑 저장/갱신.
    수동매칭 시 호출 → 이후 그 네이버번호 주문이 오면 코스트코번호=공유가격으로 자동 해석."""
    costco_pno = str(costco_pno or '').strip()
    naver_pno = str(naver_pno or '').strip()
    naver_origin_pno = str(naver_origin_pno or '').strip()
    # 네이버번호가 하나도 없거나 코스트코번호가 없으면 저장 불가
    if not costco_pno or (not naver_pno and not naver_origin_pno):
        return False
    conn = sqlite3.connect(AUTH_DB)
    _ensure_shared_naver_map(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute(
        """INSERT INTO shared_naver_map
           (costco_pno, username, naver_pno, naver_origin_pno, product_name, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(username, naver_pno) DO UPDATE SET
               costco_pno=excluded.costco_pno,
               naver_origin_pno=excluded.naver_origin_pno,
               product_name=excluded.product_name,
               updated_at=excluded.updated_at""",
        (costco_pno, str(username or ''), naver_pno, naver_origin_pno,
         str(product_name or ''), now)
    )
    conn.commit()
    conn.close()
    # 매칭 캐시 무효화 (같은 프로세스 내 즉시 반영)
    try:
        import services
        services.invalidate_shared_naver_map()
    except Exception:
        pass
    return True


def get_shared_naver_map_rows():
    """공유 네이버↔코스트코 매핑 전체 행 조회 (관리/표시용)."""
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    _ensure_shared_naver_map(conn)
    rows = conn.execute(
        "SELECT * FROM shared_naver_map ORDER BY costco_pno, username"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_shared_naver_costco_map():
    """{네이버번호(channel/origin): 코스트코번호} 통합 맵 (전체 사용자 누적)."""
    conn = sqlite3.connect(AUTH_DB)
    _ensure_shared_naver_map(conn)
    rows = conn.execute(
        "SELECT costco_pno, naver_pno, naver_origin_pno FROM shared_naver_map"
    ).fetchall()
    conn.close()
    out = {}
    for cp, npno, org in rows:
        cp = str(cp or '').strip()
        if not cp:
            continue
        if npno and str(npno).strip():
            out[str(npno).strip()] = cp
        if org and str(org).strip():
            out.setdefault(str(org).strip(), cp)
    return out


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
        # 가격등록일자: 매입가(unit_price)가 실제로 바뀐 시점만 기록 (updated_at은 모든 수정에 갱신)
        "ALTER TABLE products ADD COLUMN price_updated_at TEXT DEFAULT ''",
        # 옵션번호: 같은 네이버 상품번호라도 옵션별 매입가가 다를 때 옵션조합 고유번호(optionCode)로 구분
        "ALTER TABLE products ADD COLUMN naver_option_code TEXT DEFAULT ''",
        # 묶음배수: 상품명의 "x N개"가 상품마다 뜻이 달라(내용물 설명 vs 진짜 묶음) 명시 지정이 필요.
        #   0 = 미지정 → 상품명에서 추출 (기존 동작 유지)
        #   1 = 내용물 설명 → 곱하지 않음 (예: 신라면 120g x 30개 = 30개들이 1박스)
        #   N = 코스트코 물건 N개를 묶어 보냄 → N배
        # services.resolve_pack_factor()가 유일한 해석 지점.
        "ALTER TABLE products ADD COLUMN pack_multiplier INTEGER DEFAULT 0",
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


def delete_user_products_by_ids(username, ids):
    """사용자 DB의 products에서 id 목록을 삭제. 반환: 삭제된 행 수."""
    _ids = [int(i) for i in (ids or []) if str(i).strip()]
    if not _ids:
        return 0
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    _ph = ",".join("?" * len(_ids))
    cur = conn.execute(f"DELETE FROM products WHERE id IN ({_ph})", _ids)
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def link_naver_to_shared(username: str, user_product_id: int, shared_id: int):
    conn_auth = sqlite3.connect(AUTH_DB)
    sp_row = conn_auth.execute(
        "SELECT product_no FROM shared_products WHERE id=?", (shared_id,)
    ).fetchone()
    conn_auth.close()
    costco_pno = (sp_row[0] or '').strip() if sp_row else ''

    conn = get_user_db(username)
    _ensure_products_columns(conn)
    # 사용자 네이버번호 조회 — 공유DB 매핑에 함께 저장하기 위함
    _up = conn.execute(
        "SELECT naver_channel_pno, naver_origin_pno, costco_name FROM products WHERE id=?",
        (user_product_id,)
    ).fetchone()
    conn.execute(
        "UPDATE products SET linked_shared_id=?, product_no=? WHERE id=?",
        (shared_id, costco_pno, user_product_id)
    )
    conn.commit()
    conn.close()

    # 공유DB에 (코스트코번호 ↔ 이 사용자 네이버번호) 매핑 누적 저장
    if costco_pno and _up:
        try:
            upsert_shared_naver_map(
                costco_pno, username,
                naver_pno=str(_up['naver_channel_pno'] or ''),
                naver_origin_pno=str(_up['naver_origin_pno'] or ''),
                product_name=str(_up['costco_name'] or '')
            )
        except Exception:
            pass


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


def set_pack_multiplier(username, product_id, value):
    """묶음배수 지정. 0=미지정(상품명에서 추출), 1=내용물 설명, N=N개 묶음."""
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    conn.execute("UPDATE products SET pack_multiplier=?, updated_at=? WHERE id=?",
                 (max(0, min(50, int(value or 0))),
                  datetime.now().strftime("%Y-%m-%d %H:%M"), int(product_id)))
    conn.commit()
    conn.close()
    return True


def get_pack_ambiguous_products(username, only_unset=True):
    """상품명에 "x N개"가 있는데 묶음배수가 미지정인 상품 — 분류가 필요한 목록.

    소분수가 2 이상이면 (단가//소분수) x 배수가 서로 상쇄되어 지금도 값이 맞는다.
    실제로 위험한 건 소분수가 1인데 "x N개"가 붙은 상품이다.
    """
    import re as _re
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    sql = ("SELECT id, product_no, store_product_name, costco_name, unit_price, "
           "split_qty, sale_price, COALESCE(pack_multiplier,0) AS pack_multiplier "
           "FROM products WHERE (store_product_name LIKE '%x %개%' "
           "OR costco_name LIKE '%x %개%')")
    if only_unset:
        sql += " AND COALESCE(pack_multiplier,0)=0"
    sql += " ORDER BY unit_price DESC"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    conn.close()
    out = []
    for r in rows:
        name = r['store_product_name'] or r['costco_name'] or ''
        m = _re.search(r'x\s*(\d+)\s*개', name, _re.IGNORECASE)
        if not m:
            continue
        n = int(m.group(1))
        r['name_factor'] = n                       # 상품명이 말하는 숫자
        r['applied'] = n if 1 < n <= 50 else 1     # 현재 실제로 적용 중인 배수(클램프 포함)
        sq = max(1, int(r['split_qty'] or 1))
        # 소분수 > 1 이면 //sq 로 상쇄 → 현재도 정상. 소분수 1 + 배수 적용 = 위험.
        r['risky'] = (sq == 1 and r['applied'] > 1)
        out.append(r)
    return out


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


def _contribute_shared_from_user(product_no, costco_name, keyword, price, split_qty=1,
                                 username='', manual=False):
    """방법 A(크라우드 누적): 사용자가 코스트코번호+구입가를 직접 저장하면 공유DB에 기여.
    - manual=False(자동 크롤링/영수증): 공유값이 비어있을 때만 채움(기존 공유값 보호).
    - manual=True(사용자 직접 수정): 공유값을 덮어씀. 매칭이 공유단가를 우선하므로,
      덮어쓰지 않으면 저장한 단가가 매칭 때 공유 옛값으로 원복됨(원복 버그 해결)."""
    if not product_no or int(price or 0) <= 0:
        return False
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id, unit_price FROM shared_products WHERE product_no=?",
                       (str(product_no),)).fetchone()
    conn.close()
    if (not manual) and row and int(row['unit_price'] or 0) > 0:
        return False  # 자동 기여는 기존 공유값 보호(다른 판매자 값 유지)
    _upsert_shared_internal(costco_name or keyword, keyword or costco_name,
                            store_price=int(price), product_no=str(product_no),
                            split_qty=split_qty, updated_by=f'user:{username}'[:40])
    return True


def link_costco_to_naver(username, naver_no, costco_no):
    """영수증 매칭 시, 주문의 네이버번호(channel 또는 origin)로 제품 레코드를 찾아
    코스트코 상품번호를 채운다. (코스트코번호가 비어 있는 레코드만 — 기존값 보호)
    → 다음부터 영수증 정산이 코스트코번호로 정확히 배치됨.

    Returns: 갱신된 행 수.
    """
    naver_no = str(naver_no or '').strip()
    costco_no = str(costco_no or '').strip()
    if not naver_no or not costco_no:
        return 0
    conn = get_user_db(username)
    _ensure_products_columns(conn)
    try:
        cur = conn.execute(
            "UPDATE products SET product_no=? "
            "WHERE (naver_channel_pno=? OR naver_origin_pno=?) "
            "AND (product_no IS NULL OR product_no='')",
            (costco_no, naver_no, naver_no))
        n = cur.rowcount
        conn.commit()
    except Exception:
        n = 0
    finally:
        conn.close()
    return n


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
        _price_changed_date = now if int(new_price or 0) != existing_price else ''
        conn.execute("""UPDATE products
                        SET unit_price=?, costco_name=?, updated_at=?, product_no=?, split_qty=?, shipping_fee=?,
                            naver_origin_pno=COALESCE(NULLIF(?, ''), naver_origin_pno),
                            price_updated_at=COALESCE(NULLIF(?, ''), price_updated_at)
                        WHERE id=?""",
                     (new_price, new_name, now, product_no, split_qty, fee,
                      naver_origin_pno or '', _price_changed_date, existing['id']))

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
                         unit_price, split_qty, shipping_fee, naver_origin_pno, updated_at,
                         price_updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (product_no, costco_name, costco_name, keyword, price, split_qty, fee,
                      naver_origin_pno or '', now, (now if int(price or 0) > 0 else '')))
    conn.commit()
    conn.close()

    # 방법 A — 크라우드 누적: 사용자가 코스트코번호+구입가를 직접 저장(manual)하면 공유DB에 기여.
    #   공유DB에 그 코스트코번호가 없을 때만 추가 → 시간이 지나며 모든 판매자 커버리지 상승.
    if manual and product_no and int(price or 0) > 0:
        try:
            _contribute_shared_from_user(str(product_no), costco_name, keyword,
                                         int(price), split_qty, username, manual=True)
        except Exception:
            pass
