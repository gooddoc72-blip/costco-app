import time, json, requests, bcrypt, pybase64, math
from datetime import datetime, timedelta, timezone

def get_token(client_id, client_secret):
    timestamp = str(int((time.time() - 10) * 1000))
    password = client_id + "_" + timestamp
    last_err = None
    # 일시적 네트워크 지연/장애에 대비해 2회 재시도 (총 3회), timeout 30s
    for _attempt in range(3):
        try:
            hashed = bcrypt.hashpw(password.encode('utf-8'), client_secret.encode('utf-8'))
            sign = pybase64.standard_b64encode(hashed).decode('utf-8')
            resp = requests.post("https://api.commerce.naver.com/external/v1/oauth2/token", data={
                "client_id": client_id, "timestamp": timestamp, "client_secret_sign": sign,
                "grant_type": "client_credentials", "type": "SELF"
            }, timeout=30)
            try:
                _body = resp.json()
            except Exception:
                return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
            _token = _body.get("access_token")
            if _token:
                return _token, None
            _msg = _body.get("message") or _body.get("error_description") or _body.get("error") or str(_body)[:200]
            return None, f"HTTP {resp.status_code} — {_msg}"
        except Exception as e:
            last_err = e
            if _attempt < 2:
                time.sleep(1.5)  # 짧은 backoff 후 재시도
                continue
    return None, f"토큰 실패: {last_err}"

