/**
 * POST /api/shopping/submit-admin — 사용자가 그날 장보기 목록을 관리자에게 제출.
 * body: { date: 'YYYY-MM-DD' }
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { submitToAdmin } from '@/lib/services/shopping';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { date?: string };
  if (!body.date) return NextResponse.json({ error: 'date required' }, { status: 400 });
  try {
    const r = submitToAdmin(user.username, body.date);
    return NextResponse.json({ ok: true, ...r });
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'submit failed' }, { status: 500 });
  }
}
