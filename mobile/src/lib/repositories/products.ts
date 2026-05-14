/**
 * Products Repository — products 테이블 CRUD.
 * 조회 우선순위 정책 등 비즈니스 결정은 service에서.
 */
import { getUserDb } from '@/lib/db';

interface UpsertParams {
  matchKeyword: string;
  costcoName?: string;
  unitPrice: number;
  splitQty: number;
  productNo?: string;
  naverOriginPno?: string;
  /** true 시 박스가격 감지 (기존 unit_price의 5배 초과면 거부). 기본 false. */
  detectBoxPrice?: boolean;
  /** true 시 같은 product_no를 가진 다른 행이 있으면 이 행을 분리 (product_no→costco_no_display). */
  autoSplitCostcoNo?: boolean;
}

export interface UpsertResult {
  saved: boolean;
  error?: string;
  /** 박스가격으로 의심되어 거부됨 */
  rejected?: boolean;
  warning?: string;
}

function ensureColumns(db: any): void {
  for (const sql of [
    "ALTER TABLE products ADD COLUMN naver_origin_pno TEXT DEFAULT ''",
    "ALTER TABLE products ADD COLUMN naver_channel_pno TEXT DEFAULT ''",
    "ALTER TABLE products ADD COLUMN costco_no_display TEXT DEFAULT ''",
  ]) {
    try { db.exec(sql); } catch {}
  }
}

/** 우선순위 lookup: naver_origin_pno → product_no → match_keyword */
export function findProductByKey(
  username: string,
  keys: { naverOriginPno?: string; productNo?: string; matchKeyword?: string }
): { id: number } | null {
  const db = getUserDb(username);
  ensureColumns(db);
  if (keys.naverOriginPno) {
    const row = db.prepare(
      "SELECT id FROM products WHERE naver_origin_pno = ? AND naver_origin_pno != ''"
    ).get(keys.naverOriginPno) as any;
    if (row) return row;
  }
  if (keys.productNo) {
    const row = db.prepare(
      "SELECT id FROM products WHERE product_no = ? AND (naver_origin_pno IS NULL OR naver_origin_pno = '')"
    ).get(keys.productNo) as any;
    if (row) return row;
  }
  if (keys.matchKeyword) {
    const row = db.prepare(
      "SELECT id FROM products WHERE match_keyword = ?"
    ).get(keys.matchKeyword) as any;
    if (row) return row;
  }
  return null;
}

export interface ProductRow {
  id: number;
  productNo: string;
  storeName: string;
  costcoName: string;
  matchKeyword: string;
  unitPrice: number;
  splitQty: number;
  salePrice: number;
  naverOriginPno: string;
  naverChannelPno: string;
  costcoNoDisplay: string;   // 분리된 행의 원본 코스트코 번호
  updatedAt: string;
}

/** 제품 목록 조회 (검색어 부분일치, LIMIT 적용) */
export function listProducts(
  username: string,
  search: string = '',
  limit: number = 200
): ProductRow[] {
  const db = getUserDb(username);
  ensureColumns(db);
  const q = `%${search.trim()}%`;
  const cols = `id, product_no, store_product_name, costco_name, match_keyword,
                unit_price, split_qty, sale_price, naver_origin_pno, naver_channel_pno,
                costco_no_display, updated_at`;
  const rows = (search.trim()
    ? db.prepare(`
        SELECT ${cols} FROM products
        WHERE product_no LIKE ? OR costco_name LIKE ? OR store_product_name LIKE ?
           OR match_keyword LIKE ? OR naver_origin_pno LIKE ? OR costco_no_display LIKE ?
        ORDER BY updated_at DESC LIMIT ?
      `).all(q, q, q, q, q, q, limit)
    : db.prepare(`
        SELECT ${cols} FROM products ORDER BY updated_at DESC LIMIT ?
      `).all(limit)
  ) as any[];
  return rows.map(r => ({
    id: r.id,
    productNo: r.product_no || '',
    storeName: r.store_product_name || '',
    costcoName: r.costco_name || '',
    matchKeyword: r.match_keyword || '',
    unitPrice: Number(r.unit_price) || 0,
    splitQty: Number(r.split_qty) || 1,
    salePrice: Number(r.sale_price) || 0,
    naverOriginPno: r.naver_origin_pno || '',
    naverChannelPno: r.naver_channel_pno || '',
    costcoNoDisplay: r.costco_no_display || '',
    updatedAt: r.updated_at || '',
  }));
}

