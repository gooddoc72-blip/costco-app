'use client';
/**
 * 🔥 SNS 트렌드 TOP 30 (관리자) — 지금 뜨는 코스트코 상품과 판매 제안.
 *
 * 블로그·카페·유튜브 언급, 검색 증가, 월간 검색수, 우리 판매 추이를 점수로 묶어 순위를 매기고
 * 상위 30개는 AI가 뜨는 이유·추천 등급·추천가·상품명·키워드를 쓴다. 조사는 파이썬
 * (trend_research)이 하고, 이 화면은 결과를 보여 주고 조사를 시작만 한다.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { ChevronDown, ChevronRight, Play, RefreshCw } from 'lucide-react';
import Header from '@/components/Header';
import { trends, TrendItem, TrendReport, TrendStatus } from '@/lib/client/trends';
import { num } from '@/lib/fmt';

const GRADE_CLS: Record<string, string> = {
  '주력': 'bg-red-100 text-red-700', '시도': 'bg-blue-100 text-blue-700', '관찰': 'bg-gray-100 text-gray-600',
};

export default function TrendsPage() {
  const [rep, setRep] = useState<TrendReport | null>(null);
  const [list, setList] = useState<{ id: number; week: string; created_at: string }[]>([]);
  const [hasYt, setHasYt] = useState(false);
  const [st, setSt] = useState<TrendStatus | null>(null);
  const [err, setErr] = useState('');
  const [open, setOpen] = useState<string>('');
  const [grade, setGrade] = useState('');
  const [ytKey, setYtKey] = useState('');
  const timer = useRef<any>(null);

  const load = useCallback(async () => {
    try {
      const r = await trends.latest();
      setRep(r.report); setList(r.reports); setHasYt(r.has_youtube); setErr('');
    } catch (e: any) { setErr(e.message || String(e)); }
  }, []);

  const poll = useCallback(async () => {
    try {
      const s = await trends.status(); setSt(s);
      if (s.running) timer.current = setTimeout(poll, 3000);
      else { timer.current = null; load(); }
    } catch { timer.current = setTimeout(poll, 5000); }
  }, [load]);

  useEffect(() => { load(); poll(); return () => timer.current && clearTimeout(timer.current); }, [load, poll]);

  const start = async () => {
    if (!window.confirm('SNS 트렌드 조사를 시작합니다. 몇 분 걸리고, 판매 제안에 AI 비용이 조금 듭니다.')) return;
    try { await trends.run(); poll(); } catch (e: any) { setErr(e.message || String(e)); }
  };
  const saveYt = async () => {
    try { const r = await trends.setYoutubeKey(ytKey); setHasYt(r.has_youtube); setYtKey(''); }
    catch (e: any) { setErr(e.message || String(e)); }
  };

  const items = (rep?.items || []).filter(x => !grade || x.ai?.grade === grade);
  return (
    <>
      <Header title="🔥 SNS 트렌드 TOP 30" subtitle="지금 뜨는 코스트코 상품 · 판매 제안 (관리자)" />
      <main className="px-3 md:px-6 pt-3 pb-16 space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={start} disabled={!!st?.running}
            className="flex items-center gap-1 px-4 py-2 rounded-lg bg-primary text-white text-sm font-bold disabled:opacity-50">
            <Play size={16} /> {st?.running ? '조사 중…' : '지금 조사'}
          </button>
          <button onClick={load} className="p-2 border rounded-lg bg-white" title="새로고침"><RefreshCw size={16} /></button>
          {list.length > 1 && (
            <select onChange={async e => { const r = await trends.report(Number(e.target.value)); setRep(r.report); }}
              className="border rounded-lg px-2 py-2 text-sm bg-white">
              {list.map(r => <option key={r.id} value={r.id}>{r.week} ({r.created_at.slice(5, 16)})</option>)}
            </select>
          )}
          {rep && <span className="text-xs text-gray-500">
            {rep.created_at} · 글 {num(rep.docs)}건 (블로그 {rep.sources.blog || 0} · 카페 {rep.sources.cafe || 0} · 유튜브 {rep.sources.youtube || 0}) · 후보 {rep.candidates}개
          </span>}
        </div>

        {!hasYt && (
          <div className="bg-yellow-50 text-yellow-900 p-3 rounded-lg text-sm space-y-2">
            <div>📺 유튜브 API 키가 없어 유튜브는 빠져 있습니다. 구글 클라우드 콘솔 → 'YouTube Data API v3' 사용 설정 → 사용자 인증 정보 → API 키 만들기 후 붙여 넣으세요(무료).</div>
            <div className="flex gap-2"><input value={ytKey} onChange={e => setYtKey(e.target.value)} placeholder="AIza…"
              className="flex-1 border rounded px-2 py-1" /><button onClick={saveYt} disabled={!ytKey.trim()}
              className="px-3 py-1 rounded bg-primary text-white text-sm disabled:opacity-40">저장</button></div>
          </div>)}

        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}
        {st && (st.running || st.error) && (
          <pre className="bg-gray-900 text-gray-100 text-xs p-3 rounded-lg max-h-48 overflow-y-auto whitespace-pre-wrap">
            {st.lines.join('\n') || '시작 중…'}{st.error ? `\n❌ ${st.error}` : ''}
          </pre>)}

        {!rep && !st?.running && <div className="bg-white rounded-xl p-6 text-center text-sm text-gray-600">아직 조사 결과가 없습니다. <b>지금 조사</b>를 누르세요.</div>}

        {rep && (
          <>
            <div className="flex gap-1 text-sm">
              {['', '주력', '시도', '관찰'].map(g => (
                <button key={g} onClick={() => setGrade(g)}
                  className={clsx('px-3 py-1 rounded-full border', grade === g ? 'bg-primary text-white border-primary' : 'bg-white')}>
                  {g || '전체'} {g ? `(${rep.items.filter(x => x.ai?.grade === g).length})` : `(${rep.items.length})`}
                </button>))}
            </div>
            <section className="bg-white rounded-xl shadow-sm overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500"><tr>
                  <th className="p-2">순위</th><th className="p-2 text-left">상품</th><th className="p-2">등급</th>
                  <th className="p-2 text-right">점수</th><th className="p-2 text-right">블로그 7일</th>
                  <th className="p-2 text-right">유튜브</th><th className="p-2 text-right">검색 추세</th>
                  <th className="p-2 text-right">월 검색수</th><th className="p-2 text-right">우리 판매 14일</th>
                  <th className="p-2 text-right">매장가</th><th className="p-2 text-right">추천가</th><th className="p-2 text-right">예상 마진</th>
                </tr></thead>
                <tbody>{items.map(x => <Row key={x.costco_no} x={x} open={open === x.costco_no}
                  onToggle={() => setOpen(open === x.costco_no ? '' : x.costco_no)} />)}</tbody>
              </table>
            </section>
            <div className="text-[11px] text-gray-500">
              점수(100) = 언급 증가 30 · 언급량 15 · 검색 증가 20 · 월간 검색수 15 · 판매 가능성 20(마진·경쟁도·냉장/냉동).
              네이버 쇼핑 검색 API가 2026-07-31 종료돼 경쟁 최저가는 아직 빠져 있습니다(크롬 확장 수집으로 추가 예정).
            </div>
          </>)}
      </main>
    </>
  );
}

function Row({ x, open, onToggle }: { x: TrendItem; open: boolean; onToggle: () => void }) {
  const ch = x.prev_rank == null ? 'NEW' : x.prev_rank - x.rank;
  const price = x.ai?.price || x.suggest_price;
  return (
    <>
      <tr className="border-t cursor-pointer hover:bg-gray-50" onClick={onToggle}>
        <td className="p-2 text-center whitespace-nowrap">
          <b>{x.rank}</b>{' '}
          <span className={clsx('text-[11px]', ch === 'NEW' ? 'text-red-600' : (ch as number) > 0 ? 'text-red-600' : (ch as number) < 0 ? 'text-blue-600' : 'text-gray-400')}>
            {ch === 'NEW' ? 'NEW' : (ch as number) > 0 ? `▲${ch}` : (ch as number) < 0 ? `▼${-(ch as number)}` : '–'}
          </span>
        </td>
        <td className="p-2 min-w-[200px]">
          <span className="inline-flex items-center gap-1">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}{x.name}</span>
          <div className="text-[11px] text-gray-400">{x.costco_no}{x.cold && ' · ❄️ 냉장/냉동'}{x.sellers ? ` · 판매자 ${x.sellers}명` : ' · 아직 파는 사람 없음'}</div>
        </td>
        <td className="p-2 text-center">{x.ai?.grade && <span className={clsx('px-2 py-0.5 rounded-full text-xs', GRADE_CLS[x.ai.grade] || 'bg-gray-100')}>{x.ai.grade}</span>}</td>
        <td className="p-2 text-right font-semibold">{x.score}</td>
        <td className="p-2 text-right">{x.blog7}<span className="text-[11px] text-gray-400"> ({x.blog_prev7})</span></td>
        <td className="p-2 text-right">{x.yt_count ? `${x.yt_count}편 · ${num(x.yt_views)}회` : ''}</td>
        <td className={clsx('p-2 text-right', (x.trend || 0) >= 1.15 && 'text-red-600 font-semibold')}>{x.trend ?? '–'}</td>
        <td className="p-2 text-right">{x.volume ? num(x.volume) : ''}{x.comp && <span className="text-[11px] text-gray-400"> {x.comp}</span>}</td>
        <td className="p-2 text-right">{x.sales_recent}<span className="text-[11px] text-gray-400"> ({x.sales_prev})</span></td>
        <td className="p-2 text-right">{num(x.store)}</td>
        <td className="p-2 text-right">{num(price)}</td>
        <td className={clsx('p-2 text-right', x.margin < 0 && 'text-red-600')}>{x.margin ? `${num(x.margin)} (${Math.round(x.margin_rate * 100)}%)` : ''}</td>
      </tr>
      {open && (
        <tr className="bg-gray-50"><td colSpan={12} className="p-3 text-sm space-y-2">
          {x.ai?.reason && <div>💡 <b>뜨는 이유</b> — {x.ai.reason}</div>}
          {x.ai?.title && <div>🏷 <b>상품명 제안</b> — {x.ai.title}</div>}
          {!!x.ai?.keywords?.length && <div>🔑 <b>키워드</b> — {x.ai.keywords.join(' · ')}</div>}
          {x.ai?.caution && <div>⚠️ <b>주의</b> — {x.ai.caution}</div>}
          <div className="text-xs text-gray-500">점수 구성 — {Object.entries(x.score_parts).map(([k, v]) => `${k} ${v}`).join(' · ')}
            {x.our_price ? ` · 우리 사용자 판매가 ${num(x.our_price)}원` : ''}</div>
          {!!x.samples.length && <div className="space-y-1">{x.samples.map((s, i) => (
            <a key={i} href={s.link} target="_blank" rel="noreferrer" className="block text-xs text-blue-700 underline truncate">
              [{s.src === 'youtube' ? '유튜브' : s.src === 'cafe' ? '카페' : '블로그'}] {s.text}</a>))}</div>}
        </td></tr>)}
    </>
  );
}
