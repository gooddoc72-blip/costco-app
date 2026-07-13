"""네이버 API — 상품 조회/등록·카테고리·이미지·가격 수정"""
import time, json, requests, bcrypt, pybase64, math
from datetime import datetime, timedelta, timezone
from .core import get_token

def _get_category_cache_path():
    import os
    # 패키지化로 한 단계 깊어짐 → 앱 루트(naver_api/의 부모) 기준 유지 (기존 캐시 호환)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", "naver_categories.json")



def load_naver_category_cache(client_id, client_secret, force_refresh=False):
    """전체 카테고리를 API에서 받아 로컬 JSON 캐시로 저장.
    캐시가 있으면 바로 반환 (force_refresh=True면 강제 갱신).
    반환: ([{"id": str, "full_name": str}], error_msg)
    """
    import os, json as _json
    cache_path = _get_category_cache_path()
    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                return _json.load(f), None
        except Exception:
            pass

    token, err = get_token(client_id, client_secret)
    if not token:
        return [], err
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(
            "https://api.commerce.naver.com/external/v1/categories",
            headers=headers,
            params={"page": 1, "pageSize": 100},
            timeout=20,
        )
        if resp.status_code != 200:
            return [], f"카테고리 조회 실패({resp.status_code})"
        all_cats = resp.json() if isinstance(resp.json(), list) else []
        leaf_cats = [
            {"id": str(c["id"]), "full_name": c.get("wholeCategoryName", c.get("name", ""))}
            for c in all_cats if c.get("last") and c.get("id")
        ]
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            _json.dump(leaf_cats, f, ensure_ascii=False)
        return leaf_cats, None
    except Exception as e:
        return [], str(e)



def search_naver_categories(client_id, client_secret, keyword):
    """캐시에서 키워드로 카테고리 검색 (부분 일치, 대소문자 무시).
    반환: ([{"id": str, "full_name": str}], error_msg)
    """
    cats, err = load_naver_category_cache(client_id, client_secret)
    if err and not cats:
        return [], err
    kw = keyword.strip().lower()
    matched = [c for c in cats if kw in c["full_name"].lower()]
    matched.sort(key=lambda c: (c["full_name"].lower().index(kw), c["full_name"]))
    return matched[:50], None



def _square_canvas(im, size=1000, bg=(255, 255, 255)):
    """PIL 이미지를 '가운데 기준 정사각 크롭'으로 size×size 로 변환 (흰 여백 없음).
    짧은 변을 한 변으로 하는 정사각을 이미지 중앙에서 잘라냄:
      · 세로로 긴 사진 → 가로에 맞추고 위·아래를 균등하게 잘라냄
      · 가로로 긴 사진 → 세로에 맞추고 좌·우를 균등하게 잘라냄
    _resize_square(업로드)와 resize_square_bytes(미리보기)의 공용 로직 = 결과 동일."""
    from PIL import Image
    im = im.convert("RGB")
    _w, _h = im.size
    _s = min(_w, _h)                       # 정사각 한 변 = 짧은 변
    _left = (_w - _s) // 2                  # 가운데 정렬
    _top = (_h - _s) // 2
    im = im.crop((_left, _top, _left + _s, _top + _s))
    return im.resize((size, size), Image.LANCZOS)


def _resize_square(src_path, size=1000, bg=(255, 255, 255)):
    """이미지 파일을 1000×1000 정사각 JPEG로 변환 (네이버 업로드용).
    반환: 변환된 임시파일 경로 (실패 시 None → 원본 그대로 업로드)."""
    try:
        from PIL import Image
        import tempfile, os as _os
        with Image.open(src_path) as _im0:
            if _im0.size[0] <= 0 or _im0.size[1] <= 0:
                return None
            canvas = _square_canvas(_im0, size, bg)
        _fd, _out = tempfile.mkstemp(suffix=".jpg")
        _os.close(_fd)
        canvas.save(_out, "JPEG", quality=90)
        return _out
    except Exception:
        return None


def resize_square_bytes(img_bytes, size=1000, bg=(255, 255, 255)):
    """미리보기용 — 이미지 바이트를 1000×1000 정사각 JPEG 바이트로 변환.
    실제 업로드(_resize_square)와 동일 결과. 실패 시 None."""
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(img_bytes)) as _im0:
            canvas = _square_canvas(_im0, size, bg)
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return None


