import { NextResponse } from 'next/server';
import { getCurrentUser } from '@/lib/session';
import { getUserDb } from '@/lib/db';

interface KpiBucket {
  cnt: number;
  qty: number;
  sales: number;
  profit: number;
}

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function getWeekRange(today: Date): [string, string] {
  const dow = today.getDay() === 0 ? 6 : today.getDay() - 1; // 월=0, 일=6
  const start = new Date(today);
  start.setDate(today.getDate() - dow);
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
  return [fmtDate(start), fmtDate(end)];
}

function getMonthRange(today: Date): [string, string] {
  const start = new Date(today.getFullYear(), today.getMonth(), 1);
  const end = new Date(today.getFullYear(), today.getMonth() + 1, 0);
  return [fmtDate(start), fmtDate(end)];
}

export async function GET() {
  const username = getCurrentUser();
  if (!username) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  const today = new Date();
  const [wStart, wEnd] = getWeekRange(today);
  const [mStart, mEnd] = getMonthRange(today);

  const lwEnd = new Date(today);
  lwEnd.setDate(today.getDate() - (today.getDay() === 0 ? 6 : today.getDay()) - 1);
  const lwStart = new Date(lwEnd);
  lwStart.setDate(lwEnd.getDate() - 6);

  const lmEnd = new Date(today.getFullYear(), today.getMonth(), 0);
  const lmStart = new Date(lmEnd.getFullYear(), lmEnd.getMonth(), 1);

  try {
    const db = getUserDb(username);
    const stmt = db.prepare(
      `SELECT COUNT(*) as cnt,
              COALESCE(SUM(qty),0) as qty,
              COALESCE(SUM(order_amount),0) as sales,
              COALESCE(SUM(profit),0) as profit
         FROM daily_orders
        WHERE order_date BETWEEN ? AND ?`,
    );
    const q = (s: string, e: string): KpiBucket => stmt.get(s, e) as KpiBucket;

    const kpi = {
      today: q(fmtDate(today), fmtDate(today)),
      week: q(wStart, wEnd),
      month: q(mStart, mEnd),
      last_week: q(fmtDate(lwStart), fmtDate(lwEnd)),
      last_month: q(fmtDate(lmStart), fmtDate(lmEnd)),
    };
    db.close();
    return NextResponse.json(kpi);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'db error' }, { status: 500 });
  }
}
