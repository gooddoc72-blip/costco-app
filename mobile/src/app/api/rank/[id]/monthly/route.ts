/**
 * GET /api/rank/[id]/monthly?year=YYYY&month=MM
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getMonthly } from '@/lib/services/rankCheck';

export async function GET(req: NextRequest, ctx: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const id = parseInt(ctx.params.id, 10);
  if (!id) return NextResponse.json({ error: 'invalid id' }, { status: 400 });
  const url = new URL(req.url);
  const now = new Date();
  const year = parseInt(url.searchParams.get('year') || String(now.getFullYear()), 10);
  const month = parseInt(url.searchParams.get('month') || String(now.getMonth() + 1), 10);
  try {
    return NextResponse.json(getMonthly(user.username, id, year, month));
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}
