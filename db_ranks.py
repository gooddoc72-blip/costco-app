"""
키워드 순위 추적 레이어
keyword_tracking + rank_history 테이블 전담.
"""
from datetime import datetime

from db_core import get_user_db

# 검색결과에서 아예 사라진(미노출) 항목의 낙폭 대용값. 실제 낙폭보다 항상 크게 잡아
# 하락 알림 맨 위에 오도록 한다.
DROP_DISAPPEARED = 999


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
    try:
        conn.execute("ALTER TABLE rank_history ADD COLUMN rank_compare INTEGER")
    except Exception:
        pass
    # ── 쿠팡 순위추적 + 크롤러 수집분을 담기 위한 확장 ────────────────
    # 기존 3개 컬럼(원부/가격비교/단독)은 폐지된 네이버 쇼핑검색 API 산출물이라
    # 쿠팡 순위·아이템위너·카탈로그내 순위를 담을 자리가 없었다. 컬럼만 덧붙여
    # 기존 데이터와 화면(캘린더·하락감지)은 그대로 두고 확장한다.
    #   rank_total     — 두 플랫폼 공통 '대표 순위'(광고 제외). 기존 화면이 이 값을 읽는다.
    #   rank_exposure  — 광고까지 포함한 '실제 보이는 순위'
    #   is_item_winner — 쿠팡 전용. 'X'면 내 상품은 노출됐지만 카드엔 남의 가격이 걸린 상태
    #   rank_in_catalog/mall_count — 네이버 가격비교 판매처 목록에서 내 위치와 총 판매처 수
    for col_sql in [
        "ALTER TABLE keyword_tracking ADD COLUMN platform TEXT DEFAULT 'naver'",
        "ALTER TABLE keyword_tracking ADD COLUMN coupang_product_id TEXT DEFAULT ''",
        "ALTER TABLE keyword_tracking ADD COLUMN coupang_vendor_item_id TEXT DEFAULT ''",
        "ALTER TABLE rank_history ADD COLUMN rank_exposure INTEGER",
        "ALTER TABLE rank_history ADD COLUMN rank_in_catalog INTEGER",
        "ALTER TABLE rank_history ADD COLUMN mall_count INTEGER",
        "ALTER TABLE rank_history ADD COLUMN is_item_winner TEXT",
        "ALTER TABLE rank_history ADD COLUMN is_ad INTEGER",
        "ALTER TABLE rank_history ADD COLUMN page INTEGER",
        "ALTER TABLE rank_history ADD COLUMN source TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(col_sql)
        except Exception:
            pass   # 이미 있는 컬럼 — sqlite는 IF NOT EXISTS를 지원하지 않아 예외로 판별한다
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


def update_keyword_tracking(username, tracking_id, search_keyword=None,
                            product_keyword=None, store_name=None):
    """추적 항목 수정 — 검색 키워드/상품 키워드/스토어명 (None인 항목은 미변경)."""
    conn = get_user_db(username)
    _sets, _params = [], []
    if search_keyword is not None:
        _sets.append("search_keyword=?"); _params.append(search_keyword)
    if product_keyword is not None:
        _sets.append("product_keyword=?"); _params.append(product_keyword)
    if store_name is not None:
        _sets.append("store_name=?"); _params.append(store_name)
    if _sets:
        _params.append(int(tracking_id))
        conn.execute(f"UPDATE keyword_tracking SET {', '.join(_sets)} WHERE id=?", _params)
        conn.commit()
    conn.close()


def save_rank_result(username, tracking_id, rank_wonbu, rank_solo, rank_compare=None):
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


def find_or_create_tracking(conn, row):
    """수집 결과 1건에 대응하는 keyword_tracking 행을 찾고, 없으면 만든다.

    외부 수집기(Crawler Pro)는 tracking_id를 모르므로 '무엇을 추적 중인지'로 찾는다.
    식별 키는 플랫폼마다 다르다:
      쿠팡   — coupang_product_id (판매자가 달라도 상품은 이 값으로 묶인다)
      네이버 — naver_product_no(nvMid) 우선, 없으면 store_name
    둘 다 없으면 검색 키워드 + 상품 키워드로 맞춘다.
    """
    platform = (row.get("platform") or "naver").strip().lower()
    search_kw = (row.get("search_keyword") or "").strip()
    product_kw = (row.get("product_keyword") or search_kw).strip()
    pid = str(row.get("product_id") or "").strip()
    vid = str(row.get("vendor_item_id") or "").strip()
    store = (row.get("store_name") or "").strip()

    if platform == "coupang" and pid:
        where, params = "platform=? AND search_keyword=? AND coupang_product_id=?", (platform, search_kw, pid)
    elif platform != "coupang" and pid:
        where, params = "platform=? AND search_keyword=? AND naver_product_no=?", (platform, search_kw, pid)
    elif store:
        where, params = "platform=? AND search_keyword=? AND store_name=?", (platform, search_kw, store)
    else:
        where, params = "platform=? AND search_keyword=? AND product_keyword=?", (platform, search_kw, product_kw)

    found = conn.execute(
        f"SELECT id FROM keyword_tracking WHERE active=1 AND {where} ORDER BY id LIMIT 1", params
    ).fetchone()
    if found:
        tid = found["id"] if hasattr(found, "keys") else found[0]
        # 위너 판정에 쓰는 vendorItemId는 판매자가 옵션을 갈아끼우면 바뀌므로 최신값으로 갱신
        if platform == "coupang" and vid:
            conn.execute("UPDATE keyword_tracking SET coupang_vendor_item_id=? WHERE id=?", (vid, tid))
        return tid

    cur = conn.execute(
        """INSERT INTO keyword_tracking
           (product_keyword, search_keyword, naver_product_no, store_name,
            platform, coupang_product_id, coupang_vendor_item_id)
           VALUES (?,?,?,?,?,?,?)""",
        (product_kw, search_kw,
         pid if platform != "coupang" else "", store,
         platform, pid if platform == "coupang" else "", vid)
    )
    return cur.lastrowid


def ingest_rank_rows(username, rows, source="crawler_pro"):
    """외부 수집기가 보낸 순위 결과를 저장한다. 반환: {saved, created, skipped}

    '미노출'은 순위가 없는 게 아니라 **찾지 못했다는 사실 자체가 데이터**라서
    rank_total=NULL 로 한 줄 남긴다(그날 측정을 했다는 기록이 남아야 추이가 끊기지 않는다).
    """
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    saved = created = skipped = 0
    before_ids = {r["id"] for r in conn.execute("SELECT id FROM keyword_tracking").fetchall()}

    def _int(v):
        try:
            if v in (None, "", "-"):
                return None
            return int(v)
        except (TypeError, ValueError):
            return None

    for row in rows or []:
        if not (row.get("search_keyword") or "").strip():
            skipped += 1
            continue
        try:
            tid = find_or_create_tracking(conn, row)
            checked_at = (row.get("checked_at") or "").strip() or \
                datetime.now().strftime("%Y-%m-%d %H:%M")
            conn.execute(
                """INSERT INTO rank_history
                   (tracking_id, rank_price_compare, rank_total, rank_compare,
                    rank_exposure, rank_in_catalog, mall_count,
                    is_item_winner, is_ad, page, source, checked_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid,
                 _int(row.get("rank_price_compare")),
                 _int(row.get("rank_organic")),      # 대표 순위(광고 제외) — 기존 화면이 읽는 칸
                 _int(row.get("rank_compare")),
                 _int(row.get("rank_exposure")),
                 _int(row.get("rank_in_catalog")),
                 _int(row.get("mall_count")),
                 (row.get("is_item_winner") or None),
                 _int(row.get("is_ad")),
                 _int(row.get("page")),
                 source, checked_at)
            )
            saved += 1
        except Exception:
            skipped += 1
    conn.commit()
    after = conn.execute("SELECT id FROM keyword_tracking").fetchall()
    created = len({r["id"] for r in after} - before_ids)
    conn.close()
    return {"saved": saved, "created": created, "skipped": skipped}


def get_daily_ranks_in_month(username, tracking_id, year, month):
    """각 날짜의 가장 최근 체크 결과만 반환 (같은 날 여러 번 체크 시 옛 매칭 무시)"""
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    rows = conn.execute("""
        SELECT
            CAST(SUBSTR(rh.checked_at, 9, 2) AS INTEGER) as day,
            rh.rank_price_compare as wonbu,
            rh.rank_compare as compare,
            rh.rank_total as solo,
            rh.checked_at as last_check
        FROM rank_history rh
        WHERE rh.tracking_id = ?
          AND SUBSTR(rh.checked_at, 1, 7) = ?
          AND rh.id = (
              SELECT MAX(rh2.id) FROM rank_history rh2
              WHERE rh2.tracking_id = rh.tracking_id
                AND SUBSTR(rh2.checked_at, 1, 10) = SUBSTR(rh.checked_at, 1, 10)
          )
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
            ORDER BY checked_at DESC, id DESC
            LIMIT 2
        """, (t['id'], f"-{lookback_days} days")).fetchall()
        if len(rows) < 2:
            continue
        cur = _best(rows[0])
        prev = _best(rows[1])
        if prev is None:
            continue          # 직전에 순위가 없었으면 비교 기준이 없다 (신규/재진입)
        # 노출되던 상품이 검색결과에서 통째로 사라진 경우. 순위가 몇 계단 밀린 것보다
        # 훨씬 심각한데, 예전엔 cur=None이라고 건너뛰어 알림에 아예 안 떴다.
        disappeared = (cur is None)
        if not disappeared and cur <= prev:
            continue
        drops.append({
            'tracking_id': t['id'],
            'product_keyword': t['product_keyword'],
            'search_keyword': t['search_keyword'],
            'current_rank': cur,                       # 이탈이면 None
            'prev_rank': prev,
            'drop': DROP_DISAPPEARED if disappeared else (cur - prev),
            'disappeared': disappeared,
            'checked_at': rows[0]['checked_at'],
            'prev_checked_at': rows[1]['checked_at'],
        })
    conn.close()
    # 이탈을 맨 위로, 그 다음 낙폭이 큰 순. (current_rank가 None일 수 있어 정렬 키를 분리)
    drops.sort(key=lambda x: (-x['drop'], x['current_rank'] if x['current_rank'] is not None else 0))
    return drops[:limit]


def delete_trackings_bulk(username, tracking_ids):
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
    conn = get_user_db(username)
    _ensure_rank_tables(conn)
    rows = conn.execute("""
        SELECT kt.id, kt.product_keyword, kt.search_keyword,
               kt.naver_product_no, kt.store_name,
               kt.platform, kt.coupang_product_id,
               rh.rank_price_compare, rh.rank_total, rh.rank_compare, rh.checked_at,
               rh.rank_exposure, rh.rank_in_catalog, rh.mall_count,
               rh.is_item_winner, rh.is_ad, rh.source
        FROM keyword_tracking kt
        LEFT JOIN rank_history rh ON rh.id = (
            SELECT id FROM rank_history
            WHERE tracking_id = kt.id
            -- checked_at은 분 단위라 한 회차에 들어온 행끼리 값이 같을 수 있다.
            -- 그때는 나중에 들어온 행(id가 큰 쪽)이 최신이다.
            ORDER BY checked_at DESC, id DESC LIMIT 1
        )
        WHERE kt.active=1
        ORDER BY kt.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
