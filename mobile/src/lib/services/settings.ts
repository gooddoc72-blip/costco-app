/**
 * Settings Service — UI키 ↔ DB키 매핑 + 타입 변환.
 */
import { getAllSettings, upsertSettings } from '@/lib/repositories/settings';

/** UI 친화 키 → DB 스네이크 키 매핑 (단일 진실 출처) */
export const SETTING_KEYS = {
  shippingCost: 'shipping_cost',
  boxCost: 'box_cost',
  shippingCommissionRate: 'naver_ship_fee_commission_rate',
  targetMargin: 'target_margin',
  maxIncreasePct: 'max_increase_pct',
  naverApiClientId: 'api_client_id',
  naverApiClientSecret: 'api_client_secret',
  naverOpenApiClientId: 'open_api_client_id',
  naverOpenApiClientSecret: 'open_api_client_secret',
  kakaoApiKey: 'kakao_api_key',
  kakaoAccessToken: 'kakao_access_token',
  kakaoRefreshToken: 'kakao_refresh_token',
  telegramToken: 'telegram_token',
  telegramChatId: 'telegram_chat_id',
  coupangAccessKey: 'coupang_access_key',
  coupangSecretKey: 'coupang_secret_key',
  coupangVendorId: 'coupang_vendor_id',
  costcoEmail: 'costco_email',
  costcoPassword: 'costco_password',
  excelPassword: 'excel_password',
} as const;

const NUMERIC_KEYS = new Set(['shippingCost', 'boxCost', 'targetMargin', 'maxIncreasePct']);
const FLOAT_KEYS = new Set(['shippingCommissionRate']);

const DEFAULTS: Record<string, any> = {
  shippingCost: 1800,
  boxCost: 300,
  shippingCommissionRate: 4.0,
  targetMargin: 10,
  maxIncreasePct: 20,
};

export function loadUserSettings(username: string): Record<string, any> {
  const raw = getAllSettings(username);
  const out: Record<string, any> = {};
  for (const [uiKey, dbKey] of Object.entries(SETTING_KEYS)) {
    const v = raw[dbKey] ?? '';
    if (NUMERIC_KEYS.has(uiKey)) out[uiKey] = parseInt(v, 10) || DEFAULTS[uiKey] || 0;
    else if (FLOAT_KEYS.has(uiKey)) out[uiKey] = parseFloat(v) || DEFAULTS[uiKey] || 0;
    else out[uiKey] = v;
  }
  return out;
}

export function saveUserSettings(username: string, uiPayload: Record<string, any>): void {
  const dbPayload: Record<string, string> = {};
  for (const [uiKey, dbKey] of Object.entries(SETTING_KEYS)) {
    if (uiKey in uiPayload) {
      dbPayload[dbKey] = String(uiPayload[uiKey]);
    }
  }
  upsertSettings(username, dbPayload);
}
