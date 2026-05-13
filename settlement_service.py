"""정산 매칭 비즈니스 로직 — 발송건 vs 정산 내역 대조.

UI(streamlit)와 DB 모듈에 의존하지 않는 순수 매칭 로직.
입력은 dict 리스트, 출력은 매칭 결과 dict.
"""
from typing import List, Dict


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
            actual = int(settled_by_po[po].get('settle_amount') or 0)
            total_actual += actual
            diff = actual - exp
            rec = {
                'product_order_no': po,
                'recipient':        s.get('recipient', ''),
                'product_name':     s.get('product_name', ''),
                'expected':         exp,
                'actual':           actual,
                'diff':             diff,
                'sales_amount':     int(settled_by_po[po].get('sales_amount') or 0),
                'commission':       int(settled_by_po[po].get('commission') or 0),
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
