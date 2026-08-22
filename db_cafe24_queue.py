"""카페24 → 네이버 대행등록 대기열 (auth.db/cafe24_reg_queue).

300건 규모 배치는 한 번에 못 돌린다. 크론이 회당 N건씩 나눠 소화하려면
'어디까지 했는지'가 어딘가 남아야 하는데, Streamlit 세션 메모리는 탭을 닫으면
사라진다. 그래서 공용 auth.db에 대기열을 둔다.

상태: pending(대기) → done(등록) / skipped(이미 등록됨) / failed(실패).
실패는 자동 재시도하지 않는다 — '카테고리 판단실패'처럼 재시도해도 그대로인
건이 대부분이라 조용히 반복하면 AI 비용만 샌다. UI에서 명시적으로 되돌린다.
"""
import sqlite3
from datetime import datetime

from db_core import get_auth_db, retry_on_lock

STATUSES = ('pending', 'done', 'skipped', 'failed')


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def init_queue():
    """대기열 테이블 생성 (idempotent). 모든 진입점에서 먼저 호출한다."""
    conn = get_auth_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cafe24_reg_queue (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                target_user  TEXT NOT NULL,
                product_no   TEXT NOT NULL,
                product_name TEXT DEFAULT '',
                price        INTEGER DEFAULT 0,
                status       TEXT DEFAULT 'pending',
                detail       TEXT DEFAULT '',
                attempts     INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT '',
                updated_at   TEXT DEFAULT '',
                UNIQUE(target_user, product_no)
            )""")
        # 크론이 매번 '이 사용자의 pending'을 뽑으므로 복합 인덱스.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_c24q_user_status "
                     "ON cafe24_reg_queue(target_user, status)")
        conn.commit()
    finally:
        conn.close()


def enqueue(target_user, products):
    """상품 목록을 대기열에 추가. 반환: (추가된 수, 이미 있어서 건너뛴 수).

    products: [{'product_no', 'product_name', 'price'}, ...]
    같은 (대상사용자, 카페24상품번호)가 이미 있으면 상태와 무관하게 건드리지 않는다
    (done을 pending으로 되돌려 중복 등록하는 사고를 막는다)."""
    init_queue()
    rows = [(str(target_user), str(p.get('product_no')),
             str(p.get('product_name') or ''), int(p.get('price') or 0),
             'pending', '', 0, _now(), _now())
            for p in (products or []) if p.get('product_no')]
    if not rows:
        return 0, 0

    def _do():
        conn = get_auth_db()
        try:
            _before = conn.execute(
                "SELECT COUNT(*) FROM cafe24_reg_queue WHERE target_user=?",
                (str(target_user),)).fetchone()[0]
            conn.executemany(
                "INSERT OR IGNORE INTO cafe24_reg_queue "
                "(target_user, product_no, product_name, price, status, detail, "
                " attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            _after = conn.execute(
                "SELECT COUNT(*) FROM cafe24_reg_queue WHERE target_user=?",
                (str(target_user),)).fetchone()[0]
            return _after - _before, len(rows) - (_after - _before)
        finally:
            conn.close()

    return retry_on_lock(_do)


def next_pending(target_user, limit=30):
    """대기 중인 상품을 오래된 순으로 limit건 꺼낸다. 반환: [dict]."""
    init_queue()
    conn = get_auth_db(row=True)
    try:
        rows = conn.execute(
            "SELECT * FROM cafe24_reg_queue WHERE target_user=? AND status='pending' "
            "ORDER BY id LIMIT ?", (str(target_user), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark(qid, status, detail=''):
    """처리 결과 기록. status는 STATUSES 중 하나."""
    if status not in STATUSES:
        raise ValueError("알 수 없는 상태: %s" % status)

    def _do():
        conn = get_auth_db()
        try:
            conn.execute(
                "UPDATE cafe24_reg_queue SET status=?, detail=?, "
                "attempts=attempts+1, updated_at=? WHERE id=?",
                (status, str(detail or '')[:400], _now(), int(qid)))
            conn.commit()
        finally:
            conn.close()

    return retry_on_lock(_do)


def counts(target_user):
    """상태별 건수. 반환: {'pending':n, 'done':n, 'skipped':n, 'failed':n, 'total':n}."""
    init_queue()
    conn = get_auth_db()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM cafe24_reg_queue WHERE target_user=? "
            "GROUP BY status", (str(target_user),)).fetchall()
    finally:
        conn.close()
    out = dict((s, 0) for s in STATUSES)
    for _s, _n in rows:
        out[_s] = _n
    out['total'] = sum(out[s] for s in STATUSES)
    return out


def all_counts():
    """대상 사용자별 대기 건수 — 크론이 '일감 있는 사용자'를 찾을 때 쓴다.
    반환: [(target_user, pending_수)] (pending>0만, 많은 순)."""
    init_queue()
    conn = get_auth_db()
    try:
        rows = conn.execute(
            "SELECT target_user, COUNT(*) c FROM cafe24_reg_queue "
            "WHERE status='pending' GROUP BY target_user ORDER BY c DESC").fetchall()
        return [(r[0], r[1]) for r in rows]
    finally:
        conn.close()


def list_rows(target_user, status=None, limit=500):
    """대기열 조회 (UI 표시용). status=None이면 전체."""
    init_queue()
    conn = get_auth_db(row=True)
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM cafe24_reg_queue WHERE target_user=? AND status=? "
                "ORDER BY id LIMIT ?",
                (str(target_user), status, int(limit))).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cafe24_reg_queue WHERE target_user=? "
                "ORDER BY id LIMIT ?", (str(target_user), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def requeue_failed(target_user):
    """실패 건을 다시 대기 상태로. 반환: 되돌린 건수.
    원인을 고친 뒤(예: 카테고리 매핑 추가) 사람이 눌러서 재시도하는 용도."""
    def _do():
        conn = get_auth_db()
        try:
            cur = conn.execute(
                "UPDATE cafe24_reg_queue SET status='pending', detail='', updated_at=? "
                "WHERE target_user=? AND status='failed'", (_now(), str(target_user)))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    init_queue()
    return retry_on_lock(_do)


def clear(target_user, status=None):
    """대기열 삭제. status=None이면 그 사용자 전체. 반환: 삭제 건수."""
    def _do():
        conn = get_auth_db()
        try:
            if status:
                cur = conn.execute(
                    "DELETE FROM cafe24_reg_queue WHERE target_user=? AND status=?",
                    (str(target_user), status))
            else:
                cur = conn.execute(
                    "DELETE FROM cafe24_reg_queue WHERE target_user=?",
                    (str(target_user),))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    init_queue()
    return retry_on_lock(_do)
