/**
 * 재고 조회 API 클라이언트 — 파이썬 API(/api/stock/*)를 부른다. 읽기 전용.
 * 계산은 전부 파이썬(stock_audit)이 한다.
 */
import { call } from './billing';

export interface StMove {
  id: number; lot_id: number; owner: string; seller: string; qty: number; unit_cost: number;
  surcharge: number; is_cross: number; order_no: string; date: string; created_at: string;
  platform: string; how: string; settle_status: string; product_name?: string; received_at?: string;
}
export interface StLot {
  id: number; owner: string; product_no: string; product_name: string; qty_in: number; qty_left: number;
  unit_cost: number; list_price: number; split_qty: number; received_at: string; created_at: string;
  status: string; paid: number; memo: string; age: number; moves: StMove[];
}
export interface StOwner {
  username: string; name: string; items: number; units: number; amount: number;
  unpaid_units: number; old_units: number; pending_credit: number; borrowed: number;
}
export interface StOverview {
  owners: StOwner[]; names: Record<string, string>;
  total: { units: number; amount: number; unpaid_units: number };
}
export interface StCard {
  username: string; name: string; names: Record<string, string>;
  summary: {
    items: number; units: number; amount: number; unpaid_units: number; old_units: number; in_units: number;
    out_by: { platform: string; how: string; qty: number }[];
  };
  lots: StLot[]; used_others: StMove[];
  credits: { tx_date: string; amount: number; memo: string; settle_date: string }[];
}
export interface StSearch {
  q: string; names: Record<string, string>; lots: StLot[]; moves: StMove[];
  settle: { settle_date: string; username: string; order_no: string; product_no: string; product_name: string;
    qty: number; amount: number; source: string; stock_owner: string; memo: string }[];
  deposits: { username: string; tx_date: string; kind: string; amount: number; memo: string }[];
  dispatch: { username: string; order_no: string; date: string; product_name: string; platform: string; status: string }[];
}
export interface StCheck {
  kind: string; level: 'high' | 'mid' | 'low'; title: string; detail: string; owner: string; owner_name: string;
  lot_ids: number[]; fix: string; rows: { order_no: string; date: string; product_name: string }[];
}
export interface StChecks { items: StCheck[]; counts: Record<string, number>; checked_at: string }

const B = '/api/stock';
export const stock = {
  me: () => call<{ username: string; is_admin: boolean }>('GET', `${B}/me`),
  overview: () => call<StOverview>('GET', `${B}/overview`),
  user: (u: string, empty = true) => call<StCard>('GET', `${B}/user/${encodeURIComponent(u)}?empty=${empty ? 1 : 0}`),
  search: (q: string) => call<StSearch>('GET', `${B}/search?q=${encodeURIComponent(q)}`),
  checks: (force = false) => call<StChecks>('GET', `${B}/checks?force=${force ? 1 : 0}`),
};
