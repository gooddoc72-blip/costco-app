# -*- coding: utf-8 -*-
"""영수증 정산 — 화면과 떨어진 처리 함수 (Next.js 새 화면 /m/receipt-settle 이 쓴다).

Streamlit 화면은 매칭 결과(배치)와 사람이 고른 처리를 세션 메모리에 둬서, 서버가
다시 시작되거나 창을 닫으면 사라졌다. 여기서는 날짜별 **작업본**을 auth.db의
settle_work 표에 저장한다 — 다른 PC에서 열어도, 재시작해도 그대로 이어진다.

계산은 새로 만들지 않는다. Streamlit '🔎 당일 자동배치 미리보기'·배치 상세·정산 요청이
부르던 함수(receipt_settle·settle_core·db_settle·db_online_purchase)를 같은 순서로 부른다.
"""
import json
from datetime import datetime

import receipt_settle as _rs
import settle_core as _sc
import db_settle as _ds
import db_online_purchase as _op
from db_core import get_auth_db


# ── 작업본 저장 ────────────────────────────────────────────────
def _ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS settle_work (
        settle_date TEXT PRIMARY KEY,
        data_json   TEXT NOT NULL,
        updated_by  TEXT DEFAULT '',
        updated_at  TEXT DEFAULT ''
    )""")


def load_work(d):
    conn = get_auth_db()
    try:
        _ensure(conn)
        r = conn.execute("SELECT data_json, updated_by, updated_at FROM settle_work WHERE settle_date=?",
                         (str(d),)).fetchone()
        if not r:
            return None
        w = json.loads(r[0])
        w['_updated_by'], w['_updated_at'] = r[1], r[2]
        return w
    finally:
        conn.close()


def save_work(d, alloc, by=''):
    data = {k: v for k, v in (alloc or {}).items() if not str(k).startswith('_updated')}
    conn = get_auth_db()
    try:
        _ensure(conn)
        conn.execute("INSERT OR REPLACE INTO settle_work (settle_date, data_json, updated_by, updated_at) "
                     "VALUES (?,?,?,?)",
                     (str(d), json.dumps(data, ensure_ascii=False, default=str), str(by or ''),
                      datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    finally:
        conn.close()


def clear_work(d):
    conn = get_auth_db()
    try:
        _ensure(conn)
        conn.execute("DELETE FROM settle_work WHERE settle_date=?", (str(d),))
        conn.commit()
    finally:
        conn.close()


def receipt_items(d):
    from db import get_receipt_items_by_date
    return get_receipt_items_by_date('', str(d)) or []


def _key(r):
    return (str(r.get('username') or ''), str(r.get('order_no') or ''))


def dispatched_keys(d):
    """그날 발송된 (사용자, 주문번호) — 매칭 뒤 새로 발송된 건을 찾는다."""
    from db_core import get_user_db
    out = set()
    for u in _rs.settle_users(str(d)):
        try:
            c = get_user_db(u)
            for (o,) in c.execute("SELECT order_no FROM dispatch_log WHERE dispatched_at LIKE ?",
                                  (str(d)[:10] + '%',)):
                out.add((u, str(o)))
            c.close()
        except Exception:
            continue
    return out


# ── 매칭 실행 (Streamlit '🔎 당일 자동배치 미리보기'와 같은 순서) ──────
def run_match(d, by='', carry_days=0):
    d = str(d)
    items = receipt_items(d)
    prev = load_work(d) or {}
    try:
        import dispatch_upload as _du
        for u in _rs.settle_users(d):
            _du.relink_no_orders(u, d)
    except Exception:
        pass
    pool = _rs.build_stock_pool(d, exclude_dates=[d])
    settled = _ds.settled_order_keys(exclude_date=d)
    try:
        settled = settled | (_op.keys_all() or set())
    except Exception:
        pass
    try:
        sqmap = _ds.settled_qty_map(exclude_date=d)
    except Exception:
        sqmap = None
    alloc = _rs.allocate_dispatched_to_receipt(items, d, stock_pool=pool, exclude_orders=settled,
                                               defer_stock=True, settled_qty=sqmap)
    # 사람이 정해 둔 처리(초안)를 다시 얹는다 — 고친 행은 자동매칭을 덮어쓴다
    sticky = []
    try:
        sticky = list(_ds.get_draft(d) or [])
    except Exception:
        pass
    if sticky:
        edited = {_key(r): r for r in sticky if r.get('_edited')}
        for ar in alloc['rows']:
            if _key(ar) in edited:
                ar.update(edited[_key(ar)])
        have = {_key(r) for r in alloc['rows']}
        re_ = [r for r in sticky if _key(r) not in have]
        if re_:
            _rs.merge_matches(alloc, re_, [])
            alloc['_restored'] = len(re_)
    # 그날 표시해 둔 온라인몰 직배송 건
    try:
        onl = _op.get_by_date(d) or []
    except Exception:
        onl = []
    if onl:
        have2 = {_key(r) for r in alloc['rows']}
        orows = _rs.build_online_rows([o for o in onl if _key(o) not in have2], d)
        if orows:
            _rs.merge_matches(alloc, orows, [])
            alloc['_online_restored'] = len(orows)
    if pool:
        try:
            n = _rs.apply_stock_carry(alloc, pool)
            if n:
                alloc['_stock_carry'] = n
        except Exception:
            pass
    if carry_days:
        try:
            have = {_key(r) for r in alloc.get('rows') or []} | \
                   {_key(o) for o in alloc.get('unmatched_orders') or []}
            carry = _rs.carryover_unsettled(d, days=int(carry_days), exclude=have)
            if carry:
                alloc['unmatched_orders'] = list(alloc.get('unmatched_orders') or []) + carry
                alloc['_carried'] = len(carry)
        except Exception:
            pass
    # 전에 사람이 정한 것 — 끊은 줄(재고에서 나감·끊기)과 '영수증가로 청구' 확인 — 은
    #   다시 매칭해도 그대로 둔다. 안 그러면 매칭을 다시 누를 때마다 처음으로 돌아갔다.
    _cut = {tuple(k.split('	', 1)): v for k, v in (prev.get('_cut') or {}).items()}
    if _cut:
        _unmatch(alloc, d, set(_cut), items, keep_draft=True)
        for o in alloc.get('unmatched_orders') or []:
            if _key(o) in _cut:
                o['_pre'] = _cut[_key(o)]
    alloc['_cut'] = prev.get('_cut') or {}
    alloc['_disp_keys'] = sorted(dispatched_keys(d))
    alloc['_ov_ok'] = prev.get('_ov_ok') or []
    alloc['_matched_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    alloc['user_summary'] = _rs._summarize(alloc['rows'])
    save_work(d, alloc, by)
    return alloc


# ── 배치 줄 처리 ───────────────────────────────────────────────
def _unmatch(alloc, d, keys, items, keep_draft=False):
    """배치에서 떼어 '영수증에 없는 발송건'으로 되돌린다(Streamlit _unmatch_rows와 같은 일)."""
    drop = [r for r in alloc.get('rows', []) if _key(r) in keys]
    alloc['rows'] = [r for r in alloc.get('rows', []) if _key(r) not in keys]
    if not keep_draft:
        try:
            _ds.delete_draft_rows(d, list(keys))
        except Exception:
            pass
    by_u = {}
    for r in drop:
        if str(r.get('via') or '') == 'online':
            by_u.setdefault(str(r.get('username') or ''), []).append(str(r.get('order_no') or ''))
    for u, nos in by_u.items():
        try:
            _op.unmark(u, nos)
        except Exception:
            pass
    have = {_key(o) for o in alloc.get('unmatched_orders', [])}
    for r in drop:
        if str(r.get('via') or '') == 'memo' or _key(r) in have:
            continue
        alloc.setdefault('unmatched_orders', []).append({
            'username': r.get('username', ''), 'order_no': r.get('order_no', ''),
            'order_date': r.get('order_date', ''), 'recipient': r.get('recipient', ''),
            'product_name': r.get('product_name', ''), 'naver_no': r.get('naver_no', ''),
            'qty': int(r.get('qty') or 1), 'prev_cost': int(r.get('prev_cost') or 0),
            'split_qty': max(1, int(r.get('split_qty') or 1)),
            'costco_no': str(r.get('costco_no') or ''),
        })
        have.add(_key(r))
    used = {str(r.get('costco_no') or '') for r in alloc['rows']}
    cur = {str(u['상품번호']) for u in alloc.get('unmatched_receipt', [])}
    for it in (items or []):
        c = _rs._norm(it.get('상품번호'))
        if not c or c in used or c in cur:
            continue
        alloc.setdefault('unmatched_receipt', []).append({
            '상품번호': c, '상품명': _rs._norm(it.get('상품명')),
            '단가': int(it.get('단가') or 0), '영수증수량': int(it.get('수량') or 1)})
        cur.add(c)
    alloc['user_summary'] = _rs._summarize(alloc['rows'])
    return len(drop)


def row_actions(d, actions, by=''):
    """actions: [{username, order_no, action: ok|unok|stock|cut|edit, qty?, amount?, memo?}]"""
    d = str(d)
    alloc = load_work(d)
    if not alloc:
        raise ValueError('먼저 매칭을 실행하세요.')
    items = receipt_items(d)
    ok = {tuple(k) for k in alloc.get('_ov_ok') or []}
    cut, pre, edits = set(), {}, []
    for a in actions or []:
        k = (str(a.get('username') or ''), str(a.get('order_no') or ''))
        act = str(a.get('action') or '')
        if act == 'ok':
            ok.add(k)
        elif act == 'unok':
            ok.discard(k)
        elif act in ('stock', 'cut'):
            ok.discard(k)
            cut.add(k)
            if act == 'stock':
                pre[k] = 'stock'
        elif act == 'edit':
            for r in alloc['rows']:
                if _key(r) == k:
                    if a.get('qty') is not None:
                        r['qty'] = max(1, int(a['qty']))
                    if a.get('amount') is not None:
                        r['amount'] = max(0, int(a['amount']))
                        r['unit_price'] = r['amount']
                    if a.get('memo') is not None:
                        r['memo'] = str(a['memo'])
                    r['_edited'] = True
                    edits.append(dict(r))
    n_cut = _unmatch(alloc, d, cut, items) if cut else 0
    _cm = dict(alloc.get('_cut') or {})
    for k in cut:
        _cm['	'.join(k)] = pre.get(k, 'cut')
    alloc['_cut'] = _cm
    for o in alloc.get('unmatched_orders') or []:
        if _key(o) in pre:
            o['_pre'] = pre[_key(o)]
    if edits:
        try:
            _ds.upsert_draft_rows(d, edits, created_by=by)
        except Exception:
            pass
    alloc['_ov_ok'] = sorted(ok)
    alloc['user_summary'] = _rs._summarize(alloc['rows'])
    save_work(d, alloc, by)
    return {'cut': n_cut, 'edited': len(edits), 'ok': len(ok)}


# ── 화면에 보낼 내용 ───────────────────────────────────────────
def money_flow(items, alloc, d):
    """💰 영수증 돈은 어디로 / 청구 돈은 어디서 (Streamlit 대조 패널과 같은 셈)."""
    r_total = 0
    for it in items or []:
        try:
            r_total += int(float(it.get('단가') or 0)) * max(1, int(it.get('수량') or 1))
        except (TypeError, ValueError):
            continue
    rows = alloc.get('rows') or []
    goods = sum(int(r.get('amount') or 0) for r in rows)
    onl = self_stk = other_stk = manual = 0
    owners = {}
    for r in rows:
        v, amt = str(r.get('via') or ''), int(r.get('amount') or 0)
        u = str(r.get('username') or '')
        ow = str(r.get('stock_owner') or r.get('loan_owner') or '').strip()
        if v == 'online':
            onl += amt
        elif v == 'stock':
            if ow and ow != u:
                other_stk += amt
                owners[ow] = owners.get(ow, 0) + amt
            else:
                self_stk += amt
        elif v == 'manual':
            manual += amt
    rcpt_bill = goods - onl - self_stk - other_stk - manual
    try:
        import db_inventory as _inv
        stock_in = sum(int(l.get('unit_cost') or 0) * int(l.get('qty_in') or 0)
                       for l in _inv.find_receipt_lots(str(d), str(d)))
    except Exception:
        stock_in = 0
    try:
        import db_receipt_return as _rr
        ret = int((_rr.day_total(str(d)) or {}).get('amount') or 0)
    except Exception:
        ret = 0
    return {
        'receipt_total': r_total, 'receipt_bill': rcpt_bill, 'stock_in': stock_in,
        'returned': ret, 'undecided': r_total - rcpt_bill - stock_in - ret,
        'bill_total': goods, 'self_stock': self_stk, 'other_stock': other_stk,
        'manual': manual, 'online': onl,
        'owners': [{'username': o, 'amount': a} for o, a in sorted(owners.items(), key=lambda kv: -kv[1])],
    }


VIA_LABEL = {
    'order': '주문 코스트코번호', 'shopping': '장보기 목록', 'shopping-name': '장보기 상품명',
    'number': '제품DB 번호', 'name': '상품명 유사', 'stock': '재고 출고', 'online': '온라인몰',
    'manual': '금액 지정', 'memo': '배정 청구', 'ai': 'AI 매칭',
}


def view(d):
    d = str(d)
    from db import get_all_users
    names = {u['username']: (str(u.get('display_name') or '').strip() or u['username'])
             for u in get_all_users()}
    items = receipt_items(d)
    alloc = load_work(d)
    out = {'date': d, 'receipt_count': len(items), 'matched': bool(alloc), 'names': names}
    if not alloc:
        return out
    rows = alloc.get('rows') or []
    ok = {tuple(k) for k in alloc.get('_ov_ok') or []}
    flag = {}
    try:
        for o in _rs.receipt_overflow(items, rows):
            for r in o['orders']:
                flag[_key(r)] = f"영수증 {o['units_in']} · 주문 {o['units_used']}"
    except Exception:
        pass
    list_by = {_rs._norm(x.get('상품번호')): int(x.get('정가단가') or x.get('단가') or 0) for x in items}
    out_rows = []
    for r in rows:
        k = _key(r)
        out_rows.append({
            'username': k[0], 'name': names.get(k[0], k[0]), 'order_no': k[1],
            'order_date': str(r.get('order_date') or ''), 'product_name': str(r.get('product_name') or ''),
            'costco_no': str(r.get('costco_no') or ''), 'qty': int(r.get('qty') or 1),
            'unit_price': int(r.get('unit_price') or 0), 'list_price': list_by.get(_rs._norm(r.get('costco_no')), 0),
            'amount': int(r.get('amount') or 0), 'prev_cost': int(r.get('prev_cost') or 0),
            'via': str(r.get('via') or ''), 'via_label': VIA_LABEL.get(str(r.get('via') or ''), str(r.get('via') or '')),
            'memo': str(r.get('memo') or ''), 'stock_owner': str(r.get('stock_owner') or r.get('loan_owner') or ''),
            'edited': bool(r.get('_edited')), 'flag': flag.get(k, ''), 'ok': k in ok,
        })
    out_rows.sort(key=lambda x: (x['flag'] == '' or x['ok'], x['name'], x['order_no']))
    users = {}
    for r in out_rows:
        u = users.setdefault(r['username'], {'username': r['username'], 'name': r['name'],
                                             'count': 0, 'amount': 0, 'unmatched': 0})
        u['count'] += 1
        u['amount'] += r['amount']
    um = []
    for o in alloc.get('unmatched_orders') or []:
        u = str(o.get('username') or '')
        users.setdefault(u, {'username': u, 'name': names.get(u, u), 'count': 0, 'amount': 0, 'unmatched': 0})
        users[u]['unmatched'] += 1
        um.append({'username': u, 'name': names.get(u, u), 'order_no': str(o.get('order_no') or ''),
                   'recipient': str(o.get('recipient') or ''), 'product_name': str(o.get('product_name') or ''),
                   'qty': int(o.get('qty') or 1), 'carried_from': str(o.get('carried_from') or ''),
                   'pre': str(o.get('_pre') or '')})
    paid = []
    try:
        for inv in _ds.list_invoices(d) or []:
            if str(inv.get('status') or '') == 'paid':
                paid.append({'username': inv['username'], 'name': names.get(inv['username'], inv['username']),
                             'amount': int(inv.get('paid_amount') or inv.get('total_amount') or 0)})
    except Exception:
        pass
    try:
        late = sorted(dispatched_keys(d) - {tuple(k) for k in alloc.get('_disp_keys') or []})
    except Exception:
        late = []
    undecided = [r for r in out_rows if r['flag'] and not r['ok']]
    out.update({
        'matched_at': alloc.get('_matched_at', ''), 'updated_at': alloc.get('_updated_at', ''),
        'updated_by': alloc.get('_updated_by', ''),
        'rows': out_rows, 'users': sorted(users.values(), key=lambda x: -x['amount']),
        'unmatched_orders': um,
        'unmatched_receipt': [{'costco_no': str(x.get('상품번호') or ''), 'name': str(x.get('상품명') or ''),
                               'price': int(x.get('단가') or 0), 'qty': int(x.get('영수증수량') or 1)}
                              for x in alloc.get('unmatched_receipt') or []],
        'money': money_flow(items, alloc, d),
        'paid_invoices': paid,
        'late_dispatch': [{'username': u, 'name': names.get(u, u), 'order_no': o} for u, o in late],
        'undecided': len(undecided),
        'total': sum(r['amount'] for r in out_rows),
        'notes': {k: alloc.get(k) for k in ('_restored', '_online_restored', '_stock_carry', '_carried') if alloc.get(k)},
    })
    try:
        out['unmatched_detail'] = unmatched_view(alloc, items, names)
    except Exception as e:
        out['unmatched_detail'] = {'orders': [], 'owners': [], 'error': str(e)[:200]}
    try:
        out['assign'] = assign_view(alloc, items, d, names)
    except Exception as e:
        out['assign'] = {'rows': [], 'error': f'남은 품목을 계산하지 못했습니다: {str(e)[:200]}'}
    return out


# ── 2단계 ① 영수증에 없는 발송건 — 무엇이었는지 정한다 ─────────────────
#   Streamlit _render_unmatched_panel과 같은 규칙이다(재고 보유자 자동 추천, 자동 금액,
#   장부에 없는 남의 물건 빌려 보냄, 후보 연결, 청구 안 함). 화면 코드 안에 있던 셈을
#   여기로 옮겨 새 화면이 같은 결과를 내게 한다.
def _n(s):
    return str(s or '').strip()


def _stock_book():
    """({상품번호: {보유자: {qty, cost, list}}}, 이름 찾기 후보 목록)"""
    import db_inventory as _inv
    stock, names = {}, []
    for sr in (_inv.get_stock_summary() or []):
        pn = _n(sr.get('product_no'))
        if not pn:
            continue
        stock.setdefault(pn, {})[str(sr.get('owner') or '')] = {
            'qty': int(sr.get('qty_left') or 0), 'cost': int(sr.get('unit_cost') or 0),
            'list': int(sr.get('list_price') or 0)}
        names.append({'_pn': pn, 'name': str(sr.get('product_name') or '')})
    # 다 쓴 입고분 이름까지 본다 — 같은 상품이 입고마다 다른 이름으로 들어온다
    try:
        c = _inv._conn()
        names += [{'_pn': _n(r['product_no']), 'name': str(r['product_name'] or ''), 'owner': str(r['owner'] or '')}
                  for r in c.execute("SELECT DISTINCT product_no, product_name, owner FROM inventory_lots "
                                     "WHERE status='ACTIVE' AND TRIM(COALESCE(product_no,''))<>''")]
        c.close()
    except Exception:
        pass
    seen, cands = set(), []
    for x in names:
        if (x['_pn'], x['name']) not in seen:
            seen.add((x['_pn'], x['name']))
            cands.append(x)
    return stock, cands


class _Unmatched:
    """미분류 발송건 처리에 필요한 재고·영수증 정보를 한 번 읽어 둔다."""

    def __init__(self, alloc, items):
        import db_inventory as _inv
        self.inv = _inv
        self.stock, self.cands = _stock_book()
        self.price_by = {}
        for it in items or []:
            c = _rs._norm(it.get('상품번호'))
            if c:
                try:
                    self.price_by[c] = int(float(it.get('단가') or 0))
                except (TypeError, ValueError):
                    pass
        self.left_r = [(_rs._norm(x.get('상품번호')), str(x.get('상품명') or ''))
                       for x in (alloc.get('unmatched_receipt') or []) if _rs._norm(x.get('상품번호'))]

    def stock_pn(self, o):
        """이 주문이 쓸 재고 상품번호 — 번호가 없으면 판매자 본인 입고분 이름부터 찾는다."""
        c = _rs._norm(o.get('costco_no'))
        if c and c in self.stock:
            return c
        nm, me = str(o.get('product_name') or ''), str(o.get('username') or '')
        for pool in ([x for x in self.cands if x.get('owner') == me], self.cands):
            if not pool:
                continue
            try:
                b, _ = _rs.best_name_match(nm, pool)
            except Exception:
                b = None
            if b:
                return b['_pn']
        return c

    def auto_owner(self, cno, me):
        h = self.stock.get(cno) or {}
        if (h.get(me) or {}).get('qty', 0) > 0:
            return me
        return max(h, key=lambda k: h[k]['qty']) if h else ''

    def auto_amount(self, o, cno, owner, qty):
        """본인 재고: 아직 안 낸 몫만 · 남의 재고: 할인가로 산 것 +1,000원, 정상가면 산 값."""
        v = (self.stock.get(cno) or {}).get(owner) or {}
        c = int(v.get('cost') or 0)
        if not owner or not c:
            return 0
        if owner == str(o.get('username') or ''):
            try:
                return int(self.inv.own_unpaid_units(owner, cno, qty, order_no=str(o.get('order_no') or ''))
                           .get('unpaid_amount') or 0)
            except Exception:
                return c * qty
        return (c + self.inv.cross_surcharge_for(c, v.get('list'))) * qty

    def candidate(self, o):
        """그날 영수증에 남은 품목 중 이름이 비슷한 것(영문·한글 표기 차이) — 사람이 보고 고른다."""
        from services import name_score
        best = (0.0, '', '')
        for c, nm in self.left_r:
            try:
                s = name_score(str(o.get('product_name') or ''), nm)[0]
            except Exception:
                s = 0.0
            if s > best[0]:
                best = (s, c, nm)
        return best if best[0] >= 0.3 else (0.0, '', '')


def unmatched_view(alloc, items, names):
    """영수증에 없는 발송건 — 건마다 재고 현황·자동 보유자·자동 금액·같은 물건 후보."""
    un = list(alloc.get('unmatched_orders') or [])
    if not un:
        return {'orders': [], 'owners': []}
    U = _Unmatched(alloc, items)
    out = []
    for o in un:
        cno = U.stock_pn(o)
        me = str(o.get('username') or '')
        qty = int(o.get('qty') or 1)
        own = U.auto_owner(cno, me)
        cand = U.candidate(o)
        have = U.stock.get(cno) or {}
        out.append({
            'username': me, 'name': names.get(me, me), 'order_no': str(o.get('order_no') or ''),
            'recipient': str(o.get('recipient') or ''), 'product_name': str(o.get('product_name') or ''),
            'qty': qty, 'carried_from': str(o.get('carried_from') or ''), 'pre': str(o.get('_pre') or ''),
            'stock_no': cno, 'auto_owner': own, 'auto_amount': U.auto_amount(o, cno, own, qty),
            'stock': [{'owner': ow, 'name': names.get(ow, ow), 'qty': v['qty'], 'cost': v['cost']}
                      for ow, v in sorted(have.items(), key=lambda kv: -kv[1]['qty'])],
            'receipt_price': U.price_by.get(_rs._norm(o.get('costco_no')), 0),
            'candidate': ({'costco_no': cand[1], 'name': cand[2], 'price': U.price_by.get(cand[1], 0)}
                          if cand[1] else None),
        })
    holders = sorted({ow for v in U.stock.values() for ow in v})
    try:
        from db import get_all_users
        active = [u['username'] for u in get_all_users() if (u.get('status') or 'active') == 'active']
    except Exception:
        active = []
    owners = ([{'username': u, 'name': names.get(u, u), 'in_ledger': True} for u in holders]
              + [{'username': u, 'name': names.get(u, u), 'in_ledger': False}
                 for u in sorted(set(active) - set(holders), key=lambda u: names.get(u, u))])
    return {'orders': out, 'owners': owners}


def apply_unmatched(d, decisions, by=''):
    """decisions: [{username, order_no, how: stock|amount|link|skip, owner?, qty?, amount?, memo?}]

    반환: {added, amount, skipped, zero, no_ledger:[상품명], loans}
    금액 0원으로 남는 건은 넣지 않는다(0원 청구는 그대로 손실) — 본인 결제된 재고는 예외.
    """
    d = str(d)
    alloc = load_work(d)
    if not alloc:
        raise ValueError('먼저 매칭을 실행하세요.')
    items = receipt_items(d)
    U = _Unmatched(alloc, items)
    un = {_key(o): o for o in alloc.get('unmatched_orders') or []}
    new_rows, skip_keys, zero, no_ledger = [], [], 0, []
    for a in decisions or []:
        k = (str(a.get('username') or ''), str(a.get('order_no') or ''))
        o = un.get(k)
        how = str(a.get('how') or '')
        if not o or how not in ('stock', 'amount', 'link', 'skip'):
            continue
        if how == 'skip':
            skip_keys.append(k)
            continue
        o = dict(o)
        qty = max(1, int(a.get('qty') or o.get('qty') or 1))
        o['qty'] = qty
        memo = str(a.get('memo') or '').strip()
        amt = max(0, int(a.get('amount') or 0))
        me = k[0]
        if how == 'link':
            _, cc, cnm = U.candidate(o)
            if not cc or not U.price_by.get(cc):
                zero += 1
                continue
            lr = _rs.build_manual_rows([{'order': o, 'costco_no': cc, 'unit_price': U.price_by[cc], 'via': 'name'}])[0]
            lr['memo'] = memo or f'후보 연결 · {cnm}'
            lr['confirmed'] = True
            new_rows.append(lr)
            continue
        cno = U.stock_pn(o)
        stk_owner, loan, cost_basis = '', False, 0
        if how == 'stock':
            own = str(a.get('owner') or '') or U.auto_owner(cno, me)
            have = U.stock.get(cno) or {}
            cost = int((have.get(own) or {}).get('cost') or 0)
            cross = bool(own and own != me)
            in_ledger = bool(own and own in have)
            own_paid = False
            if not amt:
                if own and not cross:
                    try:
                        ou = U.inv.own_unpaid_units(own, cno, qty, order_no=k[1])
                    except Exception:
                        ou = {'unpaid_units': qty, 'unpaid_amount': cost * qty, 'paid_units': 0}
                    amt = int(ou.get('unpaid_amount') or 0)
                    cost_basis = cost * qty
                    own_paid = int(ou.get('paid_units') or 0) > 0
                elif in_ledger or not own:
                    amt = U.auto_amount(o, cno, own, qty) if own else 0
                else:
                    amt = 0      # 장부에 그 사람 재고가 없다 — 금액을 적어야 한다
            if amt <= 0 and not own_paid:
                zero += 1
                continue
            loan = bool(cross and not in_ledger and amt > 0)
            if not memo and loan:
                memo = f"빌린 재고 · {own} — 재고 관리 › 정산 장부에서 {own}에게 {amt:,}원 지급"
            if not memo:
                sur = U.inv.cross_surcharge_for(cost, (have.get(own) or {}).get('list')) if cross else 0
                memo = ('재고 출고' + (f' · {own} 재고' if own else '')
                        + ((f' · 할인 재고 +{sur:,}원' if sur else ' · 정상가 재고') if cross else '')
                        + (' · 본인 재고(결제됨) — 청구 없음' if own_paid and not amt
                           else (' · 본인 재고 일부 결제됨' if own_paid else '')))
            via = 'stock'
            stk_owner = own if (own and (own in have or own_paid)) else ''
            if not stk_owner and not loan:
                no_ledger.append(str(o.get('product_name') or '')[:20])
        else:
            if amt <= 0:
                zero += 1
                continue
            via = 'manual'
        new_rows.append({
            'username': me, 'order_no': o.get('order_no', ''), 'order_date': o.get('order_date', '') or d,
            'costco_no': (str(cno or '') if stk_owner else str(o.get('costco_no') or '')),
            'naver_no': o.get('naver_no', ''), 'product_name': o.get('product_name', ''),
            'qty': qty, 'unit_price': amt, 'amount': amt, 'prev_cost': int(o.get('prev_cost') or 0),
            'via': via, 'split_qty': 1, 'pack': 1, 'memo': memo, 'stock_owner': stk_owner,
            'loan_owner': (str(a.get('owner') or '') if loan else ''), 'cost_basis': cost_basis})
    if skip_keys:
        _rs.dismiss_carry(skip_keys, note=f'{d} 영수증 정산에서 청구 안 함')
        sk = set(skip_keys)
        alloc['unmatched_orders'] = [o for o in alloc.get('unmatched_orders') or [] if _key(o) not in sk]
    if new_rows:
        _rs.merge_matches(alloc, new_rows, [])
        try:
            _ds.upsert_draft_rows(d, new_rows, created_by=by)
        except Exception:
            pass
        # 끊어 둔 줄(_cut)이면 표시를 지운다 — 남겨 두면 다시 매칭할 때 또 끊겨 이 처리가 사라진다
        cm = dict(alloc.get('_cut') or {})
        for r in new_rows:
            cm.pop('\t'.join(_key(r)), None)
        alloc['_cut'] = cm
    alloc['user_summary'] = _rs._summarize(alloc['rows'])
    save_work(d, alloc, by)
    return {'added': len(new_rows), 'amount': sum(int(r['amount']) for r in new_rows),
            'skipped': len(skip_keys), 'zero': zero, 'no_ledger': no_ledger,
            'loans': sum(1 for r in new_rows if r.get('loan_owner'))}


# ── 2단계 ② 남은 영수증 품목 — 누구에게 청구 / 누구 재고로 / 반품요청 ─────────
def assign_view(alloc, items, d, names):
    """Streamlit _build_assign_rows와 같은 목록 — 주문 못 찾은 품목 + 팔고 남은 수량(팩 단위).

    그날 이미 재고로 넣은 수량과 반품요청한 수량은 뺀다. 입고 이력을 못 읽으면 목록을
    열지 않는다 — 이미 넣은 재고가 한 번 더 들어간다.
    """
    lefts = _sc.leftovers(items, alloc.get('rows') or [], str(d))
    if not lefts:
        return {'rows': [], 'error': ''}
    lots = _rs.receipt_lot_units(str(d), start=str(d))
    if lots.get('_error'):
        return {'rows': [], 'error': '재고 입고 이력을 읽지 못해 배정 목록을 열 수 없습니다 — 이대로 배정하면 '
                                     '이미 넣은 재고가 한 번 더 들어갑니다. 잠시 뒤 다시 여세요.'}
    try:
        import db_receipt_return as _rr
        rets = _rr.units_by_date(str(d))
    except Exception:
        rets = {}
    price_by, list_by = {}, {}
    for it in items or []:
        c = _rs._norm(it.get('상품번호'))
        if c:
            try:
                price_by[c] = int(float(it.get('단가') or 0))
            except (TypeError, ValueError):
                price_by[c] = 0
            list_by[c] = int(it.get('정가단가') or it.get('단가') or 0)
    out = []
    for l in lefts:
        c = str(l['costco_no'])
        sq = max(1, int(l.get('split_qty') or 1))
        units = int(l.get('units_left') or 0) - int(lots.get(c, 0) or 0) - int(rets.get(c, 0) or 0)
        packs = units // sq
        if packs <= 0:
            continue
        ow = str(l.get('owner') or '')
        out.append({'costco_no': c, 'name': str(l.get('name') or ''),
                    'price': price_by.get(c, int(l.get('unit_price') or 0)), 'list_price': list_by.get(c, 0),
                    'receipt_qty': int(l.get('qty_receipt') or 0), 'packs_left': packs, 'split_qty': sq,
                    'kind': '주문없음' if int(l.get('units_used') or 0) <= 0 else '팔고남음',
                    'requester': ow, 'requester_name': names.get(ow, ow) if ow else ''})
    out.sort(key=lambda r: (-r['packs_left'] * r['price'], r['name']))
    return {'rows': out, 'error': ''}


def apply_assign(d, mode, username, picks, bill_stock=True, by=''):
    """mode: bill(그 사람에게 청구) | stock(그 사람 재고로 입고) | return(반품요청)
    picks: [{costco_no, packs, memo?}] — 수량은 남은 팩 수를 넘지 못한다."""
    d = str(d)
    alloc = load_work(d)
    if not alloc:
        raise ValueError('먼저 매칭을 실행하세요.')
    if mode not in ('bill', 'stock', 'return'):
        raise ValueError('mode는 bill·stock·return 중 하나')
    if mode != 'return' and not username:
        raise ValueError('받을 사용자를 고르세요.')
    items = receipt_items(d)
    av = assign_view(alloc, items, d, {})
    if av['error']:
        raise ValueError(av['error'])
    src = {r['costco_no']: r for r in av['rows']}
    sel = []
    for p in picks or []:
        r = src.get(str(p.get('costco_no') or ''))
        q = int(p.get('packs') or 0)
        if not r or q <= 0:
            continue
        if q > r['packs_left']:
            raise ValueError(f"{r['name']}: 남은 {r['packs_left']}팩보다 많이 배정할 수 없습니다.")
        sel.append((r, q, str(p.get('memo') or '').strip()))
    if not sel:
        raise ValueError('배정할 품목이 없습니다.')
    if mode == 'bill':
        new = _rs.build_memo_rows([{'username': username, 'costco_no': r['costco_no'], 'product_name': r['name'],
                                    'unit_price': r['price'], 'qty': q, 'memo': m} for r, q, m in sel], d)
        if not new:
            raise ValueError('청구 줄을 만들지 못했습니다(사용자·상품번호 확인).')
        _rs.merge_matches(alloc, new, [])
        try:
            _ds.upsert_draft_rows(d, new, created_by=by)
        except Exception:
            pass
        alloc['user_summary'] = _rs._summarize(alloc['rows'])
        save_work(d, alloc, by)
        return {'mode': mode, 'count': len(new), 'amount': sum(int(x.get('amount') or 0) for x in new)}
    if mode == 'stock':
        res = _sc.receive_leftovers(d, [{
            'costco_no': r['costco_no'], 'name': r['name'], 'unit_price': r['price'], 'list_price': r['list_price'],
            'split_qty': r['split_qty'], 'units_left': q * r['split_qty'], 'owner': username} for r, q, m in sel],
            bill=bool(bill_stock), created_by=by)
        return {'mode': mode, 'count': int(res.get('ok') or 0), 'amount': int(res.get('billed') or 0),
                'skipped': int(res.get('skipped') or 0), 'failed': res.get('failed') or [],
                'dupe': res.get('dupe') or []}
    import db_receipt_return as _rr
    res = _rr.add(d, [{'costco_no': r['costco_no'], 'name': r['name'], 'qty': q, 'split_qty': r['split_qty'],
                       'unit_price': r['price'], 'list_price': r['list_price'], 'reason': m} for r, q, m in sel],
                  created_by=by)
    return {'mode': mode, 'count': int(res.get('ok') or 0), 'amount': sum(r['price'] * q for r, q, m in sel)}


# ── 정산 요청 ─────────────────────────────────────────────────
def finalize(d, by='', replace=False):
    d = str(d)
    alloc = load_work(d)
    if not alloc:
        raise ValueError('먼저 매칭을 실행하세요.')
    v = view(d)
    if v.get('undecided'):
        raise ValueError(f"영수증보다 많이 나간 주문 {v['undecided']}건을 먼저 정하세요(배치 상세의 처리).")
    rows = alloc.get('rows') or []
    if not rows:
        raise ValueError('정산할 배치가 없습니다.')
    res = _sc.finalize(d, rows, created_by=by, replace=bool(replace))
    try:
        _ds.clear_draft(d)
    except Exception:
        pass
    clear_work(d)
    return {
        'saved': res.get('saved', 0), 'applied': res.get('applied', 0),
        'users': len(res.get('totals') or {}), 'total': sum((res.get('totals') or {}).values()),
        'dropped': len(res.get('dropped') or []),
        'learned': (res.get('learned') or {}).get('filled', 0),
        'stock': {k: (v if not isinstance(v, list) else len(v)) for k, v in (res.get('stock') or {}).items()},
    }
