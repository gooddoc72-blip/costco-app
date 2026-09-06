"""청구 원장 — 발송 1건 = 청구 근거 1행.

왜 필요한가:
  지금 청구 근거는 '날짜×사용자 총액 한 줄'(purchase_settle_snapshot)뿐이다.
  계산서를 발행하려면 그 금액이 **어느 발송건에서 나왔는지** 건별로 있어야 한다.
  총액만 있으면
    · 반품 한 건이 생겨도 그 건만 빼낼 수 없고 총액을 다시 계산해야 하며
    · 이미 발행한 계산서와 나중에 다시 계산한 값이 달라지면 설명할 방법이 없다.

핵심 원칙:
  발송처리 순간에 청구 행이 생기고(pending), 매입가는 나중에 확정된다(confirmed).
  영수증이 며칠 늦게 올라와도 된다 — 발송은 이미 원장에 잡혀 있고 영수증은
  그 행의 단가를 채울 뿐이다.

상태:
  pending    발송은 됐는데 매입가가 아직 추정치(공유DB 예상가)
  confirmed  영수증 실단가 또는 재고 lot 단가로 확정
  invoiced   계산서가 발행돼 잠김 — 다시 계산해도 안 바뀐다
  canceled   반품·취소 (행을 지우지 않고 상태만 바꾼다)

매입가 출처(cost_source):
  receipt   영수증 정산에서 배치된 실단가
  stock     과거 구입분(재고 lot) 단가
  estimate  공유DB 예상가 — 아직 확정 전
"""
import sqlite3
from datetime import datetime

from db_core import get_auth_db

STATUSES = ('pending', 'confirmed', 'invoiced', 'canceled')
COST_SOURCES = {'receipt': '영수증', 'stock': '재고', 'estimate': '예상',
                'memo': '관리자 배정', 'manual': '수동'}


