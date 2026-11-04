'use client';
/**
 * 주문 수집 아래 도구 — 저장된 주문(날짜별 확인·삭제) · 제외 목록(복구) · 주문 이력 검색.
 * 펼칠 때만 불러온다.
 */
import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { num } from '@/lib/fmt';
import { oc } from '@/lib/client/oc';

type Err = (s: string) => void;

function Section({ title, children }: { title: string; children: () => React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-white rounded-xl border border-gray-100">
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-center gap-1 px-3 py-2.5 text-sm font-medium text-left">
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />} {title}
      </button>
      {open && <div className="px-3 pb-3">{children()}</div>}
    </div>
  );
}

export default function OcTools({ onErr, onChanged }: { onErr: Err; onChanged: () => void }) {
  return (
    <div className="space-y-2">
      <Section title="🗂 저장된 주문 (날짜별 확인·삭제)">{() => <DailyTool onErr={onErr} />}</Section>
      <Section title="🚫 제외 목록 (영구 삭제한 주문 복구)">{() => <ExcludedTool onErr={onErr} onChanged={onChanged} />}</Section>
      <Section title="🔍 주문 이력 검색">{() => <SearchTool onErr={onErr} />}</Section>
    </div>
  );
}

function DailyTool({ onErr }: { onErr: Err }) {
  const [dates, setDates] = useState<{ date: string; count: number }[] | null>(null);
  const [d, setD] = useState('');
  const [rows, setRows] = useState<Awaited<ReturnType<typeof oc.daily>>['rows'] | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const loadDates = useCallback(() => { oc.dailyDates().then(r => setDates(r.dates)).catch(e => onErr(e.message)); }, [onErr]);
  useEffect(loadDates, [loadDates]);
  useEffect(() => {
    setRows(null); setSel(new Set());
    if (d) oc.daily(d).then(r => setRows(r.rows)).catch(e => onErr(e.message));
  }, [d, onErr]);
  const del = async (all: boolean) => {
    const list = all ? null : Array.from(sel);
    const n = all ? (rows?.length || 0) : sel.size;
    if (!confirm(`${d} 저장된 주문 ${n}건을 지울까요?\n수익·장보기 집계에서 빠집니다. 발송 추적(주문 이력)은 그대로 둡니다.`)) return;
    if (all && !confirm(`정말 ${d} 날짜 전체(${n}건)를 지웁니까?`)) return;
    try { await oc.dailyDelete(d, list); loadDates(); setD(''); } catch (e: any) { onErr(e.message); }
  };
  if (!dates) return <div className="text-xs text-gray-400">불러오는 중…</div>;
  if (!dates.length) return <div className="text-xs text-gray-500">저장된 주문이 없습니다.</div>;
  return (
    <div className="space-y-2 text-sm">
      <select value={d} onChange={e => setD(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5">
        <option value="">날짜 고르기</option>
        {dates.map(x => <option key={x.date} value={x.date}>{x.date} ({x.count}건)</option>)}
      </select>
      {d && !rows && <div className="text-xs text-gray-400">불러오는 중…</div>}
      {rows && (
        <>
          <div className="max-h-80 overflow-y-auto space-y-0.5">
            {rows.map((r, i) => {
              const k = r.order_no || `#${i}`;
              return (
                <label key={k} className="flex items-center gap-2 text-xs py-0.5 border-t border-gray-50">
                  <input type="checkbox" disabled={!r.order_no} checked={sel.has(k)}
                    onChange={e => setSel(s => { const n = new Set(s); e.target.checked ? n.add(k) : n.delete(k); return n; })} />
                  <span className="flex-1 truncate">{r.recipient} · {r.product_name} ×{r.qty}</span>
                  <span>{num(r.order_amount)}원</span>
                </label>
              );
            })}
          </div>
          <div className="flex gap-2">
            <button disabled={!sel.size} onClick={() => del(false)} className="border border-red-200 text-red-600 rounded px-2 py-1 text-xs disabled:opacity-40">🗑 선택 {sel.size}건 삭제</button>
            <button onClick={() => del(true)} className="border border-red-300 text-red-700 rounded px-2 py-1 text-xs">🗑 {d} 전체 삭제</button>
          </div>
        </>
      )}
    </div>
  );
}

function ExcludedTool({ onErr, onChanged }: { onErr: Err; onChanged: () => void }) {
  const [rows, setRows] = useState<{ order_no: string; excluded_at: string; reason: string }[] | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const load = useCallback(() => { oc.excluded().then(r => { setRows(r.rows); setSel(new Set()); }).catch(e => onErr(e.message)); }, [onErr]);
  useEffect(load, [load]);
  const restore = async (list: string[]) => {
    try { await oc.restore(list); load(); onChanged(); } catch (e: any) { onErr(e.message); }
  };
  if (!rows) return <div className="text-xs text-gray-400">불러오는 중…</div>;
  if (!rows.length) return <div className="text-xs text-gray-500">제외 목록이 비어 있습니다.</div>;
  return (
    <div className="space-y-2 text-xs">
      <div className="text-gray-500">제외된 주문 {rows.length}건 — 수집·복원에서 항상 빠집니다. 복구하면 다음 수집 때 다시 들어옵니다.</div>
      <div className="max-h-64 overflow-y-auto">
        {rows.map(r => (
          <label key={r.order_no} className="flex items-center gap-2 py-0.5 border-t border-gray-50">
            <input type="checkbox" checked={sel.has(r.order_no)}
              onChange={e => setSel(s => { const n = new Set(s); e.target.checked ? n.add(r.order_no) : n.delete(r.order_no); return n; })} />
            <span className="flex-1">{r.order_no}</span><span className="text-gray-400">{r.excluded_at}{r.reason ? ` · ${r.reason}` : ''}</span>
          </label>
        ))}
      </div>
      <div className="flex gap-2">
        <button disabled={!sel.size} onClick={() => restore(Array.from(sel))} className="border rounded px-2 py-1 disabled:opacity-40">↩️ 선택 {sel.size}건 복구</button>
        <button onClick={() => confirm(`제외 목록 ${rows.length}건을 모두 복구할까요?`) && restore(rows.map(r => r.order_no))} className="border rounded px-2 py-1">↩️ 전체 복구</button>
      </div>
    </div>
  );
}

function SearchTool({ onErr }: { onErr: Err }) {
  const [q, setQ] = useState('');
  const [p, setP] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [rows, setRows] = useState<Record<string, any>[] | null>(null);
  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    try { setRows((await oc.search(q, p, from, to)).rows); } catch (er: any) { onErr(er.message); }
  };
  return (
    <div className="space-y-2 text-sm">
      <form onSubmit={go} className="flex flex-wrap gap-2 items-end text-xs">
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="수취인·구매자·주문번호" className="border border-gray-200 rounded px-2 py-1.5" />
        <input value={p} onChange={e => setP(e.target.value)} placeholder="상품명" className="border border-gray-200 rounded px-2 py-1.5" />
        <input type="date" value={from} onChange={e => setFrom(e.target.value)} className="border border-gray-200 rounded px-2 py-1.5" />
        <input type="date" value={to} onChange={e => setTo(e.target.value)} className="border border-gray-200 rounded px-2 py-1.5" />
        <button className="bg-gray-800 text-white rounded px-3 py-1.5">검색</button>
      </form>
      {rows && <div className="text-xs text-gray-500">{rows.length}건{rows.length >= 300 ? ' (최대 300건)' : ''}</div>}
      {rows && (
        <div className="overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-gray-400 text-left"><th>결제일</th><th>수취인</th><th>상품</th><th className="text-right">수량</th><th className="text-right">금액</th><th>상태</th><th>송장</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.order_no} className="border-t border-gray-50">
                  <td className="py-1 whitespace-nowrap">{String(r.order_date || '').slice(0, 10)}</td>
                  <td>{r.recipient}</td><td>{r.product_name}{r.option_info ? ` · ${r.option_info}` : ''}</td>
                  <td className="text-right">{r.qty}</td><td className="text-right">{num(r.order_amount)}</td>
                  <td>{r.status}</td><td className="whitespace-nowrap">{r.tracking_no || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
