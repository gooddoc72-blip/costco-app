"""바코드 스캔 — Next.js 새 화면(/m/scan)이 부른다.

한 상품에 바코드가 여러 개다. 코스트코 가격표 바코드(shared_products.barcode)와
상품 박스의 제조사 바코드(880…)가 다르고, 같은 상품도 포장이 바뀌면 박스 바코드가
바뀐다. 그래서 바코드 ↔ 코스트코 상품번호를 별도 표(product_barcodes)에 쌓는다.
처음 보는 바코드는 한 번 상품을 골라 주면 그다음부터 바로 찾는다.

찾는 순서: product_barcodes → 상품번호 그대로 → 가격표 바코드(shared_products.barcode).

매장 가격 확인(🏷)은 공유 매입가를 바꾸는 일이라 관리자만 저장한다(/m/pricetag와 같다).
저장은 price_tag_service.save를 그대로 써서 '최근 저장 → 되돌리기'에도 같이 잡힌다.
"""
from datetime import datetime

from db_core import get_auth_db, retry_on_lock


def _digits(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit())


def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_barcodes (
            barcode    TEXT PRIMARY KEY,
            product_no TEXT NOT NULL,
            kind       TEXT DEFAULT 'box',   -- box(상품 박스) | label(가격표)
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pbc_pno ON product_barcodes(product_no)")


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
            "SELECT barcode FROM product_barcodes WHERE product_no=? ORDER BY created_at", (pno,))]
    finally:
        conn.close()


def find(code):
    """바코드·상품번호 → 공유상품 dict (없으면 None)."""
    from db_products import find_shared_by_code
    c = _digits(code)
    if not c:
        return None
    pno = _linked_pno(c)
    if pno:
        sp = find_shared_by_code(pno)
        if sp:
            return sp
    sp = find_shared_by_code(c)
    if sp:
        return sp
    # 코스트코 가격표 바코드는 상품번호를 앞 0으로 자릿수를 채우고 끝에 한 자리를 더 붙인다
    # (예: 상품번호 859695 → 바코드 08596950). 그대로는 못 찾으므로 감싼 자리를 벗겨 본다.
    for cand in _pno_candidates(c):
        sp = find_shared_by_code(cand)
        if sp and str(sp.get('product_no') or '') == cand:
            return sp
    return None


def _pno_candidates(c):
    """가격표 바코드 → 상품번호 후보(순서대로). 4~7자리만 — 상품번호 길이."""
    out = []
    for s in (c[:-1], c.lstrip('0')[:-1], c.lstrip('0'), c[1:-1]):
        s = s.lstrip('0')
        if 4 <= len(s) <= 7 and s != c and s not in out:
            out.append(s)
    return out


def link(code, product_no, username, is_admin=False, kind='box'):
    """바코드를 상품에 연결. 이미 다른 상품에 붙은 바코드는 관리자만 바꾼다.
    반환: {'linked': bool, 'product_no', 'prev'}"""
    from db_products import find_shared_by_code
    c, p = _digits(code), _digits(product_no)
    if not c or not p:
        raise ValueError("바코드와 상품번호가 필요합니다")
    if c == p:
        return {'linked': False, 'product_no': p, 'prev': ''}   # 상품번호 자체 — 연결할 필요 없음
    if not find_shared_by_code(p):
        raise ValueError(f"제품 DB에 상품번호 {p}가 없습니다")
    prev = _linked_pno(c)
    if prev == p:
        return {'linked': False, 'product_no': p, 'prev': prev}
    if prev and not is_admin:
        raise PermissionError(f"이 바코드는 이미 {prev}에 연결돼 있습니다 — 관리자에게 바꿔 달라고 하세요")

    def _w():
        conn = get_auth_db()
        try:
            _ensure(conn)
            conn.execute(
                "INSERT INTO product_barcodes (barcode, product_no, kind, created_by, created_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(barcode) DO UPDATE SET product_no=excluded.product_no, "
                "kind=excluded.kind, created_by=excluded.created_by, created_at=excluded.created_at",
                (c, p, kind if kind in ('box', 'label') else 'box', username,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        finally:
            conn.close()
    retry_on_lock(_w)
    return {'linked': True, 'product_no': p, 'prev': prev}


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
    sp = find(c)
    if not sp:
        return {'found': False, 'code': c}
    pno = str(sp.get('product_no') or '')
    return {
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

    스캔한 바코드는 shared_products.barcode(가격표 바코드)에 덮어쓰지 않는다 — 박스
    바코드일 수 있어서다. 대신 product_barcodes에 연결한다.
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
