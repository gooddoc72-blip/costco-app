/**
 * POST /api/receipt/apply — 파싱 결과를 products 테이블 매입가에 반영.
 * body: { items: ReceiptItem[] }
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { applyReceiptToProducts, type ReceiptItem } from '@/lib/services/receipt';

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { items?: ReceiptItem[] };
  if (!body.items || body.items.length === 0) {
    return NextResponse.json({ error: 'items required' }, { status: 400 });
  }
  try {
    const r = applyReceiptToProducts(user.username, body.items);
    return NextResponse.json(r);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'apply failed' }, { status: 500 });
  }
}
