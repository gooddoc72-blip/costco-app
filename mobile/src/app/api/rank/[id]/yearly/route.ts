/**
 * GET /api/rank/[id]/yearly — 1년 순위 추이.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getYearly } from '@/lib/services/rankCheck';

export async function GET(_req: NextRequest, ctx: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const id = parseInt(ctx.params.id, 10);
  if (!id) return NextResponse.json({ error: 'invalid id' }, { status: 400 });
  try {
    return NextResponse.json({ history: getYearly(user.username, id) });
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}
