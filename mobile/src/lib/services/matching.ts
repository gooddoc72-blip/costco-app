/**
 * Matching Service — 주문 ↔ 제품 자동 매칭.
 *
 * 우선순위 (안정도 높은 순):
 *   1. naver_origin_pno 일치  → 네이버 상품마다 고유
 *   2. product_no 일치        → 코스트코 상품번호 (1박스 단위)
 *   3. costco_name 정확 일치  → 상품명 매칭 (변경 위험)
 *   4. match_keyword 정확 일치
 *
 * 주문 수집 시점에 매칭하여 order_history.matched_product_id 저장 →
 * 사용자가 나중에 상품명 바꿔도 매입가 링크 안 깨짐.
 */
import { getUserDb } from '@/lib/db';

export interface MatchCandidate {
  /** 주문에서 추출 가능한 모든 식별자 */
  productNo?: string;        // 코스트코 상품번호 (사용자 입력)
  naverOriginPno?: string;   // 네이버 원상품번호
  naverChannelPno?: string;  // 네이버 채널 상품번호 (스마트스토어 productId)
  productName?: string;      // 주문 상품명
}

export interface MatchResult {
  productId: number | null;
  matchKey: 'naver_origin' | 'naver_channel' | 'product_no' | 'costco_name' | 'match_keyword' | null;
}

/** 단일 주문에 대해 매칭되는 products.id 찾기 */
export function findMatchingProductId(
  username: string,
  candidate: MatchCandidate
): MatchResult {
  const db = getUserDb(username);

  // 1순위: naver_origin_pno (네이버 원상품번호 — 가장 안정적)
  if (candidate.naverOriginPno) {
    const row = db.prepare(
      "SELECT id FROM products WHERE naver_origin_pno = ? AND naver_origin_pno != ''"
    ).get(candidate.naverOriginPno) as any;
    if (row) return { productId: row.id, matchKey: 'naver_origin' };
  }

  // 1.5순위: naver_channel_pno (스마트스토어 productId)
  if (candidate.naverChannelPno) {
    const row = db.prepare(
      "SELECT id FROM products WHERE naver_channel_pno = ? AND naver_channel_pno != ''"
    ).get(candidate.naverChannelPno) as any;
    if (row) return { productId: row.id, matchKey: 'naver_channel' };
  }

  // 2순위: product_no (코스트코 상품번호)
  if (candidate.productNo) {
    const row = db.prepare(
      "SELECT id FROM products WHERE product_no = ? AND product_no != ''"
    ).get(candidate.productNo) as any;
    if (row) return { productId: row.id, matchKey: 'product_no' };
  }

  // 3순위: costco_name 정확 일치
  if (candidate.productName) {
    const r1 = db.prepare(
      "SELECT id FROM products WHERE costco_name = ?"
    ).get(candidate.productName) as any;
    if (r1) return { productId: r1.id, matchKey: 'costco_name' };

    // 4순위: match_keyword 정확 일치
    const r2 = db.prepare(
      "SELECT id FROM products WHERE match_keyword = ?"
    ).get(candidate.productName) as any;
    if (r2) return { productId: r2.id, matchKey: 'match_keyword' };
  }

  return { productId: null, matchKey: null };
}

/** 여러 주문을 한 번에 매칭 (배치) */
export function batchMatch(
  username: string,
  candidates: Array<MatchCandidate & { orderNo: string }>
): Record<string, MatchResult> {
  const result: Record<string, MatchResult> = {};
  for (const c of candidates) {
    result[c.orderNo] = findMatchingProductId(username, c);
  }
  return result;
}
