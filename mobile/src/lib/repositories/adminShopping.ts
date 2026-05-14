/**
 * 장보기 목록 관리자 제출 — auth.db의 shopping_list_submissions 테이블.
 * 한 사용자가 한 날짜 장보기 목록을 JSON 스냅샷으로 제출.
 */
import { getAuthDb } from '@/lib/db';
import type { ShoppingItem } from '@/lib/services/shopping';

export interface SubmissionRow {
  id: number;
  username: string;
  orderDate: string;
  submittedAt: string;
  totalItems: number;
  totalAmount: number;
  items: ShoppingItem[];
}

function ensureTable(db: any): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS shopping_list_submissions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL,
      order_date TEXT NOT NULL,
      submitted_at TEXT NOT NULL,
      total_items INTEGER DEFAULT 0,
      total_amount INTEGER DEFAULT 0,
      items_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_shopping_sub_user ON shopping_list_submissions(username);
    CREATE INDEX IF NOT EXISTS idx_shopping_sub_date ON shopping_list_submissions(order_date);
  `);
}

export function submitShoppingList(
  username: string, orderDate: string,
  items: ShoppingItem[], totalItems: number, totalAmount: number,
): number {
  const db = getAuthDb();
  ensureTable(db);
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ');
  db.prepare("DELETE FROM shopping_list_submissions WHERE username = ? AND order_date = ?")
    .run(username, orderDate);
  const r = db.prepare(`
    INSERT INTO shopping_list_submissions
      (username, order_date, submitted_at, total_items, total_amount, items_json)
    VALUES (?,?,?,?,?,?)
  `).run(username, orderDate, now, totalItems, totalAmount, JSON.stringify(items));
  return Number(r.lastInsertRowid) || 0;
}

export function listRecentSubmissions(limit: number, username?: string): SubmissionRow[] {
  const db = getAuthDb();
  ensureTable(db);
  const rows = (username
    ? db.prepare(`
        SELECT id, username, order_date, submitted_at, total_items, total_amount, items_json
        FROM shopping_list_submissions WHERE username = ?
        ORDER BY submitted_at DESC LIMIT ?
      `).all(username, limit)
    : db.prepare(`
        SELECT id, username, order_date, submitted_at, total_items, total_amount, items_json
        FROM shopping_list_submissions
        ORDER BY submitted_at DESC LIMIT ?
      `).all(limit)
  ) as any[];
  return rows.map(r => ({
    id: r.id,
    username: r.username,
    orderDate: r.order_date,
    submittedAt: r.submitted_at,
    totalItems: r.total_items,
    totalAmount: r.total_amount,
    items: safeParse(r.items_json),
  }));
}

function safeParse(s: string): ShoppingItem[] {
  try { return JSON.parse(s) as ShoppingItem[]; }
  catch { return []; }
}

export function deleteSubmission(id: number): boolean {
  const db = getAuthDb();
  ensureTable(db);
  const r = db.prepare("DELETE FROM shopping_list_submissions WHERE id = ?").run(id);
  return r.changes > 0;
}
