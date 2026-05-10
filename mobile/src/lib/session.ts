/**
 * Streamlit 앱과 호환되는 세션 토큰 검증
 * - sessions 테이블의 sid → username 매핑
 * - Streamlit이 발급한 토큰을 모바일에서도 그대로 사용
 */
import { getAuthDb } from './db';
import { cookies } from 'next/headers';

export const SESSION_COOKIE = '_cbsid';

export function getSessionUsername(sid: string): string | null {
  if (!sid) return null;
  try {
    const db = getAuthDb();
    const row = db
      .prepare('SELECT username, expires_at FROM sessions WHERE sid = ?')
      .get(sid) as { username: string; expires_at: string } | undefined;
    if (!row) return null;
    if (row.expires_at && new Date(row.expires_at) < new Date()) return null;
    return row.username;
  } catch {
    return null;
  }
}

export function getCurrentUser(): string | null {
  const sid = cookies().get(SESSION_COOKIE)?.value || '';
  return getSessionUsername(sid);
}
