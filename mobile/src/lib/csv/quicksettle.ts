/**
 * 네이버 QuickSettleByCase CSV 파서 (서버사이드, EUC-KR).
 *
 * 한 주문 = 두 행(상품주문 + 배송비)을 합산해 dict 1개로 반환.
 * 순수 함수 — DB/네트워크 의존 없음.
 */

export interface SettlementRecord {
  productOrderNo: string;
  orderNo: string;
  buyerName: string;
  productName: string;
  payDate: string;
  settleCompleteDate: string;
  settleBasisDate: string;
  settleType: string;
  reason: string;
  productAmount: number;
  shippingAmount: number;
  totalAmount: number;
  dispatchAt: string;
}

function decodeBuffer(buf: Buffer): string {
  for (const enc of ['euc-kr', 'utf-8'] as const) {
    try {
      return new TextDecoder(enc, { fatal: true }).decode(buf);
    } catch { /* try next */ }
  }
  return new TextDecoder('utf-8').decode(buf);
}

function toInt(v: string | undefined): number {
  if (!v) return 0;
  const s = v.replace(/,/g, '').trim();
  if (!s) return 0;
  const n = parseFloat(s);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

function normDate(s: string | undefined): string {
  if (!s) return '';
  return s.trim().replace(/\./g, '-').replace(/\//g, '-');
}

/** 1행 CSV 분할 — 쌍따옴표 안의 콤마 보호 */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') { inQuote = !inQuote; continue; }
    if (c === ',' && !inQuote) { out.push(cur); cur = ''; continue; }
    cur += c;
  }
  out.push(cur);
  return out.map(s => s.trim());
}

export function parseQuickSettleCsv(buf: Buffer): SettlementRecord[] {
  const text = decodeBuffer(buf);
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (lines.length < 2) return [];
  const headers = splitCsvLine(lines[0]);

  const idx = (name: string) => headers.findIndex(h => h.replace(/\s/g, '') === name.replace(/\s/g, ''));
  const I = {
    poMain:   idx('상품주문번호'),
    poBasis:  idx('배송비 정산기준 상품주문번호'),
    gubun:    idx('구분'),
    amount:   idx('금액'),
    orderNo:  idx('주문번호'),
    buyer:    idx('구매자명'),
    product:  idx('상품명'),
    payDate:  idx('결제일'),
    completeDate: idx('정산완료일'),
    basisDate:    idx('정산기준일'),
    settleType:   idx('정산구분'),
    reason:       idx('사유'),
    dispatchAt:   idx('집화처리(배송시작) 일시'),
  };

  const byPo = new Map<string, SettlementRecord>();
  const ensure = (po: string): SettlementRecord => {
    let d = byPo.get(po);
    if (!d) {
      d = {
        productOrderNo: po, orderNo: '', buyerName: '', productName: '',
        payDate: '', settleCompleteDate: '', settleBasisDate: '',
        settleType: '', reason: '',
        productAmount: 0, shippingAmount: 0, totalAmount: 0, dispatchAt: '',
      };
      byPo.set(po, d);
    }
    return d;
  };

  for (let i = 1; i < lines.length; i++) {
    const c = splitCsvLine(lines[i]);
    const gubun = c[I.gubun] || '';
    const amount = toInt(c[I.amount]);
    const poMain = c[I.poMain] || '';
    const poBasis = c[I.poBasis] || '';

    if (gubun === '상품주문' && poMain) {
      const d = ensure(poMain);
      d.productAmount += amount;
      d.orderNo            = c[I.orderNo] || '';
      d.buyerName          = c[I.buyer] || '';
      d.productName        = c[I.product] || '';
      d.payDate            = normDate(c[I.payDate]);
      d.settleCompleteDate = normDate(c[I.completeDate]);
      d.settleBasisDate    = normDate(c[I.basisDate]);
      d.settleType         = c[I.settleType] || '';
      d.reason             = c[I.reason] || '';
      d.dispatchAt         = c[I.dispatchAt] || '';
    } else if (gubun === '배송비') {
      const targetPo = poBasis || poMain;
      if (!targetPo) continue;
      const d = ensure(targetPo);
      d.shippingAmount += amount;
      if (!d.orderNo)            d.orderNo            = c[I.orderNo] || '';
      if (!d.buyerName)          d.buyerName          = c[I.buyer] || '';
      if (!d.payDate)            d.payDate            = normDate(c[I.payDate]);
      if (!d.settleCompleteDate) d.settleCompleteDate = normDate(c[I.completeDate]);
      if (!d.settleBasisDate)    d.settleBasisDate    = normDate(c[I.basisDate]);
      if (!d.settleType)         d.settleType         = c[I.settleType] || '';
      if (!d.reason)             d.reason             = c[I.reason] || '';
      if (!d.dispatchAt)         d.dispatchAt         = c[I.dispatchAt] || '';
    }
  }

  const out = Array.from(byPo.values());
  for (const d of out) d.totalAmount = d.productAmount + d.shippingAmount;
  return out;
}
