"""발송 기준 수익 — 그날 발송한 주문의 수익을 세션 없이 계산하고 자동 저장한다.

수익계산 화면은 '그날 송장이 찍힌 주문'(dispatch_log)이 행을 정한다. 그런데 12시
자동 저장(auto_task.auto_save_profit)은 **주문 수집일**(daily_orders) 기준으로
profit_settlements에 썼고, 홈 달력·월별·대시보드는 그 저장값을 읽었다. 그래서
화면과 통계가 어긋났다 — 10/21 실측 10월 저장 784행 중 발송일과 같은 날짜는
221행뿐, 193행은 다른 날짜, 370행은 발송 기록이 없는 주문, 발송됐는데 저장이
없는 건 572건(더블루샾 294건).

계산은 API(/api/profit/{date})와 이 모듈이 같은 함수(compute_dispatch_rows)를 쓴다.
"""
from db import (
    get_all_settings, get_all_products, get_shared_products,
    get_profit_settlements, save_profit_settlements, get_user_db,
)
from services import match_product_to_db
from pages_lib.profit_calc.loader import build_settlement_df
from pages_lib.profit_calc.compute import compute_rows, compute_profit, settled_shipping
from pages_lib.profit_calc.enrich import (
    build_receipt_maps, build_kw_overrides, load_surcharge_map,
)

#: 자동 저장 표시 — 사람이 고친 저장값(수동입력·영수증 등)과 구별한다
AUTO_SOURCE = '자동'


def is_cancelled_status(status):
    """취소·반품으로 끝난 주문 상태 — 수익에서 빠져야 한다(네이버 한글·영문, 쿠팡)."""
    s = str(status or '').strip().upper()
    return any(k in s for k in ('취소', '반품', 'CANCEL', 'RETURN'))


