/**
 * Settings Repository — settings 테이블의 key/value CRUD만.
 * 키 매핑/타입 변환은 service에서.
 */
import { getUserDb } from '@/lib/db';

export function getAllSettings(username: string): Record<string, string> {
  const db = getUserDb(username);
  const rows = db.prepare("SELECT key, value FROM settings").all() as { key: string; value: string }[];
  const out: Record<string, string> = {};
  for (const r of rows) out[r.key] = r.value;
  return out;
}

export function upsertSettings(username: string, kvPairs: Record<string, string>): void {
  const db = getUserDb(username);
  const stmt = db.prepare(
    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
  );
  const tx = db.transaction(() => {
    for (const [k, v] of Object.entries(kvPairs)) {
      stmt.run(k, v);
    }
  });
  tx();
}
