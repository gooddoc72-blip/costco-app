"""네이버 API — 주문 수집·발송 처리·정산 조회·CJ 운송장"""
import time, json, requests, bcrypt, pybase64, math
from datetime import datetime, timedelta, timezone
from .core import get_token
from .products import _format_naver_err

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
                        "옵션번호": str(po.get("optionCode", "") or po.get("itemNo", "") or ""),
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



def get_daily_settlement(client_id, client_secret, date):
    """일별 정산 합계 (/daily) — 네이버 정산내역 화면과 동일한 순액·구성.
    Returns: (dict|None, error). dict 주요 키:
      settleAmount(정산금액=입금), paySettleAmount(정산기준금액),
      commissionSettleAmount(수수료합계), benefitSettleAmount(혜택정산),
      normalSettleAmount(일반정산), quickSettleAmount(빠른정산),
      settleExpectDate, settleCompleteDate, depositorName, bankType, accountNo
    """
    token, err = get_token(client_id, client_secret)
    if not token:
        return None, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.commerce.naver.com" + _SETTLE_DAILY_PATH
    try:
        resp = requests.get(url, headers=headers,
                            params={"startDate": date, "endDate": date}, timeout=15)
        if resp.status_code != 200:
            return None, _format_naver_err(resp)
        data = resp.json()
        items = (data.get('elements') or data.get('contents') or data.get('data')
                 if isinstance(data, dict) else data) or []
        if not items:
            return None, None  # 해당일 정산 없음(오류 아님)
        return items[0], None
    except Exception as e:
        return None, str(e)[:120]



def get_purchase_decisions(client_id, client_secret, product_order_ids):
    """상품주문번호들의 구매확정 정보 조회 (자동/수동 추정용).
    Returns: {product_order_id: {'decision_date': 'YYYY-MM-DDTHH:..', 'status': str}}
    """
    out = {}
    ids = [str(p).strip() for p in (product_order_ids or []) if str(p).strip()]
    if not ids:
        return out
    token, err = get_token(client_id, client_secret)
    if not token:
        return out
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = ("https://api.commerce.naver.com/external/v1/"
           "pay-order/seller/product-orders/query")
    for i in range(0, len(ids), 300):
        chunk = ids[i:i + 300]
        try:
            resp = requests.post(url, headers=headers,
                                json={"productOrderIds": chunk}, timeout=30)
            if resp.status_code != 200:
                continue
            data = resp.json()
            items = data.get('data') if isinstance(data, dict) else data
            for it in (items or []):
                po = it.get('productOrder', {}) if isinstance(it, dict) else {}
                pid = str(po.get('productOrderId', '') or '')
                if pid:
                    out[pid] = {
                        'decision_date': po.get('decisionDate', '') or '',
                        'status':        po.get('productOrderStatus', '') or '',
                    }
        except Exception:
            continue
    return out



def get_daily_settlements_range(client_id, client_secret, start_date, end_date):
    """기간 일별 정산(입금) 합계 — {settleExpectDate(입금일): settleAmount(입금액)}.
    달력에 '그날 실제 입금된 정산금'을 표시하기 위함. Returns (dict, error)."""
    token, err = get_token(client_id, client_secret)
    if not token:
        return {}, err
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.commerce.naver.com" + _SETTLE_DAILY_PATH
    try:
        resp = requests.get(url, headers=headers,
                            params={"startDate": start_date, "endDate": end_date}, timeout=20)
        if resp.status_code != 200:
            return {}, _format_naver_err(resp)
        data = resp.json()
        items = (data.get('elements') or data.get('contents') or data.get('data')
                 if isinstance(data, dict) else data) or []
        out = {}
        for it in items:
            d = str(it.get('settleExpectDate') or it.get('settleCompleteDate') or '')[:10]
            if d:
                # 같은 날 레코드가 여러 개(일반+빠른정산 등)면 합산 (덮어쓰기 금지)
                out[d] = out.get(d, 0) + int(it.get('settleAmount') or 0)
        return out, None
    except Exception as e:
        return {}, str(e)[:120]


# 정산 API — Naver Commerce 공식 path (애플리케이션 권한 그룹: 정산)
# 건별 정산: /external/v1/pay-settle/settle/case
# 일별 정산: /external/v1/pay-settle/settle/daily
# 정확한 파라미터명을 모르므로 여러 스타일 probe

_SETTLE_CASE_PATH  = "/external/v1/pay-settle/settle/case"

_SETTLE_DAILY_PATH = "/external/v1/pay-settle/settle/daily"


_SETTLEMENT_PATH_CANDIDATES = [
    # /case — 건별 정산 (상품주문번호별). 확인된 정답 periodType: SETTLE_CASEBYCASE_*
    # 정산예정일 기준이 권장(가장 빨리 조회됨), 그다음 완료일/기준일 폴백
    ("GET", _SETTLE_CASE_PATH,  "searchDate_SETTLE_CASEBYCASE_SETTLE_SCHEDULE_DATE"),
    ("GET", _SETTLE_CASE_PATH,  "searchDate_SETTLE_CASEBYCASE_SETTLE_COMPLETE_DATE"),
    ("GET", _SETTLE_CASE_PATH,  "searchDate_SETTLE_CASEBYCASE_SETTLE_BASIS_DATE"),
    # /daily (합계 폴백 - 상품주문번호 없음)
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
                # 발송(배송) 정보 — item['delivery']에 발송일(sendDate)·송장·택배사가 있음
                _dv = item.get("delivery") or {}
                _send = str(_dv.get("sendDate", "") or "")
                if _dv.get("trackingNumber"):
                    d["송장번호"] = str(_dv.get("trackingNumber") or "")
                if _dv.get("deliveryCompany"):
                    d["택배사"] = str(_dv.get("deliveryCompany") or "")
                if _send:
                    d["발송일"] = _send.replace("T", " ")[:10]
                    d["발송처리일"] = _send.replace("T", " ")[:19]
                orders.append(d)
        except Exception:
            pass
    return orders, None

# ── 네이버 검색광고 API — 키워드 도구(월간 검색량·연관검색어) ──────────────
