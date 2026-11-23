"""📷 사진으로 네이버 신상품 등록 — 모바일(/m/naver-register)용 공용 코어 (Streamlit 비의존).

PC '네이버 등록 › 📷 제품사진(여러 장) + 가격사진으로 신상품 등록'과 같은 순서·같은 함수다.
  분석: 제품사진 → analyze_product_photo / 가격사진 → analyze_price_tag
        → 카테고리(네이버 모델 → AI) → SEO 상품명(검색광고 키 있을 때)
        → 판매가(마진계산기와 같은 pricing.sale_for_margin, 매입가는 가격 DB 우선)
        → (라벨 사진 있으면) 식품 표시사항
  등록: 사진 CDN 업로드 → (선택) AI 상세설명·연관태그 → 상세HTML → register_product
        → 제품가격 DB에 코스트코번호·매장가 저장(사진등록 source='photo-reg')

PC 화면(pages_lib/naver_register_page.py)의 _build_detail·_food_info_html·
_save_costco_price와 같은 내용이다 — 그쪽은 Streamlit 화면 안 함수라 여기서 다시 둔다.
PC 쪽 상세 HTML 모양을 바꾸면 여기도 같이 바꿀 것.
"""
import html as _hm
import json
import os
import re
import tempfile
from datetime import datetime

import naver_api
import pricing


def _keys(username):
    """(settings, ai_key, gemini_key) — AI 키는 관리자 공용키 우선(PC와 같다)."""
    from db_products import get_all_settings
    from db_auth import get_global_setting
    s = get_all_settings(username) or {}
    ak = get_global_setting('anthropic_api_key') or s.get('anthropic_api_key', '')
    gk = get_global_setting('gemini_api_key') or s.get('gemini_api_key', '')
    return s, ak, gk


def _preset(s, key):
    try:
        return json.loads(s.get(key) or '{}')
    except Exception:
        return {}


def delivery(s):
    return naver_api.merge_delivery(_preset(s, 'naver_delivery_preset'))


def customer_ship(dlv):
    return int(dlv.get('base_fee') or 0) if dlv.get('fee_type') != 'FREE' else 0


def status(username):
    s, ak, gk = _keys(username)
    dlv = delivery(s)
    return {
        'has_key': bool(s.get('api_client_id') and s.get('api_client_secret')),
        'has_ai': bool(ak or gk),
        'margin': float(s.get('cafe24_naver_margin') or 10),
        'ship_cost': int(s.get('shipping_cost') or 2000),
        'box_cost': int(s.get('box_cost') or 300),
        'customer_ship': customer_ship(dlv),
        'delivery': _delivery_label(dlv),
    }


def _delivery_label(d):
    lbl = naver_api.FEE_TYPE_LABELS.get(d['fee_type'], d['fee_type'])
    if d['fee_type'] != 'FREE':
        lbl += " %s원" % format(int(d.get('base_fee') or 0), ',')
    return "%s · %s" % (lbl, naver_api.DELIVERY_COMPANIES.get(d['company'], d['company']))


def db_cost(costco_no):
    """가격 DB 매입가(소분 반영) — 없으면 0. 마진계산기와 같은 pricing.purchase_cost."""
    c = ''.join(ch for ch in str(costco_no or '') if ch.isdigit())
    if not c:
        return 0
    try:
        from db_products import find_shared_by_code
        sp = find_shared_by_code(c)
        return pricing.purchase_cost(sp) if sp else 0
    except Exception:
        return 0


def sale_price(cost, margin, ship, box, cship):
    return pricing.sale_for_margin(int(cost or 0), float(margin or 0), int(ship or 0),
                                   int(box or 0), int(cship or 0))


# ── 한글표시사항(제품 상세정보 표) ────────────────────────────────
FOOD_LABELS = [('food_type', '식품유형'), ('volume', '내용량'), ('ingredients', '원재료명'),
               ('storage', '보관방법'), ('origin', '원산지'), ('manufacturer', '제조사'),
               ('importer', '수입원'), ('expiration', '소비기한'), ('calories', '열량'),
               ('nutrition', '영양성분')]


