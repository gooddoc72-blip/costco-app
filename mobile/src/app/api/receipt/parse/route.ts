/**
 * POST /api/receipt/parse — multipart PDF 다수 업로드 + 파싱.
 * (DB 저장은 별도 /apply 라우트에서)
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { parseMany } from '@/lib/services/receipt';

export const runtime = 'nodejs';
export const maxDuration = 60;

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const form = await req.formData();
  const files = form.getAll('files');
  if (files.length === 0) return NextResponse.json({ error: 'files required' }, { status: 400 });
  const inputs: Array<{ name: string; buf: Buffer }> = [];
  for (const f of files) {
    if (!(f instanceof File)) continue;
    inputs.push({ name: f.name, buf: Buffer.from(await f.arrayBuffer()) });
  }
  try {
    const r = await parseMany(inputs);
    return NextResponse.json(r);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || '파싱 실패' }, { status: 500 });
  }
}
