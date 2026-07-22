"""구매내역 정산 — 사용자에게 청구할 '구매금액'을 계산·저장한다. (수익계산과 별개 모듈)

개념:
  · 예상(매일 자동): 주문 × 매칭 구매가(공유DB store_price 우선) 합산. 구매가 있는 상품 자동.
  · 확정(영수증 후): 코스트코 영수증 실단가가 공유DB에 반영된 뒤 재계산 → 예상 스냅샷과의
    상품별 차액을 '변경금액'으로 산출(사용자 화면 배지).
  · 매일=구매가만 / 월말=택배비+포장비 추가.

저장(auth.db):
  purchase_settle_snapshot — 청구 스냅샷(사용자·날짜·상품별 예상단가/금액). 확정 비교의 기준선.
"""
import json
import sqlite3
from datetime import datetime

from db_core import AUTH_DB, get_user_db


# ── 구매가 계산 (예상/확정 공통) ──────────────────────────────
def compute_daily_purchase(username, date):
    """(items, goods_total) 반환. 각 item: 주문 상품별 구매가(현재 공유/제품DB 기준).
    영수증 반영 전=예상, 반영 후 재호출=확정. 순수 조회(저장 없음)."""
    from pages_lib.profit_calc.loader import build_settlement_df
    from services import match_product_to_db, resolve_pack_factor
    from db import get_all_products, get_shared_products

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
            sq = max(1, int(p.get('split_qty', 1) or 1))
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


# ── 스냅샷 저장/조회 (예상 기준선) ───────────────────────────
def _conn():
    conn = sqlite3.connect(AUTH_DB)
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


def diff_against_snapshot(settle_date, username):
    """현재 계산(확정 후보) vs 저장된 예상 baseline 상품별 차액.
    Returns {changed:[...], total_prev, total_now, total_diff}."""
    snap = get_snapshot(settle_date, username)
    cur_items, cur_total = compute_daily_purchase(username, settle_date)
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
