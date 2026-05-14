import type {
  LatestRow, CheckSummary, DayRank, HistoryPoint,
} from '@/lib/services/rankCheck';
import type { TrackingRow } from '@/lib/repositories/rankTracking';

export type { LatestRow, CheckSummary, DayRank, HistoryPoint };

export interface RankPageData { rows: LatestRow[]; hasApiKey: boolean }

export interface MonthlyData {
  tracking: TrackingRow | null;
  days: Record<number, DayRank>;
  year: number;
  month: number;
}

export async function fetchMonthly(id: number, year: number, month: number): Promise<MonthlyData> {
  const res = await fetch(`/api/rank/${id}/monthly?year=${year}&month=${month}`);
  const j = await res.json();
  if (!res.ok) throw new Error(j.error || '조회 실패');
  return j;
}

export async function fetchYearly(id: number): Promise<{ history: HistoryPoint[] }> {
  const res = await fetch(`/api/rank/${id}/yearly`);
  const j = await res.json();
  if (!res.ok) throw new Error(j.error || '조회 실패');
  return j;
}

export async function fetchRanks(): Promise<RankPageData> {
  const res = await fetch('/api/rank');
  const j = await res.json();
  if (!res.ok) throw new Error(j.error || '조회 실패');
  return j;
}

export async function createKeyword(p: {
  productKeyword: string; searchKeyword: string;
  naverProductNo?: string; storeName?: string;
}): Promise<{ id: number }> {
  const res = await fetch('/api/rank', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  });
  const j = await res.json();
  if (!res.ok) throw new Error(j.error || '추가 실패');
  return j;
}

export async function deleteKeyword(id: number): Promise<void> {
  const res = await fetch(`/api/rank/${id}`, { method: 'DELETE' });
  const j = await res.json();
  if (!res.ok) throw new Error(j.error || '삭제 실패');
}

export async function checkOne(id: number): Promise<{
  rankWonbu: number | null; rankCompare: number | null; rankSolo: number | null;
  matchInfo?: string;
}> {
  const res = await fetch(`/api/rank/${id}/check`, { method: 'POST' });
  const j = await res.json();
  if (!res.ok) throw new Error(j.error || '체크 실패');
  return j;
}

export async function checkAll(): Promise<CheckSummary> {
  const res = await fetch('/api/rank/check-all', { method: 'POST' });
  const j = await res.json();
  if (!res.ok) throw new Error(j.error || '체크 실패');
  return j;
}
