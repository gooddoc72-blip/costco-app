/**
 * 바코드 스캔 API 클라이언트 — 파이썬 API(/api/scan/*)를 부른다.
 * 바코드 ↔ 상품 연결·조회·매장 가격 저장은 전부 파이썬(scan_service)이 한다.
 */
import { call } from './billing';

export interface ScanStock { mine: number; total: number; owners?: { owner: string; qty: number }[] }
export type ScanItem =
  | { found: false; code: string }
  | {
      found: true; code: string; product_no: string; name: string;
      cur_price: number; online_price: number; price_at: string; split_qty: number;
      image: string; label_barcode: string; barcodes: string[]; stock: ScanStock;
      /** 가격표 바코드에서 상품번호를 찾아 이번에 바코드를 자동 연결했다 */
      auto_linked?: boolean;
    };
export type ScanFound = Extract<ScanItem, { found: true }>;

const B = '/api/scan';
export const scan = {
  lookup: (code: string) => call<ScanItem>('GET', `${B}/lookup/${encodeURIComponent(code)}`),
  link: (barcode: string, product_no: string) => call<ScanItem>('POST', `${B}/link`, { barcode, product_no }),
  price: (p: { code: string; product_no: string; name: string; label_barcode: string;
               new_price: number; final_price?: number; discount?: number; sale_end?: string }) =>
    call<{ saved: number; same: number; batch: string; item: ScanItem }>('POST', `${B}/price`, p),
};
