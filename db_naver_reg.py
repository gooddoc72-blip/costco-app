"""네이버 등록 사용자별 기록·한도 (auth.db/naver_register_log).

왜 필요한가: 네이버 커머스 API 키(토큰)는 사용자별 설정(api_client_id/secret)이고,
관리자가 카페24 대행등록으로 '남의 토큰'으로 올려주는 경로까지 있다. 그런데
누가 어느 토큰으로 몇 개를 올렸는지 남는 곳이 없어 집계도, 한도도 불가능했다.
카페24 경로만 cafe24_register_log로 세고 있었다(그건 '카탈로그 사용량'이고
이건 '네이버 등록 사용량' — 목적이 달라 둘 다 필요하다).

집계 주체는 '토큰 소유자'다. 관리자가 대행등록해도 그 사용자 몫으로 센다 —
상품이 실제로 생기는 곳이 그 사용자 스토어이기 때문. 대신 누가 눌렀는지는
actor 컬럼에 남겨 대행분을 구분할 수 있게 한다.

기록 지점은 naver_api.register_product 한 곳이다. 등록 경로가 9곳(수동·자동·
대행·카트패치·상품DB)으로 흩어져 있어 호출부마다 로그를 박으면 반드시
하나가 빠지고, 새 경로가 생기면 또 빠진다. register_product는 모든 경로가
지나는 최종 지점이므로 여기서 토큰 → 사용자로 되짚는다.
"""
from datetime import datetime

from db_core import get_auth_db, get_user_db, retry_on_lock

# 등록 경로 라벨 — source 컬럼에 들어가는 값.
SOURCES = ('manual', 'auto', 'cafe24', 'backfill')
SOURCE_LABELS = {
    'manual':   '수동등록',
    'auto':     '무인자동',
    'cafe24':   '카페24 대행',
    'backfill': '과거분(소급)',
    '':         '기타',
}

LIMIT_KEY = 'naver_register_limit'   # 사용자 설정. 0/빈값 = 무제한.

_id_cache = {}                       # client_id → username 역인덱스
_id_cache_at = 0.0
_ID_CACHE_TTL = 300                  # 초. 키를 새로 넣은 사용자가 5분 내 반영된다.


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ── 등록 경로 표시 ───────────────────────────────────────────────
#   register_product는 자기가 어느 경로에서 불렸는지 모른다. 호출부가
#   product_info에 reg_source/reg_actor를 실어 보내면 그대로 기록된다.
#   (product_info는 키 단위로만 읽히므로 추가 키는 등록 payload에 영향 없다.)
#   안 실어 보낸 경로 = 사용자가 화면에서 직접 누른 등록 → 기본값 manual.

DEFAULT_SOURCE = 'manual'


# ── 테이블 ────────────────────────────────────────────────────────

def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS naver_register_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT NOT NULL DEFAULT '',
            client_tag   TEXT DEFAULT '',
            origin_no    TEXT DEFAULT '',
            product_no   TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            sale_price   INTEGER DEFAULT 0,
            source       TEXT DEFAULT '',
            actor        TEXT DEFAULT '',
            created_at   TEXT DEFAULT ''
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nvlog_user "
                 "ON naver_register_log(username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nvlog_created "
                 "ON naver_register_log(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nvlog_origin "
                 "ON naver_register_log(origin_no)")


def init_log():
    """테이블 생성 (idempotent). 모든 진입점에서 먼저 호출한다."""
    def _do():
        conn = get_auth_db()
        try:
            _ensure(conn)
            conn.commit()
        finally:
            conn.close()
    try:
        retry_on_lock(_do)
        return True
    except Exception:
        return False


# ── 기록 ──────────────────────────────────────────────────────────

def log_naver_registration(username, origin_no, *, product_no='', product_name='',
                           sale_price=0, source='', actor='', client_tag=''):
    """네이버 등록 성공 1건 기록. 한도 계산과 사용자별 집계의 근거.

    같은 origin_no 재기록은 막지 않는다(수정 재등록 등) — 집계는 DISTINCT로 센다.
    """
    if not str(origin_no or '').strip():
        return False          # 성공 증거(원상품번호)가 없으면 세지 않는다
    source = str(source or DEFAULT_SOURCE)

    def _w():
        conn = get_auth_db()
        try:
            _ensure(conn)
            conn.execute(
                "INSERT INTO naver_register_log "
                "(username, client_tag, origin_no, product_no, product_name, "
                " sale_price, source, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(username or ''), str(client_tag or '')[:16],
                 str(origin_no or ''), str(product_no or ''),
                 str(product_name or '')[:120], int(sale_price or 0),
                 str(source or ''), str(actor or ''), _now()))
            conn.commit()
        finally:
            conn.close()
    try:
        retry_on_lock(_w)
        return True
    except Exception:
        return False


# ── 토큰 → 사용자 ─────────────────────────────────────────────────

