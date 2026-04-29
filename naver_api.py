import time, json, requests, bcrypt, pybase64, math
from datetime import datetime, timedelta, timezone

def get_token(client_id, client_secret):
    timestamp = str(int((time.time() - 10) * 1000))
    password = client_id + "_" + timestamp
    try:
        hashed = bcrypt.hashpw(password.encode('utf-8'), client_secret.encode('utf-8'))
        sign = pybase64.standard_b64encode(hashed).decode('utf-8')
        resp = requests.post("https://api.commerce.naver.com/external/v1/oauth2/token", data={
            "client_id": client_id, "timestamp": timestamp, "client_secret_sign": sign,
            "grant_type": "client_credentials", "type": "SELF"
        })
        return resp.json().get("access_token"), None
    except: return None, "토큰 실패"

def get_new_orders(client_id, client_secret, hours_back=48, status_type="ALL"):
    token, err = get_token(client_id, client_secret)
    if not token: return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    # 💡 [핵심] 대시보드에서 1시간을 고르더라도, 어제 오후 1시 주문을 놓치지 않기 위해 무조건 최소 48시간을 검색하게 강제합니다.
    hours_to_search = max(48, hours_back) 
    
    kst = timezone(timedelta(hours=9))
    end_dt = datetime.now(kst) - timedelta(minutes=1)
    all_ids = set()
    
    # 네이버의 24시간 제약을 피하기 위해, 48시간을 24시간씩 딱 2번만 쪼개서 초고속으로 검색합니다.
    loops = math.ceil(hours_to_search / 24)
    for i in range(loops):
        to_dt = end_dt - timedelta(hours=24 * i)
        from_dt = to_dt - timedelta(hours=23, minutes=59) 
        
        params = {
            "lastChangedFrom": from_dt.strftime("%Y-%m-%dT%H:%M:%S.000+09:00"),
            "lastChangedTo": to_dt.strftime("%Y-%m-%dT%H:%M:%S.000+09:00")
        }
        
        url = "https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/last-changed-statuses"
        resp = requests.get(url, headers=headers, params=params)
        
        if resp.status_code == 200:
            data = resp.json().get("data", {}).get("lastChangeStatuses", [])
            for item in data:
                if isinstance(item, dict) and "productOrderId" in item:
                    all_ids.add(item["productOrderId"])

    if not all_ids: return [], None

    unique_ids = list(all_ids)
    orders = []
    
   # 상세 조회
    for i in range(0, len(unique_ids), 300):
        chunk = unique_ids[i:i+300]
        query_url = "https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/query"
        d_resp = requests.post(query_url, headers=headers, json={"productOrderIds": chunk})
        
        if d_resp.status_code == 200:
            items = d_resp.json().get("data", [])
            for item in items:  # 💡 여기서부터 줄맞춤이 중요합니다.
                po = item.get("productOrder", {})
                o = item.get("order", {})
                
                p_status = po.get("productOrderStatus", "")
                place_status = po.get("placeOrderStatus") or po.get("placeOrderStatusType", "")
                
                if p_status == "PAYED":
                    sa = item.get("shippingAddress", {}) if item.get("shippingAddress") else po.get("shippingAddress", {})
                    total_payment = po.get("totalPaymentAmount", po.get("unitPrice", 0) * po.get("quantity", 1))
                    
                   # 1. 네이버 표준 72개 컬럼 (BU열 '수령위치 내용'까지)
                    naver_columns = [
                        "상품주문번호", "주문번호", "배송속성", "풀필먼트사(주문 기준)", "택배사(주문 기준)", "배송방법(구매자 요청)", 
                        "배송방법", "택배사", "송장번호", "발송일", "판매채널", "구매자명", "구매자ID", "수취인명", "주문상태", 
                        "주문세부상태", "수량클레임 여부", "결제위치", "결제일", "상품번호", "상품명", "상품종류", "반품안심케어", 
                        "멤버십N배송", "옵션정보", "옵션관리코드", "수량", "옵션가격", "상품가격", "최종 상품별 할인액", 
                        "최초 상품별 할인액", "판매자 부담 할인액", "최종 상품별 총 주문금액", "최초 상품별 총 주문금액", "사은품", 
                        "발주확인일", "발송기한", "발송처리일", "송장출력일", "배송비 형태", "배송비 묶음번호", "배송비 유형", 
                        "배송비 합계", "제주/도서 추가배송비", "배송비 할인액", "판매자 상품코드", "판매자 내부코드1", "판매자 내부코드2", 
                        "수취인연락처1", "수취인연락처2", "통합배송지", "기본배송지", "상세배송지", "구매자연락처", "우편번호", "배송메세지", 
                        "출고지", "결제수단", "네이버페이 주문관리 수수료", "매출연동 수수료", "정산예정금액", "개인통관고유부호", "주문일시", 
                        "배송희망일", "구독신청회차", "구독진행회차", "구독배송희망일", "배송태그 유형", "출입방법 유형", "출입방법 내용", 
                        "수령위치 유형", "수령위치 내용"
                    ]

                    order_dict = {col: "" for col in naver_columns}

                    # 2. 실제 데이터 매핑 (내부용 영문 필드들은 모두 삭제함)
                    order_dict.update({
                        "상품주문번호": po.get("productOrderId", ""),
                        "주문번호": o.get("orderId", ""),
                        "배송속성": "당일발송",
                        "배송방법": "택배,등기,소포",
                        "배송방법(구매자 요청)": "택배,등기,소포",
                        "판매채널": "스마트스토어",
                        "구매자명": o.get("ordererName", ""),
                        "구매자ID": o.get("ordererId", ""),
                        "수취인명": sa.get("name", ""),
                        "주문상태": "발송대기",
                        "주문세부상태": "발주확인",
                        "결제일": str(po.get("paymentDate", "")).replace("T", " ")[:19],
                        "상품번호": po.get("productId", ""),
                        "상품명": po.get("productName", ""),
                        "옵션정보": po.get("productOption", ""),
                        "수량": po.get("quantity", 1),
                        "상품가격": po.get("unitPrice", 0),
                        "최종 상품별 총 주문금액": total_payment,
                        "최초 상품별 총 주문금액": total_payment,
                        "발주확인일": str(po.get("placeOrderDate", "")).replace("T", " ")[:19] if po.get("placeOrderDate") else "",
                        "발송기한": str(po.get("shippingDueDate", "")).replace("T", " ")[:19] if po.get("shippingDueDate") else "",
                        "배송비 합계": po.get("deliveryFeeAmount", 0),
                        "수취인연락처1": sa.get("tel1", ""),
                        "수취인연락처2": sa.get("tel2", ""),
                        "통합배송지": f"{sa.get('baseAddress', '')} {sa.get('detailedAddress', '')}".strip(),
                        "기본배송지": sa.get('baseAddress', ''),
                        "상세배송지": sa.get('detailedAddress', ''),
                        "우편번호": sa.get("zipCode", ""),
                        "배송메세지": po.get("shippingMemo", ""),
                        "주문일시": str(o.get("orderDate", "")).replace("T", " ")[:19] if o.get("orderDate") else "",
                        "개인통관고유부호": o.get("individualCustomsExtractionNumber", ""),
                        "정산예정금액": total_payment
                    })
                    
                    orders.append(order_dict)
                
    return orders, None

