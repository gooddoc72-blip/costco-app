"""카페24 → 네이버 스마트스토어 대행 등록 — UI·크론 공용 로직.

pages_lib/cafe24_page.py(수동 선택 등록)와 auto_task.py(Task 10 배치 등록)가
같은 절차를 쓰도록 '상품 1건 등록'을 여기로 모았다.
두 곳에 복사해 두면 한쪽만 고쳐져 결과가 갈라진다.
"""
import re as _re
import html as _html

import cafe24_api
import naver_api
from utils import calc_match_score

# 카페24 자체상품코드가 비었을 때 상품명 매칭으로 코스트코 번호를 찾는 최소 점수.
# 매칭 화면 기준(>=2)은 사람이 검토하는 표 용도라 자동 적용엔 위험하다
# (무관한 상품이 같은 번호로 붙어 원가 계산까지 오염된다).
MATCH_MIN = 60

# 상세페이지에 쌓을 카페24 이미지 최대 장수.
DETAIL_IMG_LIMIT = 20


def fetch_image_bytes(url, timeout=15):
    """이미지 URL → (bytes, media_type). 실패 시 (None, None)."""
    import requests as _rq
    try:
        r = _rq.get(url, timeout=timeout)
        if r.status_code != 200 or not r.content:
            return None, None
        ct = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ct not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
            _u = str(url).lower()
            ct = ('image/png' if '.png' in _u else
                  'image/webp' if '.webp' in _u else 'image/jpeg')
        return r.content, ct
    except Exception:
        return None, None


def cafe24_detail_images(full, mall_id=''):
    """카페24 상품 상세 HTML/이미지에서 상세페이지 이미지 URL 목록 추출(순서 유지·절대경로).
    설명(description) 안의 <img>들 + 대표 상세이미지.

    mall_id를 넘기면 '/web/upload/...' 같은 루트상대 경로도 살린다. 카페24
    에디터가 넣는 이미지는 대개 이 형태라, 안 넘기면 상세페이지 뒷부분이
    통째로 비어 보인다."""
    urls = []
    desc = str((full or {}).get('description') or '')
    for m in _re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', desc, _re.I):
        urls.append(m.group(1))
    for k in ('detail_image', 'list_image'):
        u = (full or {}).get(k)
        if u:
            urls.append(u)
    _base = ('https://%s.cafe24.com' % str(mall_id).strip()) if mall_id else ''
    seen, out = set(), []
    for u in urls:
        u = str(u).strip()
        if u.startswith('//'):
            u = 'https:' + u
        elif u.startswith('/') and _base:
            u = _base + u              # 루트상대 → 몰 도메인 기준 절대경로
        if u and u.startswith('http') and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def build_image_detail(full, tid, tsecret, limit=DETAIL_IMG_LIMIT, mall_id=''):
    """카페24 상세이미지들을 네이버 CDN에 업로드 → <img> 스택 HTML 반환.
    업로드 성공분이 없으면 '' 반환(호출측에서 기존 HTML 폴백)."""
    _cdns = []
    for _iu in cafe24_detail_images(full, mall_id=mall_id)[:limit]:
        # square=False: 상세이미지는 세로로 길다 → 정사각 크롭하면 위·아래가 잘린다.
        _dc, _de = naver_api.upload_product_image(tid, tsecret, _iu, square=False)
        if _dc:
            _cdns.append(_dc)
    if not _cdns:
        return ''
    return ''.join(
        '<img src="%s" style="display:block;max-width:100%%;margin:0 auto">' % _u
        for _u in _cdns)


def calc_sale_price(cafe24_price, margin_pct):
    """카페24 판매가 + 마진% → 네이버 판매가(수수료 5.5% 보정, 10원 단위 반올림)."""
    try:
        _pr = int(float(cafe24_price or 0))
    except Exception:
        _pr = 0
    return int(round(_pr * (1 + float(margin_pct) / 100.0) / 0.945 / 10) * 10)


def load_existing(tid, tsecret):
    """대상 스토어 기존 상품 → (판매자상품코드 집합, 상품명 집합, err).
    중복 등록 방지용. 조회 실패 시 빈 집합 + err을 돌려주고 호출측이 판단한다."""
    have_code, have_name = set(), set()
    _exist, _err = naver_api.get_product_list(tid, tsecret)
    if _err:
        return have_code, have_name, _err
    for _e in (_exist or []):
        _sc = str(_e.get('sellerManagementCode') or '').strip()
        if _sc:
            have_code.add(_sc)
        _nm = str(_e.get('productName') or '').strip()
        if _nm:
            have_name.add(_nm)
    return have_code, have_name, None


