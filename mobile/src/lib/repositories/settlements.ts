/**
 * Settlements Repository — naver_settlements 테이블 CRUD.
 * 매칭/비즈니스 로직은 service에서.
 */
import { getUserDb } from '@/lib/db';
import type { SettlementRecord } from '@/lib/csv/quicksettle';

export interface SettlementRow {
  productOrderNo: string;
  orderNo: string;
  settleDate: string;
  settleAmount: number;
  productAmount: number;
  shippingAmount: number;
  salesAmount: number;
  commission: number;
  settleType: string;
  reason: string;
  buyerName: string;
  productName: string;
  payDate: string;
}

function ensureTable(db: any): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS naver_settlements (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      product_order_no TEXT NOT NULL,
      order_no TEXT DEFAULT '',
      settle_date TEXT NOT NULL,
      sales_amount INTEGER DEFAULT 0,
      commission INTEGER DEFAULT 0,
      settle_amount INTEGER DEFAULT 0,
      status TEXT DEFAULT '',
      raw_json TEXT DEFAULT '',
      fetched_at TEXT NOT NULL,
      UNIQUE(product_order_no, settle_date)
    );
    CREATE INDEX IF NOT EXISTS idx_settle_date ON naver_settlements(settle_date);
    CREATE INDEX IF NOT EXISTS idx_settle_po ON naver_settlements(product_order_no);
  `);
  for (const sql of [
    "ALTER TABLE naver_settlements ADD COLUMN product_amount INTEGER DEFAULT 0",
    "ALTER TABLE naver_settlements ADD COLUMN shipping_amount INTEGER DEFAULT 0",
    "ALTER TABLE naver_settlements ADD COLUMN settle_type TEXT DEFAULT ''",
    "ALTER TABLE naver_settlements ADD COLUMN reason TEXT DEFAULT ''",
    "ALTER TABLE naver_settlements ADD COLUMN buyer_name TEXT DEFAULT ''",
    "ALTER TABLE naver_settlements ADD COLUMN product_name TEXT DEFAULT ''",
    "ALTER TABLE naver_settlements ADD COLUMN pay_date TEXT DEFAULT ''",
  ]) {
    try { db.exec(sql); } catch {}
  }
}

export function saveSettlementsFromCsv(username: string, records: SettlementRecord[]): number {
  if (records.length === 0) return 0;
  const db = getUserDb(username);
  ensureTable(db);
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ');
  const stmt = db.prepare(`
    INSERT OR REPLACE INTO naver_settlements
      (product_order_no, order_no, settle_date,
       sales_amount, commission, settle_amount,
       product_amount, shipping_amount,
       settle_type, reason, buyer_name, product_name, pay_date,
       status, raw_json, fetched_at)
    VALUES (?,?,?, ?,?,?, ?,?, ?,?,?,?,?, ?,?,?)
  `);
  let saved = 0;
  const tx = db.transaction((rs: SettlementRecord[]) => {
    for (const r of rs) {
      if (!r.productOrderNo) continue;
      const settleDate = r.settleCompleteDate || r.settleBasisDate || '';
      stmt.run(
        r.productOrderNo, r.orderNo, settleDate,
        0, 0, r.totalAmount,
        r.productAmount, r.shippingAmount,
        r.settleType, r.reason, r.buyerName, r.productName, r.payDate,
        r.settleType, '', now,
      );
      saved++;
    }
  });
  tx(records);
  return saved;
}

export function fetchSettlementsByDate(username: string, settleDate: string): SettlementRow[] {
  const db = getUserDb(username);
  ensureTable(db);
  const rows = db.prepare(`
    SELECT product_order_no, order_no, settle_date,
           sales_amount, commission, settle_amount,
           product_amount, shipping_amount,
           settle_type, reason, buyer_name, product_name, pay_date
    FROM naver_settlements
    WHERE settle_date = ?
    ORDER BY product_order_no
  `).all(settleDate) as any[];
  return rows.map(r => ({
    productOrderNo: r.product_order_no || '',
    orderNo: r.order_no || '',
    settleDate: r.settle_date || '',
    settleAmount: Number(r.settle_amount) || 0,
    productAmount: Number(r.product_amount) || 0,
    shippingAmount: Number(r.shipping_amount) || 0,
    salesAmount: Number(r.sales_amount) || 0,
    commission: Number(r.commission) || 0,
    settleType: r.settle_type || '',
    reason: r.reason || '',
    buyerName: r.buyer_name || '',
    productName: r.product_name || '',
    payDate: r.pay_date || '',
  }));
}

export function deleteSettlementsByDate(username: string, settleDate: string): number {
  const db = getUserDb(username);
  ensureTable(db);
  const r = db.prepare("DELETE FROM naver_settlements WHERE settle_date = ?").run(settleDate);
  return r.changes;
}

/** 매칭에 필요한 dispatch_log 행 (네이버만 — 주문번호에 '-' 없음) */
export interface DispatchedForMatch {
  orderNo: string;
  recipient: string;
  productName: string;
  expectedSettlement: number;
  customerShippingFee: number;
}

export function fetchDispatchedForMatch(username: string, shipDate: string): DispatchedForMatch[] {
  const db = getUserDb(username);
  const rows = db.prepare(`
    SELECT dl.order_no                        AS orderNo,
           COALESCE(oh.recipient, dl.recipient)       AS recipient,
           COALESCE(oh.product_name, dl.product_name) AS productName,
           COALESCE(oh.settlement, dl.expected_settlement, 0)     AS expectedSettlement,
           COALESCE(oh.shipping_fee, dl.customer_shipping_fee, 0) AS customerShippingFee
    FROM dispatch_log dl
    LEFT JOIN order_history oh ON dl.order_no = oh.order_no
    WHERE dl.dispatched_at = ? AND instr(dl.order_no, '-') = 0
    ORDER BY dl.id
  `).all(shipDate) as any[];
  return rows.map(r => ({
    orderNo: String(r.orderNo),
    recipient: r.recipient || '',
    productName: r.productName || '',
    expectedSettlement: Number(r.expectedSettlement) || 0,
    customerShippingFee: Number(r.customerShippingFee) || 0,
  }));
}
