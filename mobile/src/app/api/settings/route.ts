/**
 * GET/PATCH /api/settings — 얇은 orchestrator.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { loadUserSettings, saveUserSettings } from '@/lib/services/settings';

export async function GET() {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  return NextResponse.json(loadUserSettings(user.username));
}

export async function PATCH(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json();
  saveUserSettings(user.username, body);
  return NextResponse.json({ ok: true });
}
