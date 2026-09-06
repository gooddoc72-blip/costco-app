"""구매내역 정산 — 사용자에게 청구할 '구매금액'을 계산·저장한다. (수익계산과 별개 모듈)

개념:
  · 예상(매일 자동): 주문 × 매칭 구매가(공유DB store_price 우선) 합산. 구매가 있는 상품 자동.
  · 확정(영수증 후): 코스트코 영수증 실단가가 공유DB에 반영된 뒤 재계산 → 예상 스냅샷과의
    상품별 차액을 '변경금액'으로 산출(사용자 화면 배지).
  · 매일=구매가만 / 월말=택배비+포장비 추가.

저장(auth.db):
  purchase_settle_snapshot — 청구 스냅샷(사용자·날짜·상품별 예상단가/금액). 확정 비교의 기준선.
"""
import calendar
import json
import sqlite3
from datetime import datetime

from db_core import AUTH_DB, get_user_db, get_auth_db


# ── 월말 택배·포장 (그달 1일~말일 누적) ──────────────────────
def is_last_day_of_month(date_str):
    y, m, d = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
    return d == calendar.monthrange(y, m)[1]


def compute_month_fees(username, year_month):
    """그달 택배·포장 누적. 정책: 실제 배정 포장비(order_packaging) + 발송건수×택배비.
    Returns {ship_count, ship_fee, ship_total, pkg_total, fees_total, year_month}."""
    from db import get_all_settings, get_dispatch_counts
    from db_packaging import get_packaging_cost_map
    y, m = int(year_month[:4]), int(year_month[5:7])
    last = calendar.monthrange(y, m)[1]
    d_from, d_to = f"{year_month}-01", f"{year_month}-{last:02d}"

    # 택배: 발송건수 × 사용자 택배비
    disp = get_dispatch_counts(username, d_from, d_to) or {}
    ship_count = sum(int(v or 0) for v in disp.values())
    s = get_all_settings(username) or {}
    try:
        ship_fee = int(s.get('shipping_cost') or 1800)
    except (TypeError, ValueError):
        ship_fee = 1800
    ship_total = ship_count * ship_fee

    # 포장: 그달 주문들의 실제 배정 포장비(order_packaging.total_cost) 합
    conn = get_user_db(username)
    onos = set()
    try:
        for r in conn.execute(
                "SELECT DISTINCT order_no FROM order_history WHERE order_date BETWEEN ? AND ?",
                (d_from, d_to)):
            if r[0]:
                onos.add(str(r[0]))
    except Exception:
        pass
    try:
        for r in conn.execute(
                "SELECT DISTINCT order_no FROM dispatch_log WHERE substr(dispatched_at,1,7)=?",
                (year_month,)):
            if r[0]:
                onos.add(str(r[0]))
    except Exception:
        pass
    conn.close()
    pkg_map = get_packaging_cost_map(username, list(onos)) if onos else {}
    pkg_total = sum(int(v or 0) for v in pkg_map.values())

    return {'ship_count': ship_count, 'ship_fee': ship_fee, 'ship_total': ship_total,
            'pkg_total': pkg_total, 'fees_total': ship_total + pkg_total,
            'year_month': year_month}


def month_fees_if_last_day(username, date_str):
    """말일이면 그달 누적 택배·포장 fees dict, 아니면 None."""
    if not is_last_day_of_month(date_str):
        return None
    return compute_month_fees(username, date_str[:7])


