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
}

function ensureColumns(db: any): void {
  for (const sql of [
    "ALTER TABLE products ADD COLUMN naver_origin_pno TEXT DEFAULT ''",
    "ALTER TABLE products ADD COLUMN naver_channel_pno TEXT DEFAULT ''",
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

export function upsertProduct(username: string, p: UpsertParams): { saved: boolean; error?: string } {
  const db = getUserDb(username);
  ensureColumns(db);
  const now = new Date().toISOString().slice(0, 16).replace('T', ' ');
  try {
    const existing = findProductByKey(username, {
      naverOriginPno: p.naverOriginPno,
      productNo: p.productNo,
      matchKeyword: p.matchKeyword,
    });
    if (existing) {
      db.prepare(`
        UPDATE products
        SET unit_price = ?, split_qty = ?, updated_at = ?,
            naver_origin_pno = COALESCE(NULLIF(?, ''), naver_origin_pno)
        WHERE id = ?
      `).run(
        p.unitPrice,
        Math.max(1, p.splitQty),
        now,
        p.naverOriginPno || '',
        existing.id
      );
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
        p.unitPrice,
        Math.max(1, p.splitQty),
        p.naverOriginPno || '',
        now
      );
    }
    return { saved: true };
  } catch (e: any) {
    return { saved: false, error: e.message };
  }
}
