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
