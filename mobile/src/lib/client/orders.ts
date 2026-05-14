import type { NormalizedOrder, Platform } from '@/lib/services/orders';

export interface CollectResponse {
  platform: Platform;
  fetched: number;
  inserted: number;
  updated: number;
  matched: number;
  errors: string[];
  rows: NormalizedOrder[];
}

export interface CollectParams {
  platform: Platform;
  hoursBack?: number;
  daysBack?: number;
  statusFilter?: string[];
}

export async function collectOrders(params: CollectParams): Promise<CollectResponse> {
  const res = await fetch('/api/orders/collect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '수집 실패');
  return json;
}
