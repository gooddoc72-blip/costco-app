import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUser } from '@/lib/session';
import { getUserDb } from '@/lib/db';

export async function GET(req: NextRequest) {
  const username = getCurrentUser();
  if (!username) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const date = req.nextUrl.searchParams.get('date');
  if (!date) {
    return NextResponse.json({ error: 'date 파라미터 필요' }, { status: 400 });
  }
  try {
    const db = getUserDb(username);
    const rows = db
      .prepare(
        `SELECT id, order_date, recipient, product_name, product_no, option_info,
                qty, order_amount, shipping_fee, settlement,
                cost_price, delivery_cost, box_cost, profit, matched
           FROM daily_orders
          WHERE order_date = ?
          ORDER BY id ASC`,
      )
      .all(date);
    db.close();
    return NextResponse.json({ date, orders: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}
