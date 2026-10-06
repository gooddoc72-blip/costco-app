# -*- coding: utf-8 -*-
"""상품 찾기 공용 — 영수증 구매이력 + 공용 가격DB를 이름·번호로 찾는다.

재고 직접 입고(inventory_page)와 수동 입출고(receipt_settle_page)가 같이 쓴다.
화면마다 따로 두면 한쪽만 고쳐져 '여기선 나오는데 저기선 안 나온다'가 된다.
"""
from db import get_shared_products


def name_key(s):
    """상품명 비교용 정규화 — 띄어쓰기·기호를 지우고 소문자로.

    영수증 축약어('KS메이플시럽1L')와 네이버 상품명('커클랜드 시그니처 메이플
    시럽 1L 유기농')은 띄어쓰기와 기호가 달라 LIKE 한 방으로는 절대 안 맞는다.
    """
    import re as _re
    return _re.sub(r'[^0-9a-z가-힣]', '', str(s or '').lower())


def name_tokens(s):
    """검색어에서 **반드시 들어 있어야 할** 토막만 뽑는다.

    'KS메이플시럽1L' → ['메이플시럽']

    한글·영문·숫자 덩어리로 자른 뒤 세 글자 이상만 남긴다. 'KS'·'1L' 같은
    짧은 토막을 필수로 걸면 안 된다 — 영수증 축약어의 브랜드 머리글자(KS,
    CJ)와 용량 표기는 네이버 상품명에 없는 경우가 더 많아서, 요구하는 순간
    진짜 그 상품까지 걸러진다('KS메이플시럽1L'로 '커클랜드 시그니처 유기농
    메이플 시럽 1L'을 못 찾는다).

    세 글자 이상이 하나도 없으면(예: '우유') 두 글자까지 받아 준다.
    """
    import re as _re
    _t = _re.findall(r'[0-9]+|[a-z]+|[가-힣]+', str(s or '').lower())
    _long = [x for x in _t if len(x) >= 3]
    return _long or [x for x in _t if len(x) >= 2]


#: 영수증·카탈로그가 한쪽은 한글, 한쪽은 영문으로 적는 브랜드 — 서로 바꿔서도 찾는다
BRAND_ALIAS = {
    '팬틴': 'pantene', '커클랜드': 'kirkland', '스타벅스': 'starbucks', '다우니': 'downy',
    '타이드': 'tide', '바운티': 'bounty', '세타필': 'cetaphil', '뉴트로지나': 'neutrogena',
    '필립스': 'philips', '브라운': 'braun', '다이슨': 'dyson', '페리에': 'perrier',
    '에비앙': 'evian', '고디바': 'godiva', '헤드앤숄더': 'headshoulders', '오랄비': 'oralb',
    '질레트': 'gillette', '크레스트': 'crest', '하기스': 'huggies', '네슬레': 'nestle',
}


def query_variants(q):
    """검색어 + 브랜드 한↔영 바꾼 것들 — [(정규화 키, 필수 토막)]."""
    _base = str(q or '').strip().lower()
    _vs = {_base}
    for ko, en in BRAND_ALIAS.items():
        if ko in _base:
            _vs.add(_base.replace(ko, en))
        if en in name_key(_base):
            _vs.add(name_key(_base).replace(en, ko))
    return [(name_key(v), name_tokens(v)) for v in _vs if v]


def search_products(q, limit=50):
    """재고 직접 입고용 상품 찾기 — 영수증 구매이력 + 공용 DB를 상품번호로 합친다.

    반환: [{product_no, name, paid(최근 영수증 실결제 단가), list(정가),
           receipt_date, store(공용 DB 매장가)}] — 최근 구매한 것이 먼저.
    """
    _qs = str(q or '').strip()
    _vars = query_variants(_qs)

    def _hit(name, pno):
        _k = name_key(name)
        if _qs and _qs in str(pno or ''):
            return True
        return any((vk and vk in _k) or (vt and all(t in _k for t in vt))
                   for vk, vt in _vars)

    out = {}
    # ① 영수증 구매이력 — 상품번호별 가장 최근 줄
    try:
        from db_stats import _receipt_conn
        conn = _receipt_conn()
        try:
            rows = conn.execute(
                "SELECT product_no, product_name, unit_price, "
                "COALESCE(list_price,0) AS list_price, receipt_date FROM receipt_items "
                "ORDER BY receipt_date DESC, id DESC"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            _pn = str(r['product_no'] or '').strip()
            # 번호를 못 읽은 품목도 낸다 — 이름으로 묶고, 번호는 고른 뒤 직접 넣는다
            _key = _pn or ('name:' + name_key(r['product_name']))
            if _key in out or not _hit(r['product_name'], _pn):
                continue
            out[_key] = {'product_no': _pn, 'name': str(r['product_name'] or ''),
                        'paid': int(r['unit_price'] or 0),
                        'list': int(r['list_price'] or 0) or int(r['unit_price'] or 0),
                        'receipt_date': str(r['receipt_date'] or ''), 'store': 0}
    except Exception:
        pass
    # ② 공용 DB — 영수증에 있으면 이름(카탈로그 정식명)·매장가만 보탠다
    try:
        for s in (get_shared_products() or []):
            _pn = str(s.get('product_no') or '').strip()
            _key = _pn or ('name:' + name_key(s.get('costco_name')))
            _sp = int(s.get('store_price') or 0) or int(s.get('unit_price') or 0)
            if _key in out:
                out[_key]['store'] = _sp
                if s.get('costco_name'):
                    out[_key]['name'] = str(s['costco_name'])
                continue
            if _hit(s.get('costco_name'), _pn):
                out[_key] = {'product_no': _pn, 'name': str(s.get('costco_name') or ''),
                            'paid': 0, 'list': 0, 'receipt_date': '', 'store': _sp}
    except Exception:
        pass
    # 영수증 건은 최근 구매순으로 이미 들어 있다 — 그 뒤에 공용 DB 건
    _res = ([h for h in out.values() if h['receipt_date']]
            + [h for h in out.values() if not h['receipt_date']])
    return _res[:limit]
