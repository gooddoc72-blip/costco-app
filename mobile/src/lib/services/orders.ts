/**
 * Orders Service — Naver API 호출 + order_history 저장.
 */
import { fetchRecentOrders, type NaverOrderRow } from '@/lib/api/naver';
import { bulkUpsertOrders, type OrderHistoryInput } from '@/lib/repositories/orders';
import { loadUserSettings } from '@/lib/services/settings';

export interface CollectResult {
  fetched: number;
  inserted: number;
  updated: number;
  errors: string[];
  rows: NaverOrderRow[];
}

/** 네이버 API 호출 → order_history upsert */
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
  // Naver row → DB row 매핑
  const dbItems: OrderHistoryInput[] = rows.map(r => ({
    orderNo: r.productOrderId,
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
  }));
  const { inserted, updated, errors } = bulkUpsertOrders(username, dbItems);
  return { fetched: rows.length, inserted, updated, errors, rows };
}
