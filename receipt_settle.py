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
import os
import sqlite3
from datetime import datetime, timedelta

from db import get_user_db, get_all_users, get_all_products
from services import (resolve_pack_factor, _token_score, is_costco_pno,
                      _combined_match_score)

#: 상품명 유사도 폴백 하한. 네이버 상품명은 SEO 키워드가 붙어 길고 영수증명은
#  축약·영문이라 공통 토큰 비율이 구조적으로 낮다. 0.55에서는 명백히 같은 상품도
#  0.50에서 걸려 탈락했다(8/19 실측: 미매칭 31건 중 14건이 0.50대).
#  0.45·0.40까지 낮춰도 추가로 붙는 건 0건이라 0.50이 경계다.
NAME_MATCH_MIN = 0.50

#: 종합 점수 하한 — 토큰 포함율만으로는 브랜드·용량이 다른 상품을 못 거른다.
#  9/1~9/3 실측에서 정매칭과 오매칭이 깨끗하게 갈렸다:
#    정매칭  이디야 커피믹스 0.61 · 라페르미에르 요거트 0.61 · 라브아섬유탈취제 0.61
#           부샤드 씨솔트 초콜릿 0.61 · 프로틴초코쉐이크 0.43
#    오매칭  풀무원그릭요거트 -> 커클랜드 그릭요거트 0.33 (브랜드가 다른데 14건이 붙었다)
#           트러플 크래커   -> ALLO 선식 크래커   0.28
#           부샤드 씨솔트 초콜릿 -> 몰든 씨솔트 천일염 0.21
#  토큰 점수도 정매칭 1.00 / 오매칭 0.50으로 갈리지만, 과거(8/19)엔 0.50대에도
#  정매칭이 있었다는 기록이 있어 토큰 하한은 그대로 두고 종합 점수를 함께 요구한다.
NAME_COMBINED_MIN = 0.40

#: 1위와 2위가 이만큼도 안 벌어지면 애매한 것으로 보고 붙이지 않는다.
#  억지로 붙이느니 미매칭으로 넘겨 사람이 확인하는 편이 낫다.
NAME_MATCH_MARGIN = 0.08


def best_name_match(order_name, candidates, name_key="name"):
    """상품명으로 최선의 후보 하나 — 애매하면 붙이지 않는다.

    candidates: [{name_key: 영수증 상품명, ...}, ...]
    반환: (best|None, info).  info의 why가 안 붙인 이유다.
    """
    _nm = _norm(order_name)
    if not _nm:
        return None, {"why": "no_name"}
    _scored = []
    for _c in (candidates or []):
        _rn = _norm((_c or {}).get(name_key))
        if not _rn:
            continue
        _t = _token_score(_nm, _rn)
        if _t < NAME_MATCH_MIN:
            continue
        _s = _combined_match_score(_nm, _rn)["total"]
        _scored.append((_s, _t, _c))
    if not _scored:
        return None, {"why": "below_token_min"}
    _scored.sort(key=lambda x: (-x[0], -x[1]))
    _s0, _t0, _best = _scored[0]
    if _s0 < NAME_COMBINED_MIN:
        return None, {"why": "below_combined_min", "score": _s0, "token": _t0}
    if len(_scored) > 1 and (_s0 - _scored[1][0]) < NAME_MATCH_MARGIN:
        return None, {"why": "ambiguous", "score": _s0, "second": _scored[1][0]}
    return _best, {"score": _s0, "token": _t0,
                   "second": (_scored[1][0] if len(_scored) > 1 else 0.0)}


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


def _split_pack(product):
    """(소분수 split_qty, 묶음배수 pack) — 수익계산과 동일 기준."""
    split, pack = 1, 1
    if product:
        try:
            split = max(1, int(product.get('split_qty', 1) or 1))
        except (TypeError, ValueError):
            split = 1
        name = product.get('store_product_name') or product.get('costco_name') or ''
        pack = resolve_pack_factor(product, name)
    return split, int(pack)


