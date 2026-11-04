"""수익계산 화면 로직 — /m/profit(API)가 부른다. Streamlit 수익계산과 같은 함수를 쓴다.

계산: pages_lib.profit_calc.dispatch_profit.compute_dispatch_rows(page_rules=True)
  = 그날 발송 + 온라인몰 직배송 행, 영수증·청구액·수동 키워드·타인재고 웃돈 반영 구입가,
    실정산 확정액, 포장 배정 박스비, 저장값 — Streamlit 화면과 같은 순서.
화면에서 고친 값(단가·키워드·영수증 품목·택배·박스)은 overrides로 넘겨 **서버가 다시 계산**한다.
브라우저는 숫자를 계산하지 않는다(옛 /m 화면은 TypeScript로 따로 계산해 숫자가 달랐다).
"""
from collections import defaultdict
from datetime import date as _date, datetime

from pages_lib.profit_calc.dispatch_profit import compute_dispatch_rows, settings_defaults

#: 화면 매칭 출처 순서(배지 개수 표시)
SOURCES = ('정산청구', '영수증', 'DB-번호', 'DB-키워드', '수동입력', '미매칭')


def _no_ship_day(d):
    try:
        from pages_lib.home_page import _is_no_ship_day
        return bool(_is_no_ship_day(d))
    except Exception:
        return False


def _base_source(s):
    s = str(s or '')
    for k in SOURCES:
        if s.startswith(k):
            return k
    return '영수증' if s.startswith('영수증') else s


def summarize_day(rows):
    """합계 — Streamlit 일별 합계와 같은 규칙: **구입가가 있는 행만** 더한다."""
    m = [r for r in rows if int(r['cost_price'] or 0) > 0]
    s = {
        'settlement': sum(r['settlement_amount'] for r in m),
        'shipping_in': sum(r['shipping_fee'] for r in m),
        'settled_shipping': sum(r['settled_shipping'] for r in m),
        'cost': sum(r['cost_price'] for r in m),
        'delivery': sum(r['delivery_cost'] for r in m),
        'box': sum(r['box_cost'] for r in m),
        'profit': sum(r['profit'] for r in m),
        'costed': len(m), 'count': len(rows),
        'nocost': len(rows) - len(m),
        'nocost_settlement': sum(r['settlement_amount'] for r in rows if int(r['cost_price'] or 0) <= 0),
    }
    s['income'] = s['settlement'] + s['settled_shipping']
    s['expense'] = s['cost'] + s['delivery'] + s['box']
    return s


def day(username, date, overrides=None):
    """일별 정산표 — 행·합계·매칭 출처 건수·다른 날짜 저장분·휴무일 여부."""
    rows, label, kind, saved = compute_dispatch_rows(username, date, use_saved=True,
                                                     page_rules=True, overrides=overrides)
    rows.sort(key=lambda r: (r['product_name'], r['recipient']))
    counts = defaultdict(int)
    for r in rows:
        counts[_base_source(r['match_source'])] += 1
    elsewhere = {}
    if rows:
        try:
            from pages_lib.profit_calc.save import saved_elsewhere
            elsewhere = saved_elsewhere(username, date, [r['order_no'] for r in rows])
        except Exception:
            elsewhere = {}
    ship, box = settings_defaults(username)
    return {
        'date': date, 'label': label or '', 'saved': saved, 'no_ship_day': _no_ship_day(date),
        'defaults': {'delivery': ship, 'box': box},
        'rows': rows, 'summary': summarize_day(rows), 'counts': dict(counts),
        'elsewhere': elsewhere,
    }


def _ps_row(r):
    """정산저장 행 — save.save_settlements와 같은 필드. 수익은 서버 값."""
    from utils import extract_sell_factor
    return {
        'order_no': r['order_no'], 'recipient': r['recipient'], 'product_name': r['product_name'],
        'product_no': r['product_no'], 'option_info': r['option_info'],
        'qty': max(1, int(r['qty'] or 1)), 'order_amount': r['order_amount'],
        'shipping_fee': r['shipping_fee'], 'extra_shipping': r['extra_shipping'],
        'settlement_amount': r['settlement_amount'], 'cost_price': r['cost_price'],
        'delivery_cost': r['delivery_cost'], 'box_cost': r['box_cost'],
        'profit': (r['settlement_amount'] + r['shipping_fee'])
                  - (r['cost_price'] + r['delivery_cost'] + r['box_cost']),
        'matched_keyword': r.get('keyword') or r['matched_name'],
        'matched_product_no': r['matched_pno'], 'match_source': r['match_source'],
        'split_qty': int(r['split_qty'] or 1),
        'sell_factor': extract_sell_factor(r['product_name']),
    }


