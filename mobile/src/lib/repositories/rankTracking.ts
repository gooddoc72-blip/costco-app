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

export interface DayRank {
  best: number;
  bestType: 'wonbu' | 'compare' | 'solo';
  wonbu: number | null;
  compare: number | null;
  solo: number | null;
}

export function dailyRanksInMonth(
  username: string, trackingId: number, year: number, month: number,
): Record<number, DayRank> {
  const db = getUserDb(username);
  ensureTables(db);
  const prefix = `${year.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}`;
  const rows = db.prepare(`
    SELECT CAST(SUBSTR(rh.checked_at, 9, 2) AS INTEGER) as day,
           rh.rank_price_compare as wonbu,
           rh.rank_compare as compare,
           rh.rank_total as solo
    FROM rank_history rh
    WHERE rh.tracking_id = ?
      AND SUBSTR(rh.checked_at, 1, 7) = ?
      AND rh.id = (
        SELECT MAX(rh2.id) FROM rank_history rh2
        WHERE rh2.tracking_id = rh.tracking_id
          AND SUBSTR(rh2.checked_at, 1, 10) = SUBSTR(rh.checked_at, 1, 10)
      )
  `).all(trackingId, prefix) as any[];
  const out: Record<number, DayRank> = {};
  for (const r of rows) {
    const vals = {
      wonbu: r.wonbu as number | null,
      compare: r.compare as number | null,
      solo: r.solo as number | null,
    };
    let best: number | null = null;
    let type: keyof typeof vals = 'solo';
    for (const k of Object.keys(vals) as Array<keyof typeof vals>) {
      const v = vals[k];
      if (v != null && (best == null || v < best)) { best = v; type = k; }
    }
    if (best != null) out[r.day] = { best, bestType: type, ...vals };
  }
  return out;
}

export interface HistoryPoint {
  checkedAt: string;
  wonbu: number | null;
  compare: number | null;
  solo: number | null;
}

export function yearlyRankHistory(
  username: string, trackingId: number,
): HistoryPoint[] {
  const db = getUserDb(username);
  ensureTables(db);
  const rows = db.prepare(`
    SELECT checked_at, rank_price_compare, rank_compare, rank_total
    FROM rank_history
    WHERE tracking_id = ?
      AND checked_at >= datetime('now', '-1 year', 'localtime')
    ORDER BY checked_at
  `).all(trackingId) as any[];
  return rows.map(r => ({
    checkedAt: r.checked_at || '',
    wonbu: r.rank_price_compare,
    compare: r.rank_compare,
    solo: r.rank_total,
  }));
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
