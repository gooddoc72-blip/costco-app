/**
 * Streamlit 앱과 호환되는 세션 토큰 검증
 * - sessions 테이블: (token, username, created_at, expires_at)
 * - expires_at 포맷: "YYYY-MM-DD HH:MM"
 * - Streamlit이 발급한 토큰을 모바일에서도 그대로 사용
 */
import crypto from 'crypto';
import { cookies } from 'next/headers';
import { getAuthDb } from './db';

export const SESSION_COOKIE = 'sid';

export function getSessionUsername(token: string): string | null {
  if (!token) return null;
  try {
    const db = getAuthDb();
    const row = db
      .prepare('SELECT username, expires_at FROM sessions WHERE token = ?')
      .get(token) as { username: string; expires_at: string } | undefined;
    if (!row) return null;
    if (row.expires_at) {
      const exp = new Date(row.expires_at.replace(' ', 'T') + ':00');
      if (exp < new Date()) return null;
    }
    return row.username;
  } catch {
    return null;
  }
}

export function getCurrentUser(): string | null {
  const token = cookies().get(SESSION_COOKIE)?.value || '';
  return getSessionUsername(token);
}

export function createSession(username: string, days = 30): string {
  const token = crypto.randomBytes(32).toString('base64url');
  const now = new Date();
  const expires = new Date(now.getTime() + days * 86400 * 1000);
  const fmt = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ` +
    `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const db = getAuthDb();
  db.prepare('INSERT INTO sessions VALUES (?,?,?,?)').run(
    token,
    username,
    fmt(now),
    fmt(expires),
  );
  return token;
}

export function deleteSession(token: string): void {
  if (!token) return;
  try {
    getAuthDb().prepare('DELETE FROM sessions WHERE token = ?').run(token);
  } catch {}
}