def upload_product_image(client_id, client_secret, image_source):
    """
    이미지(로컬 파일 경로 또는 URL)를 네이버 CDN에 업로드.
    업로드 전 1000×1000 정사각형(가운데 크롭)으로 자동 변환.
    반환: (naver_cdn_url, error_msg)
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err

    headers = {"Authorization": f"Bearer {token}"}
    tmp_path = None
    resized_path = None

    try:
        if image_source.startswith("http"):
            import tempfile, os
            ext = os.path.splitext(image_source.split("?")[0])[-1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                ext = ".jpg"
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
            os.close(tmp_fd)
            dl_resp = requests.get(
                image_source,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.costco.co.kr/",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
                timeout=20,
                stream=True,
            )
            dl_resp.raise_for_status()
            # Content-Type으로 확장자 보정
            ct = dl_resp.headers.get("Content-Type", "")
            if "png" in ct:   ext = ".png"
            elif "webp" in ct: ext = ".webp"
            elif "gif" in ct:  ext = ".gif"
            else:              ext = ".jpg"
            with open(tmp_path, "wb") as f:
                for chunk in dl_resp.iter_content(8192):
                    f.write(chunk)
            src_path = tmp_path
        else:
            src_path = image_source

        # 네이버 권장 1000×1000 정사각형으로 리사이징 (실패 시 원본 업로드)
        resized_path = _resize_square(src_path)
        upload_path = resized_path or src_path

        import os as _os
        fname = _os.path.basename(upload_path)
        with open(upload_path, "rb") as f:
            resp = requests.post(
                "https://api.commerce.naver.com/external/v1/product-images/upload",
                headers=headers,
                files={"imageFiles": (fname, f, "image/jpeg")},
                timeout=30,
            )

        if resp.status_code == 200:
            imgs = resp.json().get("images", [])
            if imgs:
                return imgs[0].get("url"), None
        return None, f"이미지 업로드 실패({resp.status_code}): {resp.text[:300]}"

    except Exception as e:
        return None, str(e)
    finally:
        for _p in (tmp_path, resized_path):
            if _p:
                try:
                    import os
                    os.remove(_p)
                except Exception:
                    pass



def upload_images_batch(client_id, client_secret, image_sources, max_images=9):
    """
    여러 이미지 URL/경로를 네이버 CDN에 순서대로 업로드.
    max_images: 최대 업로드 수 (네이버 추가이미지 최대 9장).
    반환: (naver_cdn_urls: list, errors: list)
    """
    cdn_urls = []
    errors = []
    for src in image_sources[:max_images]:
        url, err = upload_product_image(client_id, client_secret, src)
        if url:
            cdn_urls.append(url)
        else:
            errors.append(f"{src[:80]}: {err}")
    return cdn_urls, errors



def _sanitize_detail_html(html):
    """상세HTML 정제 — 네이버 detailContent가 지원 안 하는 요소 제거.
    카페24 에디봇 등의 <style>/<script>/<head> CSS가 네이버에서 '코드 그대로' 노출되는 문제 해결.
    이미지·본문은 유지."""
    import re as _re
    h = str(html or "")
    # 문서 래퍼/헤드 제거 (style·meta 포함)
    h = _re.sub(r"<!DOCTYPE[^>]*>", "", h, flags=_re.IGNORECASE)
    h = _re.sub(r"<head\b[^>]*>.*?</head>", "", h, flags=_re.IGNORECASE | _re.DOTALL)
    # 어디에 있든 style/script 블록 제거
    h = _re.sub(r"<style\b[^>]*>.*?</style>", "", h, flags=_re.IGNORECASE | _re.DOTALL)
    h = _re.sub(r"<script\b[^>]*>.*?</script>", "", h, flags=_re.IGNORECASE | _re.DOTALL)
    h = _re.sub(r"<link\b[^>]*>", "", h, flags=_re.IGNORECASE)
    # html/body 래퍼 태그만 제거(내용 유지)
    h = _re.sub(r"</?(?:html|body)\b[^>]*>", "", h, flags=_re.IGNORECASE)
    return h.strip()


def _build_food_notice(fn, name):
    """식품 라벨 dict(fn) → 네이버 FOOD 상품정보제공고시 객체. fn 없으면 None.
    fn 키: food_type, volume, ingredients, storage, manufacturer, importer, expiration, nutrition.
    미상 필드는 '상품 상세페이지 참조'로 채움(고시 누락 방지)."""
    if not fn or not any((fn or {}).values()):
        return None
    _ref = "상품 상세페이지 참조"
    _prod = (fn.get("manufacturer") or "").strip()
    _imp = (fn.get("importer") or "").strip()
    _producer = " / ".join([x for x in (_prod, _imp) if x]) or _ref
    _vol = (fn.get("volume") or "").strip() or _ref
    food = {
        "foodItem":           (fn.get("food_type") or "").strip() or str(name)[:50],
        "producer":           _producer,
        "weight":             _vol,
        "amount":             _vol,
        "packDate":           _ref,
        "expirationDate":     (fn.get("expiration") or "").strip() or _ref,
        "productComposition": (fn.get("ingredients") or "").strip() or _ref,
    }
    _keep = (fn.get("storage") or "").strip()
    if _keep:
        food["keep"] = _keep
    _cau = (fn.get("nutrition") or "").strip()
    if _cau:
        food["adCaution"] = _cau[:500]
    return {"productInfoProvidedNoticeType": "FOOD", "food": food}


def register_product(client_id, client_secret, product_info):
    """
    네이버 스마트스토어 상품 등록.
    product_info 필수 키:
      name, sale_price, image_url(네이버CDN), category_id
    선택 키:
      stock(default 100), shipping_fee(default 0), detail_content,
      after_service_tel, origin_code('03'=국내, '04'=해외)
    반환: ({"origin_product_no": str}, error_msg)
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    shipping_fee = int(product_info.get("shipping_fee", 0))
    fee_type = "FREE" if shipping_fee == 0 else "CHARGE"
    name = (product_info.get("name") or "")[:100]
    # detail_html (코스트코 상세) 우선, 없으면 detail_content, 없으면 기본값
    detail = _sanitize_detail_html(
        product_info.get("detail_html")
        or product_info.get("detail_content")
        or ""
    ) or f"<p>{name}</p>"
    as_tel = product_info.get("after_service_tel") or "1588-1234"
    origin = product_info.get("origin_code") or "03"

    rp = product_info.get("review_points") or {}

    # 추가이미지: 이미 네이버 CDN URL 목록이어야 함
    extra_image_urls = product_info.get("extra_image_urls") or []
    optional_images = [{"url": u} for u in extra_image_urls if u]

    payload = {
        "originProduct": {
            "statusType": "SALE",
            "saleType": "NEW",
            "leafCategoryId": str(product_info["category_id"]),
            "name": name,
            "detailContent": detail,
            "images": {
                "representativeImage": {"url": product_info["image_url"]},
                "optionalImages": optional_images,
            },
            "salePrice": int(product_info["sale_price"]),
            "stockQuantity": int(product_info.get("stock", 100)),
            "deliveryInfo": {
                "deliveryType": "DELIVERY",
                "deliveryAttributeType": "NORMAL",
                "deliveryCompany": product_info.get("delivery_company") or "CJGLS",
                "deliveryFee": {
                    "deliveryFeeType": fee_type,
                    "baseFee": shipping_fee,
                    "deliveryFeePayType": "PREPAID",
                },
                "claimDeliveryInfo": {
                    "returnDeliveryFee": 5000,
                    "exchangeDeliveryFee": 5000,
                },
            },
            "benefitInfo": {
                "reviewPointPolicy": {
                    "textReviewPoint":              rp.get("text", 50),
                    "photoVideoReviewPoint":        rp.get("photo", 100),
                    "afterUseTextReviewPoint":      rp.get("after_text", 100),
                    "afterUsePhotoVideoReviewPoint": rp.get("after_photo", 100),
                }
            },
            "detailAttribute": {
                "minorPurchasable": True,   # 미성년자 구매가능
                "unitCapacity": {"unitPriceYn": False},  # 가격표시제 대상(화장지 등) 필수
                "afterServiceInfo": {
                    "afterServiceTelephoneNumber": as_tel,
                    "afterServiceGuideContent": "판매자에게 문의해 주세요.",
                },
                "originAreaInfo": {
                    "originAreaCode": origin,
                    "content": "",
                },
                "productInfoProvidedNotice": {
                    "productInfoProvidedNoticeType": "ETC",
                    "etc": {
                        "itemName":                  name,
                        "modelName":                 name,
                        "manufacturer":              product_info.get("manufacturer") or "상품 상세페이지 참조",
                        "afterServiceDirector":      as_tel,
                        "returnCostReason":          "상품 상세페이지 참조",
                        "noRefundReason":            "상품 상세페이지 참조",
                        "qualityAssuranceStandard":  "상품 상세페이지 참조",
                        "compensationProcedure":     "상품 상세페이지 참조",
                        "troubleShootingContents":   "상품 상세페이지 참조",
                    },
                },
            },
        },
        # 스마트스토어 채널 노출 설정 (필수 — 없으면 등록 400)
        "smartstoreChannelProduct": {
            "naverShoppingRegistration": True,
            "channelProductDisplayStatusType": "ON",
        },
    }
    # 판매자 상품코드(코스트코 번호 등) — 있으면 sellerCodeInfo에 설정
    _seller_code = str(product_info.get("seller_code") or "").strip()
    if _seller_code:
        payload["originProduct"]["detailAttribute"]["sellerCodeInfo"] = {
            "sellerManagementCode": _seller_code[:50],
        }

    # 연관태그(SEO sellerTags) — [{code, text}] 목록. 있으면 seoInfo에 주입 (최대 10개)
    _seller_tags = product_info.get("seller_tags") or []
    _has_tags = bool(_seller_tags)
    if _has_tags:
        _st_clean = []
        for _t in _seller_tags[:10]:
            _txt = str((_t or {}).get("text") or "").strip()
            if not _txt:
                continue
            _entry = {"text": _txt}
            _code = (_t or {}).get("code")
            if _code not in (None, "", 0):
                _entry["code"] = int(_code)   # 사전 등록 태그만 code 부여
            _st_clean.append(_entry)
        if _st_clean:
            payload["originProduct"]["detailAttribute"]["seoInfo"] = {"sellerTags": _st_clean}
        else:
            _has_tags = False

    # 식품 상품정보제공고시(FOOD) — 라벨 데이터 있으면 ETC 대신 FOOD 사용
    _etc_notice = payload["originProduct"]["detailAttribute"]["productInfoProvidedNotice"]
    _food_notice_obj = _build_food_notice(product_info.get("food_notice"), name)
    _has_food = bool(_food_notice_obj)
    if _has_food:
        payload["originProduct"]["detailAttribute"]["productInfoProvidedNotice"] = _food_notice_obj

    def _do_post(_pl):
        resp = requests.post(
            "https://api.commerce.naver.com/external/v2/products",
            headers=headers,
            json=_pl,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            pno = str(data.get("originProductNo") or data.get("productNo") or "")
            return {"origin_product_no": pno}, None
        return None, f"상품 등록 실패({resp.status_code}): {_format_naver_err(resp)}"

    try:
        _res, _err = _do_post(payload)
        # 실패 시 안전 베이스라인으로 1회 재시도: 태그 제거 + 식품고시→ETC 복원
        if _err and (_has_tags or _has_food):
            _da = payload["originProduct"]["detailAttribute"]
            _dropped = []
            if _has_tags and _da.pop("seoInfo", None) is not None:
                _dropped.append("태그")
            if _has_food:
                _da["productInfoProvidedNotice"] = _etc_notice
                _dropped.append("식품고시")
            _res2, _err2 = _do_post(payload)
            if not _err2:
                return _res2, f"⚠️ {'·'.join(_dropped)} 거부되어 제외하고 등록했습니다. (원인: {_err})"
        return _res, _err
    except Exception as e:
        return None, str(e)


# ── 연관태그(SEO sellerTags) 자동 생성 ─────────────────────────────
# 네이버 쇼핑 태그는 '태그사전'에 등록된 태그(code 有)만 검색에 반영됨.
#   · 추천(사전등록) 태그 검색: GET /external/v2/tags/recommend-tags?keyword=  → [{code, text}]
#   · 제한 태그 여부(배치) 조회: GET /external/v2/tags/restricted-tags?tags=... → [{tag, restricted}]
_TAG_RECOMMEND_URL  = "https://api.commerce.naver.com/external/v2/tags/recommend-tags"
_TAG_RESTRICTED_URL = "https://api.commerce.naver.com/external/v2/tags/restricted-tags"


def _recommend_tags_with_token(token, keyword):
    """토큰 재사용용 내부 helper. 반환: [{code:int, text:str}] (code 있는 사전태그만)."""
    kw = (keyword or "").strip()
    if not kw:
        return []
    try:
        resp = requests.get(
            _TAG_RECOMMEND_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"keyword": kw},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        out = []
        for it in (data if isinstance(data, list) else []):
            _code = it.get("code")
            _text = (it.get("text") or "").strip()
            if _text and _code is not None:
                out.append({"code": int(_code), "text": _text})
        return out
    except Exception:
        return []


def search_recommend_tags(client_id, client_secret, keyword):
    """추천(사전 등록) 태그 검색. 반환: ([{code, text}], err).
    code가 붙은 태그만 = 검색에 실제 반영되는 '효과 있는' 태그."""
    token, err = get_token(client_id, client_secret)
    if not token:
        return [], err
    return _recommend_tags_with_token(token, keyword), None


def _restricted_set_with_token(token, tag_texts):
    """제한 태그(등록 불가) 텍스트 집합을 반환. 조회 실패 시 빈 집합(전부 통과)."""
    _texts = [t for t in {(x or "").strip() for x in tag_texts} if t]
    if not _texts:
        return set()
    try:
        resp = requests.get(
            _TAG_RESTRICTED_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"tags": _texts},   # string[] — requests가 tags=a&tags=b 로 직렬화
            timeout=15,
        )
        if resp.status_code != 200:
            return set()
        data = resp.json()
        return {
            (it.get("tag") or "").strip()
            for it in (data if isinstance(data, list) else [])
            if it.get("restricted") and (it.get("tag") or "").strip()
        }
    except Exception:
        return set()


