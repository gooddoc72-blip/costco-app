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


def infer_purchase_decision(decision_date: str, auto_hour_max: int = 4):
    """구매확정 일시(decisionDate)로 자동/수동 구매확정을 추정.
    새벽(0~auto_hour_max시) 확정 = 네이버 자동확정 일괄(추정), 그 외 = 고객 수동(추정).
    Returns: (라벨, 'YYYY-MM-DD HH:MM').
    """
    dd = str(decision_date or '')
    if len(dd) < 13:
        return '', ''
    try:
        hour = int(dd[11:13])
    except Exception:
        return '', dd[:16].replace('T', ' ')
    label = '🤖 자동(추정)' if hour <= auto_hour_max else '👤 수동(추정)'
    return label, dd[:16].replace('T', ' ')


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


def reverse_engineer_settlement_stats(settle_rows: List[Dict],
                                      dispatch_by_po: Dict) -> Dict:
    """수집된 정산건에서 역산: 실효 수수료율·배송비 정산율·발송→정산 소요일(빠른/일반).
    등급 입력 없이 내 스토어 실제 데이터 기준.

    Args:
        settle_rows: naver_settlements 행 (get_naver_settlements_range)
        dispatch_by_po: {상품주문번호: dispatch_log 행} (소요일 계산용)
    Returns: dict — comm_rate/ship_rate(%, median), quick/normal lag median·p90,
             quick_share(빠른정산 비중), 표본수. 표본 부족 항목은 None.
    """
    def _median(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None

    def _p90(xs):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(len(xs) * 0.9))] if xs else None

    comm_rates, ship_rates = [], []
    quick_lags, normal_lags = [], []
    quick_n = normal_n = 0
    for r in settle_rows:
        _type = settle_type_kr(r.get('settle_type', ''))
        if _type == '공제':
            continue
        _is_delivery = str(r.get('product_order_type', '') or '') == 'DELIVERY'
        _sales = int(r.get('sales_amount') or 0)
        if _is_delivery:
            _settle = int(r.get('settle_amount') or 0)
            if _sales > 0 and 0 < _settle <= _sales:
                ship_rates.append(_settle / _sales * 100)
            continue
        _comm = int(r.get('commission') or 0)
        if _sales > 0 and 0 < _comm < _sales:
            comm_rates.append(_comm / _sales * 100)
        # 소요일: 발송기록과 조인
        _d = dispatch_by_po.get(str(r.get('product_order_no', '')))
        if _d:
            _lag = _lag_days(_d.get('dispatched_at', ''), r.get('settle_date', ''))
            if _lag is not None and 0 <= _lag <= 60:
                if _type == '빠른정산':
                    quick_lags.append(_lag)
                else:
                    normal_lags.append(_lag)
        if _type == '빠른정산':
            quick_n += 1
        else:
            normal_n += 1

    _tot = quick_n + normal_n
    return {
        'comm_rate':    round(_median(comm_rates), 2) if comm_rates else None,
        'ship_rate':    round(_median(ship_rates), 2) if ship_rates else None,
        'quick_lag':    _median(quick_lags),
        'quick_lag_p90': _p90(quick_lags),
        'normal_lag':   _median(normal_lags),
        'normal_lag_p90': _p90(normal_lags),
        'quick_share':  round(quick_n / _tot * 100, 1) if _tot else None,
        'n_comm': len(comm_rates), 'n_ship': len(ship_rates),
        'n_quick_lag': len(quick_lags), 'n_normal_lag': len(normal_lags),
        'n_total': _tot,
    }


def detect_settle_mode(quick_share, yesterday_dispatched_n: int,
                       yesterday_settled_n: int) -> str:
    """판매자 정산방식(빠른/일반) 자동 판정 — 정산건 수집 데이터 기준.

    1) 실측 빠른정산 비중 ≥ 50% → 'quick'
    2) 전날 발송건이 있는데 정산에 하나도 안 잡힘 → 'normal'
       (빠른정산이면 집하 D+1 정산이므로, 전날 발송건 정산 부재 = 일반정산 구조)
    3) 실측 비중이 있고 50% 미만 → 'normal'
    4) 판단 근거 없음 → '' (미정)
    """
    if quick_share is not None and quick_share >= 50:
        return 'quick'
    if yesterday_dispatched_n > 0 and yesterday_settled_n == 0:
        return 'normal'
    if quick_share is not None:
        return 'normal'
    return ''