def get_new_orders(client_id, client_secret, hours_back=48, status_type="ALL"):
    import concurrent.futures as _cf
    token, err = get_token(client_id, client_secret)
    if not token: return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 호출자가 hours_back을 결정 (증분 동기화: 마지막 sync 이후 시간만큼만)
    # 최소 24h만 보장 (그보다 짧으면 API 1회 호출로 충분)
    hours_to_search = max(24, hours_back)

    kst = timezone(timedelta(hours=9))
    end_dt = datetime.now(kst) - timedelta(minutes=1)

    # 네이버 API 24시간 제약 → 14일을 24h씩 14개 구간으로 분할 후 병렬 요청
    loops = math.ceil(hours_to_search / 24)
    _url = "https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/last-changed-statuses"

    # API 요청당 최대 24h 제약 → 23h59m 윈도우 + 인접 구간이 1초만 겹치도록 정렬해 갭 제거
    def _fetch_window(i):
        to_dt   = end_dt - timedelta(seconds=int(86399 * i))   # 23h59m59s 간격
        from_dt = to_dt  - timedelta(hours=23, minutes=59, seconds=59)
        params  = {
            "lastChangedFrom": from_dt.strftime("%Y-%m-%dT%H:%M:%S.000+09:00"),
            "lastChangedTo":   to_dt.strftime("%Y-%m-%dT%H:%M:%S.000+09:00"),
        }
        try:
            resp = requests.get(_url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("lastChangeStatuses", [])
                return {item["productOrderId"] for item in data
                        if isinstance(item, dict) and "productOrderId" in item}
            return set()
        except Exception as e:
            # 병렬 윈도우 실패 시 조용히 누락되지 않도록 stderr에 기록
            import sys
            print(f"[naver_api] _fetch_window({i}) 실패: {e}", file=sys.stderr)
            return set()

    all_ids = set()
    with _cf.ThreadPoolExecutor(max_workers=14) as ex:
        for ids in ex.map(_fetch_window, range(loops)):
            all_ids.update(ids)

    if not all_ids: return [], None

    unique_ids = list(all_ids)
    orders = []

    _status_dist = {}  # 디버그: 상태별 카운트

    # 상세 조회 - 모든 상태 주문을 반환 (DB에 누적 저장하기 위해)
    for i in range(0, len(unique_ids), 300):
        chunk = unique_ids[i:i+300]
        query_url = "https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/query"
        d_resp = requests.post(query_url, headers=headers, json={"productOrderIds": chunk}, timeout=30)

        if d_resp.status_code == 200:
            items = d_resp.json().get("data", [])
            for item in items:
                po = item.get("productOrder", {})
                o  = item.get("order", {})

                p_status = po.get("productOrderStatus", "")
                place_status = po.get("placeOrderStatus") or po.get("placeOrderStatusType", "")
                _key = f"{p_status}/{place_status}" if place_status else p_status
                _status_dist[_key] = _status_dist.get(_key, 0) + 1

                # status_type 인자 활용: 호출자가 지정한 단일 상태만 통과
                # (예: 'READY'=배송준비, 'PAYED'=결제완료). 'ALL'이거나 빈 값이면 모든 상태 허용.
                _allowed = (not status_type) or status_type == "ALL" or (p_status == status_type)
                if p_status and _allowed:  # 빈 status 제외 + 필터 통과만
                    sa = item.get("shippingAddress", {}) if item.get("shippingAddress") else po.get("shippingAddress", {})
                    total_payment = po.get("totalPaymentAmount", po.get("unitPrice", 0) * po.get("quantity", 1))

                    # 수수료 (네이버페이 주문관리 수수료 + 매출연동 수수료) — 다양한 필드명 방어적 탐색
                    _np_commission = int(
                        po.get("payCommissionAmount")
                        or po.get("naverPayCommissionAmount")
                        or po.get("paymentCommissionAmount")
                        or 0
                    )
                    _sales_commission = int(
                        po.get("salesCommissionAmount")
                        or po.get("naverShoppingCommissionAmount")
                        or po.get("knowledgeShoppingSellingInterlockCommissionAmount")
                        or po.get("linkedShoppingCommissionAmount")
                        or 0
                    )
                    # 1차: API가 정산예정금액을 직접 제공하면 그 값 사용
                    # 2차: 결제금액 - 수수료들로 계산 (수수료가 하나라도 있으면)
                    # 3차: 폴백 — 결제금액 (옛 동작, 수수료 정보 없을 때만)
                    _expected_settle = po.get("expectedSettlementAmount") or po.get("settleAmount")
                    if _expected_settle:
                        expected_settlement = int(_expected_settle)
                    elif _np_commission or _sales_commission:
                        expected_settlement = int(total_payment) - _np_commission - _sales_commission
                    else:
                        expected_settlement = int(total_payment)
                    
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

                    # API 영문 상태코드 → 네이버 엑셀 한글 표기 변환
                    _STATUS_KO = {
                        "PAYED": "결제완료", "INSTRUCT": "발주확인",
                        "PRODUCT_READY": "발송대기", "DELIVERING": "배송중",
                        "DELIVERED": "배송완료", "PURCHASE_DECIDED": "구매확정",
                        "CANCELED": "취소완료", "RETURNED": "반품완료",
                        "EXCHANGED": "교환완료", "CANCEL_NOPAY": "미결제취소",
                    }
                    _SUB_STATUS_KO = {
                        "INSTRUCT": "발주확인", "CANCEL": "취소", "RETURN": "반품",
                        "NOT_YET": "미발주", "OK": "",
                    }

                    # 2. 실제 데이터 매핑
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
                        "주문상태": _STATUS_KO.get(p_status, p_status),
                        "주문세부상태": _SUB_STATUS_KO.get(place_status, place_status) if place_status else "",
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
                        "제주/도서 추가배송비": po.get("extraDeliveryFeeAmount", po.get("islandDeliveryFeeAmount", 0)),
                        "수취인연락처1": sa.get("tel1", ""),
                        "수취인연락처2": sa.get("tel2", ""),
                        "통합배송지": f"{sa.get('baseAddress', '')} {sa.get('detailedAddress', '')}".strip(),
                        "기본배송지": sa.get('baseAddress', ''),
                        "상세배송지": sa.get('detailedAddress', ''),
                        "우편번호": sa.get("zipCode", ""),
                        "배송메세지": po.get("shippingMemo", ""),
                        "주문일시": str(o.get("orderDate", "")).replace("T", " ")[:19] if o.get("orderDate") else "",
                        "개인통관고유부호": o.get("individualCustomsExtractionNumber", ""),
                        "네이버페이 주문관리 수수료": -_np_commission if _np_commission else 0,
                        "매출연동 수수료":           -_sales_commission if _sales_commission else 0,
                        "정산예정금액":              expected_settlement,
                    })
                    
                    orders.append(order_dict)

    # 디버그: status 분포를 모듈 변수에 저장 (UI에서 표시 가능)
    global _last_status_dist
    _last_status_dist = _status_dist
    return orders, None


