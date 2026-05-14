/**
 * POST /api/rank/check-all — 모든 추적 키워드 일괄 체크.
 */
import { NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { checkAllRanks } from '@/lib/services/rankCheck';

export const runtime = 'nodejs';
export const maxDuration = 300;  // 키워드 많을 때 대비

export async function POST() {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  try {
    const r = await checkAllRanks(user.username);
    return NextResponse.json(r);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'check failed' }, { status: 500 });
  }
}
