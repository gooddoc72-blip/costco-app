"""통장·카드 거래내역 (세무회계 ① CSV 업로드 저장).

은행/카드사 CSV는 양식이 달라 '컬럼 매핑'으로 표준화해 bank_tx에 저장.
category(계정과목)는 ② AI 자동분류/수동으로 채움.
"""
from datetime import datetime

from db_core import get_user_db


def _ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS bank_tx (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tx_date TEXT NOT NULL,
        description TEXT DEFAULT '',       -- 적요/가맹점
        amount_in INTEGER DEFAULT 0,       -- 입금
        amount_out INTEGER DEFAULT 0,      -- 출금/카드사용
        balance INTEGER DEFAULT 0,
        counterparty TEXT DEFAULT '',
        source_type TEXT DEFAULT 'bank',   -- bank | card
        source_name TEXT DEFAULT '',       -- 은행/카드사명
        category TEXT DEFAULT '',          -- 계정과목(분류)
        vat_deductible INTEGER DEFAULT 0,  -- 매입세액공제 대상(카드 등)
        biz_use INTEGER DEFAULT 1,         -- 사업용 여부(1=사업, 0=개인)
        memo TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(tx_date, description, amount_in, amount_out, balance, source_name)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_banktx_date ON bank_tx(tx_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_banktx_cat ON bank_tx(category)")
    conn.commit()


def save_bank_tx(username: str, rows: list) -> int:
    """거래 저장 (중복 자동 무시). rows: dict 리스트
    {tx_date, description, amount_in, amount_out, balance, counterparty,
     source_type, source_name}."""
    if not rows:
        return 0
    conn = get_user_db(username)
    _ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    for r in rows:
        d = str(r.get('tx_date', '') or '').strip()
        if not d:
            continue
        cur = conn.execute(
            """INSERT OR IGNORE INTO bank_tx
               (tx_date, description, amount_in, amount_out, balance, counterparty,
                source_type, source_name, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (d, str(r.get('description', '') or ''),
             int(r.get('amount_in', 0) or 0), int(r.get('amount_out', 0) or 0),
             int(r.get('balance', 0) or 0), str(r.get('counterparty', '') or ''),
             str(r.get('source_type', 'bank') or 'bank'),
             str(r.get('source_name', '') or ''), now))
        saved += cur.rowcount
    conn.commit()
    conn.close()
    return saved


def get_bank_tx(username: str, date_from: str, date_to: str, source_type: str = None) -> list:
    conn = get_user_db(username)
    _ensure(conn)
    sql = "SELECT * FROM bank_tx WHERE tx_date BETWEEN ? AND ?"
    params = [date_from, date_to]
    if source_type:
        sql += " AND source_type=?"
        params.append(source_type)
    sql += " ORDER BY tx_date, id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_tx_category(username: str, tx_id: int, category: str,
                       vat_deductible: int = None, biz_use: int = None) -> None:
    conn = get_user_db(username)
    _ensure(conn)
    sets, params = ["category=?"], [category]
    if vat_deductible is not None:
        sets.append("vat_deductible=?"); params.append(int(vat_deductible))
    if biz_use is not None:
        sets.append("biz_use=?"); params.append(int(biz_use))
    params.append(int(tx_id))
    conn.execute(f"UPDATE bank_tx SET {','.join(sets)} WHERE id=?", params)
    conn.commit()
    conn.close()


def get_uncategorized_tx(username: str, limit: int = 500) -> list:
    conn = get_user_db(username)
    _ensure(conn)
    rows = conn.execute(
        "SELECT * FROM bank_tx WHERE COALESCE(category,'')='' ORDER BY tx_date LIMIT ?",
        (int(limit),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tx_category_summary(username: str, date_from: str, date_to: str) -> list:
    """계정과목별 합계 — [{category, in_sum, out_sum, cnt}]."""
    conn = get_user_db(username)
    _ensure(conn)
    rows = conn.execute(
        """SELECT COALESCE(NULLIF(category,''),'(미분류)') category,
                  SUM(amount_in) in_sum, SUM(amount_out) out_sum, COUNT(*) cnt
           FROM bank_tx WHERE tx_date BETWEEN ? AND ?
           GROUP BY COALESCE(NULLIF(category,''),'(미분류)') ORDER BY out_sum DESC""",
        (date_from, date_to)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
