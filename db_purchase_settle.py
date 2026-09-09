"""구매가 계산 — 주문 한 건에 지금 구매가를 매겨 보는 순수 조회.

청구액을 저장하지 않는다. 예전에는 여기서 예상(est)→확정(final) 스냅샷을
따로 쌓았고, 그 값이 영수증 정산 원장과 어긋나 화면마다 금액이 달랐다.
청구의 정본은 db_settle 하나고, 이 모듈은 '지금 제품DB 기준으로 얼마짜리인가'를
답해 매핑이 빈 상품을 찾는 데만 쓴다.
"""
from db_core import get_user_db


# ── 구매가 계산 (예상/확정 공통) ──────────────────────────────
def _dispatched_records(username, date):
    """그날 송장 등록(발송처리)한 주문 → compute_daily_purchase가 쓰는 형태.

    dispatch_log는 order_history와 JOIN하는데, 쿠팡·엑셀 업로드 계정은
    order_history가 비어 있는 경우가 많다(clglobal0919는 daily_orders에만
    있는 주문이 980건). 그때는 daily_orders에서 상품번호·수량을 보충한다.
    """
    from db import get_dispatched_orders_with_details, get_user_db

    rows = get_dispatched_orders_with_details(username, str(date)) or []
    if not rows:
        return []
    _need = [str(r.get('order_no') or '') for r in rows
             if not str(r.get('product_no') or '').strip()]
    _fill = {}
    if _need:
        try:
            conn = get_user_db(username)
            CHUNK = 900                       # SQLite 변수 한도
            for i in range(0, len(_need), CHUNK):
                _c = _need[i:i + CHUNK]
                _ph = ",".join("?" * len(_c))
                for _r in conn.execute(
                        "SELECT order_no, product_no, product_name, qty "
                        "FROM daily_orders WHERE order_no IN (%s)" % _ph, _c):
                    _fill[str(_r['order_no'])] = _r
            conn.close()
        except Exception:
            _fill = {}
    out = []
    for r in rows:
        _ono = str(r.get('order_no') or '')
        _f = _fill.get(_ono)
        out.append({
            '_sk': _ono,
            '수취인명': r.get('recipient') or '',
            '상품명': (r.get('product_name') or (_f['product_name'] if _f else '') or ''),
            'product_no': (str(r.get('product_no') or '').strip()
                           or (str(_f['product_no'] or '') if _f else '')),
            '수량': int(r.get('qty') or (_f['qty'] if _f else 1) or 1),
        })
    return out


def compute_daily_purchase(username, date, basis='dispatch'):
    """(items, goods_total) 반환. 각 item: 주문 상품별 구매가(현재 공유/제품DB 기준).
    영수증 반영 전=예상, 반영 후 재호출=확정. 순수 조회(저장 없음).

    basis:
      'dispatch' — 그날 **송장 등록(발송처리)** 한 주문 기준. 기본값.
                   실제 업무 흐름이 '발송처리 → 다음날 영수증 등록 → 매칭'이라
                   청구 대상은 그날 내보낸 물건이어야 한다.
      'order'    — 주문일 기준(구버전). 발송 이력이 없는 계정 확인용.
    """
    from pages_lib.profit_calc.loader import build_settlement_df
    from services import match_product_to_db, resolve_pack_factor, resolve_split_qty
    from db import get_all_products, get_shared_products
    import pandas as _pd

    if basis == 'dispatch':
        _recs = _dispatched_records(username, date)
        if not _recs:
            return [], 0
        df = _pd.DataFrame(_recs)
    else:
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
            # 상품명 소분 규칙 우선 — 사용자 제품DB 폴백 경로도 규칙이 걸리게 한다
            sq = resolve_split_qty(p, name)
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