def find_unsettled_dispatches(dispatch_rows: List[Dict], settled_po_set: set,
                              today_str: str, delay_threshold: int = 10,
                              is_quick_seller: bool = False,
                              quick_threshold: int = 2,
                              normal_lag: int = None) -> Dict:
    """발송건 중 아직 정산되지 않은 건 추출 + 상태 분류 (순방향).

    빠른정산 판매자여도 빠른정산 제외건은 일반정산(구매확정 기준)으로 넘어가므로,
    빠른정산 기한(quick_threshold) 초과 미정산건은 자동으로 '일반정산 전환'으로 분류해
    일반정산 기한(delay_threshold)까지는 누락으로 취급하지 않는다.

    Args:
        dispatch_rows: dispatch_log 행 리스트 (order_no=상품주문번호, dispatched_at, ...)
        settled_po_set: 정산된 상품주문번호 집합 (get_settled_product_order_nos)
        today_str: 오늘 날짜 'YYYY-MM-DD' (경과일 계산용)
        delay_threshold: 발송 후 이 일수 초과 + 미정산이면 '누락 의심' (일반정산 기한, 실측 p90+2)
        is_quick_seller: 빠른정산 이용 판매자 여부 (역산 quick_share 기반)
        quick_threshold: 빠른정산 기대 소요일 (기본 2 = 집하 D+1 영업일 여유)
        normal_lag: 일반정산 실측 중앙값 소요일 → 예상 정산일 계산용
    """
    unsettled, settled_n = [], 0
    for d in dispatch_rows:
        po = str(d.get('order_no', '')).strip()
        if not po:
            continue
        if po in settled_po_set:
            settled_n += 1
            continue
        ship = d.get('dispatched_at', '')
        elapsed = _lag_days(ship, today_str)
        # 상태 분류: 빠른대기 → (기한 초과 시) 일반정산 전환 → (일반 기한 초과) 누락 의심
        if elapsed is None:
            cls = '정산지연(대기)'
        elif elapsed > delay_threshold:
            cls = '누락 의심'
        elif is_quick_seller and elapsed <= quick_threshold:
            cls = '⚡ 빠른정산 대기'
        elif is_quick_seller:
            cls = '🕐 일반정산 전환(구매확정 대기)'
        else:
            cls = '🕐 일반정산 대기(구매확정 전)'
        # 예상 정산일: 빠른대기=발송+quick_threshold, 그 외=발송+normal_lag(실측 중앙값)
        expected_date = ''
        try:
            from datetime import datetime as _dt, timedelta as _td
            _sd = _dt.strptime(str(ship)[:10], "%Y-%m-%d")
            if cls == '⚡ 빠른정산 대기':
                expected_date = (_sd + _td(days=quick_threshold)).strftime("%Y-%m-%d")
            elif normal_lag is not None:
                expected_date = (_sd + _td(days=int(normal_lag))).strftime("%Y-%m-%d")
        except Exception:
            pass
        unsettled.append({
            'product_order_no':    po,
            'recipient':           d.get('recipient', ''),
            'product_name':        d.get('product_name', ''),
            'ship_date':           ship,
            'elapsed_days':        elapsed,
            'expected_date':       expected_date,
            'expected_settlement': int(d.get('expected_settlement') or 0),
            'status':              cls,
        })
    unsettled.sort(key=lambda x: (x['elapsed_days'] if x['elapsed_days'] is not None else -1),
                   reverse=True)
    return {
        'unsettled': unsettled,
        'summary': {
            'dispatch_n':       len(dispatch_rows),
            'settled_n':        settled_n,
            'unsettled_n':      len(unsettled),
            'suspect_n':        sum(1 for u in unsettled if u['status'] == '누락 의심'),
            'quick_wait_n':     sum(1 for u in unsettled if u['status'] == '⚡ 빠른정산 대기'),
            'normal_wait_n':    sum(1 for u in unsettled
                                    if u['status'].startswith('🕐') or u['status'] == '정산지연(대기)'),
            'pending_n':        sum(1 for u in unsettled if u['status'] != '누락 의심'),
            'unsettled_amount': sum(u['expected_settlement'] for u in unsettled),
        }
    }


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
    settle_total = 0  # 상품 정산 총액 (매칭 여부 무관)

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
        settle_total += actual  # 상품 정산 총액 (오늘 들어온 상품 정산금)
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
            'settle_total':   settle_total,                 # 상품 정산 총액
            'grand_total':    settle_total + delivery_total,  # 상품+배송비 총정산
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
