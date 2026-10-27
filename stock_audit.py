"""재고 조회·점검 — 사용자별 재고 카드, 통합 검색, 이상 점검 (읽기 전용).

재고 장부(inventory_lots/inventory_moves) 계산 자체는 맞는데(10/27 점검: 입고 −
차감 = 남은 수량, 48건 전부 일치) "엉망"으로 보인 건 장부가 실물 흐름을 놓쳐도
그걸 볼 화면이 없었기 때문이다.
  · 입고를 며칠 늦게 등록하면 그 사이 발송은 영영 안 빠진다(차감은 발송 순간에만)
    → 사람이 수동 조정으로 0을 맞춘다(차감 63건 중 22건이 수동 조정).
  · 코스트코 번호를 못 찾은 발송은 차감을 조용히 건너뛴다.
  · 같은 재고를 두 번 입고, 주인 없는 관리자 재고, 누가 냈는지 모르는 미결제 재고.
여기서는 아무것도 고치지 않는다 — 찾아서 보여 주기만 한다. 고치는 건 기존 화면
(수동 입출고·입고 취소·재고 넘기기)이 한다.
"""
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

from db_core import AUTH_DB, DATA_DIR

#: 차감 경로 표시 — inventory_moves.platform
PLATFORM_LABEL = {
    'naver': '발송(네이버)', 'coupang': '발송(쿠팡)', 'upload': '발송(파일)',
    'manual': '발송(수동)', 'settle': '영수증 정산 재고출고', 'adjust': '수동 조정',
    'transfer': '재고 넘기기', 'fix': '정리 스크립트',
}
#: 발송이 만든 차감 — 주문 취소면 재고가 돌아와야 한다
DISPATCH_PLATFORMS = ('naver', 'coupang', 'upload', 'manual')
CANCEL_STATUS = ('취소완료', 'CANCELED_BY_NOPAYMENT')
RETURN_STATUS = ('반품완료',)
OLD_DAYS = 30

_CACHE = {}          # 무거운 점검(발송 대조) — {key: (시각, 값)}
_CACHE_SEC = 600


