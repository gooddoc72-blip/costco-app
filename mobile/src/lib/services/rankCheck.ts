/**
 * Rank Check Service — 키워드 등록/삭제 + 일괄 순위 체크.
 */
import {
  addTracking, deleteTracking, listLatestRanks, getTrackingById, saveRankResult,
  type LatestRow,
} from '@/lib/repositories/rankTracking';
import { checkKeywordRank } from '@/lib/api/naverShopSearch';
import { loadUserSettings } from '@/lib/services/settings';

export type { LatestRow };

export interface RankPageData {
  rows: LatestRow[];
  hasApiKey: boolean;
}

export function getRankPage(username: string): RankPageData {
  const settings = loadUserSettings(username);
  return {
    rows: listLatestRanks(username),
    hasApiKey: !!settings.naverOpenApiClientId && !!settings.naverOpenApiClientSecret,
  };
}

export function addKeyword(
  username: string, productKeyword: string, searchKeyword: string,
  naverProductNo: string = '', storeName: string = '',
): number {
  const k = (s: string) => s.trim();
  if (!k(productKeyword) || !k(searchKeyword)) {
    throw new Error('상품 키워드와 검색 키워드는 필수입니다.');
  }
  return addTracking(username, k(productKeyword), k(searchKeyword), k(naverProductNo), k(storeName));
}

export function removeKeyword(username: string, id: number): void {
  deleteTracking(username, id);
}

export interface CheckSummary {
  total: number;
  matched: number;
  notFound: number;
  errors: string[];
  results: Array<{
    id: number;
    keyword: string;
    rankWonbu: number | null;
    rankCompare: number | null;
    rankSolo: number | null;
  }>;
}

export async function checkAllRanks(username: string): Promise<CheckSummary> {
  const settings = loadUserSettings(username);
  const id = settings.naverOpenApiClientId;
  const sec = settings.naverOpenApiClientSecret;
  if (!id || !sec) throw new Error('네이버 Open API 키가 설정되지 않았습니다.');
  const trackings = listLatestRanks(username);
  const errors: string[] = [];
  const results: CheckSummary['results'] = [];
  let matched = 0, notFound = 0;

  for (const t of trackings) {
    const r = await checkKeywordRank({
      clientId: id, clientSecret: sec,
      keyword: t.searchKeyword,
      ourProductName: t.productKeyword,
      naverProductNo: t.naverProductNo,
      storeName: t.storeName,
    });
    if (r.error) {
      errors.push(`'${t.searchKeyword}': ${r.error}`);
    } else {
      saveRankResult(username, t.id, r.rankWonbu, r.rankSolo, r.rankCompare);
      if (r.rankWonbu != null || r.rankCompare != null || r.rankSolo != null) matched++;
      else notFound++;
    }
    results.push({
      id: t.id,
      keyword: t.searchKeyword,
      rankWonbu: r.rankWonbu,
      rankCompare: r.rankCompare,
      rankSolo: r.rankSolo,
    });
  }
  return { total: trackings.length, matched, notFound, errors, results };
}

export async function checkOneRank(username: string, id: number): Promise<{
  rankWonbu: number | null;
  rankCompare: number | null;
  rankSolo: number | null;
  matchInfo?: string;
}> {
  const settings = loadUserSettings(username);
  const cid = settings.naverOpenApiClientId;
  const sec = settings.naverOpenApiClientSecret;
  if (!cid || !sec) throw new Error('네이버 Open API 키가 설정되지 않았습니다.');
  const t = getTrackingById(username, id);
  if (!t) throw new Error('추적 항목이 없습니다.');
  const r = await checkKeywordRank({
    clientId: cid, clientSecret: sec,
    keyword: t.searchKeyword,
    ourProductName: t.productKeyword,
    naverProductNo: t.naverProductNo,
    storeName: t.storeName,
  });
  if (r.error) throw new Error(r.error);
  saveRankResult(username, id, r.rankWonbu, r.rankSolo, r.rankCompare);
  return {
    rankWonbu: r.rankWonbu,
    rankCompare: r.rankCompare,
    rankSolo: r.rankSolo,
    matchInfo: r.matchInfo,
  };
}
