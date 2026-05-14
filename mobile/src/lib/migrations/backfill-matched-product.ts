/**
 * Migration: 옛 order_history 행에 matched_product_id 채우기.
 *
 * 새 주문은 수집 시점에 자동 매칭되지만, 이전에 저장된 주문은 NULL이라
 * profit JOIN이 폴백(product_name 일치)으로만 동작 → 상품명 변경 시 끊김.
 * 1회 실행으로 기존 데이터에 매칭 ID 부여.
 */
import { getUserDb } from '@/lib/db';
import { findMatchingProductId } from '@/lib/services/matching';

export interface BackfillResult {
  scanned: number;
  matched: number;
  unmatched: number;
}

export function backfillMatchedProductId(username: string): BackfillResult {
  const db = getUserDb(username);
  // matched_product_id가 NULL인 행만 대상
  let rows: any[];
  try {
    rows = db.prepare(
      "SELECT id, product_no, product_name FROM order_history WHERE matched_product_id IS NULL"
    ).all();
  } catch {
    // 컬럼 없으면 추가 후 재시도
    try { db.exec("ALTER TABLE order_history ADD COLUMN matched_product_id INTEGER"); } catch {}
    rows = db.prepare(
      "SELECT id, product_no, product_name FROM order_history WHERE matched_product_id IS NULL"
    ).all();
  }

  let matched = 0;
  const updateStmt = db.prepare("UPDATE order_history SET matched_product_id = ? WHERE id = ?");

  const tx = db.transaction(() => {
    for (const r of rows) {
      const m = findMatchingProductId(username, {
        productNo: r.product_no,
        productName: r.product_name,
      });
      if (m.productId) {
        updateStmt.run(m.productId, r.id);
        matched++;
      }
    }
  });
  tx();

  return { scanned: rows.length, matched, unmatched: rows.length - matched };
}
