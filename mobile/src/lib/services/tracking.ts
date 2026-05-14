/**
 * Tracking Service — 송장 로그 일괄 등록.
 * order_history에서 메타 보강 후 dispatch_log upsert.
 */
import { fetchOrderMeta, upsertDispatchLogs, type DispatchLogInput } from '@/lib/repositories/dispatch';

export interface BulkLogItem {
  orderNo: string;
  trackingNo?: string;
  courier?: string;
  platform?: string;
}

export function logBulkDispatch(
  username: string,
  items: BulkLogItem[],
  dispatchDate: string
): { saved: number; errors: string[] } {
  // order_history에서 메타 보강
  const enriched: DispatchLogInput[] = items.map(it => {
    const meta = fetchOrderMeta(username, it.orderNo);
    return {
      orderNo: it.orderNo,
      dispatchedAt: dispatchDate,
      recipient: meta.recipient,
      productName: meta.productName,
      expectedSettlement: meta.settlement,
      customerShippingFee: meta.shippingFee,
      trackingNo: it.trackingNo,
      courier: it.courier,
      platform: it.platform || 'naver',
    };
  });
  return upsertDispatchLogs(username, enriched);
}
