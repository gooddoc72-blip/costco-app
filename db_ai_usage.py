"""AI 사용량 원장 — 관리자 공용키로 나간 호출을 사용자별로 기록·청구·제한.

왜 필요한가:
  get_ai_keys()는 **관리자 전역키를 항상 우선**한다(AI 키는 공유 인프라라는 설계).
  그래서 전 사용자의 Claude·Gemini 호출이 관리자 계정으로 과금되는데, 누가
  얼마를 썼는지 남는 곳이 없었다. 영수증 한 장 판독이 Sonnet vision 호출이라
  한 사용자가 대량으로 돌리면 청구서는 관리자에게만 온다.

기록 지점:
  ai_service의 claude_complete / claude_vision / _gemini_post 세 곳. 12개 모듈의
  모든 AI 호출이 이 셋 중 하나를 지난다. 호출부마다 붙이면 반드시 하나가 빠진다.

사용자 귀속이 네이버 등록과 다르다:
  네이버는 토큰이 사용자별이라 키로 주인을 되짚을 수 있었다. AI 키는 전 사용자
  공용 1개라 키로는 누구인지 알 수 없다. 그래서 호출 주체를 ai_service가
  들고 있고(app.py가 로그인 사용자로 1회 세팅, 크론은 태스크별 username),
  이 모듈은 그 값을 받아 적기만 한다.

금액을 기록 시점에 굳히는 이유:
  단가·환율은 바뀐다. cost_krw를 저장하지 않고 조회할 때마다 다시 계산하면
  이미 청구한 지난달 금액이 환율 따라 흔들린다. 청구서는 흔들리면 안 된다.
"""
import json
from datetime import datetime

from db_core import get_auth_db, retry_on_lock

# ── 단가 (USD / 100만 토큰) ───────────────────────────────────────
#   Anthropic 공식 가격표(2026-06 기준). 값이 바뀌면 관리자 화면에서 고친다
#   — 코드 수정 없이 반영되도록 전역 설정('ai_model_prices')이 항상 우선한다.
#   Gemini는 기본값을 0으로 둔다. 모르는 값을 넣으면 틀린 금액으로 청구하게 되고,
#   그건 집계가 없느니만 못하다. 관리자가 구글 가격표를 보고 채워야 금액이 잡힌다
#   (0이어도 토큰 수는 정확히 쌓이므로 나중에 소급 계산할 수 있다).
DEFAULT_PRICES = {
    'claude-haiku-4-5':  {'in': 1.00, 'out': 5.00},
    'claude-sonnet-5':   {'in': 2.00, 'out': 10.00},
    'claude-opus-5':     {'in': 5.00, 'out': 25.00},
    'gemini-flash':      {'in': 0.0,  'out': 0.0},
    'gemini-pro':        {'in': 0.0,  'out': 0.0},
}
# 캐시 토큰 배율 — 읽기는 입력가의 0.1배, 쓰기는 1.25배.
CACHE_READ_MULT = 0.10
CACHE_WRITE_MULT = 1.25

PRICES_KEY = 'ai_model_prices'      # 전역 설정(JSON). 관리자가 단가를 고친다.
FX_KEY = 'ai_usd_krw'               # 전역 설정. USD→KRW 환율.
MARGIN_KEY = 'ai_bill_margin_pct'   # 전역 설정. 청구 시 얹는 마진 %(기본 0=실비).
LIMIT_KEY = 'ai_monthly_limit_krw'  # 사용자 설정. 월 한도(원). 0/빈값=무제한.

DEFAULT_FX = 1400                   # 환율 미설정 시 임시값 — 관리자가 반드시 확인할 것

