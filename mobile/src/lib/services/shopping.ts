/**
 * Shopping Service — 주문을 집계해 코스트코 장보기 목록 생성.
 *
 * 규칙:
 *  - 그룹키: (productNo, productName, optionInfo)
 *  - 분리판매(splitQty>1): 코스트코구매수량 = ceil(주문수량 / splitQty)
 *  - 묶음판매(packQty>1):  코스트코구매수량 = 주문수량 × packQty
 *  - 예상금액 = 코스트코구매수량 × 팩단가
 */
import { getUserDb } from '@/lib/db';
import { fetchOrdersForShopping, fetchOrdersForDailyDump, type ShoppingRawRow } from '@/lib/repositories/orders';
import { findMatchingProductId } from '@/lib/services/matching';
import { extractPackQty } from '@/lib/utils/packQty';
import { computeCost, extractSellFactor } from '@/lib/pricing';
import { replaceDailyOrders, type DailyOrderInput } from '@/lib/repositories/dailyOrders';
import { loadUserSettings } from '@/lib/services/settings';
import { submitShoppingList as repoSubmit } from '@/lib/repositories/adminShopping';

export interface ShoppingItem {
  productNo: string;
  productName: string;
  optionInfo: string;
  orderCount: number;     // 동일 상품의 고객(주문) 수
  qty: number;            // 주문 총 수량
  packQty: number;        // 옵션에서 추출한 묶음수량
  splitQty: number;       // DB의 분리수량
  costcoQty: number;      // 실제 코스트코에서 구매할 개수
  unitPrice: number | null; // DB 팩단가 (없으면 null)
  expectedCost: number | null; // 예상 구매금액 (null = 단가 미등록)
  shippingFee: number;    // 건당 배송비 (평균)
  totalSettlement: number;
}

export interface ShoppingPageData {
  date: string;
  items: ShoppingItem[];
  totalExpected: number;
  totalSettlement: number;
  unregistered: number;   // 단가 미등록 상품 종 수
}

interface GroupAcc {
  productNo: string;
  productName: string;
  optionInfo: string;
  orderCount: number;
  qty: number;
  shippingFeeSum: number;
  shippingFeeCount: number;
  settlementSum: number;
  matchedProductIds: Set<number>;
}

function lookupProductUnit(
  username: string,
  productNo: string,
  productName: string,
  matchedId: number | null,
): { unitPrice: number | null; splitQty: number } {
  const db = getUserDb(username);
  let row: any = null;
  if (matchedId) {
    row = db.prepare("SELECT unit_price, split_qty FROM products WHERE id = ?").get(matchedId);
  }
  if (!row) {
    const m = findMatchingProductId(username, { productNo, productName });
    if (m.productId) {
      row = db.prepare("SELECT unit_price, split_qty FROM products WHERE id = ?").get(m.productId);
    }
  }
  if (!row) return { unitPrice: null, splitQty: 1 };
  const up = Number(row.unit_price) || 0;
  return {
    unitPrice: up > 0 ? up : null,
    splitQty: Math.max(1, Number(row.split_qty) || 1),
  };
}

export function getShoppingList(username: string, date: string): ShoppingPageData {
  const raw = fetchOrdersForShopping(username, date);
  const groups = new Map<string, GroupAcc>();

  for (const r of raw) {
    const key = `${r.productNo}|${r.productName}|${r.optionInfo}`;
    let g = groups.get(key);
    if (!g) {
      g = {
        productNo: r.productNo,
        productName: r.productName,
        optionInfo: r.optionInfo,
        orderCount: 0,
        qty: 0,
        shippingFeeSum: 0,
        shippingFeeCount: 0,
        settlementSum: 0,
        matchedProductIds: new Set(),
      };
      groups.set(key, g);
    }
    g.orderCount += 1;
    g.qty += r.qty;
    g.shippingFeeSum += r.shippingFee;
    g.shippingFeeCount += 1;
    g.settlementSum += r.settlement;
    if (r.matchedProductId) g.matchedProductIds.add(r.matchedProductId);
  }

  const items: ShoppingItem[] = [];
  let totalExpected = 0;
  let unregistered = 0;

  for (const g of groups.values()) {
    const matchedId = g.matchedProductIds.size === 1
      ? Array.from(g.matchedProductIds)[0] : null;
    const { unitPrice, splitQty } = lookupProductUnit(username, g.productNo, g.productName, matchedId);
    const packQty = extractPackQty(g.optionInfo);
    const costcoQty = splitQty > 1
      ? Math.ceil(g.qty / splitQty)
      : g.qty * packQty;
    const expectedCost = unitPrice == null ? null : costcoQty * unitPrice;
    if (expectedCost != null) totalExpected += expectedCost;
    if (unitPrice == null) unregistered++;
    const shippingFee = g.shippingFeeCount > 0
      ? Math.round(g.shippingFeeSum / g.shippingFeeCount) : 0;
    items.push({
      productNo: g.productNo,
      productName: g.productName,
      optionInfo: g.optionInfo,
      orderCount: g.orderCount,
      qty: g.qty,
      packQty,
      splitQty,
      costcoQty,
      unitPrice,
      expectedCost,
      shippingFee,
      totalSettlement: g.settlementSum,
    });
  }

  items.sort((a, b) =>
    a.productNo.localeCompare(b.productNo) ||
    a.productName.localeCompare(b.productName));

  const totalSettlement = items.reduce((s, i) => s + i.totalSettlement, 0);
  return { date, items, totalExpected, totalSettlement, unregistered };
}

