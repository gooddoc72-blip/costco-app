/**
 * Dashboard Repository — KPI / 알림용 집계 SQL만 담당.
 */
import { getUserDb } from '@/lib/db';

export interface KpiBucket {
  cnt: number;
  qty: number;
  sales: number;
  profit: number;
}

export interface TrendPoint {
  date: string;
  sales: number;
  profit: number;
  cnt: number;
}

export interface DashboardAlerts {
  unmatched: number;       // matched_product_id NULL 인 order_history
  pendingDispatch: number; // order_history에 있지만 dispatch_log 없음
  zeroPrice: number;       // products.unit_price = 0
}

const KPI_SQL = `
  SELECT COUNT(*) as cnt,
         COALESCE(SUM(qty),0)          as qty,
         COALESCE(SUM(order_amount),0) as sales,
         COALESCE(SUM(profit),0)       as profit
  FROM daily_orders
  WHERE order_date BETWEEN ? AND ?
`;

export function fetchKpi(username: string, startDate: string, endDate: string): KpiBucket {
  const db = getUserDb(username);
  return db.prepare(KPI_SQL).get(startDate, endDate) as KpiBucket;
}

/** 최근 N일 일별 트렌드 (오래된 → 최신) */
export function fetchTrend(username: string, days: number): TrendPoint[] {
  const db = getUserDb(username);
  const rows = db.prepare(`
    SELECT order_date as date,
           COALESCE(SUM(order_amount),0) as sales,
           COALESCE(SUM(profit),0)       as profit,
           COUNT(*)                       as cnt
    FROM daily_orders
    WHERE order_date >= date('now', ?)
    GROUP BY order_date
    ORDER BY order_date ASC
  `).all(`-${days - 1} days`) as any[];
  return rows.map(r => ({
    date: r.date,
    sales: Number(r.sales) || 0,
    profit: Number(r.profit) || 0,
    cnt: Number(r.cnt) || 0,
  }));
}

export function fetchAlerts(username: string): DashboardAlerts {
  const db = getUserDb(username);
  const unmatched = (db.prepare(`
    SELECT COUNT(*) as c FROM order_history
    WHERE matched_product_id IS NULL OR matched_product_id = 0
  `).get() as any)?.c || 0;
  const pending = (db.prepare(`
    SELECT COUNT(*) as c FROM order_history oh
    WHERE NOT EXISTS (SELECT 1 FROM dispatch_log dl WHERE dl.order_no = oh.order_no)
  `).get() as any)?.c || 0;
  const zeroPrice = (db.prepare(`
    SELECT COUNT(*) as c FROM products
    WHERE COALESCE(unit_price, 0) = 0
  `).get() as any)?.c || 0;
  return {
    unmatched: Number(unmatched),
    pendingDispatch: Number(pending),
    zeroPrice: Number(zeroPrice),
  };
}