# ── 구매가 계산 (예상/확정 공통) ──────────────────────────────
def _dispatched_records(username, date):
    """그날 송장 등록(발송처리)한 주문 → compute_daily_purchase가 쓰는 형태.

    dispatch_log는 order_history와 JOIN하는데, 쿠팡·엑셀 업로드 계정은
    order_history가 비어 있는 경우가 많다(clglobal0919는 daily_orders에만
    있는 주문이 980건). 그때는 daily_orders에서 상품번호·수량을 보충한다.
    """
    from db import get_dispatched_orders_with_details, get_user_db

    rows = get_dispatched_orders_with_details(username, str(date)) or []
    if not rows:
        return []
    _need = [str(r.get('order_no') or '') for r in rows
             if not str(r.get('product_no') or '').strip()]
    _fill = {}
    if _need:
        try:
            conn = get_user_db(username)
            CHUNK = 900                       # SQLite 변수 한도
            for i in range(0, len(_need), CHUNK):
                _c = _need[i:i + CHUNK]
                _ph = ",".join("?" * len(_c))
                for _r in conn.execute(
                        "SELECT order_no, product_no, product_name, qty "
                        "FROM daily_orders WHERE order_no IN (%s)" % _ph, _c):
                    _fill[str(_r['order_no'])] = _r
            conn.close()
        except Exception:
            _fill = {}
    out = []
    for r in rows:
        _ono = str(r.get('order_no') or '')
        _f = _fill.get(_ono)
        out.append({
            '_sk': _ono,
            '수취인명': r.get('recipient') or '',
            '상품명': (r.get('product_name') or (_f['product_name'] if _f else '') or ''),
            'product_no': (str(r.get('product_no') or '').strip()
                           or (str(_f['product_no'] or '') if _f else '')),
            '수량': int(r.get('qty') or (_f['qty'] if _f else 1) or 1),
        })
    return out


