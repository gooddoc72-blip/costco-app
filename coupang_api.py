"""쿠팡 Wing Open API 클라이언트.

인증: HMAC-SHA256 (CEA 방식)
주요 기능:
  - get_orders()       : 주문 목록 조회 (날짜 범위 + 상태 필터)
  - get_order_detail() : 개별 주문 상세
"""
import hmac
import hashlib
import time
import json
import requests
from datetime import datetime, timezone, timedelta


BASE_URL = "https://api-gateway.coupang.com"


# ── 인증 헬퍼 ──────────────────────────────────────────────────────────────

def _utc_datetime_str():
    """쿠팡 서명용 UTC datetime 문자열: YYMMDD'T'HHmmss'Z'"""
    now = datetime.now(timezone.utc)
    return now.strftime("%y%m%dT%H%M%SZ")


def _make_signature(secret_key: str, method: str, path: str,
                    query: str, dt_str: str) -> str:
    """HMAC-SHA256 서명 생성. (Coupang Wing: dt+method+path+query 순 단순 연결)"""
    message = dt_str + method + path + query
    return hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _auth_header(access_key: str, secret_key: str,
                 method: str, path: str, query: str = "") -> dict:
    """Authorization 헤더 딕셔너리 반환."""
    dt_str = _utc_datetime_str()
    sig = _make_signature(secret_key, method, path, query, dt_str)
    return {
        "Authorization": (
            f"CEA algorithm=HmacSHA256, access-key={access_key}, "
            f"signed-date={dt_str}, signature={sig}"
        ),
        "Content-Type": "application/json;charset=UTF-8",
        "X-MARKET": "KR",   # 일부 계정은 필수(없으면 400 BAD_REQUEST) — 한국 마켓 고정
    }


# ── 주문 조회 ──────────────────────────────────────────────────────────────

def get_orders(access_key: str, secret_key: str, vendor_id: str,
               status: str = "ACCEPT",
               days_back: int = 2,
               date_from: str = None,
               date_to: str = None):
    """쿠팡 주문 목록 조회.

    Args:
        status: ACCEPT(결제완료) | INSTRUCT(발주확인) | DEPARTURE(출고완료)
                DELIVERING(배송중) | FINAL_DELIVERY(배송완료)
                ALL → ACCEPT + INSTRUCT 두 번 호출
        days_back: date_from/to 미지정 시 최근 N일
        date_from / date_to: "yyyy-MM-dd" 형식 (우선 사용)

    Returns:
        (rows, error_msg)
        rows: [{'상품주문번호', '수취인명', '상품명', '옵션정보',
                '수량', '최종 상품별 총 주문금액', '배송비 합계',
                '제주/도서 추가배송비', '정산예정금액', '주문상태'}, ...]
    """
    if not (access_key and secret_key and vendor_id):
        return [], "쿠팡 API 키(Access Key / Secret Key / Vendor ID)를 설정에서 먼저 입력해주세요."

    today = datetime.now().strftime("%Y-%m-%d")
    d_to = date_to or today

    if date_from:
        # 수동 조회: 사용자 지정 날짜 기준, ACCEPT/INSTRUCT/DEPARTURE는 30일 연장 적용
        d_from_normal = date_from
        d_from_long   = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    else:
        # 자동 조회: 전체 상태 30일 기준
        d_from_normal = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        d_from_long   = (datetime.now() - timedelta(days=max(days_back, 30))).strftime("%Y-%m-%d")

    if status == "ALL":
        statuses = ["ACCEPT", "INSTRUCT", "DEPARTURE"]
    else:
        statuses = [status]

    all_pairs  = []   # [(unified_row, excel_row), ...]
    all_errors = []
    per_status = {}  # 진단 정보: {status: {"count", "error", "from", "to", "debug"}}
    for st in statuses:
        _d_from = d_from_long if st in ("ACCEPT", "INSTRUCT", "DEPARTURE") else d_from_normal
        rows, coupang_rows, err, debug_info = _fetch_orders_for_status(
            access_key, secret_key, vendor_id, st, _d_from, d_to
        )
        per_status[st] = {"count": len(rows), "error": err,
                          "from": _d_from, "to": d_to, "debug": debug_info}
        if err:
            all_errors.append(f"{st}: {err}")
        else:
            all_pairs.extend(zip(rows, coupang_rows))

    if not all_pairs and all_errors:
        return [], " | ".join(all_errors), per_status, []

    # 중복 제거 (상품주문번호 기준) — unified/excel 동시 처리
    seen = set()
    deduped = []
    deduped_coupang = []
    for u, c in all_pairs:
        key = u["상품주문번호"]
        if key not in seen:
            seen.add(key)
            deduped.append(u)
            deduped_coupang.append(c)

    # 번호 재정렬 (중복 제거 후 연속 번호 보장)
    for i, c in enumerate(deduped_coupang):
        c["번호"] = i + 1

    return deduped, None, per_status, deduped_coupang


