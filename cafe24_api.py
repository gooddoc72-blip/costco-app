"""카페24 Admin API 클라이언트 — OAuth2 인증 + 주문 조회 + 상품 가격 수정.

카페24는 OAuth 2.0 authorization code 방식 (네이버 커머스의 self-auth와 다름):
  1) 인증 URL로 이동 → 쇼핑몰 운영자 동의 → redirect_uri로 code 발급
  2) code → access_token(2h) + refresh_token(2주) 교환
  3) 토큰 만료 시 refresh_token으로 자동 갱신

토큰/키는 사용자 설정(settings)에 저장:
  cafe24_mall_id, cafe24_client_id, cafe24_client_secret,
  cafe24_access_token, cafe24_refresh_token, cafe24_token_expires_at
"""
import base64
import time
from datetime import datetime, timedelta, timezone

import requests

API_VERSION = "2024-06-01"
REDIRECT_URI = "https://cocobiz.shop/"
# 주문 읽기 + 상품 읽기/쓰기(가격수정) + 앱 읽기
SCOPES = "mall.read_order,mall.read_product,mall.write_product,mall.read_application"


def _base(mall_id):
    return f"https://{str(mall_id).strip()}.cafe24api.com"


def get_authorize_url(mall_id, client_id, state, redirect_uri=REDIRECT_URI):
    """운영자 동의를 받을 인증 URL. state에 sid를 실어 로그인 세션 복원에 사용."""
    from urllib.parse import urlencode
    q = urlencode({
        "response_type": "code",
        "client_id": str(client_id).strip(),
        "state": str(state),
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
    })
    return f"{_base(mall_id)}/api/v2/oauth/authorize?{q}"


def _token_request(mall_id, client_id, client_secret, data):
    """OAuth 토큰 엔드포인트 호출 (authorization_code / refresh_token 공용)."""
    _basic = base64.b64encode(
        f"{str(client_id).strip()}:{str(client_secret).strip()}".encode()
    ).decode()
    try:
        r = requests.post(
            f"{_base(mall_id)}/api/v2/oauth/token",
            headers={
                "Authorization": f"Basic {_basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data, timeout=20,
        )
        if r.status_code != 200:
            try:
                _e = r.json()
                _m = _e.get("error_description") or _e.get("error") or r.text[:300]
            except Exception:
                _m = r.text[:300]
            return None, f"[{r.status_code}] {_m}"
        j = r.json()
        return {
            "access_token": j.get("access_token", ""),
            "refresh_token": j.get("refresh_token", ""),
            "expires_at": j.get("expires_at", ""),   # ISO 문자열 (KST)
        }, None
    except Exception as e:
        return None, str(e)


def exchange_code_for_token(mall_id, client_id, client_secret, code,
                            redirect_uri=REDIRECT_URI):
    """인증 code → access/refresh 토큰 교환. 반환: (token_dict, err)."""
    return _token_request(mall_id, client_id, client_secret, {
        "grant_type": "authorization_code",
        "code": str(code).strip(),
        "redirect_uri": redirect_uri,
    })


def refresh_access_token(mall_id, client_id, client_secret, refresh_token):
    """refresh_token으로 access_token 갱신. 반환: (token_dict, err)."""
    return _token_request(mall_id, client_id, client_secret, {
        "grant_type": "refresh_token",
        "refresh_token": str(refresh_token).strip(),
    })


def _is_expired(expires_at):
    """expires_at(ISO/KST) 이 현재보다 과거면 True. 파싱 실패 시 만료로 간주."""
    if not expires_at:
        return True
    try:
        s = str(expires_at).replace("Z", "").split(".")[0]
        # 'YYYY-MM-DDTHH:MM:SS' (+타임존 있을 수 있음) — 타임존 제거 후 KST 기준 비교
        s = s.split("+")[0].strip()
        dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        now_kst = datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)
        return dt <= now_kst + timedelta(minutes=1)   # 1분 여유
    except Exception:
        return True


def get_valid_token(creds: dict, save_tokens=None):
    """유효한 access_token 확보 (만료 시 refresh 후 save_tokens 콜백으로 저장).
    creds: {mall_id, client_id, client_secret, access_token, refresh_token, expires_at}
    save_tokens: 갱신된 {access_token,refresh_token,expires_at} 저장 콜백 (선택).
    반환: (access_token, err)
    """
    at = creds.get("access_token", "")
    if at and not _is_expired(creds.get("expires_at", "")):
        return at, None
    # 갱신 필요
    rt = creds.get("refresh_token", "")
    if not rt:
        return None, "refresh_token 없음 — 설정 탭에서 '카페24 인증'을 다시 하세요."
    tok, err = refresh_access_token(creds.get("mall_id"), creds.get("client_id"),
                                    creds.get("client_secret"), rt)
    if err or not tok:
        return None, f"토큰 갱신 실패: {err} (재인증 필요할 수 있음)"
    if save_tokens:
        try:
            save_tokens(tok)
        except Exception:
            pass
    return tok.get("access_token"), None


