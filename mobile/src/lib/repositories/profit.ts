/**
 * Profit Repository — 수익계산 관련 DB 쿼리만 담당.
 * 비즈니스 로직 없음. SQL과 결과 매핑만.
 */
import { getUserDb } from '@/lib/db';
import type { ProfitRow } from '@/lib/types';

/** dispatch_log + order_history + products LEFT JOIN으로 한 날짜 모든 발송건 조회 */
export function fetchDispatchedRows(username: string, date: string): ProfitRow[] {
  const db = getUserDb(username);
  const rows = db.prepare(`
    SELECT
      dl.order_no                          AS orderNo,
      dl.dispatched_at                     AS dispatchedAt,
      COALESCE(oh.recipient, dl.recipient) AS recipient,
      COALESCE(oh.product_name, dl.product_name) AS productName,
      COALESCE(oh.option_info, '')         AS optionInfo,
      COALESCE(oh.qty, 1)                  AS qty,
      COALESCE(oh.order_amount, 0)         AS orderAmount,
      COALESCE(oh.settlement, dl.expected_settlement, 0)    AS settlement,
      COALESCE(oh.shipping_fee, dl.customer_shipping_fee, 0) AS customerShippingFee,
      COALESCE(oh.product_no, '')          AS costcoProductNo,
      p.id                                  AS productId,
      COALESCE(p.naver_origin_pno, '')     AS naverOriginPno,
      COALESCE(p.naver_channel_pno, '')    AS naverChannelPno,
      COALESCE(p.match_keyword, '')        AS matchKeyword,
      COALESCE(p.costco_name, '')          AS costcoName,
      COALESCE(p.unit_price, 0)            AS unitPrice,
      COALESCE(p.split_qty, 1)             AS splitQty
    FROM dispatch_log dl
    LEFT JOIN order_history oh ON dl.order_no = oh.order_no
    LEFT JOIN products p ON
      -- ⭐ 1순위: 주문 수집 시 저장된 영구 매칭 (상품명 변경에도 안 깨짐)
      (oh.matched_product_id IS NOT NULL AND p.id = oh.matched_product_id)
      -- 2순위: 코스트코 상품번호
      OR (oh.matched_product_id IS NULL AND oh.product_no != '' AND p.product_no = oh.product_no)
      -- 3순위: 정확한 상품명 일치 (마지막 폴백)
      OR (oh.matched_product_id IS NULL AND p.match_keyword = oh.product_name)
    WHERE dl.dispatched_at = ?
    ORDER BY COALESCE(oh.product_name, dl.product_name), dl.id
  `).all(date) as any[];

  return rows.map(r => ({
    orderNo: String(r.orderNo),
    dispatchedAt: r.dispatchedAt,
    recipient: r.recipient || '',
    productName: r.productName || '',
    optionInfo: r.optionInfo || '',
    qty: Number(r.qty) || 1,
    orderAmount: Number(r.orderAmount) || 0,
    settlement: Number(r.settlement) || 0,
    customerShippingFee: Number(r.customerShippingFee) || 0,
    productId: r.productId,
    costcoProductNo: r.costcoProductNo || '',
    naverOriginPno: r.naverOriginPno || '',
    naverChannelPno: r.naverChannelPno || '',
    matchKeyword: r.matchKeyword || '',
    costcoName: r.costcoName || '',
    unitPrice: Number(r.unitPrice) || 0,
    splitQty: Math.max(1, Number(r.splitQty) || 1),
    matchSource: r.productId
      ? (r.naverOriginPno || r.costcoProductNo ? 'DB-번호' : 'DB-키워드')
      : '미매칭',
  }));
}
