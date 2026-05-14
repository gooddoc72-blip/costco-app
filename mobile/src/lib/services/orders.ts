/**
 * Orders Service — Naver API 호출 + order_history 저장.
 */
import { fetchRecentOrders, type NaverOrderRow } from '@/lib/api/naver';
import { bulkUpsertOrders, type OrderHistoryInput } from '@/lib/repositories/orders';
import { loadUserSettings } from '@/lib/services/settings';
import { findMatchingProductId } from '@/lib/services/matching';

export interface CollectResult {
  fetched: number;
  inserted: number;
  updated: number;
  matched: number;     // ⭐ 신규: 매칭된 주문 수
  errors: string[];
  rows: NaverOrderRow[];
}

/** 네이버 API 호출 → 자동 매칭 → order_history upsert */
export async function collectNaverOrders(
  username: string,
  hoursBack: number = 48,
  statusFilter?: string[]
): Promise<CollectResult> {
  const settings = loadUserSettings(username);
  const clientId = settings.naverApiClientId;
  const clientSecret = settings.naverApiClientSecret;
  if (!clientId || !clientSecret) {
    throw new Error('네이버 API 키가 설정되어 있지 않습니다. ⚙️ 설정에서 입력해주세요.');
  }
  const rows = await fetchRecentOrders(clientId, clientSecret, hoursBack, statusFilter);

  // ⭐ 매칭: 수집 시점에 products.id를 영구 링크로 저장
  // → 사용자가 나중에 상품명 변경해도 매입가 연결 안 깨짐
  let matchedCount = 0;
  const dbItems: OrderHistoryInput[] = rows.map(r => {
    const match = findMatchingProductId(username, {
      productNo: r.productNo,
      productName: r.productName,
      // naverOriginPno는 주문에서 직접 못 옴 — products에서 product_no로 역참조 가능
    });
    if (match.productId) matchedCount++;
    return {
      orderNo: r.productOrderId,
      // ⭐ 결제일 우선 — 시스템 강제 날짜 금지
      orderDate: r.payDate ? r.payDate.slice(0, 10) : new Date().toISOString().slice(0, 10),
      recipient: r.recipient,
      productName: r.productName,
      productNo: r.productNo,
      optionInfo: r.optionInfo,
      qty: r.qty,
      orderAmount: r.orderAmount,
      shippingFee: r.shippingFee,
      settlement: r.settlement,
      status: r.status,
      matchedProductId: match.productId,
    };
  });
  const { inserted, updated, errors } = bulkUpsertOrders(username, dbItems);
  return { fetched: rows.length, inserted, updated, matched: matchedCount, errors, rows };
}
