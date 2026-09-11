"""예치금 원장 — 사용자가 미리 맡긴 돈과 그날 구매분 차감을 한 곳에서 기록한다.

왜 원장인가:
  잔액 컬럼 하나만 두고 더하고 빼면, 어긋났을 때 "언제 무엇 때문에 이렇게
  됐나"에 답할 수가 없다. 정산 원장(db_settle)에서 이미 겪은 문제라 같은
  방식을 쓴다 — **행을 남기고, 잔액은 언제나 합계에서 만든다.**
  잔액을 저장하지 않으면 잔액과 내역이 어긋나는 일 자체가 생기지 않는다.

흐름:
  ① 사용자가 계좌로 입금 → 관리자가 확인하고 `charge()`  (+금액)
  ② 그날 정산이 끝나면 청구액만큼 `deduct()`             (-금액)
     차감과 동시에 그날 청구서를 '입금완료(paid)'로 만든다 — 예치금 차감은
     입금의 한 방법이지 별개의 상태가 아니다. 그래야 미입금자·월별 정리 등
     기존 화면이 그대로 맞는 값을 보여 준다.
  ③ 잘못 차감했으면 `undo_deduct()` — spend 행을 무효로 돌리고 되돌림 행을
     더한다. 지우지 않는 이유는 "차감했다가 되돌렸다"가 사실이기 때문이다.

종류(kind):
  charge      예치(입금)                       +
  spend       그날 구매분 차감                  -
  spend_void  되돌려진 차감 (합계에서 제외)      0으로 취급하지 않고 refund와 짝
  refund      차감 되돌림                       +
  adjust      관리자 수동 조정 (사유 필수)       ±

하루 한 번만 차감된다:
  (username, settle_date) 부분 유니크 인덱스가 kind='spend'에만 걸려 있다.
  두 번 눌러도 두 번 빠지지 않고, 되돌린 뒤에는 다시 차감할 수 있다.
"""
import sqlite3
from datetime import datetime

from db_core import get_auth_db

#: 화면에 그대로 쓰는 이름 — 원장을 보는 사람이 코드를 몰라도 읽히게 한다
KIND_LABEL = {
    'charge':     '예치',
    'spend':      '구매 차감',
    'spend_void': '차감 취소됨',
    'refund':     '차감 되돌림',
    'adjust':     '관리자 조정',
}

#: 되돌려진 차감 — 금액은 원장에 그대로 남고, 짝이 되는 refund 행이 상쇄한다.
#  (−180,000 spend_void) + (+180,000 refund) = 0. 행을 빼는 게 아니라 되돌림을
#  더하는 방식이라야 "차감했다가 되돌렸다"가 내역에 보인다.
VOID_KIND = 'spend_void'


