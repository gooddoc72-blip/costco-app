import type { ShoppingPageData } from '@/lib/services/shopping';
import type { SendResult } from '@/lib/services/messaging';

export type { ShoppingPageData };

export async function fetchShopping(date: string): Promise<ShoppingPageData> {
  const res = await fetch(`/api/shopping?date=${encodeURIComponent(date)}`);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '조회 실패');
  return json;
}

export interface ShoppingSendResponse extends SendResult { msgLength: number }
export async function sendShopping(date: string): Promise<ShoppingSendResponse> {
  const res = await fetch('/api/shopping/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ date }),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '발송 실패');
  return json;
}