def food_rows(fd):
    """라벨 판독 dict → [[항목, 값]] (빈 값 제외)."""
    return [[lbl, str(fd.get(k) or '').strip()] for k, lbl in FOOD_LABELS
            if fd and str(fd.get(k) or '').strip()]


def costco_spec_rows(costco_no):
    """코스트코 사이트의 한글표시사항(상품번호로 조회) → [[항목, 값]]. 없으면 []."""
    cno = ''.join(ch for ch in str(costco_no or '') if ch.isdigit())
    if not cno:
        return []
    try:
        import costco_crawler as cc
        spec = cc.fetch_costco_spec(cno) or {}
    except Exception:
        spec = {}
    return [[str(k), str(v)] for k, v in spec.items() if str(v or '').strip()]


def spec_table_html(rows):
    rows = [[str(k).strip(), str(v).strip()] for k, v in (rows or [])
            if str(k).strip() and str(v).strip()]
    if not rows:
        return ''
    import costco_crawler as cc
    return cc.build_spec_table_html(dict(rows))


def _row_val(rows, *keys):
    for k, v in rows or []:
        if str(k).strip() in keys and str(v).strip():
            return str(v).strip()
    return ''


def gen_desc(username, img, mt, name, cat_full=''):
    """🤖 상세설명 초안 — PC 'AI 상세설명 생성' 버튼과 같은 함수."""
    import ai_service
    s, ak, gk = _keys(username)
    if not (ak or gk):
        raise ValueError('AI 키가 없습니다')
    d, e = ai_service.generate_product_description(ak, img, mt, str(name or '').strip(),
                                                   cat_full or '', gemini_key=gk)
    if not (d or '').strip():
        raise ValueError(f'상세설명 생성 실패: {e or "빈 응답"}')
    return d.strip()


def gen_label(username, img, mt):
    """🍱 표시사항(라벨) 사진 → {'food': dict, 'rows': [[항목, 값]]}."""
    import ai_service
    s, ak, gk = _keys(username)
    if not (ak or gk):
        raise ValueError('AI 키가 없습니다')
    fd, e = ai_service.analyze_food_label(ak, img, mt, gemini_key=gk)
    if not fd or not any(fd.values()):
        raise ValueError(f'라벨 분석 실패: {e or "읽은 내용 없음"}')
    return {'food': fd, 'rows': food_rows(fd)}


def gen_tags(username, name, cat_full=''):
    """🏷 연관태그 10개 — PC 'AI 태그 10개 생성'과 같다(후보 → 태그사전 검증 → 제한태그 제거).
    반환 [{code, text, volume}]."""
    s, ak, gk = _keys(username)
    if not (ak or gk):
        raise ValueError('AI 키가 없습니다')
    api_id, api_secret = s.get('api_client_id', ''), s.get('api_client_secret', '')
    adc = (s.get('naver_ad_api_key'), s.get('naver_ad_secret'), s.get('naver_ad_customer_id'))
    nm = str(name or '').strip()
    t, info = naver_api.build_seller_tags(api_id, api_secret, ak, nm, cat_full or '', nm,
                                          ad_creds=adc if all(adc) else None, gemini_key=gk)
    vols = (info or {}).get('volumes') or {}
    out = [{'code': x.get('code'), 'text': x['text'], 'volume': int(vols.get(x['text'], 0) or 0)}
           for x in (t or [])]
    if not out:
        why = (info or {}).get('err') or f"검증된 사전 태그 없음 (후보 {(info or {}).get('candidates', 0)}개)"
        raise ValueError(f'태그를 못 만들었습니다 — {why}. 직접 입력할 수 있습니다.')
    return out