def _conn():
    return get_auth_db(row=True)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _i(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def ensure(conn=None):
    """테이블 보장. 외부 연결을 주면 그걸 쓰고 닫지 않는다."""
    _own = conn is None
    conn = conn or _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deposit_ledger (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            tx_date     TEXT NOT NULL,            -- 발생일 (입금일 · 정산일)
            kind        TEXT NOT NULL,            -- charge | spend | spend_void | refund | adjust
            amount      INTEGER NOT NULL,         -- 부호 있는 금액 (+예치 / -차감)
            settle_date TEXT DEFAULT '',          -- spend·refund가 가리키는 정산일
            memo        TEXT DEFAULT '',
            created_by  TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dl_user ON deposit_ledger(username, tx_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dl_settle ON deposit_ledger(settle_date)")
    # 같은 날 차감은 한 번만 — 버튼을 두 번 눌러도 두 번 빠지지 않는다.
    # kind='spend'에만 걸리므로 되돌린(spend_void) 뒤에는 다시 차감할 수 있다.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dl_spend_once "
                 "ON deposit_ledger(username, settle_date) WHERE kind='spend'")
    conn.commit()
    if _own:
        conn.close()


# ── 읽기 ────────────────────────────────────────────────────
def balance(username):
    """지금 잔액 — 원장 합계. 저장된 잔액은 없다."""
    conn = _conn()
    ensure(conn)
    try:
        r = conn.execute(
            "SELECT COALESCE(SUM(amount),0) b FROM deposit_ledger WHERE username=?",
            (str(username),)).fetchone()
        return _i(r['b'])
    finally:
        conn.close()


def balances():
    """{username: 잔액} — 원장에 한 줄이라도 있는 사용자만."""
    conn = _conn()
    ensure(conn)
    try:
        return {str(r['username']): _i(r['b']) for r in conn.execute(
            "SELECT username, COALESCE(SUM(amount),0) b FROM deposit_ledger "
            "GROUP BY username")}
    finally:
        conn.close()


def summary(username):
    """{balance, charged, spent, last_charge} — 화면 상단 요약용."""
    conn = _conn()
    ensure(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT kind, COALESCE(SUM(amount),0) s, MAX(tx_date) last FROM deposit_ledger "
            "WHERE username=? GROUP BY kind", (str(username),))]
        by = {str(r['kind']): r for r in rows}
        bal = sum(_i(r['s']) for r in rows)
        return {
            'balance': bal,
            'charged': _i((by.get('charge') or {}).get('s')),
            # spend는 음수로 쌓이므로 부호를 뒤집어 '쓴 돈'으로 보여 준다
            'spent': -_i((by.get('spend') or {}).get('s')),
            'last_charge': str((by.get('charge') or {}).get('last') or ''),
        }
    finally:
        conn.close()


def ledger(username=None, date_from=None, date_to=None, limit=500):
    """원장 내역 — 최신순. 되돌려진 차감(spend_void)도 그대로 보여 준다."""
    conn = _conn()
    ensure(conn)
    try:
        sql = "SELECT * FROM deposit_ledger WHERE 1=1"
        args = []
        if username:
            sql += " AND username=?"
            args.append(str(username))
        if date_from:
            sql += " AND tx_date>=?"
            args.append(str(date_from))
        if date_to:
            sql += " AND tx_date<=?"
            args.append(str(date_to))
        sql += " ORDER BY tx_date DESC, id DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def deducted(settle_date, username):
    """그날 이미 차감했는지 — 차감 행(dict) 또는 None."""
    conn = _conn()
    ensure(conn)
    try:
        r = conn.execute(
            "SELECT * FROM deposit_ledger WHERE username=? AND settle_date=? AND kind='spend'",
            (str(username), str(settle_date))).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def deducted_map(settle_date):
    """{username: 차감액} — 그 날짜에 예치금으로 결제된 사용자들."""
    conn = _conn()
    ensure(conn)
    try:
        return {str(r['username']): -_i(r['amount']) for r in conn.execute(
            "SELECT username, amount FROM deposit_ledger "
            "WHERE settle_date=? AND kind='spend'", (str(settle_date),))}
    finally:
        conn.close()


# ── 쓰기 ────────────────────────────────────────────────────
def _insert(conn, username, tx_date, kind, amount, settle_date='', memo='', by=''):
    cur = conn.execute(
        """INSERT INTO deposit_ledger
           (username, tx_date, kind, amount, settle_date, memo, created_by, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (str(username), str(tx_date), str(kind), _i(amount), str(settle_date or ''),
         str(memo or ''), str(by or ''), _now()))
    return int(cur.lastrowid)


def charge(username, amount, tx_date=None, memo='', by=''):
    """예치 — 사용자가 보낸 돈을 관리자가 확인하고 올린다.

    반환: {'ok', 'msg', 'balance'}
    """
    amt = _i(amount)
    if amt <= 0:
        return {'ok': False, 'msg': "예치 금액이 올바르지 않습니다.", 'balance': balance(username)}
    conn = _conn()
    ensure(conn)
    try:
        _insert(conn, username, tx_date or _today(), 'charge', amt, memo=memo, by=by)
        conn.commit()
    finally:
        conn.close()
    return {'ok': True, 'msg': "예치 완료", 'balance': balance(username)}


def adjust(username, amount, memo, by=''):
    """수동 조정 — 사유 없이는 못 한다.

    나중에 "왜 이 잔액이 되었나"에 답할 근거가 메모밖에 없다.
    amount는 부호 있는 값(+ 늘림 / − 줄임).
    """
    amt = _i(amount)
    note = str(memo or '').strip()
    if amt == 0:
        return {'ok': False, 'msg': "조정 금액이 0원입니다.", 'balance': balance(username)}
    if not note:
        return {'ok': False, 'msg': "조정 사유를 입력하세요.", 'balance': balance(username)}
    conn = _conn()
    ensure(conn)
    try:
        _insert(conn, username, _today(), 'adjust', amt, memo=note, by=by)
        conn.commit()
    finally:
        conn.close()
    return {'ok': True, 'msg': "조정 완료", 'balance': balance(username)}


def deduct(settle_date, username, amount, by='', memo=''):
    """그날 구매금액을 예치금에서 뺀다.

    잔액이 모자라도 막지 않는다 — 구매는 이미 일어난 일이라 원장이 그것을
    부정하면 그날 청구서가 어디에도 안 남는다. 대신 잔액이 마이너스가 되고
    화면이 "추가 예치 필요"로 크게 알린다. 부를 때 부족 여부를 미리 확인해
    사용자에게 물어보는 것은 화면 쪽 몫이다.

    같은 날 두 번 부르면 두 번 빠지지 않는다(부분 유니크 인덱스).
    반환: {'ok', 'msg', 'balance', 'short'(부족액, 0이면 충분했음)}
    """
    amt = _i(amount)
    if amt <= 0:
        return {'ok': False, 'msg': "차감할 금액이 없습니다.",
                'balance': balance(username), 'short': 0}
    conn = _conn()
    ensure(conn)
    _dup = False
    try:
        _insert(conn, username, str(settle_date), 'spend', -amt,
                settle_date=settle_date, by=by,
                memo=memo or "%s 구매분 차감" % settle_date)
        conn.commit()
    except sqlite3.IntegrityError:
        # 이미 차감된 날 — 두 번 누른 것이다. 실패가 아니라 '이미 됨'이다.
        _dup = True
    finally:
        conn.close()
    _after = balance(username)
    return {'ok': True, 'balance': _after, 'short': max(0, -_after),
            'msg': "이미 차감된 날짜입니다." if _dup else "차감 완료"}


def undo_deduct(settle_date, username, by='', memo=''):
    """차감 되돌리기 — 정산을 다시 돌려 금액이 바뀌었거나 잘못 차감했을 때.

    spend 행을 지우지 않고 spend_void로 표시한 뒤 되돌림(refund) 행을 더한다.
    "차감했다가 되돌렸다"는 실제로 일어난 일이고, 지워 버리면 사용자가 자기
    내역에서 사라진 금액을 설명할 방법이 없다.
    """
    conn = _conn()
    ensure(conn)
    try:
        r = conn.execute(
            "SELECT id, amount FROM deposit_ledger "
            "WHERE username=? AND settle_date=? AND kind='spend'",
            (str(username), str(settle_date))).fetchone()
        if r is None:
            return {'ok': False, 'msg': "예치금으로 차감된 건이 아닙니다.",
                    'balance': balance(username)}
        amt = -_i(r['amount'])            # spend는 음수로 저장돼 있다
        conn.execute("UPDATE deposit_ledger SET kind='spend_void' WHERE id=?", (int(r['id']),))
        _insert(conn, username, _today(), 'refund', amt, settle_date=settle_date, by=by,
                memo=memo or "%s 차감 취소" % settle_date)
        conn.commit()
    finally:
        conn.close()
    return {'ok': True, 'msg': "차감 취소 완료", 'balance': balance(username)}


def delete_entry(entry_id):
    """원장 한 줄 삭제 — 잘못 올린 예치·조정을 치울 때만.

    차감(spend)은 청구서의 '입금완료'와 짝이라 여기서 지우면 짝이 어긋난다.
    되돌리려면 undo_deduct를 쓴다.
    반환: (지웠는지, 사유)
    """
    conn = _conn()
    ensure(conn)
    try:
        r = conn.execute("SELECT kind FROM deposit_ledger WHERE id=?",
                         (int(entry_id),)).fetchone()
        if r is None:
            return False, "없는 내역입니다."
        if str(r['kind']) in ('spend', 'spend_void', 'refund'):
            return False, "차감 관련 내역은 여기서 지울 수 없습니다 — 정산·청구에서 '차감 취소'를 쓰세요."
        conn.execute("DELETE FROM deposit_ledger WHERE id=?", (int(entry_id),))
        conn.commit()
        return True, "삭제했습니다."
    finally:
        conn.close()
