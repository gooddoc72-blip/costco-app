"""네이버 'QuickSettleByCase' CSV 파서.

스마트스토어 정산관리 → 빠른정산 건별 다운로드 파일 (EUC-KR 인코딩).
한 주문당 두 행(상품주문 + 배송비)을 합산해 dict 1개로 반환.

UI/DB 의존 없음 — 순수 파싱 로직만.
"""
import csv
import io


def _decode_text(file_bytes: bytes) -> str:
    """EUC-KR/CP949 우선, 실패 시 UTF-8/UTF-8-SIG fallback."""
    for enc in ('euc-kr', 'cp949', 'utf-8-sig', 'utf-8'):
        try:
            return file_bytes.decode(enc)
        except Exception:
            continue
    return file_bytes.decode('utf-8', errors='replace')


def _to_int(v) -> int:
    if v is None:
        return 0
    s = str(v).replace(',', '').strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def _norm_date(s: str) -> str:
    """'2026.05.13' → '2026-05-13' 형식 통일."""
    if not s:
        return ''
    return str(s).strip().replace('.', '-').replace('/', '-')


def parse_naver_quicksettle_csv(file_bytes: bytes) -> list:
    """QuickSettleByCase.csv → 주문별 정산 dict 리스트.

    Returns: [
      {
        'product_order_no': '...',         # 상품주문번호
        'order_no': '...',                 # 주문번호
        'buyer_name': '...',
        'product_name': '...',
        'pay_date': 'YYYY-MM-DD',
        'settle_complete_date': 'YYYY-MM-DD',
        'settle_basis_date': 'YYYY-MM-DD',
        'settle_type': '빠른정산'|'공제',
        'reason': '배송시작'|'집화처리'|'클레임요청'|...,
        'product_amount': int,             # 상품 정산금액 (음수=클레임환불)
        'shipping_amount': int,            # 배송비 정산금액
        'total_amount': int,               # product + shipping
        'dispatch_at': '...',              # 집화처리 일시
      }, ...
    ]
    """
    text = _decode_text(file_bytes)
    reader = csv.DictReader(io.StringIO(text))
    by_po = {}  # 상품주문번호(메인) → dict
    for r in reader:
        # 컬럼명을 strip하여 인코딩 노이즈 흡수
        rr = {(k or '').strip(): (v if v is not None else '') for k, v in r.items()}
        po_main = rr.get('상품주문번호', '').strip()
        po_basis = rr.get('배송비 정산기준 상품주문번호', '').strip()
        gubun = rr.get('구분', '').strip()
        amount = _to_int(rr.get('금액', 0))

        if gubun == '상품주문':
            # 상품주문 행 — 메인 주문 식별
            d = by_po.setdefault(po_main, {
                'product_order_no': po_main,
                'product_amount': 0,
                'shipping_amount': 0,
            })
            d['product_amount'] += amount
            d['order_no']            = rr.get('주문번호', '').strip()
            d['buyer_name']          = rr.get('구매자명', '').strip()
            d['product_name']        = rr.get('상품명', '').strip()
            d['pay_date']            = _norm_date(rr.get('결제일', ''))
            d['settle_complete_date']= _norm_date(rr.get('정산완료일', ''))
            d['settle_basis_date']   = _norm_date(rr.get('정산기준일', ''))
            d['settle_type']         = rr.get('정산구분', '').strip()
            d['reason']              = rr.get('사유', '').strip()
            d['dispatch_at']         = rr.get('집화처리(배송시작) 일시', '').strip()

        elif gubun == '배송비':
            # 배송비 행 — 같은 상품주문번호 또는 정산기준 컬럼 참조
            target_po = po_basis or po_main
            if not target_po:
                continue
            d = by_po.setdefault(target_po, {
                'product_order_no': target_po,
                'product_amount': 0,
                'shipping_amount': 0,
            })
            d['shipping_amount'] += amount
            # 상품주문 행이 안 들어왔을 경우 대비 메타도 보강
            d.setdefault('order_no',             rr.get('주문번호', '').strip())
            d.setdefault('buyer_name',           rr.get('구매자명', '').strip())
            d.setdefault('product_name',         '')
            d.setdefault('pay_date',             _norm_date(rr.get('결제일', '')))
            d.setdefault('settle_complete_date', _norm_date(rr.get('정산완료일', '')))
            d.setdefault('settle_basis_date',    _norm_date(rr.get('정산기준일', '')))
            d.setdefault('settle_type',          rr.get('정산구분', '').strip())
            d.setdefault('reason',               rr.get('사유', '').strip())
            d.setdefault('dispatch_at',          rr.get('집화처리(배송시작) 일시', '').strip())

    # total_amount 계산 + 정리
    for d in by_po.values():
        d['total_amount'] = int(d.get('product_amount', 0)) + int(d.get('shipping_amount', 0))

    return list(by_po.values())