def _auth():
    c = sqlite3.connect(AUTH_DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _user_conn(username):
    """사용자 DB — 없는 파일을 새로 만들지 않는다(get_user_db는 만든다)."""
    p = os.path.join(DATA_DIR, f"{username}.db")
    if not os.path.exists(p):
        return None
    c = sqlite3.connect(p, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def names() -> dict:
    with _auth() as c:
        return {r['username']: (str(r['display_name'] or '').strip() or r['username'])
                for r in c.execute("SELECT username, display_name FROM users")}


def _admins() -> set:
    with _auth() as c:
        return {r['username'] for r in c.execute("SELECT username FROM users WHERE is_admin=1")}


def _age(received_at) -> int:
    try:
        return (date.today() - date.fromisoformat(str(received_at)[:10])).days
    except ValueError:
        return 0


def _lots(c, where='1=1', args=()):
    return [dict(r) for r in c.execute(
        f"SELECT * FROM inventory_lots WHERE {where} ORDER BY received_at DESC, id DESC", args)]


def _moves_by_lot(c, lot_ids):
    out = {}
    ids = [int(i) for i in lot_ids]
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        ph = ",".join("?" * len(part))
        for m in c.execute(f"SELECT * FROM inventory_moves WHERE lot_id IN ({ph}) ORDER BY created_at, id",
                           part):
            out.setdefault(int(m['lot_id']), []).append(_move_out(dict(m)))
    return out


def _move_out(m) -> dict:
    return {
        'id': int(m['id']), 'lot_id': int(m['lot_id']), 'owner': m['owner'], 'seller': m['seller'],
        'qty': int(m['qty'] or 0), 'unit_cost': int(m['unit_cost'] or 0),
        'surcharge': int(m['surcharge'] or 0), 'is_cross': int(m['is_cross'] or 0),
        'order_no': m['order_no'] or '', 'date': str(m['dispatched_at'] or m['created_at'] or '')[:10],
        'created_at': str(m['created_at'] or '')[:16], 'platform': m['platform'] or '',
        'how': PLATFORM_LABEL.get(m['platform'] or '', m['platform'] or ''),
        'settle_status': m['settle_status'] or '',
    }


def _lot_out(l, moves) -> dict:
    return {
        'id': int(l['id']), 'owner': l['owner'], 'product_no': l['product_no'] or '',
        'product_name': l['product_name'] or '', 'qty_in': int(l['qty_in'] or 0),
        'qty_left': int(l['qty_left'] or 0), 'unit_cost': int(l['unit_cost'] or 0),
        'list_price': int(l.get('list_price') or 0), 'split_qty': int(l['split_qty'] or 1),
        'received_at': str(l['received_at'] or '')[:10], 'created_at': str(l['created_at'] or '')[:16],
        'status': l['status'] or '', 'paid': int(l.get('paid') or 0), 'memo': l['memo'] or '',
        'age': _age(l['received_at']), 'moves': moves,
    }


# ── 사용자별 현황 ─────────────────────────────────────────
def overview() -> dict:
    nm = names()
    with _auth() as c:
        lots = _lots(c, "status='ACTIVE' AND qty_left>0")
        pend = c.execute("""SELECT owner, SUM(qty*unit_cost+surcharge) s FROM inventory_moves
                            WHERE is_cross=1 AND settle_status='PENDING' GROUP BY owner""").fetchall()
        borrowed = c.execute("""SELECT seller, COUNT(*) n FROM inventory_moves
                                WHERE is_cross=1 AND seller<>owner GROUP BY seller""").fetchall()
    owners = {}
    for l in lots:
        o = owners.setdefault(l['owner'], {'username': l['owner'], 'name': nm.get(l['owner'], l['owner']),
                                           'items': 0, 'units': 0, 'amount': 0, 'unpaid_units': 0,
                                           'old_units': 0, 'pending_credit': 0, 'borrowed': 0})
        q = int(l['qty_left'] or 0)
        o['items'] += 1
        o['units'] += q
        o['amount'] += q * int(l['unit_cost'] or 0)
        if not int(l.get('paid') or 0):
            o['unpaid_units'] += q
        if _age(l['received_at']) > OLD_DAYS:
            o['old_units'] += q
    for r in pend:
        if r['owner'] in owners:
            owners[r['owner']]['pending_credit'] = int(r['s'] or 0)
    for r in borrowed:
        if r['seller'] in owners:
            owners[r['seller']]['borrowed'] = int(r['n'] or 0)
    rows = sorted(owners.values(), key=lambda o: -o['amount'])
    return {'owners': rows, 'names': nm,
            'total': {'units': sum(o['units'] for o in rows), 'amount': sum(o['amount'] for o in rows),
                      'unpaid_units': sum(o['unpaid_units'] for o in rows)}}


def user_card(username: str, include_empty: bool = True) -> dict:
    """한 사용자의 재고 — 입고마다 '어디로 나갔나'까지, 남의 재고를 쓴 내역, 재고 적립."""
    nm = names()
    with _auth() as c:
        lots = _lots(c, "owner=?" + ("" if include_empty else " AND qty_left>0"), (username,))
        mv = _moves_by_lot(c, [l['id'] for l in lots])
        used = [_move_out(dict(m)) for m in c.execute(
            "SELECT * FROM inventory_moves WHERE seller=? AND owner<>? ORDER BY created_at DESC LIMIT 300",
            (username, username))]
        lot_name = {int(r['id']): r['product_name'] for r in c.execute(
            "SELECT id, product_name FROM inventory_lots WHERE owner<>?", (username,))}
        credits = [dict(r) for r in c.execute(
            """SELECT tx_date, amount, memo, settle_date FROM deposit_ledger
               WHERE username=? AND kind IN ('stock_credit','return_credit')
               ORDER BY id DESC LIMIT 200""", (username,))]
    for m in used:
        m['product_name'] = lot_name.get(m['lot_id'], '')
    out_lots = [_lot_out(l, mv.get(int(l['id']), [])) for l in lots]
    left = [l for l in out_lots if l['qty_left'] > 0 and l['status'] == 'ACTIVE']
    out_by = {}
    for l in out_lots:
        for m in l['moves']:
            out_by[m['platform']] = out_by.get(m['platform'], 0) + m['qty']
    return {
        'username': username, 'name': nm.get(username, username), 'names': nm,
        'summary': {
            'items': len(left), 'units': sum(l['qty_left'] for l in left),
            'amount': sum(l['qty_left'] * l['unit_cost'] for l in left),
            'unpaid_units': sum(l['qty_left'] for l in left if not l['paid']),
            'old_units': sum(l['qty_left'] for l in left if l['age'] > OLD_DAYS),
            'in_units': sum(l['qty_in'] for l in out_lots),
            'out_by': [{'platform': k, 'how': PLATFORM_LABEL.get(k, k), 'qty': v}
                       for k, v in sorted(out_by.items(), key=lambda x: -x[1])],
        },
        'lots': out_lots, 'used_others': used, 'credits': credits,
    }


# ── 통합 검색 ─────────────────────────────────────────────
def _norm(s) -> str:
    return re.sub(r'[^0-9a-z가-힣]', '', str(s or '').lower())


def _find_dispatch(order_no: str) -> list:
    """주문번호로 모든 사용자 발송 기록·주문 상태를 찾는다(주문번호 검색 때만)."""
    out = []
    for u in names():
        c = _user_conn(u)
        if c is None:
            continue
        try:
            for r in c.execute("SELECT order_no, dispatched_at, product_name, platform FROM dispatch_log "
                               "WHERE order_no LIKE ? LIMIT 5", (f"%{order_no}%",)):
                st = c.execute("SELECT status FROM order_history WHERE order_no=? LIMIT 1",
                               (r['order_no'],)).fetchone()
                out.append({'username': u, 'order_no': r['order_no'], 'date': str(r['dispatched_at'])[:10],
                            'product_name': r['product_name'] or '', 'platform': r['platform'] or '',
                            'status': (st['status'] if st else '') or ''})
        except sqlite3.Error:
            pass
        finally:
            c.close()
    return out


def search(q: str) -> dict:
    q = str(q or '').strip()
    if len(q) < 2:
        return {'q': q, 'lots': [], 'moves': [], 'settle': [], 'deposits': [], 'dispatch': []}
    nm = names()
    who = {u for u, n in nm.items() if _norm(q) and (_norm(q) in _norm(n) or _norm(q) == _norm(u))}
    words = [w for w in re.split(r'\s+', q) if len(_norm(w)) >= 2] or [q]
    is_order = bool(re.fullmatch(r'[0-9\-]{8,}', q))
    with _auth() as c:
        all_lots = _lots(c)
        hit = []
        for l in all_lots:
            hay = _norm(f"{l['product_name']} {l['product_no']} {l['memo']}")
            if (l['owner'] in who or str(l['product_no']) == q
                    or all(_norm(w) in hay for w in words)):
                hit.append(l)
        hit = hit[:200]
        mv = _moves_by_lot(c, [l['id'] for l in hit])
        lots = [_lot_out(l, mv.get(int(l['id']), [])) for l in hit]
        moves, settle = [], []
        if is_order:
            lot_by = {int(l['id']): l for l in all_lots}
            for m in c.execute("SELECT * FROM inventory_moves WHERE order_no LIKE ? ORDER BY id",
                               (f"%{q}%",)):
                mo = _move_out(dict(m))
                l = lot_by.get(mo['lot_id']) or {}
                mo['product_name'] = l.get('product_name', '')
                mo['received_at'] = str(l.get('received_at', ''))[:10]
                moves.append(mo)
            settle = [dict(r) for r in c.execute(
                """SELECT settle_date, username, order_no, product_no, product_name, qty, amount,
                          source, stock_owner, memo FROM settle_item WHERE order_no LIKE ?
                   ORDER BY settle_date DESC LIMIT 50""", (f"%{q}%",))]
        deposits = [dict(r) for r in c.execute(
            """SELECT username, tx_date, kind, amount, memo FROM deposit_ledger
               WHERE kind IN ('stock_credit','return_credit') AND memo LIKE ?
               ORDER BY id DESC LIMIT 50""", (f"%{q}%",))]
    return {'q': q, 'names': nm, 'lots': lots, 'moves': moves, 'settle': settle,
            'deposits': deposits, 'dispatch': _find_dispatch(q) if is_order else []}


# ── 이상 점검 ─────────────────────────────────────────────
def _resolved_dispatch(username, d_from, d_to=None) -> list:
    """그 사용자의 발송 기록 + 코스트코 번호(발송 차감과 같은 해석)."""
    c = _user_conn(username)
    if c is None:
        return []
    try:
        sql = "SELECT order_no, dispatched_at, product_name FROM dispatch_log WHERE dispatched_at>=?"
        args = [d_from]
        if d_to:
            sql += " AND dispatched_at<=?"
            args.append(d_to)
        rows = [dict(r) for r in c.execute(sql, args)]
        if not rows:
            return []
        try:
            from db_dispatch_log import _resolve_order_items
            items = _resolve_order_items(c, [r['order_no'] for r in rows], username=username)
        except Exception:
            items = {}
        for r in rows:
            r['costco_no'] = str((items.get(r['order_no']) or {}).get('costco_no') or '').strip()
        return rows
    finally:
        c.close()


def checks(force: bool = False) -> dict:
    key = 'checks'
    hit = _CACHE.get(key)
    if hit and not force and time.time() - hit[0] < _CACHE_SEC:
        return hit[1]
    nm = names()
    admins = _admins()
    items = []

    def add(kind, level, title, detail, owner='', lot_ids=(), fix='', rows=None):
        items.append({'kind': kind, 'level': level, 'title': title, 'detail': detail,
                      'owner': owner, 'owner_name': nm.get(owner, owner) if owner else '',
                      'lot_ids': list(lot_ids), 'fix': fix, 'rows': rows or []})

    with _auth() as c:
        lots = _lots(c)
        moves = [dict(r) for r in c.execute("SELECT * FROM inventory_moves")]
    mv_orders = {m['order_no'] for m in moves}
    used = {}
    for m in moves:
        used.setdefault(int(m['lot_id']), []).append(m)
    active = [l for l in lots if l['status'] == 'ACTIVE' and int(l['qty_left'] or 0) > 0]

    # 0) 장부 산수 — 입고 − 차감 = 남은 수량
    for l in lots:
        exp = int(l['qty_in'] or 0) - sum(int(m['qty'] or 0) for m in used.get(int(l['id']), []))
        if exp != int(l['qty_left'] or 0) or int(l['qty_left'] or 0) < 0:
            add('math', 'high', f"장부 수량 불일치 — lot {l['id']}",
                f"{l['product_name'][:30]} · 입고 {l['qty_in']} − 차감 = {exp}인데 남은 수량 {l['qty_left']}",
                l['owner'], [l['id']], '수동 입출고로 맞추기')

    # 1) 같은 재고를 두 번 입고한 것 같음 — 같은 보유자·번호·수량, 입고일 3일 이내
    seen = set()
    for a in lots:
        for b in lots:
            if (a['id'] < b['id'] and a['owner'] == b['owner'] and a['product_no']
                    and a['product_no'] == b['product_no'] and a['qty_in'] == b['qty_in']
                    and abs(_age(a['received_at']) - _age(b['received_at'])) <= 3
                    and (a['id'], b['id']) not in seen):
                seen.add((a['id'], b['id']))
                untouched = [x['id'] for x in (a, b) if not used.get(int(x['id']))]
                add('dup', 'high', f"중복 입고 의심 — {nm.get(a['owner'], a['owner'])} · {a['product_name'][:24]}",
                    f"lot {a['id']}({a['received_at'][:10]}, 등록 {str(a['created_at'])[:10]}) · "
                    f"lot {b['id']}({b['received_at'][:10]}, 등록 {str(b['created_at'])[:10]}) 둘 다 {a['qty_in']}개"
                    + (f" · 한 번도 안 쓴 lot {untouched}" if untouched else ''),
                    a['owner'], [a['id'], b['id']], '실물 확인 후 안 쓴 쪽 입고 취소')

    # 2) 늦게 등록한 입고 — 입고일~등록일 사이 그 상품 발송은 차감되지 않았다
    since = (date.today() - timedelta(days=60)).isoformat()
    late = [l for l in lots if l['product_no'] and str(l['received_at']) >= since
            and _age(l['received_at']) - _age(str(l['created_at'])[:10]) >= 2]
    by_owner = {}
    for l in late:
        by_owner.setdefault(l['owner'], []).append(l)
    for o, ls in by_owner.items():
        d_from = min(str(l['received_at'])[:10] for l in ls)
        disp = _resolved_dispatch(o, d_from)
        for l in ls:
            gap = [r for r in disp if r['costco_no'] == l['product_no']
                   and str(l['received_at'])[:10] <= str(r['dispatched_at'])[:10] <= str(l['created_at'])[:10]
                   and r['order_no'] not in mv_orders]
            if not gap:
                continue      # 그 사이 발송이 없었으면 늦게 넣어도 빠진 게 없다
            add('late', 'mid',
                f"늦게 등록한 입고 — {nm.get(o, o)} · {l['product_name'][:24]}",
                f"lot {l['id']} 입고일 {l['received_at'][:10]}인데 {str(l['created_at'])[:10]}에 등록 · "
                f"그 사이 이 상품 발송 {len(gap)}건은 재고에서 안 빠짐 — 재고에서 보낸 건이면 차감이 필요합니다",
                o, [l['id']], '해당 발송 중 재고에서 보낸 것만 수동 출고',
                rows=[{'order_no': r['order_no'], 'date': str(r['dispatched_at'])[:10],
                       'product_name': r['product_name'] or ''} for r in gap[:30]])

    # 3) 미결제 재고 — 누가 냈는지 정해지지 않아 판매 때 청구 여부가 갈린다
    for l in active:
        if not int(l.get('paid') or 0) and l['owner'] not in admins:   # 관리자 재고는 4)에서
            add('unpaid', 'mid', f"미결제 재고 — {nm.get(l['owner'], l['owner'])} · {l['product_name'][:24]}",
                f"lot {l['id']} 남은 {l['qty_left']}개 · {int(l['qty_left']) * int(l['unit_cost'] or 0):,}원 — "
                "보유자가 이미 냈으면 '결제됨'으로, 아니면 팔릴 때 구입가로 청구됩니다",
                l['owner'], [l['id']], '결제 여부 확인')

    # 4) 관리자 이름 재고 — 주인 없는 영수증 잔량
    for l in active:
        if l['owner'] in admins:
            add('admin', 'mid', f"주인 없는 재고(관리자) — {l['product_name'][:30]}",
                f"lot {l['id']} 남은 {l['qty_left']}개 · {l['received_at'][:10]} 입고 · {l['memo'][:40]}",
                l['owner'], [l['id']], '실제 보유자에게 넘기기 또는 매장 반품')

    # 5) 반품 기한 지남
    for l in active:
        if _age(l['received_at']) > OLD_DAYS:
            add('old', 'low', f"{OLD_DAYS}일 넘은 재고 — {nm.get(l['owner'], l['owner'])} · {l['product_name'][:24]}",
                f"lot {l['id']} 남은 {l['qty_left']}개 · {_age(l['received_at'])}일 경과", l['owner'], [l['id']],
                '판매 계획 확인')

    # 6) 상품번호 없는 재고(빌린 재고 등) — 번호로 차감·검색이 안 된다
    for l in lots:
        if not str(l['product_no'] or '').strip() and int(l['qty_left'] or 0) > 0:
            add('nopno', 'low', f"상품번호 없는 재고 — {nm.get(l['owner'], l['owner'])} · {l['product_name'][:24]}",
                f"lot {l['id']} · {l['status']} · 남은 {l['qty_left']}개 · {l['memo'][:40]}", l['owner'], [l['id']])

    # 7) 수동 조정이 대부분인 입고 — 실제로 어디로 갔는지 기록이 없다
    for l in lots:
        ms = used.get(int(l['id']), [])
        adj = sum(int(m['qty'] or 0) for m in ms if m['platform'] == 'adjust')
        if adj and adj * 2 >= int(l['qty_in'] or 0) and int(l['qty_in'] or 0) >= 3:
            add('adjust', 'low', f"수동 조정으로 비운 입고 — {nm.get(l['owner'], l['owner'])} · {l['product_name'][:24]}",
                f"lot {l['id']} 입고 {l['qty_in']}개 중 {adj}개를 수동 조정으로 뺌 — 어느 주문으로 나갔는지 기록 없음",
                l['owner'], [l['id']])

    # 8) 발송 차감됐는데 주문이 취소/반품 — 재고가 돌아오지 않았다
    lot_by = {int(l['id']): l for l in lots}
    by_seller = {}
    for m in moves:
        if m['platform'] in DISPATCH_PLATFORMS:
            by_seller.setdefault(m['seller'], []).append(m)
    with _auth() as c:
        try:
            cr = {r['order_no'] for r in c.execute("SELECT order_no FROM customer_return")}
        except sqlite3.Error:
            cr = set()
    for s, ms in by_seller.items():
        uc = _user_conn(s)
        if uc is None:
            continue
        try:
            for m in ms:
                st = uc.execute("SELECT status FROM order_history WHERE order_no=? LIMIT 1",
                                (m['order_no'],)).fetchone()
                st = (st['status'] if st else '') or ''
                l = lot_by.get(int(m['lot_id'])) or {}
                if st in CANCEL_STATUS:
                    add('cancel', 'high', f"취소된 주문인데 재고 차감 — {nm.get(s, s)} · {str(l.get('product_name', ''))[:24]}",
                        f"주문 {m['order_no']} ({st}) · lot {m['lot_id']}에서 {m['qty']}개 빠짐 — 물건이 안 나갔으면 복구",
                        l.get('owner', ''), [m['lot_id']], '재고 복구')
                elif st in RETURN_STATUS and m['order_no'] not in cr:
                    add('return', 'mid', f"반품된 주문 — 반품입고 기록 없음 · {nm.get(s, s)} · {str(l.get('product_name', ''))[:24]}",
                        f"주문 {m['order_no']} ({st}) · lot {m['lot_id']}에서 {m['qty']}개 빠짐 — "
                        "물건이 돌아왔으면 '고객 반품'에 등록", l.get('owner', ''), [m['lot_id']], '고객 반품 등록')
        except sqlite3.Error:
            pass
        finally:
            uc.close()

    # 9) 코스트코 번호를 못 찾은 발송(14일) — 재고가 있어도 차감을 건너뛴다
    d14 = (date.today() - timedelta(days=14)).isoformat()
    unres = []
    for u in sorted({l['owner'] for l in active} | set(by_seller)):
        rows = _resolved_dispatch(u, d14)
        n = [r for r in rows if not r['costco_no']]
        if n:
            unres.append({'username': u, 'name': nm.get(u, u), 'total': len(rows), 'unresolved': len(n),
                          'samples': sorted({str(r['product_name'] or '')[:30] for r in n})[:8]})
    for x in unres:
        add('unresolved', 'mid' if x['unresolved'] * 5 >= x['total'] else 'low',
            f"코스트코 번호 못 찾은 발송 — {x['name']} {x['unresolved']}/{x['total']}건(14일)",
            "이 발송들은 재고가 있어도 차감되지 않습니다 · 예: " + ' / '.join(x['samples'][:4]),
            x['username'], fix='가격 DB에서 코스트코 번호 연결')

    order = {'high': 0, 'mid': 1, 'low': 2}
    items.sort(key=lambda i: (order.get(i['level'], 3), i['kind']))
    counts = {}
    for i in items:
        counts[i['kind']] = counts.get(i['kind'], 0) + 1
    res = {'items': items, 'counts': counts, 'checked_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
           'names': nm}
    _CACHE[key] = (time.time(), res)
    return res