def _fetch_orders_for_status(access_key, secret_key, vendor_id,
                              status, d_from, d_to):
    """단일 상태 주문 목록 페이지네이션 조회.
    Returns: (unified_rows, coupang_excel_rows, error_or_None, first_page_debug_or_None)
    """
    path = f"/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/ordersheets"
    rows = []
    coupang_rows = []
    next_token = None
    _first_debug = None

    while True:
        params = {
            "createdAtFrom": d_from,
            "createdAtTo":   d_to,
            "status":        status,
            "maxPerPage":    50,
        }
        if next_token:
            params["nextToken"] = next_token

        # PreparedRequest로 실제 전송 URL을 먼저 확정하고, 그 query로 서명
        from urllib.parse import urlparse
        _prep = requests.Request("GET", BASE_URL + path, params=params).prepare()
        _parsed = urlparse(_prep.url)
        query = _parsed.query  # requests가 정규화한 실제 query string
        headers = _auth_header(access_key, secret_key, "GET", path, query)

        try:
            resp = requests.get(_prep.url, headers=headers, timeout=30)
        except requests.exceptions.RequestException as e:
            return [], f"네트워크 오류: {e}", _first_debug

        if resp.status_code != 200:
            try:
                body = resp.json()
                msg = body.get("message") or body.get("errorMessage") or resp.text[:400]
            except Exception:
                msg = resp.text[:400]
            return [], [], f"API 오류 [{resp.status_code}]: {msg}", _first_debug

        body = resp.json()

        # 첫 응답 진단 정보 수집 (디버그용)
        if _first_debug is None:
            _data_sample = body.get("data")
            _first_order = (_data_sample[0] if isinstance(_data_sample, list) and _data_sample else {})
            _first_debug = {
                "code": body.get("code"),
                "message": body.get("message"),
                "data_type": type(_data_sample).__name__,
                "data_count": len(_data_sample) if isinstance(_data_sample, list) else "not-list",
                "first_order_keys": list(_first_order.keys()) if _first_order else [],
                "items_field": ("items" if "items" in _first_order
                                else "orderItems" if "orderItems" in _first_order
                                else "없음"),
                "raw_snippet": str(body)[:600],
            }

        if str(body.get("code", "")) != "200":
            return [], [], f"쿠팡 응답 오류: {body.get('message', body)}", _first_debug

        data = body.get("data") or []
        for order in data:
            seq = len(rows) + 1
            unified, excel = _parse_order_and_excel(order, seq)
            rows.extend(unified)
            coupang_rows.extend(excel)

        next_token = body.get("nextToken")
        if not next_token or not data:
            break

    return rows, coupang_rows, None, _first_debug


# 쿠팡 배송준비 리스트 엑셀 컬럼 순서 (Wing 어드민 다운로드 형식과 동일)
_COUPANG_EXCEL_COLS = [
    "번호", "묶음배송번호", "주문번호", "택배사", "운송장번호",
    "분리배송 Y/N", "분리배송 출고예정일", "주문시 출고예정일", "출고일(발송일)", "주문일",
    "등록상품명", "등록옵션명", "노출상품명(옵션명)", "노출상품ID", "옵션ID",
    "최초등록등록상품명/옵션명", "업체상품코드", "바코드",
    "결제액", "배송비구분", "배송비", "도서산간 추가배송비", "구매수(수량)", " ",
    "구매자", "구매자전화번호", "수취인이름", "수취인전화번호", "우편번호", "수취인 주소",
    "배송메세지", "상품별 추가메시지", "주문자 추가메시지", "배송완료일", "구매확정일자",
    "개인통관번호(PCCC)", "통관용수취인전화번호", "기타", "결제위치", "배송유형",
]


