import type { ReceiptItem, ParseSummary, ApplyResult } from '@/lib/services/receipt';

export type { ReceiptItem, ParseSummary, ApplyResult };

export async function parseReceipts(files: File[]): Promise<ParseSummary> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  const res = await fetch('/api/receipt/parse', { method: 'POST', body: fd });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '파싱 실패');
  return json;
}

export async function applyReceipts(items: ReceiptItem[]): Promise<ApplyResult> {
  const res = await fetch('/api/receipt/apply', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '저장 실패');
  return json;
}