def ship_orders(client_id, client_secret, ship_data):
    from datetime import datetime, timedelta, timezone
    import requests

    token, err = get_token(client_id, client_secret)
    if not token:
        return None, f"토큰 오류: {err}"

    # 1. 네이버 커머스 API 표준 택배사 코드 (공식 문서 기준 정밀 매핑)
    # 💡 롯데택배는 반드시 'HYUNDAI'를 써야 합니다.
    courier_map = {
        "CJ대한통운": "CJGLS", "대한통운": "CJGLS", "CJ": "CJGLS",
        "우체국택배": "EPOST", "우체국": "EPOST",
        "한진택배": "HANJIN", "한진": "HANJIN",
        "롯데택배": "HYUNDAI", "롯데": "HYUNDAI", # ✨ 결정적 수정: LOTTE -> HYUNDAI
        "로젠택배": "KGB", "로젠": "KGB",          # ✨ 결정적 수정: LOGEN -> KGB
        "경동택배": "KDEXP", "경동": "KDEXP",
        "대신택배": "DAESIN", "대신": "DAESIN"
    }

    url = "https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/dispatch"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    # 2. 날짜 형식 (네이버 표준 ISO8601)
    now = datetime.now(timezone(timedelta(hours=9)))
    dispatch_date = now.strftime("%Y-%m-%dT%H:%M:%S.000+09:00")

    dispatch_list = []
    for item in ship_data:
        input_name = str(item.get("택배사", "")).strip()
        final_code = courier_map.get(input_name, "ETC")
        
        # 만약 입력된 택배사 이름이 이미 영문 대문자 코드 형태라면 그대로 사용
        if input_name.isupper() and len(input_name) >= 3:
            final_code = input_name

        dispatch_list.append({
            "productOrderId": str(item["productOrderId"]),
            "deliveryMethod": "DELIVERY",
            "deliveryCompanyCode": final_code,
            "trackingNumber": str(item["trackingNumber"]),
            "dispatchDate": dispatch_date
        })

    # 3. 데이터 전송 (네이버 V1 표준 키: dispatchProductOrders)
    payload = {"dispatchProductOrders": dispatch_list}
    
    try:
        resp = requests.post(url, headers=headers, json=payload)
        res_json = resp.json()
        
        if resp.status_code == 200:
            data = res_json.get("data", {})
            s_list = data.get("successProductOrderIds", [])
            f_list = data.get("failProductOrderInfos", [])
            
            fail_details = []
            for f in f_list:
                poid = f.get("productOrderId", "")
                msg = f.get("message", "사유 미상")
                # 디버깅을 위해 전송된 코드 포함
                sent_code = "알수없음"
                for d in dispatch_list:
                    if d["productOrderId"] == poid:
                        sent_code = d["deliveryCompanyCode"]
                        break
                fail_details.append(f"[{poid}] {msg} (시도코드: {sent_code})")
            
            return {
                "success": len(s_list),
                "fail": len(f_list),
                "fail_details": fail_details
            }, None
        else:
            return None, f"API 오류({resp.status_code}): {res_json.get('message', '사유 미상')}"
            
    except Exception as e:
        return None, f"시스템 에러: {str(e)}"

