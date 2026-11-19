/**
 * 사진으로 네이버 등록 API 클라이언트 — 파이썬 API(/api/nreg/*).
 * 판매가 계산·카테고리·등록은 전부 파이썬(naver_register_service)이 한다.
 */
import { call } from './billing';

export interface NregQuota { limit: number; used: number; remaining: number | null; blocked: boolean }
export interface NregPreview {
  product_no: string; name: string; image: string; cost: number; split_qty: number;
  margin: number; sale_price: number; registered: string; has_image: boolean;
}
export interface NregJob { status: 'running' | 'ok' | 'fail'; msg: string; origin_no: string }

const B = '/api/nreg';
export const nreg = {
  status: () => call<{ has_key: boolean; quota: NregQuota }>('GET', `${B}/status`),
  preview: (code: string) => call<NregPreview>('GET', `${B}/preview/${encodeURIComponent(code)}`),
  register: (product_no: string, sale_price: number) =>
    call<{ job: string }>('POST', `${B}/register`, { product_no, sale_price }),
  job: (id: string) => call<NregJob>('GET', `${B}/job/${id}`),
};