def _parse_order_and_excel(order: dict, seq_no: int) -> tuple:
    """주문 1건 → (unified_rows, coupang_excel_rows) 병렬 반환."""
    receiver  = order.get("receiver") or {}
    orderer   = order.get("orderer") or {}
    recv_name = receiver.get("name") or orderer.get("name") or "-"
    status    = order.get("status", "")
    order_ship = int(order.get("shippingPrice") or 0)

    items = (order.get("items")
             or order.get("orderItems")
             or order.get("orderItem")
             or [])

    # 주소 조합
    addr1     = receiver.get("addr1") or receiver.get("addr", "") or ""
    addr2     = receiver.get("addr2") or ""
    full_addr = (addr1 + (" " + addr2 if addr2 else "")).strip()

    # 전화번호 (안전번호 우선)
    recv_phone  = receiver.get("safeNumber") or receiver.get("phoneNumber") or ""
    buyer_phone = orderer.get("safeNumber") or orderer.get("phoneNumber") or ""

    # 주문일 포맷 (ISO → 공백 구분)
    ordered_at = order.get("orderedAt") or order.get("createdAt") or ""
    if ordered_at and "T" in str(ordered_at):
        ordered_at = str(ordered_at).replace("T", " ").rstrip("Z").strip()

    bundle_id = (order.get("bundleShippingOrderId")
                 or order.get("shippingOrderId")
                 or order.get("orderId")
                 or "")

    unified_rows = []
    excel_rows   = []

    for idx, item in enumerate(items):
        _cancel = str(item.get("cancelType") or "").strip().upper()
        if _cancel and _cancel != "NONE":
            continue

        qty        = int(item.get("quantity") or 1)
        unit_price = int(item.get("orderPrice") or 0)
        settlement = int(item.get("shippingCountPriceWithCommission") or 0)
        ship_fee   = order_ship if idx == 0 else 0

        item_name   = item.get("vendorItemName") or ""
        option_name = item.get("vendorOptionName") or item.get("optionName") or ""
        exposed_nm  = f"{item_name} ({option_name})" if option_name else item_name

        # 쿠팡 아이템 식별자: orderItemId가 없으면(대부분 None) vendorItemId 사용.
        #   발송처리(dispatch_orders) 상품주문번호 형식 orderId-itemId 에 쓰임. None 방지.
        _item_id = (str(item.get("orderItemId") or "").strip()
                    or str(item.get("vendorItemId") or "").strip())
        unified_rows.append({
            "상품주문번호": f"{order.get('orderId')}-{_item_id}",
            "수취인명":     recv_name,
            "상품명":       item_name,
            "옵션정보":     item.get("externalVendorSkuCode") or "",
            "수량":         qty,
            "상품가격":     unit_price,   # 판매단가(orderPrice) — 네이버와 동일하게 수집
            "최종 상품별 총 주문금액": unit_price * qty,
            "배송비 합계":  ship_fee,
            "제주/도서 추가배송비": 0,
            "정산예정금액": settlement,
            "주문상태":     status,
            "플랫폼":       "쿠팡",
        })
        excel_rows.append({
            "번호":                    seq_no + len(excel_rows),
            "묶음배송번호":             bundle_id,
            "주문번호":                 order.get("orderId") or "",
            "택배사":                   "",
            "운송장번호":               "",
            "분리배송 Y/N":             "분리배송불가",
            "분리배송 출고예정일":       "",
            "주문시 출고예정일":         order.get("shippingDueDate") or "",
            "출고일(발송일)":           "",
            "주문일":                   ordered_at,
            "등록상품명":               item_name,
            "등록옵션명":               option_name,
            "노출상품명(옵션명)":        exposed_nm,
            "노출상품ID":               item.get("productId") or item.get("vendorId") or "",
            "옵션ID":                   item.get("vendorItemId") or item.get("orderItemId") or "",
            "최초등록등록상품명/옵션명": f"{item_name},{option_name}",
            "업체상품코드":              item.get("externalVendorSkuCode") or "",
            "바코드":                   item.get("barcode") or "",
            "결제액":                   unit_price * qty,
            "배송비구분":               "무료" if ship_fee == 0 else "유료",
            "배송비":                   ship_fee,
            "도서산간 추가배송비":       0,
            "구매수(수량)":             qty,
            " ":                       "",
            "구매자":                   orderer.get("name") or "",
            "구매자전화번호":           buyer_phone,
            "수취인이름":               recv_name,
            "수취인전화번호":           recv_phone,
            "우편번호":                 receiver.get("postCode") or receiver.get("zipCode") or "",
            "수취인 주소":              full_addr,
            "배송메세지":               receiver.get("message") or "",
            "상품별 추가메시지":         item.get("additionalMessage") or "",
            "주문자 추가메시지":         order.get("ordererMessage") or "",
            "배송완료일":               "",
            "구매확정일자":             "",
            "개인통관번호(PCCC)":       item.get("personalCustomsClearanceCode") or "",
            "통관용수취인전화번호":      "",
            "기타":                     "",
            "결제위치":                 order.get("deviceType") or order.get("ordererDeviceName") or "",
            "배송유형":                 "판매자 배송",
        })

    return unified_rows, excel_rows