def _build_id_index():
    """모든 사용자 설정을 훑어 api_client_id → username 역인덱스를 만든다.

    설정은 사용자별 DB(get_user_db)에 흩어져 있어 한 번에 조회할 수 없다.
    등록 1건은 네트워크 왕복 수 초라 이 스캔은 TTL 캐시로 충분히 싸다.
    """
    idx = {}
    try:
        from db_auth import get_all_users
        users = get_all_users()
    except Exception:
        return idx
    for u in users:
        un = str(u.get('username') or '').strip()
        if not un:
            continue
        try:
            conn = get_user_db(un)
            row = conn.execute(
                "SELECT value FROM settings WHERE key='api_client_id'").fetchone()
            conn.close()
        except Exception:
            continue
        cid = str((row['value'] if row else '') or '').strip()
        if cid:
            idx[cid] = un
    return idx


def resolve_user_by_client_id(client_id, *, refresh=False):
    """커머스 API client_id의 주인 username. 못 찾으면 ''."""
    global _id_cache, _id_cache_at
    cid = str(client_id or '').strip()
    if not cid:
        return ''
    import time as _t
    if refresh or not _id_cache or (_t.time() - _id_cache_at) > _ID_CACHE_TTL:
        _id_cache = _build_id_index()
        _id_cache_at = _t.time()
    hit = _id_cache.get(cid, '')
    if not hit and not refresh:
        # 방금 키를 넣은 사용자 — 캐시가 오래된 경우이므로 한 번만 강제 갱신.
        return resolve_user_by_client_id(cid, refresh=True)
    return hit


def client_tag(client_id):
    """감사용 토큰 식별 태그 (앞 10자). 사용자 매칭 실패분도 추적 가능하게."""
    return str(client_id or '')[:10]


def log_by_client_id(client_id, origin_no, product_info=None):
    """register_product가 부르는 훅 — 토큰으로 주인을 찾아 1건 기록."""
    info = product_info or {}
    return log_naver_registration(
        resolve_user_by_client_id(client_id), origin_no,
        product_no=str(info.get('seller_code') or ''),
        product_name=str(info.get('name') or ''),
        sale_price=int(info.get('sale_price') or 0),
        source=str(info.get('reg_source') or DEFAULT_SOURCE),
        actor=str(info.get('reg_actor') or ''),
        client_tag=client_tag(client_id))


# ── 한도 ──────────────────────────────────────────────────────────

def get_naver_reg_count(username):
    """누적 등록 수 — 같은 상품 재등록은 1로 본다(DISTINCT origin_no)."""
    if not str(username or '').strip():
        return 0
    conn = get_auth_db()
    try:
        _ensure(conn)
        row = conn.execute(
            "SELECT COUNT(DISTINCT origin_no) FROM naver_register_log "
            " WHERE username=? AND TRIM(COALESCE(origin_no,''))<>''",
            (str(username),)).fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        conn.close()


def get_naver_reg_limit(username):
    """사용자별 누적 한도. 0이면 무제한. 미설정도 무제한."""
    try:
        from db_products import get_setting
        v = str(get_setting(username, LIMIT_KEY) or '').strip()
    except Exception:
        v = ''
    if not v:
        return 0
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return 0


def set_naver_reg_limit(username, limit):
    from db_products import set_setting
    set_setting(username, LIMIT_KEY, str(max(0, int(limit or 0))))


def naver_reg_quota(username):
    """{limit, used, remaining, blocked} — 한도 확인 1회로 끝내는 헬퍼.
    limit=0(무제한)이면 blocked는 항상 False, remaining은 None."""
    lim = get_naver_reg_limit(username)
    used = get_naver_reg_count(username)
    if lim <= 0:
        return {'limit': 0, 'used': used, 'remaining': None, 'blocked': False}
    rem = max(0, lim - used)
    return {'limit': lim, 'used': used, 'remaining': rem, 'blocked': rem <= 0}


def block_reason_by_client_id(client_id):
    """등록 전 차단 사유 문자열, 통과면 None.

    토큰 주인을 못 찾으면 차단하지 않는다 — 기록이 목적이고, 매칭 실패로
    정상 등록을 막으면 손해가 더 크다(미매칭분은 client_tag로 남는다).
    """
    un = resolve_user_by_client_id(client_id)
    if not un:
        return None
    q = naver_reg_quota(un)
    if q['blocked']:
        return (f"네이버 등록 한도 초과 — '{un}' 누적 {q['used']}/{q['limit']}건. "
                "관리자에게 한도 상향을 요청하세요.")
    return None


# ── 집계·조회 ─────────────────────────────────────────────────────

