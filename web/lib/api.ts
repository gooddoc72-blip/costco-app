// API 클라이언트 — Streamlit과 동일 세션(sid) 재사용.
//   sid 우선순위: URL ?sid= → localStorage. 요청엔 Authorization: Bearer 로 첨부.
//   API는 동일 오리진 /api/* (nginx가 uvicorn:8610 로 프록시).

export type ProfitRow = {
  order_no: string;
  recipient: string;
  product_name: string;
  qty: number;
  settlement_amount: number;
  shipping_fee: number;
  extra_shipping: number;
  settled_shipping: number;
  cost_price: number;
  delivery_cost: number;
  box_cost: number;
  profit: number;
  match_source: string;
  matched_name: string;
  matched_pno: string;
  split_qty: number;
};

export type ProfitSummary = {
  settlement: number;
  settled_shipping: number;
  cost: number;
  delivery: number;
  box: number;
  profit: number;
};

export type ProfitResponse = {
  date: string;
  source: string | null;
  count: number;
  rows: ProfitRow[];
  summary: ProfitSummary;
  saved: boolean;
  note: string;
};

export function getSid(): string {
  if (typeof window === 'undefined') return '';
  try {
    const q = new URL(window.location.href).searchParams.get('sid');
    if (q) {
      window.localStorage.setItem('sid', q);
      return q;
    }
    return window.localStorage.getItem('sid') || '';
  } catch {
    return '';
  }
}

function authHeaders(): Record<string, string> {
  const sid = getSid();
  return sid ? { Authorization: `Bearer ${sid}` } : {};
}

async function handle(res: Response) {
  if (res.status === 401) throw new Error('로그인이 필요합니다 (sid 없음/만료). 앱에서 다시 접속하세요.');
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
  return res.json();
}

export async function getMe(): Promise<{ username: string; is_admin: boolean }> {
  const res = await fetch('/api/me', { headers: authHeaders() });
  return handle(res);
}

export async function getProfit(date: string): Promise<ProfitResponse> {
  const res = await fetch(`/api/profit/${date}`, { headers: authHeaders() });
  return handle(res);
}

export async function saveProfit(date: string, rows: Record<string, unknown>[]) {
  const res = await fetch(`/api/profit/${date}/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ rows }),
  });
  return handle(res);
}

export async function deleteRows(date: string, orderNos: string[]) {
  const res = await fetch(`/api/profit/${date}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ order_nos: orderNos }),
  });
  return handle(res);
}

export function won(n: number): string {
  return (n || 0).toLocaleString('ko-KR') + '원';
}
