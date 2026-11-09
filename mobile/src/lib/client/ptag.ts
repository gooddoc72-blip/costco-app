/**
 * 가격표 사진 API 클라이언트 — 파이썬 API(/api/ptag/*)를 부른다.
 * 판독(AI)·비교·저장·되돌리기는 전부 파이썬(price_tag_service)이 한다.
 */
import { call } from './billing';

export type PtStatus = 'change' | 'same' | 'suspect' | 'new' | 'no_pno' | 'no_price';

export interface PtRow {
  ok: true; photo: string; product_no: string; barcode: string; name: string; tag_name: string;
  cur_price: number; online_price: number; new_price: number; final_price: number;
  discount: number; sale_end: string; change_pct: number | null; status: PtStatus; provider: string;
}
export interface PtFail { ok: false; photo: string; error: string }
export interface PtBatch {
  batch: string; created_at: string; created_by: string; undone: boolean; count: number;
  rows: { product_no: string; product_name: string; prev_price: number; new_price: number;
          final_price: number; discount: number; sale_end: string; was_new: number }[];
}

const B = '/api/ptag';
export const ptag = {
  read: (name: string, content_b64: string) => call<PtRow | PtFail>('POST', `${B}/read`, { name, content_b64 }),
  lookup: (code: string) =>
    call<{ product_no: string; name: string; cur_price: number; online_price: number }>('GET', `${B}/lookup/${encodeURIComponent(code)}`),
  save: (rows: Pick<PtRow, 'product_no' | 'name' | 'barcode' | 'new_price' | 'final_price' | 'discount' | 'sale_end' | 'photo'>[]) =>
    call<{ batch: string; saved: number; same: number; skipped: { name: string; why: string }[] }>('POST', `${B}/save`, { rows }),
  history: () => call<{ batches: PtBatch[] }>('GET', `${B}/history`),
  undo: (batch: string) => call<{ restored: number; kept_new: number }>('POST', `${B}/undo`, { batch }),
};

/**
 * 사진 → 긴 변 1600px JPEG base64. 폰 사진(4~8MB)을 그대로 보내면 느리고 서버 한도에 걸린다.
 * 아이폰 HEIC도 브라우저가 그릴 수 있으면 여기서 JPEG가 된다.
 */
export async function shrinkJpegB64(f: File, maxEdge = 1600): Promise<string> {
  const url = URL.createObjectURL(f);
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => rej(new Error(`${f.name}: 사진을 열 수 없습니다`));
      i.src = url;
    });
    const k = Math.min(1, maxEdge / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement('canvas');
    c.width = Math.round(img.naturalWidth * k);
    c.height = Math.round(img.naturalHeight * k);
    c.getContext('2d')!.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL('image/jpeg', 0.88).split(',')[1] || '';
  } finally {
    URL.revokeObjectURL(url);
  }
}
