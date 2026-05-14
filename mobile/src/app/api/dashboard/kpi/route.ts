/**
 * GET /api/dashboard/kpi — 얇은 orchestrator.
 */
import { NextResponse } from 'next/server';
import { getCurrentUser } from '@/lib/session';
import { getDashboard } from '@/lib/services/dashboard';

export async function GET() {
  const username = getCurrentUser();
  if (!username) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  try {
    const data = getDashboard(username);
    return NextResponse.json(data);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}
