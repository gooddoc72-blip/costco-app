/**
 * POST /api/orders/collect — 네이버/쿠팡 API에서 최근 주문 수집 후 order_history 저장.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { collectNaverOrders, collectCoupangOrders, type Platform } from '@/lib/services/orders';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as {
    platform?: Platform;
    hoursBack?: number;
    daysBack?: number;
    statusFilter?: string[];
  };
  const platform: Platform = body.platform || 'naver';
  try {
    if (platform === 'coupang') {
      const days = Math.max(1, Math.min(31, body.daysBack || 7));
      const statuses = body.statusFilter?.length ? body.statusFilter : ['ACCEPT', 'INSTRUCT'];
      const result = await collectCoupangOrders(user.username, days, statuses);
      return NextResponse.json(result);
    }
    const hours = Math.max(1, Math.min(168, body.hoursBack || 48));
    const result = await collectNaverOrders(user.username, hours, body.statusFilter);
    return NextResponse.json(result);
  } catch (e: any) {
    return NextResponse.json({ error: e.message || '수집 실패' }, { status: 500 });
  }
}
