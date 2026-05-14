/**
 * DELETE /api/rank/[id] — 추적 비활성화
 * POST   /api/rank/[id]/check  → 별도 라우트
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { removeKeyword } from '@/lib/services/rankCheck';

export async function DELETE(_req: NextRequest, ctx: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const id = parseInt(ctx.params.id, 10);
  if (!id) return NextResponse.json({ error: 'invalid id' }, { status: 400 });
  removeKeyword(user.username, id);
  return NextResponse.json({ ok: true });
}
