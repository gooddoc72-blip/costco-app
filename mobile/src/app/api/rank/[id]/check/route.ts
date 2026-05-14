/**
 * POST /api/rank/[id]/check — 단일 키워드 순위 체크.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { checkOneRank } from '@/lib/services/rankCheck';

export const runtime = 'nodejs';
export const maxDuration = 60;

export async function POST(_req: NextRequest, ctx: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const id = parseInt(ctx.params.id, 10);
  if (!id) return NextResponse.json({ error: 'invalid id' }, { status: 400 });
  try {
    const r = await checkOneRank(user.username, id);
    return NextResponse.json(r);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'check failed' }, { status: 500 });
  }
}
