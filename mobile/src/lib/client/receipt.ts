import { withBase } from '@/lib/basePath';
import { call } from './billing';
import type { ReceiptItem, ParseSummary, ApplyResult } from '@/lib/services/receipt';

export type { ReceiptItem, ParseSummary, ApplyResult };

export async function parseReceipts(files: File[]): Promise<ParseSummary> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  const res = await fetch(withBase('/api/receipt/parse'), { method: 'POST', body: fd });
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

/**
 * 업로드 전 긴 변 3200px JPEG로 축소. 폰 원본(3~5MB)을 그대로 올리면 느리다.
 * ⚠️ 전에는 1568px로 줄였는데, 그러면 세로로 긴 영수증의 글자가 판독 한계 아래로
 *    작아지고 서버의 '세로 조각 판독'(긴 사진만 켜짐)도 꺼져 오독이 잦았다.
 */
async function shrinkForAi(file: File, maxEdge = 3200): Promise<Blob> {
  if (!file.type.startsWith('image/')) return file;
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
    if (scale >= 1 && file.type === 'image/jpeg') return file;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) return file;
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob: Blob | null = await new Promise(res => canvas.toBlob(res, 'image/jpeg', 0.9));
    return blob && blob.size > 0 ? blob : file;
  } catch {
    return file;   // 축소 실패(HEIC 등)해도 원본으로 — 서버가 JPEG로 바꾼다
  }
}

function blobToB64(b: Blob): Promise<string> {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(',')[1] || '');
    r.onerror = () => bad(r.error);
    r.readAsDataURL(b);
  });
}

/**
 * 📷 영수증 사진 판독 — 영수증 정산과 **같은 파이썬 판독기**(/api/rphoto)를 쓴다.
 * 사진 한 장에 수십 초~2분이라 뒤에서 돌리고 작업 상태를 2초마다 본다.
 * (전에는 Next.js 안의 따로 만든 TS 판독기가 읽었다 — 파이썬 쪽 고침이 하나도 안 들어가 오독이 잦았다)
 */
export async function parseReceiptPhotos(files: File[], onLog?: (line: string) => void): Promise<PhotoParseSummary> {
  const up: { name: string; content_b64: string }[] = [];
  for (const f of files) {
    up.push({ name: f.name || 'receipt.jpg', content_b64: await blobToB64(await shrinkForAi(f)) });
  }
  const { job } = await call<{ job: string }>('POST', '/api/rphoto/parse', { files: up });
  const until = Date.now() + 15 * 60_000;
  while (Date.now() < until) {
    await new Promise(r => setTimeout(r, 2000));
    const st = await call<{ running: boolean; lines: string[]; result: PhotoParseSummary | null; error: string }>(
      'GET', `/api/rphoto/job/${job}`);
    if (onLog && st.lines.length) onLog(st.lines[st.lines.length - 1]);
    if (!st.running) {
      if (st.error) throw new Error(st.error);
      if (!st.result) throw new Error('판독 결과가 없습니다');
      return st.result;
    }
  }
  throw new Error('판독이 15분 넘게 끝나지 않았습니다 — 사진 수를 줄여 다시 올려 주세요');
}

export async function applyReceipts(items: ReceiptItem[]): Promise<ApplyResult> {
  const res = await fetch(withBase('/api/receipt/apply'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '저장 실패');
  return json;
}