def _conn():
    conn = get_auth_db()
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS billing_ledger (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL,
            order_no      TEXT NOT NULL,
            dispatched_at TEXT NOT NULL,
            product_no    TEXT DEFAULT '',
            product_name  TEXT DEFAULT '',
            qty           INTEGER DEFAULT 1,
            split_qty     INTEGER DEFAULT 1,
            unit_cost     INTEGER DEFAULT 0,
            amount        INTEGER DEFAULT 0,
            cost_source   TEXT DEFAULT 'estimate',
            ref_id        TEXT DEFAULT '',
            status        TEXT DEFAULT 'pending',
            invoice_id    INTEGER DEFAULT 0,
            memo          TEXT DEFAULT '',
            created_at    TEXT,
            confirmed_at  TEXT,
            invoiced_at   TEXT,
            UNIQUE(username, order_no, dispatched_at)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bl_date ON billing_ledger(dispatched_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bl_user ON billing_ledger(username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bl_inv ON billing_ledger(invoice_id)")
    conn.commit()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _receipt_settled_map(username):
    """영수증 정산에서 이미 확정된 (주문번호 → {amount, batch_id}).

    영수증 배치는 '이 주문을 이 실단가로 샀다'는 확정 기록이다. 원장은 그 값을
    그대로 쓰고 confirmed로 둔다 — 예상가로 다시 덮으면 안 된다.
    """
    out = {}
    try:
        conn = _conn()
        for r in conn.execute(
                "SELECT order_no, amount, batch_id FROM receipt_settle_items "
                "WHERE username=?", (username,)):
            out[str(r['order_no'])] = {'amount': int(r['amount'] or 0),
                                       'batch_id': str(r['batch_id'] or '')}
        conn.close()
    except Exception:
        pass
    return out


def sync_from_dispatch(username, date, dry_run=False):
    """그날 발송건을 원장에 반영. 반환: {'created','updated','skipped','rows'}

    · 이미 invoiced(계산서 발행)된 행은 절대 건드리지 않는다.
    · 영수증에서 확정된 건은 그 금액으로 confirmed.
    · 아니면 공유DB 예상가로 pending.
    """
    from db_purchase_settle import _dispatched_records
    from services import match_product_to_db, resolve_pack_factor, resolve_split_qty
    from db import get_all_products, get_shared_products

    recs = _dispatched_records(username, date)
    if not recs:
        return {'created': 0, 'updated': 0, 'skipped': 0, 'rows': []}

    _settled = _receipt_settled_map(username)
    _up, _sp = get_all_products(username), get_shared_products()
    conn = _conn()
    _ensure(conn)
    _exist = {str(r['order_no']): dict(r) for r in conn.execute(
        "SELECT * FROM billing_ledger WHERE username=? AND dispatched_at=?",
        (username, str(date)))}

    created = updated = skipped = 0
    out = []
    now = _now()
    for rec in recs:
        ono = str(rec.get('_sk') or '').strip()
        if not ono:
            continue
        name = str(rec.get('상품명') or '')
        qty = max(1, int(rec.get('수량') or 1))
        pno = str(rec.get('product_no') or '')

        _prev = _exist.get(ono)
        if _prev and str(_prev.get('status')) == 'invoiced':
            skipped += 1                     # 발행된 청구는 불변
            continue

        _hit = _settled.get(ono)
        if _hit:
            amount = int(_hit['amount'] or 0)
            src, ref, status = 'receipt', _hit['batch_id'], 'confirmed'
            p = match_product_to_db(username, name, product_no=pno,
                                    _user_prods=_up, _shared_prods=_sp)
            sq = resolve_split_qty(p or {}, name)
            unit = int((p or {}).get('unit_price') or 0)
        else:
            p = match_product_to_db(username, name, product_no=pno,
                                    _user_prods=_up, _shared_prods=_sp)
            sq = resolve_split_qty(p or {}, name)
            unit = int((p or {}).get('unit_price') or 0)
            pf = resolve_pack_factor(p or {}, name)
            amount = (unit // max(1, sq)) * qty * int(pf or 1)
            src, ref, status = 'estimate', '', 'pending'

        row = {'username': username, 'order_no': ono, 'dispatched_at': str(date),
               'product_no': str((p or {}).get('product_no') or pno),
               'product_name': name, 'qty': qty, 'split_qty': int(sq),
               'unit_cost': unit, 'amount': int(amount),
               'cost_source': src, 'ref_id': str(ref), 'status': status}
        out.append(row)
        if dry_run:
            continue
        if _prev:
            conn.execute(
                "UPDATE billing_ledger SET product_no=?, product_name=?, qty=?, split_qty=?,"
                " unit_cost=?, amount=?, cost_source=?, ref_id=?, status=?, confirmed_at=?"
                " WHERE id=?",
                (row['product_no'], name, qty, int(sq), unit, int(amount), src, str(ref),
                 status, (now if status == 'confirmed' else _prev.get('confirmed_at')),
                 _prev['id']))
            updated += 1
        else:
            conn.execute(
                "INSERT INTO billing_ledger (username, order_no, dispatched_at, product_no,"
                " product_name, qty, split_qty, unit_cost, amount, cost_source, ref_id,"
                " status, created_at, confirmed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (username, ono, str(date), row['product_no'], name, qty, int(sq), unit,
                 int(amount), src, str(ref), status, now,
                 (now if status == 'confirmed' else '')))
            created += 1
    if not dry_run:
        conn.commit()
    conn.close()
    return {'created': created, 'updated': updated, 'skipped': skipped, 'rows': out}


def receipt_items_in_range(date_from, date_to):
    """기간 내 영수증 품목 — [{costco_no, name, unit_price, receipt_date, qty}].

    영수증은 관리자가 올리므로 전 사용자 DB의 receipt_items를 훑는다.
    같은 상품이 여러 날 찍혔으면 가장 최근 영수증 단가를 쓴다.
    """
    import glob
    import os
    from db_core import DATA_DIR
    out = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, '*.db'))):
        u = os.path.basename(path)[:-3]
        if u == 'auth' or '.bak' in u or '.backup' in u:
            continue
        try:
            conn = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT product_no, product_name, unit_price, qty, receipt_date "
                "FROM receipt_items WHERE receipt_date BETWEEN ? AND ?",
                (str(date_from), str(date_to))).fetchall()
            conn.close()
        except Exception:
            continue
        for r in rows:
            cno = str(r['product_no'] or '').strip()
            if not cno or int(r['unit_price'] or 0) <= 0:
                continue
            prev = out.get(cno)
            if prev and str(prev['receipt_date']) >= str(r['receipt_date']):
                continue
            out[cno] = {'costco_no': cno,
                        'name': str(r['product_name'] or ''),
                        'unit_price': int(r['unit_price'] or 0),
                        'qty': int(r['qty'] or 0),
                        'receipt_date': str(r['receipt_date'] or '')}
    return sorted(out.values(), key=lambda x: x['name'])


def suggest_receipt_for(product_name, candidates, top=3):
    """발송 상품명 -> 영수증 품목 후보 상위 N.

    싼 토큰 점수로 좁힌 뒤 종합 점수(용량·브랜드 반영)로 다시 세운다.
    추천일 뿐이라 확정은 사람이 한다 — 이름만 겹쳐 붙였다가 엉뚱한 단가가
    청구된 적이 있다.
    """
    from services import _token_score, _combined_match_score
    nm = str(product_name or '').strip()
    if not nm:
        return []
    pre = []
    for c in (candidates or []):
        t = _token_score(nm, c.get('name') or '')
        if t > 0:
            pre.append((t, c))
    pre.sort(key=lambda x: -x[0])
    out = []
    for t, c in pre[:40]:
        sc = _combined_match_score(nm, c.get('name') or '')['total']
        out.append(dict(c, score=round(sc, 3)))
    out.sort(key=lambda x: -x['score'])
    return out[:int(top)]


