"""장보기 목록 제출 — 사용자별 장보기 스냅샷 저장 (auth.db).

사용자가 주문 업로드 후 '장보기 목록 보내기' 클릭 → 그 시점의 장보기 항목을 JSON으로 스냅샷.
관리자는 사용자별/날짜별 목록을 조회·엑셀 다운로드.
"""
import json
import sqlite3
from datetime import datetime

from db_core import AUTH_DB, get_auth_db, get_user_db, retry_on_lock


def _ensure_table():
    conn = get_auth_db()
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
    """장보기 목록 스냅샷 저장. 같은 (username, order_date)에 기존 있으면 덮어씀.

    ⚠️ 12시 크론 동시실행 구간에서 auth.db 잠금으로 통째로 실패한 적이 있다
       (관리자 페이지에 그 사용자 목록이 아예 안 뜸). 잠금은 재시도로 넘긴다.
    """
    _ensure_table()

    def _write():
        conn = get_auth_db()
        try:
            # 기존 동일 사용자×날짜 삭제 (재제출 시 덮어쓰기) + INSERT를 한 트랜잭션으로
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
            return sid
        finally:
            conn.close()

    return retry_on_lock(_write)


#: 관리자 화면·엑셀에 보여줄 컬럼 순서 (팩단가는 노출하지 않음)
SHOP_ITEM_COLS = ("코스트코상품번호", "상품명", "옵션정보", "주문건수", "주문수량",
                  "코스트코구매수량", "매장금액", "정산금액", "배송비")


def normalize_shopping_items(items: list) -> list:
    """저장된 items_json → 화면/엑셀용 표준 형태.

    · '팩단가'는 제외 (매장금액에 이미 반영됨)
    · 예전 제출분의 '예상금액'은 '매장금액'으로 읽어준다 (하위호환)
    """
    out = []
    for _it in (items or []):
        if not isinstance(_it, dict):
            continue
        _amt = _it.get("매장금액")
        if _amt in (None, ""):
            _amt = _it.get("예상금액") or 0      # 구 스키마 폴백
        _row = {}
        for _c in SHOP_ITEM_COLS:
            if _c == "매장금액":
                _row[_c] = int(_amt or 0)
            elif _c in ("주문건수", "주문수량", "코스트코구매수량", "정산금액", "배송비"):
                try:
                    _row[_c] = int(_it.get(_c) or 0)
                except (TypeError, ValueError):
                    _row[_c] = 0
            else:
                _row[_c] = str(_it.get(_c, "") or "")
        out.append(_row)
    return out


def get_recent_shopping_submissions(limit: int = 50, username: str = None) -> list:
    """최근 제출 목록. username 지정 시 해당 사용자만."""
    _ensure_table()
    conn = get_auth_db()
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


