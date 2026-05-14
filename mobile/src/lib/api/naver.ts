/**
 * Naver Commerce API Client (TypeScript).
 *
 * Python의 naver_api.get_token / get_new_orders 를 TS로 재구현.
 *  - OAuth 토큰 발급 (bcrypt + base64)
 *  - last-changed-statuses 로 변경 주문번호 목록
 *  - product-orders/query 로 상세 조회 (300건씩 청킹)
 */
import bcrypt from 'bcrypt';

const API_BASE = 'https://api.commerce.naver.com';

interface TokenCacheEntry {
  token: string;
  expiresAt: number; // ms
}
const _tokenCache = new Map<string, TokenCacheEntry>();

/** OAuth 토큰 발급 (1회/시간 캐시) */
export async function getNaverToken(clientId: string, clientSecret: string): Promise<string> {
  const cached = _tokenCache.get(clientId);
  if (cached && cached.expiresAt > Date.now() + 60_000) return cached.token;

  const timestamp = Date.now().toString();
  const pwd = `${clientId}_${timestamp}`;
  // 핵심: client_secret 자체가 bcrypt salt 형식 ($2a$10$...) — 그것을 salt로 사용
  const hashed = await bcrypt.hash(pwd, clientSecret);
  const sig = Buffer.from(hashed, 'utf-8').toString('base64');

  const form = new URLSearchParams({
    client_id: clientId,
    timestamp,
    grant_type: 'client_credentials',
    client_secret_sign: sig,
    type: 'SELF',
  });

  const res = await fetch(`${API_BASE}/external/v1/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`Naver 토큰 실패 [${res.status}]: ${errText.slice(0, 200)}`);
  }
  const data = await res.json() as { access_token: string; expires_in: number };
  _tokenCache.set(clientId, {
    token: data.access_token,
    expiresAt: Date.now() + (data.expires_in || 3000) * 1000,
  });
  return data.access_token;
}

export interface NaverOrderRow {
  productOrderId: string;
  orderId: string;
  recipient: string;
  productName: string;
  productNo: string;          // 판매자 입력 상품번호 (코스트코 번호)
  naverOriginPno: string;     // 네이버 원상품번호 (응답에 있으면)
  naverChannelPno: string;    // 네이버 채널 상품번호 (스마트스토어 productId)
  optionInfo: string;
  qty: number;
  orderAmount: number;
  shippingFee: number;
  settlement: number;
  status: string;
  payDate: string;
}

/** 변경된 productOrderId 목록 조회 (1단계) */
async function fetchChangedOrderIds(
  token: string, fromIso: string
): Promise<string[]> {
  const url = `${API_BASE}/external/v1/pay-order/seller/product-orders/last-changed-statuses`;
  const params = new URLSearchParams({
    lastChangedFromDate: fromIso,
    lastChangedType: 'PRODUCT_ORDER',
  });
  const res = await fetch(`${url}?${params}`, {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`changed-statuses 실패: ${res.status}`);
  const data = await res.json();
  const list = data?.data?.lastChangeStatuses || [];
  return list
    .map((it: any) => String(it.productOrderId))
    .filter((id: string) => id);
}

/** productOrderIds로 상세 조회 (2단계, 300건씩 청킹) */
async function fetchOrderDetails(
  token: string, orderIds: string[]
): Promise<NaverOrderRow[]> {
  if (orderIds.length === 0) return [];
  const url = `${API_BASE}/external/v1/pay-order/seller/product-orders/query`;
  const headers = {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
  const result: NaverOrderRow[] = [];
  for (let i = 0; i < orderIds.length; i += 300) {
    const chunk = orderIds.slice(i, i + 300);
    const res = await fetch(url, {
      method: 'POST', headers,
      body: JSON.stringify({ productOrderIds: chunk }),
    });
    if (!res.ok) continue;
    const data = await res.json();
    for (const item of (data?.data || [])) {
      const po = item.productOrder || {};
      const od = item.order || {};
      const total = parseInt(po.totalPaymentAmount || po.totalProductAmount || 0);
      const np = parseInt(po.naverPayCommission || 0);
      const sales = parseInt(po.salesCommission || 0);
      const expectedSettle = parseInt(po.expectedSettlementAmount || po.settleAmount || (total - np - sales) || 0);
      // 네이버 응답 필드명이 버전마다 다름 — 방어적으로 모두 시도
      const channelPno = String(po.productId || po.channelProductNo || '');
      const originPno  = String(po.originProductNo || po.originProductId || '');
      // 판매자가 입력한 상품번호(코스트코) — productNo 또는 sellerProductCode 등
      const sellerNo   = String(po.productNo || po.sellerProductCode || '');
      result.push({
        productOrderId: String(po.productOrderId || ''),
        orderId: String(od.orderId || ''),
        recipient: po.shippingAddress?.name || od.ordererName || '-',
        productName: po.productName || '',
        productNo: sellerNo,
        naverOriginPno: originPno,
        naverChannelPno: channelPno,
        optionInfo: po.productOption || '',
        qty: parseInt(po.quantity || 1),
        orderAmount: parseInt(po.totalProductAmount || total || 0),
        shippingFee: parseInt(po.totalDeliveryFee || po.deliveryFeeAmount || 0),
        settlement: expectedSettle,
        status: po.productOrderStatus || '',
        payDate: po.paymentDate || od.paymentDate || '',
      });
    }
  }
  return result;
}

/** 최근 N시간 내 변경된 주문 fetch (status 필터링) */
export async function fetchRecentOrders(
  clientId: string, clientSecret: string,
  hoursBack: number,
  statusFilter?: string[]
): Promise<NaverOrderRow[]> {
  const token = await getNaverToken(clientId, clientSecret);
  const from = new Date(Date.now() - hoursBack * 3600_000);
  // ISO with +09:00 (KST)
  const fromIso = new Date(from.getTime() + 9 * 3600_000)
    .toISOString().replace('Z', '+09:00');

  const ids = await fetchChangedOrderIds(token, fromIso);
  const orders = await fetchOrderDetails(token, ids);
  if (!statusFilter || statusFilter.length === 0) return orders;
  const set = new Set(statusFilter);
  return orders.filter(o => set.has(o.status));
}