_last_status_dist = {}

def get_last_status_dist():
    """최근 get_new_orders 호출의 status 분포 반환 (디버그용)."""
    return dict(_last_status_dist)

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

        # 입력된 택배사 이름이 이미 영문 대문자 코드 형태라면 그대로 사용
        if input_name.isupper() and len(input_name) >= 3:
            final_code = input_name

        dispatch_list.append({
            "productOrderId": str(item["productOrderId"]),
            "deliveryMethod": "DELIVERY",
            "deliveryCompanyCode": final_code,
            "trackingNumber": str(item["trackingNumber"]),
            "dispatchDate": dispatch_date,
        })

    # 3. 데이터 전송 — 최대 30건씩 분할, 실패 항목 제외 자동 재시도
    import re as _re
    CHUNK_SIZE = 30
    chunks = [dispatch_list[i:i+CHUNK_SIZE] for i in range(0, len(dispatch_list), CHUNK_SIZE)]
    total_success = 0
    total_fail = 0
    all_fail_details = []
    success_order_ids = []

    try:
        for chunk in chunks:
            remaining = list(chunk)
            # 실패 항목을 제거하며 최대 len(chunk)번 재시도
            for _attempt in range(len(chunk) + 1):
                if not remaining:
                    break
                payload = {"dispatchProductOrders": remaining}
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                res_json = resp.json()

                if resp.status_code == 200:
                    data = res_json.get("data", {})
                    s_list = data.get("successProductOrderIds", [])
                    f_list = data.get("failProductOrderInfos", [])
                    total_success += len(s_list)
                    total_fail += len(f_list)
                    success_order_ids.extend(str(x) for x in s_list)
                    for f in f_list:
                        poid = f.get("productOrderId", "")
                        msg = f.get("message", "사유 미상")
                        all_fail_details.append(f"[실패] {poid}: {msg}")
                    break  # 청크 성공, 다음 청크로

                # 400 오류 — 실패 인덱스 파싱 후 해당 항목 제거 후 재시도
                err_msg = res_json.get('message', '사유 미상')
                idx_match = _re.search(r'contents\[(\d+)\]', err_msg)
                if idx_match:
                    fail_idx = int(idx_match.group(1))
                    if fail_idx < len(remaining):
                        bad = remaining.pop(fail_idx)
                        bad_poid = bad.get('productOrderId', '알수없음')
                        all_fail_details.append(
                            f"[건너뜀] {bad_poid}: 발송처리 불가 (이미 처리됐거나 취소/반품 상태)"
                        )
                        total_fail += 1
                        continue  # 재시도
                # 인덱스 파싱 불가 또는 반복 실패 → 전체 청크 실패 처리
                all_fail_details.append(
                    f"[오류 400] {err_msg} ({len(remaining)}건 전체 실패)"
                )
                total_fail += len(remaining)
                break

        return {
            "success": total_success,
            "fail": total_fail,
            "fail_details": all_fail_details,
            "sent_count": len(dispatch_list),
            "success_order_ids": success_order_ids,
        }, None

    except Exception as e:
        return None, f"시스템 에러: {str(e)}"

# ✅ 텔레그램 NoneType 에러 완벽 해결
def send_telegram(tok, cid, msg):
    try: 
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": cid, "text": msg}, timeout=10)
        return True, None
    except Exception as e: 
        return False, str(e)