const NF = new Intl.NumberFormat('ko-KR');
function fmtN(n: number): string { return NF.format(n); }

/** 한 날짜의 주문을 daily_orders에 dump — 수익계산 페이지의 영구 소스가 됨. */
export function saveDailyFromOrderHistory(username: string, date: string): {
  saved: number;
  matched: number;
  totalProfit: number;
} {
  const rows = fetchOrdersForDailyDump(username, date);
  const settings = loadUserSettings(username);
  const shippingCost = Number(settings.shippingCost) || 1800;
  const boxCost = Number(settings.boxCost) || 300;
  const db = getUserDb(username);

  let matched = 0;
  let totalProfit = 0;
  const items: DailyOrderInput[] = rows.map(r => {
    let prod: any = null;
    if (r.matchedProductId) {
      prod = db.prepare("SELECT unit_price, split_qty FROM products WHERE id = ?").get(r.matchedProductId);
    }
    if (!prod) {
      const m = findMatchingProductId(username, { productNo: r.productNo, productName: r.productName });
      if (m.productId) prod = db.prepare("SELECT unit_price, split_qty FROM products WHERE id = ?").get(m.productId);
    }
    const unitPrice = Number(prod?.unit_price) || 0;
    const splitQty = Math.max(1, Number(prod?.split_qty) || 1);
    const sellFactor = extractSellFactor(r.productName);
    const cost = unitPrice > 0 ? computeCost(unitPrice, splitQty, r.qty, sellFactor) : 0;
    const profit = cost > 0
      ? (r.settlement + r.shippingFee) - (cost + shippingCost + boxCost)
      : 0;
    if (cost > 0) { matched++; totalProfit += profit; }
    return {
      orderDate: date,
      recipient: r.recipient,
      productName: r.productName,
      productNo: r.productNo,
      optionInfo: r.optionInfo,
      qty: r.qty,
      orderAmount: r.orderAmount,
      shippingFee: r.shippingFee,
      extraShipping: 0,
      settlement: r.settlement,
      costPrice: cost,
      deliveryCost: shippingCost,
      boxCost: boxCost,
      profit,
      matched: cost > 0 ? 1 : 0,
    };
  });
  const saved = replaceDailyOrders(username, date, items);
  return { saved, matched, totalProfit };
}

/** 장보기 목록을 관리자 DB(shopping_list_submissions)에 제출. */
export function submitToAdmin(username: string, date: string): {
  submissionId: number; totalItems: number; totalAmount: number;
} {
  const data = getShoppingList(username, date);
  const totalAmount = data.items.reduce((s, i) => s + (i.expectedCost || 0), 0);
  const id = repoSubmit(username, date, data.items, data.items.length, totalAmount);
  return { submissionId: id, totalItems: data.items.length, totalAmount };
}

/** 카톡/텔레그램용 메시지 문자열. (예상금액 제외, 사용자 요구사항대로) */
export function buildShoppingMessage(data: ShoppingPageData): string {
  const [, mm, dd] = data.date.split('-');
  const lines: string[] = [`🛒 코스트코 장보기 (${mm}/${dd})`, ''];
  for (const it of data.items) {
    const name = it.productName.slice(0, 30);
    const opt = (it.optionInfo || '').trim() || '-';
    const unit = it.unitPrice ?? 0;
    lines.push(`${name} - ${opt} - ${it.qty} - ${fmtN(unit)} - 배송 ${fmtN(it.shippingFee)}`);
  }
  lines.push('');
  lines.push(`💰 예상 총액: ${fmtN(data.totalExpected)}원 / 📦 ${data.items.length}종`);
  return lines.join('\n');
}
