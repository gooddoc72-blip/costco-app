/**
 * GET/PATCH /api/settings — 사용자 설정 (key/value)
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getUserDb } from '@/lib/db';

// 사용자 친화 키 → DB 키 매핑
const KEY_MAP: Record<string, string> = {
  shippingCost: 'shipping_cost',
  boxCost: 'box_cost',
  shippingCommissionRate: 'naver_ship_fee_commission_rate',
  targetMargin: 'target_margin',
  maxIncreasePct: 'max_increase_pct',
  // API 키
  naverApiClientId: 'api_client_id',
  naverApiClientSecret: 'api_client_secret',
  naverOpenApiClientId: 'open_api_client_id',
  naverOpenApiClientSecret: 'open_api_client_secret',
  // 카톡 / 텔레그램
  kakaoApiKey: 'kakao_api_key',
  kakaoAccessToken: 'kakao_access_token',
  kakaoRefreshToken: 'kakao_refresh_token',
  telegramToken: 'telegram_token',
  telegramChatId: 'telegram_chat_id',
  // 쿠팡
  coupangAccessKey: 'coupang_access_key',
  coupangSecretKey: 'coupang_secret_key',
  coupangVendorId: 'coupang_vendor_id',
  // 코스트코 (크롤링)
  costcoEmail: 'costco_email',
  costcoPassword: 'costco_password',
  excelPassword: 'excel_password',
};

export async function GET() {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const db = getUserDb(user.username);
  const rows = db.prepare("SELECT key, value FROM settings").all() as { key: string; value: string }[];
  const dbMap: Record<string, string> = {};
  for (const r of rows) dbMap[r.key] = r.value;

  const out: Record<string, any> = {};
  for (const [k, dbk] of Object.entries(KEY_MAP)) {
    out[k] = dbMap[dbk] ?? '';
  }
  // 숫자 변환
  out.shippingCost = parseInt(out.shippingCost) || 1800;
  out.boxCost = parseInt(out.boxCost) || 300;
  out.shippingCommissionRate = parseFloat(out.shippingCommissionRate) || 4.0;
  out.targetMargin = parseInt(out.targetMargin) || 10;
  out.maxIncreasePct = parseInt(out.maxIncreasePct) || 20;
  return NextResponse.json(out);
}

export async function PATCH(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const body = await req.json() as Record<string, any>;

  const db = getUserDb(user.username);
  const upsert = db.prepare(
    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
  );
  const tx = db.transaction(() => {
    for (const [k, v] of Object.entries(body)) {
      const dbk = KEY_MAP[k];
      if (!dbk) continue;
      upsert.run(dbk, String(v));
    }
  });
  tx();
  return NextResponse.json({ ok: true });
}
