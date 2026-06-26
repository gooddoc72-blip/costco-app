"""정산 매칭 비즈니스 로직 — 발송건 vs 정산 내역 대조.

UI(streamlit)와 DB 모듈에 의존하지 않는 순수 매칭 로직.
입력은 dict 리스트, 출력은 매칭 결과 dict.
"""
from typing import List, Dict


def _diff_reason(diff: int, settled: Dict, tolerance: int = 10) -> str:
    """예상 vs 실제 정산 차액의 원인을 추정해 한 줄 라벨로 반환."""
    if abs(diff) <= tolerance:
        return "일치"
    _stype = str(settled.get('settle_type', '') or '')
    _stype_u = _stype.upper()
    _reason = str(settled.get('reason', '') or '')
    _ad = int(settled.get('ad_cost') or 0)
    _comm = int(settled.get('commission') or 0)
    if ('공제' in _stype or '클레임' in _reason or '취소' in _reason or '반품' in _reason
            or any(k in _stype_u for k in ('CLAIM', 'CANCEL', 'REFUND', 'RETURN', 'DEDUCT'))):
        return "공제/클레임"
    if _ad > 0:
        return f"광고비 공제 {_ad:,}원"
    if diff < 0 and _comm > 0 and abs(diff + _comm) <= max(tolerance, int(_comm * 0.1)):
        return f"수수료 차감 {_comm:,}원"
    if diff < 0:
        return "정산 차감(수수료/배송비)"
    return "추가 정산(이전분 등)"


def settle_type_kr(code: str) -> str:
    """네이버 settleType 코드 → 한글 라벨."""
    c = str(code or '')
    if not c:
        return ''
    if 'FAST' in c or 'QUICK' in c or '빠른' in c:
        return '빠른정산'
    if 'NORMAL' in c or '일반' in c:
        return '일반정산'
    if '공제' in c or 'CLAIM' in c or 'DEDUCT' in c:
        return '공제'
    return c


def _lag_days(ship_date: str, settle_date: str) -> int:
    """발송일 → 정산일 소요 일수. 파싱 실패 시 None."""
    try:
        from datetime import datetime as _dt
        a = _dt.strptime(str(ship_date)[:10], "%Y-%m-%d")
        b = _dt.strptime(str(settle_date)[:10], "%Y-%m-%d")
        return (b - a).days
    except Exception:
        return None


def match_settled_to_dispatch(settled_rows: List[Dict], dispatch_by_po: Dict,
                              tolerance: int = 10) -> Dict:
    """정산일 기준 역추적 매칭 — 정산건의 상품주문번호로 원래 발송건을 찾는다.

    Args:
        settled_rows: naver_settlements 행 dict 리스트 (product_order_no,
                      settle_amount, commission, settle_type, product_order_type,
                      product_name, buyer_name, settle_date, ...)
        dispatch_by_po: {order_no: dispatch_row}  (get_dispatch_by_order_nos 결과)
        tolerance: 금액 차이 허용 오차

    Returns:
        {
          'matched':[...], 'mismatched':[...], 'no_dispatch':[...],
          'delivery_total': int, 'delivery_n': int,
          'summary': {...}
        }
    """
    matched, mismatched, no_dispatch = [], [], []
    delivery_total = delivery_n = 0
    total_expected = total_actual = 0

    for s in settled_rows:
        po = str(s.get('product_order_no', '')).strip()
        if not po:
            continue
        pot = str(s.get('product_order_type', '') or '')
        actual = int(s.get('settle_amount') or 0)
        # 배송비 정산 라인(DELIVERY)은 발송건과 별도 번호 → 배송비 합산만
        if pot == 'DELIVERY':
            delivery_total += actual
            delivery_n += 1
            continue
        d = dispatch_by_po.get(po)
        base = {
            'product_order_no': po,
            'product_name':     s.get('product_name', ''),
            'buyer_name':       s.get('buyer_name', ''),
            'settle_type':      settle_type_kr(s.get('settle_type', '')),
            'actual':           actual,
            'commission':       int(s.get('commission') or 0),
            'settle_date':      s.get('settle_date', ''),
        }
        if d:
            exp = int(d.get('expected_settlement') or 0)
            diff = actual - exp
            total_expected += exp
            total_actual += actual
            rec = {
                **base,
                'ship_date':   d.get('dispatched_at', ''),
                'expected':    exp,
                'diff':        diff,
                'lag_days':    _lag_days(d.get('dispatched_at', ''), s.get('settle_date', '')),
                'diff_reason': _diff_reason(diff, s, tolerance),
            }
            (matched if abs(diff) <= tolerance else mismatched).append(rec)
        else:
            no_dispatch.append({**base, 'ship_date': '', 'expected': 0,
                                'diff': 0, 'lag_days': None, 'diff_reason': '발송기록 없음'})

    return {
        'matched': matched,
        'mismatched': mismatched,
        'no_dispatch': no_dispatch,
        'delivery_total': delivery_total,
        'delivery_n': delivery_n,
        'summary': {
            'settled_n':      len(matched) + len(mismatched) + len(no_dispatch),
            'matched_n':      len(matched),
            'mismatched_n':   len(mismatched),
            'no_dispatch_n':  len(no_dispatch),
            'delivery_n':     delivery_n,
            'delivery_total': delivery_total,
            'total_expected': total_expected,
            'total_actual':   total_actual,
            'total_diff':     total_actual - total_expected,
        }
    }