# ✅ 카카오톡 나에게 보내기 (REST API)
def send_kakao(access_token, msg, rest_api_key=None, refresh_token=None):
    """카카오톡 메모. 1차로 전체를 '한 건'에 담아 발송(분리 없음).
    길이 초과 등으로 1차가 실패할 때만 200자 단위로 나눠 남은 부분까지 이어 발송."""
    import time as _t
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    state = {"refreshed": None}

    def _post(text):
        """단건 발송. 401이면 토큰 갱신 후 1회 재시도. 반환: (resp, token_err)"""
        payload = {"template_object": json.dumps({
            "object_type": "text", "text": text,
            "link": {"web_url": "https://sell.smartstore.naver.com",
                     "mobile_web_url": "https://sell.smartstore.naver.com"},
            "button_title": "스마트스토어 바로가기",
        })}
        resp = requests.post(url, headers=headers, data=payload, timeout=15)
        if resp.status_code == 401 and refresh_token and rest_api_key:
            new_token, new_refresh, err = refresh_kakao_token(rest_api_key, refresh_token)
            if not new_token:
                return resp, f"토큰 갱신 실패: {err}"
            headers["Authorization"] = f"Bearer {new_token}"
            state["refreshed"] = f"__TOKEN_REFRESHED__{new_token}||{new_refresh}"
            resp = requests.post(url, headers=headers, data=payload, timeout=15)
        return resp, None

    text = msg or ''

    # 1차: 전체를 한 건으로 발송 시도 (성공 시 단일 메시지 → 분리 없음)
    try:
        resp, tok_err = _post(text)
        if tok_err:
            return False, tok_err
        if resp.status_code == 200:
            return True, state["refreshed"]
    except Exception as e:
        return False, f"카카오 발송 예외: {e}"

    # 1차 실패 시 fallback → 큰 단위로만 나눠 남은 부분까지 이어 발송
    # (카카오 memo/default text는 실측 8000자 이상도 단건 허용 → 분할은 거의 안 일어남)
    MAX = 3500
    chunks = [text[i:i+MAX] for i in range(0, len(text), MAX)] if text else ['']
    total = len(chunks)
    sent = 0
    for ci, chunk in enumerate(chunks):
        if ci > 0:
            _t.sleep(0.5)  # 청크 사이 sleep — rate limit/순서 보장
        try:
            resp, tok_err = _post(chunk)
            if tok_err:
                return False, f"{tok_err} ({ci+1}/{total} 발송 중)"
            if resp.status_code != 200:
                return False, f"카카오 발송 실패 (성공 {sent}/{total}, 청크 {ci+1} 실패 {resp.status_code}): {resp.text[:120]}"
            sent += 1
        except Exception as e:
            return False, f"카카오 발송 예외 (성공 {sent}/{total}): {e}"
    return True, state["refreshed"]

def refresh_kakao_token(rest_api_key, refresh_token):
    """카카오 refresh_token으로 새 access_token 발급"""
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token
    }
    try:
        resp = requests.post(url, data=data, timeout=10)
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
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            return result.get("access_token"), result.get("refresh_token"), None
        else:
            return None, None, f"토큰 발급 실패 ({resp.status_code}): {resp.text}"
    except Exception as e:
        return None, None, str(e)

