/**
 * 정산·청구 API 클라이언트 — 파이썬 API(/api/billing/*)를 부른다.
 *
 * ⚠️ withBase를 쓰지 않는다. /m 아래 Next 라우트가 아니라 nginx가 /api/ 를
 *    FastAPI로 넘긴다. 돈 계산은 전부 파이썬(db_settle·db_deposit) 쪽에 있다 —
 *    여기서 금액을 다시 계산하지 않는다.
 *
 * 로그인: /m 로그인 쿠키(sid)를 그대로 쓰고, Streamlit에서 ?sid=로 넘어오면
 *    그 토큰을 Bearer로 보낸다(Streamlit 로그인 그대로 이어서 쓰기).
 */
import { withBase } from '@/lib/basePath';

const TOKEN_KEY = 'billing_sid';

function token(): string {
  if (typeof window === 'undefined') return '';
  const q = new URLSearchParams(window.location.search).get('sid');
  if (q) {
    try { sessionStorage.setItem(TOKEN_KEY, q); } catch { /* 저장 못 해도 이번 요청은 된다 */ }
    return q;
  }
  try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, msg: string) { super(msg); this.status = status; }
}

export async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const t = token();
  const res = await fetch(path, {
    method,
    credentials: 'include',
    cache: 'no-store',
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(t ? { Authorization: `Bearer ${t}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && typeof window !== 'undefined') {
    window.location.href = withBase('/login');
    throw new ApiError(401, '로그인이 필요합니다');
  }
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new ApiError(res.status, (e && (e.detail || e.error)) || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

// ── 타입 ──
export type Status = 'draft' | 'billed' | 'paid';

export interface DayRow {
  username: string; name: string; status: Status;
  item_count: number; total_amount: number; paid_amount: number;
  pay_method: '' | 'deposit' | 'account'; deducted: number; balance: number;
  billed_at: string; paid_at: string; memo: string;
}
export interface DayResp {
  date: string;
  rows: DayRow[];
  summary: { total: number; users: number; draft: number; draft_n: number;
             billed: number; billed_n: number; paid: number; paid_n: number; deposit_n: number };
  recent: { date: string; users: number; total: number; paid: number }[];
}
export interface Item {
  id: number; source: string; product_name: string; product_no: string; qty: number;
  list_price: number; discount_per_pack: number; unit_price: number; amount: number;
  order_no: string; memo: string;
}
export interface ItemsResp { items: Item[]; goods_total: number; invoice_total: number; status: string }

export interface DepRow {
  username: string; name: string; charged: number; spent: number; adjusted: number;
  returned: number; balance: number; billed: number; other_paid: number; last_charge: string;
}
export interface DepResp { rows: DepRow[]; totals: Record<string, number> }
export interface DepHistRow {
  id: number; date: string; kind: string; kind_label: string; amount: number;
  balance: number; settle_date: string; memo: string; by: string;
}
export interface DepHistResp {
  username: string; name: string; balance: number;
  period: { charged: number; spent: number; adjusted: number }; rows: DepHistRow[];
}
export interface UnpaidRow { username: string; name: string; days: number; amount: number; oldest: string; newest: string }

export interface UserInvRow {
  date: string; status: Status; item_count: number; total_amount: number; paid_amount: number;
  pay_method: '' | 'deposit' | 'account'; billed_at: string; paid_at: string; memo: string;
}
export interface UserResp {
  username: string; name: string; date_from: string; date_to: string; rows: UserInvRow[];
  balance: number;
  summary: { total: number; draft: number; billed: number; billed_n: number; paid: number };
}

// ── 호출 ──
const B = '/api/billing';
export const billing = {
  day: (d: string) => call<DayResp>('GET', `${B}/day/${d}`),
  items: (d: string, u: string) => call<ItemsResp>('GET', `${B}/day/${d}/items/${encodeURIComponent(u)}`),
  bill: (d: string, usernames: string[]) =>
    call<{ billed: number; amount: number }>('POST', `${B}/day/${d}/bill`, { usernames }),
  paid: (d: string, rows: { username: string; paid_amount?: number; memo?: string }[]) =>
    call<{ paid: number }>('POST', `${B}/day/${d}/paid`, { rows }),
  unpay: (d: string, username: string) =>
    call<{ ok: boolean; deposit_refunded: { balance: number } | null }>('POST', `${B}/day/${d}/unpay`, { username }),
  deduct: (d: string, usernames: string[]) =>
    call<{ deducted: { username: string; amount: number }[]; amount: number }>('POST', `${B}/day/${d}/deduct`, { usernames }),
  deposits: () => call<DepResp>('GET', `${B}/deposits`),
  depositHistory: (u: string, from = '', to = '') =>
    call<DepHistResp>('GET', `${B}/deposits/${encodeURIComponent(u)}?date_from=${from}&date_to=${to}`),
  charge: (username: string, amount: number, tx_date: string, memo: string) =>
    call<{ ok: boolean; balance: number }>('POST', `${B}/deposits/charge`, { username, amount, tx_date, memo }),
  unpaid: () => call<{ rows: UnpaidRow[]; total: number }>('GET', `${B}/unpaid`),
  users: () => call<{ rows: { username: string; name: string }[] }>('GET', `${B}/users`),
  user: (u: string, from = '', to = '') =>
    call<UserResp>('GET', `${B}/user/${encodeURIComponent(u)}?date_from=${from}&date_to=${to}`),
  userPaid: (u: string, rows: { date: string; paid_amount?: number; memo?: string }[]) =>
    call<{ paid: number; amount: number; skipped: string[] }>('POST', `${B}/user/${encodeURIComponent(u)}/paid`, { rows }),
  unpaidDetail: (u: string) =>
    call<{ rows: { date: string; amount: number; items: number; billed_at: string }[] }>('GET', `${B}/unpaid/${encodeURIComponent(u)}`),
};