def filter_restricted_tags(client_id, client_secret, tag_texts):
    """제한 태그를 제거하고 남은 태그명 리스트 반환. 반환: (남은 리스트, err)."""
    token, err = get_token(client_id, client_secret)
    if not token:
        return list(tag_texts), err
    _bad = _restricted_set_with_token(token, tag_texts)
    return [t for t in tag_texts if (t or "").strip() not in _bad], None


_TAG_CAND_SYSTEM = (
    "너는 네이버 스마트스토어 SEO 태그 전문가다. 주어진 상품에 대해 "
    "구매자가 실제로 검색할 법한 한국어 검색 키워드 후보를 관련도 높은 순으로 제안한다. "
    "규칙: (1) 브랜드명·카테고리명 단독은 태그로 부적합하니 제외, (2) 2~10자 명사형 위주, "
    "(3) 특수문자·이모지·공백만 있는 것 제외, (4) 중복 제거. "
    "출력은 오직 JSON 배열 하나. 예: [\"키워드1\",\"키워드2\"] — 설명·코드블록 금지."
)


def _ai_tag_candidates(ai_key, name, category_path, detail, n=24):
    """Claude로 검색 키워드 후보 생성 (관련도순). 실패 시 상품명 토큰 폴백."""
    _cands = []
    try:
        import ai_service, json as _json2, re as _re2
        _msg = (
            f"상품명: {name}\n"
            f"카테고리: {category_path or '미상'}\n"
            f"상세(발췌): {(detail or '')[:300]}\n\n"
            f"위 상품의 검색 키워드 후보를 관련도순으로 {n}개 JSON 배열로."
        )
        _txt, _err = ai_service.claude_complete(ai_key, _TAG_CAND_SYSTEM, _msg, max_tokens=700)
        if _txt:
            _m = _re2.search(r"\[.*\]", _txt, _re2.S)
            if _m:
                _arr = _json2.loads(_m.group(0))
                _cands = [str(x).strip() for x in _arr if str(x).strip()]
    except Exception:
        _cands = []
    if not _cands:  # AI 실패 → 상품명 토큰 폴백
        _cands = [w for w in str(name).replace("/", " ").split() if len(w) >= 2][:n]
    # 정규화 중복 제거 (순서 유지 = 관련도 유지)
    _seen, _out = set(), []
    for _c in _cands:
        _k = _c.lower().replace(" ", "")
        if _k and _k not in _seen:
            _seen.add(_k); _out.append(_c)
    return _out[:n]