def _parse_order(order: dict) -> list:
    """하위호환용 래퍼."""
    unified, _ = _parse_order_and_excel(order, 1)
    return unified


# ── 일괄 발송처리 ──────────────────────────────────────────────────────────

def dispatch_orders(access_key: str, secret_key: str, vendor_id: str, ship_data: list):
    """쿠팡 Wing 일괄 발송처리.

    ship_data 항목 형식:
        {"productOrderId": "orderId-orderItemId", "courierCode": "CJ대한통운", "trackingNumber": "1234567890"}

    productOrderId는 {orderId}-{orderItemId} 형식으로, 쿠팡 주문 조회 시 생성되는
    상품주문번호를 그대로 사용하면 됩니다.

    Returns:
        ({"success": N, "fail": N, "fail_details": [...], "sent_count": N}, error_msg)
    """
    COURIER_MAP = {
        "CJ대한통운": "CJ_LOGISTICS", "CJGLS": "CJ_LOGISTICS", "CJ": "CJ_LOGISTICS",
        "한진택배":   "HANJIN",       "HANJIN": "HANJIN",
        "롯데택배":   "LOTTE",        "HYUNDAI": "LOTTE",   # 네이버 코드 HYUNDAI → 쿠팡 LOTTE
        "우체국택배": "EPOST",        "EPOST": "EPOST",
        "로젠택배":   "LOGEN",        "KGB": "LOGEN",
        "경동택배":   "KDEXP",        "KDEXP": "KDEXP",
        "대신택배":   "DAESIN",       "DAESIN": "DAESIN",
    }

    total_success = 0
    total_fail = 0
    fail_details = []
    success_order_ids = []

    for item in ship_data:
        poid = str(item.get("productOrderId", "")).strip()
        tracking = str(item.get("trackingNumber", "")).replace("-", "").strip()

        # {orderId}-{orderItemId} 파싱
        if "-" in poid:
            parts = poid.split("-", 1)
            order_id = parts[0]
            item_id  = parts[1]
        else:
            # 하이픈 없으면 orderId만 있는 경우 — API 호출 불가, skip
            total_fail += 1
            fail_details.append(f"[건너뜀] {poid}: orderItemId를 파싱할 수 없습니다. 상품주문번호 형식(orderId-orderItemId)을 확인하세요.")
            continue

        input_courier = str(item.get("courierCode", "")).strip()
        courier_code = COURIER_MAP.get(input_courier, input_courier)

        path = (
            f"/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}"
            f"/orders/{order_id}/items/{item_id}/invoices/{courier_code}"
        )
        headers = _auth_header(access_key, secret_key, "PUT", path, "")

        try:
            resp = requests.put(
                BASE_URL + path,
                headers=headers,
                json={"invoiceNumber": tracking},
                timeout=15,
            )
            try:
                body = resp.json()
            except Exception:
                body = {}

            code = str(body.get("code", ""))
            if resp.status_code == 200 and code == "200":
                total_success += 1
                success_order_ids.append(poid)
            else:
                msg = body.get("message") or resp.text[:200]
                total_fail += 1
                fail_details.append(f"[실패] {poid}: [{resp.status_code}] {msg}")
        except Exception as e:
            total_fail += 1
            fail_details.append(f"[오류] {poid}: {str(e)}")

    return {
        "success": total_success,
        "fail": total_fail,
        "fail_details": fail_details,
        "sent_count": len(ship_data),
        "success_order_ids": success_order_ids,
    }, None


# ── 정산(매출내역) 조회 ────────────────────────────────────────────────────