def analyze(username, prod, prod_mt, price, price_mt, label=None, label_mt='image/jpeg', log=None):
    """사진 분석 → 미리보기 dict. 실패면 ValueError(사유).
    모바일은 여기서 한글표시사항·상세설명·연관태그까지 모두 만든다(사진만 올리면 끝까지)."""
    log = log or (lambda m: None)
    import ai_service
    s, ak, gk = _keys(username)
    if not (ak or gk):
        raise ValueError('AI 키가 없습니다 — 관리자에게 공용 AI 키 설정을 요청하세요')
    api_id, api_secret = s.get('api_client_id', ''), s.get('api_client_secret', '')
    i1, e1 = ai_service.analyze_product_photo(ak, prod, prod_mt, gemini_key=gk)
    if e1 or not i1:
        raise ValueError(f'제품사진 분석 실패: {e1}')
    log('가격표 읽는 중…')
    i2, e2 = ai_service.analyze_price_tag(ak, price, price_mt, gemini_key=gk)
    if e2 or not i2:
        raise ValueError(f'가격사진 분석 실패: {e2}')

    name = i1.get('name') or i2.get('product_name', '')
    cost_tag = int(i2.get('price') or 0)
    cno = str(i2.get('product_no') or '')

    log('카테고리 고르는 중…')
    cid, cfull, cinfo = None, '', {}
    if name:
        mq = " ".join(x for x in [(i1.get('brand') or '').strip(), name] if x)
        try:
            cid, cfull, _ = naver_api.suggest_category_for_name(
                mq, api_id, api_secret, ai_key=ak, extra_terms=[i1.get('category') or ''], info=cinfo)
        except Exception:
            cid = None
    if not cid and i1.get('category'):
        try:
            cr, _ = naver_api.search_naver_categories(api_id, api_secret, i1['category'])
            if cr:
                cid, cfull = cr[0]['id'], cr[0]['full_name']
        except Exception:
            pass

    out = {
        'name': name, 'name_raw': '', 'cost_tag': cost_tag,
        'cat_id': str(cid or ''), 'cat_full': cfull or '',
        'cat_src': ('네이버 모델에서 가져옴' if cinfo.get('source') == 'model'
                    else ('AI 추정 — 맞는지 확인하세요' if cid else '')),
        'brand': i1.get('brand', '') or '',
        'manufacturer': i1.get('manufacturer', '') or i1.get('brand', '') or '',
        'model_name': i1.get('model_name', '') or '',
        'volume': i1.get('volume', '') or '',
        'costco_no': cno, 'barcode': str(i2.get('barcode') or ''),
        'food': None, 'seo_front': [],
    }

    # SEO 상품명 — 검색광고 키가 있을 때만 (PC와 같다)
    adk, ads, adc = s.get('naver_ad_api_key'), s.get('naver_ad_secret'), s.get('naver_ad_customer_id')
    if name and adk and ads and adc:
        try:
            r, _ = naver_api.keyword_seo_name(
                adk, ads, adc, name, ai_key=ak, category=(cfull or i1.get('category') or ''),
                front_max=200, n_front=3, gemini_key=gk,
                brand=out['brand'], model_name=out['model_name'])
            if r and r.get('name') and r['name'] != name:
                out['name_raw'], out['name'] = name, r['name']
                out['seo_front'] = list(r.get('front') or [])
        except Exception:
            pass

    out['label_err'] = ''
    if label:
        log('표시사항(라벨) 읽는 중…')
        try:
            fd, _fe = ai_service.analyze_food_label(ak, label, label_mt, gemini_key=gk)
            if fd and any(fd.values()):
                out['food'] = fd
                out['volume'] = fd.get('volume') or out['volume']
                out['manufacturer'] = fd.get('manufacturer') or out['manufacturer']
            else:
                out['label_err'] = f'라벨 분석 실패: {_fe or "읽은 내용 없음"}'
        except Exception as e:
            out['label_err'] = f'라벨 분석 실패: {e}'

    # 🇰🇷 한글표시사항 — 코스트코 사이트(상품번호) 우선, 없으면 라벨 사진 판독
    log('한글표시사항 가져오는 중… (코스트코 사이트)')
    rows = costco_spec_rows(cno)
    out['spec_src'] = 'costco' if rows else ''
    if not rows and out['food']:
        rows, out['spec_src'] = food_rows(out['food']), 'label'
    out['spec_rows'] = rows
    if out['spec_src'] == 'costco':
        out['manufacturer'] = (_row_val(rows, '제조자/수입자', '제조원/수입원', '제조원', '수입원', '제조사')
                               or out['manufacturer'])
        out['volume'] = out['volume'] or _row_val(rows, '내용량', '용량', '중량', '총 중량')

    # 📝 상세설명 · 🏷 연관태그 — 모바일은 사진만 올리면 끝까지 자동으로 채운다
    out['desc'], out['desc_err'] = '', ''
    if name:
        log('상세설명 쓰는 중…')
        try:
            out['desc'] = gen_desc(username, prod, prod_mt,
                                   ai_service.ensure_volume_in_name(out['name'], out['volume'] or ''),
                                   out['cat_full'])
        except Exception as e:
            out['desc_err'] = str(e)
    out['tags'], out['tags_err'] = [], ''
    if name:
        log('연관태그 만드는 중… (AI 후보 → 태그사전 검증)')
        try:
            out['tags'] = gen_tags(username, out['name'], out['cat_full'])
        except Exception as e:
            out['tags_err'] = str(e)

    st = status(username)
    out['cost_db'] = db_cost(cno)
    out['cost'] = out['cost_db'] or cost_tag
    out.update({k: st[k] for k in ('margin', 'ship_cost', 'box_cost', 'customer_ship', 'delivery')})
    out['sale'] = sale_price(out['cost'], st['margin'], st['ship_cost'], st['box_cost'],
                             st['customer_ship'])
    return out


