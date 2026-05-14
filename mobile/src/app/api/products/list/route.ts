/**
 * GET /api/products/list?q=검색어&limit=200 — 제품 DB 목록 조회.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { listUserProducts } from '@/lib/services/products';

export async function GET(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const url = new URL(req.url);
  const q = url.searchParams.get('q') || '';
  const limit = parseInt(url.searchParams.get('limit') || '200', 10);
  const result = listUserProducts(user.username, q, limit);
  return NextResponse.json(result);
}
