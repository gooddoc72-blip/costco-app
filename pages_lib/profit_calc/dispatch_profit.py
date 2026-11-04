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
from pages_lib.profit_calc.compute import compute_rows, compute_profit, settled_shipping, SETTLED_SOURCE
from pages_lib.profit_calc.enrich import (
    build_receipt_maps, build_kw_overrides, load_surcharge_map, load_settled_cost_map,
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


def _saved_val(v):
    """저장값 — 저장된 적 없으면(None·NaN) None. int(NaN)은 ValueError라 0으로 떨어지면 안 된다."""
    try:
        if v is None or v != v:          # NaN은 자기 자신과 같지 않다
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def apply_actual_settlements(rows, act_map, cp_map):
    """정산예정금액 → 실제 정산액(확정된 주문만). page.py 557-603과 같은 규칙.

    네이버: settlement_matches의 actual. 쿠팡: 주문(orderId) 전체 정산액을 그 주문의 아이템
    행들에 추정 정산액 비율로 나눈다 — 통째로 넣으면 다품목 주문이 2배로 잡힌다. 마지막 행이
    나머지를 받아 합계가 정확히 맞는다. rows를 그대로 고치고 'actual' 표시를 단다.
    """
    for r in rows:
        r.setdefault('actual', False)
        av = (act_map or {}).get(str(r['order_no']))
        if av and _int(av.get('actual')) > 0:
            r['settlement_amount'] = _int(av['actual'])
            r['actual'] = True
    groups = {}
    for r in rows:
        if '-' in str(r['order_no']):
            groups.setdefault(str(r['order_no']).split('-')[0], []).append(r)
    for oid, grp in groups.items():
        cv = (cp_map or {}).get(oid)
        if not (cv and _int(cv.get('settlement')) > 0):
            continue
        total = _int(cv['settlement'])
        if len(grp) == 1:
            grp[0]['settlement_amount'], grp[0]['actual'] = total, True
            continue
        bases = [max(0, _int(r['settlement_amount'])) for r in grp]
        bsum, alloc = sum(bases), 0
        for j, r in enumerate(grp):
            if j == len(grp) - 1:
                val = total - alloc
            elif bsum > 0:
                val = int(round(total * bases[j] / bsum)); alloc += val
            else:
                val = int(round(total / len(grp))); alloc += val
            r['settlement_amount'], r['actual'] = int(val), True
    return rows


def compute_dispatch_rows(username, date, use_saved=True, page_rules=False, overrides=None,
                          links_out=None):
    """날짜별 정산표 행 — build_settlement_df + compute_rows(구입가·매칭) + compute_profit.

    use_saved=True: 정산저장(ps)된 주문은 저장된 구입가·택배·박스를 쓴다(화면·API).
                    **저장 안 된 주문은 새로 계산한다** — 같은 날짜에 저장분이 섞여 있어도.
    use_saved=False: 저장값을 무시하고 지금 매칭·설정으로 새로 계산한다(자동 저장용).
    page_rules=True: Streamlit 수익계산 화면과 똑같이 — 실정산 확정액, 포장 배정 박스비.
    overrides: {order_no: {unit_cost, keyword, receipt_pno, receipt_name, receipt_unit,
                delivery, box}} — 화면에서 고친 값(저장 전 미리보기·저장 공용).
    반환: (rows, src_label, kind, saved). rows는 dict 목록.
    """
    df, src_label, kind = build_settlement_df(username, date)
    saved = (kind == 'ps')
    if df is None or df.empty:
        return [], src_label, kind, saved
    _use = saved and use_saved
    _ps = {}
    if _use:
        _ps = {str(r.get('order_no') or ''): r for r in (get_profit_settlements(username, date) or [])}
    ov = {str(k): (v or {}) for k, v in (overrides or {}).items()}

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
    receipt_pick = {}
    for r in rows_in:
        o = ov.get(r['idx']) or {}
        _kw = str(o.get('receipt_name') or o.get('keyword') or '').strip()
        if _kw:                             # 화면에서 고른 키워드·영수증 품목 — compute의 복합키로
            _rk = f"{r['수취인명']}_{r['상품명']}_{r['idx']}_{date}"
            kw_overrides[_rk] = _kw
            if o.get('receipt_pno'):
                receipt_pick[_rk] = str(o['receipt_pno'])
    surcharge_map = load_surcharge_map(username, [r['idx'] for r in rows_in])
    settled_map = load_settled_cost_map(username, [r['idx'] for r in rows_in])
    results, _links = compute_rows(
        rows_in, match_fn=match_fn,
        receipt_by_pno=receipt_by_pno, receipt_matches=receipt_matches,
        kw_overrides=kw_overrides, receipt_pick=receipt_pick,
        surcharge_map=surcharge_map, calc_date_str=date, settled_map=settled_map)

    pkg_map = {}
    if page_rules:
        try:
            from db_packaging import get_packaging_cost_map
            pkg_map = get_packaging_cost_map(username, [str(r.get('_sk', '')) for r in records]) or {}
        except Exception:
            pkg_map = {}

    out = []
    for rec, res in zip(records, results):
        settlement = _int(rec.get('정산예정금액', 0))
        shipping = _int(rec.get('배송비 합계', 0))
        ono = str(rec.get('_sk', ''))
        o = ov.get(ono) or {}
        sv = _ps.get(ono)
        qty = max(1, _int(rec.get('수량', 1), 1))
        _src = str(res.get('source', ''))
        _sv_cost = _int((sv or {}).get('cost_price')) if sv else 0
        if o.get('receipt_unit') is not None and (o.get('receipt_name') or o.get('receipt_pno')):
            cost, _src = _int(o['receipt_unit']) * qty, '영수증(선택)'
        elif o.get('unit_cost') is not None:
            cost = _int(o['unit_cost']) * qty
            if cost > 0:
                _src = '수동입력'
        elif _use and sv and _sv_cost > 0 and _src != SETTLED_SOURCE \
                and not str(o.get('keyword') or '').strip():
            # (화면에서 매칭 상품을 바꾼 행은 저장값 대신 바꾼 상품으로 다시 계산한다)
            # 저장값 — 단 영수증 정산 청구가 확정된 주문은 청구액(빌린 금액 포함)이 저장값보다 우선이다
            cost = _sv_cost
        else:
            # 타인재고 웃돈(+원가) 가산 — page.py 후처리(costs += surcharge)와 동일
            # 정산청구 행은 청구액에 이미 빌린 금액(웃돈 포함)이 들어 있다 — 다시 더하지 않는다
            _sur = 0 if _src == SETTLED_SOURCE else _int(surcharge_map.get(ono, 0))
            cost = _int(res.get('cost', 0)) + _sur
            if _sur and '타인재고' not in _src:
                _src = f"{_src}+타인재고"
        _sv_d = _saved_val((sv or {}).get('delivery_cost')) if _use else None
        _sv_b = _saved_val((sv or {}).get('box_cost')) if _use else None
        # 행별 택배·박스 우선순위(page.py 622-632): 화면 수정 → 저장값 → (박스: 포장 배정) → 설정
        deliv = (_int(o['delivery']) if o.get('delivery') is not None
                 else _sv_d if _sv_d is not None else def_ship)
        box = (_int(o['box']) if o.get('box') is not None
               else _sv_b if _sv_b is not None else _int(pkg_map.get(ono, def_box), def_box))
        _kw = str(o.get('receipt_name') or o.get('keyword') or '').strip()
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
            'saved': bool(sv),
            'keyword': _kw,
            'edited': bool(o),
        })
    if page_rules:
        try:
            from db import get_actual_settlements_map, get_coupang_settled_map
            apply_actual_settlements(out, get_actual_settlements_map(username),
                                     get_coupang_settled_map(username))
        except Exception:
            pass
        for r in out:
            r['profit'] = compute_profit(r['settlement_amount'], r['shipping_fee'], r['cost_price'],
                                         r['delivery_cost'], r['box_cost'])
    # 자동링크(영수증 이름매칭으로 알게 된 코스트코번호)는 저장할 때 반영한다 — 호출자가 받아 간다
    if links_out is not None:
        links_out.extend(_links)
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
