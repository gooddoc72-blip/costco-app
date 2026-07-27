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