def build_seller_tags(client_id, client_secret, ai_key, name, category_path="",
                      detail="", ad_creds=None, limit=10):
    """상품등록용 sellerTags 상위 N개 생성.
    1) AI로 검색 키워드 후보 → 2) 추천태그 API 검증(code 有) → 3) 제한태그 제거
    → 4) (ad_creds 있으면) 검색량 우선, 없으면 관련도순 → 상위 limit개.
    반환: ([{code:int, text:str}], info_dict).  info_dict: 진단용(후보수/검증수/제한제거수).
    """
    info = {"candidates": 0, "validated": 0, "after_restricted": 0, "err": None}
    token, err = get_token(client_id, client_secret)
    if not token:
        info["err"] = err
        return [], info

    cands = _ai_tag_candidates(ai_key, name, category_path, detail)
    info["candidates"] = len(cands)

    # 2) 후보별 추천태그 검증 — code 있는 사전태그만 수집 (관련도 순위 = rank 보존)
    validated = {}   # text → {"code", "rank"}
    for _rank, _kw in enumerate(cands):
        for _t in _recommend_tags_with_token(token, _kw):
            _tx = _t["text"]
            if _tx not in validated:
                validated[_tx] = {"code": _t["code"], "rank": _rank}
        time.sleep(0.08)   # rate limit 대비 소량 스로틀
        if len(validated) >= 40:   # 충분히 모이면 조기 종료(호출 절약)
            break
    info["validated"] = len(validated)
    if not validated:
        return [], info

    # 3) 제한 태그 배치 1회 제거
    _bad = _restricted_set_with_token(token, list(validated.keys()))
    for _b in _bad:
        validated.pop(_b, None)
    info["after_restricted"] = len(validated)
    if not validated:
        return [], info

    # 4) 정렬: 검색량 우선(ad_creds) → 없으면 관련도(rank)
    _vols = {}
    if ad_creds and all(ad_creds):
        try:
            from .keywords import keyword_volumes, _norm_kw
            _vmap = keyword_volumes(ad_creds[0], ad_creds[1], ad_creds[2], list(validated.keys()))
            for _tx in validated:
                _pc, _mo, _ = _vmap.get(_norm_kw(_tx), (0, 0, 0))
                _vols[_tx] = int(_pc) + int(_mo)
        except Exception:
            _vols = {}

    def _sort_key(item):
        _tx, _v = item
        return (-_vols.get(_tx, 0), _v["rank"])   # 검색량 desc, 관련도 asc

    _ordered = sorted(validated.items(), key=_sort_key)
    tags = [{"code": v["code"], "text": tx} for tx, v in _ordered[:limit]]
    info["volumes"] = {tx: _vols.get(tx, 0) for tx, _ in _ordered[:limit]}
    return tags, info


# 매칭 정보 디버그 출력용 (마지막 매칭 결과 저장)

def calc_min_price(unit_cost, shipping_cost, box_cost, target_margin_rate):
    """적자 안 나는 최소 판매가 계산 (네이버 수수료 5.5% 기준)"""
    total_cost = unit_cost + shipping_cost + box_cost
    return int(total_cost * (1 + target_margin_rate) / 0.945 / 100) * 100


