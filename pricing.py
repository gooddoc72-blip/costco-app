"""판매가 계산 — 플랫폼 공통 가격 공식 (네이버·쿠팡 공유).

네이버(naver_register_service)와 쿠팡(coupang_reprice)이 같은 공식을 쓰도록 여기 한 곳에 둔다.
플랫폼별 '재가격 오케스트레이션'은 각 모듈에서 분리한다:
  · 네이버 = naver_register_service.reprice_registered (등록 리스팅 기반)
  · 쿠팡   = coupang_reprice.reprice_coupang         (주문 vendorItemId 기반)
"""

# 코스트코 상품을 가져올 때 판매가에 기본 반영하는 택배비.
#   무료배송으로 등록하므로 배송비를 판매가에 녹여 역마진을 막는다.
DEFAULT_IMPORT_SHIPPING = 3000


def compute_sale_price(product: dict, margin: float,
                       shipping_cost: int = DEFAULT_IMPORT_SHIPPING) -> int:
    """코스트코가(온라인가 우선) → 판매가.
    공식: (원가 + 택배비) ×(1+마진%) ÷0.945 (네이버 수수료 5.5% 그로스업) → 10원 반올림.
    택배비 기본 3000원: 무료배송으로 등록하므로 배송비를 판매가에 포함해 역마진 방지.
    원가 없으면 0 반환."""
    cost = (
        int(product.get("online_price") or 0)
        or int(product.get("unit_price") or 0)
        or int(product.get("sale_price") or 0)
    )
    if cost <= 0:
        return 0
    cost += int(shipping_cost or 0)   # 택배비 포함 (판매가로 회수)
    return int(round(cost * (1 + margin / 100.0) / 0.945 / 10) * 10)


def unit_cost(product: dict) -> int:
    """저장 원가에서 단품 원가 추정 — split_qty(소분)면 카톤가 ÷ split_qty.
    묶음배수(상품명 'xN개')는 방향이 반대(곱)이라 재가격에선 위험해 미적용(상한 가드로 방어)."""
    raw = (int(product.get("online_price") or 0) or int(product.get("unit_price") or 0)
           or int(product.get("sale_price") or 0))
    if raw <= 0:
        return 0
    sq = max(1, int(product.get("split_qty") or 1))
    return raw // sq if sq > 1 else raw


# 네이버 수수료율 — 위 판매가 공식의 ÷0.945와 같은 값.
NAVER_FEE_RATE = 0.055


def purchase_cost(product: dict) -> int:
    """마진계산용 매입가 — unit_cost와 달리 sale_price로 폴백하지 않는다.
    판매가를 매입가로 잡으면 마진이 늘 '수수료만큼 적자'로 보여 계산기가 거짓말을 한다."""
    raw = int(product.get("online_price") or 0) or int(product.get("unit_price") or 0)
    if raw <= 0:
        return 0
    sq = max(1, int(product.get("split_qty") or 1))
    return raw // sq if sq > 1 else raw


# 고객이 낸 배송비에도 네이버가 정산 시 수수료를 뗀다 — 판매가 수수료와 별개.
#   실측: 배송비 4,000원 → 수수료 77원 (77/4000 = 1.925%).
NAVER_SHIP_FEE_RATE = 0.01925


def sale_for_margin(cost, margin_pct, ship_cost=0, box_cost=0, customer_ship=0,
                    fee_rate: float = NAVER_FEE_RATE,
                    ship_fee_rate: float = NAVER_SHIP_FEE_RATE) -> int:
    """목표 마진율(판매가 대비 %)을 맞추는 판매가 — margin_breakdown의 역산.
    판매가 × (1 − 수수료율 − 마진율) = 매입가 + 택배비 + 포장비 − 고객배송비 × (1 − 배송비수수료율)
    10원 단위 올림(내림하면 목표 마진에 몇 원 모자란다). 매입가 없거나 식이 성립 안 하면 0."""
    import math
    cost = int(cost or 0)
    if cost <= 0:
        return 0
    denom = 1 - fee_rate - float(margin_pct or 0) / 100.0
    if denom <= 0:
        return 0
    need = (cost + int(ship_cost or 0) + int(box_cost or 0)
            - max(0, int(customer_ship or 0)) * (1 - ship_fee_rate))
    if need <= 0:
        return 0
    return int(math.ceil(need / denom / 10) * 10)


def margin_breakdown(sale_price, cost, ship_cost=0, box_cost=0, customer_ship=0,
                     fee_rate: float = NAVER_FEE_RATE,
                     ship_fee_rate: float = NAVER_SHIP_FEE_RATE) -> dict:
    """마진금액 = 판매가 + 고객배송비 − 판매가수수료 − 배송비수수료 − 택배비 − 포장비 − 매입가.
    무료배송이면 customer_ship=0 → 배송비 수입·수수료 모두 0.
    margin_rate는 판매가 대비 %(판매가 0이면 0)."""
    sale = max(0, int(sale_price or 0))
    cship = max(0, int(customer_ship or 0))
    fee = int(round(sale * fee_rate))
    ship_fee = int(round(cship * ship_fee_rate))
    ship, box, cost = int(ship_cost or 0), int(box_cost or 0), int(cost or 0)
    margin = sale + cship - fee - ship_fee - ship - box - cost
    return {'sale': sale, 'customer_ship': cship, 'fee': fee, 'ship_fee': ship_fee,
            'ship': ship, 'box': box, 'cost': cost, 'margin': margin,
            'margin_rate': (margin / sale * 100) if sale else 0.0}
