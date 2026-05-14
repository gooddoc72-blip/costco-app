/**
 * POST /api/cron/rank-check?user=<username>
 *
 * 외부 cron이 호출하는 보호된 endpoint. Authorization: Bearer <CRON_SECRET>.
 * 로그인 세션 대신 환경변수 토큰으로 인증한다 (서버 → 서버).
 *
 * 사용 예 (Linux cron):
 *   0 12 * * * curl -fsS -X POST -H "Authorization: Bearer $CRON_SECRET" \
 *     "https://costcobiz.shop/api/cron/rank-check?user=admin"
 */
import { NextRequest, NextResponse } from 'next/server';
import { checkAllRanks } from '@/lib/services/rankCheck';

export const runtime = 'nodejs';
export const maxDuration = 300;
export const dynamic = 'force-dynamic';

function isAuthorized(req: NextRequest): boolean {
  const secret = process.env.CRON_SECRET;
  if (!secret) return false;
  const h = req.headers.get('authorization') || '';
  const expected = `Bearer ${secret}`;
  return h === expected;
}

export async function POST(req: NextRequest) {
  if (!isAuthorized(req)) {
    return NextResponse.json({ error: 'forbidden' }, { status: 403 });
  }
  const user = new URL(req.url).searchParams.get('user') || '';
  if (!user) return NextResponse.json({ error: 'user required' }, { status: 400 });
  try {
    const r = await checkAllRanks(user);
    return NextResponse.json({ ok: true, ...r });
  } catch (e: any) {
    return NextResponse.json({ ok: false, error: e?.message || 'check failed' }, { status: 500 });
  }
}
