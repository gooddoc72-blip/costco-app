import { NextResponse } from 'next/server';
import { getCurrentUser } from '@/lib/session';
import { getAuthDb } from '@/lib/db';

export async function GET() {
  const username = getCurrentUser();
  if (!username) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const row = getAuthDb()
    .prepare('SELECT username, display_name, is_admin FROM users WHERE username = ?')
    .get(username) as { username: string; display_name: string; is_admin: number } | undefined;
  if (!row) {
    return NextResponse.json({ error: 'user not found' }, { status: 404 });
  }
  return NextResponse.json({
    username: row.username,
    display_name: row.display_name,
    is_admin: !!row.is_admin,
  });
}