# ✅ 텔레그램 NoneType 에러 완벽 해결
def send_telegram(tok, cid, msg):
    try: 
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": cid, "text": msg})
        return True, None
    except Exception as e: 
        return False, str(e)

# ✅ 카카오톡 나에게 보내기 (REST API)
def send_kakao(access_token, msg, rest_api_key=None, refresh_token=None):
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    payload = {
        "template_object": json.dumps({
            "object_type": "text",
            "text": msg,
            "link": {
                "web_url": "https://sell.smartstore.naver.com",
                "mobile_web_url": "https://sell.smartstore.naver.com"
            },
            "button_title": "스마트스토어 바로가기"
        })
    }
    try:
        resp = requests.post(url, headers=headers, data=payload)
        if resp.status_code == 200:
            return True, None
        
        # 401 에러이고 refresh_token이 있으면 토큰 갱신 시도
        if resp.status_code == 401 and refresh_token and rest_api_key:
            new_token, new_refresh, err = refresh_kakao_token(rest_api_key, refresh_token)
            if new_token:
                # 갱신된 토큰으로 재시도
                headers["Authorization"] = f"Bearer {new_token}"
                resp2 = requests.post(url, headers=headers, data=payload)
                if resp2.status_code == 200:
                    return True, f"__TOKEN_REFRESHED__{new_token}||{new_refresh}"
                else:
                    return False, f"카카오톡 전송 실패 (갱신 후 {resp2.status_code}): {resp2.text}"
            else:
                return False, f"토큰 갱신 실패: {err}. 원본 오류: {resp.text}"
        
        return False, f"카카오톡 전송 실패 ({resp.status_code}): {resp.text}"
    except Exception as e:
        return False, str(e)

def refresh_kakao_token(rest_api_key, refresh_token):
    """카카오 refresh_token으로 새 access_token 발급"""
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token
    }
    try:
        resp = requests.post(url, data=data)
        if resp.status_code == 200:
            result = resp.json()
            new_access = result.get("access_token", "")
            new_refresh = result.get("refresh_token", refresh_token)  # 없으면 기존 것 유지
            return new_access, new_refresh, None
        else:
            return None, None, f"갱신 실패 ({resp.status_code}): {resp.text}"
    except Exception as e:
        return None, None, str(e)