def _order_cost(receipt_unit, qty, product):
    """수익계산과 동일한 매입가 공식 적용."""
    split, pack = _split_pack(product)
    return (int(receipt_unit) // split) * int(qty) * pack


def get_settle_start_date():
    """정산 기준일 — 이 날짜 이전의 구매(영수증)는 매칭에 쓰지 않는다.

    과거 데이터는 영수증 업로드가 들쭉날쭉해 재고 계산이 실제와 맞지 않는다.
    기준일부터 규칙대로(매일 구매 → 당일 영수증 업로드 → 당일 정산) 쌓아야
    재고 이월이 신뢰할 수 있는 값이 된다.
    데이터를 지우지는 않는다 — 과거 영수증은 공유DB 매장 카탈로그의 근거다.
    """
    try:
        from db import get_global_setting
        return (get_global_setting("settle_start_date") or "").strip()
    except Exception:
        return ""


def build_stock_pool(date_upto, exclude_dates=None):
    """지정일까지의 '가용 재고'를 계산한다 — {코스트코번호: {units, price, name}}.

    왜 필요한가:
      실제 운영은 당일 산 것만 당일 나가지 않는다. 8/13에 10개 사서 8/13에 3개,
      8/18에 4개 나가는 식이다. 그런데 매칭은 '그날 영수증 ↔ 그날 주문'만 봐서,
      재고에서 나간 주문은 영원히 미매칭으로 남았다(8/18 실측: 미매칭 56건 중
      16건이 과거 영수증에 있는 재고 출고분이었다).

    계산 방식 — 이미 저장된 사실만 쓴다. 별도 장부를 만들지 않아 이중 차감이 없다:
      가용 units = Σ(영수증 구매 qty × split_qty)  −  Σ(정산에 배치된 qty × pack)
    단위는 소분 단위(units)다. 소분 상품은 1팩이 split_qty개로 쪼개져 나간다.
    """
    from db import get_shared_products
    import glob
    from db_core import DATA_DIR

    exclude = {str(d) for d in (exclude_dates or [])}
    split_by = {}
    for sp in (get_shared_products() or []):
        pn = _norm(sp.get('product_no'))
        if pn:
            split_by[pn] = max(1, int(sp.get('split_qty') or 1))

    # ① 입고 — 모든 사용자 DB의 영수증 이력 (지정일 이하, 제외일 빼고)
    pool = {}
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "*.db"))):
        u = os.path.basename(f)[:-3]
        if u == "auth" or ".bak" in u or ".backup" in u:
            continue
        try:
            conn = sqlite3.connect(f)
            conn.row_factory = sqlite3.Row
            _start = get_settle_start_date()
            if _start:
                rows = conn.execute(
                    "SELECT product_no, product_name, unit_price, qty, receipt_date "
                    "FROM receipt_items WHERE receipt_date <= ? AND receipt_date >= ?",
                    (str(date_upto), _start)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT product_no, product_name, unit_price, qty, receipt_date "
                    "FROM receipt_items WHERE receipt_date <= ?", (str(date_upto),)).fetchall()
            conn.close()
        except Exception:
            continue
        for r in rows:
            pn = _norm(r['product_no'])
            rd = _norm(r['receipt_date'])
            if not pn or rd in exclude:
                continue
            e = pool.setdefault(pn, {'units': 0, 'price': 0, 'name': '', 'date': ''})
            e['units'] += int(r['qty'] or 0) * split_by.get(pn, 1)
            if rd >= e['date']:          # 가장 최근 영수증 단가를 쓴다
                e['price'] = int(r['unit_price'] or 0)
                e['name'] = _norm(r['product_name'])
                e['date'] = rd

    # ② 출고 — 이미 정산에 배치된 수량
    try:
        from db_receipt_settle import _conn as _rs_conn, _ensure as _rs_ensure
        c = _rs_conn(); _rs_ensure(c)
        _st = get_settle_start_date()
        _q = ("SELECT costco_no, SUM(qty) FROM receipt_settle_items "
              "WHERE order_date <= ?" + (" AND order_date >= ?" if _st else "") +
              " GROUP BY costco_no")
        _args = (str(date_upto), _st) if _st else (str(date_upto),)
        for pn, used in c.execute(_q, _args):
            pn = _norm(pn)
            if pn in pool:
                pool[pn]['units'] -= int(used or 0)
        c.close()
    except Exception:
        pass

    return {k: v for k, v in pool.items() if v['units'] > 0}


