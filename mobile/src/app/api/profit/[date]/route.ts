/**
 * GET /api/profit/[date]
 * 얇은 orchestrator — service만 호출.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getProfitPageData } from '@/lib/services/profit';

export async function GET(
  _req: NextRequest,
  { params }: { params: { date: string } }
) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  if (!/^\d{4}-\d{2}-\d{2}$/.test(params.date)) {
    return NextResponse.json({ error: 'Invalid date' }, { status: 400 });
  }
  const data = getProfitPageData(user.username, params.date);
  return NextResponse.json(data);
}