def get_product_list(client_id, client_secret, channel_seller_id=""):
    """스마트스토어 상품 전체 목록 조회 (판매중 + 판매중지).
    공식: POST /external/v1/products/search (v1, POST + JSON body)
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    url = "https://api.commerce.naver.com/external/v1/products/search"

    def _extract_delivery_fee(item, cp):
        """배송비 추출 - 네이버 커머스 API 응답 구조에서 다양한 경로 시도"""
        for src in (cp, item):
            if not isinstance(src, dict):
                continue
            # 경로 1: deliveryInfo.deliveryFee.baseFee
            di = src.get("deliveryInfo")
            if isinstance(di, dict):
                df = di.get("deliveryFee")
                if isinstance(df, dict):
                    base = df.get("baseFee") or df.get("baseDeliveryFee")
                    if base is not None:
                        try: return int(base)
                        except: pass
            # 경로 2: deliveryFee.baseFee (cp/item 직속)
            df = src.get("deliveryFee")
            if isinstance(df, dict):
                base = df.get("baseFee") or df.get("baseDeliveryFee") or df.get("deliveryFee")
                if base is not None:
                    try: return int(base)
                    except: pass
            if isinstance(df, (int, float)):
                return int(df)
            # 경로 3: 직접 baseFee
            for k in ("baseFee", "baseDeliveryFee", "deliveryBaseFee"):
                v = src.get(k)
                if v is not None:
                    try: return int(v)
                    except: pass
        return 0

    result = []
    # SALE + OUTOFSTOCK + SUSPENSION 모두 조회
    for status_filter in ["SALE", "OUTOFSTOCK", "SUSPENSION"]:
        page = 1
        while page <= 20:
            body = {
                "searchKeywordType": "",
                "productStatusTypes": [status_filter],
                "page": page,
                "size": 100,
                "orderType": "NO",
                "periodType": "PROD_REG_DAY",
            }
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=20)
            except Exception as e:
                return None, f"네트워크 오류: {e}"
            if resp.status_code != 200:
                # 첫 페이지부터 실패하면 에러, 그 외는 종료
                if page == 1 and status_filter == "SALE":
                    return None, f"[{resp.status_code}] {resp.text[:300]}"
                break
            data = resp.json()
            contents = data.get("contents") or data.get("content") or data.get("products") or []
            for item in contents:
                cps = item.get("channelProducts") or []
                cp = cps[0] if cps else item
                # 상태 추출 (응답에 포함된 실제 상태)
                actual_status = (
                    cp.get("statusType") or item.get("statusType") or
                    cp.get("productStatusType") or item.get("productStatusType") or
                    status_filter
                )
                # 카테고리 경로 추출 (wholeCategoryName 또는 leafCategoryId)
                _whole_cat = (
                    item.get("wholeCategoryName") or cp.get("wholeCategoryName") or
                    item.get("categoryPath") or cp.get("categoryPath") or ""
                )
                result.append({
                    "originProductNo": str(item.get("originProductNo") or cp.get("originProductNo") or ""),
                    "channelProductNo": str(cp.get("channelProductNo") or item.get("channelProductNo") or ""),
                    "productName": cp.get("name") or item.get("name") or "",
                    "salePrice": cp.get("salePrice") or item.get("salePrice") or 0,
                    "deliveryFee": _extract_delivery_fee(item, cp),
                    "status": actual_status,
                    "wholeCategoryName": _whole_cat,
                })
            total = data.get("totalElements") or data.get("total")
            if total is not None and sum(1 for r in result if r["status"] == status_filter or actual_status == status_filter) >= total:
                break
            if len(contents) < 100:
                break
            page += 1
    return result, None


def debug_first_product_response(client_id, client_secret):
    """첫 상품 1개 응답을 raw로 반환 (배송비 필드 위치 디버그용)"""
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.commerce.naver.com/external/v1/products/search"
    body = {
        "searchKeywordType": "", "productStatusTypes": ["SALE"],
        "page": 1, "size": 1, "orderType": "NO", "periodType": "PROD_REG_DAY",
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=20)
        if resp.status_code != 200:
            return None, f"[{resp.status_code}] {resp.text[:300]}"
        return resp.json(), None
    except Exception as e:
        return None, str(e)



def get_products_by_nos(client_id, client_secret, product_nos: list):
    """originProductNo 목록으로 개별 상품 정보 조회"""
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    results = []
    errors = []
    for pno in product_nos:
        pno = str(pno).strip()
        if not pno:
            continue
        try:
            resp = requests.get(
                f"https://api.commerce.naver.com/external/v2/products/origin-products/{pno}",
                headers=headers, timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                op = data.get("originProduct", data)
                results.append({
                    "originProductNo": pno,
                    "productName": op.get("name") or data.get("name") or "",
                    "salePrice": op.get("salePrice") or data.get("salePrice") or 0,
                })
            else:
                errors.append(f"{pno}: {resp.status_code}")
        except Exception as e:
            errors.append(f"{pno}: {e}")
    err_msg = ", ".join(errors) if errors else None
    return results, err_msg



def _format_naver_err(resp) -> str:
    """Naver API 에러 응답에서 message + errors 배열까지 추출."""
    try:
        j = resp.json()
        msg = j.get('message', '')
        errs = j.get('errors') or j.get('invalidInputs') or []
        if errs:
            parts = []
            for e in errs[:5]:
                if isinstance(e, dict):
                    f = e.get('field') or e.get('name') or ''
                    m = e.get('message') or e.get('reason') or ''
                    parts.append(f"[{f}] {m}" if f else m)
                else:
                    parts.append(str(e))
            return f"{msg} | invalid: {' / '.join(parts)}"
        return msg or resp.text[:300]
    except Exception:
        return resp.text[:300]


# 네이버 origin product PUT 시 보내면 안 되는 read-only / 시스템 필드

_READONLY_KEYS = {
    'originProductNo', 'productNo', 'channelProductNo',
    'regDate', 'modifiedDate', 'createdDate', 'updatedDate',
    'channelProducts', 'channelServiceType',
    'managerPurchasePoint',  # 적립 관리자 포인트 (계산값일 가능성)
    'representativeImage',   # GET에선 url만 있는 dict이지만 PUT은 images 안에 들어가야 함
    'discountedPrice', 'mobileDiscountedPrice',  # 계산 결과값
    'sellerTags',            # 금칙어 포함된 옛 태그 PUT 시 거부됨 → 가격수정 목적상 항상 제거
    'wholeCategoryName', 'wholeCategoryId',  # leafCategoryId만 있으면 됨
}


# 어디에 있든 발견 시 제거할 태그/검색태그 관련 필드명

_TAG_FIELDS_TO_STRIP = {'sellerTags', 'searchTags', 'searchTagList', 'tagList', 'productTagList'}



def _strip_tag_fields_deep(obj):
    """dict/list 구조를 재귀적으로 탐색하여 _TAG_FIELDS_TO_STRIP 필드 모두 제거 (in-place)."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if k in _TAG_FIELDS_TO_STRIP:
                obj.pop(k, None)
            else:
                _strip_tag_fields_deep(obj[k])
    elif isinstance(obj, list):
        for item in obj:
            _strip_tag_fields_deep(item)