def margin_info(sale, cost, ship, box, cship):
    b = pricing.margin_breakdown(int(sale or 0), int(cost or 0), int(ship or 0), int(box or 0),
                                 customer_ship=int(cship or 0))
    return {'margin': int(b['margin']), 'margin_rate': round(float(b['margin_rate']), 1)}


# ── 상세 HTML (PC _food_info_html·_build_detail과 같은 모양) ─────────────
def food_info_html(d):
    if not d:
        return ""

    def _cell(v):
        s = re.sub(r'(?i)<\s*br\s*/?\s*>', '\n', str(v))
        return '<br>'.join(_hm.escape(p.strip()) for p in s.split('\n') if p.strip())

    rows = [("식품유형", d.get("food_type")), ("내용량", d.get("volume")),
            ("원재료명", d.get("ingredients")), ("보관방법", d.get("storage")),
            ("원산지", d.get("origin")), ("제조사", d.get("manufacturer")),
            ("수입원", d.get("importer")), ("소비기한", d.get("expiration")),
            ("열량", d.get("calories")), ("영양성분", d.get("nutrition"))]
    tr = []
    for lbl, val in rows:
        v = str(val or "").strip()
        if not v:
            continue
        tr.append(
            '<tr><th style="background:#f5f5f5;border:1px solid #ddd;padding:10px 12px;'
            'text-align:center;width:25%;font-weight:700;color:#333;word-break:keep-all">'
            f'{_hm.escape(lbl)}</th>'
            '<td style="border:1px solid #ddd;padding:10px 12px;text-align:left;'
            f'width:75%;color:#333;line-height:1.6;word-break:keep-all;overflow-wrap:anywhere">{_cell(v)}</td></tr>')
    if not tr:
        return ""
    return ('<div style="max-width:720px;margin:24px auto 8px;padding:0 12px">'
            '<div style="font-size:20px;font-weight:800;text-align:center;'
            'padding:12px 0;color:#222">제품 상세정보</div>'
            '<table style="width:100%;table-layout:fixed;border-collapse:collapse;font-size:15px">'
            '<colgroup><col style="width:25%"><col style="width:75%"></colgroup>'
            + ''.join(tr) + '</table></div>')


