/**
 * Coupang Wing API Client (TypeScript).
 *
 * Python의 coupang_api.get_orders / dispatch_orders를 TS로 재구현.
 * 인증 방식: CEA algorithm=HmacSHA256 (path + query + timestamp 서명)
 */
import crypto from 'crypto';

const BASE_URL = 'https://api-gateway.coupang.com';

/** CEA 서명 헤더 생성 */
function buildAuthHeader(
  accessKey: string,
  secretKey: string,
  method: string,
  path: string,
  query: string
): Record<string, string> {
  // ISO8601 형식 (YYMMDDTHHmmssZ)
  const now = new Date().toISOString().replace(/[-:.]/g, '').slice(2, 15) + 'Z';
  const timestamp = now;
  const message = timestamp + method.toUpperCase() + path + (query || '');
  const signature = crypto
    .createHmac('sha256', secretKey)
    .update(message)
    .digest('hex');
  return {
    'Authorization':
      `CEA algorithm=HmacSHA256, access-key=${accessKey}, signed-date=${timestamp}, signature=${signature}`,
    'Content-Type': 'application/json',
  };
}

export interface CoupangOrderRow {
  productOrderId: string;  // "orderId-orderItemId" 형식
  recipient: string;
  productName: string;
  productNo: string;
  optionInfo: string;
  qty: number;
  orderAmount: number;
  shippingFee: number;
  settlement: number;
  status: string;
  payDate: string;
}

/** 단일 상태 주문 조회 (페이지네이션) */
async function fetchOrdersByStatus(
  accessKey: string, secretKey: string, vendorId: string,
  status: string, dateFrom: string, dateTo: string
): Promise<CoupangOrderRow[]> {
  const path = `/v2/providers/openapi/apis/api/v4/vendors/${vendorId}/ordersheets`;
  const result: CoupangOrderRow[] = [];
  let nextToken: string | undefined;

  // 페이지네이션 최대 50회 (안전장치)
  for (let page = 0; page < 50; page++) {
    const params = new URLSearchParams({
      createdAtFrom: dateFrom,
      createdAtTo: dateTo,
      status,
      maxPerPage: '50',
    });
    if (nextToken) params.set('nextToken', nextToken);
    const queryString = params.toString();
    const headers = buildAuthHeader(accessKey, secretKey, 'GET', path, queryString);

    const res = await fetch(`${BASE_URL}${path}?${queryString}`, { headers });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Coupang API [${res.status}]: ${text.slice(0, 300)}`);
    }
    const body = await res.json();
    if (String(body.code) !== '200') {
      throw new Error(`Coupang 응답 오류: ${body.message || JSON.stringify(body).slice(0, 200)}`);
    }
    const data = (body.data || []) as any[];

    for (const order of data) {
      const receiver = order.receiver || {};
      const recvName = receiver.name || order.orderer?.name || '-';
      const status = order.status || '';
      const orderShip = parseInt(order.shippingPrice) || 0;
      const items = (order.items || []) as any[];

      items.forEach((item, idx) => {
        if (item.cancelType) return;  // 취소 아이템 제외
        const qty = parseInt(item.quantity) || 1;
        const unitPrice = parseInt(item.orderPrice) || 0;
        const settlement = parseInt(item.shippingCountPriceWithCommission) || 0;
        const shipFee = idx === 0 ? orderShip : 0;
        result.push({
          productOrderId: `${order.orderId}-${item.orderItemId}`,
          recipient: recvName,
          productName: item.vendorItemName || '',
          productNo: String(item.sellerProductId || ''),
          optionInfo: item.externalVendorSkuCode || '',
          qty,
          orderAmount: unitPrice * qty,
          shippingFee: shipFee,
          settlement,
          status,
          payDate: order.orderedAt || order.paidAt || '',
        });
      });
    }

    nextToken = body.nextToken;
    if (!nextToken || data.length === 0) break;
  }

  return result;
}

/** 최근 N일 쿠팡 주문 조회 (status filter 지원) */
export async function fetchCoupangOrders(
  accessKey: string, secretKey: string, vendorId: string,
  daysBack: number = 7,
  statuses: string[] = ['ACCEPT', 'INSTRUCT']  // 결제완료 + 상품준비중
): Promise<CoupangOrderRow[]> {
  if (!accessKey || !secretKey || !vendorId) {
    throw new Error('쿠팡 API 키가 설정되어 있지 않습니다.');
  }
  const today = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.now() - daysBack * 86400_000).toISOString().slice(0, 10);

  const all: CoupangOrderRow[] = [];
  for (const status of statuses) {
    const rows = await fetchOrdersByStatus(accessKey, secretKey, vendorId, status, from, today);
    all.push(...rows);
  }

  // productOrderId 중복 제거
  const seen = new Set<string>();
  return all.filter(r => {
    if (seen.has(r.productOrderId)) return false;
    seen.add(r.productOrderId);
    return true;
  });
}
