/**
 * POST /api/tracking/log
 *  Body: { items: [{ orderNo, trackingNo, courier, platform }], dispatchDate }
 * → dispatch_log에 발송 성공 이력 저장
 *
 * 이 함수는 "실제 발송 API 호출"이 아니라 "이미 발송한 주문의 송장정보를 기록"만 함.
 * (실제 네이버/쿠팡 발송 API 호출은 추후 또는 Streamlit 페이지에서 처리)
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getUserDb } from '@/lib/db';

interface LogItem {
  orderNo: string;
  trackingNo?: string;
  courier?: string;
  platform?: string;
}

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as { items: LogItem[]; dispatchDate: string };

  if (!Array.isArray(body.items) || body.items.length === 0) {
    return NextResponse.json({ error: 'items required' }, { status: 400 });
  }
  const date = body.dispatchDate || new Date().toISOString().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json({ error: 'Invalid dispatchDate' }, { status: 400 });
  }

  const db = getUserDb(user.username);
  // dispatch_log 테이블 보장
  db.exec(`
    CREATE TABLE IF NOT EXISTS dispatch_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_no TEXT NOT NULL,
      dispatched_at TEXT NOT NULL,
      recipient TEXT DEFAULT '',
      product_name TEXT DEFAULT '',
      expected_settlement INTEGER DEFAULT 0,
      tracking_no TEXT DEFAULT '',
      courier TEXT DEFAULT '',
      platform TEXT DEFAULT 'naver',
      customer_shipping_fee INTEGER DEFAULT 0,
      created_at TEXT NOT NULL,
      UNIQUE(order_no, dispatched_at)
    )
  `);
  try {
    db.exec("ALTER TABLE dispatch_log ADD COLUMN customer_shipping_fee INTEGER DEFAULT 0");
  } catch {}

  const findHist = db.prepare(`
    SELECT recipient, product_name, settlement, shipping_fee
    FROM order_history WHERE order_no = ?
  `);
  const upsertLog = db.prepare(`
    INSERT OR REPLACE INTO dispatch_log
      (order_no, dispatched_at, recipient, product_name,
       expected_settlement, tracking_no, courier, platform,
       customer_shipping_fee, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ');

  let saved = 0;
  const errors: string[] = [];
  const tx = db.transaction(() => {
    for (const it of body.items) {
      const orderNo = String(it.orderNo).trim();
      if (!orderNo) continue;
      try {
        const hist = findHist.get(orderNo) as any;
        upsertLog.run(
          orderNo,
          date,
          hist?.recipient || '',
          hist?.product_name || '',
          hist?.settlement || 0,
          (it.trackingNo || '').toString().replace(/-/g, '').trim(),
          it.courier || '',
          it.platform || 'naver',
          hist?.shipping_fee || 0,
          now
        );
        saved++;
      } catch (e: any) {
        errors.push(`${orderNo}: ${e.message}`);
      }
    }
  });
  tx();
  return NextResponse.json({ saved, errors });
}
