/**
 * GET  /api/rank — 키워드 목록 + 최신 순위
 * POST /api/rank — 키워드 추가  body: {productKeyword, searchKeyword, naverProductNo?, storeName?}
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getRankPage, addKeyword } from '@/lib/services/rankCheck';

export async function GET() {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  try {
    return NextResponse.json(getRankPage(user.username));
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as {
    productKeyword?: string; searchKeyword?: string;
    naverProductNo?: string; storeName?: string;
  };
  try {
    const id = addKeyword(
      user.username,
      body.productKeyword || '', body.searchKeyword || '',
      body.naverProductNo, body.storeName,
    );
    return NextResponse.json({ id });
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || '추가 실패' }, { status: 400 });
  }
}
