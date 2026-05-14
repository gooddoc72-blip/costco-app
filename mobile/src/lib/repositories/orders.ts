/**
 * Orders Repository — order_history 테이블 CRUD.
 */
import { getUserDb } from '@/lib/db';

export interface OrderHistoryInput {
  orderNo: string;
  orderDate: string;
  recipient: string;
  productName: string;
  productNo?: string;
  naverOriginPno?: string;          // 네이버 원상품번호 (방식 A 핵심 식별자)
  naverChannelPno?: string;         // 네이버 채널 상품번호
  optionInfo?: string;
  qty: number;
  orderAmount: number;
  shippingFee: number;
  settlement: number;
  status?: string;
  matchedProductId?: number | null;  // ⭐ 매칭된 products.id (영구 링크)
}

function ensureTable(username: string): void {
  const db = getUserDb(username);
  db.exec(`
    CREATE TABLE IF NOT EXISTS order_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_no TEXT UNIQUE,
      order_date TEXT,
      recipient TEXT DEFAULT '',
      product_name TEXT DEFAULT '',
      product_no TEXT DEFAULT '',
      option_info TEXT DEFAULT '',
      qty INTEGER DEFAULT 1,
      order_amount INTEGER DEFAULT 0,
      shipping_fee INTEGER DEFAULT 0,
      settlement INTEGER DEFAULT 0,
      cost_price INTEGER DEFAULT 0,
      profit INTEGER DEFAULT 0,
      status TEXT DEFAULT '',
      tracking_no TEXT DEFAULT '',
      created_at TEXT,
      raw_json TEXT DEFAULT ''
    )
  `);
  // matched_product_id 컬럼 (구버전 DB 호환)
  try { db.exec("ALTER TABLE order_history ADD COLUMN matched_product_id INTEGER"); } catch {}
  try { db.exec("CREATE INDEX IF NOT EXISTS idx_oh_matched ON order_history(matched_product_id)"); } catch {}
  // 네이버 원/채널 상품번호 — 방식 A 식별자
  try { db.exec("ALTER TABLE order_history ADD COLUMN naver_origin_pno TEXT DEFAULT ''"); } catch {}
  try { db.exec("ALTER TABLE order_history ADD COLUMN naver_channel_pno TEXT DEFAULT ''"); } catch {}
}

/** order_no UNIQUE 충돌 시 INSERT 무시 (덮어쓰지 않음 — status 보존) */
export function bulkUpsertOrders(username: string, items: OrderHistoryInput[]): {
  inserted: number; updated: number; errors: string[];
} {
  ensureTable(username);
  const db = getUserDb(username);
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ');
  let inserted = 0, updated = 0;
  const errors: string[] = [];

  const findByNo = db.prepare("SELECT id, matched_product_id FROM order_history WHERE order_no = ?");
  const insertStmt = db.prepare(`
    INSERT INTO order_history
      (order_no, order_date, recipient, product_name, product_no,
       naver_origin_pno, naver_channel_pno, option_info,
       qty, order_amount, shipping_fee, settlement, status,
       matched_product_id, created_at)
    VALUES (?,?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?)
  `);
  // UPDATE 시 matched_product_id가 NULL이 아닐 때만 갱신 (기존 매칭 보존)
  const updateStmt = db.prepare(`
    UPDATE order_history
    SET order_date = ?, recipient = ?, product_name = ?, product_no = ?,
        naver_origin_pno  = COALESCE(NULLIF(?, ''), naver_origin_pno),
        naver_channel_pno = COALESCE(NULLIF(?, ''), naver_channel_pno),
        option_info = ?, qty = ?, order_amount = ?, shipping_fee = ?,
        settlement = ?, status = ?,
        matched_product_id = COALESCE(?, matched_product_id)
    WHERE order_no = ?
  `);

  const tx = db.transaction(() => {
    for (const it of items) {
      try {
        const existing = findByNo.get(it.orderNo) as any;
        if (existing) {
          updateStmt.run(
            it.orderDate, it.recipient, it.productName, it.productNo || '',
            it.naverOriginPno || '', it.naverChannelPno || '',
            it.optionInfo || '', it.qty, it.orderAmount, it.shippingFee,
            it.settlement, it.status || '',
            it.matchedProductId ?? null,
            it.orderNo,
          );
          updated++;
        } else {
          insertStmt.run(
            it.orderNo, it.orderDate, it.recipient, it.productName,
            it.productNo || '',
            it.naverOriginPno || '', it.naverChannelPno || '',
            it.optionInfo || '', it.qty, it.orderAmount,
            it.shippingFee, it.settlement, it.status || '',
            it.matchedProductId ?? null,
            now,
          );
          inserted++;
        }
      } catch (e: any) {
        errors.push(`${it.orderNo}: ${e.message}`);
      }
    }
  });
  tx();
  return { inserted, updated, errors };
}

export interface ShoppingRawRow {
  productNo: string;
  productName: string;
  optionInfo: string;
  qty: number;
  shippingFee: number;
  settlement: number;
  matchedProductId: number | null;
}

/** 한 날짜의 모든 주문 raw 행 (집계 전, 장보기 목록 생성용) */
export function fetchOrdersForShopping(username: string, date: string): ShoppingRawRow[] {
  const db = getUserDb(username);
  const rows = db.prepare(`
    SELECT product_no, product_name, option_info,
           qty, shipping_fee, settlement, matched_product_id
    FROM order_history
    WHERE order_date = ?
    ORDER BY product_no, product_name, option_info
  `).all(date) as any[];
  return rows.map(r => ({
    productNo: r.product_no || '',
    productName: r.product_name || '',
    optionInfo: r.option_info || '',
    qty: Number(r.qty) || 0,
    shippingFee: Number(r.shipping_fee) || 0,
    settlement: Number(r.settlement) || 0,
    matchedProductId: r.matched_product_id ?? null,
  }));
}

export interface OrderRowForDump {
  recipient: string;
  productName: string;
  productNo: string;
  optionInfo: string;
  qty: number;
  orderAmount: number;
  shippingFee: number;
  settlement: number;
  matchedProductId: number | null;
}

/** 한 날짜의 주문 전체 (daily_orders dump용 — 행 단위 보존) */
export function fetchOrdersForDailyDump(username: string, date: string): OrderRowForDump[] {
  const db = getUserDb(username);
  const rows = db.prepare(`
    SELECT recipient, product_name, product_no, option_info,
           qty, order_amount, shipping_fee, settlement, matched_product_id
    FROM order_history
    WHERE order_date = ?
    ORDER BY id
  `).all(date) as any[];
  return rows.map(r => ({
    recipient: r.recipient || '',
    productName: r.product_name || '',
    productNo: r.product_no || '',
    optionInfo: r.option_info || '',
    qty: Number(r.qty) || 1,
    orderAmount: Number(r.order_amount) || 0,
    shippingFee: Number(r.shipping_fee) || 0,
    settlement: Number(r.settlement) || 0,
    matchedProductId: r.matched_product_id ?? null,
  }));
}

export function getActiveOrders(username: string): any[] {
  const db = getUserDb(username);
  return db.prepare(`
    SELECT id, order_no, order_date, recipient, product_name, product_no,
           option_info, qty, order_amount, shipping_fee, settlement,
           status, tracking_no
    FROM order_history
    WHERE (tracking_no IS NULL OR tracking_no = '')
      AND status NOT IN ('DELIVERED', 'PURCHASE_DECIDED', 'CANCELED', 'RETURNED', 'EXCHANGED')
    ORDER BY order_date DESC, id DESC
    LIMIT 500
  `).all();
}
