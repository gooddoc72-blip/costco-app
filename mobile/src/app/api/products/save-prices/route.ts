/**
 * PATCH /api/products/save-prices — 얇은 orchestrator.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { saveProductPrices } from '@/lib/services/profit';
import type { PriceSaveItem } from '@/lib/types';

export async function PATCH(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { items: PriceSaveItem[] };
  if (!Array.isArray(body.items) || body.items.length === 0) {
    return NextResponse.json({ error: 'items required' }, { status: 400 });
  }
  const result = saveProductPrices(user.username, body.items);
  return NextResponse.json(result);
}