def match_shipped_vs_settled(shipped: List[Dict], settled: List[Dict],
                              tolerance: int = 10) -> Dict:
    """발송건(shipped)과 정산 내역(settled)을 productOrderNo 기준으로 매칭.

    Args:
        shipped: [{'product_order_no', 'recipient', 'product_name', 'expected_settlement', ...}, ...]
        settled: [{'product_order_no', 'settle_amount', 'sales_amount', 'commission', ...}, ...]
        tolerance: 금액 차이가 이 값 이하면 일치로 간주 (반올림 오차 흡수)

    Returns:
        {
          'matched':   [{'po', 'expected', 'actual', 'diff', ...}, ...],  # 양쪽 모두 존재
          'mismatched':[...],   # 양쪽 모두 있지만 금액 차이 > tolerance
          'missing':   [...],   # 발송했는데 정산 안된 (정산 누락)
          'orphan':    [...],   # 정산만 있고 발송 기록 없음 (조회 범위 외)
          'summary':   {'shipped_n', 'settled_n', 'matched_n', 'mismatched_n',
                        'missing_n', 'orphan_n',
                        'total_expected', 'total_actual', 'total_diff'}
        }
    """
    shipped_by_po = {str(s.get('product_order_no', '')).strip(): s for s in shipped
                     if s.get('product_order_no')}
    settled_by_po = {str(s.get('product_order_no', '')).strip(): s for s in settled
                     if s.get('product_order_no')}

    matched, mismatched, missing, orphan = [], [], [], []
    total_expected = total_actual = 0

    for po, s in shipped_by_po.items():
        exp = int(s.get('expected_settlement') or 0)
        total_expected += exp
        if po in settled_by_po:
            _st = settled_by_po[po]
            actual = int(_st.get('settle_amount') or 0)
            total_actual += actual
            diff = actual - exp
            _settle_type = str(_st.get('settle_type', '') or '')
            rec = {
                'product_order_no': po,
                'recipient':        s.get('recipient', ''),
                'product_name':     s.get('product_name', ''),
                'expected':         exp,
                'actual':           actual,
                'diff':             diff,
                'sales_amount':     int(_st.get('sales_amount') or 0),
                'product_amount':   int(_st.get('product_amount') or 0),
                'shipping_amount':  int(_st.get('shipping_amount') or 0),
                'commission':       int(_st.get('commission') or 0),
                'ad_cost':          int(_st.get('ad_cost') or 0),
                'settle_type':      _settle_type,
                'diff_reason':      _diff_reason(diff, _st, tolerance),
            }
            if abs(diff) <= tolerance:
                matched.append(rec)
            else:
                mismatched.append(rec)
        else:
            missing.append({
                'product_order_no': po,
                'recipient':        s.get('recipient', ''),
                'product_name':     s.get('product_name', ''),
                'expected':         exp,
            })

    for po, s in settled_by_po.items():
        if po not in shipped_by_po:
            orphan.append({
                'product_order_no': po,
                'order_no':         s.get('order_no', ''),
                'settle_amount':    int(s.get('settle_amount') or 0),
                'sales_amount':     int(s.get('sales_amount') or 0),
            })

    return {
        'matched':    matched,
        'mismatched': mismatched,
        'missing':    missing,
        'orphan':     orphan,
        'summary': {
            'shipped_n':      len(shipped_by_po),
            'settled_n':      len(settled_by_po),
            'matched_n':      len(matched),
            'mismatched_n':   len(mismatched),
            'missing_n':      len(missing),
            'orphan_n':       len(orphan),
            'total_expected': total_expected,
            'total_actual':   total_actual,
            'total_diff':     total_actual - total_expected,
        }
    }