def save_day(username, date, overrides=None, move_dup=False, update_price_db=False):
    """💾 정산저장 — 그날 표 전체를 저장한다(Streamlit '정산저장'과 같다).

    고친 키워드·구입가는 정산매칭 DB(settlement_overrides)에도 남겨 다음에 다시 쓰고,
    update_price_db면 구입가를 고친 행의 단가를 제품가격 DB에도 반영한다.
    반환: {saved, note, prices, links}
    """
    from db import save_settlement_override, get_all_products
    from pages_lib.profit_calc.save import save_ps_rows, upsert_row_prices
    from pages_lib.profit_calc.compute import apply_auto_links
    ov = {str(k): (v or {}) for k, v in (overrides or {}).items()}
    links = []
    rows, _l, _k, _s = compute_dispatch_rows(username, date, use_saved=True, page_rules=True,
                                             overrides=ov, links_out=links)
    if not rows:
        return {'saved': 0, 'note': '이 날짜에 발송된 주문이 없습니다.', 'prices': 0, 'links': 0}
    n, note = save_ps_rows(username, date, [_ps_row(r) for r in rows], move_dup=move_dup)
    cost_edited = []
    for r in rows:
        o = ov.get(r['order_no']) or {}
        kw = str(o.get('receipt_name') or o.get('keyword') or '').strip()
        cost_ov = o.get('unit_cost') is not None or o.get('receipt_unit') is not None
        if kw or cost_ov:
            save_settlement_override(username, r['recipient'], r['product_name'],
                                     keyword=kw, cost=r['cost_price'] if cost_ov else 0)
        if cost_ov:
            cost_edited.append(r)
    prices = upsert_row_prices(username, cost_edited, get_all_products(username)) \
        if (update_price_db and cost_edited) else 0
    nl = apply_auto_links(username, links) if links else 0
    return {'saved': n, 'note': note, 'prices': prices, 'links': nl}


