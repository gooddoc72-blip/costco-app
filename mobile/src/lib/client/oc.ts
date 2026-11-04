/**
 * 일일 주문 수집 API 클라이언트 — 파이썬 API(/api/oc/*)를 부른다.
 * 네이버·쿠팡·카페24 수집과 저장은 전부 파이썬(order_collect_service → Streamlit과 같은
 * 수집기·저장 함수)이 한다. 수집은 오래 걸려 작업 번호를 받아 진행을 본다.
 */
import { call } from './billing';

export type Platform = 'naver' | 'coupang' | 'cafe24';

export interface OcStatus {
  naver: boolean; coupang: boolean; cafe24: boolean; cafe24_auth: boolean;
  cutoff_hour: number | null; last_sync: string; shipping_cost: number; box_cost: number;
  excel_password_set: boolean;
}
export interface OcResult {
  warnings: string[]; errors: string[]; fetched: number; order_nos: string[];
  in_range?: number; prev?: number; prev_included?: boolean; after?: number; deferred_cut?: number;
  excluded?: number; address_filled?: number; sync_cleared?: number; history?: number;
  fee_updates?: number; sale_updates?: number; status_dist?: Record<string, number>;
  debug?: { status: string; count: number; error: string; from: string; to: string }[];
}
export interface OcJob<T> { running: boolean; lines: string[]; result: T | null; error: string; started: string }
export interface OcRow {
  order_no: string; platform: 'naver' | 'coupang'; recipient: string; buyer: string; product_name: string;
  product_no: string; option_info: string; qty: number; amount: number; shipping_fee: number;
  settlement: number; pay_day: string; ordered_at: string; status: string; cost: number; split_qty: number;
}
export interface OcActive { rows: OcRow[]; deferred_cut: number; cutoff_hour: number | null; shipped_today: boolean }

const B = '/api/oc';
export const oc = {
  status: () => call<OcStatus>('GET', `${B}/status`),
  collect: (p: { platform: Platform; date_from: string; date_to: string; status: string; incl_prev: boolean; incl_after_cutoff: boolean }) =>
    call<{ job: string }>('POST', `${B}/collect`, p),
  excel: (files: { name: string; content_b64: string }[], password: string, order_date: string, incl_after_cutoff: boolean) =>
    call<{ job: string }>('POST', `${B}/excel`, { files, password, order_date, incl_after_cutoff }),
  job: <T,>(job: string) => call<OcJob<T>>('GET', `${B}/job/${job}`),
  active: (incl_after_cutoff: boolean) => call<OcActive>('GET', `${B}/active?incl_after_cutoff=${incl_after_cutoff ? 1 : 0}`),
  save: (order_date: string, order_nos: string[] | null, incl_after_cutoff: boolean) =>
    call<{ orders: number; carried: number }>('POST', `${B}/save`, { order_date, order_nos, incl_after_cutoff }),
  sync: () => call<{ job: string }>('POST', `${B}/sync`),
  exclude: (order_nos: string[], also_daily: boolean) =>
    call<{ deleted: number; daily_deleted: number }>('POST', `${B}/exclude`, { order_nos, also_daily }),
  excluded: () => call<{ rows: { order_no: string; excluded_at: string; reason: string }[] }>('GET', `${B}/excluded`),
  restore: (order_nos: string[]) => call<{ restored: number }>('POST', `${B}/restore`, { order_nos }),
  search: (q: string, product: string, date_from: string, date_to: string) =>
    call<{ rows: Record<string, any>[] }>('GET', `${B}/search?q=${encodeURIComponent(q)}&product=${encodeURIComponent(product)}&date_from=${date_from}&date_to=${date_to}`),
  dailyDates: () => call<{ dates: { date: string; count: number }[] }>('GET', `${B}/daily`),
  daily: (d: string) => call<{ rows: { order_no: string; recipient: string; product_name: string; option_info: string; qty: number; order_amount: number; settlement: number; cost_price: number }[] }>('GET', `${B}/daily/${d}`),
  dailyDelete: (order_date: string, order_nos: string[] | null) =>
    call<{ deleted: number }>('POST', `${B}/daily/delete`, { order_date, order_nos }),
};

/** 작업이 끝날 때까지 1.5초마다 본다. onLine으로 진행 문구를 넘긴다. */
export async function waitJob<T>(job: string, onLine?: (s: string) => void): Promise<T> {
  for (;;) {
    await new Promise(r => setTimeout(r, 1500));
    const st = await oc.job<T>(job);
    if (onLine && st.lines.length) onLine(st.lines[st.lines.length - 1]);
    if (!st.running) {
      if (st.error) throw new Error(st.error);
      return st.result as T;
    }
  }
}

/** 파일 → base64 (data: 머리 없이) */
export function fileB64(f: File): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(',')[1] || '');
    r.onerror = () => rej(r.error);
    r.readAsDataURL(f);
  });
}