def confirm_with_receipt(ledger_id, costco_no, unit_price, receipt_date='', memo=''):
    """원장 행을 영수증 품목으로 확정한다.

    청구액 = (영수증 팩단가 // 소분수) x 수량. 소분·묶음은 원장 행에 저장된 값을 쓴다.
    발행된 행은 건드리지 않는다.
    """
    conn = _conn()
    _ensure(conn)
    r = conn.execute("SELECT * FROM billing_ledger WHERE id=?", (int(ledger_id),)).fetchone()
    if not r:
        conn.close()
        return False
    if str(r['status']) == 'invoiced':
        conn.close()
        return False
    sq = max(1, int(r['split_qty'] or 1))
    qty = max(1, int(r['qty'] or 1))
    up = int(unit_price or 0)
    amount = (up // sq) * qty
    conn.execute(
        "UPDATE billing_ledger SET product_no=?, unit_cost=?, amount=?, cost_source='receipt',"
        " ref_id=?, status='confirmed', confirmed_at=?, memo=COALESCE(NULLIF(?,''), memo)"
        " WHERE id=?",
        (str(costco_no or ''), up, int(amount), str(receipt_date or ''), _now(),
         str(memo or ''), int(ledger_id)))
    conn.commit()
    conn.close()
    return True


def get_ledger(date_from, date_to, username=None, status=None):
    conn = _conn()
    _ensure(conn)
    sql = "SELECT * FROM billing_ledger WHERE dispatched_at BETWEEN ? AND ?"
    args = [str(date_from), str(date_to)]
    if username:
        sql += " AND username=?"
        args.append(username)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY dispatched_at DESC, username, id"
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.close()
    return rows


def daily_amounts(username, date_from, date_to):
    """사용자의 날짜별 구입(청구)금액 — {발송일: 금액}. 취소분은 뺀다.

    홈 달력에 그날 얼마어치를 샀는지 보여주기 위한 것이다. 원장이 발송 기준이라
    '그날 내보낸 물건의 매입가 합계'가 된다.
    """
    conn = _conn()
    _ensure(conn)
    rows = conn.execute(
        "SELECT dispatched_at, SUM(amount) FROM billing_ledger "
        "WHERE username=? AND dispatched_at BETWEEN ? AND ? AND status<>'canceled' "
        "GROUP BY dispatched_at",
        (username, str(date_from), str(date_to))).fetchall()
    conn.close()
    return {str(r[0]): int(r[1] or 0) for r in rows if int(r[1] or 0)}


def summarize(date_from, date_to, username=None):
    """사용자별 합계 — 취소분은 빼고, 상태별 건수도 함께."""
    rows = get_ledger(date_from, date_to, username=username)
    out = {}
    for r in rows:
        u = r['username']
        e = out.setdefault(u, {'amount': 0, 'count': 0, 'pending': 0,
                               'confirmed': 0, 'invoiced': 0, 'canceled': 0,
                               'no_price': 0})
        st = str(r.get('status') or 'pending')
        e[st] = e.get(st, 0) + 1
        if st == 'canceled':
            continue
        # 아직 안 산 물건은 매입가가 없는 것이 정상이다. 다만 이대로 계산서를
        # 끊으면 그만큼 덜 청구되므로 따로 센다.
        if int(r.get('amount') or 0) <= 0:
            e['no_price'] += 1
        e['amount'] += int(r.get('amount') or 0)
        e['count'] += 1
    return out


def unpriced_rows(date_from, date_to, username=None):
    """매입가가 아직 없는 행. 영수증이 올라오면 채워진다. 계산서 발행 전 확인용."""
    return [r for r in get_ledger(date_from, date_to, username=username)
            if str(r.get('status')) not in ('canceled', 'invoiced')
            and int(r.get('amount') or 0) <= 0]


def set_status(ids, status, memo=''):
    """상태 변경 — 발행된 행(invoiced)은 취소만 허용한다."""
    if status not in STATUSES or not ids:
        return 0
    conn = _conn()
    _ensure(conn)
    n = 0
    for i in ids:
        row = conn.execute("SELECT status FROM billing_ledger WHERE id=?", (int(i),)).fetchone()
        if not row:
            continue
        if str(row['status']) == 'invoiced' and status != 'canceled':
            continue                       # 발행분은 되돌리지 않는다
        cur = conn.execute(
            "UPDATE billing_ledger SET status=?, memo=COALESCE(NULLIF(?,''), memo) WHERE id=?",
            (status, str(memo or ''), int(i)))
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n
