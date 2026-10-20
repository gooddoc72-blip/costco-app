"""구매가 계산 — 주문 한 건에 지금 구매가를 매겨 보는 순수 조회.

청구액을 저장하지 않는다. 예전에는 여기서 예상(est)→확정(final) 스냅샷을
따로 쌓았고, 그 값이 영수증 정산 원장과 어긋나 화면마다 금액이 달랐다.
청구의 정본은 db_settle 하나고, 이 모듈은 '지금 제품DB 기준으로 얼마짜리인가'를
답해 매핑이 빈 상품을 찾는 데만 쓴다.
"""
from datetime import datetime

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
    from services import _combined_match_score, name_score
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
        # 영수증 영문 약칭(KS STRAWBERRIES)은 한글로 바꾼 이름으로도 견준다
        _t, _used = name_score(_nm, _cn)
        if _t > 0:
            _pre.append((_t, _p, _used))
    _pre.sort(key=lambda x: -x[0])
    _out = []
    for _t, _p, _used in _pre[:40]:
        _sc = _combined_match_score(_nm, _used)['total']
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


# ── 전 사용자 가격 연결 정리 ─────────────────────────────────
#: 연결 후보의 근거 — 위일수록 확실하다. 확실한 것만 화면에서 기본 체크한다.
LINK_SOURCE_LABEL = {
    'history': '정산 이력',      # 영수증·재고로 실제 정산된 번호
    'map':     '공유 매핑',      # 공유 네이버↔코스트코 맵(영수증·관리자 확정)
    'name':    '이름 추정',      # 상품명 유사도 — 사람이 봐야 한다
}