def build_detail(s, name, imgs, desc="", food_html=""):
    top, bottom = s.get('naver_detail_top_img') or '', s.get('naver_detail_bottom_img') or ''
    p = []
    if top:
        p.append(f'<img src="{top}" style="max-width:100%;display:block;margin:0 auto">')
    p.append('<div style="font-size:32px;font-weight:800;text-align:center;'
             f'padding:20px 12px;line-height:1.35">{_hm.escape(name)}</div>')
    if desc and str(desc).strip():
        safe = _hm.escape(str(desc).strip()).replace('\n', '<br>')
        p.append('<div style="font-size:17px;line-height:1.75;text-align:center;'
                 f'padding:4px 16px 22px;color:#333">{safe}</div>')
    for u in imgs:
        p.append(f'<img src="{u}" style="max-width:100%;display:block;'
                 'margin:0 auto 20px;border:1px solid #cccccc">')
    if food_html:
        p.append(food_html)
    if bottom:
        p.append(f'<img src="{bottom}" style="max-width:100%;display:block;margin:0 auto">')
    return '<div style="text-align:center">' + ''.join(p) + '</div>'


def save_costco_price(username, is_admin, costco_no, costco_name, price,
                      naver_no='', barcode=''):
    """PC _save_costco_price와 같다 — 사진등록으로 알게 된 코스트코번호·매장가를 가격 DB에."""
    from db import upsert_shared_store_price, upsert_user_private
    cno, pr, nm = str(costco_no or '').strip(), int(price or 0), str(costco_name or '').strip()
    if not (cno and pr > 0 and nm):
        return ''
    try:
        from services import is_costco_pno
        if not is_costco_pno(cno):
            return ''
    except Exception:
        pass
    bc = ''.join(ch for ch in str(barcode or '') if ch.isdigit())
    try:
        upsert_shared_store_price(
            costco_name=nm, keyword=nm, price=pr, product_no=cno, updated_by=username,
            receipt_date=datetime.now().strftime("%Y-%m-%d"), force_store=bool(is_admin),
            source='photo-reg', barcode=(bc or None))
    except Exception as e:
        return f"제품가격 DB 저장 실패: {e}"
    if naver_no:
        try:
            upsert_user_private(username, nm, nm, naver_origin_pno=str(naver_no))
        except Exception:
            pass
    return f"제품가격 DB 등록 — {cno} {pr:,}원"


def _upload(api_id, api_secret, data, mt):
    ext = {'image/png': '.png', 'image/webp': '.webp'}.get(mt, '.jpg')
    fd, tp = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        with open(tp, 'wb') as w:
            w.write(data)
        return naver_api.upload_product_image(api_id, api_secret, tp)
    finally:
        try:
            os.remove(tp)
        except Exception:
            pass


