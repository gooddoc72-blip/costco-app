/**
 * GET /api/profit/[date]
 * → 그 날짜에 일괄발송 성공한 주문 리스트 + 매칭 상품 정보 + 설정
 *
 * 데이터 소스: dispatch_log LEFT JOIN order_history LEFT JOIN products
 * → "이 날짜에 발송한 주문" = "이 날짜의 수익계산 대상"
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getUserDb, getAuthDb } from '@/lib/db';
import type { ProfitRow, Settings } from '@/lib/types';

export async function GET(
  req: NextRequest,
  { params }: { params: { date: string } }
) {
  const user = await getSessionUser();
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }
  const date = params.date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json({ error: 'Invalid date' }, { status: 400 });
  }

  const db = getUserDb(user.username);

  // dispatch_log + order_history + products (네이버 원상품번호 우선 매칭)
  const rows = db.prepare(`
    SELECT
      dl.order_no                          AS orderNo,
      dl.dispatched_at                     AS dispatchedAt,
      COALESCE(oh.recipient, dl.recipient) AS recipient,
      COALESCE(oh.product_name, dl.product_name) AS productName,
      COALESCE(oh.option_info, '')         AS optionInfo,
      COALESCE(oh.qty, 1)                  AS qty,
      COALESCE(oh.order_amount, 0)         AS orderAmount,
      COALESCE(oh.settlement, dl.expected_settlement, 0)    AS settlement,
      COALESCE(oh.shipping_fee, dl.customer_shipping_fee, 0) AS customerShippingFee,
      COALESCE(oh.product_no, '')          AS costcoProductNo,
      p.id                                  AS productId,
      COALESCE(p.naver_origin_pno, '')     AS naverOriginPno,
      COALESCE(p.naver_channel_pno, '')    AS naverChannelPno,
      COALESCE(p.match_keyword, '')        AS matchKeyword,
      COALESCE(p.costco_name, '')          AS costcoName,
      COALESCE(p.unit_price, 0)            AS unitPrice,
      COALESCE(p.split_qty, 1)             AS splitQty
    FROM dispatch_log dl
    LEFT JOIN order_history oh ON dl.order_no = oh.order_no
    LEFT JOIN products p
      ON (oh.product_no != '' AND p.product_no = oh.product_no)
      OR p.match_keyword = oh.product_name
    WHERE dl.dispatched_at = ?
    ORDER BY COALESCE(oh.product_name, dl.product_name), dl.id
  `).all(date) as any[];

  const profitRows: ProfitRow[] = rows.map(r => ({
    orderNo: String(r.orderNo),
    dispatchedAt: r.dispatchedAt,
    recipient: r.recipient,
    productName: r.productName,
    optionInfo: r.optionInfo,
    qty: Number(r.qty) || 1,
    orderAmount: Number(r.orderAmount) || 0,
    settlement: Number(r.settlement) || 0,
    customerShippingFee: Number(r.customerShippingFee) || 0,
    productId: r.productId,
    costcoProductNo: r.costcoProductNo || '',
    naverOriginPno: r.naverOriginPno || '',
    naverChannelPno: r.naverChannelPno || '',
    matchKeyword: r.matchKeyword || '',
    costcoName: r.costcoName || '',
    unitPrice: Number(r.unitPrice) || 0,
    splitQty: Math.max(1, Number(r.splitQty) || 1),
    matchSource: r.productId
      ? (r.naverOriginPno || r.costcoProductNo ? 'DB-번호' : 'DB-키워드')
      : '미매칭',
  }));

  // 설정 조회 (settings 테이블)
  const set = (k: string, fallback: any) => {
    try {
      const row = db.prepare("SELECT value FROM settings WHERE key=?").get(k) as any;
      return row?.value ?? fallback;
    } catch {
      return fallback;
    }
  };
  const settings: Settings = {
    shippingCost: parseInt(set('shipping_cost', '1800'), 10) || 1800,
    boxCost: parseInt(set('box_cost', '300'), 10) || 300,
    shippingCommissionRate: parseFloat(set('naver_ship_fee_commission_rate', '4.0')) || 4.0,
  };

  return NextResponse.json({ rows: profitRows, settings, date });
}