def _int(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def settings_defaults(username):
    """행별 기본 택배비/박스비 — page.py와 동일 폴백(1800/300, 이상치 방어)."""
    s = get_all_settings(username)
    try:
        ship = int(s.get('shipping_cost') or 2000)
    except (TypeError, ValueError):
        ship = 1800
    try:
        box = int(s.get('box_cost') or 300)
    except (TypeError, ValueError):
        box = 300
    if ship > 100000:
        ship = 1800
    if box > 10000:
        box = 300
    return ship, box


def compute_dispatch_rows(username, date, use_saved=True):
    """날짜별 정산표 행 — build_settlement_df + compute_rows(구입가·매칭) + compute_profit.

    use_saved=True: 정산저장(ps) 소스면 저장된 구입가·택배·박스를 그대로 쓴다(화면·API).
    use_saved=False: 저장값을 무시하고 지금 매칭·설정으로 새로 계산한다(자동 저장용).
    반환: (rows, src_label, kind, saved). rows는 dict 목록.
    """
    df, src_label, kind = build_settlement_df(username, date)
    saved = (kind == 'ps')
    if df is None or df.empty:
        return [], src_label, kind, saved
    _use = saved and use_saved

    def_ship, def_box = settings_defaults(username)
    preload_user = get_all_products(username)
    preload_shared = get_shared_products()
    _memo = {}

    def match_fn(nm, pno):
        if pno:
            return match_product_to_db(username, nm, product_no=pno,
                                       _user_prods=preload_user, _shared_prods=preload_shared)
        if nm not in _memo:
            _memo[nm] = match_product_to_db(username, nm, product_no='',
                                            _user_prods=preload_user, _shared_prods=preload_shared)
        return _memo[nm]

    has_pno = 'product_no' in df.columns
    has_cost = '구입가격' in df.columns
    has_deliv = '택배원가' in df.columns
    has_box = '박스원가' in df.columns
    records = df.to_dict('records')
    rows_in = [{
        'idx': str(rec.get('_sk', '')),
        '수취인명': rec.get('수취인명', ''),
        '상품명': rec.get('상품명', ''),
        'product_no': (str(rec.get('product_no', '') or '') if has_pno else ''),
        '수량': rec.get('수량', 1),
        '구입가격': (rec.get('구입가격', 0) if has_cost else 0),
    } for rec in records]

    # 영수증 매칭·수동 오버라이드·타인재고 웃돈을 DB에서 재구성 → compute_rows(순수)에 주입
    unique_products = list(dict.fromkeys(str(r['상품명'] or '') for r in rows_in))
    receipt_by_pno, receipt_matches = build_receipt_maps(username, unique_products, match_fn)
    kw_overrides = build_kw_overrides(username, rows_in, date)
    surcharge_map = load_surcharge_map(username, [r['idx'] for r in rows_in])
    results, _links = compute_rows(
        rows_in, match_fn=match_fn,
        receipt_by_pno=receipt_by_pno, receipt_matches=receipt_matches,
        kw_overrides=kw_overrides, surcharge_map=surcharge_map, calc_date_str=date)

    out = []
    for rec, res in zip(records, results):
        settlement = _int(rec.get('정산예정금액', 0))
        shipping = _int(rec.get('배송비 합계', 0))
        _src = str(res.get('source', ''))
        if _use and has_cost:
            cost = _int(rec.get('구입가격', 0))
        else:
            # 타인재고 웃돈(+원가) 가산 — page.py 후처리(costs += surcharge)와 동일
            _sur = _int(surcharge_map.get(str(rec.get('_sk', '')), 0))
            cost = _int(res.get('cost', 0)) + _sur
            if _sur and '타인재고' not in _src:
                _src = f"{_src}+타인재고"
        deliv = _int(rec.get('택배원가', def_ship)) if (_use and has_deliv) else def_ship
        box = _int(rec.get('박스원가', def_box)) if (_use and has_box) else def_box
        out.append({
            'order_no': str(rec.get('_sk', '')),
            'recipient': str(rec.get('수취인명', '') or ''),
            'product_name': str(rec.get('상품명', '') or ''),
            'product_no': str(rec.get('product_no', '') or ''),
            'option_info': str(rec.get('옵션정보', '') or ''),
            'qty': _int(rec.get('수량', 1), 1),
            'order_amount': _int(rec.get('최종 상품별 총 주문금액', 0)),
            'settlement_amount': settlement,
            'shipping_fee': shipping,
            'extra_shipping': _int(rec.get('제주/도서 추가배송비', 0)),
            'settled_shipping': settled_shipping(shipping),
            'cost_price': cost,
            'delivery_cost': deliv,
            'box_cost': box,
            'profit': compute_profit(settlement, shipping, cost, deliv, box),
            'match_source': _src,
            'matched_name': str(res.get('matched_name', '')),
            'matched_pno': str(res.get('matched_pno', '')),
            'split_qty': _int(res.get('sqty', 1), 1),
        })
    return out, src_label, kind, saved


def _remove_stale_auto(conn, date, disp, cur=None, strict=False):
    """이 날짜의 '자동' 저장 행 중 그날 발송되지 않은 주문을 지운다. 반환: 지운 수.

    지우는 건 **다른 날 발송 기록이 있거나(그날 저장으로 옮겨진다) 취소·반품됐거나
    주문 이력에서 아예 사라진 주문**뿐이다. 발송 기록이 없다고 안 보낸 것은 아니다 —
    쿠팡·스마트스토어에서 직접 보내 앱 발송 기록에 없는 주문이 있다(김혜림 쿠팡 92행).
    """
    if cur is None:
        cur = {str(r['order_no']): r for r in conn.execute(
            "SELECT order_no, match_source FROM profit_settlements WHERE settlement_date=?",
            (str(date),)).fetchall()}
    _cand = [o for o, r in cur.items() if r['match_source'] == AUTO_SOURCE and o not in disp]
    _stale = []
    for o in _cand:
        _dl = conn.execute("SELECT 1 FROM dispatch_log WHERE order_no=? LIMIT 1",
                           (o,)).fetchone()
        try:
            _st = conn.execute("SELECT status FROM order_history WHERE order_no=?",
                               (o,)).fetchone()
        except Exception:
            _st = ('',)
        if strict and _dl and not (_st is None or is_cancelled_status(_st[0])):
            # 다른 날 발송된 주문 — 그 발송일에 수익 행이 이미 있으면 여기 것은 지우고,
            # 없으면 **발송일로 옮긴다**(지우면 그 주문 수익이 어디에도 안 남는다).
            _has = conn.execute(
                "SELECT 1 FROM profit_settlements p JOIN dispatch_log d ON d.order_no=p.order_no "
                "AND p.settlement_date=substr(d.dispatched_at,1,10) WHERE p.order_no=? LIMIT 1",
                (o,)).fetchone()
            if not _has:
                _dd = conn.execute("SELECT MIN(substr(dispatched_at,1,10)) FROM dispatch_log "
                                   "WHERE order_no=?", (o,)).fetchone()[0]
                if _dd:
                    conn.execute("UPDATE profit_settlements SET settlement_date=? WHERE "
                                 "settlement_date=? AND order_no=? AND match_source=?",
                                 (_dd, str(date), o, AUTO_SOURCE))
                continue
        if _dl or _st is None or is_cancelled_status(_st[0]):
            _stale.append(o)
    if _stale:
        conn.executemany("DELETE FROM profit_settlements WHERE settlement_date=? "
                         "AND order_no=? AND match_source=?",
                         [(str(date), o, AUTO_SOURCE) for o in _stale])
    return len(_stale)


def clean_stale_auto(username, date):
    """정산저장(화면) 뒤에 부른다 — 그날 발송건만 남기고 옛 주문일 기준 자동 행을 정리.
    10/1 고영부: 발송 10건인데 취소된 주문의 자동 행 17건이 남아 달력 수익이
    49,494원이 아니라 239,333원으로 보였다."""
    online = _online_dates(username)
    conn = get_user_db(username)
    conn.row_factory = __import__('sqlite3').Row
    try:
        keep = {str(r[0]) for r in conn.execute(
            "SELECT order_no FROM dispatch_log WHERE dispatched_at LIKE ?", (str(date)[:10] + '%',))}
        keep |= {o for o, d in online.items() if d == str(date)[:10]}
        if not keep:
            return 0          # 발송 기록이 없는 날은 손대지 않는다(화면도 이 날은 행을 만들지 않는다)
        mv, rm = enforce_dispatch_basis(conn, date, keep, online)
        conn.commit()
        return mv + rm
    finally:
        conn.close()


def _online_dates(username):
    """{주문번호: 정산일} — 코스트코 온라인몰 직배송으로 표시된 주문."""
    try:
        from db_core import get_auth_db
        a = get_auth_db()
        try:
            return {str(o): str(d) for o, d in a.execute(
                "SELECT order_no, settle_date FROM online_purchase WHERE username=?", (str(username),))}
        finally:
            a.close()
    except Exception:
        return {}


def basis_date(conn, order_no, online=None):
    """그 주문의 수익 날짜 — 송장 등록으로 발송처리된 날(첫 발송), 없으면 온라인몰 직배송 정산일, 없으면 None."""
    r = conn.execute("SELECT MIN(substr(dispatched_at,1,10)) FROM dispatch_log WHERE order_no=?",
                     (str(order_no),)).fetchone()
    return (r[0] if r and r[0] else None) or (online or {}).get(str(order_no))


def enforce_dispatch_basis(conn, date, keep, online=None):
    """이 날짜의 수익 행 중 keep(그날 발송 + 온라인몰 직배송)에 없는 행을 정리한다 — 저장 출처와 상관없이.

    수익계산은 무조건 '송장 등록으로 발송처리된 건 + 온라인몰 직배송 건'이다(10/26 확정).
      · 다른 날 발송(또는 온라인몰 정산)된 주문 → 그 날짜로 옮긴다(그 날짜에 이미 행이 있으면 여기 것을 지운다)
      · 발송 기록도 온라인몰 표시도 없는 주문 → 지운다(발송하지 않은 주문은 수익이 아니다)
    반환: (옮긴 수, 지운 수)
    """
    moved = removed = 0
    for rid, o in conn.execute("SELECT id, order_no FROM profit_settlements WHERE settlement_date=?",
                               (str(date),)).fetchall():
        o = str(o)
        if o in keep:
            continue
        bd = basis_date(conn, o, online)
        if bd and bd != str(date) and not conn.execute(
                "SELECT 1 FROM profit_settlements WHERE settlement_date=? AND order_no=?", (bd, o)).fetchone():
            conn.execute("UPDATE profit_settlements SET settlement_date=? WHERE id=?", (bd, rid))
            moved += 1
        else:
            conn.execute("DELETE FROM profit_settlements WHERE id=?", (rid,))
            removed += 1
    return moved, removed


def save_dispatch_profit(username, date):
    """그날 **송장 등록으로 발송처리된 주문 + 온라인몰 직배송 주문**의 수익을 profit_settlements에 저장한다.

    · 이 날짜에 있는 그 밖의 행은 출처와 상관없이 정리한다(enforce_dispatch_basis) — 발송일로 옮기거나 지운다.
    · 이 날짜 주문이 다른 날짜에 저장돼 있으면: '자동' 행은 지우고 여기 새로 저장, 사람이 고친 행은
      **이 날짜로 옮긴다**(고친 값은 지키되 날짜는 발송일이어야 한다).
    · 이 날짜에 사람이 고친 행은 덮어쓰지 않는다.
    · 구입가가 0(미매칭)인 행도 저장한다 — 통계는 구입가 0 행을 수익에서 빼고,
      월별 점검이 '구입가 없음'으로 짚는다.
    반환: {'saved', 'moved', 'removed', 'kept_manual', 'skipped_other'}
    """
    out = {'saved': 0, 'moved': 0, 'removed': 0, 'kept_manual': 0, 'skipped_other': 0}
    rows, _lbl, kind, _saved = compute_dispatch_rows(username, date, use_saved=False)
    disp = {r['order_no'] for r in rows} if kind in ('dispatch', 'ps') else set()
    online = _online_dates(username)
    conn = get_user_db(username)
    try:
        out['moved'], out['removed'] = enforce_dispatch_basis(conn, date, disp, online)
        if not disp:
            conn.commit()
            return out                              # 그날 발송·온라인몰 건이 없다
        cur = {str(r['order_no']): r for r in conn.execute(
            "SELECT order_no, match_source FROM profit_settlements WHERE settlement_date=?",
            (str(date),)).fetchall()}
        # 다른 날짜에 저장된 같은 주문 — 자동은 지우고(여기 새로 저장), 사람이 고친 값은 이 날짜로 옮긴다
        _nos = list(disp)
        for i in range(0, len(_nos), 900):
            ch = _nos[i:i + 900]
            for r in conn.execute(
                    "SELECT id, order_no, match_source FROM profit_settlements "
                    "WHERE settlement_date!=? AND order_no IN (%s)" % ",".join("?" * len(ch)),
                    [str(date)] + ch).fetchall():
                o = str(r['order_no'])
                if r['match_source'] != AUTO_SOURCE and o not in cur:
                    conn.execute("UPDATE profit_settlements SET settlement_date=? WHERE id=?",
                                 (str(date), r['id']))
                    cur[o] = {'match_source': r['match_source']}
                else:
                    conn.execute("DELETE FROM profit_settlements WHERE id=?", (r['id'],))
                out['moved'] += 1
        conn.commit()
    finally:
        conn.close()

    to_save = []
    for r in rows:
        o = r['order_no']
        if o in cur and cur[o]['match_source'] != AUTO_SOURCE:
            out['kept_manual'] += 1
            continue
        to_save.append(dict(r, match_source=AUTO_SOURCE,
                            matched_keyword=r.get('matched_name', ''),
                            matched_product_no=r.get('matched_pno', ''),
                            sell_factor=1))
    if to_save:
        out['saved'] = save_profit_settlements(username, str(date), to_save)
    return out