def delete_orders(username, order_nos):
    """선택 주문 영구 삭제(취소건 등) — 발송기록·주문이력·정산저장·일별주문 + 영수증 정산 품목.
    발송기록을 안 지우면 다시 불러올 때 되살아난다(page.py 1021-1062와 같음)."""
    from db import get_user_db
    onos = [str(o).strip() for o in (order_nos or []) if str(o).strip() and str(o).strip() != 'nan']
    if not onos:
        return 0
    conn = get_user_db(username)
    ph = ",".join("?" * len(onos))
    try:
        for tbl in ("dispatch_log", "order_history", "profit_settlements", "daily_orders"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE order_no IN ({ph})", onos)
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()
    try:
        import db_settle
        db_settle.remove_items(username, onos)
    except Exception:
        pass
    return len(onos)


def receipts(username, days=90):
    """영수증 품목 고르기용 — 최근 영수증 품목(같은 상품번호는 최근 것 하나)."""
    from db import get_recent_receipt_items
    seen, out = set(), []
    for it in get_recent_receipt_items(username, days=days) or []:
        k = (str(it.get('상품번호') or ''), str(it.get('상품명') or ''))
        if k in seen:
            continue
        seen.add(k)
        out.append({'pno': k[0], 'name': k[1], 'unit': int(it.get('단가') or 0),
                    'date': str(it.get('receipt_date') or '')})
    return out


# ── 📅 월별 ───────────────────────────────────────────────
def _prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def month(target, ym=''):
    """월 합계·전월·일별·상품별·점검 — monthly.render_monthly의 계산 부분 그대로."""
    from db_profit_calc import (get_month_settlements, get_profit_months,
                                get_settlements_by_order_nos, calc_row_profit)
    from pages_lib.profit_calc.monthly import audit_rows, summarize, row_profit, CHECKS
    months = get_profit_months(target)
    this = _date.today().strftime("%Y-%m")
    if this not in months:
        months = [this] + months
    ym = ym if ym in months else months[0]
    rows = get_month_settlements(target, ym)
    out = {'months': months, 'ym': ym, 'checks': [{'code': c, 'title': t, 'sev': s} for c, t, s in CHECKS]}
    if not rows:
        return {**out, 'summary': {}, 'prev': {}, 'days': [], 'top': [], 'bottom': [], 'rows': [], 'issue_counts': {}}
    other = [r for r in get_settlements_by_order_nos(target, {str(r.get('order_no') or '') for r in rows})
             if str(r.get('settlement_date') or '')[:7] != ym]
    try:
        from db import get_user_db
        c = get_user_db(target)
        dispatched = {str(r[0]) for r in c.execute('SELECT order_no FROM dispatch_log')}
        c.close()
    except Exception:
        dispatched = None
    issues = audit_rows(rows, other, dispatched=dispatched)

    by_day = defaultdict(list)
    for r in rows:
        by_day[r['settlement_date']].append(r)
    try:
        from db_dispatch_log import get_dispatch_counts
        disp = get_dispatch_counts(target, f"{ym}-01", f"{ym}-31")
    except Exception:
        disp = {}
    days = []
    for d in sorted(set(by_day) | set(disp)):
        ds = summarize(by_day.get(d, []))
        days.append({'date': d, 'dispatched': disp.get(d, 0), 'saved': ds.get('cnt', 0),
                     'sales': ds.get('sales', 0), 'settle': ds.get('settle', 0),
                     'cost': ds.get('cost', 0), 'profit': ds.get('profit', 0),
                     'issues': sum(1 for r in by_day.get(d, []) if r['id'] in issues)})

    by_p = defaultdict(lambda: defaultdict(int))
    for r in rows:
        p = by_p[r.get('product_name') or '(이름 없음)']
        p['cnt'] += 1
        p['qty'] += int(r.get('qty') or 0)
        p['sales'] += int(r.get('order_amount') or 0)
        p['profit'] += row_profit(r)
    prods = sorted(({'name': k, **v} for k, v in by_p.items()), key=lambda x: x['profit'])

    counts = defaultdict(int)
    for v in issues.values():
        for c in v:
            counts[c] += 1
    issue_rows = [{
        'id': r['id'], 'date': r['settlement_date'], 'order_no': str(r.get('order_no') or ''),
        'recipient': r.get('recipient', ''), 'product_name': r.get('product_name', ''),
        'qty': int(r.get('qty') or 0), 'sales': int(r.get('order_amount') or 0),
        'settle': int(r.get('settlement_amount') or 0), 'cost': int(r.get('cost_price') or 0),
        'delivery': int(r.get('delivery_cost') or 0), 'box': int(r.get('box_cost') or 0),
        'saved_profit': int(r.get('profit') or 0), 'profit_now': calc_row_profit(r),
        'match': r.get('match_source', ''), 'codes': issues[r['id']],
    } for r in rows if r['id'] in issues]
    # 중복 — 다른 달 행까지 같이 내야 어느 날짜를 남길지 고를 수 있다
    dup_o = {x['order_no'] for x in issue_rows if 'dup' in x['codes']}
    dup_other = [{
        'id': r['id'], 'date': r['settlement_date'], 'order_no': str(r.get('order_no') or ''),
        'recipient': r.get('recipient', ''), 'product_name': r.get('product_name', ''),
        'sales': int(r.get('order_amount') or 0), 'profit_now': row_profit(r),
    } for r in other if str(r.get('order_no')) in dup_o]
    disp_dates = {}
    if dup_o:
        try:
            from db_dispatch_log import get_dispatch_by_order_nos
            disp_dates = {o: str((v or {}).get('dispatched_at') or '')[:10]
                          for o, v in get_dispatch_by_order_nos(target, list(dup_o)).items()}
        except Exception:
            disp_dates = {}
    return {**out, 'summary': summarize(rows), 'prev': summarize(get_month_settlements(target, _prev_month(ym))),
            'days': days, 'top': prods[-10:][::-1], 'bottom': prods[:10],
            'rows': issue_rows, 'dup_other': dup_other, 'dup_dispatch': disp_dates,
            'issue_counts': dict(counts)}