def get_revenue_history(access_key: str, secret_key: str, vendor_id: str,
                        date_from: str, date_to: str):
    """쿠팡 매출/정산 내역 조회. 쿠팡 API는 '기간 1개월 미만'만 허용하므로
    범위를 27일 단위로 자동 분할해 조회·병합한다. Returns: (records, error).
    """
    from datetime import datetime as _dt, timedelta as _td
    try:
        _d0 = _dt.strptime(date_from, "%Y-%m-%d").date()
        _d1 = _dt.strptime(date_to, "%Y-%m-%d").date()
    except Exception:
        _d0 = _d1 = None
    if not _d0 or not _d1 or _d0 > _d1:
        return _fetch_revenue_chunk(access_key, secret_key, vendor_id, date_from, date_to)
    out, _cur = [], _d0
    while _cur <= _d1:
        _end = min(_cur + _td(days=26), _d1)  # 27일 구간(1개월 미만)
        recs, err = _fetch_revenue_chunk(
            access_key, secret_key, vendor_id,
            _cur.strftime("%Y-%m-%d"), _end.strftime("%Y-%m-%d"))
        if err:
            return out, err
        out.extend(recs)
        _cur = _end + _td(days=1)
    return out, None


def _fetch_revenue_chunk(access_key: str, secret_key: str, vendor_id: str,
                         date_from: str, date_to: str):
    """revenue-history 단일 구간(≤1개월) 페이지네이션 조회 — 매출인식일 기준.
    record(item 단위): order_id, vendor_item_id, product_id, product_name, sale_date,
      recognition_date, settlement_date, sale_amount, service_fee(수수료+VAT),
      settlement_amount, delivery_settlement, quantity.
    """
    from urllib.parse import urlparse
    path = "/v2/providers/openapi/apis/api/v1/revenue-history"
    out = []
    token = ""
    for _ in range(200):  # 페이지 한도(안전장치)
        params = {"vendorId": vendor_id, "recognitionDateFrom": date_from,
                  "recognitionDateTo": date_to, "maxPerPage": 50, "token": token}
        prep = requests.Request("GET", BASE_URL + path, params=params).prepare()
        query = urlparse(prep.url).query
        headers = _auth_header(access_key, secret_key, "GET", path, query)
        try:
            resp = requests.get(prep.url, headers=headers, timeout=30)
        except requests.exceptions.RequestException as e:
            return out, f"네트워크 오류: {e}"
        if resp.status_code != 200:
            return out, f"{resp.status_code}: {resp.text[:200]}"
        j = resp.json()
        data = j.get("data") or []
        for od in data:
            oid = str(od.get("orderId") or "")
            dfee = od.get("deliveryFee") or {}
            dfee_settle = int(dfee.get("settlementAmount") or 0)
            for it in (od.get("items") or []):
                out.append({
                    "order_id":           oid,
                    "vendor_item_id":     str(it.get("vendorItemId") or ""),
                    "product_id":         str(it.get("productId") or ""),
                    "product_name":       it.get("productName") or it.get("vendorItemName") or "",
                    "sale_date":          od.get("saleDate") or "",
                    "recognition_date":   od.get("recognitionDate") or "",
                    "settlement_date":    od.get("settlementDate") or "",
                    "final_settlement_date": od.get("finalSettlementDate") or "",
                    "sale_amount":        int(it.get("saleAmount") or 0),
                    "service_fee":        int(it.get("serviceFee") or 0) + int(it.get("serviceFeeVat") or 0),
                    "settlement_amount":  int(it.get("settlementAmount") or 0),
                    "delivery_settlement": dfee_settle,  # 주문당 1회만(아래에서 0 처리)
                    "quantity":           int(it.get("quantity") or 1),
                })
                dfee_settle = 0  # 배송비 정산은 주문당 1회만 반영
        token = j.get("nextToken") or ""
        if not j.get("hasNext") or not token:
            break
    return out, None


# ── 단순 API 연결 테스트 ───────────────────────────────────────────────────

def test_connection(access_key: str, secret_key: str, vendor_id: str):
    """API 키 유효성 확인. (오늘 날짜 주문 1건 조회 시도)

    Returns:
        (ok: bool, message: str)
    """
    today = datetime.now().strftime("%Y-%m-%d")
    rows, _, err, _ = _fetch_orders_for_status(
        access_key, secret_key, vendor_id, "ACCEPT", today, today
    )
    if err:
        return False, err
    return True, f"연결 성공 (오늘 ACCEPT 주문 {len(rows)}건 조회됨)"
