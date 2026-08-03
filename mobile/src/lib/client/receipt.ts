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

export interface PhotoCheck {
  file: string;
  provider: string;
  verified: boolean;
  issues: string[];
}
export type PhotoParseSummary = ParseSummary & { checks?: PhotoCheck[] };

/** 업로드 전 긴 변 1568px로 축소 — 폰 원본(3~5MB)을 그대로 올리면 판독이 느리고 비싸다. */
async function shrinkForAi(file: File, maxEdge = 1568): Promise<Blob> {
  if (!file.type.startsWith('image/')) return file;
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
    if (scale >= 1) return file;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) return file;
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob: Blob | null = await new Promise(res => canvas.toBlob(res, 'image/jpeg', 0.85));
    return blob && blob.size > 0 ? blob : file;
  } catch {
    return file;   // 축소 실패해도 원본으로 계속 (판독은 되게)
  }
}

export async function parseReceiptPhotos(files: File[]): Promise<PhotoParseSummary> {
  const fd = new FormData();
  for (const f of files) {
    const blob = await shrinkForAi(f);
    fd.append('files', blob, f.name || 'receipt.jpg');
  }
  const res = await fetch('/api/receipt/parse-photo', { method: 'POST', body: fd });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '판독 실패');
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
