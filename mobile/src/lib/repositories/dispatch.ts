/**
 * Dispatch Repository — dispatch_log 테이블 CRUD.
 */
import { getUserDb } from '@/lib/db';

export interface DispatchLogInput {
  orderNo: string;
  dispatchedAt: string;
  recipient?: string;
  productName?: string;
  expectedSettlement?: number;
  customerShippingFee?: number;
  trackingNo?: string;
  courier?: string;
  platform?: string;
}

function ensureTable(username: string): void {
  const db = getUserDb(username);
  db.exec(`
    CREATE TABLE IF NOT EXISTS dispatch_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_no TEXT NOT NULL,
      dispatched_at TEXT NOT NULL,
      recipient TEXT DEFAULT '',
      product_name TEXT DEFAULT '',
      expected_settlement INTEGER DEFAULT 0,
      tracking_no TEXT DEFAULT '',
      courier TEXT DEFAULT '',
      platform TEXT DEFAULT 'naver',
      customer_shipping_fee INTEGER DEFAULT 0,
      created_at TEXT NOT NULL,
      UNIQUE(order_no, dispatched_at)
    )
  `);
  try {
    db.exec("ALTER TABLE dispatch_log ADD COLUMN customer_shipping_fee INTEGER DEFAULT 0");
  } catch {}
}

/** order_history에서 주문 메타 조회 (없으면 빈 객체) */
export function fetchOrderMeta(username: string, orderNo: string): {
  recipient: string; productName: string; settlement: number; shippingFee: number;
} {
  const db = getUserDb(username);
  const row = db.prepare(
    "SELECT recipient, product_name AS productName, settlement, shipping_fee AS shippingFee FROM order_history WHERE order_no = ?"
  ).get(orderNo) as any;
  return {
    recipient: row?.recipient || '',
    productName: row?.productName || '',
    settlement: Number(row?.settlement) || 0,
    shippingFee: Number(row?.shippingFee) || 0,
  };
}

/** dispatch_log UPSERT (UNIQUE order_no+dispatched_at 충돌 시 갱신) */
export function upsertDispatchLogs(username: string, items: DispatchLogInput[]): {
  saved: number; errors: string[];
} {
  ensureTable(username);
  const db = getUserDb(username);
  const stmt = db.prepare(`
    INSERT OR REPLACE INTO dispatch_log
      (order_no, dispatched_at, recipient, product_name,
       expected_settlement, tracking_no, courier, platform,
       customer_shipping_fee, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ');
  let saved = 0;
  const errors: string[] = [];
  const tx = db.transaction(() => {
    for (const it of items) {
      try {
        stmt.run(
          it.orderNo,
          it.dispatchedAt,
          it.recipient || '',
          it.productName || '',
          it.expectedSettlement || 0,
          (it.trackingNo || '').toString().replace(/-/g, '').trim(),
          it.courier || '',
          it.platform || 'naver',
          it.customerShippingFee || 0,
          now
        );
        saved++;
      } catch (e: any) {
        errors.push(`${it.orderNo}: ${e.message}`);
      }
    }
  });
  tx();
  return { saved, errors };
}
