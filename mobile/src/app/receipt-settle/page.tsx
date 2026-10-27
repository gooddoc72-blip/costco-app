'use client';
/**
 * 🧾 영수증 정산 (관리자) — ① 발송 파일 → ② 영수증 → ③ 예외만 정하기 → ④ 확정.
 *
 * 발송 파일·영수증을 저장하면 서버가 바로 매칭한다(지난 14일 늦게 들어온 발송건 포함).
 * 영수증 수량을 넘겨 붙은 자동 매칭 줄은 서버가 '영수증에 없는 발송건'으로 옮기고,
 * 과거 영수증의 미발송 구매분으로 메꿀 수 있으면 메꾼다. 사람은 남은 예외만 정한다.
 * 빌려 출고는 정하는 즉시 청구·재고 차감·적립된다. 자동 매칭 내역 표는 고칠 때만 연다.
 * 매칭·금액 계산·정산은 전부 파이썬(/api/rsettle/*)이 한다 — 여기서 다시 계산하지 않는다.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import { RefreshCw, Search } from 'lucide-react';
import Header from '@/components/Header';
import { rsettle, RsView, RsRow, RowAction } from '@/lib/client/rsettle';
import { num } from '@/lib/fmt';
import UnmatchedPanel from '@/components/rsettle/UnmatchedPanel';
import AssignPanel from '@/components/rsettle/AssignPanel';
import ReceiptPanel from '@/components/rsettle/ReceiptPanel';
import DispatchPanel from '@/components/rsettle/DispatchPanel';

type Act = '' | 'ok' | 'stock' | 'cut';
interface Pending { act?: Act; qty?: number; amount?: number; memo?: string }

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const k = (r: { username: string; order_no: string }) => `${r.username}\t${r.order_no}`;

export default function ReceiptSettlePage() {
  const [day, setDay] = useState<string>('');
  const [v, setV] = useState<RsView | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [pend, setPend] = useState<Record<string, Pending>>({});
  const [userF, setUserF] = useState('');
  const [onlyFlag, setOnlyFlag] = useState(false);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get('date');
    setDay(q || today());
  }, []);

  const load = useCallback(async (d: string) => {
    if (!d) return;
    setLoading(true); setErr('');
    try { setV(await rsettle.day(d)); setPend({}); }
    catch (e: any) { setErr(e.message || String(e)); setV(null); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(day); }, [day, load]);

  // 매칭은 사람이 따로 누르는 단계가 아니다 — 발송 파일·영수증을 저장하면 바로 돌린다.
  //   늦게 들어온 지난 발송건(14일)도 항상 함께 본다(그 날짜를 다시 열지 않고 오늘 붙인다).
  const doMatch = async (prefix = '') => {
    setBusy(true); setErr('');
    try {
      const r = await rsettle.match(day, 14);
      setV(r); setPend({});
      const nx = (r.unmatched_orders?.length || 0) + (r.assign?.rows?.length || 0) + (r.undecided || 0);
      setMsg(`${prefix ? prefix + ' → ' : ''}🔎 자동 매칭 ${r.rows?.length || 0}건 · ${num(r.total || 0)}원`
        + (r.notes?._auto_cut ? ` · 영수증보다 많이 붙은 ${r.notes._auto_cut}건은 예외로` : '')
        + (r.notes?._carried ? ` · 지난 발송 ${r.notes._carried}건 포함` : '')
        + (nx ? ` — 아래 예외 ${nx}건만 정하면 됩니다` : ' — 예외 없음, 확정하면 끝'));
    } catch (e: any) { setErr('매칭 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };

  const rows = v?.rows || [];
  const shown = useMemo(() => rows.filter(r =>
    (!userF || r.username === userF) && (!onlyFlag || (r.flag && !r.ok))), [rows, userF, onlyFlag]);
  const nPend = Object.keys(pend).length;

  const setP = (r: RsRow, p: Pending) => setPend(m => {
    const cur = { ...(m[k(r)] || {}), ...p };
    const n = { ...m };
    const same = (cur.act === undefined || cur.act === '') && cur.qty === undefined
      && cur.amount === undefined && cur.memo === undefined;
    if (same) delete n[k(r)]; else n[k(r)] = cur;
    return n;
  });

  const save = async () => {
    const acts: RowAction[] = [];
    for (const r of rows) {
      const p = pend[k(r)];
      if (!p) continue;
      if (p.qty !== undefined || p.amount !== undefined || p.memo !== undefined)
        acts.push({ username: r.username, order_no: r.order_no, action: 'edit', qty: p.qty, amount: p.amount, memo: p.memo });
      if (p.act === 'ok') acts.push({ username: r.username, order_no: r.order_no, action: 'ok' });
      if (p.act === 'stock' || p.act === 'cut') acts.push({ username: r.username, order_no: r.order_no, action: p.act });
    }
    if (!acts.length) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await rsettle.rows(day, acts);
      setV(r); setPend({});
      setMsg(`💾 저장 — 수정 ${r.result.edited}건 · 끊음 ${r.result.cut}건`
        + (r.result.cut ? ' (끊은 건은 아래 "영수증에 없는 발송건"으로 옮겨졌습니다)' : ''));
    } catch (e: any) { setErr('저장 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };

  const doFinalize = async () => {
    if (!v) return;
    const paid = v.paid_invoices || [];
    const t = `${(v.users || []).filter(u => u.count).length}명 · ${num(v.total || 0)}원을 확정합니다.`
      + (paid.length ? `\n\n⚠️ 이미 입금완료된 청구서가 있습니다 — ${paid.map(p => p.name).join(', ')}.\n품목은 들어가지만 청구액은 늘지 않습니다.` : '')
      + ((v.unmatched_orders || []).length ? `\n\n⚠️ 처리 안 한 발송건 ${v.unmatched_orders!.length}건은 이번 청구에서 빠집니다.` : '');
    if (!window.confirm(t)) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await rsettle.finalize(day, false);
      setMsg(`✅ 정산 완료 — 품목 ${r.saved}건 · ${r.users}명 · ${num(r.total)}원`
        + (r.dropped ? ` · ⚠️ 단가 없는 ${r.dropped}건 제외` : '')
        + (r.learned ? ` · 🧠 번호 학습 ${r.learned}건` : '')
        + (r.stock?.credited ? ` · 💰 보유자 예치금 적립 ${r.stock.credited}건` : '')
        + ' — 정산·청구 화면에서 청구하세요.');
      await load(day);
    } catch (e: any) { setErr('정산 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };

  const m = v?.money;
  return (
    <>
      <Header title="🧾 영수증 정산" subtitle="① 발송 파일 → ② 영수증 → ③ 예외만 정하기 → ④ 확정" right={
        <a href={`/app/`} className="text-xs text-gray-500 underline">기존 화면</a>
      } />
      <main className="px-3 md:px-6 pt-3 pb-24 space-y-4">
        {/* 날짜 · 매칭 */}
        <div className="flex flex-wrap items-center gap-2">
          <input type="date" value={day} onChange={e => e.target.value && setDay(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm bg-white" />
          <button onClick={() => load(day)} className="p-2 border rounded-lg bg-white" title="새로고침">
            <RefreshCw size={16} className={clsx(loading && 'animate-spin')} />
          </button>
          <button onClick={() => doMatch()} disabled={busy || !day}
            className="flex items-center gap-1 px-3 py-2 rounded-lg border bg-white text-sm disabled:opacity-50">
            <Search size={16} /> {v?.matched ? '다시 매칭' : '매칭'}
          </button>
          {v?.matched_at && (
            <span className="text-xs text-gray-500">
              매칭 {v.matched_at.slice(5, 16)} · 마지막 저장 {v.updated_at?.slice(5, 16)} {v.updated_by}
            </span>
          )}
        </div>
        <div className="text-xs text-gray-500">
          영수증 {v?.receipt_count ?? 0}품목 · 발송 파일이나 영수증을 저장하면 <b>자동으로 매칭</b>합니다.
          영수증 수량을 넘겨 붙은 주문·지난 날짜에 늦게 들어온 발송건은 자동으로 예외 목록에 들어갑니다.
        </div>

        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm whitespace-pre-wrap">❌ {err}</div>}
        {msg && <div className="bg-green-50 text-green-800 p-3 rounded-lg text-sm">{msg}</div>}

        {day && v && <DispatchPanel day={day} names={v.names} onErr={setErr}
          onSaved={async t => { setErr(''); await doMatch(t); }} />}
        {day && v && <ReceiptPanel day={day} count={v.receipt_count} onErr={setErr}
          onSaved={async t => { setErr(''); await doMatch(t); }} />}
        {v?.receipt_changed && (
          <Warn tone="red">🧾 매칭한 뒤 영수증이 바뀌었습니다({v.receipt_changed.slice(5, 16)}) — <b>다시 매칭</b>해야 새 영수증이 반영됩니다.</Warn>)}

        {v && !v.matched && !loading && (
          <div className="bg-white rounded-xl p-6 text-center text-gray-600 text-sm">
            이 날짜는 아직 매칭하지 않았습니다. 발송 파일·영수증을 저장하면 자동으로 매칭합니다(이미 올렸으면 <b>매칭</b>).
            {v.receipt_count === 0 && <div className="mt-2 text-orange-600">⚠️ 이 날짜 영수증이 없습니다 — 위 🧾 영수증 올리기에서 먼저 저장하세요.</div>}
          </div>
        )}

        {v?.matched && (
          <>
            {/* 경고 */}
            {!!v.paid_invoices?.length && (
              <Warn tone="red">이미 <b>입금완료</b>된 청구서가 있습니다 — {v.paid_invoices.map(p => `${p.name} ${num(p.amount)}원`).join(' · ')}.
                지금 정산하면 품목은 들어가지만 청구액은 늘지 않습니다. 금액을 바꾸려면 정산·청구에서 <b>입금완료 취소</b> 후 정산하세요.</Warn>)}
            {!!v.late_dispatch?.length && (
              <Warn tone="red">매칭한 뒤에 새로 발송된 주문 {v.late_dispatch.length}건 — {v.late_dispatch.slice(0, 4).map(x => `${x.name} ${x.order_no}`).join(' · ')}. {' '}
                <button onClick={() => doMatch()} className="underline font-bold">다시 매칭</button></Warn>)}

            {/* KPI */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Kpi label="청구 합계(물건값)" value={v.total || 0} sub={`${(v.users || []).filter(u => u.count).length}명 · ${rows.length}건`} />
              <Kpi label="③ 영수증에 없는 발송건" value={(v.unmatched_orders || []).length} unit="건" warn={!!v.unmatched_orders?.length} ok={!v.unmatched_orders?.length} sub="정하기 전에는 청구 안 됨" />
              <Kpi label="③ 남은 영수증 품목" value={(v.assign?.rows || []).length} unit="건" warn={!!v.assign?.rows?.length} ok={!v.assign?.rows?.length} sub="누구에게 청구·재고·반품" />
              <Kpi label="③ 일부만 넘친 주문" value={v.undecided || 0} unit="건" warn={!!v.undecided} ok={!v.undecided} sub="아래 자동 매칭 내역에서 정함" />
            </div>

            {/* ③ 예외만 — 자동으로 못 정한 것 */}
            <UnmatchedPanel v={v} day={day} busy={busy} setBusy={setBusy} onErr={setErr}
              onDone={(nv, t) => { setV(nv); setPend({}); setErr(''); setMsg(t); }} />
            <AssignPanel v={v} day={day} busy={busy} setBusy={setBusy} onErr={setErr}
              onDone={(nv, t) => { setV(nv); setPend({}); setErr(''); setMsg(t); }} />

            <details open={!!v.undecided || nPend > 0} className="bg-white rounded-xl shadow-sm">
              <summary className="px-3 py-2 cursor-pointer text-sm font-bold">
                🔍 자동 매칭 내역 {rows.length}건 · 사용자별 청구 — 고칠 것이 있을 때만 여세요{v.undecided ? ` (⚠️ 일부만 넘친 주문 ${v.undecided}건)` : ''}
              </summary>
              <div className="space-y-3 p-2">
            {/* 사용자별 */}
            <section className="bg-white rounded-xl shadow-sm overflow-x-auto">
              <div className="px-3 py-2 font-bold text-sm border-b">💰 사용자별 정산표</div>
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500"><tr>
                  <th className="text-left p-2">판매자</th><th className="text-right p-2">배치</th>
                  <th className="text-right p-2">청구액</th><th className="text-right p-2">미처리 발송</th><th />
                </tr></thead>
                <tbody>{(v.users || []).map(u => (
                  <tr key={u.username} className={clsx('border-t', userF === u.username && 'bg-blue-50')}>
                    <td className="p-2">{u.name}</td><td className="p-2 text-right">{u.count}</td>
                    <td className="p-2 text-right font-semibold">{num(u.amount)}</td>
                    <td className={clsx('p-2 text-right', u.unmatched && 'text-orange-600 font-semibold')}>{u.unmatched || ''}</td>
                    <td className="p-2 text-right">
                      <button onClick={() => setUserF(userF === u.username ? '' : u.username)}
                        className="text-xs underline text-gray-500">{userF === u.username ? '전체 보기' : '이 사람만'}</button>
                    </td>
                  </tr>))}
                </tbody>
              </table>
            </section>

            {/* 배치 상세 */}
            <section className="bg-white rounded-xl shadow-sm">
              <div className="px-3 py-2 border-b flex flex-wrap items-center gap-3">
                <span className="font-bold text-sm">🔍 배치 상세 ({shown.length}/{rows.length})</span>
                <label className="text-xs flex items-center gap-1">
                  <input type="checkbox" checked={onlyFlag} onChange={e => setOnlyFlag(e.target.checked)} /> 확인 필요만
                </label>
                {userF && <button onClick={() => setUserF('')} className="text-xs underline">사용자 필터 해제</button>}
                <span className="flex-1" />
                <button onClick={() => setPend({})} disabled={!nPend || busy} className="text-xs px-2 py-1 border rounded disabled:opacity-40">되돌리기</button>
                <button onClick={save} disabled={!nPend || busy}
                  className="text-sm px-3 py-1.5 rounded-lg bg-primary text-white font-bold disabled:opacity-40">💾 변경 {nPend}건 저장</button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 text-gray-500"><tr>
                    <th className="p-2 text-left">처리</th><th className="p-2 text-left">확인</th>
                    <th className="p-2 text-left">판매자</th><th className="p-2 text-left">주문번호</th>
                    <th className="p-2 text-left">상품명</th><th className="p-2 text-right">수량</th>
                    <th className="p-2 text-right">정가</th><th className="p-2 text-right">실단가</th>
                    <th className="p-2 text-right">청구액</th><th className="p-2 text-left">근거</th><th className="p-2 text-left">메모</th>
                  </tr></thead>
                  <tbody>{shown.map(r => {
                    const p = pend[k(r)] || {};
                    return (
                      <tr key={k(r)} className={clsx('border-t align-top', r.flag && !r.ok && 'bg-yellow-50', Object.keys(p).length && 'bg-blue-50')}>
                        <td className="p-1">
                          <select value={p.act ?? ''} onChange={e => setP(r, { act: e.target.value as Act })}
                            className="border rounded px-1 py-1 bg-white">
                            <option value="">— 그대로 —</option>
                            {r.flag && <option value="ok">✅ 영수증가로 청구</option>}
                            <option value="stock">📦 재고에서 나감</option>
                            <option value="cut">✂️ 끊기(이번 청구 제외)</option>
                          </select>
                        </td>
                        <td className="p-2 whitespace-nowrap">{r.flag ? (r.ok ? '✅ 확인됨' : `⚠️ ${r.flag}`) : ''}</td>
                        <td className="p-2 whitespace-nowrap">{r.name}</td>
                        <td className="p-2 font-mono">{r.order_no}</td>
                        <td className="p-2 min-w-[180px]">{r.product_name}{r.stock_owner && r.stock_owner !== r.username && <span className="text-purple-700"> · 보유자 {v.names[r.stock_owner] || r.stock_owner}</span>}</td>
                        <td className="p-1 text-right"><input type="number" min={1} value={p.qty ?? r.qty}
                          onChange={e => setP(r, { qty: Number(e.target.value) === r.qty ? undefined : Number(e.target.value) })}
                          className="w-14 border rounded px-1 py-1 text-right" /></td>
                        <td className="p-2 text-right">{r.list_price ? num(r.list_price) : ''}</td>
                        <td className="p-2 text-right">{num(r.unit_price)}</td>
                        <td className="p-1 text-right"><input type="number" min={0} step={100} value={p.amount ?? r.amount}
                          onChange={e => setP(r, { amount: Number(e.target.value) === r.amount ? undefined : Number(e.target.value) })}
                          className="w-24 border rounded px-1 py-1 text-right" /></td>
                        <td className="p-2 whitespace-nowrap">{r.via_label}{r.edited && ' ✏️'}</td>
                        <td className="p-1"><input value={p.memo ?? r.memo}
                          onChange={e => setP(r, { memo: e.target.value === r.memo ? undefined : e.target.value })}
                          className="w-40 border rounded px-1 py-1" /></td>
                      </tr>);
                  })}</tbody>
                </table>
              </div>
              <div className="px-3 py-2 text-[11px] text-gray-500">
                ✅ 영수증가로 청구 = 그날 산 물건이 맞다 · 📦 재고에서 나감 = 누군가의 재고에서 나갔다(영수증에 없는 발송건으로 옮겨 보유자를 정함) · ✂️ 끊기 = 이번 청구에서 뺌.
                고친 값은 다시 매칭해도 유지됩니다.
              </div>
            </section>

              </div>
            </details>

            {/* 돈 흐름 */}
            {m && (
              <details className="bg-white rounded-xl shadow-sm">
                <summary className="px-3 py-2 cursor-pointer text-sm font-bold">💰 돈 흐름 확인{m.undecided ? ` · ⚠️ 영수증 중 아직 안 정한 돈 ${num(m.undecided)}원` : ' · ✅ 영수증 전부 정리됨'}</summary>
              <section className="grid md:grid-cols-2 gap-3 p-2">
                <Flow title="🧾 영수증 돈은 어디로 갔나" items={[
                  ['영수증 합계', m.receipt_total, true], ['① 주문에 붙어 청구', m.receipt_bill], ['② 재고로 입고', m.stock_in],
                  ['③ 반품요청', m.returned], ['④ 아직 안 정함', m.undecided, true]]}
                  foot={m.undecided > 0 ? `영수증 중 ${num(m.undecided)}원이 아직 청구·재고·반품 어느 것도 아닙니다 — 위 '배정할 영수증 품목'에서 정하세요.`
                    : m.undecided < 0 ? `영수증보다 ${num(-m.undecided)}원 더 청구·입고됐습니다 — 배치 상세의 ⚠️ 줄을 확인하세요.` : '✅ 영수증이 전부 정리됐습니다.'} />
                <Flow title="💳 청구 돈은 어디서 왔나" items={[
                  ['청구 합계', m.bill_total, true], ['ⓐ 그날 영수증으로 산 것', m.receipt_bill], ['ⓑ 본인·과거 구매분 재고', m.self_stock],
                  ['ⓒ 남의 재고 → 보유자에게 감', m.other_stock], ['ⓓ 금액 직접 지정', m.manual], ['ⓔ 온라인몰 직배송', m.online]]}
                  foot={m.owners.length ? 'ⓒ는 빌려 출고를 정할 때 보유자 예치금에 적립됨 — ' + m.owners.map(o => `${v.names[o.username] || o.username} +${num(o.amount)}원`).join(' · ') : ''} />
              </section>
              </details>)}

            {/* ④ 확정 */}
            <section className="bg-white rounded-xl shadow-sm p-3 space-y-2 sticky bottom-2 border">
              <button onClick={doFinalize} disabled={busy || !!v.undecided || !rows.length || nPend > 0}
                className="w-full py-3 rounded-lg bg-primary text-white font-bold disabled:opacity-40">
                ✅ ④ 확정 ({(v.users || []).filter(u => u.count).length}명 · {num(v.total || 0)}원)
              </button>
              {(!!v.undecided || nPend > 0) && <div className="text-xs text-gray-500">
                {nPend > 0 ? '저장하지 않은 변경이 있습니다 — 먼저 💾 저장하세요. ' : ''}
                {v.undecided ? `영수증보다 많이 나간 주문 ${v.undecided}건을 정해야 켜집니다.` : ''}</div>}
            </section>
          </>
        )}
      </main>
    </>
  );
}

function Warn({ tone, children }: { tone: 'red' | 'yellow'; children: React.ReactNode }) {
  return <div className={clsx('p-3 rounded-lg text-sm', tone === 'red' ? 'bg-red-50 text-red-800' : 'bg-yellow-50 text-yellow-900')}>{children}</div>;
}

function Kpi({ label, value, sub, warn, ok, unit = '원' }: { label: string; value: number; sub?: string; warn?: boolean; ok?: boolean; unit?: string }) {
  return (
    <div className={clsx('bg-white rounded-xl p-3 border-l-4 shadow-sm', warn ? 'border-yellow-400' : ok ? 'border-green-500' : 'border-primary')}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-bold">{num(value)}{unit}</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  );
}

function Flow({ title, items, foot }: { title: string; items: [string, number, boolean?][]; foot?: string }) {
  return (
    <div className="bg-white rounded-xl shadow-sm p-3">
      <div className="font-bold text-sm mb-2">{title}</div>
      <table className="w-full text-sm"><tbody>{items.map(([l, n, b]) => (
        <tr key={l} className={clsx('border-t', b && 'font-semibold')}><td className="py-1">{l}</td><td className="py-1 text-right">{num(n)}</td></tr>))}
      </tbody></table>
      {foot && <div className="text-xs text-gray-600 mt-2">{foot}</div>}
    </div>
  );
}
