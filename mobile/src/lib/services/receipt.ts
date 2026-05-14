/**
 * Receipt Service — PDF 파싱 + product_no 기준 매입가 업데이트.
 *
 * 매칭은 1차로 product_no 정확 일치만. (이름 fuzzy 매칭은 Phase 2)
 * 박스가격 안전장치(5배 룰)는 upsertProduct가 처리.
 */
import { parseCostcoReceiptPdf, type ReceiptItem } from '@/lib/pdf/costcoReceipt';
import { upsertProduct, findProductByKey } from '@/lib/repositories/products';
import { getUserDb } from '@/lib/db';

export type { ReceiptItem };

export interface ParseSummary {
  items: ReceiptItem[];
  errors: Array<{ file: string; error: string; preview?: string }>;
}

export async function parseMany(files: Array<{ name: string; buf: Buffer }>): Promise<ParseSummary> {
  const all: ReceiptItem[] = [];
  const errors: ParseSummary['errors'] = [];
  for (const f of files) {
    const r = await parseCostcoReceiptPdf(f.buf, f.name);
    if (r.error) errors.push({ file: f.name, error: r.error, preview: r.preview });
    else all.push(...r.items);
  }
  // 같은 productNo 중 영수증날짜 최신 우선
  const byNo = new Map<string, ReceiptItem>();
  for (const it of all) {
    const existing = byNo.get(it.productNo);
    if (!existing || (it.receiptDate > existing.receiptDate)) byNo.set(it.productNo, it);
  }
  return { items: Array.from(byNo.values()), errors };
}

export interface ApplyResult {
  updated: number;
  rejected: number;     // 박스가격 안전장치로 거부
  notFound: number;     // products 테이블에 없음
  warnings: string[];
  details: Array<{
    productNo: string; productName: string; receiptPrice: number;
    status: 'updated' | 'rejected' | 'not_found';
    note?: string;
  }>;
}

/**
 * 영수증 파싱 결과를 products 테이블에 반영.
 *  - product_no 일치하는 행이 있을 때만 unit_price 갱신
 *  - 새 가격이 기존의 5배 초과면 박스가격으로 의심하고 거부
 */
export function applyReceiptToProducts(
  username: string, items: ReceiptItem[],
): ApplyResult {
  const db = getUserDb(username);
  const out: ApplyResult = { updated: 0, rejected: 0, notFound: 0, warnings: [], details: [] };
  for (const it of items) {
    const existing = findProductByKey(username, { productNo: it.productNo });
    if (!existing) {
      out.notFound++;
      out.details.push({
        productNo: it.productNo, productName: it.productName,
        receiptPrice: it.unitPrice, status: 'not_found',
      });
      continue;
    }
    const row = db.prepare(
      "SELECT match_keyword, costco_name FROM products WHERE id = ?"
    ).get(existing.id) as any;
    const r = upsertProduct(username, {
      matchKeyword: row?.match_keyword || it.productName,
      costcoName: row?.costco_name || it.productName,
      unitPrice: it.unitPrice,
      splitQty: 1,
      productNo: it.productNo,
      detectBoxPrice: true,
    });
    if (r.rejected) {
      out.rejected++;
      out.warnings.push(`${it.productNo}: ${r.warning}`);
      out.details.push({
        productNo: it.productNo, productName: it.productName,
        receiptPrice: it.unitPrice, status: 'rejected', note: r.warning,
      });
    } else if (r.saved) {
      out.updated++;
      out.details.push({
        productNo: it.productNo, productName: it.productName,
        receiptPrice: it.unitPrice, status: 'updated',
      });
    }
  }
  return out;
}
