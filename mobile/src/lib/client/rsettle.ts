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
  undecided?: number; total?: number; notes?: Record<string, number>; receipt_changed?: string;
  unmatched_detail?: { orders: RsUnmatchedDetail[]; owners: RsOwner[]; error?: string };
  assign?: { rows: RsAssignRow[]; error: string };
}
export interface RsUnmatchedDetail {
  username: string; name: string; order_no: string; recipient: string; product_name: string;
  qty: number; carried_from: string; pre: string; stock_no: string;
  auto_owner: string; auto_amount: number; receipt_price: number;
  stock: { owner: string; name: string; qty: number; cost: number }[];
  candidate: { costco_no: string; name: string; price: number } | null;
}
export interface RsOwner { username: string; name: string; in_ledger: boolean }
export interface RsAssignRow {
  costco_no: string; name: string; price: number; list_price: number; receipt_qty: number;
  packs_left: number; split_qty: number; kind: string; requester: string; requester_name: string;
}
export interface UnmatchedDecision {
  username: string; order_no: string; how: 'stock' | 'amount' | 'link' | 'skip';
  owner?: string; qty?: number; amount?: number; memo?: string;
}
export interface ReceiptRow {
  receipt_date: string; costco_no: string; name: string; qty: number;
  list_price: number; discount: number; unit_price: number;
}
export interface ParseJob { running: boolean; lines: string[]; error: string; started: string;
  result: { rows: ReceiptRow[]; fails: { file: string; error: string }[] } | null }
export interface ReceiptCheck {
  count: number; dropped: number; filled: number; fill_date: string; dates: string[]; qty: number; amount: number;
  outliers: { name: string; qty: number; unit_price: number; amount: number; why: string }[];
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
  unmatched: (d: string, decisions: UnmatchedDecision[]) =>
    call<RsView & { result: { added: number; amount: number; skipped: number; zero: number; no_ledger: string[]; loans: number } }>(
      'POST', `${B}/day/${d}/unmatched`, { decisions }),
  assign: (d: string, mode: 'bill' | 'stock' | 'return', username: string, picks: { costco_no: string; packs: number; memo?: string }[], bill_stock = true) =>
    call<RsView & { result: { mode: string; count: number; amount: number; skipped?: number; failed?: string[]; dupe?: { name: string; owner: string; have: number }[] } }>(
      'POST', `${B}/day/${d}/assign`, { mode, username, picks, bill_stock }),
  receipt: (d: string) => call<{ rows: ReceiptRow[] }>('GET', `${B}/day/${d}/receipt`),
  parseStart: (files: { name: string; content_b64: string }[]) => call<{ job: string }>('POST', `${B}/receipt/parse`, { files }),
  parseStatus: (job: string) => call<ParseJob>('GET', `${B}/receipt/parse/${job}`),
  receiptCheck: (d: string, rows: ReceiptRow[]) => call<ReceiptCheck>('POST', `${B}/day/${d}/receipt/check`, { rows }),
  receiptSave: (d: string, rows: ReceiptRow[]) =>
    call<{ count: number; new: number; updated: number; removed: number; priced: number; price_skip: number; filled: number; fill_date: string; dates: string[] }>(
      'POST', `${B}/day/${d}/receipt/save`, { rows }),
};

/** 파일 → base64 (data: 머리 없이) */
export function fileToB64(f: File): Promise<string> {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(',')[1] || '');
    r.onerror = () => bad(r.error);
    r.readAsDataURL(f);
  });
}