def get_kakao_token_by_code(rest_api_key, auth_code, redirect_uri="http://localhost"):
    """인가 코드로 카카오 access_token + refresh_token 발급"""
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "code": auth_code
    }
    try:
        resp = requests.post(url, data=data)
        if resp.status_code == 200:
            result = resp.json()
            return result.get("access_token"), result.get("refresh_token"), None
        else:
            return None, None, f"토큰 발급 실패 ({resp.status_code}): {resp.text}"
    except Exception as e:
        return None, None, str(e)

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
            import urllib.request, tempfile, os
            ext = os.path.splitext(image_source.split("?")[0])[-1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".jpg"
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
            os.close(tmp_fd)
            req = urllib.request.Request(
                image_source,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.costco.co.kr/"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(tmp_path, "wb") as f:
                    f.write(resp.read())
            src_path = tmp_path
        else:
            src_path = image_source

        with open(src_path, "rb") as f:
            resp = requests.post(
                "https://api.commerce.naver.com/external/v1/product-images/upload",
                headers=headers,
                files={"imageFiles": f},
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
    detail = product_info.get("detail_content") or f"<p>{name}</p>"
    as_tel = product_info.get("after_service_tel") or "1588-1234"
    origin = product_info.get("origin_code") or "03"

    payload = {
        "originProduct": {
            "statusType": "SALE",
            "saleType": "NEW",
            "leafCategoryId": str(product_info["category_id"]),
            "name": name,
            "detailContent": detail,
            "images": {
                "representativeImage": {"url": product_info["image_url"]},
                "optionalImages": [],
            },
            "salePrice": int(product_info["sale_price"]),
            "stockQuantity": int(product_info.get("stock", 100)),
            "deliveryInfo": {
                "deliveryType": "DELIVERY",
                "deliveryAttributeType": "NORMAL",
                "deliveryFee": {
                    "deliveryFeeType": fee_type,
                    "baseFee": shipping_fee,
                    "deliveryFeePayType": "PREPAID",
                },
                "returnDeliveryFee": {
                    "deliveryFeeType": "CHARGE",
                    "baseFee": 5000,
                    "deliveryFeePayType": "COLLECT",
                },
                "exchangeDeliveryFee": {
                    "deliveryFeeType": "CHARGE",
                    "baseFee": 5000,
                    "deliveryFeePayType": "COLLECT",
                },
            },
            "detailAttribute": {
                "afterServiceInfo": {
                    "afterServiceTelephoneNumber": as_tel,
                    "afterServiceGuideContent": "판매자에게 문의해 주세요.",
                },
                "originAreaInfo": {
                    "originAreaCode": origin,
                    "content": "",
                },
            },
        }
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
        msg = resp.json().get("message") or resp.text[:400]
        return None, f"상품 등록 실패({resp.status_code}): {msg}"
    except Exception as e:
        return None, str(e)


def calc_min_price(unit_cost, shipping_cost, box_cost, target_margin_rate):
    """적자 안 나는 최소 판매가 계산 (네이버 수수료 5.5% 기준)"""
    total_cost = unit_cost + shipping_cost + box_cost
    return int(total_cost * (1 + target_margin_rate) / 0.945 / 100) * 100

def get_product_list(client_id, client_secret):
    """스마트스토어 판매중인 상품 목록 조회"""
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.get(
            "https://api.commerce.naver.com/external/v2/products/origin-products",
            headers=headers,
            params={"sellerId": client_id, "page": 1, "size": 100, "productStatusType": "SALE"}
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("originProducts", [])
            result = []
            for item in items:
                result.append({
                    "originProductNo": item.get("originProductNo", ""),
                    "productName": item.get("name", ""),
                    "salePrice": item.get("salePrice", 0),
                })
            return result, None
        return None, f"상품 조회 실패({resp.status_code}): {resp.text[:200]}"
    except Exception as e:
        return None, str(e)

def update_product_price(client_id, client_secret, origin_product_no, new_price):
    """스마트스토어 상품 판매가 수정"""
    token, err = get_token(client_id, client_secret)
    if not token:
        return False, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.patch(
            f"https://api.commerce.naver.com/external/v2/products/origin-products/{origin_product_no}",
            headers=headers,
            json={"originProduct": {"salePrice": new_price}}
        )
        if resp.status_code == 200:
            return True, None
        return False, f"가격 수정 실패({resp.status_code}): {resp.json().get('message', resp.text[:100])}"
    except Exception as e:
        return False, str(e)

# ✅ CJ대한통운 API 접수 (가상 구현 - 실제 API 연동 시 수정 필요)
def register_cj_order(api_id, api_pw, account_no, order_data):
    """
    CJ대한통운 API를 통해 주문을 접수하고 송장번호를 받아옵니다.
    실제 CJ API 명세에 따라 이 부분을 구현해야 합니다.
    """
    import random
    results = []
    for item in order_data:
        # 실제로는 여기서 requests.post 등으로 CJ API를 호출
        tracking_no = f"68{random.randint(1000000000, 9999999999)}"
        results.append({
            "productOrderId": item['productOrderId'],
            "trackingNumber": tracking_no
        })
    return results, None