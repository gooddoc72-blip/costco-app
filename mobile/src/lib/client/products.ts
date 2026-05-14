import type { ProductRow } from '@/lib/services/products';

export type { ProductRow };

export async function fetchProducts(q: string, limit: number = 200): Promise<{ rows: ProductRow[]; total: number }> {
  const url = `/api/products/list?q=${encodeURIComponent(q)}&limit=${limit}`;
  const res = await fetch(url);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '제품 조회 실패');
  return json;
}

export async function patchProduct(
  id: number,
  fields: { unitPrice?: number; splitQty?: number; matchKeyword?: string; costcoName?: string }
): Promise<{ saved: boolean }> {
  const res = await fetch(`/api/products/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '수정 실패');
  return json;
}

export async function deleteProductRequest(id: number): Promise<{ deleted: boolean }> {
  const res = await fetch(`/api/products/${id}`, { method: 'DELETE' });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '삭제 실패');
  return json;
}
