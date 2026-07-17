"""공지사항 — 관리자가 올리면 모든 사용자 홈 상단에 뜬다.

할인제품 대량구매(bulk_deals)와는 별개. 이쪽은 순수 알림용 글이다.
auth.db(공유)에 둔다 — 한 번 쓰면 전원이 봐야 하므로.
"""
import sqlite3
from datetime import datetime

from db_core import AUTH_DB

LEVELS = {
    'info':    ('ℹ️', '안내'),
    'warning': ('⚠️', '주의'),
    'urgent':  ('🔴', '긴급'),
}


def _conn():
    conn = sqlite3.connect(AUTH_DB, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        body TEXT DEFAULT '',
        level TEXT DEFAULT 'info',
        pinned INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        starts_at TEXT DEFAULT '',
        ends_at TEXT DEFAULT '',
        created_by TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notice_active ON notices(active, pinned)")


def create_notice(title, body='', level='info', pinned=0, ends_at='', created_by=''):
    if not (title or '').strip():
        return 0
    conn = _conn()
    _ensure_table(conn)
    cur = conn.execute("""INSERT INTO notices
        (title, body, level, pinned, active, starts_at, ends_at, created_by, created_at)
        VALUES (?,?,?,?,1,?,?,?,?)""",
        (title.strip(), str(body or ''),
         str(level if level in LEVELS else 'info'),
         1 if pinned else 0, datetime.now().strftime("%Y-%m-%d"),
         str(ends_at or ''), str(created_by or ''), _now()))
    nid = cur.lastrowid
    conn.commit()
    conn.close()
    return int(nid or 0)


def update_notice(notice_id, title=None, body=None, level=None, pinned=None):
    conn = _conn()
    _ensure_table(conn)
    sets, params = [], []
    if title is not None:
        sets.append("title=?"); params.append(str(title).strip())
    if body is not None:
        sets.append("body=?"); params.append(str(body))
    if level is not None:
        sets.append("level=?"); params.append(str(level if level in LEVELS else 'info'))
    if pinned is not None:
        sets.append("pinned=?"); params.append(1 if pinned else 0)
    if sets:
        params.append(int(notice_id))
        conn.execute(f"UPDATE notices SET {','.join(sets)} WHERE id=?", params)
        conn.commit()
    conn.close()
    return True


def get_notices(active_only=True, limit=30):
    """홈 표시용. 고정(pinned) 먼저, 그다음 최신순. 종료일이 지난 건 제외."""
    conn = _conn()
    _ensure_table(conn)
    sql = "SELECT * FROM notices WHERE 1=1"
    params = []
    if active_only:
        sql += (" AND active=1 AND (ends_at='' OR ends_at IS NULL"
                " OR date(ends_at) >= date('now'))")
    sql += " ORDER BY pinned DESC, id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_notice_active(notice_id, active):
    conn = _conn()
    _ensure_table(conn)
    conn.execute("UPDATE notices SET active=? WHERE id=?",
                 (1 if active else 0, int(notice_id)))
    conn.commit()
    conn.close()
    return True


def delete_notice(notice_id):
    conn = _conn()
    _ensure_table(conn)
    conn.execute("DELETE FROM notices WHERE id=?", (int(notice_id),))
    conn.commit()
    conn.close()
    return True
