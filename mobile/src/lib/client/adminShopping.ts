import type { ShoppingItem } from '@/lib/services/shopping';

export interface AdminSubmission {
  id: number;
  username: string;
  orderDate: string;
  submittedAt: string;
  totalItems: number;
  totalAmount: number;
  items: ShoppingItem[];
}

export async function fetchAdminSubmissions(opt: { limit?: number; user?: string } = {})
: Promise<{ submissions: AdminSubmission[] }> {
  const params = new URLSearchParams();
  if (opt.limit) params.set('limit', String(opt.limit));
  if (opt.user) params.set('user', opt.user);
  const res = await fetch(`/api/admin/shopping?${params.toString()}`);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '조회 실패');
  return json;
}

export async function deleteAdminSubmission(id: number): Promise<{ deleted: boolean }> {
  const res = await fetch(`/api/admin/shopping/${id}`, { method: 'DELETE' });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '삭제 실패');
  return json;
}
