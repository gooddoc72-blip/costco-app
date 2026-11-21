"""바코드 스캔 — Next.js 새 화면(/m/scan)이 부른다.

한 상품에 바코드가 여러 개다. 규칙은 barcode_core에 있다:
🏷 매장 바코드(가격표, shared_products.barcode) = 가격 추이용,
📦 상품 바코드(포장, product_barcodes kind='box') = 재고용.
처음 보는 상품 바코드는 한 번 상품을 골라 주면 그다음부터 바로 찾는다.

찾는 순서: product_barcodes → 상품번호 그대로 → 가격표 바코드(shared_products.barcode).

매장 가격 확인(🏷)은 공유 매입가를 바꾸는 일이라 관리자만 저장한다(/m/pricetag와 같다).
저장은 price_tag_service.save를 그대로 써서 '최근 저장 → 되돌리기'에도 같이 잡힌다.
"""
from datetime import datetime

from db_core import get_auth_db, retry_on_lock
import barcode_core as bc


def _digits(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit())


def _ensure(conn):
    bc.ensure(conn)


def _linked_pno(code):
    conn = get_auth_db()
    try:
        _ensure(conn)
        r = conn.execute("SELECT product_no FROM product_barcodes WHERE barcode=?", (code,)).fetchone()
        return str(r[0]) if r else ''
    finally:
        conn.close()


def barcodes_of(pno):
    conn = get_auth_db()
    try:
        _ensure(conn)
        return [str(r[0]) for r in conn.execute(
            "SELECT barcode FROM product_barcodes WHERE product_no=? AND COALESCE(kind,'box')='box' "
            "ORDER BY created_at", (pno,))]
    finally:
        conn.close()


def find(code):
    """바코드·상품번호 → 공유상품 dict (없으면 None)."""
    return _find(code)[0]


def _find(code):
    """→ (공유상품 dict|None, 찾은 방법). 방법: linked | exact | stripped | ''

    stripped = 가격표 바코드에서 상품번호를 벗겨 찾은 경우 — 호출 쪽이 자동 연결한다.
    """
    from db_products import find_shared_by_code
    c = _digits(code)
    if not c:
        return None, ''
    pno = _linked_pno(c)
    if pno:
        sp = find_shared_by_code(pno)
        if sp:
            return sp, 'linked'
    sp = find_shared_by_code(c)
    if sp:
        return sp, 'exact'
    # 코스트코 가격표 바코드는 상품번호를 앞 0으로 자릿수를 채우고 끝에 한 자리를 더 붙인다
    # (예: 상품번호 859695 → 바코드 08596950). 그대로는 못 찾으므로 감싼 자리를 벗겨 본다.
    for cand in _pno_candidates(c):
        sp = find_shared_by_code(cand)
        if sp and str(sp.get('product_no') or '') == cand:
            return sp, 'stripped'
    return None, ''


def _pno_candidates(c):
    return bc.pno_candidates(c)


def link(code, product_no, username, is_admin=False, kind='box', units=1):
    """가격 확인 화면에서 바코드를 상품에 연결.

    매장 가격표 바코드면 상품 DB의 매장 바코드 칸(비어 있을 때)에, 아니면 상품 포장
    바코드(product_barcodes)로 넣는다. 이미 다른 상품에 붙은 바코드는 관리자만 바꾼다.
    반환: {'linked': bool, 'product_no', 'prev', 'kind'}"""
    c, p = _digits(code), _digits(product_no)
    if not c or not p:
        raise ValueError("바코드와 상품번호가 필요합니다")
    if c == p:
        return {'linked': False, 'product_no': p, 'prev': '', 'kind': ''}   # 상품번호 자체
    if kind == 'label' or bc.is_store(c, p):
        return {'linked': bool(bc.set_store_barcode(p, c, username)), 'product_no': p,
                'prev': '', 'kind': 'store'}
    r = bc.link_box(c, p, username, is_admin=is_admin, units=units)
    return {**r, 'kind': 'box'}


def _stock(pno, username, is_admin):
    """재고 — 본인 몫, 전체 합계. 관리자에게만 보유자별 내역을 보여 준다."""
    from db_inventory import get_stock_summary
    mine, total, owners = 0, 0, {}
    for r in get_stock_summary():
        if str(r.get('product_no') or '') != pno:
            continue
        q = int(r.get('qty_left') or 0)
        total += q
        o = str(r.get('owner') or '')
        owners[o] = owners.get(o, 0) + q
        if o == username:
            mine += q
    out = {'mine': mine, 'total': total}
    if is_admin:
        out['owners'] = [{'owner': o, 'qty': q} for o, q in sorted(owners.items(), key=lambda x: -x[1])]
    return out


def lookup(code, username, is_admin=False):
    c = _digits(code)
    sp, how = _find(c)
    if not sp:
        return {'found': False, 'code': c}
    pno = str(sp.get('product_no') or '')
    # 찍은 바코드가 상품번호와 맞아 찾았으면 그 자리에서 바코드를 연결해 둔다
    # — 다음부터는 벗기기 없이 바로 찾고, 출고·재고 스캔도 같은 표를 쓴다.
    auto_linked = False
    if how == 'stripped' and pno:
        try:
            auto_linked = bool(link(c, pno, username, is_admin=False, kind='label').get('linked'))
        except Exception:
            auto_linked = False
    return {
        'auto_linked': auto_linked,
        'found': True,
        'code': c,
        'product_no': pno,
        'name': str(sp.get('costco_name') or ''),
        'cur_price': int(sp.get('store_price') or sp.get('unit_price') or 0),
        'online_price': int(sp.get('online_price') or 0),
        'price_at': str(sp.get('store_updated_at') or '')[:10],
        'split_qty': int(sp.get('split_qty') or 1),
        'image': str(sp.get('image_url') or ''),
        'label_barcode': str(sp.get('barcode') or ''),
        'barcodes': barcodes_of(pno) if pno else [],
        'stock': _stock(pno, username, is_admin) if pno else {'mine': 0, 'total': 0},
    }


def save_price(username, row, code=''):
    """매장 가격 확인 1건 저장 — 같은 값이어도 '확인한 날'(store_updated_at)이 오늘로 바뀐다.

    스캔한 바코드는 link()가 갈라 넣는다 — 매장 가격표 바코드면 매장 칸(비었을 때),
    상품 포장 바코드면 product_barcodes. 매장 칸을 덮어쓰지는 않는다.
    """
    import price_tag_service as pts
    pno = _digits(row.get('product_no'))
    r = pts.save(username, [{
        'product_no': pno, 'name': row.get('name') or '',
        'barcode': row.get('label_barcode') or '',
        'new_price': int(row.get('new_price') or 0),
        'final_price': int(row.get('final_price') or 0),
        'discount': int(row.get('discount') or 0),
        'sale_end': str(row.get('sale_end') or ''),
        'photo': 'scan',
    }])
    c = _digits(code)
    if c and pno and c != pno and c != _digits(row.get('label_barcode')):
        try:
            link(c, pno, username, is_admin=True)
        except Exception:
            pass
    return r
