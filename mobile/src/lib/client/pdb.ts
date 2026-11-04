/**
 * 제품 DB API 클라이언트 — 파이썬 API(/api/pdb/*)를 부른다.
 * 분류·검색·표시값·저장 규칙은 전부 파이썬(product_db_service·db_products)에 있다.
 * Streamlit 제품 DB와 같은 함수라 두 화면의 결과가 같다.
 */
import { call } from './billing';

export type PdbKind = 'focus' | 'all' | 'online' | 'naver' | 'selfreg' | 'split' | 'oos' | 'susp';

export interface PdbRow {
  key: string; shared_id: number | null; private_id: number | null; is_shared: boolean;
  match_keyword: string; name: string; sub_name: string; costco_name: string; image_url: string;
  costco_no: string; costco_no_bad: boolean; naver_no: string; split_locked: boolean;
  unit_price: number; store_price: number; online_price: number; piece_price: number;
  split_qty: number; sale_price: number; shipping_fee: number;
  status: 'sale' | 'oos' | 'susp'; category: string; naver_category_id: string; updated: string;
}
export interface PdbExtra { product_no: string; name: string; paid: number; list: number; receipt_date: string }
export interface PdbList {
  kinds: { key: PdbKind; label: string; count: number }[]; kind: PdbKind;
  categories: { name: string; count: number }[]; category: string;
  total: number; page: number; pages: number; rows: PdbRow[]; extra: PdbExtra[];
}
export interface PdbSave {
  shared_id: number | null; private_id: number | null; match_keyword: string; costco_name: string;
  product_no?: string; unit_price?: number; split_qty: number; sale_price: number; shipping_fee: number;
  naver_category_id?: string;
}
export interface PackRow {
  id: number; name: string; unit_price: number; split_qty: number; name_factor: number;
  applied: number; risky: boolean; cost_now: number;
}
export interface LinkRow { naver_no: string; name: string; costco_no: string; costco_name: string; times: number }
export interface OptRow {
  product_name: string; naver_pno: string; option_info: string; option_code: string;
  cnt: number; current_no: string; costco_no: string;
}
export interface PriceHist {
  id: number; created_at: string; costco_name: string; old_cost: number; new_cost: number;
  diff: number; diff_pct: number; shipping_fee: number; notified: number; naver_updated: number;
}

const qs = (o: Record<string, string | number>) =>
  Object.entries(o).filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&');

export const pdb = {
  me: () => call<{ username: string; is_admin: boolean }>('GET', '/api/stock/me'),
  list: (p: { kind: string; category: string; q: string; page: number; per: number }) =>
    call<PdbList>('GET', `/api/pdb/list?${qs(p)}`),
  save: (row: PdbSave) => call<{ ok: boolean }>('POST', '/api/pdb/save', row),
  remove: (id: number) => call<{ deleted: number }>('POST', '/api/pdb/delete', { id }),
  unlock: (id: number) => call<{ product_no: string }>('POST', '/api/pdb/unlock', { id }),
  pack: () => call<{ rows: PackRow[] }>('GET', '/api/pdb/pack'),
  setPack: (id: number, value: number) => call<{ ok: boolean }>('POST', '/api/pdb/pack', { id, value }),
  links: () => call<{ rows: LinkRow[] }>('GET', '/api/pdb/links'),
  applyLinks: (items: { naver_no: string; costco_no: string }[]) =>
    call<{ linked: number }>('POST', '/api/pdb/links', { items }),
  sellers: () => call<{ rows: { username: string; name: string }[] }>('GET', '/api/pdb/sellers'),
  options: (date_from: string, date_to: string, username: string) =>
    call<{ rows: OptRow[]; dupe_products: number }>('GET', `/api/pdb/options?${qs({ date_from, date_to, username })}`),
  saveOptions: (username: string, items: { naver_pno: string; option_code: string; option_name: string; product_name: string; costco_pno: string }[]) =>
    call<{ saved: number; removed: number }>('POST', '/api/pdb/options', { username, items }),
  priceHistory: () => call<{ rows: PriceHist[] }>('GET', '/api/pdb/price-history'),
  clearPriceHistory: () => call<{ deleted: number }>('POST', '/api/pdb/price-history/clear'),
};
