/**
 * POST /api/admin/migrate
 *  Body: { action: 'fix-order-dates' | 'backfill-matched-product' | 'all' }
 *
 * 1회성 DB 정리 작업. 관리자/사용자 본인만 실행 가능.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { fixOrderDates } from '@/lib/migrations/fix-order-dates';
import { backfillMatchedProductId } from '@/lib/migrations/backfill-matched-product';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { action: string };

  const results: Record<string, any> = {};

  if (body.action === 'fix-order-dates' || body.action === 'all') {
    results.fixOrderDates = fixOrderDates(user.username);
  }
  if (body.action === 'backfill-matched-product' || body.action === 'all') {
    results.backfillMatchedProductId = backfillMatchedProductId(user.username);
  }
  if (Object.keys(results).length === 0) {
    return NextResponse.json({ error: 'Unknown action' }, { status: 400 });
  }
  return NextResponse.json({ ok: true, results });
}
