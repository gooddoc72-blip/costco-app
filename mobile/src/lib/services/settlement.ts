/**
 * Settlement Service — 발송건 vs 정산 내역 매칭, 배송비 수수료 분석.
 * 순수 비즈니스 로직 + repo orchestration.
 */
import { parseQuickSettleCsv, type SettlementRecord } from '@/lib/csv/quicksettle';
import {
  saveSettlementsFromCsv, fetchSettlementsByDate, deleteSettlementsByDate,
  fetchDispatchedForMatch,
  type SettlementRow, type DispatchedForMatch,
} from '@/lib/repositories/settlements';

const TOLERANCE = 10;

export interface MatchedRow {
  productOrderNo: string;
  recipient: string;
  productName: string;
  expected: number;
  actual: number;
  diff: number;
  salesAmount: number;
  commission: number;
}
export interface MissingRow {
  productOrderNo: string;
  recipient: string;
  productName: string;
  expected: number;
}
export interface OrphanRow {
  productOrderNo: string;
  orderNo: string;
  settleAmount: number;
  salesAmount: number;
}
export interface MatchSummary {
  shippedN: number;
  settledN: number;
  matchedN: number;
  mismatchedN: number;
  missingN: number;
  orphanN: number;
  totalExpected: number;
  totalActual: number;
  totalDiff: number;
}
export interface MatchResult {
  matched: MatchedRow[];
  mismatched: MatchedRow[];
  missing: MissingRow[];
  orphan: OrphanRow[];
  summary: MatchSummary;
}

export interface ShippingRow {
  productOrderNo: string;
  recipient: string;
  customerPaid: number;
  settled: number;
  commission: number;
  rate: number;
}
export interface ShippingAnalysis {
  rows: ShippingRow[];
  totalCustomerShipping: number;
  totalSettledShipping: number;
  totalCommission: number;
  avgCommissionRate: number;
}

export interface SettlementPageData {
  settled: SettlementRow[];
  dispatched: DispatchedForMatch[];
  match: MatchResult;
  shipping: ShippingAnalysis;
}

function matchShippedVsSettled(
  shipped: DispatchedForMatch[],
  settled: SettlementRow[],
): MatchResult {
  const settledByPo = new Map(settled.map(s => [s.productOrderNo, s]));
  const matched: MatchedRow[] = [];
  const mismatched: MatchedRow[] = [];
  const missing: MissingRow[] = [];
  const orphan: OrphanRow[] = [];
  let totalExpected = 0;
  let totalActual = 0;
  const shippedPos = new Set<string>();

  for (const s of shipped) {
    const po = s.orderNo;
    shippedPos.add(po);
    const exp = s.expectedSettlement;
    totalExpected += exp;
    const sr = settledByPo.get(po);
    if (sr) {
      const actual = sr.settleAmount;
      totalActual += actual;
      const diff = actual - exp;
      const rec: MatchedRow = {
        productOrderNo: po,
        recipient: s.recipient,
        productName: s.productName,
        expected: exp,
        actual,
        diff,
        salesAmount: sr.salesAmount,
        commission: sr.commission,
      };
      (Math.abs(diff) <= TOLERANCE ? matched : mismatched).push(rec);
    } else {
      missing.push({
        productOrderNo: po,
        recipient: s.recipient,
        productName: s.productName,
        expected: exp,
      });
    }
  }
  for (const sr of settled) {
    if (!shippedPos.has(sr.productOrderNo)) {
      orphan.push({
        productOrderNo: sr.productOrderNo,
        orderNo: sr.orderNo,
        settleAmount: sr.settleAmount,
        salesAmount: sr.salesAmount,
      });
    }
  }
  return {
    matched, mismatched, missing, orphan,
    summary: {
      shippedN: shipped.length,
      settledN: settled.length,
      matchedN: matched.length,
      mismatchedN: mismatched.length,
      missingN: missing.length,
      orphanN: orphan.length,
      totalExpected, totalActual,
      totalDiff: totalActual - totalExpected,
    },
  };
}

function analyzeShippingCommission(
  dispatched: DispatchedForMatch[],
  settled: SettlementRow[],
): ShippingAnalysis {
  const settledByPo = new Map(settled.map(s => [s.productOrderNo, s]));
  const rows: ShippingRow[] = [];
  let totalCust = 0;
  let totalSett = 0;
  for (const d of dispatched) {
    const sr = settledByPo.get(d.orderNo);
    if (!sr) continue;
    const cust = d.customerShippingFee;
    const sett = sr.shippingAmount;
    const comm = cust - sett;
    totalCust += cust;
    totalSett += sett;
    rows.push({
      productOrderNo: d.orderNo,
      recipient: d.recipient,
      customerPaid: cust,
      settled: sett,
      commission: comm,
      rate: cust > 0 ? Math.round(comm / cust * 10000) / 100 : 0,
    });
  }
  const totalCommission = totalCust - totalSett;
  return {
    rows,
    totalCustomerShipping: totalCust,
    totalSettledShipping: totalSett,
    totalCommission,
    avgCommissionRate: totalCust > 0
      ? Math.round(totalCommission / totalCust * 10000) / 100 : 0,
  };
}

export interface UploadResult {
  parsed: number;
  saved: number;
  quickN: number;
  claimN: number;
  productSum: number;
  shippingSum: number;
  totalSum: number;
}

export function uploadCsv(username: string, buf: Buffer): UploadResult {
  const parsed = parseQuickSettleCsv(buf);
  const saved = saveSettlementsFromCsv(username, parsed);
  let quickN = 0, claimN = 0, productSum = 0, shippingSum = 0, totalSum = 0;
  for (const r of parsed) {
    if (r.settleType === '빠른정산') quickN++;
    else if (r.settleType === '공제') claimN++;
    productSum  += r.productAmount;
    shippingSum += r.shippingAmount;
    totalSum    += r.totalAmount;
  }
  return { parsed: parsed.length, saved, quickN, claimN, productSum, shippingSum, totalSum };
}

export function getSettlementPage(
  username: string, settleDate: string, shipDate: string,
): SettlementPageData {
  const settled = fetchSettlementsByDate(username, settleDate);
  const dispatched = fetchDispatchedForMatch(username, shipDate);
  const match = matchShippedVsSettled(dispatched, settled);
  const shipping = analyzeShippingCommission(dispatched, settled);
  return { settled, dispatched, match, shipping };
}

export function clearSettleDate(username: string, settleDate: string): number {
  return deleteSettlementsByDate(username, settleDate);
}
