/**
 * PATCH /api/products/[id]  — 단일 필드 수정
 * DELETE /api/products/[id] — 삭제
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { updateProduct, deleteProduct } from '@/lib/services/products';

export async function PATCH(req: NextRequest, ctx: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const id = parseInt(ctx.params.id, 10);
  if (!id) return NextResponse.json({ error: 'invalid id' }, { status: 400 });
  const body = await req.json() as {
    unitPrice?: number; splitQty?: number; matchKeyword?: string; costcoName?: string;
  };
  const result = updateProduct(user.username, id, body);
  if (!result.saved) return NextResponse.json({ error: result.error }, { status: 400 });
  return NextResponse.json(result);
}

export async function DELETE(req: NextRequest, ctx: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const id = parseInt(ctx.params.id, 10);
  if (!id) return NextResponse.json({ error: 'invalid id' }, { status: 400 });
  const result = deleteProduct(user.username, id);
  if (!result.deleted) return NextResponse.json({ error: result.error }, { status: 400 });
  return NextResponse.json(result);
}
