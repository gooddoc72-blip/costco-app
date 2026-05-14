import type { NaverOrderRow } from '@/lib/api/naver';

export interface CollectResponse {
  fetched: number;
  inserted: number;
  updated: number;
  errors: string[];
  rows: NaverOrderRow[];
}

export async function collectOrders(
  hoursBack: number, statusFilter?: string[]
): Promise<CollectResponse> {
  const res = await fetch('/api/orders/collect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hoursBack, statusFilter }),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '수집 실패');
  return json;
}