def _get_category_cache_path():
    import os
    base = os.path.dirname(os.path.abspath(__file__))
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
    detail = (
        product_info.get("detail_html")
        or product_info.get("detail_content")
        or f"<p>{name}</p>"
    )
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
            "benefitInfo": {
                "reviewPointPolicy": {
                    "textReviewPoint":              rp.get("text", 50),
                    "photoVideoReviewPoint":        rp.get("photo", 100),
                    "afterUseTextReviewPoint":      rp.get("after_text", 100),
                    "afterUsePhotoVideoReviewPoint": rp.get("after_photo", 100),
                }
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
                "productInfoProvidedNotice": {
                    "productInfoProvidedNoticeType": "ETC",
                    "etc": {
                        "returnCostReason":          "상품 상세페이지 참조",
                        "noRefundReason":            "상품 상세페이지 참조",
                        "qualityAssuranceStandard":  "상품 상세페이지 참조",
                        "compensationProcedure":     "상품 상세페이지 참조",
                        "troubleShootingContents":   "상품 상세페이지 참조",
                    },
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


# 매칭 정보 디버그 출력용 (마지막 매칭 결과 저장)
_last_match_info = [""]

def get_last_match_info():
    return _last_match_info[0]

def check_keyword_rank(open_client_id, open_client_secret, keyword,
                       our_product_name='', naver_product_no='',
                       store_name='', max_pages=10):
    """
    네이버 쇼핑 검색에서 원부/단독 순위 별도 추적 (최대 1000위 탐색)
    반환: (rank_wonbu, rank_solo, error)
      - rank_wonbu: 가격비교 모음(원부) 매칭 시 순위 (None=미발견)
      - rank_solo: 단독 상품 매칭 시 순위 (None=미발견)
    productType (Naver 쇼핑 검색 API):
      "1" 일반상품, "2" 가격비교 매칭 일반상품
      "3" 가격비교 비매칭 일반상품, "4" 단독상품
      가격비교 모음(원부)은 별도 productType 사용 (보통 큰 값)
    """
    try:
        from utils import ProductMatcher
    except ImportError:
        ProductMatcher = None

    _last_match_info[0] = ""
    """
    네이버 쇼핑 검색 API로 키워드 순위 확인.
    open_client_id/secret: developers.naver.com Open API 키 (Commerce API와 다름)
    반환: (rank_price_compare, rank_total, error_msg)
      - rank_price_compare: 가격비교 상품 중 순위 (None=미발견)
      - rank_total: 전체 상품 중 순위 (None=미발견)
    """
    headers = {
        "X-Naver-Client-Id": open_client_id,
        "X-Naver-Client-Secret": open_client_secret,
    }

    import re as _re
    def _clean_trigrams(s):
        s = _re.sub(r'[^\w가-힣]', '', s.lower())
        return set(s[i:i+3] for i in range(len(s)-2)) if len(s) >= 3 else set()

    def _classify(item):
        try:
            hp = int(item.get("hprice") or 0)
        except (TypeError, ValueError):
            hp = 0
        if hp > 0:
            return "원부"
        ptype = str(item.get("productType", ""))
        if ptype == "2":
            return "가격비교"
        return "단독"

    # ── 1단계: 모든 항목을 먼저 수집 (전체 통합 순위 기준) ──
    # pos = API 응답 전체에서 몇 번째인지 (광고 제외, 분류 무관)
    # 실제 네이버 웹 순위와 동일한 기준으로 계산
    collected = []
    overall_pos = 0

    for page in range(max_pages):
        start = page * 100 + 1
        params = {"query": keyword, "display": 100, "start": start, "sort": "sim"}
        try:
            resp = requests.get(
                "https://openapi.naver.com/v1/search/shop.json",
                headers=headers, params=params, timeout=15,
            )
            if resp.status_code == 401:
                return None, None, None, "인증 실패: 네이버 Open API 키를 확인해주세요"
            if resp.status_code != 200:
                err = resp.json().get("errorMessage", resp.text[:200])
                return None, None, None, f"API 오류({resp.status_code}): {err}"

            items = resp.json().get("items", [])
            if not items:
                break

            for item in items:
                overall_pos += 1
                cls = _classify(item)
                collected.append({
                    "cls": cls,
                    "pos": overall_pos,
                    "mall_pid": str(item.get("productId", "")),
                    "title": item.get("title", "").replace("<b>", "").replace("</b>", "").strip(),
                    "mall": item.get("mallName", ""),
                    "ptype": str(item.get("productType", "")),
                    "hp": item.get("hprice") or 0,
                })
        except Exception as e:
            return None, None, None, str(e)

    if not collected:
        return None, None, None, None

    # ── 2단계: 우선순위 매칭 ──
    # 우선순위: 1) PNO_EXACT (productId)  2) STORE+NAME (best sim)  3) NAME_ONLY
    rank_wonbu = rank_compare = rank_solo = None
    debug_lines = []

    def _record_match(it, reason):
        nonlocal rank_wonbu, rank_compare, rank_solo
        debug_lines.append(
            f"{keyword}: [{it['cls']}] pos={it['pos']} ptype={it['ptype']} hp={it['hp']} mall={it['mall']} | {it['title'][:45]} | {reason}"
        )
        if it["cls"] == "원부" and rank_wonbu is None:
            rank_wonbu = it["pos"]
        elif it["cls"] == "가격비교" and rank_compare is None:
            rank_compare = it["pos"]
        elif it["cls"] == "단독" and rank_solo is None:
            rank_solo = it["pos"]

    def _get_sim(t1, t2):
        if ProductMatcher:
            return ProductMatcher.get_score(t1, t2)["total"]
        _a, _b = _clean_trigrams(t1), _clean_trigrams(t2)
        return len(_a & _b) / len(_a | _b) if (_a | _b) else 0.0

    # 우선순위 1: productId 정확 일치 (가장 신뢰도 높음, 사용자가 등록 시)
    if naver_product_no:
        for it in collected:
            if it["mall_pid"] == str(naver_product_no):
                _record_match(it, f"PNO_EXACT(productId={it['mall_pid']})")
                break

    # 우선순위 2: 스토어명 매칭 + 이름 유사도 가장 높은 것 (best sim)
    if store_name and our_product_name and rank_wonbu is None and rank_compare is None and rank_solo is None:
        best_it, best_sim = None, 0.0
        for it in collected:
            if store_name in it["mall"]:
                sim = _get_sim(it["title"], our_product_name)
                if sim > best_sim:
                    best_sim, best_it = sim, it
        if best_it and best_sim >= 0.40:
            _record_match(best_it, f"STORE+NAME(sim={best_sim:.2f}, mall={best_it['mall']}, productId={best_it['mall_pid']})")

    # 우선순위 3: 이름 유사도 (스토어명 없거나 미매칭일 때 fallback)
    if our_product_name and rank_wonbu is None and rank_compare is None and rank_solo is None:
        best_it, best_sim = None, 0.0
        for it in collected:
            sim = _get_sim(it["title"], our_product_name)
            if sim > best_sim:
                best_sim, best_it = sim, it
        
        # 오매칭(타사 상품)을 원천 차단하기 위해 임계값을 0.25에서 0.60으로 대폭 상향
        # 진짜 내 상품이 묶인 카탈로그라면 ProductMatcher 보정 덕에 0.60 이상이 나옴
        if best_it and best_sim >= 0.60:
            _record_match(best_it, f"NAME_BEST(sim={best_sim:.2f}, mall={best_it['mall']})")

    if debug_lines:
        _last_match_info[0] = " || ".join(debug_lines)
    return rank_wonbu, rank_compare, rank_solo, None


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
        # 404 → channelProductNo로 간주, originProductNo 변환 후 재조회
        if g.status_code == 404:
            new_origin, rerr = resolve_origin_product_no(client_id, client_secret, pno)
            if new_origin and new_origin != pno:
                pno = new_origin
                g = _get(pno)
            else:
                return False, f"원상품번호를 찾지 못했습니다(404). {rerr or ''}".strip(), None
        if g.status_code != 200:
            return False, f"상품 조회 실패({g.status_code}: {_format_naver_err(g)})", None

        data = g.json()
        origin_product = data.get('originProduct') or {}
        if not origin_product:
            return False, f"GET 응답에 originProduct 없음: {str(data)[:200]}", None

        # read-only 제거 + unitCapacity / sellerTags 같은 검증 유발 필드 정리
        origin_product = _sanitize_for_put(dict(origin_product))
        origin_product['salePrice'] = int(new_price)

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


# ── 정산 내역 조회 ─────────────────────────────────────────
def get_settlement_history(client_id, client_secret, start_date, end_date):
    """네이버 커머스 정산 API.
    Returns:
        (records, error_msg, used_endpoint_info, attempts_log)
        attempts_log: 모든 시도의 상태/메시지 list — 성공/실패 무관하게 항상 반환
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err, None, []
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = "https://api.commerce.naver.com"
    attempts = []
    success_data = None
    success_info = None
    for method, path, style in _SETTLEMENT_PATH_CANDIDATES:
        url = base + path
        params = _build_params(start_date, end_date, style)
        try:
            resp = _probe_settlement(method, url, headers, params)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    attempts.append(f"❌ {path}?{style}: 200 비-JSON")
                    continue
                items = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = (data.get('elements') or data.get('contents')
                             or data.get('data') or data.get('items')
                             or data.get('list') or [])
                attempts.append(f"✅ {path}?{style}: {len(items)}건")
                # /case 응답을 우선 (productOrderId 있을 가능성), 없으면 /daily 폴백
                if success_data is None or _SETTLE_CASE_PATH in path:
                    success_data = items if isinstance(items, list) else []
                    success_info = f"{path} [{style}] → {len(success_data)}건"
                    # /case 성공이면 즉시 종료 (이게 우리가 원하는 것)
                    if _SETTLE_CASE_PATH in path:
                        return success_data, None, success_info, attempts
                continue
            err_body = _format_naver_err(resp)[:300] if resp.status_code == 400 else ""
            attempts.append(f"❌ {path}?{style}: {resp.status_code}" + (f" — {err_body}" if err_body else ""))
        except Exception as e:
            attempts.append(f"❌ {path}?{style}: EXC {str(e)[:40]}")
    if success_data is not None:
        return success_data, None, success_info, attempts
    return None, "정산 endpoint 모두 실패", None, attempts


# 정산 API — Naver Commerce 공식 path (애플리케이션 권한 그룹: 정산)
# 건별 정산: /external/v1/pay-settle/settle/case
# 일별 정산: /external/v1/pay-settle/settle/daily
# 정확한 파라미터명을 모르므로 여러 스타일 probe
_SETTLE_CASE_PATH  = "/external/v1/pay-settle/settle/case"
_SETTLE_DAILY_PATH = "/external/v1/pay-settle/settle/daily"

_SETTLEMENT_PATH_CANDIDATES = [
    # /case — Naver 400 응답에서 확인된 필수 파라미터: searchDate + periodType
    # periodType 후보 (가장 가능성 높은 순)
    ("GET", _SETTLE_CASE_PATH,  "searchDate_SETTLE_COMPLETE_DATE"),
    ("GET", _SETTLE_CASE_PATH,  "searchDate_SETTLE_COMPLETE"),
    ("GET", _SETTLE_CASE_PATH,  "searchDate_SETTLE_BASIS_DATE"),
    ("GET", _SETTLE_CASE_PATH,  "searchDate_SETTLE_BASIS"),
    ("GET", _SETTLE_CASE_PATH,  "searchDate_PAY_DATE"),
    ("GET", _SETTLE_CASE_PATH,  "searchDate_PAYMENT_DATE"),
    ("GET", _SETTLE_CASE_PATH,  "searchDate_SETTLE_EXPECT_DATE"),
    # /daily (참고 - 이미 동작 확인)
    ("GET", _SETTLE_DAILY_PATH, "dateRange"),
]


def _build_params(date_from, date_to, style):
    # /case 전용: searchDate(단일 날짜) + periodType(enum) + 페이지
    if style.startswith("searchDate_"):
        period_type = style.replace("searchDate_", "")
        return {
            "searchDate": date_from,
            "periodType": period_type,
            "page": 1,
            "size": 1000,
        }
    return {
        "dateRange":   {"startDate":       date_from, "endDate":       date_to},
        "rangeStd":    {"startDate":       date_from, "endDate":       date_to},
        "date":        {"settleDate":      date_from},
    }.get(style, {"startDate": date_from, "endDate": date_to})


def _probe_settlement(method, url, headers, params):
    """단일 settlement endpoint probe — GET은 query, POST는 body로 전송.
    Naver API rate limit 방지를 위해 호출 사이 짧은 대기.
    """
    import time as _time
    _time.sleep(0.25)
    if method == "POST":
        return requests.post(url, headers=headers, json=params, timeout=15)
    return requests.get(url, headers=headers, params=params, timeout=15)


def debug_settlement_response(client_id, client_secret, settle_date):
    """모든 후보(path, param_style)를 probe."""
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = "https://api.commerce.naver.com"
    probes = {}
    for method, path, style in _SETTLEMENT_PATH_CANDIDATES:
        params = _build_params(settle_date, settle_date, style)
        key = f"{method} {path}  [{style}: {','.join(params.keys())}]"
        url = base + path
        try:
            resp = _probe_settlement(method, url, headers, params)
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text[:300]
                probes[key] = {"status": 200, "body": body}
            else:
                probes[key] = {
                    "status": resp.status_code,
                    "msg": _format_naver_err(resp)[:200],
                }
        except Exception as e:
            probes[key] = {"status": "EXC", "msg": str(e)[:200]}
    return probes, None


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


def fetch_order_details_by_ids(client_id, client_secret, order_ids):
    """주문번호 리스트로 직접 상세 조회 → 72컬럼 dict 리스트 반환.
    raw_json 없는 옛 주문의 주소/연락처 등 보완용.
    """
    if not order_ids:
        return [], None
    token, err = get_token(client_id, client_secret)
    if not token:
        return [], err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/query"
    _STATUS_KO = {
        "PAYED": "결제완료", "INSTRUCT": "발주확인",
        "PRODUCT_READY": "발송대기", "DELIVERING": "배송중",
        "DELIVERED": "배송완료", "PURCHASE_DECIDED": "구매확정",
        "CANCELED": "취소완료", "RETURNED": "반품완료",
        "EXCHANGED": "교환완료", "CANCEL_NOPAY": "미결제취소",
    }
    _SUB_STATUS_KO = {
        "INSTRUCT": "발주확인", "CANCEL": "취소", "RETURN": "반품",
        "NOT_YET": "미발주", "OK": "",
    }
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
        "수령위치 유형", "수령위치 내용",
    ]
    orders = []
    for i in range(0, len(order_ids), 300):
        chunk = order_ids[i:i+300]
        try:
            resp = requests.post(url, headers=headers, json={"productOrderIds": chunk}, timeout=15)
            if resp.status_code != 200:
                continue
            for item in resp.json().get("data", []):
                po = item.get("productOrder", {})
                o  = item.get("order", {})
                p_status = po.get("productOrderStatus", "")
                place_status = po.get("placeOrderStatus") or po.get("placeOrderStatusType", "")
                if not p_status:
                    continue
                sa = item.get("shippingAddress", {}) if item.get("shippingAddress") else po.get("shippingAddress", {})
                total_payment = po.get("totalPaymentAmount", po.get("unitPrice", 0) * po.get("quantity", 1))
                # 수수료 → 정산예정금액 정확히 계산 (get_new_orders와 동일 로직)
                _np_commission = int(
                    po.get("payCommissionAmount") or po.get("naverPayCommissionAmount")
                    or po.get("paymentCommissionAmount") or 0
                )
                _sales_commission = int(
                    po.get("salesCommissionAmount") or po.get("naverShoppingCommissionAmount")
                    or po.get("knowledgeShoppingSellingInterlockCommissionAmount")
                    or po.get("linkedShoppingCommissionAmount") or 0
                )
                _expected_settle = po.get("expectedSettlementAmount") or po.get("settleAmount")
                if _expected_settle:
                    expected_settlement = int(_expected_settle)
                elif _np_commission or _sales_commission:
                    expected_settlement = int(total_payment) - _np_commission - _sales_commission
                else:
                    expected_settlement = int(total_payment)
                d = {col: "" for col in naver_columns}
                d.update({
                    "상품주문번호": po.get("productOrderId", ""),
                    "주문번호": o.get("orderId", ""),
                    "배송속성": "당일발송",
                    "배송방법": "택배,등기,소포",
                    "배송방법(구매자 요청)": "택배,등기,소포",
                    "판매채널": "스마트스토어",
                    "구매자명": o.get("ordererName", ""),
                    "구매자ID": o.get("ordererId", ""),
                    "수취인명": sa.get("name", ""),
                    "주문상태": _STATUS_KO.get(p_status, p_status),
                    "주문세부상태": _SUB_STATUS_KO.get(place_status, place_status) if place_status else "",
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
                    "기본배송지": sa.get("baseAddress", ""),
                    "상세배송지": sa.get("detailedAddress", ""),
                    "우편번호": sa.get("zipCode", ""),
                    "배송메세지": po.get("shippingMemo", ""),
                    "주문일시": str(o.get("orderDate", "")).replace("T", " ")[:19] if o.get("orderDate") else "",
                    "개인통관고유부호": o.get("individualCustomsExtractionNumber", ""),
                    "네이버페이 주문관리 수수료": -_np_commission if _np_commission else 0,
                    "매출연동 수수료":           -_sales_commission if _sales_commission else 0,
                    "정산예정금액":              expected_settlement,
                })
                orders.append(d)
        except Exception:
            pass
    return orders, None