/**
 * GET /api/orders/active?date=YYYY-MM-DD
 * → 미발송 주문 또는 특정 날짜 결제건 조회 (order_history 기반)
 *
 * date 미지정 시: status가 미발송 상태인 주문 (PAYED, READY 등)
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getUserDb } from '@/lib/db';

export async function GET(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const date = req.nextUrl.searchParams.get('date');
  const db = getUserDb(user.username);

  let rows: any[];
  if (date && /^\d{4}-\d{2}-\d{2}$/.test(date)) {
    rows = db.prepare(`
      SELECT id, order_no, order_date, recipient, product_name, product_no,
             option_info, qty, order_amount, shipping_fee, settlement, status, tracking_no
      FROM order_history
      WHERE order_date = ?
      ORDER BY id DESC
    `).all(date);
  } else {
    rows = db.prepare(`
      SELECT id, order_no, order_date, recipient, product_name, product_no,
             option_info, qty, order_amount, shipping_fee, settlement, status, tracking_no
      FROM order_history
      WHERE (tracking_no IS NULL OR tracking_no = '')
        AND status NOT IN ('DELIVERED', 'PURCHASE_DECIDED', 'CANCELED', 'RETURNED', 'EXCHANGED')
      ORDER BY order_date DESC, id DESC
      LIMIT 500
    `).all();
  }

  return NextResponse.json({ rows, count: rows.length });
}
