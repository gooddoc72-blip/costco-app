/**
 * POST /api/shopping/send — 카톡/텔레그램 발송.
 * body: { date: 'YYYY-MM-DD' }
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getShoppingList, buildShoppingMessage } from '@/lib/services/shopping';
import { sendShoppingMessage } from '@/lib/services/messaging';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { date?: string };
  if (!body.date) return NextResponse.json({ error: 'date required' }, { status: 400 });
  try {
    const data = getShoppingList(user.username, body.date);
    if (data.items.length === 0) {
      return NextResponse.json({ error: '해당 날짜에 주문이 없습니다.' }, { status: 400 });
    }
    const msg = buildShoppingMessage(data);
    const result = await sendShoppingMessage(user.username, msg, data.items.length);
    return NextResponse.json({ ...result, msgLength: msg.length });
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'send failed' }, { status: 500 });
  }
}
