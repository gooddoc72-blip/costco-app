"""영수증 정산 — 코스트코 영수증 품목을 각 사용자 주문에 '자동배치'하고,
각 주문의 구입가(cost_price)에 영수증 실단가를 반영한다. (관리자 전용)

핵심 브리지:
  영수증 상품번호 = '코스트코 번호'
  주문 product_no  = '네이버 상품ID'
  → 각 사용자 products 의 product_no(코스트코) ↔ naver_channel_pno/naver_origin_pno(네이버)
    매핑을 통해 연결한다.

비용 공식은 수익계산과 동일:
  구입가 = (영수증단가 // split_qty) * 수량 * 묶음배수(pack)
"""
from db import get_user_db, get_all_users, get_all_products
from services import resolve_pack_factor, _token_score


def _norm(s):
    return str(s or '').strip()


def _naver_to_product_map(username):
    """네이버번호(및 코스트코번호) → 사용자 products 레코드."""
    prods = get_all_products(username) or []
    m = {}
    for p in prods:
        for k in ('naver_channel_pno', 'naver_origin_pno'):
            nv = _norm(p.get(k))
            if nv and nv not in m:
                m[nv] = p
        cn = _norm(p.get('product_no'))
        if cn and cn not in m:      # 주문이 코스트코번호로 저장된 경우 대비
            m[cn] = p
    return m


