"""장보기 목록 제출 — 사용자별 장보기 스냅샷 저장 (auth.db).

사용자가 주문 업로드 후 '장보기 목록 보내기' 클릭 → 그 시점의 장보기 항목을 JSON으로 스냅샷.
관리자는 사용자별/날짜별 목록을 조회·엑셀 다운로드.
"""
import json
import sqlite3
from datetime import datetime

from db_core import AUTH_DB


def _ensure_table():
    conn = sqlite3.connect(AUTH_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS shopping_list_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        order_date TEXT NOT NULL,
        submitted_at TEXT NOT NULL,
        total_items INTEGER DEFAULT 0,
        total_amount INTEGER DEFAULT 0,
        items_json TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shopping_sub_user ON shopping_list_submissions(username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shopping_sub_date ON shopping_list_submissions(order_date)")
    conn.commit()
    conn.close()


def submit_shopping_list(username: str, order_date: str, items: list,
                         total_items: int = 0, total_amount: int = 0) -> int:
    """장보기 목록 스냅샷 저장. 같은 (username, order_date)에 기존 있으면 덮어씀."""
    _ensure_table()
    conn = sqlite3.connect(AUTH_DB)
    # 기존 동일 사용자×날짜 삭제 (재제출 시 덮어쓰기)
    conn.execute(
        "DELETE FROM shopping_list_submissions WHERE username=? AND order_date=?",
        (username, order_date)
    )
    cur = conn.execute(
        """INSERT INTO shopping_list_submissions
           (username, order_date, submitted_at, total_items, total_amount, items_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (username, order_date, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         int(total_items), int(total_amount),
         json.dumps(items, ensure_ascii=False))
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def get_recent_shopping_submissions(limit: int = 50, username: str = None) -> list:
    """최근 제출 목록. username 지정 시 해당 사용자만."""
    _ensure_table()
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    if username:
        rows = conn.execute(
            """SELECT id, username, order_date, submitted_at, total_items, total_amount, items_json
               FROM shopping_list_submissions WHERE username=?
               ORDER BY submitted_at DESC LIMIT ?""",
            (username, int(limit))
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, username, order_date, submitted_at, total_items, total_amount, items_json
               FROM shopping_list_submissions
               ORDER BY submitted_at DESC LIMIT ?""",
            (int(limit),)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_shopping_submission(submission_id: int) -> bool:
    _ensure_table()
    conn = sqlite3.connect(AUTH_DB)
    cur = conn.execute(
        "DELETE FROM shopping_list_submissions WHERE id=?", (int(submission_id),)
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n > 0
