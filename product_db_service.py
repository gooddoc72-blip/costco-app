"""제품 DB 화면 로직 — /m/products(API)가 부른다. Streamlit 제품 DB 화면과 같은 규칙.

분류·검색·표시값을 화면 코드에서 떼어 낸 것이다. 화면마다 따로 짜면 '품절'이나
'자체등록'의 뜻이 화면마다 달라진다(실제로 /m 옛 화면은 개인 행만 보여서 매장가·
상태가 아예 없었다). 판정은 pages_lib/product_db_page.py 1176-1254와 같다.
"""
from db_products import (get_all_products, get_all_products_merged, price_db_focus)

OOS = ('OUTOFSTOCK', 'SOLD_OUT', 'SOLDOUT')
SUSP = ('SUSPENSION', 'STOP', 'PAUSE', 'CLOSE', 'PROHIBITION')

#: 분류 — (키, 화면 이름). 첫 번째가 기본이다.
KINDS = (
    ('focus', '⭐ 팔았거나 산 상품'),
    ('all', '전체'),
    ('online', '🌐 코스트코 온라인'),
    ('naver', '🛍 네이버 등록'),
    ('selfreg', '🛠 네이버 자체등록'),
    ('split', '🔵 소분'),
    ('oos', '🟠 품절'),
    ('susp', '🚫 판매중지'),
)
UNCAT = '(미분류)'


def _s(v):
    return str(v or '').strip()


def _status(p):
    return (_s(p.get('status')) or 'SALE').upper()


def is_naver(p):
    return (int(p.get('from_naver') or 0) == 1
            or bool(_s(p.get('naver_product_no'))) or bool(_s(p.get('naver_origin_pno'))))


def is_selfreg(p):
    """코스트코번호 없이 네이버번호로만 등록된 상품(코스트코 미매칭)."""
    return not _s(p.get('product_no')) and bool(
        _s(p.get('naver_channel_pno')) or _s(p.get('naver_origin_pno')))


def is_split(p):
    return int(p.get('split_qty', 1) or 1) > 1


def _kind_fn(kind, focus):
    ordered, bought = focus
    return {
        'focus': lambda p: (_s(p.get('product_no')) in bought or bool(
            {_s(p.get(k)) for k in ('naver_channel_pno', 'naver_origin_pno',
                                     'naver_product_no')} & ordered)),
        'all': lambda p: True,
        'online': lambda p: (p.get('price_type') or '') == '온라인',
        'naver': is_naver,
        'selfreg': is_selfreg,
        'split': is_split,
        'oos': lambda p: _status(p) in OOS,
        'susp': lambda p: _status(p) in SUSP,
    }.get(kind)


