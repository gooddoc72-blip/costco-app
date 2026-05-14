/**
 * DELETE /api/admin/shopping/[id] — 제출 1건 삭제 (관리자 전용).
 */
import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUser } from '@/lib/session';
import { getAuthDb } from '@/lib/db';
import { deleteSubmission } from '@/lib/repositories/adminShopping';

export async function DELETE(_req: NextRequest, ctx: { params: { id: string } }) {
  const username = getCurrentUser();
  if (!username) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const row = getAuthDb()
    .prepare('SELECT is_admin FROM users WHERE username = ?')
    .get(username) as { is_admin: number } | undefined;
  if (!row?.is_admin) return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
  const id = parseInt(ctx.params.id, 10);
  if (!id) return NextResponse.json({ error: 'invalid id' }, { status: 400 });
  const ok = deleteSubmission(id);
  return NextResponse.json({ deleted: ok });
}
