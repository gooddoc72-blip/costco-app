/**
 * GET /api/settlement/match?settleDate=YYYY-MM-DD&shipDate=YYYY-MM-DD
 * DELETE /api/settlement/match?settleDate=YYYY-MM-DD — 해당 정산일 데이터 제거
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getSettlementPage, clearSettleDate } from '@/lib/services/settlement';

export async function GET(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const url = new URL(req.url);
  const settleDate = url.searchParams.get('settleDate') || '';
  const shipDate = url.searchParams.get('shipDate') || '';
  if (!settleDate || !shipDate) {
    return NextResponse.json({ error: 'settleDate / shipDate required' }, { status: 400 });
  }
  try {
    const data = getSettlementPage(user.username, settleDate, shipDate);
    return NextResponse.json(data);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const url = new URL(req.url);
  const settleDate = url.searchParams.get('settleDate') || '';
  if (!settleDate) return NextResponse.json({ error: 'settleDate required' }, { status: 400 });
  const removed = clearSettleDate(user.username, settleDate);
  return NextResponse.json({ removed });
}
