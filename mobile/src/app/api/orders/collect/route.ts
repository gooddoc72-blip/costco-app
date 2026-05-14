/**
 * POST /api/orders/collect — Naver API에서 최근 주문 수집 후 order_history 저장.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { collectNaverOrders } from '@/lib/services/orders';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { hoursBack?: number; statusFilter?: string[] };
  const hours = Math.max(1, Math.min(168, body.hoursBack || 48));
  try {
    const result = await collectNaverOrders(user.username, hours, body.statusFilter);
    return NextResponse.json(result);
  } catch (e: any) {
    return NextResponse.json({ error: e.message || '수집 실패' }, { status: 500 });
  }
}
