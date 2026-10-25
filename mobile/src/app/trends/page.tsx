'use client';
/**
 * 🔥 SNS 트렌드 TOP 30 (관리자) — 지금 뜨는 코스트코 상품과 판매 제안.
 *
 * 블로그·카페·유튜브 언급, 검색 증가, 월간 검색수, 우리 판매 추이를 점수로 묶어 순위를 매기고
 * 상위 40개는 에이전트 회의(조사원 → 분석가 A·B 독립 의견 → 엇갈린 것만 토론 → 검증관 판정
 * → 규칙 검사)가 등급·이유·추천가·상품명·키워드를 정하고, 관리자가 승인한다. 조사·회의는
 * 파이썬(trend_research, trend_council)이 하고, 이 화면은 결과·회의록을 보여 주고 승인을 받는다.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { ChevronDown, ChevronRight, Play, RefreshCw } from 'lucide-react';
import Header from '@/components/Header';
import { trends, Decision, TrendItem, TrendReport, TrendStatus } from '@/lib/client/trends';
import { num } from '@/lib/fmt';

const GRADE_CLS: Record<string, string> = {
  '주력': 'bg-red-100 text-red-700', '시도': 'bg-blue-100 text-blue-700', '관찰': 'bg-gray-100 text-gray-600',
  '제외': 'bg-gray-200 text-gray-500 line-through',
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
  const [record, setRecord] = useState('');
  const [dec, setDec] = useState<Record<string, Decision>>({});
  const timer = useRef<any>(null);

  const show = (r: TrendReport | null) => { setRep(r); setDec(r?.decisions || {}); };
  const load = useCallback(async () => {
    try {
      const r = await trends.latest();
      show(r.report); setList(r.reports); setHasYt(r.has_youtube); setRecord(r.track_record || ''); setErr('');
    } catch (e: any) { setErr(e.message || String(e)); }
  }, []);
  const decide = async (costco_no: string, decision: string) => {
    if (!rep) return;
    try { const r = await trends.decide(rep.id, costco_no, decision); setDec(r.decisions); }
    catch (e: any) { setErr(e.message || String(e)); }
  };

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
            <select onChange={async e => { const r = await trends.report(Number(e.target.value)); show(r.report); }}
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

        {rep?.council && (
          <div className="bg-white rounded-xl p-3 text-sm space-y-1 border">
            <div>🧑‍⚖️ <b>에이전트 회의</b> — 조사원 {rep.council.members.scout} · 분석가 A {rep.council.members.a} ·
              분석가 B {rep.council.members.b} · 검증관 {rep.council.members.judge}</div>
            <div className="text-gray-600">두 분석가 합의 <b>{rep.council.agreed}</b>개 · 엇갈려 토론·판정 <b>{rep.council.disputed}</b>개 ·
              규칙으로 등급 조정 <b>{rep.council.rules_applied}</b>개 · 제외 {rep.council.excluded?.length || 0}개 ({rep.council.seconds}초)</div>
            <div className="text-gray-600">📈 지난 판정 성적(승인 상품의 2주 뒤 판매 증가): {record || '아직 2주 지난 회의가 없습니다'}</div>
            {!!rep.council.excluded?.length && (
              <details className="text-xs text-gray-500"><summary className="cursor-pointer">회의에서 제외된 상품 {rep.council.excluded.length}개</summary>
                {rep.council.excluded.map(x => <div key={x.costco_no}>· {x.name} — {x.reason}</div>)}</details>)}
          </div>)}

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
                  <th className="p-2">승인</th>
                </tr></thead>
                <tbody>{items.map(x => <Row key={x.costco_no} x={x} open={open === x.costco_no}
                  onToggle={() => setOpen(open === x.costco_no ? '' : x.costco_no)}
                  d={dec[x.costco_no]?.decision || ''} onDecide={v => decide(x.costco_no, v)} />)}</tbody>
              </table>
            </section>
            <div className="text-[11px] text-gray-500">
              점수(100) = 언급 증가 30 · 언급량 15 · 검색 증가 20 · 월간 검색수 15 · 판매 가능성 20(마진·경쟁도·냉장/냉동).
              등급은 회의가 정하고, 마진 마이너스·검색 300 미만(우리 판매 없음)·끝난 시즌은 코드가 등급을 낮춘다.
              ✅ 승인한 상품만 2주 뒤 판매로 적중 여부를 매겨 다음 회의 검증관에게 알려 준다.
              네이버 쇼핑 검색 API가 2026-07-31 종료돼 경쟁 최저가는 아직 빠져 있습니다(크롬 확장 수집으로 추가 예정).
            </div>
          </>)}
      </main>
    </>
  );
}

function Opin({ who, o, d }: { who: string; o?: { grade?: string; confidence?: number; reason?: string; data_issue?: string };
  d?: { grade?: string; changed?: boolean; reason?: string; rebuttal?: string } | null }) {
  if (!o?.grade) return null;
  return (
    <div className="bg-white rounded-lg border p-2 space-y-1">
      <div><b>{who}</b> <span className={clsx('px-1.5 rounded text-xs', GRADE_CLS[o.grade] || 'bg-gray-100')}>{o.grade}</span>
        {o.confidence ? <span className="text-[11px] text-gray-400"> 확신 {o.confidence}/5</span> : null}</div>
      <div className="text-xs">{o.reason}</div>
      {o.data_issue && <div className="text-xs text-orange-700">⚠️ 데이터 이상: {o.data_issue}</div>}
      {d && <div className="text-xs border-t pt-1">💬 토론 뒤 <span className={clsx('px-1.5 rounded', GRADE_CLS[d.grade || ''] || 'bg-gray-100')}>{d.grade}</span>
        {d.changed ? ' (의견 바꿈)' : ' (유지)'} — {d.reason}{d.rebuttal ? <div className="text-gray-500">↔ {d.rebuttal}</div> : null}</div>}
    </div>);
}

function Row({ x, open, onToggle, d, onDecide }: { x: TrendItem; open: boolean; onToggle: () => void; d: string; onDecide: (v: string) => void }) {
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
        <td className="p-2 text-center whitespace-nowrap">{x.ai?.grade && <span className={clsx('px-2 py-0.5 rounded-full text-xs', GRADE_CLS[x.ai.grade] || 'bg-gray-100')}>{x.ai.grade}</span>}
          {x.council && <div className="text-[10px] text-gray-400">{x.council.how === '합의' ? '🤝 합의' : '⚖️ 판정'}{x.council.rules.length ? ' · 📏규칙' : ''}</div>}</td>
        <td className="p-2 text-right font-semibold">{x.score}</td>
        <td className="p-2 text-right">{x.blog7}<span className="text-[11px] text-gray-400"> ({x.blog_prev7})</span></td>
        <td className="p-2 text-right">{x.yt_count ? `${x.yt_count}편 · ${num(x.yt_views)}회` : ''}</td>
        <td className={clsx('p-2 text-right', (x.trend || 0) >= 1.15 && 'text-red-600 font-semibold')}>{x.trend ?? '–'}</td>
        <td className="p-2 text-right">{x.volume ? num(x.volume) : ''}{x.comp && <span className="text-[11px] text-gray-400"> {x.comp}</span>}
          {!!x.volume_checked && x.volume_checked > (x.volume || 0) && (
            <div className="text-[11px] text-blue-700" title={`검증관이 '${x.volume_kw}'로 다시 조회`}>🔎 {num(x.volume_checked)}</div>)}</td>
        <td className="p-2 text-right">{x.sales_recent}<span className="text-[11px] text-gray-400"> ({x.sales_prev})</span></td>
        <td className="p-2 text-right">{num(x.store)}</td>
        <td className="p-2 text-right">{num(price)}</td>
        <td className={clsx('p-2 text-right', x.margin < 0 && 'text-red-600')}>{x.margin ? `${num(x.margin)} (${Math.round(x.margin_rate * 100)}%)` : ''}</td>
        <td className="p-2 text-center whitespace-nowrap" onClick={e => e.stopPropagation()}>
          <button onClick={() => onDecide(d === 'approve' ? '' : 'approve')} title="주력으로 민다"
            className={clsx('px-2 py-0.5 rounded text-xs border mr-1', d === 'approve' ? 'bg-green-600 text-white border-green-600' : 'bg-white')}>✅</button>
          <button onClick={() => onDecide(d === 'hold' ? '' : 'hold')} title="보류"
            className={clsx('px-2 py-0.5 rounded text-xs border', d === 'hold' ? 'bg-gray-600 text-white border-gray-600' : 'bg-white')}>⏸</button>
        </td>
      </tr>
      {open && (
        <tr className="bg-gray-50"><td colSpan={13} className="p-3 text-sm space-y-2">
          {x.ai?.reason && <div>💡 <b>최종 판단</b> — {x.ai.reason}</div>}
          {x.ai?.ruling && <div>⚖️ <b>검증관 판정</b> — {x.ai.ruling}</div>}
          {!!x.council?.rules.length && <div className="text-orange-700">📏 <b>규칙 적용</b> — {x.council.rules.join(' · ')}</div>}
          {x.blog_capped && <div className="text-orange-700 text-xs">⚠️ 최근 글이 많아 이전 7일을 끝까지 세지 못했습니다 — 언급 증가율은 중립으로 계산</div>}
          {x.council && (
            <div className="grid md:grid-cols-2 gap-2">
              <Opin who="분석가 A" o={x.council.a} d={x.council.a_debate} />
              <Opin who="분석가 B" o={x.council.b} d={x.council.b_debate} />
            </div>)}
          {x.council?.scout?.total ? <div className="text-xs text-gray-600">🔎 조사원 — 관련 글 {x.council.scout.relevant}/{x.council.scout.total} · {x.council.scout.note}</div> : null}
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
