/** 🔥 SNS 트렌드 TOP 30 API 클라이언트 — 파이썬 API(/api/trends/*). 관리자 전용. */
import { call } from './billing';

export interface TrendItem {
  rank: number; prev_rank: number | null; costco_no: string; name: string; full_name: string; kw: string;
  store: number; category: string; score: number; score_parts: Record<string, number>;
  mentions: number; blog7: number; blog_prev7: number; blog_total: number;
  yt_count: number; yt_views: number; volume: number; comp: string; trend: number | null;
  sales_recent: number; sales_prev: number; sellers: number;
  our_price: number; suggest_price: number; margin: number; margin_rate: number; cold: boolean;
  samples: { src: string; text: string; link: string }[];
  ai: { grade?: string; reason?: string; price?: number; title?: string; keywords?: string[]; caution?: string };
}
export interface TrendReport {
  week: string; created_at: string; docs: number; sources: Record<string, number>;
  candidates: number; youtube: boolean; elapsed: number; items: TrendItem[];
}
export interface TrendStatus { running: boolean; lines: string[]; started_at: string; error: string }

const B = '/api/trends';
export const trends = {
  latest: () => call<{ report: TrendReport | null; reports: { id: number; week: string; created_at: string }[]; has_youtube: boolean }>('GET', `${B}/latest`),
  report: (id: number) => call<{ report: TrendReport }>('GET', `${B}/report/${id}`),
  run: () => call<{ started: boolean }>('POST', `${B}/run`, {}),
  status: () => call<TrendStatus>('GET', `${B}/status`),
  setYoutubeKey: (key: string) => call<{ ok: boolean; has_youtube: boolean }>('POST', `${B}/youtube-key`, { key }),
};
