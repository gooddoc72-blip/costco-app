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


# 실패 사유 코드 — 조치 방법이 서로 다른 것만 나눈다.
# (같은 조치로 해결되는 걸 쪼개면 목록만 늘고 판단은 안 쉬워진다)
FAIL_REASONS = {
    'NO_CATEGORY':  ('카테고리 판단실패', '상품명이 모호합니다. 카페24 상품명을 다듬거나 AI 키를 확인하세요.'),
    'NO_IMAGE':     ('대표이미지 없음', '카페24 상품에 이미지가 없습니다. 카페24에서 등록 후 재시도하세요.'),
    'IMAGE_UPLOAD': ('이미지 업로드 실패', '이미지 용량·형식 또는 원본 URL 접근 문제입니다. 재시도로 풀리는 경우가 많습니다.'),
    'CAFE24_FETCH': ('카페24 조회 실패', '상품이 삭제됐거나 카페24 권한·토큰 문제입니다.'),
    'AUTH':         ('인증/토큰 오류', '카페24 또는 네이버 재인증이 필요합니다. 설정 탭에서 다시 연결하세요.'),
    'CERT':         ('상품인증 정보 필요', 'KC·어린이제품 인증 대상입니다. 인증정보와 카탈로그(모델명)가 있어야 등록됩니다 — 스마트스토어에서 수동 등록하세요.'),
    'RATE_LIMIT':   ('호출 한도 초과', '잠시 후 재시도하면 됩니다. 회당 등록 건수를 줄이는 것도 방법입니다.'),
    'PRICE':        ('판매가 거부', '네이버 최소/최대 판매가 범위를 벗어났습니다. 마진율을 확인하세요.'),
    'NAME':         ('상품명 거부', '금지어·길이 문제입니다. 상품명을 수정하세요.'),
    'TAG':          ('태그 거부', '태그가 거부됐습니다(등록 자체는 태그 없이 재시도됩니다).'),
    'NAVER_REJECT': ('네이버 등록 거부', '네이버가 지목한 필드를 사유에서 확인하세요.'),
    'EXCEPTION':    ('처리 중 예외', '예상 못 한 오류입니다. 자동화 로그를 확인하세요.'),
    'UNKNOWN':      ('기타', '사유 문구를 확인하세요.'),
}


def reason_label(code):
    return FAIL_REASONS.get(str(code or ''), FAIL_REASONS['UNKNOWN'])[0]


def reason_hint(code):
    return FAIL_REASONS.get(str(code or ''), FAIL_REASONS['UNKNOWN'])[1]


def _classify_naver_error(msg):
    """네이버 등록 거부 메시지 → 사유 코드. 메시지가 '[field] 설명' 형태다."""
    _m = str(msg or '').lower()
    # 상품인증(KC·어린이제품)을 먼저 본다. 아래 AUTH 판정이 '인증'이라는
    # 단어만 보고 로그인 인증 오류로 잘못 분류하던 실제 사고가 있었다
    # ('어린이인증 대상 카테고리 상품은 카탈로그 입력이 필수입니다' → AUTH).
    if any(k in _m for k in ('certification', '인증 대상', '어린이인증',
                             'kc인증', '인증정보', '인증 정보')):
        return 'CERT'
    # 로그인/토큰 인증 — '인증' 단어 단독으로는 판정하지 않는다
    if any(k in _m for k in ('unauthorized', '401', 'invalid_token',
                             'access token', '토큰')):
        return 'AUTH'
    if any(k in _m for k in ('429', 'too many', 'rate limit', '한도')):
        return 'RATE_LIMIT'
    if any(k in _m for k in ('saleprice', 'price', '판매가', '가격')):
        return 'PRICE'
    if any(k in _m for k in ('productname', '상품명', '금지어')):
        return 'NAME'
    if any(k in _m for k in ('seoinfo', 'sellertags', '태그')):
        return 'TAG'
    return 'NAVER_REJECT'


def classify_failure(detail):
    """실패 사유 문구 → 코드. reason이 비어 있는 과거 기록을 위한 폴백이다.
    새로 쌓이는 건 register_one이 코드를 직접 붙인다."""
    _d = str(detail or '')
    _l = _d.lower()
    if '카테고리 판단실패' in _d:
        return 'NO_CATEGORY'
    if '이미지 없음' in _d:
        return 'NO_IMAGE'
    if '이미지업로드 실패' in _d or '이미지 업로드 실패' in _d:
        return 'IMAGE_UPLOAD'
    if '카페24 상세조회 실패' in _d:
        return 'CAFE24_FETCH'
    if _d.startswith('예외:'):
        return 'EXCEPTION'
    if not _d.strip():
        return 'UNKNOWN'
    return _classify_naver_error(_d)


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


