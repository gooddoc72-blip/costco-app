/**
 * 수익계산 — 도메인 타입 정의
 * Streamlit 누더기 코드와 결별하기 위한 깔끔한 모델
 */

/** 하나의 발송된 주문 + 상품 정보 + 계산된 수익 */
export interface ProfitRow {
  // 식별자
  orderNo: string;           // Naver productOrderId (UNIQUE)
  dispatchedAt: string;      // YYYY-MM-DD

  // 주문 정보 (order_history)
  recipient: string;
  productName: string;
  optionInfo: string;
  qty: number;
  orderAmount: number;       // 최종 상품별 총 주문금액

  // 정산 정보
  settlement: number;        // 정산예정금액 (상품, 수수료 차감 후)
  customerShippingFee: number; // 고객결제 배송비 (수수료 차감 전)

  // 매칭된 상품 (products 테이블에서 조인)
  productId: number | null;
  costcoProductNo: string;   // 코스트코 상품번호 (1박스에 여러 네이버 상품 가능)
  naverOriginPno: string;    // 네이버 원상품번호 (네이버 상품마다 고유) ⭐
  naverChannelPno: string;   // 네이버 채널상품번호 (SmartStore 표시용)
  matchKeyword: string;
  costcoName: string;
  unitPrice: number;         // products.unit_price (1박스 가격)
  splitQty: number;          // 1박스 = N개 분할
  matchSource: 'DB-번호' | 'DB-키워드' | '미매칭';
}

/** 수익 계산 결과 */
export interface ProfitCalc {
  computedCost: number;       // 매입가
  shippingSettleAmount: number; // 실정산 배송비 = customerShippingFee × (1 - 수수료율)
  shippingCommission: number; // 배송비 수수료 = customerShippingFee - shippingSettleAmount
  totalIncome: number;        // settlement + shippingSettleAmount
  totalCost: number;          // computedCost + 택배비 + 박스비
  profit: number;             // totalIncome - totalCost
}

/** 일괄 가격 저장 요청 — 각 행 별 새 단가 */
export interface PriceSaveItem {
  /** Naver 원상품번호 — 같은 코스트코 상품이라도 네이버별 독립 가격 보장 */
  naverOriginPno: string;
  /** 또는 코스트코 상품번호 (naverOriginPno 없을 때만 사용) */
  costcoProductNo?: string;
  /** 매칭 키워드 (둘 다 없을 때 fallback) */
  matchKeyword: string;
  /** 사용자가 입력한 1박스 가격 (split_qty 적용 후) */
  boxPrice: number;
  /** 1박스 분할 수량 */
  splitQty: number;
}

/** 비즈니스 설정 */
export interface Settings {
  shippingCost: number;       // 발송 택배비
  boxCost: number;            // 박스비
  shippingCommissionRate: number; // 네이버 배송비 수수료율 (%)
}