def get_shopping_submissions_range(date_from: str, date_to: str) -> list:
    """[관리자] 날짜범위 내 사용자별 장보기 제출 집계.
    반환: [{username, order_date, order_count(주문건수 합), item_count(종수), amount(코스트코구매금액 합)}]
    order_count = items_json 각 항목의 '주문건수' 합계. 같은 (user,date)는 제출이 이미 덮어써서 1행.
    """
    _ensure_table()
    conn = get_auth_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT username, order_date, total_items, total_amount, items_json
           FROM shopping_list_submissions
           WHERE order_date >= ? AND order_date <= ?
           ORDER BY order_date DESC, username""",
        (date_from, date_to)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        _oc = 0
        try:
            for _it in json.loads(r['items_json'] or '[]'):
                _oc += int(_it.get('주문건수') or 0)
        except Exception:
            _oc = 0
        out.append({
            'username': r['username'],
            'order_date': r['order_date'],
            'order_count': _oc,
            'item_count': int(r['total_items'] or 0),
            'amount': int(r['total_amount'] or 0),
        })
    return out


def get_shopping_submissions_detail(date_from: str, date_to: str,
                                   username: str = None, limit: int = 2000) -> list:
    """[관리자] 날짜범위 내 장보기 제출 **원본 행**(items_json 포함) 조회.

    get_shopping_submissions_range()는 집계만 돌려주므로, 과거 날짜의 상세 품목·
    엑셀·프린트가 필요한 관리자 화면은 이 함수를 쓴다.
    username 지정 시 해당 사용자만. 최신 날짜 → 사용자명 순으로 정렬.
    """
    _ensure_table()
    conn = get_auth_db()
    conn.row_factory = sqlite3.Row
    sql = """SELECT id, username, order_date, submitted_at, total_items, total_amount, items_json
             FROM shopping_list_submissions
             WHERE order_date >= ? AND order_date <= ?"""
    params = [date_from, date_to]
    if username:
        sql += " AND username = ?"
        params.append(username)
    sql += " ORDER BY order_date DESC, username ASC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_shopping_submission_dates(limit: int = 180) -> list:
    """[관리자] 제출 이력이 있는 날짜 목록 (최신순). 기간 선택 UI용."""
    _ensure_table()
    conn = get_auth_db()
    rows = conn.execute(
        """SELECT DISTINCT order_date FROM shopping_list_submissions
           ORDER BY order_date DESC LIMIT ?""", (int(limit),)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def delete_shopping_submission(submission_id: int) -> bool:
    _ensure_table()
    conn = get_auth_db()
    cur = conn.execute(
        "DELETE FROM shopping_list_submissions WHERE id=?", (int(submission_id),)
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n > 0


# ── 장보기 목록 제외(발송 전 빼기) — 사용자 DB에 날짜별 영속 ──────────
#   세션에만 두면 새로고침(=새 Streamlit 세션) 때 제외가 풀려 뺐던 제품이
#   장보기 목록에 되살아난다. 주문 데이터(order_history/daily_orders)는
#   건드리지 않고 '이 날짜에 뺀 항목'만 저장한다.

def _ensure_shop_excl_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS shopping_exclusions (
                        order_date  TEXT NOT NULL,
                        rowkey      TEXT NOT NULL,
                        label       TEXT DEFAULT '',
                        excluded_at TEXT,
                        PRIMARY KEY (order_date, rowkey)
                    )""")


def get_shopping_exclusions(username: str, order_date: str) -> list:
    """해당 날짜에 장보기 목록에서 뺀 rowkey 목록."""
    conn = get_user_db(username)
    try:
        _ensure_shop_excl_table(conn)
        rows = conn.execute(
            "SELECT rowkey FROM shopping_exclusions WHERE order_date=? ORDER BY rowkey",
            (str(order_date),)).fetchall()
        return [str(r['rowkey']) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_shopping_exclusion_rows(username: str, order_date: str) -> list:
    """제외 목록 상세 (복구 UI용) — 오늘 목록에 없는 항목도 라벨로 보여주기 위함."""
    conn = get_user_db(username)
    try:
        _ensure_shop_excl_table(conn)
        rows = conn.execute(
            "SELECT rowkey, label, excluded_at FROM shopping_exclusions "
            "WHERE order_date=? ORDER BY excluded_at DESC, rowkey", (str(order_date),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def add_shopping_exclusions(username: str, order_date: str, rowkeys, labels: dict = None) -> int:
    """제외 목록에 추가(기존 건 유지). 반환: 추가 시도한 건수."""
    keys = [str(k) for k in (rowkeys or []) if str(k).strip()]
    if not keys:
        return 0
    conn = get_user_db(username)
    try:
        _ensure_shop_excl_table(conn)
        _now = datetime.now().strftime("%Y-%m-%d %H:%M")
        for _k in keys:
            conn.execute(
                "INSERT OR REPLACE INTO shopping_exclusions "
                "(order_date, rowkey, label, excluded_at) VALUES (?,?,?,?)",
                (str(order_date), _k, str((labels or {}).get(_k, '') or ''), _now))
        conn.commit()
        return len(keys)
    finally:
        conn.close()


def remove_shopping_exclusions(username: str, order_date: str, rowkeys) -> int:
    """제외 해제(되살리기). 반환: 해제된 건수."""
    keys = [str(k) for k in (rowkeys or []) if str(k).strip()]
    if not keys:
        return 0
    conn = get_user_db(username)
    try:
        _ensure_shop_excl_table(conn)
        ph = ",".join("?" * len(keys))
        cur = conn.execute(
            f"DELETE FROM shopping_exclusions WHERE order_date=? AND rowkey IN ({ph})",
            [str(order_date)] + keys)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def clear_shopping_exclusions(username: str, order_date: str) -> int:
    """해당 날짜의 제외를 모두 해제. 반환: 해제 건수."""
    conn = get_user_db(username)
    try:
        _ensure_shop_excl_table(conn)
        cur = conn.execute("DELETE FROM shopping_exclusions WHERE order_date=?", (str(order_date),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