def get_stock_status(date_upto=None):
    """현재 구입재고 현황 — 입고·사용·잔량·재고금액.

    build_stock_pool은 '남은 것'만 돌려주는데, 현황 화면은 입고/사용까지
    보여야 실물과 대조할 수 있다. 같은 원천(영수증 이력·정산 기록)을 쓰되
    차감 전 값도 함께 낸다.
    반환: [{costco_no, name, price, units_in, units_used, units_left, amount}]
    """
    import glob
    from datetime import datetime as _dt
    from db import get_shared_products
    from db_core import DATA_DIR

    d = str(date_upto or _dt.now().strftime("%Y-%m-%d"))
    start = get_settle_start_date()

    split_by = {}
    for sp in (get_shared_products() or []):
        pn = _norm(sp.get('product_no'))
        if pn:
            split_by[pn] = max(1, int(sp.get('split_qty') or 1))

    pool = {}
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "*.db"))):
        u = os.path.basename(f)[:-3]
        if u == "auth" or ".bak" in u or ".backup" in u:
            continue
        try:
            conn = sqlite3.connect(f)
            conn.row_factory = sqlite3.Row
            if start:
                rows = conn.execute(
                    "SELECT product_no,product_name,unit_price,qty,receipt_date FROM receipt_items "
                    "WHERE receipt_date <= ? AND receipt_date >= ?", (d, start)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT product_no,product_name,unit_price,qty,receipt_date FROM receipt_items "
                    "WHERE receipt_date <= ?", (d,)).fetchall()
            conn.close()
        except Exception:
            continue
        for r in rows:
            pn = _norm(r['product_no'])
            if not pn:
                continue
            e = pool.setdefault(pn, {'costco_no': pn, 'name': '', 'price': 0,
                                     'units_in': 0, 'units_used': 0, 'date': ''})
            e['units_in'] += int(r['qty'] or 0) * split_by.get(pn, 1)
            rd = _norm(r['receipt_date'])
            if rd >= e['date']:
                e['price'] = int(r['unit_price'] or 0)
                e['name'] = _norm(r['product_name'])
                e['date'] = rd

    try:
        from db_receipt_settle import _conn as _rc, _ensure as _re
        c = _rc(); _re(c)
        q = ("SELECT costco_no, SUM(qty) FROM receipt_settle_items WHERE order_date <= ?"
             + (" AND order_date >= ?" if start else "") + " GROUP BY costco_no")
        for pn, used in c.execute(q, (d, start) if start else (d,)):
            pn = _norm(pn)
            if pn in pool:
                pool[pn]['units_used'] += int(used or 0)
        c.close()
    except Exception:
        pass

    out = []
    for e in pool.values():
        left = e['units_in'] - e['units_used']
        sq = split_by.get(e['costco_no'], 1)
        out.append({**e, 'units_left': left,
                    'amount': max(0, left) * (e['price'] // max(1, sq))})
    out.sort(key=lambda x: -x['amount'])
    return out


def get_settled_order_keys():
    """이미 다른 배치에서 정산된 (사용자, 주문번호) 집합.

    조회 범위를 전날까지 넓히면 어제 이미 정산한 주문이 다시 잡혀 구입가가
    두 번 덮어써진다. 그걸 막는 제외 목록.
    """
    try:
        from db_receipt_settle import iter_all_settlement_item_orders
        return {(_norm(r.get('username')), _norm(r.get('order_no')))
                for r in (iter_all_settlement_item_orders() or [])}
    except Exception:
        return set()


def build_shopping_bridge(order_date, users=None):
    """그날 장보기 제출목록 → {username: {네이버번호: 코스트코번호}} + 이름 후보.

    이게 핵심 브리지다. 장보기 목록은 '그날 각 사용자가 실제로 사야 했던 상품'을
    코스트코번호와 네이버상품번호로 나란히 들고 있다(8/31 실측: oxo 18품목 중
    코스트코번호 15·네이버번호 18, tblue 20품목 중 8·20). 제품DB 매핑률이 20%대라
    번호 브리지가 자주 끊기는데, 장보기 목록이 그 공백을 그날치로 메운다.

    Returns: {username: {'nv2cno': {네이버번호: 코스트코번호},
                         'items': [{costco, naver, name, qty}...]}}
    """
    import json as _json
    out = {}
    try:
        from db import get_shopping_submissions_detail
        subs = get_shopping_submissions_detail(str(order_date), str(order_date)) or []
    except Exception:
        return out
    for s in subs:
        uname = _norm(s.get('username'))
        if users is not None and uname not in set(users):
            continue
        try:
            items = _json.loads(s.get('items_json') or '[]')
        except Exception:
            items = []
        nv2cno, lst = {}, []
        for it in items:
            if not isinstance(it, dict):
                continue
            cno = _norm(it.get('코스트코상품번호'))
            nv = _norm(it.get('네이버상품번호'))
            nm = _norm(it.get('상품명'))
            if cno and nv:
                nv2cno.setdefault(nv, cno)
            if cno or nv:
                lst.append({'costco': cno, 'naver': nv, 'name': nm,
                            'qty': int(it.get('주문수량') or 0)})
        if nv2cno or lst:
            out[uname] = {'nv2cno': nv2cno, 'items': lst}
    return out


def allocate_dispatched_to_receipt(receipt_items, dispatch_date, users=None,
                                   shopping_date=None, exclude_orders=None,
                                   stock_pool=None):
    """오늘 출고된 주문 × 오늘 영수증 × 그날 장보기 목록 — 3자 매칭.

    정산 대상은 '오늘 일괄발송(출고)한 주문'이다. 돈이 나가는 시점이 발송이고,
    수익계산도 dispatch_log 기준이라 기준이 일치한다. 결제일(order_date)로 잡으면
    마감 이후 주문이 다음날 출고되는 실제 흐름과 어긋나 매칭이 대량으로 깨졌다.

    주문 1건당 매칭 순서:
      ① 장보기 브리지 — 출고건 네이버번호 → 그날 장보기 항목의 코스트코번호
      ② 제품DB 매핑   — products.naver_* → product_no
      ③ 이름 유사도   — 출고 상품명 ↔ 영수증 상품명 (≥ NAME_MATCH_MIN)
      ④ 재고 이월     — 과거 구매분(stock_pool)에서 차감

    Returns: allocate_receipt_to_orders와 동일한 형태(rows/unmatched_*/user_summary).
    """
    from db import get_dispatched_orders_with_details

    price_by_costco, name_by_costco = {}, {}
    for it in (receipt_items or []):
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
    bridge = build_shopping_bridge(shopping_date or dispatch_date, users=users)
    _ritems = [{'costco': c, 'name': name_by_costco.get(c, ''), 'price': price_by_costco[c]}
               for c in price_by_costco]
    _excl = exclude_orders if exclude_orders is not None else set()

    rows, matched_costco, unmatched_orders = [], set(), []
    for uname in users:
        try:
            disp = get_dispatched_orders_with_details(uname, str(dispatch_date)) or []
        except Exception:
            disp = []
        if not disp:
            continue
        nmap = _naver_to_product_map(uname)
        ub = bridge.get(uname) or {'nv2cno': {}, 'items': []}
        for o in disp:
            ono = _norm(o.get('order_no'))
            if _excl and (uname, ono) in _excl:
                continue
            onv = _norm(o.get('product_no'))
            nm = _norm(o.get('product_name'))
            prod = nmap.get(onv)
            costco_no, via = '', ''

            # ① 장보기 목록 브리지 (그날 사야 했던 목록 = 영수증과 같은 근거)
            _c = _norm(ub['nv2cno'].get(onv))
            if _c and _c in price_by_costco:
                costco_no, via = _c, 'shopping'
            # ①-b 장보기 항목 이름으로 코스트코번호 찾기 (네이버번호가 비어 있는 제출분)
            if not costco_no and ub['items']:
                _cands = [si for si in ub['items']
                          if si['costco'] and si['costco'] in price_by_costco]
                _b, _bi = best_name_match(nm, _cands)
                if _b:
                    costco_no, via = _b['costco'], 'shopping-name'
            # ② 제품DB 매핑 → ③ 주문번호 자체
            if not costco_no:
                _c2 = _norm((prod or {}).get('product_no'))
                if _c2 in price_by_costco:
                    costco_no, via = _c2, 'number'
                elif onv in price_by_costco:
                    costco_no, via = onv, 'number'
            # ③ 영수증 상품명 유사도
            if not costco_no:
                _b, _bi = best_name_match(nm, _ritems)
                if _b:
                    costco_no, via = _b['costco'], 'name'

            qty = int(o.get('qty') or 1)
            _sq, _pk = _split_pack(prod)

            # ④ 재고 이월 — 오늘 영수증에 없으면 과거 구매분에서 찾는다
            if not costco_no and stock_pool:
                _cand = _norm((prod or {}).get('product_no')) or onv
                if _cand not in stock_pool:
                    _pool_cands = [{'_pn': _pn, 'name': _e.get('name') or ''}
                                   for _pn, _e in stock_pool.items()]
                    _pb, _pi = best_name_match(nm, _pool_cands)
                    _cand = _pb['_pn'] if _pb else None
                if _cand and stock_pool.get(_cand, {}).get('units', 0) > 0:
                    _need = qty * int(_pk or 1)
                    if stock_pool[_cand]['units'] >= _need:
                        stock_pool[_cand]['units'] -= _need
                        rows.append({
                            'username': uname, 'order_no': ono,
                            'order_date': str(dispatch_date), 'costco_no': _cand,
                            'naver_no': onv, 'product_name': nm, 'qty': qty,
                            'unit_price': int(stock_pool[_cand].get('price') or 0),
                            'amount': _order_cost(int(stock_pool[_cand].get('price') or 0), qty, prod),
                            'prev_cost': int(o.get('cost_price') or 0), 'via': 'stock',
                            'split_qty': _sq, 'pack': _pk,
                        })
                        matched_costco.add(_cand)
                        continue

            if not costco_no:
                unmatched_orders.append({
                    'username': uname, 'order_no': ono,
                    'order_date': str(dispatch_date), 'recipient': _norm(o.get('recipient')),
                    'naver_no': onv, 'product_name': nm, 'qty': qty,
                    'prev_cost': int(o.get('cost_price') or 0),
                    'split_qty': _sq,
                })
                continue

            up = price_by_costco[costco_no]
            rows.append({
                'username': uname, 'order_no': ono, 'order_date': str(dispatch_date),
                'costco_no': costco_no, 'naver_no': onv, 'product_name': nm,
                'qty': qty, 'unit_price': up, 'amount': _order_cost(up, qty, prod),
                'prev_cost': int(o.get('cost_price') or 0), 'via': via,
                'split_qty': _sq, 'pack': _pk,
            })
            matched_costco.add(costco_no)

    unmatched = [
        {'상품번호': c, '상품명': name_by_costco.get(c, ''), '단가': price_by_costco[c]}
        for c in price_by_costco if c not in matched_costco
    ]
    return {'rows': rows, 'unmatched_receipt': unmatched,
            'unmatched_orders': unmatched_orders,
            'user_summary': _summarize(rows)}


def allocate_receipt_to_orders(receipt_items, date_from, date_to, users=None,
                               stock_pool=None, exclude_orders=None, basis='daily',
                               carry_days=0):
    """영수증 품목을 기간 내 모든 사용자 주문에 코스트코 상품번호로 자동배치.

    basis:
      'daily'   — daily_orders(일일 주문건). 사용자가 화면에서 보는 '그날 발송할
                  주문'과 같은 집합이다. **기본값.**
      'payment' — order_history(결제일 기준). 구버전 동작.

    왜 기본을 바꿨나: 두 테이블의 order_date 의미가 다르다.
      daily_orders   = 수집·저장한 날짜 (결제일 무시, db_orders.save_daily_orders)
      order_history  = 결제일        (db_orders.save_order_history)
    영수증은 '그날 장 본 것'이라 '그날 발송할 일일주문'에 대응하는데,
    결제일로 잘라서 대상 집합이 어긋났다. 9/1 실측:
      clglobal0919 일일주문 44건 → order_history에선 9/1 16건뿐
        (나머지는 8/31 13 · 8/30 7 · 8/29 6 · 날짜 'nan' 2)
      oxo 일일주문 13건 → 9/1 4건뿐 (9/2 5 · 9/3 1 · history에 아예 없음 3)
    즉 28건은 영수증과 매칭될 기회조차 없었다.

    carry_days:
      0보다 크면 date_from 이전 carry_days 일까지의 **아직 정산 안 된** 주문도
      후보에 넣는다(미정산 이월). 품절이라 며칠 뒤에 샀거나 주문이 밀린 건을
      잡기 위한 것이다.
      단 이월분은 **코스트코 상품번호가 정확히 일치할 때만** 붙인다.
      이름 유사도와 재고 이월은 적용하지 않는다 — 오래된 주문까지 이름으로
      붙이기 시작하면 오매칭이 급격히 는다. 이월분 미매칭은 부족분 목록에도
      넣지 않는다(원래 정산 기간 밖이라 노이즈가 된다).

    Returns:
      {
        'rows': [{username, order_no, order_date, costco_no, naver_no,
                  product_name, qty, unit_price, amount, prev_cost, via}, ...],
        'unmatched_receipt': [{상품번호, 상품명, 단가}, ...],   # 주문 못 찾은 영수증 품목
        'user_summary': {username: {amount, qty, count}},
        'carry': {'scanned': 훑은 이월 주문 수, 'matched': 이월로 붙은 수,
                  'date_from': 이월 시작일},
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

    _excl = exclude_orders if exclude_orders is not None else set()
    # 미정산 이월 조회 시작일 — date_from 하루 전부터 carry_days 만큼 거슬러 올라간다.
    _carry_from = ''
    if int(carry_days or 0) > 0:
        try:
            _d0 = datetime.strptime(str(date_from), "%Y-%m-%d").date()
            _carry_from = str(_d0 - timedelta(days=int(carry_days)))
            _carry_to = str(_d0 - timedelta(days=1))
        except Exception:
            _carry_from = ''
    _carry_stat = {'scanned': 0, 'matched': 0, 'date_from': _carry_from}

    rows, matched_costco, unmatched_orders = [], set(), []
    for uname in users:
        nmap = _naver_to_product_map(uname)     # 네이버번호 → 사용자 제품레코드(정확한 코스트코번호)
        _cols = ("order_no, order_date, recipient, product_no, product_name, qty, cost_price")
        try:
            conn = get_user_db(uname)
            if basis == 'payment':
                ords = conn.execute(
                    "SELECT " + _cols + " FROM order_history "
                    "WHERE order_date BETWEEN ? AND ?",
                    (date_from, date_to)
                ).fetchall()
            else:
                # 옛 행 중 order_no가 빈 것은 정산 대상에서 뺀다 — 이미 정산됐는지
                # 판별할 키가 없어 매번 다시 배치돼 이중 청구가 된다.
                ords = conn.execute(
                    "SELECT " + _cols + " FROM daily_orders "
                    "WHERE order_date BETWEEN ? AND ? AND COALESCE(order_no,'') <> ''",
                    (date_from, date_to)
                ).fetchall()
            # 미정산 이월 — 정산 기간 이전의 아직 안 붙은 주문. 번호가 정확히
            # 맞을 때만 붙이므로 오래된 주문을 훑어도 오매칭이 늘지 않는다.
            _carry = []
            if _carry_from and basis != 'payment':
                _carry = conn.execute(
                    "SELECT " + _cols + " FROM daily_orders "
                    "WHERE order_date BETWEEN ? AND ? AND COALESCE(order_no,'') <> ''",
                    (_carry_from, _carry_to)
                ).fetchall()
            elif _carry_from:
                _carry = conn.execute(
                    "SELECT " + _cols + " FROM order_history "
                    "WHERE order_date BETWEEN ? AND ?",
                    (_carry_from, _carry_to)
                ).fetchall()
            conn.close()
        except Exception:
            ords, _carry = [], []
        _seen_ono = set()
        for _is_carry, o in ([(False, _o) for _o in ords]
                             + [(True, _o) for _o in _carry]):
            _ono = _norm(o['order_no'])
            # 같은 주문이 기간 내 여러 날짜에 저장돼 있으면 한 번만 배치한다
            if _ono and _ono in _seen_ono:
                continue
            if _ono:
                _seen_ono.add(_ono)
            # 이미 정산된 주문은 후보에서 뺀다 (구입가 이중 반영 방지)
            if _excl and (uname, _ono) in _excl:
                continue
            onv = _norm(o['product_no'])
            nm = _norm(o['product_name'])
            prod = nmap.get(onv)
            via = 'number'
            # ① 코스트코번호 브리지 — 제품레코드의 product_no (정확) → 없으면 주문번호 자체
            costco_no = _norm((prod or {}).get('product_no'))
            if costco_no not in price_by_costco:
                costco_no = onv if onv in price_by_costco else ''
            if _is_carry:
                # 이월분(정산 기간 밖)은 번호가 정확히 맞을 때만 붙인다.
                # 이름 유사도까지 허용하면 오래된 주문에 엉뚱하게 붙는다.
                _carry_stat['scanned'] += 1
                if costco_no not in price_by_costco:
                    continue                    # 부족분 목록에도 넣지 않는다
                via = 'carry'
                _carry_stat['matched'] += 1
            # ② 상품명 유사도 폴백 — 번호로 못 붙은 주문을 영수증 상품명과 매칭
            if not _is_carry and costco_no not in price_by_costco:
                _best, _info = best_name_match(nm, _ritems)
                if _best:
                    costco_no = _best['costco']
                    via = 'name'
            # ③ 재고 이월 — 당일 영수증에 없으면 과거 구매분(가용 재고)에서 찾는다.
            #    실제로는 며칠 전 산 걸 오늘 내보내는 일이 흔한데, 당일 영수증만
            #    보면 그게 전부 미매칭으로 남았다. 찾으면 수량을 차감해 같은 재고가
            #    두 번 쓰이지 않게 한다.
            _from_stock = None
            if not _is_carry and costco_no not in price_by_costco and stock_pool:
                _cand = _norm((prod or {}).get('product_no')) or onv
                if _cand not in stock_pool:
                    _pool_cands = [{'_pn': _pn, 'name': _e.get('name') or ''}
                                   for _pn, _e in stock_pool.items()]
                    _pb, _pi = best_name_match(nm, _pool_cands)
                    _cand = _pb['_pn'] if _pb else None
                if _cand and stock_pool.get(_cand, {}).get('units', 0) > 0:
                    _need = int(o['qty'] or 1) * int(_split_pack(prod)[1] or 1)
                    if stock_pool[_cand]['units'] >= _need:
                        stock_pool[_cand]['units'] -= _need
                        _from_stock = _cand
                        via = 'stock'

            if _from_stock is None and costco_no not in price_by_costco:
                # 미매칭 주문 — 수동/AI 매칭 후보로 반환
                unmatched_orders.append({
                    'username': uname, 'order_no': _norm(o['order_no']),
                    'order_date': _norm(o['order_date']), 'recipient': _norm(o['recipient']),
                    'naver_no': onv, 'product_name': nm, 'qty': int(o['qty'] or 1),
                    'prev_cost': int(o['cost_price'] or 0),
                    'split_qty': int((prod or {}).get('split_qty', 1) or 1),
                })
                continue
            if _from_stock is not None:
                costco_no = _from_stock
                up = int(stock_pool[_from_stock].get('price') or 0)
            else:
                up = price_by_costco[costco_no]
            qty = int(o['qty'] or 1)
            _sq, _pk = _split_pack(prod)
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
                # 남은 재고 계산용 — 소비량 = qty × pack (소분 단위)
                'split_qty': _sq,
                'pack': _pk,
            })
            matched_costco.add(costco_no)

    unmatched = [
        {'상품번호': c, '상품명': name_by_costco.get(c, ''), '단가': price_by_costco[c]}
        for c in price_by_costco if c not in matched_costco
    ]
    return {'rows': rows, 'unmatched_receipt': unmatched,
            'unmatched_orders': unmatched_orders,
            'user_summary': _summarize(rows),
            'carry': _carry_stat}


def compute_leftovers(receipt_items, rows):
    """영수증 구매수량에서 배치된 주문 소비량을 빼 '남은 수량'을 낸다.

    단위는 재고원장(db_inventory)과 같은 **소분 단위**:
      입고 units = 영수증 수량(팩) × split_qty
      소비 units = Σ(주문 qty × pack)
    둘 다 정수라 잔량도 정수로 떨어진다. 팩의 배수가 아닐 수 있다
    (1팩을 4소분해 2개만 팔면 2소분 = 0.5팩 잔량).

    반환: [{costco_no, name, unit_price, qty_receipt, split_qty,
            units_in, units_used, units_left, packs_left}]  (남은 게 있는 품목만)
    """
    used_by, split_by = {}, {}
    for r in rows or []:
        c = _norm(r.get('costco_no'))
        if not c:
            continue
        used_by[c] = used_by.get(c, 0) + int(r.get('qty') or 0) * int(r.get('pack') or 1)
        # 같은 상품인데 사용자마다 소분 수가 다르면 큰 값을 기준으로 잡는다.
        # 작게 잡으면 입고량이 과소계산돼 있지도 않은 부족분이 생긴다.
        split_by[c] = max(split_by.get(c, 1), int(r.get('split_qty') or 1))

    # 같은 상품번호가 표에 여러 줄로 들어올 수 있어 먼저 합산한다.
    agg = {}
    for it in receipt_items or []:
        c = _norm(it.get('상품번호'))
        if not c:
            continue
        try:
            up = int(float(it.get('단가') or 0))
        except (TypeError, ValueError):
            up = 0
        try:
            qn = int(it.get('수량') or 0)
        except (TypeError, ValueError):
            qn = 0
        if qn <= 0 or up <= 0:
            continue
        a = agg.setdefault(c, {'name': _norm(it.get('상품명')), 'unit_price': up, 'qty': 0})
        a['qty'] += qn
        if not a['name']:
            a['name'] = _norm(it.get('상품명'))

    out = []
    for c, a in agg.items():
        sq = split_by.get(c, 1)
        units_in = a['qty'] * sq
        units_used = used_by.get(c, 0)
        units_left = units_in - units_used
        if units_left <= 0:
            continue
        out.append({
            'costco_no': c, 'name': a['name'], 'unit_price': a['unit_price'],
            'qty_receipt': a['qty'], 'split_qty': sq,
            'units_in': units_in, 'units_used': units_used, 'units_left': units_left,
            'packs_left': units_left / sq,
        })
    out.sort(key=lambda x: -x['units_left'] * x['unit_price'])
    return out


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
            # 수동 매칭 경로는 묶음배수를 모른다 → 1로 둔다(남은수량이 과대추정되지
            # 않는 쪽. 과소추정하면 없는 재고를 잡는다).
            'split_qty': split, 'pack': 1,
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


def learn_costco_mappings(rows):
    """매칭 결과(네이버번호 ↔ 코스트코번호)를 사용자 제품DB에 되기입한다.

    왜 필요한가:
      영수증 매칭은 '제품DB에 코스트코번호가 있느냐'에 걸려 있는데, 실측 매핑률이
      20%대(oxo 23% / tblue 21%)에 고착돼 있었다. 이름·재고·수동·AI로 어렵게 붙인
      매칭이 어디에도 저장되지 않아, 같은 상품이 다음 정산에서 또 미매칭으로 나왔다.
      한 번 붙은 매핑을 저장하면 다음부터는 ①번(번호 브리지)으로 바로 붙는다.

    안전장치:
      · 이미 코스트코번호가 있는 레코드는 절대 덮어쓰지 않는다(사용자 값이 권위).
      · 4~7자리 형식 검증(is_costco_pno) — 네이버 ID가 번호칸에 섞이는 오염 차단.
      · via='number'는 이미 번호로 붙은 행이라 배울 게 없어 건너뛴다.

    Returns: {'filled': 채운 레코드 수, 'by_user': {username: n}, 'pairs': 시도한 매핑 수}
    """
    pairs = {}
    for r in (rows or []):
        if str(r.get('via') or '') == 'number':
            continue
        u = _norm(r.get('username'))
        nv = _norm(r.get('naver_no'))
        cno = _norm(r.get('costco_no'))
        if not (u and nv and is_costco_pno(cno)):
            continue
        pairs.setdefault(u, {}).setdefault(nv, cno)

    filled, by_user = 0, {}
    for uname, m in pairs.items():
        try:
            conn = get_user_db(uname)
        except Exception:
            continue
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
            key_cols = [c for c in ('naver_channel_pno', 'naver_origin_pno') if c in cols]
            if not key_cols or 'product_no' not in cols:
                continue
            _where = " OR ".join("TRIM(COALESCE(%s,''))=?" % c for c in key_cols)
            n = 0
            for nv, cno in m.items():
                cur = conn.execute(
                    "UPDATE products SET product_no=? "
                    "WHERE TRIM(COALESCE(product_no,''))='' AND (%s)" % _where,
                    [cno] + [nv] * len(key_cols))
                n += cur.rowcount
            conn.commit()
            if n:
                by_user[uname] = n
                filled += n
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return {'filled': filled, 'by_user': by_user,
            'pairs': sum(len(v) for v in pairs.values())}


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
            # 일일주문 기준으로 정산하면 order_history에 아예 없는 주문이 많다
            # (clglobal0919 980건 · oxo 160건). daily_orders에도 반영해야
            # 수익계산이 실제 매입가를 본다.
            try:
                conn.execute("UPDATE daily_orders SET cost_price=? WHERE order_no=?", (amt, ono))
            except Exception:
                pass
            try:
                conn.execute("UPDATE profit_settlements SET cost_price=? WHERE order_no=?", (amt, ono))
            except Exception:
                pass
            updated += 1
        conn.commit()
        conn.close()
    return updated
