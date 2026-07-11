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



def upload_product_image(client_id, client_secret, image_source):
    """
    이미지(로컬 파일 경로 또는 URL)를 네이버 CDN에 업로드.
    반환: (naver_cdn_url, error_msg)
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err

    headers = {"Authorization": f"Bearer {token}"}
    tmp_path = None

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

        import os as _os
        fname = _os.path.basename(src_path)
        with open(src_path, "rb") as f:
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
        if tmp_path:
            try:
                import os
                os.remove(tmp_path)
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

    try:
        resp = requests.post(
            "https://api.commerce.naver.com/external/v2/products",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            pno = str(data.get("originProductNo") or data.get("productNo") or "")
            return {"origin_product_no": pno}, None
        return None, f"상품 등록 실패({resp.status_code}): {_format_naver_err(resp)}"
    except Exception as e:
        return None, str(e)


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


# ── 정산 내역 조회 ─────────────────────────────────────────
