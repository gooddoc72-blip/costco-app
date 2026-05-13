/**
 * PATCH /api/products/save-prices
 *
 * 일괄 가격 저장 — 네이버 원상품번호 우선, 같은 코스트코 상품도 네이버별 독립 저장.
 *
 * 조회 우선순위: naver_origin_pno > product_no > match_keyword
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getUserDb } from '@/lib/db';
import type { PriceSaveItem } from '@/lib/types';

export async function PATCH(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const body = await req.json() as { items: PriceSaveItem[] };
  if (!Array.isArray(body.items) || body.items.length === 0) {
    return NextResponse.json({ error: 'items required' }, { status: 400 });
  }

  const db = getUserDb(user.username);
  const now = new Date().toISOString().slice(0, 16).replace('T', ' ');

  // products 테이블 columns 보장 (필요 시 ALTER)
  for (const sql of [
    "ALTER TABLE products ADD COLUMN naver_origin_pno TEXT DEFAULT ''",
    "ALTER TABLE products ADD COLUMN naver_channel_pno TEXT DEFAULT ''",
  ]) {
    try { db.exec(sql); } catch {}
  }

  let saved = 0;
  const errors: string[] = [];

  const findByOrigin = db.prepare(
    "SELECT id FROM products WHERE naver_origin_pno = ? AND naver_origin_pno != ''"
  );
  const findByPno = db.prepare(
    "SELECT id FROM products WHERE product_no = ? AND (naver_origin_pno IS NULL OR naver_origin_pno = '')"
  );
  const findByKw = db.prepare(
    "SELECT id FROM products WHERE match_keyword = ?"
  );
  const updateById = db.prepare(`
    UPDATE products
    SET unit_price = ?, split_qty = ?, updated_at = ?,
        naver_origin_pno = COALESCE(NULLIF(?, ''), naver_origin_pno)
    WHERE id = ?
  `);
  const insertNew = db.prepare(`
    INSERT INTO products
      (product_no, store_product_name, costco_name, match_keyword,
       unit_price, split_qty, naver_origin_pno, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const tx = db.transaction((items: PriceSaveItem[]) => {
    for (const it of items) {
      try {
        let existing: any = null;
        if (it.naverOriginPno) {
          existing = findByOrigin.get(it.naverOriginPno);
        }
        if (!existing && it.costcoProductNo) {
          existing = findByPno.get(it.costcoProductNo);
        }
        if (!existing && it.matchKeyword) {
          existing = findByKw.get(it.matchKeyword);
        }
        if (existing) {
          updateById.run(
            it.boxPrice,
            Math.max(1, it.splitQty),
            now,
            it.naverOriginPno || '',
            existing.id
          );
        } else {
          insertNew.run(
            it.costcoProductNo || '',
            it.matchKeyword,
            it.matchKeyword,
            it.matchKeyword,
            it.boxPrice,
            Math.max(1, it.splitQty),
            it.naverOriginPno || '',
            now
          );
        }
        saved++;
      } catch (e: any) {
        errors.push(`${it.naverOriginPno || it.matchKeyword}: ${e.message}`);
      }
    }
  });
  tx(body.items);

  return NextResponse.json({ saved, errors });
}
