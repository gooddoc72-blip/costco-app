/**
 * Profit Client API — 브라우저에서 호출하는 wrapper.
 */
import type { ProfitRow, Settings, PriceSaveItem } from '@/lib/types';

export interface ProfitFetchResult {
  rows: ProfitRow[];
  settings: Settings;
  date: string;
}

export async function fetchProfit(date: string): Promise<ProfitFetchResult> {
  const res = await fetch(`/api/profit/${date}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function savePrices(items: PriceSaveItem[]): Promise<{ saved: number; errors: string[] }> {
  const res = await fetch('/api/products/save-prices', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '저장 실패');
  return json;
}
