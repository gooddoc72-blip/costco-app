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

    rows, matched_costco, unmatched_orders = [], set(), []
    for uname in users:
        nmap = _naver_to_product_map(uname)     # 네이버번호 → 사용자 제품레코드(정확한 코스트코번호)
        try:
            conn = get_user_db(uname)
            ords = conn.execute(
                "SELECT order_no, order_date, recipient, product_no, product_name, qty, cost_price "
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
                # 미매칭 주문 — 수동/AI 매칭 후보로 반환
                unmatched_orders.append({
                    'username': uname, 'order_no': _norm(o['order_no']),
                    'order_date': _norm(o['order_date']), 'recipient': _norm(o['recipient']),
                    'naver_no': onv, 'product_name': nm, 'qty': int(o['qty'] or 1),
                    'prev_cost': int(o['cost_price'] or 0),
                    'split_qty': int((prod or {}).get('split_qty', 1) or 1),
                })
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
    return {'rows': rows, 'unmatched_receipt': unmatched,
            'unmatched_orders': unmatched_orders,
            'user_summary': _summarize(rows)}


def _summarize(rows):
    user_summary = {}
    for r in rows:
        s = user_summary.setdefault(r['username'], {'amount': 0, 'qty': 0, 'count': 0})
        s['amount'] += r['amount']
        s['qty'] += r['qty']
        s['count'] += 1
    return user_summary


def build_manual_rows(pairs):
    """수동/AI 매칭 결과 → 배치행. pairs: [{order(dict), costco_no, unit_price, costco_name}].
    order dict은 unmatched_orders 항목."""
    out = []
    for pr in pairs:
        o = pr['order']
        up = int(pr.get('unit_price', 0) or 0)
        qty = int(o.get('qty', 1) or 1)
        split = max(1, int(o.get('split_qty', 1) or 1))
        out.append({
            'username': o['username'], 'order_no': o['order_no'],
            'order_date': o.get('order_date', ''), 'costco_no': str(pr.get('costco_no', '')),
            'naver_no': o.get('naver_no', ''), 'product_name': o.get('product_name', ''),
            'qty': qty, 'unit_price': up, 'amount': (up // split) * qty,
            'prev_cost': int(o.get('prev_cost', 0) or 0), 'via': pr.get('via', 'manual'),
        })
    return out


def ai_match_receipt_orders(unmatched_receipt, unmatched_orders,
                            anthropic_key='', gemini_key=''):
    """AI로 미매칭 영수증 품목 ↔ 미매칭 주문을 상품명 의미 기준으로 매칭.
    Gemini 키가 있으면 Gemini 우선, 없거나 실패 시 Claude 폴백.
    Returns: (pairs, error).
      pairs: [{order_index, costco_no, unit_price}] (매칭된 것만).
      error: 실패 사유 문자열('' = 정상). API 크레딧 부족 등 실제 오류를 호출자가 표시하도록.
    """
    if not (anthropic_key or gemini_key):
        return [], 'AI 키가 없습니다 (설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키 등록).'
    if not (unmatched_receipt and unmatched_orders):
        return [], ''
    import json
    r_lines = "\n".join(f"R{i}: [{it['상품번호']}] {it['상품명']} ({it['단가']}원)"
                        for i, it in enumerate(unmatched_receipt))
    o_lines = "\n".join(f"O{i}: {o['product_name']} (수량 {o['qty']})"
                        for i, o in enumerate(unmatched_orders))
    system = ("너는 코스트코 영수증 품목과 네이버 스마트스토어 주문 상품명을 같은 상품끼리 "
              "매칭하는 전문가다. 네이버 상품명은 검색 키워드가 잔뜩 붙어 길고, 영수증명은 짧다. "
              "브랜드·핵심 상품종류·용량이 일치하면 적극적으로 매칭한다(키워드 나열은 무시). "
              "명백히 다른 상품만 제외한다. 반드시 JSON만 출력한다.")
    user_msg = (
        f"[영수증 품목]\n{r_lines}\n\n[미매칭 주문]\n{o_lines}\n\n"
        "각 주문을 같은 상품의 영수증 품목에 연결해 JSON 배열로만 출력: "
        "[{\"o\": 주문번호(int), \"r\": 영수증번호(int)}]. "
        "한 영수증 품목에 여러 주문이 연결될 수 있다. 매칭 없으면 []."
    )
    try:
        from ai_service import ai_complete
        txt, err, _prov = ai_complete(system, user_msg, gemini_key=gemini_key,
                                      anthropic_key=anthropic_key, max_tokens=1024)
        if not txt:
            return [], (err or 'AI 응답이 비어 있습니다.')
        s = txt[txt.find('['): txt.rfind(']') + 1]
        pairs = json.loads(s) if s else []
    except Exception as e:
        return [], f'AI 매칭 처리 오류: {e}'
    out = []
    for p in pairs:
        try:
            oi, ri = int(p['o']), int(p['r'])
            if 0 <= oi < len(unmatched_orders) and 0 <= ri < len(unmatched_receipt):
                out.append({'order_index': oi, 'costco_no': str(unmatched_receipt[ri]['상품번호']),
                            'unit_price': int(unmatched_receipt[ri]['단가'])})
        except Exception:
            continue
    return out, ''


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