def naver_reg_summary(date_from='', date_to=''):
    """사용자별 등록 집계. 관리자 화면용.

    반환: [{username, display_name, total, period, by_source{}, agency,
            last_at, limit, remaining, blocked}] — total 내림차순.
    total은 누적(전체 기간), period는 기간 필터가 있을 때 그 구간 건수.
    agency는 관리자 대행(actor가 본인과 다른) 건수.

    모든 수치는 '상품 기준'(origin_no 중복 제거)이다. 수정 재등록으로 같은
    상품이 두 번 남아도 1로 세야 경로별 합이 누적과 맞는다. 경로는 처음
    등록된 경로로 귀속한다(재등록 경로로 바뀌면 합이 흔들린다).
    """
    conn = get_auth_db()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT username, source, actor, origin_no, created_at "
            "  FROM naver_register_log "
            " WHERE TRIM(COALESCE(origin_no,''))<>'' ORDER BY id").fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    agg = {}
    for un, src, actor, origin, created in rows:
        un = str(un or '')
        a = agg.setdefault(un, {'username': un, 'seen': set(), 'period_seen': set(),
                                'by_source': {}, 'agency': 0, 'last_at': ''})
        _first = origin not in a['seen']
        a['seen'].add(origin)
        if _first:
            src = str(src or '')
            a['by_source'][src] = a['by_source'].get(src, 0) + 1
            if str(actor or '') and str(actor) != un:
                a['agency'] += 1
        if str(created or '') > a['last_at']:
            a['last_at'] = str(created or '')
        if date_from and str(created or '') < date_from:
            continue
        if date_to and str(created or '') > (date_to + ' 23:59'):
            continue
        a['period_seen'].add(origin)

    try:
        from db_auth import get_all_users
        names = {u['username']: (u.get('display_name') or '') for u in get_all_users()}
    except Exception:
        names = {}

    out = []
    for un, a in agg.items():
        q = naver_reg_quota(un) if un else {'limit': 0, 'remaining': None,
                                           'blocked': False}
        out.append({
            'username': un or '(미매칭 토큰)',
            'display_name': names.get(un, ''),
            'total': len(a['seen']),
            'period': len(a['period_seen']),
            'by_source': a['by_source'],
            'agency': a['agency'],
            'last_at': a['last_at'],
            'limit': q['limit'],
            'remaining': q['remaining'],
            'blocked': q['blocked'],
        })
    out.sort(key=lambda d: -d['total'])
    return out


def list_naver_registrations(username='', *, source='', limit=300):
    """최근 등록 로그. username/source 빈 값이면 전체."""
    conn = get_auth_db()
    try:
        _ensure(conn)
        sql = ("SELECT created_at, username, product_name, origin_no, product_no, "
               "       sale_price, source, actor, client_tag "
               "  FROM naver_register_log WHERE 1=1")
        args = []
        if str(username or '').strip():
            sql += " AND username=?"
            args.append(str(username))
        if str(source or '').strip():
            sql += " AND source=?"
            args.append(str(source))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit or 300))
        rows = conn.execute(sql, tuple(args)).fetchall()
        keys = ('created_at', 'username', 'product_name', 'origin_no', 'product_no',
                'sale_price', 'source', 'actor', 'client_tag')
        return [dict(zip(keys, r)) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── 과거분 소급 ───────────────────────────────────────────────────

def backfill_user(username):
    """이 기능 이전에 등록된 건을 사용자 products 테이블에서 소급 기록.

    로그가 비어 있으면 한도가 실제 사용량을 반영하지 못한다(이미 수천 개
    올린 사용자가 0으로 잡힌다). products.naver_origin_pno가 그 사용자
    스토어에 상품이 있다는 증거이므로 이를 source='backfill'로 채운다.
    이미 로그에 있는 origin_no는 건너뛴다(중복 소급 방지).

    반환: 새로 넣은 건수.
    """
    un = str(username or '').strip()
    if not un:
        return 0
    try:
        uc = get_user_db(un)
        rows = uc.execute(
            "SELECT naver_origin_pno, store_product_name, costco_name, product_no, "
            "       sale_price, updated_at FROM products "
            " WHERE TRIM(COALESCE(naver_origin_pno,''))<>''").fetchall()
        uc.close()
    except Exception:
        return 0
    if not rows:
        return 0

    conn = get_auth_db()
    try:
        _ensure(conn)
        have = {r[0] for r in conn.execute(
            "SELECT origin_no FROM naver_register_log WHERE username=?", (un,))}
        ins = []
        for r in rows:
            origin = str(r['naver_origin_pno'] or '').strip()
            if not origin or origin in have:
                continue
            have.add(origin)
            ins.append((un, '', origin, str(r['product_no'] or ''),
                        str(r['store_product_name'] or r['costco_name'] or '')[:120],
                        int(r['sale_price'] or 0), 'backfill', '',
                        str(r['updated_at'] or '')[:16] or _now()))
        if ins:
            conn.executemany(
                "INSERT INTO naver_register_log "
                "(username, client_tag, origin_no, product_no, product_name, "
                " sale_price, source, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ins)
            conn.commit()
        return len(ins)
    except Exception:
        return 0
    finally:
        conn.close()


def backfill_all():
    """전 사용자 소급. 반환: {username: 넣은 건수}."""
    try:
        from db_auth import get_all_users
        users = get_all_users()
    except Exception:
        return {}
    out = {}
    for u in users:
        un = str(u.get('username') or '').strip()
        if not un:
            continue
        n = backfill_user(un)
        if n:
            out[un] = n
    return out