def register(username, is_admin, imgs, f, log=None):
    """imgs: [(bytes, media_type)] 첫 장이 대표. f: 화면에서 확정한 값.
    반환: {origin_no, name, sale, images, tags, price_msg, warning}. 실패면 ValueError."""
    log = log or (lambda m: None)
    import ai_service
    s, ak, gk = _keys(username)
    api_id, api_secret = s.get('api_client_id', ''), s.get('api_client_secret', '')
    name = str(f.get('name') or '').strip()
    vol = str(f.get('volume') or '').strip()
    sale = int(f.get('sale_price') or 0)
    cat = str(f.get('cat_id') or '').strip()
    cno = str(f.get('costco_no') or '').strip()
    if not (imgs and name and vol and sale > 0 and cat and cno):
        raise ValueError('상품명·용량·판매가·카테고리·코스트코 상품번호·사진이 모두 있어야 합니다')
    name_final = ai_service.ensure_volume_in_name(name, vol)

    log(f'사진 {len(imgs[:10])}장 업로드 중…')
    cdns = []
    for data, mt in imgs[:10]:
        u, _e = _upload(api_id, api_secret, data, mt)
        if u:
            cdns.append(u)
    if not cdns:
        raise ValueError('이미지 업로드 실패 — 대표이미지를 못 올렸습니다')

    desc = str(f.get('desc') or '').strip()
    if not desc and f.get('ai_desc') and (ak or gk):
        log('AI 상세설명 작성 중…')
        try:
            d, _ = ai_service.generate_product_description(
                ak, imgs[0][0], imgs[0][1], name_final, f.get('cat_full', ''), gemini_key=gk)
            desc = (d or '').strip()
        except Exception:
            desc = ''

    # 화면에서 고른 태그가 있으면 그대로(직접 입력 태그는 code 없음), 없으면 AI로 만든다
    tags = [{'code': x.get('code'), 'text': str(x.get('text') or '').strip()}
            for x in (f.get('tags') or []) if str(x.get('text') or '').strip()][:10]
    if not tags and f.get('ai_tags') and (ak or gk):
        log('연관태그 만드는 중…')
        try:
            tags = [{'code': x.get('code'), 'text': x['text']}
                    for x in gen_tags(username, name_final, f.get('cat_full', ''))]
        except Exception:
            tags = []

    food = f.get('food') or None
    dlv = delivery(s)
    cship = int(f.get('customer_ship') if f.get('customer_ship') is not None else customer_ship(dlv))
    if cship != customer_ship(dlv):     # 화면에서 고객 배송비를 바꿨으면 등록 배송비에도 반영 (PC와 같다)
        dlv = naver_api.merge_delivery({
            **dlv, 'fee_type': ('FREE' if cship <= 0 else
                                ('PAID' if dlv.get('fee_type') == 'FREE' else dlv.get('fee_type'))),
            'base_fee': cship})
    benefits = {k: v for k, v in _preset(s, 'naver_benefit_preset').items() if v}
    brand = str(f.get('brand') or '').strip()

    log('네이버에 등록 중…')
    res, err = naver_api.register_product(api_id, api_secret, {
        "reg_user": username,     # 수량을 셀 계정
        "reg_source": "manual", "reg_actor": username,
        "name": name_final, "sale_price": sale,
        "image_url": cdns[0], "extra_image_urls": cdns[1:],
        "category_id": cat, "seller_code": cno,
        "barcode": str(f.get('barcode') or ''),
        "seller_tags": tags,
        "food_notice": ({**food, 'volume': vol} if food else None),
        "detail_html": build_detail(s, name_final, cdns, desc,
                                    spec_table_html(f.get('spec_rows')) or food_info_html(food)),
        "delivery": dlv, "delivery_company": dlv['company'],
        "benefits": benefits or None,
        "shipping_fee": 0, "origin_code": "03",
        "after_service_tel": s.get("naver_as_tel") or "1588-1234",
        "brand": brand,
        "manufacturer": (str(f.get('manufacturer') or '').strip() or brand or "상품 상세페이지 참조"),
        "model_name": str(f.get('model_name') or '').strip(),
    })
    if not res:
        raise ValueError(str(err or '등록 실패'))
    origin = str(res.get('origin_product_no') or '')
    pmsg = save_costco_price(username, is_admin, cno,
                             (f.get('name_raw') or name), int(f.get('cost_tag') or 0),
                             naver_no=origin, barcode=str(f.get('barcode') or ''))
    return {'origin_no': origin, 'name': name_final, 'sale': sale, 'images': len(cdns),
            'tags': len(tags), 'price_msg': pmsg,
            'warning': ' · '.join(x for x in [str(res.get('warning') or ''), str(err or '')] if x)}
