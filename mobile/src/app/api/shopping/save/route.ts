/**
 * POST /api/shopping/save — 한 날짜의 order_history를 daily_orders에 저장.
 * body: { date: 'YYYY-MM-DD' }
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { saveDailyFromOrderHistory } from '@/lib/services/shopping';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { date?: string };
  if (!body.date) return NextResponse.json({ error: 'date required' }, { status: 400 });
  try {
    const r = saveDailyFromOrderHistory(user.username, body.date);
    return NextResponse.json(r);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'save failed' }, { status: 500 });
  }
}
