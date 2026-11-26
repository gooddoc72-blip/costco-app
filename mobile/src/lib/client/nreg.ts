/**
 * 📷 사진으로 네이버 신상품 등록 API 클라이언트 — 파이썬 API(/api/nreg/*).
 * 분석·판매가·등록은 전부 파이썬(photo_register_service)이 한다 — PC 사진등록과 같은 함수.
 */
import { call } from './billing';

export interface NregQuota { limit: number; used: number; remaining: number | null; blocked: boolean }
export interface NregStatus {
  has_key: boolean; has_ai: boolean; margin: number; ship_cost: number; box_cost: number;
  customer_ship: number; delivery: string; quota: NregQuota;
}
export type Food = Record<string, string>;
export interface NregPreview {
  name: string; name_raw: string; cost: number; cost_tag: number; cost_db: number;
  cat_id: string; cat_full: string; cat_src: string;
  brand: string; manufacturer: string; model_name: string; volume: string;
  costco_no: string; barcode: string; food: Food | null; seo_front: string[];
  margin: number; ship_cost: number; box_cost: number; customer_ship: number; delivery: string;
  sale: number;
  /** 한글표시사항 표 [[항목, 값]] — costco: 코스트코 사이트, label: 라벨 사진 판독 */
  spec_rows: [string, string][]; spec_src: '' | 'costco' | 'label';
  desc: string; desc_err: string; tags: Tag[]; tags_err: string; label_err: string;
}
export interface Tag { code: number | null; text: string; volume?: number }
export interface NregResult {
  origin_no: string; name: string; sale: number; images: number; tags: number;
  price_msg: string; warning: string;
}
export interface Delivery {
  ship_cost: number; fee_type: string; base_fee: number; repeat_quantity: number;
  free_conditional_amount: number; return_fee: number; exchange_fee: number; company: string;
}
export type Benefits = Record<string, number>;
export interface NregPresets {
  delivery: Delivery; benefits: Benefits;
  fee_types: { code: string; label: string }[];
  companies: { code: string; label: string }[];
  benefit_keys: { key: string; label: string }[];
}
export interface Job<T> { status: 'running' | 'ok' | 'fail'; msg: string; result: T | null }
export interface Img { name: string; content_b64: string }

const B = '/api/nreg';
export const nreg = {
  status: () => call<NregStatus>('GET', `${B}/status`),
  analyze: (prod: Img, price: Img, label?: Img | null) =>
    call<{ job: string }>('POST', `${B}/analyze`, { prod, price, label: label || null }),
  price: (p: { cost: number; margin: number; ship_cost: number; box_cost: number; customer_ship: number; sale?: number }) =>
    call<{ sale: number; margin: number; margin_rate: number }>('POST', `${B}/price`, p),
  presets: () => call<NregPresets>('GET', `${B}/presets`),
  savePreset: (kind: 'delivery' | 'benefit', value: Delivery | Benefits) =>
    call<{ value: Delivery | Benefits }>('POST', `${B}/presets/${kind}`, { value }),
  categories: (q: string) =>
    call<{ items: { id: string; full_name: string }[] }>('GET', `${B}/categories?q=${encodeURIComponent(q)}`),
  register: (body: Record<string, unknown>) => call<{ job: string }>('POST', `${B}/register`, body),
  job: <T>(id: string) => call<Job<T>>('GET', `${B}/job/${id}`),
  desc: (image: Img, name: string, cat_full: string) => call<{ job: string }>('POST', `${B}/desc`, { image, name, cat_full }),
  label: (image: Img) => call<{ job: string }>('POST', `${B}/label`, { image }),
  spec: (costco_no: string) => call<{ rows: [string, string][] }>('GET', `${B}/spec/${encodeURIComponent(costco_no)}`),
  tags: (name: string, cat_full: string) => call<{ job: string }>('POST', `${B}/tags`, { name, cat_full }),
};

/** 작업이 끝날 때까지 2초마다 묻는다. 진행 문구는 onMsg로. */
export async function waitJob<T>(id: string, onMsg: (m: string) => void): Promise<T> {
  for (;;) {
    await new Promise(r => setTimeout(r, 2000));
    const j = await nreg.job<T>(id);
    if (j.status === 'running') { onMsg(j.msg); continue; }
    if (j.status === 'fail') throw new Error(j.msg);
    return j.result as T;
  }
}