def match_daily_total(dispatch_rows: List[Dict], daily_settle_total: int,
                      tolerance: int = 10) -> Dict:
    """일괄발송 합계 vs 일일정산 합계 매칭 (per-order productOrderId 없이 합계로만 검증).

    Args:
        dispatch_rows: dispatch_log 행 리스트 (expected_settlement 포함)
        daily_settle_total: API /pay-settle/settle/daily 의 settleAmount
        tolerance: 합계 차이가 이 값 이하면 일치로 간주

    Returns:
        {
          'dispatch_count': 발송 성공 건수,
          'expected_total': 발송건 정산예정합계,
          'actual_total':   네이버 실제 정산합계,
          'diff':           actual - expected (음수면 누락 가능성),
          'match':          'OK' | 'MISMATCH',
          'rate':           expected 대비 actual 비율 (%),
        }
    """
    expected_total = sum(int(r.get('expected_settlement') or 0) for r in dispatch_rows)
    actual_total = int(daily_settle_total or 0)
    diff = actual_total - expected_total
    rate = (actual_total / expected_total * 100.0) if expected_total else 0
    return {
        'dispatch_count': len(dispatch_rows),
        'expected_total': expected_total,
        'actual_total':   actual_total,
        'diff':           diff,
        'match':          'OK' if abs(diff) <= tolerance else 'MISMATCH',
        'rate':           round(rate, 2),
    }


def analyze_shipping_commission(dispatch_rows: List[Dict],
                                settled_rows: List[Dict]) -> Dict:
    """배송비 수수료 분석 — 발송건의 고객결제 배송비 vs CSV 정산된 배송비.

    Args:
        dispatch_rows: dispatch_log 행 (각 행에 expected_settlement, order_no 포함)
            * 별도 customer_shipping_fee가 있으면 사용, 없으면 order_history에서 가져와야 함
        settled_rows: db_settlements 행 (product_amount, shipping_amount 분리)

    Returns:
        {
          'rows': 매칭별 상세 [{po, customer_paid_shipping, settled_shipping, commission, ...}],
          'total_customer_shipping': int,
          'total_settled_shipping':  int,
          'total_commission':         int,
          'avg_commission_rate':     float,  # %
        }
    """
    settled_by_po = {str(s.get('product_order_no', '')).strip(): s for s in settled_rows}
    rows = []
    total_cust = total_sett = 0
    for d in dispatch_rows:
        po = str(d.get('order_no', '')).strip()
        if not po:
            continue
        s = settled_by_po.get(po)
        if not s:
            continue
        cust = int(d.get('customer_shipping_fee') or 0)
        sett = int(s.get('shipping_amount') or 0)
        comm = cust - sett
        total_cust += cust
        total_sett += sett
        rate = (comm / cust * 100) if cust > 0 else 0
        rows.append({
            'product_order_no': po,
            'recipient':        d.get('recipient', ''),
            'customer_paid':    cust,
            'settled':          sett,
            'commission':       comm,
            'rate':             round(rate, 2),
        })
    total_comm = total_cust - total_sett
    avg_rate = (total_comm / total_cust * 100) if total_cust > 0 else 0
    return {
        'rows': rows,
        'total_customer_shipping': total_cust,
        'total_settled_shipping':  total_sett,
        'total_commission':         total_comm,
        'avg_commission_rate':     round(avg_rate, 2),
    }


def shipped_orders_from_db_rows(rows: list) -> List[Dict]:
    """db_orders의 order_history 또는 daily_orders 행을 매칭 입력 dict로 변환.

    DB 컬럼명을 매칭 함수가 기대하는 키로 통일.
    """
    out = []
    for r in rows:
        po = str(r.get('상품주문번호') or r.get('product_order_no') or r.get('order_no') or '').strip()
        if not po:
            continue
        out.append({
            'product_order_no':    po,
            'recipient':           r.get('수취인명') or r.get('recipient', ''),
            'product_name':        r.get('상품명')   or r.get('product_name', ''),
            'expected_settlement': int(r.get('정산예정금액') or r.get('settlement') or 0),
        })
    return out
