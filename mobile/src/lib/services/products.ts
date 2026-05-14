/**
 * Products Service — 제품 DB 조회/수정/삭제.
 * 비즈니스 정책: 검색어 trim, limit 보호, 가격 음수 차단.
 */
import {
  listProducts as repoList,
  updateProductById as repoUpdate,
  deleteProductById as repoDelete,
  unlockCostcoNo as repoUnlock,
  type ProductRow,
} from '@/lib/repositories/products';

export type { ProductRow };

export interface ListResult {
  rows: ProductRow[];
  total: number;
}

export function listUserProducts(username: string, search: string, limit: number): ListResult {
  const safeLimit = Math.max(10, Math.min(500, limit));
  const rows = repoList(username, search.trim(), safeLimit);
  return { rows, total: rows.length };
}

export function updateProduct(
  username: string,
  id: number,
  fields: { unitPrice?: number; splitQty?: number; matchKeyword?: string; costcoName?: string }
) {
  if (fields.unitPrice !== undefined && fields.unitPrice < 0) {
    return { saved: false, error: '단가는 0 이상이어야 합니다.' };
  }
  if (fields.splitQty !== undefined && fields.splitQty < 1) {
    return { saved: false, error: '분할수량은 1 이상이어야 합니다.' };
  }
  return repoUpdate(username, id, fields);
}

export function deleteProduct(username: string, id: number) {
  return repoDelete(username, id);
}

export function unlockProductCostcoNo(username: string, id: number) {
  return repoUnlock(username, id);
}
