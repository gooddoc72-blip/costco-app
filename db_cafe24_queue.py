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
                reason       TEXT DEFAULT '',
                attempts     INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT '',
                updated_at   TEXT DEFAULT '',
                UNIQUE(target_user, product_no)
            )""")
        # 크론이 매번 '이 사용자의 pending'을 뽑으므로 복합 인덱스.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_c24q_user_status "
                     "ON cafe24_reg_queue(target_user, status)")
        # 이미 만들어진 테이블에 reason이 없으면 추가(구버전에서 올라온 경우)
        _cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(cafe24_reg_queue)").fetchall()}
        if 'reason' not in _cols:
            conn.execute("ALTER TABLE cafe24_reg_queue ADD COLUMN reason TEXT DEFAULT ''")
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
             'pending', '', '', 0, _now(), _now())
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
                " reason, attempts, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
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


def mark(qid, status, detail='', reason=''):
    """처리 결과 기록. status는 STATUSES 중 하나.
    reason은 실패 사유 코드(cafe24_register_service.FAIL_REASONS) — 실패에만 의미가 있다."""
    if status not in STATUSES:
        raise ValueError("알 수 없는 상태: %s" % status)
    _rs = str(reason or '')[:32] if status == 'failed' else ''

    def _do():
        conn = get_auth_db()
        try:
            conn.execute(
                "UPDATE cafe24_reg_queue SET status=?, detail=?, reason=?, "
                "attempts=attempts+1, updated_at=? WHERE id=?",
                (status, str(detail or '')[:400], _rs, _now(), int(qid)))
            conn.commit()
        finally:
            conn.close()

    return retry_on_lock(_do)


def fail_reason_counts(target_user):
    """실패 건을 사유 코드별로 집계. 반환: [(reason_code, 건수)] 많은 순.

    사유가 비어 있는 과거 기록은 문구로 분류해 채운다(구버전 호환)."""
    init_queue()
    conn = get_auth_db()
    try:
        rows = conn.execute(
            "SELECT reason, detail FROM cafe24_reg_queue "
            "WHERE target_user=? AND status='failed'", (str(target_user),)).fetchall()
    finally:
        conn.close()
    try:
        from cafe24_register_service import classify_failure
    except Exception:
        classify_failure = lambda d: 'UNKNOWN'
    agg = {}
    for _rs, _dt in rows:
        _code = str(_rs or '').strip() or classify_failure(_dt)
        agg[_code] = agg.get(_code, 0) + 1
    return sorted(agg.items(), key=lambda kv: -kv[1])


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


def all_queue_users():
    """대기열이 하나라도 있는 전 계정의 상태별 집계.
    반환: [{'user', 'pending', 'done', 'skipped', 'failed', 'total'}] 총건수 많은 순.

    all_counts()는 pending>0만 돌려주므로 '처리가 끝나 pending이 0인 계정'이
    목록에서 사라진다. 그런 계정의 기록도 지울 수 있어야 해서 따로 둔다."""
    init_queue()
    conn = get_auth_db()
    try:
        rows = conn.execute(
            "SELECT target_user, status, COUNT(*) FROM cafe24_reg_queue "
            "GROUP BY target_user, status").fetchall()
    finally:
        conn.close()
    agg = {}
    for _u, _st, _n in rows:
        _e = agg.setdefault(_u, dict((k, 0) for k in STATUSES))
        if _st in _e:
            _e[_st] = _n
    out = []
    for _u, _e in agg.items():
        _e = dict(_e)
        _e['user'] = _u
        _e['total'] = sum(_e[k] for k in STATUSES)
        out.append(_e)
    out.sort(key=lambda r: -r['total'])
    return out


def list_rows(target_user, status=None, limit=500, reason=None):
    """대기열 조회 (UI 표시용). status=None이면 전체. reason으로 실패 사유 필터."""
    init_queue()
    conn = get_auth_db(row=True)
    try:
        if status and reason:
            rows = conn.execute(
                "SELECT * FROM cafe24_reg_queue WHERE target_user=? AND status=? "
                "AND reason=? ORDER BY id LIMIT ?",
                (str(target_user), status, str(reason), int(limit))).fetchall()
        elif status:
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


def requeue_failed(target_user, reason=None):
    """실패 건을 다시 대기 상태로. reason을 주면 그 사유만. 반환: 되돌린 건수.
    원인을 고친 뒤(예: 상품명 수정, 카페24 이미지 등록) 사람이 눌러 재시도하는 용도."""
    def _do():
        conn = get_auth_db()
        try:
            if reason:
                cur = conn.execute(
                    "UPDATE cafe24_reg_queue SET status='pending', detail='', reason='', "
                    "updated_at=? WHERE target_user=? AND status='failed' AND reason=?",
                    (_now(), str(target_user), str(reason)))
            else:
                cur = conn.execute(
                    "UPDATE cafe24_reg_queue SET status='pending', detail='', reason='', "
                    "updated_at=? WHERE target_user=? AND status='failed'",
                    (_now(), str(target_user)))
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


# ── 카페24 등록 한도 (사용자별 누적) ───────────────────────────────
#   관리자가 카페24 카탈로그를 열어준 사용자가 무한정 등록하는 걸 막는다.
#   한도는 사용자 설정 'cafe24_register_limit'(0/빈값 = 무제한),
#   사용량은 '이 앱의 카페24 경로로 등록에 성공한 상품 수'(누적)다.

def _ensure_reg_log(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cafe24_register_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT NOT NULL,
            product_no   TEXT DEFAULT '',
            origin_no    TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            source       TEXT DEFAULT '',
            created_at   TEXT DEFAULT ''
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cf24log_user "
                 "ON cafe24_register_log(username)")


def log_cafe24_registration(username, product_no, origin_no='', product_name='', source=''):
    """카페24 경로 등록 성공 1건 기록. 한도 계산의 근거."""
    def _w():
        conn = get_auth_db()
        try:
            _ensure_reg_log(conn)
            conn.execute(
                "INSERT INTO cafe24_register_log "
                "(username, product_no, origin_no, product_name, source, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (str(username or ''), str(product_no or ''), str(origin_no or ''),
                 str(product_name or '')[:120], str(source or ''), _now()))
            conn.commit()
        finally:
            conn.close()
    try:
        retry_on_lock(_w)
        return True
    except Exception:
        return False


def get_cafe24_reg_count(username):
    """누적 등록 수 — 대기열 완료분(done)과 직접등록 로그를 상품번호로 합집합.

    대기열을 쓰지 않는 '건별 등록'도 세야 하고, 반대로 이 기능이 생기기 전에
    대기열로 이미 등록한 것도 빠지면 안 된다. 그래서 둘을 합쳐 중복을 뺀다.
    """
    conn = get_auth_db()
    try:
        _ensure_reg_log(conn)
        init_queue()
        row = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT product_no FROM cafe24_reg_queue "
            "   WHERE target_user=? AND status='done' AND TRIM(COALESCE(product_no,''))<>''"
            "  UNION "
            "  SELECT product_no FROM cafe24_register_log "
            "   WHERE username=? AND TRIM(COALESCE(product_no,''))<>''"
            ")", (str(username), str(username))).fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        conn.close()


def get_cafe24_reg_limit(username):
    """사용자별 누적 한도. 0이면 무제한. 미설정도 무제한."""
    try:
        from db import get_setting
        v = str(get_setting(username, 'cafe24_register_limit') or '').strip()
    except Exception:
        v = ''
    if not v:
        return 0
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return 0


def cafe24_reg_quota(username):
    """{limit, used, remaining, blocked} — 한도 확인 1회로 끝내는 헬퍼.
    limit=0(무제한)이면 blocked는 항상 False, remaining은 None."""
    _lim = get_cafe24_reg_limit(username)
    _used = get_cafe24_reg_count(username)
    if _lim <= 0:
        return {'limit': 0, 'used': _used, 'remaining': None, 'blocked': False}
    _rem = max(0, _lim - _used)
    return {'limit': _lim, 'used': _used, 'remaining': _rem, 'blocked': _rem <= 0}
