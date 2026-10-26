/** 🔥 SNS 트렌드 TOP 30 API 클라이언트 — 파이썬 API(/api/trends/*). 관리자 전용. */
import { call } from './billing';

export interface Opinion { grade?: string; confidence?: number; reason?: string; evidence?: string[]; data_issue?: string }
export interface DebateTurn { grade?: string; changed?: boolean; reason?: string; rebuttal?: string }
export interface Council {
  how: string; scout: { relevant?: number; total?: number; note?: string };
  a: Opinion; b: Opinion; a_debate?: DebateTurn | null; b_debate?: DebateTurn | null;
  ruling: string; rules: string[];
}
export interface TrendItem {
  rank: number; prev_rank: number | null; costco_no: string; name: string; full_name: string; kw: string;
  store: number; category: string; score: number; score_parts: Record<string, number>;
  mentions: number; blog7: number; blog_prev7: number; blog_total: number; blog_capped?: boolean;
  yt_count: number; yt_views: number; volume: number; comp: string; trend: number | null;
  volume_checked?: number; volume_kw?: string;   // 검증관이 다른 표현으로 다시 조회한 검색수
  news?: number; buzz?: number;                    // 화제성 — 유명인·방송·대란 기사 수, 화제 표현 글 수
  influencers?: { channel: string; subs: number; views: number; link: string }[];   // 구독 10만+ 채널 영상
  suggest_price: number; margin: number; margin_rate: number; cold: boolean;
  samples: { src: string; text: string; link: string }[];
  ai: { grade?: string; confidence?: number; reason?: string; ruling?: string; price?: number; title?: string; keywords?: string[]; caution?: string };
  council?: Council;
}
export interface Decision { decision: string; memo: string; decided_by: string; decided_at: string }
export interface TrendReport {
  id: number; week: string; created_at: string; docs: number; sources: Record<string, number>;
  candidates: number; youtube: boolean; elapsed: number; items: TrendItem[];
  council?: { members: Record<string, string>; agreed: number; disputed: number; rules_applied: number;
    seconds: number; excluded?: { costco_no: string; name: string; reason: string }[] } | null;
  decisions?: Record<string, Decision>;
}
export interface TrendStatus { running: boolean; lines: string[]; started_at: string; error: string }

const B = '/api/trends';
export const trends = {
  latest: () => call<{ report: TrendReport | null; reports: { id: number; week: string; created_at: string }[]; has_youtube: boolean; track_record: string }>('GET', `${B}/latest`),
  report: (id: number) => call<{ report: TrendReport }>('GET', `${B}/report/${id}`),
  run: () => call<{ started: boolean }>('POST', `${B}/run`, {}),
  status: () => call<TrendStatus>('GET', `${B}/status`),
  setYoutubeKey: (key: string) => call<{ ok: boolean; has_youtube: boolean }>('POST', `${B}/youtube-key`, { key }),
  decide: (id: number, costco_no: string, decision: string, memo = '') =>
    call<{ decisions: Record<string, Decision> }>('POST', `${B}/report/${id}/decide`, { costco_no, decision, memo }),
};