def compute_daily_purchase(username, date, basis='dispatch'):
    """(items, goods_total) 반환. 각 item: 주문 상품별 구매가(현재 공유/제품DB 기준).
    영수증 반영 전=예상, 반영 후 재호출=확정. 순수 조회(저장 없음).

    basis:
      'dispatch' — 그날 **송장 등록(발송처리)** 한 주문 기준. 기본값.
                   실제 업무 흐름이 '발송처리 → 다음날 영수증 등록 → 매칭'이라
                   청구 대상은 그날 내보낸 물건이어야 한다.
      'order'    — 주문일 기준(구버전). 발송 이력이 없는 계정 확인용.
    """
    from pages_lib.profit_calc.loader import build_settlement_df
    from services import match_product_to_db, resolve_pack_factor, resolve_split_qty
    from db import get_all_products, get_shared_products
    import pandas as _pd

    if basis == 'dispatch':
        _recs = _dispatched_records(username, date)
        if not _recs:
            return [], 0
        df = _pd.DataFrame(_recs)
    else:
        df, _label, _kind = build_settlement_df(username, date)
    if df is None or df.empty:
        return [], 0

    uprods = get_all_products(username)
    sprods = get_shared_products()
    _memo = {}

    def _match(name, pno):
        if pno:
            return match_product_to_db(username, name, product_no=pno,
                                       _user_prods=uprods, _shared_prods=sprods)
        if name not in _memo:
            _memo[name] = match_product_to_db(username, name, product_no='',
                                              _user_prods=uprods, _shared_prods=sprods)
        return _memo[name]

    has_pno = 'product_no' in df.columns
    items, total = [], 0
    for rec in df.to_dict('records'):
        name = str(rec.get('상품명', '') or '')
        qty = max(1, int(rec.get('수량', 1) or 1))
        pno = (str(rec.get('product_no', '') or '') if has_pno else '')
        p = _match(name, pno) or _match(name, '')
        if p:
            # 상품명 소분 규칙 우선 — 사용자 제품DB 폴백 경로도 규칙이 걸리게 한다
            sq = resolve_split_qty(p, name)
            sf = resolve_pack_factor(p, name)
            unit = int(p.get('unit_price') or 0)      # 공유 store_price(영수증 실단가) 우선 반영됨
            amount = (unit // sq) * qty * sf
            matched = p.get('costco_name') or p.get('store_product_name') or name
        else:
            sq, unit, amount, matched = 1, 0, 0, ''
        total += amount
        items.append({
            'order_no': str(rec.get('_sk', '') or ''),
            'recipient': str(rec.get('수취인명', '') or ''),
            'product_name': name,
            'matched_name': matched,
            'product_no': pno,
            'qty': qty,
            'split_qty': sq,
            'unit_price': unit,
            'amount': int(amount),
        })
    return items, int(total)


def suggest_shared_matches(product_name, shared_prods=None, top=5):
    """상품명 → 공유DB 후보 상위 N. (코스트코번호 연결 도우미용)

    싼 토큰 점수로 후보를 좁힌 뒤 종합 점수(용량·브랜드 반영)로 다시 세운다.
    3,891개를 전부 종합 점수로 재면 화면이 눈에 띄게 느려진다.
    반환: [{'product_no','costco_name','unit_price','split_qty','score'}]
    """
    from services import _token_score, _combined_match_score
    from db import get_shared_products
    _nm = str(product_name or '').strip()
    if not _nm:
        return []
    _sp = shared_prods if shared_prods is not None else (get_shared_products() or [])
    _pre = []
    for _p in _sp:
        _cn = str(_p.get('costco_name') or '')
        if not _cn:
            continue
        _t = _token_score(_nm, _cn)
        if _t > 0:
            _pre.append((_t, _p))
    _pre.sort(key=lambda x: -x[0])
    _out = []
    for _t, _p in _pre[:40]:
        _sc = _combined_match_score(_nm, str(_p.get('costco_name') or ''))['total']
        _out.append({'product_no': str(_p.get('product_no') or ''),
                     'costco_name': str(_p.get('costco_name') or ''),
                     'unit_price': int(_p.get('unit_price') or 0),
                     'split_qty': max(1, int(_p.get('split_qty') or 1)),
                     'score': round(_sc, 3)})
    _out.sort(key=lambda x: -x['score'])
    return _out[:int(top)]


def link_product_mapping(username, naver_no, product_name, costco_no, split_qty=1):
    """사용자 제품DB에 '네이버번호 → 코스트코번호 + 소분수'를 기입한다.

    구매금액이 0원으로 나오는 근본 원인은 이 매핑이 없어서다. 주문은 네이버번호로
    들어오는데 가격은 공유DB에 코스트코번호로 있어, 둘을 잇지 못하면 값을 못 찾는다.
    (oxo 1,345개 중 코스트코번호가 있는 건 312개뿐)

    소분수는 사용자 레코드에 쓴다 — match_product_to_db가 사용자 항목이 있으면
    사용자 split_qty를 우선하도록 설계돼 있다(소분을 하는 사람과 안 하는 사람이
    같은 상품을 다르게 팔 수 있어서다).
    반환: 'updated' | 'inserted' | '' (실패)
    """
    from db import get_user_db
    _nv = str(naver_no or '').strip()
    _cno = str(costco_no or '').strip()
    _sq = max(1, int(split_qty or 1))
    _nm = str(product_name or '').strip()
    if not (username and _cno):
        return ''
    conn = get_user_db(username)
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
        _keys = [c for c in ('naver_channel_pno', 'naver_origin_pno') if c in _cols]
        _now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if _nv and _keys:
            _where = " OR ".join("TRIM(COALESCE(%s,''))=?" % c for c in _keys)
            cur = conn.execute(
                "UPDATE products SET product_no=?, split_qty=?, updated_at=? WHERE (%s)" % _where,
                [_cno, _sq, _now] + [_nv] * len(_keys))
            if cur.rowcount:
                conn.commit()
                return 'updated'
        if not _nm:
            return ''
        for _mk in (_nm[:180], "%s#%s" % (_nm[:170], _nv or _cno)):
            try:
                conn.execute(
                    "INSERT INTO products (product_no, store_product_name, costco_name,"
                    " match_keyword, unit_price, split_qty, updated_at, naver_channel_pno)"
                    " VALUES (?,?,?,?,0,?,?,?)",
                    (_cno, _nm, _nm, _mk, _sq, _now, _nv))
                conn.commit()
                return 'inserted'
            except sqlite3.IntegrityError:
                continue
        return ''
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── 스냅샷 저장/조회 (예상 기준선) ───────────────────────────
def _conn():
    conn = get_auth_db()
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS purchase_settle_snapshot (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            settle_date TEXT,
            username    TEXT,
            est_total   INTEGER DEFAULT 0,   -- 예상 구매금액(기준선)
            est_items_json TEXT,             -- 예상 상품별 스냅샷(변경 비교 기준)
            final_total INTEGER DEFAULT 0,   -- 확정 구매금액(영수증 반영 후)
            changed_json TEXT,               -- 확정 시 변경 상품 목록
            fees_total  INTEGER DEFAULT 0,   -- 월말 택배+포장 (해당일만)
            status      TEXT DEFAULT 'est',  -- 'est'(예상) | 'final'(확정)
            created_by  TEXT,
            created_at  TEXT,
            updated_at  TEXT,
            UNIQUE(settle_date, username)
        )
    """)
    conn.commit()


def save_estimate(settle_date, username, est_total, est_items, created_by=''):
    """예상 기준선 저장(upsert). 확정 비교의 baseline. status='est'로 초기화(final 해제)."""
    conn = _conn()
    _ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO purchase_settle_snapshot
           (settle_date, username, est_total, est_items_json, final_total, changed_json,
            status, created_by, created_at, updated_at)
           VALUES (?,?,?,?,0,'','est',?,?,?)
           ON CONFLICT(settle_date, username) DO UPDATE SET
             est_total=excluded.est_total, est_items_json=excluded.est_items_json,
             final_total=0, changed_json='', status='est', updated_at=excluded.updated_at""",
        (str(settle_date), username, int(est_total),
         json.dumps(est_items, ensure_ascii=False), created_by, now, now),
    )
    conn.commit()
    conn.close()


