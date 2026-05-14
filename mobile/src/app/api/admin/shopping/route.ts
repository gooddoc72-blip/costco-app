/**
 * GET /api/admin/shopping?limit=50&user=optional — 관리자 전용. 사용자 장보기 제출 조회.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUser } from '@/lib/session';
import { getAuthDb } from '@/lib/db';
import { listRecentSubmissions } from '@/lib/repositories/adminShopping';

export async function GET(req: NextRequest) {
  const username = getCurrentUser();
  if (!username) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const row = getAuthDb()
    .prepare('SELECT is_admin FROM users WHERE username = ?')
    .get(username) as { is_admin: number } | undefined;
  if (!row?.is_admin) return NextResponse.json({ error: 'Forbidden' }, { status: 403 });

  const url = new URL(req.url);
  const limit = Math.max(1, Math.min(200, parseInt(url.searchParams.get('limit') || '50', 10)));
  const user = url.searchParams.get('user') || undefined;
  const submissions = listRecentSubmissions(limit, user);
  return NextResponse.json({ submissions });
}
