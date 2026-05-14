/**
 * Orders Service — Naver API 호출 + order_history 저장.
 */
import { fetchRecentOrders, type NaverOrderRow } from '@/lib/api/naver';
import { fetchCoupangOrders, type CoupangOrderRow } from '@/lib/api/coupang';
import { bulkUpsertOrders, type OrderHistoryInput } from '@/lib/repositories/orders';
import { loadUserSettings } from '@/lib/services/settings';
import { findMatchingProductId } from '@/lib/services/matching';

export type Platform = 'naver' | 'coupang';

/** 공통 주문 행 구조 (플랫폼별 normalize 후) */
export interface NormalizedOrder {
  orderNo: string;
  payDate: string;
  recipient: string;
  productName: string;
  productNo: string;
  optionInfo: string;
  qty: number;
  orderAmount: number;
  shippingFee: number;
  settlement: number;
  status: string;
}

export interface CollectResult {
  platform: Platform;
  fetched: number;
  inserted: number;
  updated: number;
  matched: number;
  errors: string[];
  rows: NormalizedOrder[];
}

/** 정규화된 주문 리스트 → 매칭 + DB 저장 (공통 헬퍼) */
function saveNormalized(
  username: string,
  rows: NormalizedOrder[],
  platform: Platform
): Omit<CollectResult, 'rows'> {
  let matchedCount = 0;
  const dbItems: OrderHistoryInput[] = rows.map(r => {
    const match = findMatchingProductId(username, {
      productNo: r.productNo,
      productName: r.productName,
    });
    if (match.productId) matchedCount++;
    return {
      orderNo: r.orderNo,
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
  return { platform, fetched: rows.length, inserted, updated, matched: matchedCount, errors };
}

/** 네이버 주문 수집 */
export async function collectNaverOrders(
  username: string,
  hoursBack: number = 48,
  statusFilter?: string[]
): Promise<CollectResult> {
  const settings = loadUserSettings(username);
  if (!settings.naverApiClientId || !settings.naverApiClientSecret) {
    throw new Error('네이버 API 키가 설정되어 있지 않습니다. ⚙️ 설정에서 입력해주세요.');
  }
  const raw = await fetchRecentOrders(
    settings.naverApiClientId, settings.naverApiClientSecret, hoursBack, statusFilter
  );
  const rows: NormalizedOrder[] = raw.map(r => ({
    orderNo: r.productOrderId,
    payDate: r.payDate,
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
  return { ...saveNormalized(username, rows, 'naver'), rows };
}

/** 쿠팡 주문 수집 */
export async function collectCoupangOrders(
  username: string,
  daysBack: number = 7,
  statuses: string[] = ['ACCEPT', 'INSTRUCT']
): Promise<CollectResult> {
  const settings = loadUserSettings(username);
  if (!settings.coupangAccessKey || !settings.coupangSecretKey || !settings.coupangVendorId) {
    throw new Error('쿠팡 API 키가 설정되어 있지 않습니다. ⚙️ 설정에서 입력해주세요.');
  }
  const raw = await fetchCoupangOrders(
    settings.coupangAccessKey, settings.coupangSecretKey, settings.coupangVendorId,
    daysBack, statuses
  );
  const rows: NormalizedOrder[] = raw.map(r => ({
    orderNo: r.productOrderId,
    payDate: r.payDate,
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
  return { ...saveNormalized(username, rows, 'coupang'), rows };
}
