/**
 * daily_orders Repository — 수익계산용 일자별 주문 dump.
 * 비즈니스 로직(cost/profit 계산)은 service에서.
 */
import { getUserDb } from '@/lib/db';

export interface DailyOrderInput {
  orderDate: string;
  recipient: string;
  productName: string;
  productNo: string;
  optionInfo: string;
  qty: number;
  orderAmount: number;
  shippingFee: number;
  extraShipping: number;
  settlement: number;
  costPrice: number;
  deliveryCost: number;
  boxCost: number;
  profit: number;
  matched: number;
}

function ensureTable(db: any): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS daily_orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_date TEXT NOT NULL,
      recipient TEXT DEFAULT '',
      product_name TEXT DEFAULT '',
      product_no TEXT DEFAULT '',
      option_info TEXT DEFAULT '',
      qty INTEGER DEFAULT 1,
      order_amount INTEGER DEFAULT 0,
      shipping_fee INTEGER DEFAULT 0,
      extra_shipping INTEGER DEFAULT 0,
      settlement INTEGER DEFAULT 0,
      cost_price INTEGER DEFAULT 0,
      delivery_cost INTEGER DEFAULT 0,
      box_cost INTEGER DEFAULT 0,
      profit INTEGER DEFAULT 0,
      matched INTEGER DEFAULT 0,
      created_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_do_date ON daily_orders(order_date);
  `);
}

/** 한 날짜의 모든 daily_orders 삭제 후 새 행 일괄 insert (멱등) */
export function replaceDailyOrders(username: string, orderDate: string, items: DailyOrderInput[]): number {
  const db = getUserDb(username);
  ensureTable(db);
  const now = new Date().toISOString().slice(0, 16).replace('T', ' ');
  const del = db.prepare("DELETE FROM daily_orders WHERE order_date = ?");
  const ins = db.prepare(`
    INSERT INTO daily_orders
      (order_date, recipient, product_name, product_no, option_info,
       qty, order_amount, shipping_fee, extra_shipping, settlement,
       cost_price, delivery_cost, box_cost, profit, matched, created_at)
    VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?)
  `);
  const tx = db.transaction(() => {
    del.run(orderDate);
    for (const it of items) {
      ins.run(
        it.orderDate, it.recipient, it.productName, it.productNo, it.optionInfo,
        it.qty, it.orderAmount, it.shippingFee, it.extraShipping, it.settlement,
        it.costPrice, it.deliveryCost, it.boxCost, it.profit, it.matched, now,
      );
    }
  });
  tx();
  return items.length;
}

export function getSavedDates(username: string, limit: number = 60): string[] {
  const db = getUserDb(username);
  ensureTable(db);
  const rows = db.prepare(
    "SELECT DISTINCT order_date FROM daily_orders ORDER BY order_date DESC LIMIT ?"
  ).all(limit) as any[];
  return rows.map(r => r.order_date);
}