def _abs_url(u, mall_id=''):
    """카페24 이미지 경로 → 절대 URL. 못 만들면 ''.
    '//cdn/...' 프로토콜 상대와 '/web/upload/...' 루트 상대를 모두 처리한다."""
    u = str(u or '').strip()
    if not u:
        return ''
    if u.startswith('//'):
        return 'https:' + u
    if u.startswith('/') and mall_id:
        return 'https://%s.cafe24.com' % str(mall_id).strip() + u
    return u if u.startswith('http') else ''


_IMG_SRC_RE = _re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(.*?)\2', _re.I | _re.S)
_SCRIPT_RE = _re.compile(r'<script\b.*?</script\s*>', _re.I | _re.S)


def rewrite_detail_images(html, tid, tsecret, mall_id='', limit=DETAIL_IMG_LIMIT):
    """카페24 상세 HTML의 <img src>만 네이버 CDN URL로 교체한다.

    구조·텍스트·순서는 건드리지 않는다 — 판매자가 만든 상세페이지를 그대로
    옮기는 게 목적이다. 이미지는 원본 그대로 업로드된다(upload_product_image의
    square=False 경로).
    업로드에 실패한 이미지는 카페24 원본 URL을 그대로 남긴다. 지우면 그 자리가
    비어버리는데, 핫링크라도 되는 편이 낫다.

    반환: (html, 교체 성공수, 실패수)
    """
    _html_in = _SCRIPT_RE.sub('', str(html or ''))   # <script>는 네이버가 어차피 거부
    cache, stat = {}, {'ok': 0, 'fail': 0, 'n': 0}

    def _sub(m):
        pre, q, url = m.group(1), m.group(2), m.group(3)
        u = _abs_url(url, mall_id)
        if not u:
            return m.group(0)
        if u in cache:
            return pre + q + cache[u] + q
        if stat['n'] >= limit:
            return m.group(0)
        stat['n'] += 1
        cdn, _e = naver_api.upload_product_image(tid, tsecret, u, square=False)
        if not cdn:
            stat['fail'] += 1
            return m.group(0)
        cache[u] = cdn
        stat['ok'] += 1
        return pre + q + cdn + q

    return _IMG_SRC_RE.sub(_sub, _html_in), stat['ok'], stat['fail']


def build_detail_html(full, name, tid, tsecret, mall_id='', mode='html',
                      top_img='', bottom_img='', limit=DETAIL_IMG_LIMIT):
    """상세페이지 조립: [상단 고정] + 상품명 + [본문] + [하단 고정].

    mode='html'  : 카페24 상세 HTML 그대로(이미지만 네이버 CDN으로 교체) — 기본
    mode='image' : 카페24 상세이미지만 순서대로 쌓기(편집이 쉬운 대신 원본 레이아웃 손실)
    """
    _nm = _html.escape(str(name or ''))
    _parts = ['<div style="text-align:center">']
    if top_img:
        _parts.append('<img src="%s" style="display:block;max-width:100%%;'
                      'margin:0 auto">' % top_img)
    _parts.append('<div style="font-size:22px;font-weight:800;padding:18px 12px;'
                  'color:#222;line-height:1.45">%s</div>' % _nm)

    _body = ''
    if mode == 'image':
        _body = build_image_detail(full, tid, tsecret, limit=limit, mall_id=mall_id)
    else:
        _raw = str((full or {}).get('description') or '')
        if _raw.strip():
            _body, _ok, _fail = rewrite_detail_images(
                _raw, tid, tsecret, mall_id=mall_id, limit=limit)
        # 카페24 상세가 비어 있으면 이미지 스택으로 폴백(빈 상세페이지 방지)
        if not str(_body).strip():
            _body = build_image_detail(full, tid, tsecret, limit=limit, mall_id=mall_id)
    _parts.append(_body or '<p>%s</p>' % _nm)

    if bottom_img:
        _parts.append('<img src="%s" style="display:block;max-width:100%%;'
                      'margin:24px auto 0">' % bottom_img)
    _parts.append('</div>')
    return '\n'.join(_parts)


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
    seen, out = set(), []
    for u in urls:
        u = _abs_url(u, mall_id)
        if u and u not in seen:
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