FEATURE_LABELS = {
    'receipt':  '영수증 판독',
    'photo':    '상품사진 분석',
    'label':    '식품라벨 판독',
    'pricetag': '가격표 판독',
    'desc':     '상세설명 생성',
    'name':     '상품명 최적화',
    'category': '카테고리 판단',
    'brief':    '정산 브리핑',
    'ask':      '화면 질문',
    '':         '기타',
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ym(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m")


# ── 단가·환율 설정 ────────────────────────────────────────────────

def get_prices():
    """{model_key: {'in': usd, 'out': usd}} — 전역 설정이 기본값을 덮어쓴다."""
    out = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    try:
        from db_auth import get_global_setting
        raw = str(get_global_setting(PRICES_KEY) or '').strip()
        if raw:
            for k, v in (json.loads(raw) or {}).items():
                if isinstance(v, dict):
                    out[str(k)] = {'in': float(v.get('in') or 0),
                                   'out': float(v.get('out') or 0)}
    except Exception:
        pass
    return out


def set_prices(prices):
    from db_auth import set_global_setting
    set_global_setting(PRICES_KEY, json.dumps(prices, ensure_ascii=False))


def get_usd_krw():
    try:
        from db_auth import get_global_setting
        v = str(get_global_setting(FX_KEY) or '').strip()
        return float(v) if v else float(DEFAULT_FX)
    except Exception:
        return float(DEFAULT_FX)


def set_usd_krw(v):
    from db_auth import set_global_setting
    set_global_setting(FX_KEY, str(float(v or 0) or DEFAULT_FX))


def get_margin_pct():
    try:
        from db_auth import get_global_setting
        v = str(get_global_setting(MARGIN_KEY) or '').strip()
        return max(0.0, float(v)) if v else 0.0
    except Exception:
        return 0.0


def set_margin_pct(v):
    from db_auth import set_global_setting
    set_global_setting(MARGIN_KEY, str(max(0.0, float(v or 0))))


def price_for(model, prices=None):
    """모델명 → 단가. 날짜 붙은 이름('claude-haiku-4-5-20251001')도 찾는다.

    가장 긴 접두사 일치를 쓴다 — 'claude-haiku-4-5'와 'claude-haiku-4-5-2025…'가
    둘 다 등록돼 있으면 구체적인 쪽이 이겨야 한다.
    """
    prices = prices if prices is not None else get_prices()
    m = str(model or '').strip()
    if m in prices:
        return prices[m]
    best = None
    for k in prices:
        if m.startswith(k) and (best is None or len(k) > len(best)):
            best = k
    return prices.get(best) if best else None


def cost_krw(model, in_tokens, out_tokens, cache_read=0, cache_write=0,
             *, prices=None, fx=None, margin_pct=None):
    """실비(원). 단가 미등록 모델은 0 — 토큰은 남으므로 나중에 소급 계산 가능."""
    p = price_for(model, prices)
    if not p:
        return 0
    fx = get_usd_krw() if fx is None else fx
    margin = get_margin_pct() if margin_pct is None else margin_pct
    usd = ((int(in_tokens or 0) * float(p['in'])
            + int(out_tokens or 0) * float(p['out'])
            + int(cache_read or 0) * float(p['in']) * CACHE_READ_MULT
            + int(cache_write or 0) * float(p['in']) * CACHE_WRITE_MULT)
           / 1_000_000.0)
    return int(round(usd * fx * (1.0 + margin / 100.0)))


# ── 테이블 ────────────────────────────────────────────────────────

def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT NOT NULL DEFAULT '',
            ym           TEXT DEFAULT '',
            provider     TEXT DEFAULT '',
            model        TEXT DEFAULT '',
            feature      TEXT DEFAULT '',
            in_tokens    INTEGER DEFAULT 0,
            out_tokens   INTEGER DEFAULT 0,
            cache_read   INTEGER DEFAULT 0,
            cache_write  INTEGER DEFAULT 0,
            cost_krw     INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT ''
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aiu_user_ym "
                 "ON ai_usage_log(username, ym)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aiu_ym ON ai_usage_log(ym)")


def init_log():
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

def log_usage(username, provider, model, *, feature='', in_tokens=0, out_tokens=0,
              cache_read=0, cache_write=0):
    """AI 호출 1건 기록. 금액은 지금 단가·환율로 굳힌다(청구 근거).

    토큰이 모두 0이면 적지 않는다 — 실패한 호출은 과금되지 않는다.
    """
    if not (int(in_tokens or 0) or int(out_tokens or 0)
            or int(cache_read or 0) or int(cache_write or 0)):
        return False
    krw = cost_krw(model, in_tokens, out_tokens, cache_read, cache_write)

    def _w():
        conn = get_auth_db()
        try:
            _ensure(conn)
            conn.execute(
                "INSERT INTO ai_usage_log "
                "(username, ym, provider, model, feature, in_tokens, out_tokens, "
                " cache_read, cache_write, cost_krw, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (str(username or ''), _ym(), str(provider or ''), str(model or ''),
                 str(feature or ''), int(in_tokens or 0), int(out_tokens or 0),
                 int(cache_read or 0), int(cache_write or 0), krw, _now()))
            conn.commit()
        finally:
            conn.close()
    try:
        retry_on_lock(_w)
        return True
    except Exception:
        return False


# ── 한도 ──────────────────────────────────────────────────────────

def month_cost(username, ym=''):
    """그 달 누적 실비(원)."""
    if not str(username or '').strip():
        return 0
    conn = get_auth_db()
    try:
        _ensure(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_krw),0) FROM ai_usage_log "
            " WHERE username=? AND ym=?", (str(username), ym or _ym())).fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        conn.close()


def get_limit(username):
    """사용자 월 한도(원). 0이면 무제한."""
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


