/**
 * GET /api/shopping?date=YYYY-MM-DD — 장보기 목록 데이터.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getShoppingList } from '@/lib/services/shopping';

export async function GET(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const date = new URL(req.url).searchParams.get('date') || '';
  if (!date) return NextResponse.json({ error: 'date required' }, { status: 400 });
  try {
    const data = getShoppingList(user.username, date);
    return NextResponse.json(data);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}