def finalize(settle_date, username, final_total, changed, fees_total=0, created_by=''):
    """확정 — 예상 baseline은 보존하고 final_total·변경목록·상태만 갱신.
    예상 저장이 없으면 baseline=final로 생성(변경 0)."""
    conn = _conn()
    _ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exists = conn.execute(
        "SELECT id FROM purchase_settle_snapshot WHERE settle_date=? AND username=?",
        (str(settle_date), username)).fetchone()
    if exists:
        conn.execute(
            """UPDATE purchase_settle_snapshot
               SET final_total=?, changed_json=?, fees_total=?, status='final', updated_at=?
               WHERE settle_date=? AND username=?""",
            (int(final_total), json.dumps(changed, ensure_ascii=False), int(fees_total),
             now, str(settle_date), username))
    else:
        conn.execute(
            """INSERT INTO purchase_settle_snapshot
               (settle_date, username, est_total, est_items_json, final_total, changed_json,
                fees_total, status, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?, 'final', ?,?,?)""",
            (str(settle_date), username, int(final_total), '[]', int(final_total),
             json.dumps(changed, ensure_ascii=False), int(fees_total), created_by, now, now))
    conn.commit()
    conn.close()


def get_snapshot(settle_date, username):
    conn = _conn()
    _ensure(conn)
    r = conn.execute(
        "SELECT * FROM purchase_settle_snapshot WHERE settle_date=? AND username=?",
        (str(settle_date), username)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    for k, j in (('est_items', 'est_items_json'), ('changed', 'changed_json')):
        try:
            d[k] = json.loads(d.get(j) or '[]')
        except Exception:
            d[k] = []
    return d


def diff_against_snapshot(settle_date, username, basis='dispatch'):
    """현재 계산(확정 후보) vs 저장된 예상 baseline 상품별 차액.

    basis는 예상을 저장할 때 쓴 기준과 같아야 한다. 다르면 대상 주문 자체가
    달라져 없는 차액이 생긴다.
    Returns {changed:[...], total_prev, total_now, total_diff}."""
    snap = get_snapshot(settle_date, username)
    cur_items, cur_total = compute_daily_purchase(username, settle_date, basis=basis)
    prev_by = {}
    if snap:
        for it in snap.get('est_items', []):
            prev_by[(it.get('order_no'), it.get('product_name'))] = int(it.get('amount', 0) or 0)
    changed = []
    for it in cur_items:
        k = (it.get('order_no'), it.get('product_name'))
        prev = prev_by.get(k)
        nowv = int(it.get('amount', 0) or 0)
        if prev is not None and prev != nowv:
            changed.append({'order_no': it['order_no'], 'product_name': it['product_name'],
                            'prev': prev, 'now': nowv, 'diff': nowv - prev})
    total_prev = int(snap['est_total']) if snap else cur_total
    return {'changed': changed, 'total_prev': total_prev,
            'total_now': cur_total, 'total_diff': cur_total - total_prev}


def get_order_dispatch_counts(username, date_from, date_to):
    """사용자의 날짜별 {주문수집 건수, 발송 건수}.

    구매내역 정산은 '무엇을 얼마에 청구했나'만이 아니라 '몇 건 받아 몇 건 내보냈나'를
    같이 봐야 한다. 수집만 되고 안 나간 건이 쌓이면 청구가 어긋난다.
      주문수집 = daily_orders(수집·저장한 날짜 기준)
      발송     = dispatch_log(송장 등록으로 발송처리한 날짜 기준)
    반환: {날짜: {'orders': n, 'dispatch': n}}
    """
    out = {}
    try:
        conn = get_user_db(username)
    except Exception:
        return out
    try:
        for d, n in conn.execute(
                "SELECT order_date, count(*) FROM daily_orders "
                "WHERE order_date BETWEEN ? AND ? GROUP BY order_date",
                (str(date_from), str(date_to))):
            out.setdefault(str(d), {'orders': 0, 'dispatch': 0})['orders'] = int(n or 0)
    except Exception:
        pass
    try:
        for d, n in conn.execute(
                "SELECT dispatched_at, count(*) FROM dispatch_log "
                "WHERE dispatched_at BETWEEN ? AND ? GROUP BY dispatched_at",
                (str(date_from), str(date_to))):
            out.setdefault(str(d), {'orders': 0, 'dispatch': 0})['dispatch'] = int(n or 0)
    except Exception:
        pass
    conn.close()
    return out


def get_dispatch_list(username, date):
    """그 날짜에 발송처리한 주문 목록 — 화면에서 건수를 눌렀을 때 보여줄 내역."""
    from db import get_dispatched_orders_with_details
    try:
        return get_dispatched_orders_with_details(username, str(date)) or []
    except Exception:
        return []


def _snap_amount(r):
    """그 날짜의 청구 기준액 — 확정됐으면 확정액, 아니면 예상액."""
    return (int(r.get('final_total') or 0) if str(r.get('status')) == 'final'
            else int(r.get('est_total') or 0))


def get_period_rows(date_from, date_to, username=None):
    """기간 내 저장된 정산 스냅샷 — [{settle_date, username, amount, fees_total, status}].

    화면이 매번 compute_daily_purchase를 날짜 수만큼 돌면 느리다.
    '예상 저장'·'확정' 때 남긴 스냅샷을 읽어 집계한다.
    """
    conn = _conn()
    _ensure(conn)
    sql = ("SELECT settle_date, username, est_total, final_total, fees_total, status, updated_at "
           "FROM purchase_settle_snapshot WHERE settle_date BETWEEN ? AND ?")
    args = [str(date_from), str(date_to)]
    if username:
        sql += " AND username=?"
        args.append(username)
    sql += " ORDER BY settle_date, username"
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.close()
    for r in rows:
        r['amount'] = _snap_amount(r)
    return rows


def get_daily_summary(date_from, date_to):
    """일별 정리 — {날짜: {사용자: 금액}}, 사용자별 합계, 날짜별 합계."""
    rows = get_period_rows(date_from, date_to)
    by_date, by_user = {}, {}
    for r in rows:
        d, u, a = r['settle_date'], r['username'], int(r['amount'] or 0)
        by_date.setdefault(d, {})[u] = by_date.setdefault(d, {}).get(u, 0) + a
        by_user[u] = by_user.get(u, 0) + a
    return {'rows': rows, 'by_date': by_date, 'by_user': by_user,
            'total': sum(by_user.values())}


def get_monthly_summary(year_month):
    """월별 정리 — 사용자별 {구매금액, 월비용, 청구액, 일수, 확정일수}.

    year_month: 'YYYY-MM'
    """
    _ym = str(year_month)[:7]
    _last = calendar.monthrange(int(_ym[:4]), int(_ym[5:7]))[1]
    rows = get_period_rows('%s-01' % _ym, '%s-%02d' % (_ym, _last))
    out = {}
    for r in rows:
        u = r['username']
        e = out.setdefault(u, {'goods': 0, 'fees': 0, 'days': 0, 'final_days': 0})
        e['goods'] += int(r['amount'] or 0)
        e['fees'] += int(r.get('fees_total') or 0)
        e['days'] += 1
        if str(r.get('status')) == 'final':
            e['final_days'] += 1
    for u, e in out.items():
        e['charge'] = e['goods'] + e['fees']
    return out


def get_user_badge(settle_date, username):
    """사용자 화면 배지용 — 확정되어 예상과 다르면 변경 요약 반환, 아니면 None."""
    snap = get_snapshot(settle_date, username)
    if not snap or snap.get('status') != 'final':
        return None
    diff = int(snap.get('final_total', 0)) - int(snap.get('est_total', 0))
    changed = snap.get('changed', [])
    if not changed and diff == 0:
        return None
    return {'diff': diff, 'changed': changed,
            'est_total': int(snap.get('est_total', 0)),
            'final_total': int(snap.get('final_total', 0))}
