/**
 * 영수증 정산 API 클라이언트 — 파이썬 API(/api/rsettle/*)를 부른다.
 * 매칭·금액 계산·정산은 전부 파이썬(receipt_settle_service)이 한다.
 * 작업본은 서버 DB에 저장돼 새로고침·재시작에도 남는다.
 */
import { call } from './billing';

export interface RsRow {
  username: string; name: string; order_no: string; order_date: string;
  product_name: string; costco_no: string; qty: number; unit_price: number; list_price: number;
  amount: number; prev_cost: number; via: string; via_label: string; memo: string;
  stock_owner: string; edited: boolean; flag: string; ok: boolean;
}
export interface RsUser { username: string; name: string; count: number; amount: number; unmatched: number }
export interface RsUnmatched {
  username: string; name: string; order_no: string; recipient: string; product_name: string;
  qty: number; carried_from: string; pre: string;
}
export interface RsMoney {
  receipt_total: number; receipt_bill: number; stock_in: number; returned: number; undecided: number;
  bill_total: number; self_stock: number; other_stock: number; manual: number; online: number;
  owners: { username: string; amount: number }[];
}
export interface RsView {
  date: string; receipt_count: number; matched: boolean; names: Record<string, string>;
  matched_at?: string; updated_at?: string; updated_by?: string;
  rows?: RsRow[]; users?: RsUser[]; unmatched_orders?: RsUnmatched[];
  unmatched_receipt?: { costco_no: string; name: string; price: number; qty: number }[];
  money?: RsMoney; paid_invoices?: { username: string; name: string; amount: number }[];
  late_dispatch?: { username: string; name: string; order_no: string }[];
  undecided?: number; total?: number; notes?: Record<string, number>;
}
export interface RowAction {
  username: string; order_no: string; action: 'ok' | 'unok' | 'stock' | 'cut' | 'edit';
  qty?: number; amount?: number; memo?: string;
}
export interface FinalizeRes {
  saved: number; applied: number; users: number; total: number; dropped: number; learned: number;
  stock: Record<string, number>;
}

const B = '/api/rsettle';
export const rsettle = {
  day: (d: string) => call<RsView>('GET', `${B}/day/${d}`),
  match: (d: string, carry_days = 0) => call<RsView>('POST', `${B}/day/${d}/match`, { carry_days }),
  rows: (d: string, actions: RowAction[]) =>
    call<RsView & { result: { cut: number; edited: number; ok: number } }>('POST', `${B}/day/${d}/rows`, { actions }),
  finalize: (d: string, replace: boolean) => call<FinalizeRes>('POST', `${B}/day/${d}/finalize`, { replace }),
};