def _sanitize_for_put(d: dict) -> dict:
    """PUT 본문에 부적합한 read-only / 검증 실패 유발 필드 정리."""
    if not isinstance(d, dict):
        return d
    out = {k: v for k, v in d.items() if k not in _READONLY_KEYS}

    # 어디에 있든 태그류 필드는 전부 제거 (금칙어 거부 회피)
    _strip_tag_fields_deep(out)

    # 가격표시제 대상 카테고리: detailAttribute.unitCapacity.unitPriceYn 필수 (boolean)
    # 없거나 문자열이면 false(단위가격 미사용) 채움
    da = out.setdefault('detailAttribute', {})
    if isinstance(da, dict):
        uc = da.setdefault('unitCapacity', {})
        if isinstance(uc, dict):
            cur = uc.get('unitPriceYn')
            if not isinstance(cur, bool):
                uc['unitPriceYn'] = False

    return out



def resolve_origin_product_no(client_id, client_secret, channel_product_no):
    """channelProductNo(스토어 노출 상품번호)로 originProductNo(원상품번호)를 찾는다.
    가격수정 API는 originProductNo만 받으므로, 저장된 번호가 채널번호일 때 변환용.
    반환: (origin_no, err)"""
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.commerce.naver.com/external/v1/products/search"
    target = str(channel_product_no).strip()
    for status in ("SALE", "OUTOFSTOCK", "SUSPENSION", "WAIT", "PROHIBITION"):
        for page in range(1, 21):
            body = {"searchKeywordType": "", "productStatusTypes": [status],
                    "page": page, "size": 100, "orderType": "NO", "periodType": "PROD_REG_DAY"}
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=20)
            except Exception as e:
                return None, f"상품검색 네트워크 오류: {e}"
            if resp.status_code != 200:
                break
            data = resp.json()
            contents = data.get("contents") or data.get("content") or data.get("products") or []
            for it in contents:
                origin = str(it.get("originProductNo") or "")
                for cp in (it.get("channelProducts") or []):
                    if str(cp.get("channelProductNo") or "") == target and origin:
                        return origin, None
            if len(contents) < 100:
                break
    return None, "채널 상품번호에 해당하는 원상품번호를 찾지 못했습니다."



