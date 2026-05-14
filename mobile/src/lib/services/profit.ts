/**
 * Profit Service — 수익계산 데이터 + 설정 통합.
 */
import { fetchDispatchedRows } from '@/lib/repositories/profit';
import { loadUserSettings } from '@/lib/services/settings';
import { upsertProduct } from '@/lib/repositories/products';
import type { ProfitRow, Settings, PriceSaveItem } from '@/lib/types';

export interface ProfitPageData {
  rows: ProfitRow[];
  settings: Settings;
  date: string;
}

export function getProfitPageData(username: string, date: string): ProfitPageData {
  const rows = fetchDispatchedRows(username, date);
  const allSettings = loadUserSettings(username);
  const settings: Settings = {
    shippingCost: allSettings.shippingCost,
    boxCost: allSettings.boxCost,
    shippingCommissionRate: allSettings.shippingCommissionRate,
  };
  return { rows, settings, date };
}

export function saveProductPrices(username: string, items: PriceSaveItem[]): {
  saved: number; rejected: number; errors: string[]; warnings: string[];
} {
  let saved = 0;
  let rejected = 0;
  const errors: string[] = [];
  const warnings: string[] = [];
  for (const it of items) {
    const res = upsertProduct(username, {
      matchKeyword: it.matchKeyword,
      unitPrice: it.boxPrice,
      splitQty: Math.max(1, it.splitQty),
      productNo: it.costcoProductNo,
      naverOriginPno: it.naverOriginPno,
      detectBoxPrice: true,  // ⭐ 박스가격 안전장치 활성화
      autoSplitCostcoNo: true,  // ⭐ 같은 코스트코번호 다중 행이면 자동 분리
    });
    if (res.saved) {
      saved++;
      if (res.warning) warnings.push(`${it.naverOriginPno || it.matchKeyword}: ${res.warning}`);
    } else if (res.rejected) {
      rejected++;
      warnings.push(`${it.naverOriginPno || it.matchKeyword}: ${res.warning}`);
    } else {
      errors.push(`${it.naverOriginPno || it.matchKeyword}: ${res.error}`);
    }
  }
  return { saved, rejected, errors, warnings };
}
