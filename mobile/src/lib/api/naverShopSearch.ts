/**
 * 네이버 Open API — 쇼핑 검색 + 우리 상품 순위 매칭.
 *
 * 반환 (rank_wonbu, rank_compare, rank_solo):
 *  - wonbu (원부, 가격비교 모음): hprice > 0
 *  - compare (가격비교 매칭 일반): productType === '2'
 *  - solo (단독): 그 외
 *
 * 우선순위 매칭: 1) productId 정확  2) store+name(sim≥0.40)  3) name(sim≥0.60)
 */

const SHOP_URL = 'https://openapi.naver.com/v1/search/shop.json';

export interface RankResult {
  rankWonbu: number | null;
  rankCompare: number | null;
  rankSolo: number | null;
  error?: string;
  matchInfo?: string;
}

interface ShopItem {
  cls: '원부' | '가격비교' | '단독';
  pos: number;
  mallPid: string;
  title: string;
  mall: string;
  ptype: string;
  hp: number;
}

function strip(html: string): string {
  return html.replace(/<\/?b>/g, '').trim();
}

function classify(item: any): ShopItem['cls'] {
  const hp = Number(item?.hprice) || 0;
  if (hp > 0) return '원부';
  if (String(item?.productType || '') === '2') return '가격비교';
  return '단독';
}

function trigrams(s: string): Set<string> {
  const clean = s.toLowerCase().replace(/[^\w가-힣]/g, '');
  if (clean.length < 3) return new Set();
  const out = new Set<string>();
  for (let i = 0; i <= clean.length - 3; i++) out.add(clean.slice(i, i + 3));
  return out;
}

function jaccard(a: string, b: string): number {
  const A = trigrams(a);
  const B = trigrams(b);
  if (A.size === 0 && B.size === 0) return 0;
  let inter = 0;
  for (const t of A) if (B.has(t)) inter++;
  return inter / (A.size + B.size - inter);
}

export async function checkKeywordRank(opt: {
  clientId: string;
  clientSecret: string;
  keyword: string;
  ourProductName?: string;
  naverProductNo?: string;
  storeName?: string;
  maxPages?: number;
}): Promise<RankResult> {
  const maxPages = opt.maxPages ?? 10;
  const headers = {
    'X-Naver-Client-Id': opt.clientId,
    'X-Naver-Client-Secret': opt.clientSecret,
  };
  let posWonbu = 0, posCompare = 0, posSolo = 0;
  const collected: ShopItem[] = [];

  for (let page = 0; page < maxPages; page++) {
    const start = page * 100 + 1;
    const url = `${SHOP_URL}?query=${encodeURIComponent(opt.keyword)}&display=100&start=${start}&sort=sim`;
    let res: Response;
    try { res = await fetch(url, { headers }); }
    catch (e: any) {
      return { rankWonbu: null, rankCompare: null, rankSolo: null, error: String(e?.message || e) };
    }
    if (res.status === 401) {
      return { rankWonbu: null, rankCompare: null, rankSolo: null, error: '인증 실패: 네이버 Open API 키를 확인하세요.' };
    }
    if (!res.ok) {
      const text = await res.text();
      let msg = text.slice(0, 200);
      try { msg = JSON.parse(text)?.errorMessage || msg; } catch {}
      return { rankWonbu: null, rankCompare: null, rankSolo: null, error: `API ${res.status}: ${msg}` };
    }
    const items: any[] = (await res.json())?.items || [];
    if (items.length === 0) break;
    for (const it of items) {
      const cls = classify(it);
      let pos: number;
      if (cls === '원부') pos = ++posWonbu;
      else if (cls === '가격비교') pos = ++posCompare;
      else pos = ++posSolo;
      collected.push({
        cls, pos,
        mallPid: String(it.productId || ''),
        title: strip(it.title || ''),
        mall: it.mallName || '',
        ptype: String(it.productType || ''),
        hp: Number(it.hprice) || 0,
      });
    }
    if (items.length < 100) break;
  }

  if (collected.length === 0) {
    return { rankWonbu: null, rankCompare: null, rankSolo: null };
  }

  let rankWonbu: number | null = null;
  let rankCompare: number | null = null;
  let rankSolo: number | null = null;
  const debug: string[] = [];
  const record = (it: ShopItem, reason: string) => {
    debug.push(`[${it.cls}] pos=${it.pos} ${it.title.slice(0, 40)} | ${reason}`);
    if (it.cls === '원부' && rankWonbu == null) rankWonbu = it.pos;
    else if (it.cls === '가격비교' && rankCompare == null) rankCompare = it.pos;
    else if (it.cls === '단독' && rankSolo == null) rankSolo = it.pos;
  };

  // 1순위: productId 정확 일치
  if (opt.naverProductNo) {
    for (const it of collected) {
      if (it.mallPid === opt.naverProductNo) {
        record(it, `PNO_EXACT(${opt.naverProductNo})`);
        break;
      }
    }
  }
  const found = () => rankWonbu != null || rankCompare != null || rankSolo != null;

  // 2순위: 스토어명 매칭 + 이름 유사도
  if (!found() && opt.storeName && opt.ourProductName) {
    let best: ShopItem | null = null;
    let bestSim = 0;
    for (const it of collected) {
      if (it.mall.includes(opt.storeName)) {
        const sim = jaccard(it.title, opt.ourProductName);
        if (sim > bestSim) { bestSim = sim; best = it; }
      }
    }
    if (best && bestSim >= 0.40) record(best, `STORE+NAME(sim=${bestSim.toFixed(2)})`);
  }

  // 3순위: 이름 유사도만 (>= 0.60)
  if (!found() && opt.ourProductName) {
    let best: ShopItem | null = null;
    let bestSim = 0;
    for (const it of collected) {
      const sim = jaccard(it.title, opt.ourProductName);
      if (sim > bestSim) { bestSim = sim; best = it; }
    }
    if (best && bestSim >= 0.60) record(best, `NAME_BEST(sim=${bestSim.toFixed(2)})`);
  }

  return {
    rankWonbu, rankCompare, rankSolo,
    matchInfo: debug.join(' || ') || undefined,
  };
}
