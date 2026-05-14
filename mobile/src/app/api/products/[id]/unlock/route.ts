/**
 * POST /api/products/[id]/unlock — 분리된 행을 코스트코 번호 매칭으로 복귀.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { unlockProductCostcoNo } from '@/lib/services/products';

export async function POST(_req: NextRequest, ctx: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const id = parseInt(ctx.params.id, 10);
  if (!id) return NextResponse.json({ error: 'invalid id' }, { status: 400 });
  const r = unlockProductCostcoNo(user.username, id);
  if (!r.unlocked) return NextResponse.json({ error: r.error }, { status: 400 });
  return NextResponse.json(r);
}
