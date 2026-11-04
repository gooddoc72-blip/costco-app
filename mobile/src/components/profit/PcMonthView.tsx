'use client';
/**
 * 📅 월별 — 한 달 정산저장을 모아 합계·일별·상품별을 보고, 점검에 걸린 행을 고친다.
 * 합계 규칙은 대시보드와 같다(수익은 지금 값으로 다시 계산, 구입가 0 행은 수익에서 뺌).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import { num } from '@/lib/fmt';
import { pc, PmMonth, PmIssueRow } from '@/lib/client/pc';

const SEV: Record<string, string> = { high: '🔴', mid: '🟠', low: '🟡' };
const short = (t: string) => t.split(' —')[0].split(' (')[0];

export default function PcMonthView({ admin, me, onErr, openDay }: {
  admin: boolean; me: string; onErr: (s: string) => void; openDay: (d: string) => void;
}) {
  const [users, setUsers] = useState<{ username: string; name: string }[]>([]);
  const [u, setU] = useState(me);
  const [ym, setYm] = useState('');
  const [d, setD] = useState<PmMonth | null>(null);
  const [pick, setPick] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => { if (admin) pc.users().then(r => setUsers(r.rows)).catch(() => {}); }, [admin]);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await pc.month(ym, u);
      setD(r);
      if (!ym) setYm(r.ym);
      setPick(p => (p && r.issue_counts[p]) ? p : (r.checks.find(c => r.issue_counts[c.code])?.code || ''));
    } catch (e: any) { onErr(e.message || String(e)); }
    finally { setLoading(false); }
  }, [ym, u, onErr]);
  useEffect(() => { load(); }, [load]);

  const s = d?.summary || {};
  const p = d?.prev || {};
  const hasPrev = !!p.cnt;
  const diff = (k: keyof typeof s) => hasPrev ? `${((s[k] || 0) - (p[k] || 0)) >= 0 ? '+' : ''}${num((s[k] || 0) - (p[k] || 0))}` : '';
  const dupN = d?.rows.filter(r => r.codes.includes('dup')).length || 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-end text-sm">
        {admin && users.length > 0 && (
          <label className="text-xs text-gray-500">사용자<br />
            <select value={u} onChange={e => { setU(e.target.value); setYm(''); }} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm">
              <option value={me}>{me} (나)</option>
              {users.filter(x => x.username !== me).map(x => <option key={x.username} value={x.username}>{x.name}</option>)}
            </select></label>
        )}
        <label className="text-xs text-gray-500">월<br />
          <select value={ym} onChange={e => setYm(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm">
            {(d?.months || []).map(m => <option key={m} value={m}>{m}</option>)}
          </select></label>
        {loading && <span className="text-xs text-gray-400">불러오는 중…</span>}
      </div>

      {d && !s.cnt && <div className="bg-blue-50 text-blue-700 p-3 rounded-lg text-sm">{d.ym}에 정산저장된 주문이 없습니다. 일별 화면에서 정산저장하면 여기 모입니다.</div>}

      {d && !!s.cnt && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Kpi label="주문" v={`${num(s.cnt)}건`} sub={hasPrev ? `${(s.cnt || 0) - (p.cnt || 0) >= 0 ? '+' : ''}${(s.cnt || 0) - (p.cnt || 0)}건` : ''} />
            <Kpi label="매출" v={`${num(s.sales)}원`} sub={diff('sales')} />
            <Kpi label="정산(예정+실정산)" v={`${num(s.settle)}원`} sub={diff('settle')} />
            <Kpi label="수익" v={`${num(s.profit)}원`} sub={diff('profit')} strong />
            <Kpi label="구입가" v={`${num(s.cost)}원`} />
            <Kpi label="택배비 + 박스비" v={`${num((s.delivery || 0) + (s.box || 0))}원`} />
            <Kpi label="고객 배송비" v={`${num(s.ship_in)}원`} />
            <Kpi label="수익률 (수익÷매출)" v={s.sales ? `${(((s.profit || 0) / s.sales) * 100).toFixed(1)}%` : '-'} />
          </div>
          {!!s.nocost_cnt && <div className="bg-orange-50 text-orange-800 p-2.5 rounded-lg text-xs">⚠️ 구입가가 없는 <b>{s.nocost_cnt}건</b>(정산 {num(s.nocost_settle)}원)은 수익에서 빠져 있습니다 — 아래 점검에서 구입가를 넣으면 수익에 들어갑니다.</div>}
          {dupN > 0 && <div className="bg-red-50 text-red-700 p-2.5 rounded-lg text-xs">🔴 같은 주문이 여러 날짜에 저장된 행이 <b>{dupN}건</b> — 매출·수익이 두 번 잡혀 있습니다. 아래 점검 › 중복에서 정리하세요.</div>}

          <details className="bg-white rounded-xl border border-gray-100 p-3">
            <summary className="text-sm font-medium cursor-pointer">📆 일별 내역</summary>
            <div className="overflow-x-auto mt-2">
              <table className="w-full text-xs">
                <thead><tr className="text-gray-400 text-right"><th className="text-left">날짜</th><th>발송</th><th>정산저장</th><th>매출</th><th>정산</th><th>구입가</th><th>수익</th><th>점검</th></tr></thead>
                <tbody>
                  {d.days.map(x => (
                    <tr key={x.date} className="border-t border-gray-50 text-right">
                      <td className="text-left py-1"><button onClick={() => openDay(x.date)} className="text-blue-600 underline">{x.date}</button></td>
                      <td>{x.dispatched}</td>
                      <td className={x.dispatched && x.saved < x.dispatched ? 'text-orange-600 font-bold' : ''}>{x.saved}</td>
                      <td>{num(x.sales)}</td><td>{num(x.settle)}</td><td>{num(x.cost)}</td>
                      <td className={x.profit >= 0 ? 'text-emerald-600' : 'text-red-600'}>{num(x.profit)}</td><td>{x.issues || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="text-[11px] text-gray-400 mt-1">발송보다 정산저장이 적은 날(주황)은 날짜를 눌러 그날 정산저장하세요.</div>
          </details>

          <details className="bg-white rounded-xl border border-gray-100 p-3">
            <summary className="text-sm font-medium cursor-pointer">🏷 상품별 수익 (상위·하위 10)</summary>
            <div className="grid sm:grid-cols-2 gap-3 mt-2 text-xs">
              {([['💰 상위', d.top], ['🔻 하위', d.bottom]] as const).map(([t, list]) => (
                <div key={t}><div className="text-gray-500 mb-1">{t}</div>
                  {list.map(x => (
                    <div key={x.name} className="flex justify-between gap-2 py-0.5 border-t border-gray-50">
                      <span className="truncate">{x.name}</span>
                      <span className={clsx('whitespace-nowrap', x.profit >= 0 ? 'text-emerald-600' : 'text-red-600')}>{num(x.profit)} · {x.cnt}건</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </details>

          <div className="bg-white rounded-xl border border-gray-100 p-3 space-y-2">
            <div className="text-sm font-bold">🔍 점검 · 수정</div>
            {!d.rows.length ? <div className="text-sm text-emerald-700">✅ 점검에 걸린 행이 없습니다.</div> : (
              <>
                <div className="flex flex-wrap gap-1.5">
                  {d.checks.filter(c => d.issue_counts[c.code]).map(c => (
                    <button key={c.code} onClick={() => setPick(c.code)}
                      className={clsx('px-2.5 py-1 rounded-full text-xs border', pick === c.code ? 'bg-gray-800 text-white border-gray-800' : 'bg-white border-gray-200')}>
                      {SEV[c.sev]} {short(c.title)} {d.issue_counts[c.code]}</button>
                  ))}
                </div>
                {pick && <div className="text-xs text-gray-500">{d.checks.find(c => c.code === pick)?.title}</div>}
                {pick === 'dup'
                  ? <DupTool d={d} u={u} onErr={onErr} onDone={load} />
                  : pick && <EditTool rows={d.rows.filter(r => r.codes.includes(pick))} pick={pick} checks={d.checks} u={u} onErr={onErr} onDone={load} />}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Kpi({ label, v, sub, strong }: { label: string; v: string; sub?: string; strong?: boolean }) {
  return (
    <div className="bg-white rounded-xl border border-gray-100 p-2.5">
      <div className="text-[11px] text-gray-400">{label}</div>
      <div className={clsx('font-bold', strong ? 'text-lg' : 'text-sm')}>{v}</div>
      {sub && <div className="text-[11px] text-gray-500">{sub} (전월 대비)</div>}
    </div>
  );
}

const FIELDS = [['settle', 'settlement_amount', '정산'], ['cost', 'cost_price', '구입가'],
  ['delivery', 'delivery_cost', '택배비'], ['box', 'box_cost', '박스비']] as const;

function EditTool({ rows, pick, checks, u, onErr, onDone }: {
  rows: PmIssueRow[]; pick: string; checks: PmMonth['checks']; u: string; onErr: (s: string) => void; onDone: () => void;
}) {
  const [ed, setEd] = useState<Record<number, Record<string, number>>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => { setEd({}); }, [pick, rows]);
  const title = useMemo(() => Object.fromEntries(checks.map(c => [c.code, short(c.title)])), [checks]);
  const changed = Object.entries(ed).filter(([, ch]) => Object.keys(ch).length).length;
  const save = async () => {
    setBusy(true);
    try { await pc.monthEdit(u, ed); onDone(); } catch (e: any) { onErr(e.message || String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-2">
      {pick === 'stale' && <div className="text-xs bg-blue-50 text-blue-800 p-2 rounded">영수증 정산이 구입가를 바꿀 때 저장 수익은 그대로 남아 생깁니다. 합계는 현재 값 기준 수익으로 계산합니다. 구입가가 맞는지 보고, 틀렸으면 고쳐 저장하세요.</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr className="text-gray-400 text-right"><th className="text-left">날짜·상품</th><th>수량</th><th>매출</th><th>정산✏️</th><th>구입가✏️</th><th>택배✏️</th><th>박스✏️</th><th>저장수익</th><th>현재수익</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="border-t border-gray-50 text-right align-top">
                <td className="text-left py-1 max-w-[260px]">{r.date} · {r.recipient}<div className="text-gray-600">{r.product_name}</div>
                  <div className="text-gray-400">{r.match}{r.codes.filter(c => c !== pick).length ? ` · ${r.codes.filter(c => c !== pick).map(c => title[c]).join(', ')}` : ''}</div></td>
                <td>{r.qty}</td><td>{num(r.sales)}</td>
                {FIELDS.map(([k, f]) => (
                  <td key={k}><input type="number" min={0} step={100} defaultValue={r[k]}
                    onChange={e => setEd(s => {
                      const v = Math.max(0, Math.round(+e.target.value || 0));
                      const ch = { ...(s[r.id] || {}) };
                      if (v === r[k]) delete ch[f]; else ch[f] = v;
                      return { ...s, [r.id]: ch };
                    })}
                    className="w-20 border border-gray-200 rounded px-1 py-0.5 text-right" /></td>
                ))}
                <td>{num(r.saved_profit)}</td>
                <td className={r.profit_now >= 0 ? 'text-emerald-600' : 'text-red-600'}>{num(r.profit_now)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button disabled={!changed || busy} onClick={save} className="bg-blue-600 text-white text-sm px-3 py-1.5 rounded-lg disabled:bg-gray-300">
        💾 수정 저장 ({changed}행) — 수익은 다시 계산됩니다</button>
    </div>
  );
}

function DupTool({ d, u, onErr, onDone }: { d: PmMonth; u: string; onErr: (s: string) => void; onDone: () => void }) {
  const groups = useMemo(() => {
    const g: Record<string, { id: number; date: string; recipient: string; product_name: string; sales: number }[]> = {};
    d.rows.filter(r => r.codes.includes('dup')).forEach(r => (g[r.order_no] ||= []).push(r));
    (d.dup_other || []).forEach(r => (g[r.order_no] ||= []).push(r));
    Object.values(g).forEach(list => list.sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id));
    return g;
  }, [d]);
  // 기본: 발송일과 같은 날짜, 없으면 가장 이른 날짜를 남긴다
  const [keep, setKeep] = useState<Record<string, number>>({});
  useEffect(() => {
    const k: Record<string, number> = {};
    Object.entries(groups).forEach(([o, list]) => {
      const dd = d.dup_dispatch?.[o] || '';
      k[o] = (list.find(r => r.date === dd) || list[0]).id;
    });
    setKeep(k);
  }, [groups, d.dup_dispatch]);
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const del = Object.entries(groups).flatMap(([o, list]) => list.filter(r => r.id !== keep[o]));
  const amt = del.reduce((a, r) => a + r.sales, 0);
  const go = async () => {
    setBusy(true);
    try { await pc.monthDelete(u, del.map(r => r.id)); setOk(false); onDone(); }
    catch (e: any) { onErr(e.message || String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-2 text-xs">
      <div className="bg-blue-50 text-blue-800 p-2 rounded">주문은 한 번만 정산돼야 하므로 <b>한 날짜만 남기고</b> 나머지를 지웁니다. 기본은 발송기록의 발송일과 같은 날짜, 없으면 가장 이른 날짜입니다.</div>
      {Object.entries(groups).map(([o, list]) => (
        <div key={o} className="border border-gray-100 rounded-lg p-2">
          <div className="text-gray-500">주문 {o}{d.dup_dispatch?.[o] ? ` · 발송일 ${d.dup_dispatch[o]}` : ''}</div>
          {list.map(r => (
            <label key={r.id} className="flex items-center gap-2 py-0.5">
              <input type="radio" name={`k_${o}`} checked={keep[o] === r.id} onChange={() => setKeep(s => ({ ...s, [o]: r.id }))} />
              <span className="w-24">{r.date}</span><span className="flex-1 truncate">{r.recipient} · {r.product_name}</span><span>{num(r.sales)}</span>
              {keep[o] === r.id ? <span className="text-emerald-600">남김</span> : <span className="text-red-500">지움</span>}
            </label>
          ))}
        </div>
      ))}
      <div className="text-gray-600">지울 행 {del.length}개 · 중복으로 잡혀 있던 매출 {num(amt)}원</div>
      <label className="flex items-center gap-1"><input type="checkbox" checked={ok} onChange={e => setOk(e.target.checked)} /> 지울 행을 확인했습니다</label>
      <button disabled={!ok || !del.length || busy} onClick={go} className="bg-red-600 text-white px-3 py-1.5 rounded-lg disabled:bg-gray-300">🗑 중복 {del.length}행 지우기</button>
    </div>
  );
}
