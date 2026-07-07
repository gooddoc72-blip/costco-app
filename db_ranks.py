"""
키워드 순위 추적 레이어
keyword_tracking + rank_history 테이블 전담.
"""
from datetime import datetime

from db_core import get_user_db


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
            ORDER BY checked_at DESC
            LIMIT 2
        """, (t['id'], f"-{lookback_days} days")).fetchall()
        if len(rows) < 2:
            continue
        cur = _best(rows[0])
        prev = _best(rows[1])
        if cur is None or prev is None:
            continue
        if cur > prev:
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
