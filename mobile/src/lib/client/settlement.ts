import type {
  SettlementPageData, UploadResult,
} from '@/lib/services/settlement';

export type { SettlementPageData, UploadResult };

export async function uploadSettlementCsv(file: File): Promise<UploadResult> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/settlement/upload-csv', { method: 'POST', body: fd });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '업로드 실패');
  return json;
}

export async function fetchMatch(
  settleDate: string, shipDate: string,
): Promise<SettlementPageData> {
  const url = `/api/settlement/match?settleDate=${settleDate}&shipDate=${shipDate}`;
  const res = await fetch(url);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '조회 실패');
  return json;
}

export async function deleteSettleDate(settleDate: string): Promise<{ removed: number }> {
  const res = await fetch(`/api/settlement/match?settleDate=${settleDate}`, { method: 'DELETE' });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '삭제 실패');
  return json;
}