def _admin_request(creds, method, path, save_tokens=None, params=None, json_body=None):
    """Admin API 공통 호출 (토큰 자동확보 + 401 시 1회 재시도). 반환: (json, err)."""
    token, err = get_valid_token(creds, save_tokens)
    if err:
        return None, err
    url = f"{_base(creds.get('mall_id'))}{path}"

    def _do(tok):
        return requests.request(
            method, url,
            headers={"Authorization": f"Bearer {tok}",
                     "Content-Type": "application/json",
                     "X-Cafe24-Api-Version": API_VERSION},
            params=params, json=json_body, timeout=30,
        )

    try:
        r = _do(token)
        if r.status_code == 401:  # 토큰 만료로 판단 → 강제 refresh 후 1회 재시도
            tok2, e2 = refresh_access_token(creds.get("mall_id"), creds.get("client_id"),
                                            creds.get("client_secret"),
                                            creds.get("refresh_token", ""))
            if tok2 and not e2:
                if save_tokens:
                    try: save_tokens(tok2)
                    except Exception: pass
                r = _do(tok2.get("access_token"))
        if r.status_code not in (200, 201):
            try:
                _e = r.json()
                _m = (_e.get("error", {}) or {}).get("message") or _e.get("message") or r.text[:400]
            except Exception:
                _m = r.text[:400]
            return None, f"[{r.status_code}] {_m}"
        return r.json(), None
    except Exception as e:
        return None, str(e)


# ── 주문 조회 ──────────────────────────────────────────────

def get_orders(creds, start_date, end_date, save_tokens=None, limit=500):
    """기간 주문 조회 → 내부 표준 형식(리스트) 반환.
    start_date/end_date: 'YYYY-MM-DD'. 반환: (orders, err).
    내부 컬럼: 상품주문번호/주문번호/수취인명/상품명/상품번호/수량/상품가격/
             최종 상품별 총 주문금액/배송비 합계/정산예정금액/주문상태/주문일시
    """
    out = []
    offset = 0
    while True:
        data, err = _admin_request(
            creds, "GET", "/api/v2/admin/orders", save_tokens,
            params={
                "start_date": start_date, "end_date": end_date,
                "date_type": "order_date",
                "embed": "items,receivers",
                "limit": min(500, limit), "offset": offset,
            })
        if err:
            return (out or None), err
        _orders = (data or {}).get("orders", []) or []
        if not _orders:
            break
        for od in _orders:
            _oid = str(od.get("order_id", ""))
            _odate = od.get("order_date", "") or od.get("payment_date", "")
            _status = od.get("order_status", "") or od.get("shipping_status", "")
            # 수취인
            _recv = ""
            _recvs = od.get("receivers") or []
            if _recvs:
                _recv = _recvs[0].get("name", "") or _recvs[0].get("shipping_name", "")
            # 배송비 (주문 단위)
            try:
                _ship = int(float(od.get("shipping_fee", 0) or 0))
            except Exception:
                _ship = 0
            _items = od.get("items") or []
            for it in _items:
                try:
                    _qty = int(float(it.get("quantity", 1) or 1))
                except Exception:
                    _qty = 1
                try:
                    _price = int(float(it.get("product_price", 0) or 0))
                except Exception:
                    _price = 0
                try:
                    _paid = int(float(it.get("payment_amount",
                                             it.get("actual_payment_amount", _price * _qty)) or 0))
                except Exception:
                    _paid = _price * _qty
                out.append({
                    "상품주문번호": str(it.get("order_item_code", "") or f"{_oid}-{it.get('product_no','')}"),
                    "주문번호": _oid,
                    "수취인명": _recv,
                    "상품명": str(it.get("product_name", "") or ""),
                    "상품번호": str(it.get("product_no", "") or ""),
                    "옵션정보": str(it.get("option_value", "") or ""),
                    "수량": _qty,
                    "상품가격": _price,
                    "최종 상품별 총 주문금액": _paid,
                    "배송비 합계": _ship,        # 주문 대표배송비 (아이템 분할은 후처리)
                    "정산예정금액": _paid,        # 카페24는 수수료 구조 상이 → 결제액 기준(후처리 조정)
                    "주문상태": _status,
                    "주문일시": _odate,
                    "_platform": "cafe24",
                })
        if len(_orders) < min(500, limit):
            break
        offset += len(_orders)
        if offset >= limit:
            break
    return out, None


# ── 상품 가격 수정 ─────────────────────────────────────────

def update_product_price(creds, product_no, new_price, save_tokens=None):
    """카페24 상품 판매가 수정. 반환: (ok, err).
    PUT /api/v2/admin/products/{product_no}  body {shop_no, request:{price}}
    """
    _pno = str(product_no).strip()
    if not _pno:
        return False, "상품번호가 비어 있습니다."
    try:
        _p = int(float(new_price))
    except Exception:
        return False, "가격이 올바르지 않습니다."
    data, err = _admin_request(
        creds, "PUT", f"/api/v2/admin/products/{_pno}", save_tokens,
        json_body={"shop_no": 1, "request": {"price": str(_p)}})
    if err:
        return False, err
    return True, None


def get_product(creds, product_no, save_tokens=None):
    """상품 단건 조회 (가격 확인용). 반환: (product_dict, err)."""
    data, err = _admin_request(
        creds, "GET", f"/api/v2/admin/products/{str(product_no).strip()}", save_tokens)
    if err:
        return None, err
    return (data or {}).get("product", {}), None