def set_limit(username, krw):
    from db_products import set_setting
    set_setting(username, LIMIT_KEY, str(max(0, int(krw or 0))))


def ai_quota(username, ym=''):
    """{limit, used, remaining, blocked} — 이번 달 기준."""
    lim = get_limit(username)
    used = month_cost(username, ym)
    if lim <= 0:
        return {'limit': 0, 'used': used, 'remaining': None, 'blocked': False}
    rem = max(0, lim - used)
    return {'limit': lim, 'used': used, 'remaining': rem, 'blocked': rem <= 0}


def block_reason(username):
    """한도 초과 시 사유 문자열, 통과면 None.

    사용자를 모르면(크론 초기화 전 등) 막지 않는다 — 기록이 목적이고,
    귀속 실패로 정상 기능을 막으면 손해가 더 크다.
    """
    un = str(username or '').strip()
    if not un:
        return None
    q = ai_quota(un)
    if q['blocked']:
        return (f"AI 사용 한도 초과 — 이번 달 {q['used']:,}원 / 한도 {q['limit']:,}원. "
                "관리자에게 한도 상향을 요청하세요.")
    return None


# ── 집계·조회 ─────────────────────────────────────────────────────

def usage_summary(ym=''):
    """사용자별 그 달 집계. [{username, display_name, cost, calls, in_tokens,
    out_tokens, by_provider{}, by_feature{}, limit, remaining, blocked}]
    — 비용 내림차순."""
    ym = ym or _ym()
    conn = get_auth_db()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT username, provider, feature, in_tokens, out_tokens, cost_krw "
            "  FROM ai_usage_log WHERE ym=?", (ym,)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    agg = {}
    for un, prov, feat, itok, otok, krw in rows:
        un = str(un or '')
        a = agg.setdefault(un, {'username': un, 'cost': 0, 'calls': 0,
                                'in_tokens': 0, 'out_tokens': 0,
                                'by_provider': {}, 'by_feature': {}})
        a['cost'] += int(krw or 0)
        a['calls'] += 1
        a['in_tokens'] += int(itok or 0)
        a['out_tokens'] += int(otok or 0)
        prov = str(prov or '')
        feat = str(feat or '')
        a['by_provider'][prov] = a['by_provider'].get(prov, 0) + int(krw or 0)
        a['by_feature'][feat] = a['by_feature'].get(feat, 0) + int(krw or 0)

    try:
        from db_auth import get_all_users
        names = {u['username']: (u.get('display_name') or '') for u in get_all_users()}
    except Exception:
        names = {}

    out = []
    for un, a in agg.items():
        q = ai_quota(un, ym) if un else {'limit': 0, 'remaining': None,
                                         'blocked': False}
        a.update({'display_name': names.get(un, ''),
                  'username': un or '(미귀속)',
                  'limit': q['limit'], 'remaining': q['remaining'],
                  'blocked': q['blocked']})
        out.append(a)
    out.sort(key=lambda d: -d['cost'])
    return out


def month_totals(ym=''):
    """{cost, calls, users} — 그 달 전체. 관리자 실제 지출과 대조용."""
    ym = ym or _ym()
    conn = get_auth_db()
    try:
        _ensure(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_krw),0), COUNT(*), COUNT(DISTINCT username) "
            "  FROM ai_usage_log WHERE ym=?", (ym,)).fetchone()
        return {'cost': int(row[0] or 0), 'calls': int(row[1] or 0),
                'users': int(row[2] or 0)}
    except Exception:
        return {'cost': 0, 'calls': 0, 'users': 0}
    finally:
        conn.close()


def list_usage(username='', ym='', *, provider='', feature='', limit=300):
    """최근 호출 로그. 빈 값은 전체."""
    conn = get_auth_db()
    try:
        _ensure(conn)
        sql = ("SELECT created_at, username, provider, model, feature, "
               "       in_tokens, out_tokens, cost_krw "
               "  FROM ai_usage_log WHERE 1=1")
        args = []
        for _col, _val in (('username', username), ('ym', ym),
                           ('provider', provider), ('feature', feature)):
            if str(_val or '').strip():
                sql += f" AND {_col}=?"
                args.append(str(_val))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit or 300))
        rows = conn.execute(sql, tuple(args)).fetchall()
        keys = ('created_at', 'username', 'provider', 'model', 'feature',
                'in_tokens', 'out_tokens', 'cost_krw')
        return [dict(zip(keys, r)) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def months_with_usage(limit=24):
    """기록이 있는 달 목록(최신순) — 화면 선택용."""
    conn = get_auth_db()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT DISTINCT ym FROM ai_usage_log WHERE TRIM(COALESCE(ym,''))<>'' "
            " ORDER BY ym DESC LIMIT ?", (int(limit),)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        conn.close()