def update_product_price(client_id, client_secret, origin_product_no, new_price,
                         new_shipping_fee=None):
    """스마트스토어 상품 판매가(+배송비) 수정.
    GET origin-products/{번호} → read-only 필드 제거 → salePrice/배송비 교체 → PUT.
    번호가 channelProductNo면 GET이 404 → originProductNo로 변환 후 재조회.
    new_shipping_fee가 None이 아니면 deliveryInfo.deliveryFee.baseFee도 갱신
    (0 이하면 무료배송 전환). (PATCH는 게이트웨이 미지원(GW.NOT_FOUND)이라 사용 안 함)
    반환: (ok, err, used_origin_no)  # used_origin_no: 실제 적용에 쓴 원번호(변환됐으면 새 번호)
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return False, err, None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    pno = str(origin_product_no).strip()
    if not pno:
        return False, "상품번호가 비어 있습니다.", None

    def _get(p):
        return requests.get(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{p}",
            headers=headers, timeout=15)

    try:
        g = _get(pno)
        # 404/403 → channelProductNo로 간주, originProductNo 변환 후 재조회
        # (채널번호를 origin-products로 조회하면 404 또는 403(FORBIDDEN)이 올 수 있음)
        if g.status_code in (403, 404):
            new_origin, rerr = resolve_origin_product_no(client_id, client_secret, pno)
            if new_origin and new_origin != pno:
                pno = new_origin
                g = _get(pno)
            else:
                return False, f"원상품번호를 찾지 못했습니다({g.status_code}). {rerr or ''}".strip(), None
        if g.status_code != 200:
            return False, f"상품 조회 실패({g.status_code}: {_format_naver_err(g)})", None

        data = g.json()
        origin_product = data.get('originProduct') or {}
        if not origin_product:
            return False, f"GET 응답에 originProduct 없음: {str(data)[:200]}", None

        # read-only 제거 + unitCapacity / sellerTags 같은 검증 유발 필드 정리
        origin_product = _sanitize_for_put(dict(origin_product))

        # new_price = 고객이 실제로 결제할 '최종 판매가' 목표.
        # 즉시할인이 걸려 있으면 할인 구조는 그대로 두고, 정가(salePrice)를 보정해
        # (정가 − 할인) = new_price 가 되도록 한다. 할인이 없으면 정가 = new_price.
        _target = int(new_price)
        origin_product['salePrice'] = _target
        _cb = origin_product.get('customerBenefit')
        _disc = ((_cb or {}).get('immediateDiscountPolicy') or {}).get('discountMethod') \
            if isinstance(_cb, dict) else None
        if isinstance(_disc, dict) and _disc.get('value'):
            _dv = int(_disc.get('value') or 0)
            _du = (_disc.get('unitType') or '').upper()
            if _du == 'WON' and _dv > 0:
                origin_product['salePrice'] = _target + _dv          # 정가 = 목표 + 할인액
            elif _du == 'PERCENT' and 0 < _dv < 100:
                origin_product['salePrice'] = int(round(_target / (1 - _dv / 100.0)))  # 정가 = 목표 ÷ (1−할인율)

        # 배송비(택배비) 반영 — 제공된 경우에만
        if new_shipping_fee is not None:
            _di = origin_product.get('deliveryInfo')
            if isinstance(_di, dict):
                _dfee = _di.get('deliveryFee')
                if isinstance(_dfee, dict):
                    _nf = int(new_shipping_fee)
                    if _nf <= 0:
                        _dfee['deliveryFeeType'] = 'FREE'
                        _dfee['baseFee'] = 0
                    else:
                        if _dfee.get('deliveryFeeType') in (None, '', 'FREE'):
                            _dfee['deliveryFeeType'] = 'PAID'
                        _dfee['baseFee'] = _nf

        put_body = {"originProduct": origin_product}
        smartstore = data.get('smartstoreChannelProduct')
        if smartstore:
            put_body["smartstoreChannelProduct"] = _sanitize_for_put(dict(smartstore))

        put_resp = requests.put(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{pno}",
            headers=headers, json=put_body, timeout=20)
        if put_resp.status_code == 200:
            return True, None, pno
        return False, f"판매가 수정 실패({put_resp.status_code}: {_format_naver_err(put_resp)})", None
    except Exception as e:
        return False, f"판매가 수정 예외: {e}", None


def update_product_name(client_id, client_secret, product_no, new_name):
    """스마트스토어 상품명 수정 (originProduct.name 교체).
    update_product_price와 동일한 GET origin-products → sanitize → PUT 구조 재사용.
    번호가 channelProductNo면 GET 404 → originProductNo로 변환 후 재조회.
    반환: (ok, err, used_origin_no)
    """
    _nm = str(new_name or '').strip()
    if not _nm:
        return False, "변경할 상품명이 비어 있습니다.", None
    token, err = get_token(client_id, client_secret)
    if not token:
        return False, err, None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    pno = str(product_no).strip()
    if not pno:
        return False, "상품번호가 비어 있습니다.", None

    def _get(p):
        return requests.get(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{p}",
            headers=headers, timeout=15)

    try:
        g = _get(pno)
        if g.status_code == 404:
            # 저장된 번호가 채널상품번호일 수 있음 → 원상품번호로 변환 후 재조회
            new_origin, rerr = resolve_origin_product_no(client_id, client_secret, pno)
            if new_origin and new_origin != pno:
                pno = new_origin
                g = _get(pno)
            else:
                return False, f"원상품번호를 찾지 못했습니다(404). {rerr or ''}".strip(), None
        if g.status_code == 403:
            return False, ("접근 권한 없음(403) — 이 상품은 현재 커머스 API 키의 스토어 소속이 아닙니다. "
                           "상품이 등록된 스토어의 API 키로 로그인해 수정하세요."), None
        if g.status_code != 200:
            return False, f"상품 조회 실패({g.status_code}: {_format_naver_err(g)})", None

        data = g.json()
        origin_product = data.get('originProduct') or {}
        if not origin_product:
            return False, f"GET 응답에 originProduct 없음: {str(data)[:200]}", None

        origin_product = _sanitize_for_put(dict(origin_product))
        origin_product['name'] = _nm  # ⭐ 상품명만 교체 (가격·옵션 등 나머지는 원본 유지)

        put_body = {"originProduct": origin_product}
        smartstore = data.get('smartstoreChannelProduct')
        if smartstore:
            put_body["smartstoreChannelProduct"] = _sanitize_for_put(dict(smartstore))

        put_resp = requests.put(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{pno}",
            headers=headers, json=put_body, timeout=20)
        if put_resp.status_code == 200:
            return True, None, pno
        return False, f"상품명 수정 실패({put_resp.status_code}: {_format_naver_err(put_resp)})", None
    except Exception as e:
        return False, f"상품명 수정 예외: {e}", None


def update_product_tags(client_id, client_secret, product_no, tags):
    """기존 상품의 연관태그(seoInfo.sellerTags) 교체. tags=[{code,text}] (검증된 태그).
    update_product_name과 동일한 GET→sanitize→PUT 구조. 반환: (ok, err, used_origin_no)."""
    _clean = []
    for _t in (tags or [])[:10]:
        _txt = str((_t or {}).get('text') or '').strip()
        if not _txt:
            continue
        _e = {'text': _txt}
        _code = (_t or {}).get('code')
        if _code not in (None, '', 0):
            _e['code'] = int(_code)
        _clean.append(_e)

    token, err = get_token(client_id, client_secret)
    if not token:
        return False, err, None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    pno = str(product_no).strip()
    if not pno:
        return False, "상품번호가 비어 있습니다.", None

    def _get(p):
        return requests.get(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{p}",
            headers=headers, timeout=15)

    try:
        g = _get(pno)
        if g.status_code == 404:
            # 저장된 번호가 채널상품번호일 수 있음 → 원상품번호로 변환 후 재조회
            new_origin, rerr = resolve_origin_product_no(client_id, client_secret, pno)
            if new_origin and new_origin != pno:
                pno = new_origin
                g = _get(pno)
            else:
                return False, f"원상품번호를 찾지 못했습니다(404). {rerr or ''}".strip(), None
        if g.status_code == 403:
            return False, ("접근 권한 없음(403) — 이 상품은 현재 커머스 API 키의 스토어 소속이 아닙니다. "
                           "상품이 등록된 스토어의 API 키로 로그인해 수정하세요."), None
        if g.status_code != 200:
            return False, f"상품 조회 실패({g.status_code}: {_format_naver_err(g)})", None

        data = g.json()
        origin_product = data.get('originProduct') or {}
        if not origin_product:
            return False, f"GET 응답에 originProduct 없음: {str(data)[:200]}", None

        origin_product = _sanitize_for_put(dict(origin_product))   # 옛 태그(금칙어 포함 가능) 제거
        _da = origin_product.setdefault('detailAttribute', {})
        if isinstance(_da, dict):
            _seo = _da.get('seoInfo') if isinstance(_da.get('seoInfo'), dict) else {}
            if _clean:
                _seo['sellerTags'] = _clean          # 검증된 새 태그 주입
                _da['seoInfo'] = _seo
            else:
                _seo.pop('sellerTags', None)          # 빈 목록이면 태그 제거
                if _seo:
                    _da['seoInfo'] = _seo

        put_body = {"originProduct": origin_product}
        smartstore = data.get('smartstoreChannelProduct')
        if smartstore:
            put_body["smartstoreChannelProduct"] = _sanitize_for_put(dict(smartstore))

        put_resp = requests.put(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{pno}",
            headers=headers, json=put_body, timeout=20)
        if put_resp.status_code == 200:
            return True, None, pno
        return False, f"태그 수정 실패({put_resp.status_code}: {_format_naver_err(put_resp)})", None
    except Exception as e:
        return False, f"태그 수정 예외: {e}", None


def get_origin_product_full(client_id, client_secret, product_no):
    """기존 상품의 원본 전체(originProduct + smartstoreChannelProduct)를 조회.
    번호가 channelProductNo면 originProductNo로 변환 후 재조회.
    편집 화면에서 현재 값(상품명·판매가·카테고리·이미지·태그·자체코드·상세)을 불러오는 용도.
    반환: (data_dict, used_origin_no, err)  # data_dict = GET 응답 원본(JSON)
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    pno = str(product_no).strip()
    if not pno:
        return None, None, "상품번호가 비어 있습니다."

    def _get(p):
        return requests.get(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{p}",
            headers=headers, timeout=15)

    try:
        g = _get(pno)
        if g.status_code in (403, 404):
            new_origin, rerr = resolve_origin_product_no(client_id, client_secret, pno)
            if new_origin and new_origin != pno:
                pno = new_origin
                g = _get(pno)
            elif g.status_code == 403:
                return None, None, ("접근 권한 없음(403) — 이 상품은 현재 커머스 API 키의 스토어 소속이 "
                                    "아닙니다. 상품이 등록된 스토어의 API 키로 로그인하세요.")
            else:
                return None, None, f"원상품번호를 찾지 못했습니다({g.status_code}). {rerr or ''}".strip()
        if g.status_code != 200:
            return None, None, f"상품 조회 실패({g.status_code}: {_format_naver_err(g)})"
        return g.json(), pno, None
    except Exception as e:
        return None, None, f"상품 조회 예외: {e}"


