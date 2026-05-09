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
    d_from = date_from or (
        datetime.now() - timedelta(days=days_back)
    ).strftime("%Y-%m-%d")
    d_to = date_to or today

    if status == "ALL":
        statuses = ["ACCEPT", "INSTRUCT"]
    else:
        statuses = [status]

    all_rows = []
    for st in statuses:
        rows, err = _fetch_orders_for_status(
            access_key, secret_key, vendor_id, st, d_from, d_to
        )
        if err:
            return [], err
        all_rows.extend(rows)

    # 중복 제거 (상품주문번호 기준)
    seen = set()
    deduped = []
    for r in all_rows:
        key = r["상품주문번호"]
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped, None


def _fetch_orders_for_status(access_key, secret_key, vendor_id,
                              status, d_from, d_to):
    """단일 상태 주문 목록 페이지네이션 조회."""
    path = f"/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/ordersheets"
    rows = []
    next_token = None

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
            return [], f"네트워크 오류: {e}"

        if resp.status_code != 200:
            try:
                body = resp.json()
                msg = body.get("message") or body.get("errorMessage") or resp.text[:400]
            except Exception:
                msg = resp.text[:400]
            return [], f"API 오류 [{resp.status_code}]: {msg}"

        body = resp.json()
        if str(body.get("code", "")) != "200":
            return [], f"쿠팡 응답 오류: {body.get('message', body)}"

        data = body.get("data") or []
        for order in data:
            parsed = _parse_order(order)
            rows.extend(parsed)

        next_token = body.get("nextToken")
        if not next_token or not data:
            break

    return rows, None


def _parse_order(order: dict) -> list:
    """주문 1건 → 아이템별 행 리스트 변환 (기존 네이버 컬럼 구조 호환)."""
    receiver  = order.get("receiver") or {}
    recv_name = receiver.get("name") or order.get("orderer", {}).get("name", "-")
    status    = order.get("status", "")

    # 주문 레벨 배송비 (아이템이 여러 개면 첫 아이템에만 부과, 나머지 0)
    order_ship = int(order.get("shippingPrice") or 0)
    items = order.get("items") or []

    rows = []
    for idx, item in enumerate(items):
        # 취소 아이템 제외
        if item.get("cancelType"):
            continue

        qty        = int(item.get("quantity") or 1)
        unit_price = int(item.get("orderPrice") or 0)
        settlement = int(item.get("shippingCountPriceWithCommission") or 0)
        ship_fee   = order_ship if idx == 0 else 0

        rows.append({
            "상품주문번호": f"{order.get('orderId')}-{item.get('orderItemId')}",
            "수취인명":     recv_name,
            "상품명":       item.get("vendorItemName") or "",
            "옵션정보":     item.get("externalVendorSkuCode") or "",
            "수량":         qty,
            "최종 상품별 총 주문금액": unit_price * qty,
            "배송비 합계":  ship_fee,
            "제주/도서 추가배송비": 0,
            "정산예정금액": settlement,
            "주문상태":     status,
            "플랫폼":       "쿠팡",
        })
    return rows


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
    }, None


# ── 단순 API 연결 테스트 ───────────────────────────────────────────────────

def test_connection(access_key: str, secret_key: str, vendor_id: str):
    """API 키 유효성 확인. (오늘 날짜 주문 1건 조회 시도)

    Returns:
        (ok: bool, message: str)
    """
    today = datetime.now().strftime("%Y-%m-%d")
    rows, err = _fetch_orders_for_status(
        access_key, secret_key, vendor_id, "ACCEPT", today, today
    )
    if err:
        return False, err
    return True, f"연결 성공 (오늘 ACCEPT 주문 {len(rows)}건 조회됨)"
