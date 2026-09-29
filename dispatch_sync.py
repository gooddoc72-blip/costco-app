"""앱 밖에서 발송한 주문을 발송 기록(dispatch_log)에 채운다.

영수증 정산은 '발송 기준'으로 매칭한다. 그런데 스마트스토어센터에서 직접
발송하면(앱 일괄발송이 실패해 건별로 처리한 날 등) dispatch_log가 안 생겨,
그 주문은 '송장등록 안 된 주문'으로 남고 청구에서 빠진다
(oxo 9/29 — 길리안 7건 + 묶음배송 2건).

  pull_naver_dispatch : 네이버에 물어 실제 발송된 건의 송장·발송일로 기록
  mark_dispatched     : 네이버에도 기록이 없는 건(다른 경로 발송)을 관리자가 지정

기록 경로는 앱 일괄발송 성공 때와 같다 — 주문 상세를 주문이력에 먼저 넣고
(수익계산·정산이 상품명·정산예정금액을 거기서 읽는다) log_dispatch_success로
남긴다(재고 차감 포함, 주문번호 기준 멱등).
"""
from datetime import datetime

#: 네이버 택배사 코드 → 앱이 쓰는 이름(기존 발송 기록과 같은 표기)
_COURIER_KO = {"CJGLS": "CJ대한통운", "EPOST": "우체국택배", "HANJIN": "한진택배",
               "HYUNDAI": "롯데택배", "KGB": "로젠택배", "KDEXP": "경동택배",
               "DAESIN": "대신택배"}

#: 발송된 것으로 보는 네이버 상태
_SHIPPED = {"DELIVERING", "DELIVERED", "PURCHASE_DECIDED", "EXCHANGED"}


def _parse_dt(s):
    s = str(s or '')[:19].replace('T', ' ')
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def pull_naver_dispatch(username, order_nos):
    """주문번호들을 네이버에 조회해 **실제로 발송된 것만** dispatch_log에 기록한다.

    발송일은 네이버의 발송시각을 영업일로 환산한 날이다(새벽 처리는 전날 —
    앱 일괄발송과 같은 규칙). 정산일과 다를 수 있으니 결과에 날짜별로 알려 준다.

    반환: {'saved': {날짜: 건수}, 'not_shipped': [주문번호], 'error': str}
    """
    import requests
    import pandas as pd
    import naver_api
    from naver_api.core import get_token
    from naver_api.orders import _call_retry
    from db import get_all_settings, save_order_history, search_order_history, log_dispatch_success
    from db_dispatch_log import business_dispatch_date

    out = {'saved': {}, 'not_shipped': [], 'error': ''}
    ids = sorted({str(o).strip() for o in (order_nos or []) if str(o).strip()})
    if not ids:
        return out
    s = get_all_settings(username) or {}
    cid, sec = s.get('api_client_id', ''), s.get('api_client_secret', '')
    if not (cid and sec):
        out['error'] = '네이버 API 키 없음'
        return out
    tok, err = get_token(cid, sec)
    if not tok:
        out['error'] = f'토큰 오류: {err}'
        return out

    shipped = {}   # 주문번호 → (발송일, 송장, 택배사)
    for i in range(0, len(ids), 300):
        chunk = ids[i:i + 300]
        try:
            r = _call_retry("POST",
                            "https://api.commerce.naver.com/external/v1/pay-order/seller/product-orders/query",
                            headers={"Authorization": f"Bearer {tok}"},
                            json={"productOrderIds": chunk})
        except requests.exceptions.RequestException as e:
            out['error'] = f'네이버 조회 실패: {e}'
            return out
        if r.status_code != 200:
            out['error'] = f'네이버 조회 실패(HTTP {r.status_code})'
            return out
        for d in (r.json().get('data') or []):
            po = d.get('productOrder') or {}
            dv = d.get('delivery') or {}
            pid = str(po.get('productOrderId') or '')
            _dt = _parse_dt(dv.get('sendDate') or dv.get('dispatchDate'))
            if po.get('productOrderStatus') in _SHIPPED and _dt:
                shipped[pid] = (business_dispatch_date(_dt),
                                str(dv.get('trackingNumber') or ''),
                                _COURIER_KO.get(str(dv.get('deliveryCompany') or ''),
                                                str(dv.get('deliveryCompany') or '')))
    out['not_shipped'] = [o for o in ids if o not in shipped]
    if not shipped:
        return out

    # 주문 상세 → 주문이력 (정산예정금액·상품명의 출처)
    try:
        rows, _e = naver_api.fetch_order_details_by_ids(cid, sec, list(shipped))
        if rows:
            save_order_history(username, pd.DataFrame(rows))
    except Exception:
        pass
    hist = {str(h.get('order_no')): h
            for h in (search_order_history(username, date_from='', date_to='', limit=5000) or [])}

    by_date = {}
    for pid, (dday, trk, cour) in shipped.items():
        h = hist.get(pid, {})
        by_date.setdefault(dday, []).append({
            'order_no': pid, 'recipient': h.get('recipient') or '',
            'product_name': h.get('product_name') or '',
            'expected_settlement': int(h.get('settlement') or 0),
            'customer_shipping_fee': int(h.get('shipping_fee') or 0),
            'tracking_no': trk, 'courier': cour})
    for dday, rws in by_date.items():
        out['saved'][dday] = log_dispatch_success(username, rws, dday, platform='naver')
    return out


def mark_dispatched(username, orders, dispatched_at):
    """네이버에도 발송 기록이 없는 주문을 관리자가 '발송됨'으로 지정한다.

    다른 경로(퀵·직접 전달·다른 스토어 송장)로 보낸 건이다. 물건은 나갔으니
    정산 대상이 되어야 한다. orders: [{order_no, recipient, product_name, tracking_no}]
    반환: 저장 건수
    """
    from dispatch_upload import save_dispatch
    rows = [{'order_no': str(o.get('order_no') or ''),
             'recipient': str(o.get('recipient') or ''),
             'product_name': str(o.get('product_name') or ''),
             'tracking_no': str(o.get('tracking_no') or ''),
             'courier': str(o.get('courier') or '직접지정')}
            for o in (orders or []) if str(o.get('order_no') or '').strip()]
    if not rows:
        return 0
    saved, _skipped, _moved = save_dispatch({username: rows}, str(dispatched_at),
                                            platform='manual')
    return int(sum((saved or {}).values()))