def update_product_full(client_id, client_secret, product_no, updates):
    """기존 상품 종합 수정 — GET origin-products → 필드 교체 → PUT (나머지 원본 보존).
    updates 지원 키(있는 것만 반영):
      name, sale_price, category_id, image_url(대표), extra_image_urls(추가 목록),
      detail_html(상세HTML), seller_tags([{code,text}]), seller_code(자체코드).
    update_product_name/price/tags 와 동일한 GET→sanitize→PUT 구조.
    반환: (ok, err, used_origin_no)
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return False, err, None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    pno = str(product_no).strip()
    if not pno:
        return False, "상품번호가 비어 있습니다.", None
    updates = updates or {}

    def _get(p):
        return requests.get(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{p}",
            headers=headers, timeout=15)

    try:
        g = _get(pno)
        if g.status_code in (403, 404):
            new_origin, rerr = resolve_origin_product_no(client_id, client_secret, pno)
            if new_origin and new_origin != pno:
                pno = new_origin
                g = _get(pno)
            elif g.status_code == 403:
                return False, ("접근 권한 없음(403) — 이 상품은 현재 커머스 API 키의 스토어 소속이 아닙니다. "
                               "상품이 등록된 스토어의 API 키로 로그인해 수정하세요."), None
            else:
                return False, f"원상품번호를 찾지 못했습니다({g.status_code}). {rerr or ''}".strip(), None
        if g.status_code != 200:
            return False, f"상품 조회 실패({g.status_code}: {_format_naver_err(g)})", None

        data = g.json()
        origin_product = data.get('originProduct') or {}
        if not origin_product:
            return False, f"GET 응답에 originProduct 없음: {str(data)[:200]}", None

        # read-only/태그류 제거 (태그는 아래에서 검증본으로 다시 주입)
        origin_product = _sanitize_for_put(dict(origin_product))

        # ── 필드 교체 (updates에 있는 것만) ──
        if updates.get('name'):
            origin_product['name'] = str(updates['name']).strip()[:100]
        if 'sale_price' in updates and updates['sale_price'] is not None:
            origin_product['salePrice'] = int(updates['sale_price'])
        if updates.get('category_id'):
            origin_product['leafCategoryId'] = str(updates['category_id']).strip()
        if updates.get('detail_html'):
            origin_product['detailContent'] = _sanitize_detail_html(updates['detail_html'])

        # 이미지 교체 — 대표/추가 (제공된 경우에만)
        if updates.get('image_url') or ('extra_image_urls' in updates):
            _imgs = origin_product.get('images')
            if not isinstance(_imgs, dict):
                _imgs = {}
            if updates.get('image_url'):
                _imgs['representativeImage'] = {"url": updates['image_url']}
            if 'extra_image_urls' in updates:
                _imgs['optionalImages'] = [{"url": u} for u in (updates.get('extra_image_urls') or []) if u]
            origin_product['images'] = _imgs

        _da = origin_product.setdefault('detailAttribute', {})
        if not isinstance(_da, dict):
            _da = {}
            origin_product['detailAttribute'] = _da

        # 판매자 자체코드(코스트코 번호)
        if 'seller_code' in updates:
            _sc = str(updates.get('seller_code') or '').strip()
            if _sc:
                _sci = _da.get('sellerCodeInfo') if isinstance(_da.get('sellerCodeInfo'), dict) else {}
                _sci['sellerManagementCode'] = _sc[:50]
                _da['sellerCodeInfo'] = _sci

        # 연관태그(SEO sellerTags) — 검증된 [{code,text}] (sanitize가 지운 뒤 새로 주입)
        if 'seller_tags' in updates:
            _clean = []
            for _t in (updates.get('seller_tags') or [])[:10]:
                _txt = str((_t or {}).get('text') or '').strip()
                if not _txt:
                    continue
                _e = {'text': _txt}
                _code = (_t or {}).get('code')
                if _code not in (None, '', 0):
                    _e['code'] = int(_code)
                _clean.append(_e)
            _seo = _da.get('seoInfo') if isinstance(_da.get('seoInfo'), dict) else {}
            if _clean:
                _seo['sellerTags'] = _clean
                _da['seoInfo'] = _seo
            else:
                _seo.pop('sellerTags', None)
                if _seo:
                    _da['seoInfo'] = _seo

        put_body = {"originProduct": origin_product}
        smartstore = data.get('smartstoreChannelProduct')
        if smartstore:
            put_body["smartstoreChannelProduct"] = _sanitize_for_put(dict(smartstore))

        put_resp = requests.put(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{pno}",
            headers=headers, json=put_body, timeout=20)
        if put_resp.status_code == 200:
            return True, None, pno
        return False, f"상품 수정 실패({put_resp.status_code}: {_format_naver_err(put_resp)})", None
    except Exception as e:
        return False, f"상품 수정 예외: {e}", None


# ── 정산 내역 조회 ─────────────────────────────────────────
