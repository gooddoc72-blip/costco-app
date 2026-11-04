'use client';
/**
 * 📋 일일 주문 수집 — 네이버·쿠팡·카페24·엑셀 수집 → 미발송 주문 확인 → 💾 날짜에 저장.
 *
 * 예전 /m 수집은 네이버·쿠팡 수집을 TypeScript로 따로 짜서 네이버 조회 조건 이름이 틀리고
 * 쿠팡 수량이 늘 1이었다. 지금은 Streamlit과 같은 파이썬 수집기(/api/oc)를 부른다.
 * 목록은 DB의 미발송 주문이라 새로고침해도 그대로다. 장보기 목록·송장 엑셀은 아직 기존 화면에 있다.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { oc, waitJob, fileB64, OcStatus, OcResult, OcActive, Platform } from '@/lib/client/oc';
import { num } from '@/lib/fmt';
import OcTools from '@/components/orders/OcTools';

const kst = (days = 0) => new Date(Date.now() + 9 * 3600e3 - days * 864e5).toISOString().slice(0, 10);
const PLAT: Record<string, string> = { naver: '🟢 네이버', coupang: '🟡 쿠팡', cafe24: '🔵 카페24' };

export default function OrdersPage() {
  const [st, setSt] = useState<OcStatus | null>(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [act, setAct] = useState<OcActive | null>(null);
  const [inclAfter, setInclAfter] = useState(false);
  const [recent, setRecent] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);

  useEffect(() => { oc.status().then(setSt).catch(e => setErr(e.message || String(e))); }, []);
  const loadActive = useCallback(async () => {
    setLoading(true);
    try { setAct(await oc.active(inclAfter)); } catch (e: any) { setErr(e.message || String(e)); }
    finally { setLoading(false); }
  }, [inclAfter]);
  useEffect(() => { loadActive(); }, [loadActive]);

  const onCollected = (r: OcResult, label: string) => {
    setRecent(new Set(r.order_nos));
    const parts = [`✅ ${label}: 받은 주문 ${num(r.fetched)}건`];
    if (r.in_range != null) parts.push(`결제일 기간 안 ${r.in_range}건`);
    if (r.prev) parts.push(`기간 이전 미발송 ${r.prev}건${r.prev_included ? ' 포함' : ' (미포함)'}`);
    if (r.after) parts.push(`종료일 이후 결제 ${r.after}건 제외`);
    if (r.deferred_cut) parts.push(`마감 이후 ${r.deferred_cut}건 내일분으로 제외`);
    if (r.excluded) parts.push(`제외 목록 ${r.excluded}건 배제`);
    if (r.sync_cleared) parts.push(`이미 발송된 ${r.sync_cleared}건 미발송에서 뺌`);
    if (r.fee_updates || r.sale_updates) parts.push(`제품 DB 배송비 ${r.fee_updates || 0}·판매가 ${r.sale_updates || 0}건 갱신`);
    setMsg(parts.join(' · ') + (r.warnings.length ? `\n⚠️ ${r.warnings.join('\n⚠️ ')}` : '')
      + (r.errors.length ? `\n❌ ${r.errors.join('\n❌ ')}` : '')
      + '\n아래 목록을 확인하고 💾 저장을 눌러야 수익·장보기 집계에 들어갑니다.');
    loadActive();
  };

  return (
    <>
      <Header title="📋 일일 주문 수집" subtitle="수집 → 미발송 확인 → 날짜에 저장"
        right={<a href="/app/" className="text-xs text-gray-500 underline">장보기·송장 엑셀(기존 화면)</a>} />
      <main className="px-3 md:px-6 pt-3 pb-28 space-y-3">
        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm whitespace-pre-wrap">❌ {err}</div>}
        {st && <CollectPanel st={st} inclAfter={inclAfter} setInclAfter={setInclAfter}
          onErr={setErr} onDone={onCollected} onMsg={setMsg} />}
        {msg && <div className="bg-green-50 text-green-800 p-3 rounded-lg text-sm whitespace-pre-wrap">{msg}</div>}
        <ActiveList act={act} loading={loading} recent={recent} inclAfter={inclAfter} naver={!!st?.naver}
          onErr={setErr} onMsg={setMsg} reload={loadActive} />
        <OcTools onErr={setErr} onChanged={loadActive} />
      </main>
      <BottomNav />
    </>
  );
}

function CollectPanel({ st, inclAfter, setInclAfter, onErr, onDone, onMsg }: {
  st: OcStatus; inclAfter: boolean; setInclAfter: (v: boolean) => void;
  onErr: (s: string) => void; onDone: (r: OcResult, label: string) => void; onMsg: (s: string) => void;
}) {
  const tabs = ([...(st.naver ? ['naver'] : []), ...(st.coupang ? ['coupang'] : []),
    ...(st.cafe24 ? ['cafe24'] : []), 'excel']) as (Platform | 'excel')[];
  const [tab, setTab] = useState<Platform | 'excel'>(tabs[0]);
  const [from, setFrom] = useState(kst(2));
  const [to, setTo] = useState(kst(0));
  const [nvStatus, setNvStatus] = useState('PAYED');
  const [cpStatus, setCpStatus] = useState('ALL');
  const [inclPrev, setInclPrev] = useState(true);
  const [files, setFiles] = useState<File[]>([]);
  const [pw, setPw] = useState('');
  const [xlDate, setXlDate] = useState(kst(0));
  const [busy, setBusy] = useState('');

  useEffect(() => {      // 플랫폼마다 기본 기간이 다르다(Streamlit과 같게)
    setFrom(kst(tab === 'coupang' ? 30 : tab === 'cafe24' ? 14 : 2)); setTo(kst(0));
  }, [tab]);

  const run = async () => {
    setBusy('시작…'); onErr(''); onMsg('');
    try {
      let job: string;
      if (tab === 'excel') {
        if (!files.length) throw new Error('엑셀 파일을 고르세요');
        const fs = await Promise.all(files.map(async f => ({ name: f.name, content_b64: await fileB64(f) })));
        job = (await oc.excel(fs, pw, xlDate, inclAfter)).job;
      } else {
        job = (await oc.collect({
          platform: tab, date_from: from, date_to: to,
          status: tab === 'naver' ? nvStatus : tab === 'coupang' ? cpStatus : '',
          incl_prev: inclPrev, incl_after_cutoff: inclAfter,
        })).job;
      }
      const r = await waitJob<OcResult>(job, setBusy);
      onDone(r, tab === 'excel' ? '📄 엑셀' : PLAT[tab]);
    } catch (e: any) { onErr(e.message || String(e)); }
    finally { setBusy(''); }
  };

  return (
    <div className="bg-white rounded-xl border border-gray-100 p-3 space-y-2">
      <div className="flex flex-wrap gap-1">
        {tabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx('px-3 py-1.5 rounded-lg text-sm', tab === t ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700')}>
            {t === 'excel' ? '📄 엑셀 올리기' : PLAT[t]}</button>
        ))}
      </div>
      {!st.naver && <div className="text-xs text-gray-500">💡 설정에서 네이버 API 키를 등록하면 네이버 주문을 자동으로 가져옵니다.</div>}
      {tab === 'cafe24' && !st.cafe24_auth && <div className="text-xs text-orange-700">카페24 인증 전입니다 — 설정 › 카페24 연동 › 카페24 인증하기를 먼저 하세요.</div>}

      {tab !== 'excel' ? (
        <div className="flex flex-wrap items-end gap-2 text-sm">
          {tab === 'naver' && (
            <label className="text-xs text-gray-500">주문 상태<br />
              <select value={nvStatus} onChange={e => setNvStatus(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm">
                <option value="PAYED">결제완료·발주확인</option><option value="ALL">전체 (신규+배송준비)</option>
              </select></label>
          )}
          {tab === 'coupang' && (
            <label className="text-xs text-gray-500">쿠팡 상태<br />
              <select value={cpStatus} onChange={e => setCpStatus(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm">
                <option value="ALL">신규 + 상품준비중</option><option value="ACCEPT">결제완료(신규)</option><option value="INSTRUCT">상품준비중</option>
              </select></label>
          )}
          <label className="text-xs text-gray-500">{tab === 'naver' ? '결제일 시작' : '시작일'}<br />
            <input type="date" value={from} onChange={e => setFrom(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm" /></label>
          <label className="text-xs text-gray-500">끝<br />
            <input type="date" value={to} onChange={e => setTo(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm" /></label>
        </div>
      ) : (
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <label className="text-xs text-gray-500">네이버 발주발송관리 엑셀<br />
            <input type="file" accept=".xlsx,.xls,.csv" multiple onChange={e => setFiles(Array.from(e.target.files || []))} className="text-sm" /></label>
          <label className="text-xs text-gray-500">엑셀 비밀번호{st.excel_password_set ? ' (비우면 설정값)' : ''}<br />
            <input type="password" value={pw} onChange={e => setPw(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm w-32" /></label>
          <label className="text-xs text-gray-500">주문 날짜<br />
            <input type="date" value={xlDate} onChange={e => setXlDate(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm" /></label>
        </div>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
        {tab === 'naver' && <label className="flex items-center gap-1"><input type="checkbox" checked={inclPrev} onChange={e => setInclPrev(e.target.checked)} />
          📦 기간 이전에 결제된 미발송 주문도 포함 (최근 7일)</label>}
        {st.cutoff_hour != null && <label className="flex items-center gap-1"><input type="checkbox" checked={inclAfter} onChange={e => setInclAfter(e.target.checked)} />
          ⏰ {String(st.cutoff_hour).padStart(2, '0')}:00 마감 이후 주문도 포함</label>}
      </div>
      <div className="flex items-center gap-2">
        <button onClick={run} disabled={!!busy} className="bg-blue-600 text-white rounded-lg px-4 py-2 text-sm font-medium disabled:bg-gray-300">
          {tab === 'excel' ? '📄 엑셀 읽기' : `🔄 ${PLAT[tab]} 주문 조회`}</button>
        {busy && <span className="text-xs text-gray-500">{busy}</span>}
        {st.last_sync && <span className="ml-auto text-[11px] text-gray-400">마지막 네이버 수집 {st.last_sync}</span>}
      </div>
    </div>
  );
}

function ActiveList({ act, loading, recent, inclAfter, naver, onErr, onMsg, reload }: {
  act: OcActive | null; loading: boolean; recent: Set<string>; inclAfter: boolean; naver: boolean;
  onErr: (s: string) => void; onMsg: (s: string) => void; reload: () => void;
}) {
  const [plat, setPlat] = useState<'' | 'naver' | 'coupang'>('');
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [date, setDate] = useState(kst(0));
  const [alsoDaily, setAlsoDaily] = useState(false);
  const [busy, setBusy] = useState('');
  useEffect(() => { setSel(new Set()); }, [act]);
  const rows = useMemo(() => (act?.rows || []).filter(r => !plat || r.platform === plat), [act, plat]);
  const tot = rows.reduce((a, r) => ({ qty: a.qty + r.qty, amount: a.amount + r.amount, cost: a.cost + r.cost, nocost: a.nocost + (r.cost ? 0 : 1) }),
    { qty: 0, amount: 0, cost: 0, nocost: 0 });

  const save = async () => {
    // 고른 게 없으면 지금 보이는 목록(플랫폼을 골랐으면 그 플랫폼만)을 저장한다
    const list = sel.size ? Array.from(sel) : (plat ? rows.map(r => r.order_no) : null);
    setBusy('저장 중…');
    try {
      const r = await oc.save(date, list, inclAfter);
      onMsg(`✅ ${date}에 ${r.orders}건 저장했습니다${r.carried ? ` · 이월 주문 ${r.carried}건은 이미 이전 날짜에 있어 다시 저장하지 않았습니다` : ''}.`);
      reload();
    } catch (e: any) { onErr(e.message || String(e)); } finally { setBusy(''); }
  };
  const sync = async () => {
    setBusy('발송상태 확인 중…');
    try {
      const r = await waitJob<{ checked: number; updated: number; cleared: number }>((await oc.sync()).job);
      onMsg(`✅ 동기화 — 조회 ${r.checked}건 · 갱신 ${r.updated}건 · 발송완료로 제외 ${r.cleared}건`);
      reload();
    } catch (e: any) { onErr(e.message || String(e)); } finally { setBusy(''); }
  };
  const exclude = async () => {
    const list = Array.from(sel);
    if (!list.length || !confirm(`선택한 ${list.length}건을 영구 삭제할까요?\n주문 이력에서 지우고 제외 목록에 올립니다 — 다시 수집해도 들어오지 않습니다(제외 목록에서 복구 가능).`
      + (alsoDaily ? '\n저장된 수집분도 지워 수익·장보기 집계에서도 빠집니다.' : ''))) return;
    setBusy('삭제 중…');
    try {
      const r = await oc.exclude(list, alsoDaily);
      onMsg(`🗑 ${r.deleted}건 영구 삭제${r.daily_deleted ? ` · 저장된 수집분 ${r.daily_deleted}건도 삭제` : ''}.`);
      reload();
    } catch (e: any) { onErr(e.message || String(e)); } finally { setBusy(''); }
  };

  return (
    <div className="bg-white rounded-xl border border-gray-100 p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-bold">📦 {act?.shipped_today ? '오늘 발송한 주문' : '미발송 주문'} {act ? `${act.rows.length}건` : ''}</div>
        {loading && <span className="text-xs text-gray-400">불러오는 중…</span>}
        <div className="flex gap-1 ml-auto text-xs">
          {([['', '전체'], ['naver', '네이버'], ['coupang', '쿠팡']] as const).map(([k, l]) => (
            <button key={k} onClick={() => setPlat(k)} className={clsx('px-2 py-1 rounded', plat === k ? 'bg-gray-800 text-white' : 'bg-gray-100')}>{l}</button>
          ))}
        </div>
      </div>
      {!!act?.deferred_cut && <div className="text-xs text-blue-700">⏰ {String(act.cutoff_hour).padStart(2, '0')}:00 마감 이후 주문 {act.deferred_cut}건은 내일분이라 목록에서 뺐습니다(위 &apos;마감 이후 주문도 포함&apos;으로 보기).</div>}
      {rows.length > 0 && (
        <div className="text-xs text-gray-600 flex flex-wrap gap-x-3">
          <span>{rows.length}건 · 수량 {num(tot.qty)}</span><span>주문금액 {num(tot.amount)}원</span>
          <span>예상 구입가 {num(tot.cost)}원</span>{tot.nocost > 0 && <span className="text-orange-600">단가 없음 {tot.nocost}건</span>}
        </div>
      )}

      <div className="flex flex-wrap items-end gap-2 text-sm border-t border-gray-50 pt-2">
        <label className="text-xs text-gray-500">저장할 주문 날짜<br />
          <input type="date" value={date} onChange={e => setDate(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm" /></label>
        <button onClick={save} disabled={!!busy || !act?.rows.length || !!act?.shipped_today}
          className="bg-blue-600 text-white rounded-lg px-3 py-2 text-sm font-medium disabled:bg-gray-300">
          💾 {sel.size ? `선택 ${sel.size}건` : '목록 전체'} 저장</button>
        {naver && <button onClick={sync} disabled={!!busy} className="border border-gray-200 rounded-lg px-3 py-2 text-sm">🚚 발송상태 동기화</button>}
        <button onClick={exclude} disabled={!!busy || !sel.size} className="border border-red-200 text-red-600 rounded-lg px-3 py-2 text-sm disabled:opacity-40">🗑 선택 영구 삭제</button>
        <label className="text-xs text-gray-500 flex items-center gap-1"><input type="checkbox" checked={alsoDaily} onChange={e => setAlsoDaily(e.target.checked)} />
          저장된 수집분도 삭제(취소 주문)</label>
        {busy && <span className="text-xs text-gray-500">{busy}</span>}
      </div>

      {rows.length > 0 && (
        <label className="flex items-center gap-1 text-xs text-gray-500">
          <input type="checkbox" checked={rows.every(r => sel.has(r.order_no))}
            onChange={e => setSel(e.target.checked ? new Set(rows.map(r => r.order_no)) : new Set())} /> 보이는 {rows.length}건 전체 선택</label>
      )}
      <div className="space-y-1">
        {rows.map(r => (
          <label key={r.order_no} className={clsx('flex gap-2 items-start rounded-lg border p-2 text-sm cursor-pointer',
            recent.has(r.order_no) ? 'border-blue-200 bg-blue-50/40' : 'border-gray-100')}>
            <input type="checkbox" className="mt-1" checked={sel.has(r.order_no)}
              onChange={e => setSel(s => { const n = new Set(s); e.target.checked ? n.add(r.order_no) : n.delete(r.order_no); return n; })} />
            <div className="min-w-0 flex-1">
              <div className="leading-snug">{r.platform === 'coupang' ? '🟡' : '🟢'} {r.product_name}
                {r.option_info && <span className="text-xs text-gray-400"> · {r.option_info}</span>}</div>
              <div className="text-xs text-gray-500 flex flex-wrap gap-x-2">
                <span>{r.recipient}</span><span>×{r.qty}</span><span>결제 {r.pay_day}</span>
                {r.status && <span>{r.status}</span>}{recent.has(r.order_no) && <span className="text-blue-600">방금 수집</span>}
                <span className="text-gray-300">{r.order_no}</span>
              </div>
            </div>
            <div className="text-right text-xs whitespace-nowrap">
              <div className="font-medium">{num(r.amount)}원</div>
              <div className={r.cost ? 'text-gray-500' : 'text-orange-500'}>구입 {r.cost ? num(r.cost) : '?'}{r.split_qty > 1 ? ` (÷${r.split_qty})` : ''}</div>
            </div>
          </label>
        ))}
        {act && !rows.length && !loading && <div className="text-center text-sm text-gray-400 py-6">미발송 주문이 없습니다.</div>}
      </div>
    </div>
  );
}
