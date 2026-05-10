import { NextResponse } from 'next/server';
import { getCurrentUser } from '@/lib/session';
import { getUserDb } from '@/lib/db';

export async function GET() {
  const username = getCurrentUser();
  if (!username) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  try {
    const db = getUserDb(username);
    const rows = db
      .prepare(
        `SELECT order_date AS date,
                COUNT(*) AS orders,
                COALESCE(SUM(qty),0) AS qty,
                COALESCE(SUM(order_amount),0) AS sales,
                COALESCE(SUM(profit),0) AS profit
           FROM daily_orders
          GROUP BY order_date
          ORDER BY order_date DESC
          LIMIT 60`,
      )
      .all() as Array<{ date: string; orders: number; qty: number; sales: number; profit: number }>;
    db.close();
    return NextResponse.json({ dates: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}
