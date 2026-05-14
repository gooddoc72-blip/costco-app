/**
 * POST /api/settlement/upload-csv — multipart 파일 업로드.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { uploadCsv } from '@/lib/services/settlement';

export const runtime = 'nodejs';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const form = await req.formData();
  const file = form.get('file');
  if (!(file instanceof File)) {
    return NextResponse.json({ error: 'file required' }, { status: 400 });
  }
  try {
    const buf = Buffer.from(await file.arrayBuffer());
    const result = uploadCsv(user.username, buf);
    return NextResponse.json(result);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || '파싱 실패' }, { status: 500 });
  }
}