def register_one(creds, save_tokens, product, margin, target, opts,
                 have_code=None, have_name=None, shared=None):
    """카페24 상품 1건을 대상 사용자의 스마트스토어에 등록.

    product : {'product_no', 'product_name', 'price'} (카페24 목록 항목)
    target  : {'api_id', 'api_secret', 'as_tel'}
    opts    : {'img_detail', 'photo_ai', 'gen_tags', 'opt_name',
               'ai_key', 'gemini_key', 'ad_creds'}
    have_code/have_name : 호출측이 유지하는 중복 방지 집합(성공 시 여기에 채워 넣는다)
    shared  : get_shared_products() 결과 — 코스트코 번호 매칭용

    반환: {'status': 'ok'|'skip'|'fail', 'name', 'code', 'code_src',
           'category', 'tags', 'price', 'detail'}
    """
    have_code = have_code if have_code is not None else set()
    have_name = have_name if have_name is not None else set()
    shared = shared or []
    tid, tsecret = target['api_id'], target['api_secret']
    ai_key = opts.get('ai_key') or ''
    gem_key = opts.get('gemini_key') or ''
    ad_creds = opts.get('ad_creds')

    _name = str(product.get('product_name', ''))
    _pno = product.get('product_no')
    sale = calc_sale_price(product.get('price'), margin)

    def _r(status, detail, **kw):
        _out = {'status': status, 'name': _name, 'code': '', 'code_src': '',
                'category': '', 'tags': 0, 'price': sale, 'detail': detail}
        _out.update(kw)
        return _out

    # 0) 카페24 상세 조회
    _full, _fe = cafe24_api.get_product(creds, _pno, save_tokens=save_tokens)
    if _fe and not _full:
        return _r('fail', '카페24 상세조회 실패: %s' % str(_fe)[:120])
    _full = _full or {}
    _cf_name = _full.get('product_name') or _name
    _name = _cf_name

    # 판매자상품코드 = 코스트코 번호. 우선순위:
    #   ① 카페24 자체상품코드(매칭·동기화가 기록한 코스트코 번호)
    #   ② 상품명 매칭(공용 코스트코 DB) — 고득점만 채택
    #   ③ 카페24 상품번호(코스트코 매칭 실패 시 폴백)
    _costco_code = str(_full.get('custom_product_code') or '').strip()
    _code_src = '자체코드'
    if not _costco_code:
        _bs, _bp = 0, None
        for _s in shared:
            _sc = calc_match_score(_cf_name, _s['costco_name'])
            if _sc > _bs:
                _bs, _bp = _sc, _s
        if _bp and _bs >= MATCH_MIN:
            _costco_code = str(_bp['product_no'] or '').strip()
            _code_src = '매칭(%s)' % _bs
    _seller_code = _costco_code or str(_pno)
    if not _costco_code:
        _code_src = '카페24번호'

    # 중복 등록 방지 — 코스트코 번호·카페24 번호·상품명 중 하나라도 겹치면 건너뛴다
    # (두 코드 체계가 섞여 있어 둘 다 확인).
    _cands = set(c for c in (_costco_code, str(_pno)) if c)
    if (_cands & have_code) or _cf_name in have_name:
        return _r('skip', '이미 등록됨', code=_seller_code, code_src=_code_src)

    # 대표 이미지
    _rep = _full.get('detail_image') or _full.get('list_image') or ''
    if not _rep:
        return _r('fail', '이미지 없음', code=_seller_code, code_src=_code_src)
    _cdn, _ue = naver_api.upload_product_image(tid, tsecret, _rep)
    if not _cdn:
        return _r('fail', '이미지업로드 실패: %s' % str(_ue or '')[:100],
                  code=_seller_code, code_src=_code_src)

    # ① AI 제품사진 분석(비전) → 상품명·원산지·브랜드 (opt-in)
    _ai_photo = {}
    if opts.get('photo_ai') and (ai_key or gem_key):
        import ai_service
        _imgb, _mt = fetch_image_bytes(_rep)
        if _imgb:
            _apr, _ape = ai_service.analyze_product_photo(
                ai_key, _imgb, _mt, gemini_key=gem_key)
            if _apr:
                _ai_photo = _apr
    _base_name = str(_ai_photo.get('name') or '').strip() or _cf_name

    # ② 카테고리 자동판단 — AI 검색어 추정 + 로컬 카테고리 캐시
    #    gemini_key를 명시적으로 넘긴다. 안 넘기면 products.py의
    #    'if ai_key or gemini_key' 게이트가 Anthropic 키에만 걸려,
    #    Gemini 키만 있는 구성에서 AI 카테고리 선택이 통째로 건너뛰어진다.
    _cid, _cfull, _ = naver_api.suggest_category_for_name(
        _base_name, tid, tsecret, ai_key=ai_key, gemini_key=gem_key,
        extra_terms=[_ai_photo.get('category') or ''])
    if not _cid and _ai_photo.get('category'):
        _cr2, _ = naver_api.search_naver_categories(
            tid, tsecret, str(_ai_photo['category']).strip())
        if _cr2:
            _cid, _cfull = _cr2[0].get('id'), _cr2[0].get('full_name')
    if not _cid:
        _name = _base_name
        return _r('fail', '카테고리 판단실패', code=_seller_code, code_src=_code_src)

    # ③ 최종 상품명: 연관키워드(저경쟁 100~300+대표어) 최적화 — 분석 상품명을 seed로
    _final_name = _base_name
    if ad_creds and opts.get('opt_name', True):
        _kn, _ki = naver_api.keyword_optimized_name(
            ad_creds[0], ad_creds[1], ad_creds[2], _base_name,
            ai_key=ai_key, category=_cfull, gemini_key=gem_key)
        if _kn and len(_kn) >= 4:
            _final_name = _kn
    if not str(_final_name or '').strip():   # 안전장치: 상품명 절대 비우지 않음
        _final_name = _cf_name or _base_name
    _name = _final_name

    # ④ 태그ID(검색 반영되는 사전등록 태그만)
    _desc_txt = str(_full.get('description') or '')
    _tags = []
    if opts.get('gen_tags', True):
        _tags, _ = naver_api.build_seller_tags(
            tid, tsecret, ai_key, _final_name, _cfull, _desc_txt, ad_creds,
            gemini_key=gem_key)

    # ⑤ 속성: AI 분석값 우선, 없으면 카페24 값
    _manuf = (str(_ai_photo.get('brand') or '').strip()
              or str(_full.get('manufacturer_name')
                     or _full.get('brand_name') or '').strip())
    _model = str(_full.get('model_name') or '').strip()
    _origin = (str(_ai_photo.get('origin') or '').strip()
               or str(_full.get('origin_place_value') or '').strip())

    # ⑥ 상세페이지: 상단에 상품명 + (옵션) 카페24 상세이미지 스택
    _name_block = (
        '<div style="text-align:center;font-size:22px;font-weight:800;'
        'padding:18px 12px;color:#222;line-height:1.45">'
        + _html.escape(str(_final_name)) + '</div>')
    _body_html = _desc_txt or ''
    if opts.get('img_detail'):
        _img_html = build_image_detail(_full, tid, tsecret,
                                       mall_id=(creds or {}).get('mall_id', ''))
        if _img_html:
            _body_html = _img_html
    _detail_html = _name_block + (
        _body_html or '<p>%s</p>' % _html.escape(str(_final_name)))

    _res, _e2 = naver_api.register_product(tid, tsecret, {
        "name": _final_name, "sale_price": sale,
        "image_url": _cdn, "category_id": _cid,
        "detail_html": _detail_html,
        "shipping_fee": 0, "origin_code": "03",
        "after_service_tel": target.get('as_tel') or '1588-1234',
        "seller_tags": _tags, "manufacturer": _manuf or None,
        "model_name": _model or None, "origin_content": _origin or None,
        "seller_code": _seller_code,
    })
    if _e2:
        return _r('fail', str(_e2)[:200], code=_seller_code, code_src=_code_src,
                  category=str(_cfull or ''), tags=len(_tags or []))

    # 같은 배치 안에서의 중복도 차단
    have_code |= _cands
    have_name.add(_final_name)
    have_name.add(_cf_name)
    return _r('ok', '등록', code=_seller_code, code_src=_code_src,
              category=str(_cfull or ''), tags=len(_tags or []))
