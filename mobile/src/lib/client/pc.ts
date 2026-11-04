/**
 * 수익계산 API 클라이언트 — 파이썬 API(/api/pc/*)를 부른다.
 * 구입가·수익 계산은 전부 파이썬(profit_service → Streamlit 수익계산과 같은 함수)이 한다.
 * 화면에서 고친 값은 overrides로 보내 서버가 다시 계산한 숫자만 보여 준다.
 */
import { call } from './billing';

export interface PcRow {
  order_no: string; recipient: string; product_name: string; product_no: string; option_info: string;
  qty: number; order_amount: number; settlement_amount: number; shipping_fee: number; extra_shipping: number;
  settled_shipping: number; cost_price: number; delivery_cost: number; box_cost: number; profit: number;
  match_source: string; matched_name: string; matched_pno: string; split_qty: number;
  saved: boolean; keyword: string; edited: boolean; actual?: boolean;
}
export interface PcSummary {
  settlement: number; shipping_in: number; settled_shipping: number; cost: number; delivery: number;
  box: number; profit: number; costed: number; count: number; nocost: number; nocost_settlement: number;
  income: number; expense: number;
}
export interface PcDay {
  date: string; label: string; saved: boolean; no_ship_day: boolean;
  defaults: { delivery: number; box: number };
  rows: PcRow[]; summary: PcSummary; counts: Record<string, number>; elsewhere: Record<string, string>;
}
export interface PcOverride {
  unit_cost?: number; keyword?: string; receipt_pno?: string; receipt_name?: string; receipt_unit?: number;
  delivery?: number; box?: number;
}
export type PcOverrides = Record<string, PcOverride>;
export interface PcReceipt { pno: string; name: string; unit: number; date: string }

export interface PmSum {
  cnt?: number; qty?: number; sales?: number; settle?: number; ship_in?: number; cost?: number;
  delivery?: number; box?: number; profit?: number; nocost_cnt?: number; nocost_settle?: number;
}
export interface PmIssueRow {
  id: number; date: string; order_no: string; recipient: string; product_name: string; qty: number;
  sales: number; settle: number; cost: number; delivery: number; box: number;
  saved_profit: number; profit_now: number; match: string; codes: string[];
}
export interface PmMonth {
  months: string[]; ym: string; checks: { code: string; title: string; sev: 'high' | 'mid' | 'low' }[];
  summary: PmSum; prev: PmSum;
  days: { date: string; dispatched: number; saved: number; sales: number; settle: number; cost: number; profit: number; issues: number }[];
  top: { name: string; cnt: number; qty: number; sales: number; profit: number }[];
  bottom: { name: string; cnt: number; qty: number; sales: number; profit: number }[];
  rows: PmIssueRow[]; issue_counts: Record<string, number>;
  dup_other?: { id: number; date: string; order_no: string; recipient: string; product_name: string; sales: number; profit_now: number }[];
  dup_dispatch?: Record<string, string>;
}

export const pc = {
  me: () => call<{ username: string; is_admin: boolean }>('GET', '/api/stock/me'),
  day: (date: string) => call<PcDay>('GET', `/api/pc/day/${date}`),
  preview: (date: string, overrides: PcOverrides) =>
    call<PcDay>('POST', `/api/pc/day/${date}/preview`, { overrides }),
  save: (date: string, overrides: PcOverrides, move_dup: boolean, update_price_db: boolean) =>
    call<{ saved: number; note: string; prices: number; links: number }>(
      'POST', `/api/pc/day/${date}/save`, { overrides, move_dup, update_price_db }),
  remove: (date: string, order_nos: string[]) =>
    call<{ deleted: number }>('POST', `/api/pc/day/${date}/delete`, { order_nos }),
  receipts: () => call<{ items: PcReceipt[] }>('GET', '/api/pc/receipts'),
  users: () => call<{ rows: { username: string; name: string }[] }>('GET', '/api/pc/users'),
  month: (ym: string, username: string) =>
    call<PmMonth>('GET', `/api/pc/month?ym=${encodeURIComponent(ym)}&username=${encodeURIComponent(username)}`),
  monthEdit: (username: string, edits: Record<number, Record<string, number>>) =>
    call<{ updated: number }>('POST', '/api/pc/month/edit', { username, edits }),
  monthDelete: (username: string, ids: number[]) =>
    call<{ deleted: number }>('POST', '/api/pc/month/delete', { username, ids }),
};