def _order_cost(receipt_unit, qty, product):
    """수익계산과 동일한 매입가 공식 적용."""
    split, pack = 1, 1
    if product:
        try:
            split = max(1, int(product.get('split_qty', 1) or 1))
        except (TypeError, ValueError):
            split = 1
        name = product.get('store_product_name') or product.get('costco_name') or ''
        pack = resolve_pack_factor(product, name)
    return (int(receipt_unit) // split) * int(qty) * int(pack)


def allocate_receipt_to_orders(receipt_items, date_from, date_to, users=None):
    """영수증 품목을 기간 내 모든 사용자 주문에 코스트코 상품번호로 자동배치.

    Returns:
      {
        'rows': [{username, order_no, order_date, costco_no, naver_no,
                  product_name, qty, unit_price, amount, prev_cost}, ...],
        'unmatched_receipt': [{상품번호, 상품명, 단가}, ...],   # 주문 못 찾은 영수증 품목
        'user_summary': {username: {amount, qty, count}},
      }
    """
    price_by_costco, name_by_costco = {}, {}
    for it in receipt_items:
        cno = _norm(it.get('상품번호'))
        if not cno:
            continue
        try:
            up = int(float(it.get('단가') or 0))
        except (TypeError, ValueError):
            up = 0
        if up > 0:
            price_by_costco[cno] = up
            name_by_costco[cno] = _norm(it.get('상품명'))

    if users is None:
        users = [u['username'] for u in get_all_users()]

    # 이름 폴백용 영수증 목록
    _ritems = [{'costco': c, 'name': name_by_costco.get(c, ''), 'price': price_by_costco[c]}
               for c in price_by_costco]

    rows, matched_costco = [], set()
    for uname in users:
        nmap = _naver_to_product_map(uname)     # 네이버번호 → 사용자 제품레코드(정확한 코스트코번호)
        try:
            conn = get_user_db(uname)
            ords = conn.execute(
                "SELECT order_no, order_date, product_no, product_name, qty, cost_price "
                "FROM order_history WHERE order_date BETWEEN ? AND ?",
                (date_from, date_to)
            ).fetchall()
            conn.close()
        except Exception:
            ords = []
        for o in ords:
            onv = _norm(o['product_no'])
            nm = _norm(o['product_name'])
            prod = nmap.get(onv)
            via = 'number'
            # ① 코스트코번호 브리지 — 제품레코드의 product_no (정확) → 없으면 주문번호 자체
            costco_no = _norm((prod or {}).get('product_no'))
            if costco_no not in price_by_costco:
                costco_no = onv if onv in price_by_costco else ''
            # ② 상품명 유사도 폴백 — 번호로 못 붙은 주문을 영수증 상품명과 매칭
            if costco_no not in price_by_costco:
                _best, _sc = None, 0.0
                for ri in _ritems:
                    s = _token_score(nm, ri['name'])
                    if s > _sc:
                        _sc, _best = s, ri
                if _best and _sc >= 0.55:
                    costco_no = _best['costco']
                    via = 'name'
            if costco_no not in price_by_costco:
                continue
            up = price_by_costco[costco_no]
            qty = int(o['qty'] or 1)
            rows.append({
                'username': uname,
                'order_no': _norm(o['order_no']),
                'order_date': _norm(o['order_date']),
                'costco_no': costco_no,
                'naver_no': onv,
                'product_name': nm,
                'qty': qty,
                'unit_price': up,
                'amount': _order_cost(up, qty, prod),
                'prev_cost': int(o['cost_price'] or 0),
                'via': via,
            })
            matched_costco.add(costco_no)

    unmatched = [
        {'상품번호': c, '상품명': name_by_costco.get(c, ''), '단가': price_by_costco[c]}
        for c in price_by_costco if c not in matched_costco
    ]
    user_summary = {}
    for r in rows:
        s = user_summary.setdefault(r['username'], {'amount': 0, 'qty': 0, 'count': 0})
        s['amount'] += r['amount']
        s['qty'] += r['qty']
        s['count'] += 1
    return {'rows': rows, 'unmatched_receipt': unmatched, 'user_summary': user_summary}


def cleanup_orphan_settlements():
    """정산 항목 중 '해당 사용자 order_history에 더 이상 없는 주문'(삭제됨)을 제거.
    이미 생긴 orphan 일괄 정리용. Returns: {'checked': n, 'removed': n, 'batches': set}."""
    from db_receipt_settle import iter_all_settlement_item_orders, delete_settlement_items_by_id
    items = iter_all_settlement_item_orders()
    if not items:
        return {'checked': 0, 'removed': 0}
    # 사용자별 존재하는 order_no 집합
    by_user = {}
    for it in items:
        by_user.setdefault(_norm(it['username']), set()).add(_norm(it['order_no']))
    existing = {}
    for uname, onos in by_user.items():
        try:
            conn = get_user_db(uname)
            rows = conn.execute(
                "SELECT order_no FROM order_history").fetchall()
            conn.close()
            existing[uname] = {_norm(r[0]) for r in rows}
        except Exception:
            existing[uname] = set()
    orphan_ids, orphan_batches = [], set()
    for it in items:
        uname = _norm(it['username'])
        ono = _norm(it['order_no'])
        if ono and ono not in existing.get(uname, set()):
            orphan_ids.append(int(it['id']))
            orphan_batches.add(it['batch_id'])
    removed = delete_settlement_items_by_id(orphan_ids, list(orphan_batches)) if orphan_ids else 0
    return {'checked': len(items), 'removed': removed, 'batches': orphan_batches}


def apply_receipt_settlement(rows):
    """배치행 amount를 각 사용자 order_history.cost_price(+ profit_settlements)에 반영.
    Returns: 갱신된 주문 수."""
    by_user = {}
    for r in rows:
        by_user.setdefault(r['username'], []).append(r)
    updated = 0
    for uname, urows in by_user.items():
        try:
            conn = get_user_db(uname)
        except Exception:
            continue
        for r in urows:
            ono = _norm(r.get('order_no'))
            amt = int(r.get('amount', 0) or 0)
            if not ono:
                continue
            conn.execute("UPDATE order_history SET cost_price=? WHERE order_no=?", (amt, ono))
            try:
                conn.execute("UPDATE profit_settlements SET cost_price=? WHERE order_no=?", (amt, ono))
            except Exception:
                pass
            updated += 1
        conn.commit()
        conn.close()
    return updated