def _view(p, split_orig, naver_mode):
    """화면 한 줄 — 숫자·배지 판단을 서버에서 끝내서 화면은 그리기만 한다."""
    store = int(p.get('store_price') or 0)
    online = int(p.get('online_price') or 0)
    if store == 0 and online == 0 and p.get('unit_price'):
        if (p.get('price_type') or '매장') == '온라인':
            online = int(p.get('unit_price') or 0)
        else:
            store = int(p.get('unit_price') or 0)
    sq = int(p.get('split_qty', 1) or 1)
    st = _status(p)
    costco_no = split_orig or _s(p.get('product_no'))
    naver_name = _s(p.get('naver_name'))
    costco_name = _s(p.get('costco_name'))
    if naver_mode:
        name, sub = (naver_name or costco_name), ('' if naver_name else '네이버명 없음')
    else:
        name = naver_name or costco_name
        sub = costco_name if (naver_name and naver_name != costco_name) else ''
    return {
        'key': f"{p.get('shared_id') or ''}:{p.get('private_id') or ''}:{p.get('match_keyword') or ''}",
        'shared_id': p.get('shared_id'),
        'private_id': p.get('private_id'),
        'is_shared': p.get('shared_id') is not None,
        'match_keyword': p.get('match_keyword') or '',
        'name': name, 'sub_name': sub, 'costco_name': costco_name,
        'image_url': p.get('image_url') or '',
        'costco_no': costco_no,
        # 코스트코번호는 6~7자리. 9자리 이상이면 네이버번호가 잘못 들어간 것이다.
        'costco_no_bad': costco_no.isdigit() and len(costco_no) >= 9,
        'naver_no': (_s(p.get('naver_product_no')) or _s(p.get('naver_channel_pno'))
                     or _s(p.get('naver_origin_pno'))),
        'split_locked': bool(split_orig),
        'unit_price': int(p.get('unit_price') or 0),
        'store_price': store, 'online_price': online,
        'piece_price': (store // sq) if (sq > 1 and store > 0) else 0,
        'split_qty': sq,
        'sale_price': int(p.get('sale_price', 0) or 0),
        'shipping_fee': int(p.get('shipping_fee', 0) or 0),
        'status': 'oos' if st in OOS else ('susp' if st in SUSP else 'sale'),
        'category': p.get('category') or '',
        'naver_category_id': p.get('naver_category_id') or '',
        'updated': _s(p.get('shared_updated_at'))[:10],
    }


def list_products(username, kind='focus', category='', q='', page=1, per=20, is_admin=False):
    """분류·카테고리·검색·페이지를 적용한 목록.

    반환: {kinds:[{key,label,count}], kind, categories:[{name,count}], category,
           total, page, pages, rows:[...], extra:[구매이력에만 있는 상품]}
    """
    products = get_all_products_merged(username)
    focus = price_db_focus(username)
    kind = kind if _kind_fn(kind, focus) else 'focus'
    kinds = [{'key': k, 'label': lbl, 'count': sum(1 for p in products if _kind_fn(k, focus)(p))}
             for k, lbl in KINDS]
    rows = [p for p in products if _kind_fn(kind, focus)(p)]

    cats = {}
    for p in rows:
        c = p.get('category') or UNCAT
        cats[c] = cats.get(c, 0) + 1
    categories = [{'name': c, 'count': n} for c, n in sorted(
        cats.items(), key=lambda x: (x[0] == UNCAT, x[0]))]
    if category:
        rows = [p for p in rows if (p.get('category') or UNCAT) == category]

    extra = []
    if _s(q):
        from product_search import matcher
        hit = matcher(q)
        rows = [p for p in rows if hit(
            (p.get('costco_name'), p.get('naver_name')),
            (p.get('product_no'), p.get('naver_origin_pno'), p.get('naver_product_no'),
             p.get('naver_channel_pno')))]
        # 가격DB에 없는 상품 — 영수증 구매이력에서 찾아 보여 준다(Streamlit '전체' 탭과 같음)
        if not category:
            try:
                from product_search import search_products
                have = {_s(p.get('product_no')) for p in rows}
                extra = [{'product_no': h['product_no'], 'name': h['name'], 'paid': h['paid'],
                          'list': h['list'], 'receipt_date': h['receipt_date']}
                         for h in search_products(q, limit=30,
                                                  only_owner=None if is_admin else username)
                         if h.get('receipt_date') and h['product_no'] not in have]
            except Exception:
                extra = []

    per = max(5, min(100, int(per or 20)))
    total = len(rows)
    pages = max(1, (total + per - 1) // per)
    page = max(1, min(int(page or 1), pages))
    chunk = rows[(page - 1) * per: page * per]

    # 가격 분리 원본번호는 합친 목록에 없다 — 개인 행에서 읽는다
    split_orig = {}
    if any(p.get('private_id') for p in chunk):
        split_orig = {r['id']: _s(r.get('costco_no_display'))
                      for r in get_all_products(username) if _s(r.get('costco_no_display'))}
    return {
        'kinds': kinds, 'kind': kind, 'categories': categories, 'category': category,
        'total': total, 'page': page, 'pages': pages,
        'rows': [_view(p, split_orig.get(p.get('private_id'), ''), kind == 'naver') for p in chunk],
        'extra': extra,
    }


def save_product(username, row, is_admin=False):
    """✏️ 저장 — 공유 상품이면 내 판매가·배송비·소분만, 개인 상품이면 전 필드.

    row: {shared_id, private_id, match_keyword, costco_name, product_no, unit_price,
          split_qty, sale_price, shipping_fee, naver_category_id}
    공유 상품의 매입가·상품명은 읽기전용이다(관리자 영수증이 원가의 근거).
    공유 카테고리 ID는 모든 판매자 등록에 쓰여 관리자만 바꾼다.
    """
    from db_products import (upsert_user_private, update_private_product,
                             set_shared_naver_category)
    if row.get('shared_id'):
        kw = _s(row.get('match_keyword'))
        if not kw:
            raise ValueError('상품 키가 없습니다')
        upsert_user_private(username, kw, _s(row.get('costco_name')) or kw,
                            sale_price=int(row.get('sale_price') or 0),
                            shipping_fee=int(row.get('shipping_fee') or 0),
                            split_qty=int(row.get('split_qty') or 1))
        if is_admin and row.get('naver_category_id') is not None:
            set_shared_naver_category(row['shared_id'], row.get('naver_category_id'))
        return {'ok': True}
    if not row.get('private_id'):
        raise ValueError('내 상품 행이 아닙니다')
    n = update_private_product(
        username, row['private_id'],
        product_no=row.get('product_no'), costco_name=row.get('costco_name'),
        unit_price=row.get('unit_price'), split_qty=row.get('split_qty'),
        sale_price=row.get('sale_price'), shipping_fee=row.get('shipping_fee'))
    return {'ok': n > 0}


def pack_rows(username):
    """🔢 묶음 상품 분류 필요 — 화면용으로 1주문 구입가격까지 계산해서 낸다."""
    from db_products import get_pack_ambiguous_products
    out = []
    for r in get_pack_ambiguous_products(username, only_unset=True):
        sq = max(1, int(r['split_qty'] or 1))
        out.append({
            'id': r['id'], 'name': r['store_product_name'] or r['costco_name'] or '',
            'unit_price': int(r['unit_price'] or 0), 'split_qty': sq,
            'name_factor': int(r['name_factor']), 'applied': int(r['applied']),
            'risky': bool(r['risky']),
            'cost_now': (int(r['unit_price'] or 0) // sq) * int(r['applied']),
        })
    return out


def option_rows(username, date_from, date_to):
    """🏷 옵션별 코스트코번호 표 + 같은 번호가 여러 옵션에 붙은 상품 수."""
    from db_orders import get_option_rows
    from db_products import get_option_map
    rows = get_option_rows(username, date_from, date_to) or []
    omap = get_option_map(username)
    out = [{
        'product_name': _s(r['product_name'])[:60],
        'naver_pno': _s(r['product_no']),
        'option_info': _s(r['option_info'])[:60],
        'option_code': _s(r['option_code']),
        'cnt': int(r['cnt'] or 0),
        'current_no': _s(r.get('costco_no')),
        'costco_no': _s(omap.get((_s(r['product_no']), _s(r['option_code'])), '')),
    } for r in rows]
    same = {}
    for r in out:
        if r['current_no']:
            k = (r['naver_pno'], r['current_no'])
            same[k] = same.get(k, 0) + 1
    return {'rows': out, 'dupe_products': sum(1 for v in same.values() if v > 1)}