def suggest_shared_matches(product_name, shared_prods=None, top=5):
    """상품명 → 공유DB 후보 상위 N. (코스트코번호 연결 도우미용)

    싼 토큰 점수로 후보를 좁힌 뒤 종합 점수(용량·브랜드 반영)로 다시 세운다.
    3,891개를 전부 종합 점수로 재면 화면이 눈에 띄게 느려진다.
    반환: [{'product_no','costco_name','unit_price','split_qty','score'}]
    """
    from services import _token_score, _combined_match_score
    from db import get_shared_products
    _nm = str(product_name or '').strip()
    if not _nm:
        return []
    _sp = shared_prods if shared_prods is not None else (get_shared_products() or [])
    _pre = []
    for _p in _sp:
        _cn = str(_p.get('costco_name') or '')
        if not _cn:
            continue
        _t = _token_score(_nm, _cn)
        if _t > 0:
            _pre.append((_t, _p))
    _pre.sort(key=lambda x: -x[0])
    _out = []
    for _t, _p in _pre[:40]:
        _sc = _combined_match_score(_nm, str(_p.get('costco_name') or ''))['total']
        _out.append({'product_no': str(_p.get('product_no') or ''),
                     'costco_name': str(_p.get('costco_name') or ''),
                     'unit_price': int(_p.get('unit_price') or 0),
                     'split_qty': max(1, int(_p.get('split_qty') or 1)),
                     'score': round(_sc, 3)})
    _out.sort(key=lambda x: -x['score'])
    return _out[:int(top)]


def link_product_mapping(username, naver_no, product_name, costco_no, split_qty=1):
    """사용자 제품DB에 '네이버번호 → 코스트코번호 + 소분수'를 기입한다.

    구매금액이 0원으로 나오는 근본 원인은 이 매핑이 없어서다. 주문은 네이버번호로
    들어오는데 가격은 공유DB에 코스트코번호로 있어, 둘을 잇지 못하면 값을 못 찾는다.
    (oxo 1,345개 중 코스트코번호가 있는 건 312개뿐)

    소분수는 사용자 레코드에 쓴다 — match_product_to_db가 사용자 항목이 있으면
    사용자 split_qty를 우선하도록 설계돼 있다(소분을 하는 사람과 안 하는 사람이
    같은 상품을 다르게 팔 수 있어서다).
    반환: 'updated' | 'inserted' | '' (실패)
    """
    from db import get_user_db
    _nv = str(naver_no or '').strip()
    _cno = str(costco_no or '').strip()
    _sq = max(1, int(split_qty or 1))
    _nm = str(product_name or '').strip()
    if not (username and _cno):
        return ''
    # 네이버번호가 코스트코번호 칸에 들어가는 것을 막는다(4~7자리)
    try:
        from services import is_costco_pno
        if not is_costco_pno(_cno):
            return ''
    except Exception:
        pass
    # 공유맵에도 남긴다 (코스트코번호는 상품 고유값)
    if _nv:
        try:
            from db import upsert_shared_naver_map
            upsert_shared_naver_map(_cno, username, naver_pno=_nv, product_name=_nm,
                                    source='confirm')
        except Exception:
            pass
    conn = get_user_db(username)
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
        _keys = [c for c in ('naver_channel_pno', 'naver_origin_pno') if c in _cols]
        _now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if _nv and _keys:
            _where = " OR ".join("TRIM(COALESCE(%s,''))=?" % c for c in _keys)
            cur = conn.execute(
                "UPDATE products SET product_no=?, split_qty=?, updated_at=? WHERE (%s)" % _where,
                [_cno, _sq, _now] + [_nv] * len(_keys))
            if cur.rowcount:
                conn.commit()
                return 'updated'
        if not _nm:
            return ''
        for _mk in (_nm[:180], "%s#%s" % (_nm[:170], _nv or _cno)):
            try:
                conn.execute(
                    "INSERT INTO products (product_no, store_product_name, costco_name,"
                    " match_keyword, unit_price, split_qty, updated_at, naver_channel_pno)"
                    " VALUES (?,?,?,?,0,?,?,?)",
                    (_cno, _nm, _nm, _mk, _sq, _now, _nv))
                conn.commit()
                return 'inserted'
            except sqlite3.IntegrityError:
                continue
        return ''
    finally:
        try:
            conn.close()
        except Exception:
            pass