/** 단일 제품 수정 (id 기반, 사용자 직접 편집용) */
export function updateProductById(
  username: string,
  id: number,
  fields: { unitPrice?: number; splitQty?: number; matchKeyword?: string; costcoName?: string }
): { saved: boolean; error?: string } {
  const db = getUserDb(username);
  const now = new Date().toISOString().slice(0, 16).replace('T', ' ');
  const sets: string[] = [];
  const vals: any[] = [];
  if (fields.unitPrice !== undefined) { sets.push('unit_price = ?'); vals.push(fields.unitPrice); }
  if (fields.splitQty !== undefined) { sets.push('split_qty = ?'); vals.push(Math.max(1, fields.splitQty)); }
  if (fields.matchKeyword !== undefined) { sets.push('match_keyword = ?'); vals.push(fields.matchKeyword); }
  if (fields.costcoName !== undefined) { sets.push('costco_name = ?'); vals.push(fields.costcoName); }
  if (sets.length === 0) return { saved: false, error: 'no fields' };
  sets.push('updated_at = ?'); vals.push(now);
  vals.push(id);
  try {
    db.prepare(`UPDATE products SET ${sets.join(', ')} WHERE id = ?`).run(...vals);
    return { saved: true };
  } catch (e: any) {
    return { saved: false, error: e.message };
  }
}

/** 가격 분리된 행을 코스트코 번호 매칭으로 복귀 (costco_no_display → product_no) */
export function unlockCostcoNo(username: string, id: number): { unlocked: boolean; error?: string } {
  const db = getUserDb(username);
  ensureColumns(db);
  try {
    const row = db.prepare(
      "SELECT costco_no_display FROM products WHERE id = ?"
    ).get(id) as any;
    const orig = (row?.costco_no_display || '').trim();
    if (!orig) return { unlocked: false, error: '분리된 행이 아닙니다.' };
    db.prepare(
      "UPDATE products SET product_no = ?, costco_no_display = '' WHERE id = ?"
    ).run(orig, id);
    return { unlocked: true };
  } catch (e: any) {
    return { unlocked: false, error: e.message };
  }
}

/** 제품 삭제 */
export function deleteProductById(username: string, id: number): { deleted: boolean; error?: string } {
  const db = getUserDb(username);
  try {
    db.prepare('DELETE FROM products WHERE id = ?').run(id);
    return { deleted: true };
  } catch (e: any) {
    return { deleted: false, error: e.message };
  }
}

export function upsertProduct(username: string, p: UpsertParams): UpsertResult {
  const db = getUserDb(username);
  ensureColumns(db);
  const now = new Date().toISOString().slice(0, 16).replace('T', ' ');
  try {
    const existing = findProductByKey(username, {
      naverOriginPno: p.naverOriginPno,
      productNo: p.productNo,
      matchKeyword: p.matchKeyword,
    });

    // 🛡 박스가격 감지 — 새 가격이 기존의 5배 초과면 박스가 잘못 들어온 것으로 의심
    let warning: string | undefined;
    let finalUnitPrice = p.unitPrice;
    if (p.detectBoxPrice && existing) {
      const oldRow = db.prepare(
        "SELECT unit_price, sale_price FROM products WHERE id = ?"
      ).get(existing.id) as any;
      const oldUnit = Number(oldRow?.unit_price) || 0;
      const oldSale = Number(oldRow?.sale_price) || 0;
      const suspicious =
        (oldUnit > 0 && p.unitPrice > oldUnit * 5) ||
        (oldUnit === 0 && oldSale > 0 && p.unitPrice > oldSale * 5) ||
        (oldUnit === 0 && oldSale === 0 && p.unitPrice > 200000);
      if (suspicious) {
        // 거부: 기존 가격 유지
        finalUnitPrice = oldUnit || finalUnitPrice;
        warning = `박스가격 감지 — 새 가격 ${p.unitPrice}원이 기존 ${oldUnit || oldSale}원의 5배 초과. 기존 가격 유지.`;
        return { saved: false, rejected: true, warning };
      }
    }

    if (existing) {
      db.prepare(`
        UPDATE products
        SET unit_price = ?, split_qty = ?, updated_at = ?,
            naver_origin_pno = COALESCE(NULLIF(?, ''), naver_origin_pno)
        WHERE id = ?
      `).run(
        finalUnitPrice,
        Math.max(1, p.splitQty),
        now,
        p.naverOriginPno || '',
        existing.id
      );

      // ⭐ 자동 분리: 같은 product_no를 가진 다른 행이 있으면 이 행만 격리
      if (p.autoSplitCostcoNo && p.productNo) {
        const sibling = db.prepare(
          "SELECT COUNT(*) as c FROM products WHERE product_no = ? AND id <> ?"
        ).get(p.productNo, existing.id) as any;
        if (sibling && Number(sibling.c) > 0) {
          db.prepare(
            "UPDATE products SET costco_no_display = ?, product_no = '' WHERE id = ?"
          ).run(p.productNo, existing.id);
        }
      }
    } else {
      db.prepare(`
        INSERT INTO products
          (product_no, store_product_name, costco_name, match_keyword,
           unit_price, split_qty, naver_origin_pno, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        p.productNo || '',
        p.costcoName || p.matchKeyword,
        p.costcoName || p.matchKeyword,
        p.matchKeyword,
        finalUnitPrice,
        Math.max(1, p.splitQty),
        p.naverOriginPno || '',
        now
      );
    }
    return { saved: true, warning };
  } catch (e: any) {
    return { saved: false, error: e.message };
  }
}