def unlinked_sold_products(days=30, users=None):
    """최근 팔렸는데 관리자 가격DB(공유DB)와 **연결이 안 된** 사용자 상품 + 연결 후보.

    가격은 공유DB에 코스트코번호로 한 벌만 둔다. 사용자 상품은 네이버에서
    번호 없이 들어와 이 번호가 비면 관리자 영수증 가격을 영영 못 받는다
    (10/20 실측: 최근 30일 판매상품 496개 중 250개 미연결).
    팔린 적 없는 상품까지 정리하면 수천 개라, 실제로 팔린 것만 본다.

    후보 근거: 정산 이력 > 공유 매핑 > 이름 추정(사람 확인 필요).
    반환: [{username, naver_no, product_name, orders, cur_no, split_qty,
            cand_no, cand_name, cand_price, source, score}]
    """
    import sqlite3
    from datetime import date, timedelta
    from db import get_shared_products, get_all_users
    from db_core import AUTH_DB
    _since = str(date.today() - timedelta(days=int(days)))
    shared = get_shared_products() or []
    by_no = {str(p.get('product_no') or '').strip(): p for p in shared
             if str(p.get('product_no') or '').strip()}
    kw_set = {str(p.get('match_keyword') or '') for p in shared}
    id_set = {int(p['id']) for p in shared if p.get('id') is not None}

    # 정산 이력·공유 매핑 — (사용자, 네이버번호) → 코스트코번호
    hist, smap = {}, {}
    a = sqlite3.connect(AUTH_DB)
    a.row_factory = sqlite3.Row
    try:
        for r in a.execute(
                "SELECT username, naver_no, product_no, COUNT(*) n FROM settle_item "
                "WHERE COALESCE(naver_no,'')!='' AND COALESCE(product_no,'')!='' "
                "GROUP BY 1,2,3"):
            hist.setdefault((r['username'], str(r['naver_no'])), {})[str(r['product_no'])] = r['n']
        try:
            for r in a.execute("SELECT * FROM shared_naver_map"):
                for k in ('naver_pno', 'naver_origin_pno'):
                    if r[k]:
                        smap[(r['username'], str(r[k]))] = (str(r['costco_pno'] or ''),
                                                            str(r['source'] or ''))
        except Exception:
            pass
    finally:
        a.close()

    _users = users or [u['username'] for u in get_all_users()
                       if (u.get('status') or 'active') == 'active']
    import os
    from db_core import DATA_DIR
    out = []
    for u in _users:
        # DB가 없는 계정을 열면 빈 파일이 새로 생긴다 — 있는 것만 본다
        if not os.path.exists(os.path.join(DATA_DIR, f"{u}.db")):
            continue
        try:
            c = get_user_db(u)
        except Exception:
            continue
        try:
            tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'daily_orders' not in tabs or 'products' not in tabs:
                continue
            sold = {}
            for r in c.execute(
                    "SELECT product_no, product_name, COUNT(*) n FROM daily_orders "
                    "WHERE order_date>=? AND COALESCE(product_no,'')!='' "
                    "GROUP BY product_no", (_since,)):
                sold[str(r['product_no'])] = (str(r['product_name'] or ''), int(r['n']))
            prods = [dict(r) for r in c.execute("SELECT * FROM products")]
        finally:
            c.close()
        idx = {}
        for p in prods:
            for k in ('naver_channel_pno', 'naver_origin_pno', 'product_no'):
                v = str(p.get(k) or '').strip()
                if v:
                    idx.setdefault(v, p)
        for nv, (pname, n) in sold.items():
            p = idx.get(nv) or {}
            cur = str(p.get('product_no') or '').strip()
            if p and ((cur and cur in by_no)
                      or str(p.get('match_keyword') or '') in kw_set
                      or (p.get('linked_shared_id') is not None
                          and int(p['linked_shared_id']) in id_set)):
                continue                            # 이미 공유DB와 이어져 있다
            keys = [nv] + [str(p.get(k) or '') for k in ('naver_channel_pno', 'naver_origin_pno')
                           if str(p.get(k) or '') and str(p.get(k)) != nv]
            cand, src, score = '', '', 0.0
            _h = {}
            for k in keys:
                for cn, cnt in (hist.get((u, k)) or {}).items():
                    if cn in by_no:
                        _h[cn] = _h.get(cn, 0) + cnt
            if _h:
                cand = max(_h, key=_h.get)
                src, score = 'history', 1.0
            if not cand:
                for k in keys:
                    cn, _s = smap.get((u, k), ('', ''))
                    if cn in by_no:
                        cand, src, score = cn, 'map', 0.9
                        break
            if not cand:
                _nm = str(p.get('costco_name') or '') or pname
                _sg = suggest_shared_matches(_nm, shared_prods=shared, top=1)
                if _sg and _sg[0].get('product_no'):
                    cand, src, score = _sg[0]['product_no'], 'name', float(_sg[0]['score'])
            sp = by_no.get(cand) or {}
            # 이력·매핑도 틀릴 수 있다 — 예전 오매칭이 이력으로 남는다
            # (ARLA 고다 슬라이스 → 닭가슴살슬라이스, 길리안 템테이션 → MERCI).
            # 상품명 유사도를 함께 보여 주고, 화면은 유사도가 낮으면 기본 체크하지 않는다.
            _sim = 0.0
            if sp:
                try:
                    from services import name_score, _combined_match_score
                    _nm2 = str(p.get('costco_name') or '') or pname
                    _, _used = name_score(_nm2, str(sp.get('costco_name') or ''))
                    _sim = float(_combined_match_score(_nm2, _used)['total'])
                except Exception:
                    _sim = 0.0
            out.append({
                'sim': round(_sim, 2),
                'username': u, 'naver_no': nv,
                'product_name': str(p.get('costco_name') or '') or pname,
                'orders': n, 'cur_no': cur, 'in_db': bool(p),
                'split_qty': max(1, int(p.get('split_qty') or 1)) if p else 1,
                'cand_no': cand, 'cand_name': str(sp.get('costco_name') or ''),
                'cand_price': int(sp.get('store_price') or 0) or int(sp.get('unit_price') or 0),
                'source': src, 'score': round(score, 2),
            })
    out.sort(key=lambda r: ({'history': 0, 'map': 1, 'name': 2}.get(r['source'], 3),
                            -r['orders']))
    return out
