/**
 * 가격 계산 순수 함수 — 저장과 매칭의 대칭성 보장.
 *
 * 핵심 공식:
 *   매칭(매입가 계산):  cost = (unitPrice / splitQty) × (qty × sellFactor)
 *   저장(단가 계산):    unitPrice = (cost × splitQty) / (qty × sellFactor)
 *
 * 두 함수는 정확히 역연산 — 저장한 그대로 다시 불러와도 같은 값 보장.
 */

import type { ProfitRow, ProfitCalc, Settings } from './types';

/** 상품명에 "x N개" 묶음 표기가 있으면 N 추출 (1주문 = N개) */
export function extractSellFactor(productName: string): number {
  const m = productName.match(/x\s*(\d+)\s*개/i);
  if (!m) return 1;
  const n = parseInt(m[1], 10);
  if (Number.isFinite(n) && n > 1 && n <= 50) return n;
  return 1;
}

/** unit_price + split_qty + qty + sell_factor → 매입가 */
export function computeCost(
  unitPrice: number,
  splitQty: number,
  qty: number,
  sellFactor: number
): number {
  const sq = Math.max(1, splitQty | 0);
  const q = Math.max(1, qty | 0);
  const sf = Math.max(1, sellFactor | 0);
  return Math.floor(unitPrice / sq) * (q * sf);
}

/** 매입가 → unit_price (저장용) — computeCost의 역함수 */
export function unitPriceFromCost(
  cost: number,
  splitQty: number,
  qty: number,
  sellFactor: number
): number {
  const sq = Math.max(1, splitQty | 0);
  const q = Math.max(1, qty | 0);
  const sf = Math.max(1, sellFactor | 0);
  const denom = Math.max(1, q * sf);
  return Math.floor((cost * sq) / denom);
}

/** ProfitRow + 사용자 매입가 + 설정 → 전체 계산 */
export function calcProfit(
  row: Pick<ProfitRow, 'settlement' | 'customerShippingFee'>,
  cost: number,
  settings: Settings
): ProfitCalc {
  const factor = Math.max(0, 1 - settings.shippingCommissionRate / 100);
  const shippingSettleAmount = Math.round(row.customerShippingFee * factor);
  const shippingCommission = row.customerShippingFee - shippingSettleAmount;
  const totalIncome = row.settlement + shippingSettleAmount;
  const totalCost = cost + settings.shippingCost + settings.boxCost;
  return {
    computedCost: cost,
    shippingSettleAmount,
    shippingCommission,
    totalIncome,
    totalCost,
    profit: totalIncome - totalCost,
  };
}

/** ProfitRow에서 자동 매입가 (저장된 unit_price 기준) */
export function autoCostFromRow(row: ProfitRow): number {
  if (!row.unitPrice || row.unitPrice <= 0) return 0;
  const sellFactor = extractSellFactor(row.productName);
  return computeCost(row.unitPrice, row.splitQty, row.qty, sellFactor);
}
