/**
 * 키워드 순위 추적 — keyword_tracking + rank_history.
 */
import { getUserDb } from '@/lib/db';

export interface TrackingRow {
  id: number;
  productKeyword: string;
  searchKeyword: string;
  naverProductNo: string;
  storeName: string;
}

export interface LatestRow extends TrackingRow {
  rankPriceCompare: number | null;
  rankCompare: number | null;
  rankTotal: number | null;
  checkedAt: string | null;
}

function ensureTables(db: any): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS keyword_tracking (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      product_keyword TEXT NOT NULL,
      search_keyword TEXT NOT NULL,
      naver_product_no TEXT DEFAULT '',
      store_name TEXT DEFAULT '',
      active INTEGER DEFAULT 1,
      created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS rank_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      tracking_id INTEGER NOT NULL,
      rank_price_compare INTEGER,
      rank_compare INTEGER,
      rank_total INTEGER,
      checked_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_rank_tracking ON rank_history(tracking_id, checked_at DESC);
  `);
  try { db.exec("ALTER TABLE rank_history ADD COLUMN rank_compare INTEGER"); } catch {}
}

export function addTracking(
  username: string, productKeyword: string, searchKeyword: string,
  naverProductNo: string = '', storeName: string = '',
): number {
  const db = getUserDb(username);
  ensureTables(db);
  const r = db.prepare(`
    INSERT INTO keyword_tracking (product_keyword, search_keyword, naver_product_no, store_name)
    VALUES (?,?,?,?)
  `).run(productKeyword, searchKeyword, naverProductNo, storeName);
  return Number(r.lastInsertRowid) || 0;
}

export function deleteTracking(username: string, id: number): void {
  const db = getUserDb(username);
  ensureTables(db);
  db.prepare("UPDATE keyword_tracking SET active = 0 WHERE id = ?").run(id);
}

export function listLatestRanks(username: string): LatestRow[] {
  const db = getUserDb(username);
  ensureTables(db);
  const rows = db.prepare(`
    SELECT kt.id, kt.product_keyword, kt.search_keyword,
           kt.naver_product_no, kt.store_name,
           rh.rank_price_compare, rh.rank_compare, rh.rank_total, rh.checked_at
    FROM keyword_tracking kt
    LEFT JOIN rank_history rh ON rh.id = (
      SELECT id FROM rank_history WHERE tracking_id = kt.id
      ORDER BY checked_at DESC LIMIT 1
    )
    WHERE kt.active = 1
    ORDER BY kt.id
  `).all() as any[];
  return rows.map(r => ({
    id: r.id,
    productKeyword: r.product_keyword || '',
    searchKeyword: r.search_keyword || '',
    naverProductNo: r.naver_product_no || '',
    storeName: r.store_name || '',
    rankPriceCompare: r.rank_price_compare,
    rankCompare: r.rank_compare,
    rankTotal: r.rank_total,
    checkedAt: r.checked_at,
  }));
}

export function getTrackingById(username: string, id: number): TrackingRow | null {
  const db = getUserDb(username);
  ensureTables(db);
  const r = db.prepare(`
    SELECT id, product_keyword, search_keyword, naver_product_no, store_name
    FROM keyword_tracking WHERE id = ? AND active = 1
  `).get(id) as any;
  if (!r) return null;
  return {
    id: r.id,
    productKeyword: r.product_keyword || '',
    searchKeyword: r.search_keyword || '',
    naverProductNo: r.naver_product_no || '',
    storeName: r.store_name || '',
  };
}

export function saveRankResult(
  username: string, trackingId: number,
  rankWonbu: number | null, rankSolo: number | null, rankCompare: number | null,
): void {
  const db = getUserDb(username);
  ensureTables(db);
  const now = new Date().toISOString().slice(0, 16).replace('T', ' ');
  db.prepare(`
    INSERT INTO rank_history
      (tracking_id, rank_price_compare, rank_total, rank_compare, checked_at)
    VALUES (?,?,?,?,?)
  `).run(trackingId, rankWonbu, rankSolo, rankCompare, now);
}
