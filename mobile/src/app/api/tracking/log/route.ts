/**
 * POST /api/tracking/log — 얇은 orchestrator.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { logBulkDispatch, type BulkLogItem } from '@/lib/services/tracking';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { items: BulkLogItem[]; dispatchDate: string };
  if (!Array.isArray(body.items) || body.items.length === 0) {
    return NextResponse.json({ error: 'items required' }, { status: 400 });
  }
  const date = body.dispatchDate || new Date().toISOString().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json({ error: 'Invalid dispatchDate' }, { status: 400 });
  }
  const result = logBulkDispatch(user.username, body.items, date);
  return NextResponse.json(result);
}