def calc_sale_price(cafe24_price, margin_pct, shipping_cost=None, mode='calc'):
    """카페24 판매가 → 네이버 판매가.

    mode='asis' : 카페24 가격 그대로 (마진·수수료·택배비 미적용)
    mode='calc' : 아래 공식 적용

    공식: (카페24가 + 택배비) × (1+마진%) ÷ 0.945 → 10원 반올림.
    코스트코 경로(pricing.compute_sale_price)와 같은 공식이다.

    택배비를 더하는 이유: 네이버에 **무료배송으로 등록**하므로, 판매가에
    녹이지 않으면 배송비가 그대로 손실이다. 카페24는 상품별 배송비를 쓰지
    않아(shipping_fee_by_product='F', 몰 단위 정책) 상품에서 가져올 수 없다.
    유료배송(fee_type=CHARGE)으로 등록할 거면 0을 넘긴다.
    """
    try:
        _pr = int(float(cafe24_price or 0))
    except Exception:
        _pr = 0
    if _pr <= 0:
        return 0
    if mode == 'asis':
        # 카페24 판매가를 그대로 쓴다. 카페24 가격에 이미 마진·배송비가
        # 반영돼 있는 몰이면 이쪽이 맞다(중복으로 얹지 않는다).
        return _pr
    if shipping_cost is None:
        try:
            from pricing import DEFAULT_IMPORT_SHIPPING
            shipping_cost = DEFAULT_IMPORT_SHIPPING
        except Exception:
            shipping_cost = 3000
    try:
        _sh = max(0, int(shipping_cost or 0))
    except (TypeError, ValueError):
        _sh = 0
    return int(round((_pr + _sh) * (1 + float(margin_pct) / 100.0) / 0.945 / 10) * 10)


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
    opts    : {'detail_mode'('html'|'image'), 'top_img', 'bottom_img',
               'price_mode'('asis'|'calc'), 'delivery'(배송 프리셋),
               'benefits'(구매/리뷰 포인트), 'photo_ai', 'gen_tags', 'opt_name',
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
    # 유료배송이면 배송비를 구매자가 내므로 판매가에 녹이지 않는다
    _dv = naver_api.merge_delivery(opts.get('delivery'))
    # 무료배송일 때만 택배비를 판매가에 녹인다(유료면 구매자가 낸다)
    _ship_in_price = _dv['ship_cost'] if _dv['fee_type'] == 'FREE' else 0
    sale = calc_sale_price(product.get('price'), margin, _ship_in_price,
                           mode=opts.get('price_mode', 'calc'))

    def _r(status, detail, reason='', **kw):
        _out = {'status': status, 'name': _name, 'code': '', 'code_src': '',
                'category': '', 'tags': 0, 'price': sale, 'detail': detail,
                'reason': reason if status == 'fail' else ''}
        _out.update(kw)
        return _out

    # 0) 카페24 상세 조회
    _full, _fe = cafe24_api.get_product(creds, _pno, save_tokens=save_tokens)
    if _fe and not _full:
        return _r('fail', '카페24 상세조회 실패: %s' % str(_fe)[:120],
                  reason='AUTH' if 'token' in str(_fe).lower() else 'CAFE24_FETCH')
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
        return _r('fail', '이미지 없음', reason='NO_IMAGE',
                  code=_seller_code, code_src=_code_src)
    _cdn, _ue = naver_api.upload_product_image(tid, tsecret, _rep)
    if not _cdn:
        return _r('fail', '이미지업로드 실패: %s' % str(_ue or '')[:100],
                  reason='IMAGE_UPLOAD', code=_seller_code, code_src=_code_src)

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
        return _r('fail', '카테고리 판단실패', reason='NO_CATEGORY',
                  code=_seller_code, code_src=_code_src)

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

    # ⑥ 상세페이지: [상단 고정] + 상품명 + 카페24 상세 원본 + [하단 고정]
    _detail_html = build_detail_html(
        _full, _final_name, tid, tsecret,
        mall_id=(creds or {}).get('mall_id', ''),
        mode=opts.get('detail_mode', 'html'),
        top_img=opts.get('top_img', ''), bottom_img=opts.get('bottom_img', ''))

    _res, _e2 = naver_api.register_product(tid, tsecret, {
        "name": _final_name, "sale_price": sale,
        "image_url": _cdn, "category_id": _cid,
        "detail_html": _detail_html,
        "shipping_fee": 0, "origin_code": "03",
        "delivery": _dv,          # 배송비 유형·반품/교환비·택배사 (프리셋 일괄 적용)
        "after_service_tel": target.get('as_tel') or '1588-1234',
        "seller_tags": _tags, "manufacturer": _manuf or None,
        "model_name": _model or None, "origin_content": _origin or None,
        "seller_code": _seller_code,
        # 구매/리뷰 포인트 — 등록 시점에 같이 건다. 안 넣으면 나중에
        # '혜택 일괄 적용'을 상품 수만큼 다시 돌려야 한다(건당 API 2회).
        "benefits": opts.get('benefits') or None,
    })
    if _e2:
        return _r('fail', str(_e2)[:200], reason=_classify_naver_error(_e2),
                  code=_seller_code, code_src=_code_src,
                  category=str(_cfull or ''), tags=len(_tags or []))

    # 같은 배치 안에서의 중복도 차단
    have_code |= _cands
    have_name.add(_final_name)
    have_name.add(_cf_name)
    _warn = str((_res or {}).get('warning') or '')
    return _r('ok', ('등록 (⚠️ %s)' % _warn[:120]) if _warn else '등록',
              code=_seller_code, code_src=_code_src,
              category=str(_cfull or ''), tags=len(_tags or []))
