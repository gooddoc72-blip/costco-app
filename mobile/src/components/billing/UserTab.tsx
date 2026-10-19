'use client';
/**
 * 👤 사용자별 — 한 사람의 여러 날치 청구를 한 번에 입금완료.
 *
 * 입금은 사람 단위로 여러 날치를 한 번에 보내는 일이 많다. Streamlit에서는
 * 체크할 때마다 화면 전체를 다시 계산(4~5초)하는 동안 '입금완료 저장 (0일)'
 * 버튼이 갱신되지 않아, 체크하고 눌러도 저장되지 않았다(10/19). 여기서는
 * 체크·금액이 이 표 안에서만 바뀌고 버튼을 누를 때만 서버에 저장한다.
 */
import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import { billing, ItemsResp, UserInvRow, UserResp } from '@/lib/client/billing';
import { num } from '@/lib/fmt';
import { Btn } from './DayTab';

const ST_LABEL: Record<string, string> = { draft: '청구 전', billed: '청구됨', paid: '입금완료' };
const ST_CLS: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-700', billed: 'bg-yellow-100 text-yellow-800', paid: 'bg-green-100 text-green-800',
};

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function daysAgo(n: number): string { const d = new Date(); d.setDate(d.getDate() - n); return ymd(d); }
const PERIODS: Record<string, () => [string, string]> = {
  '최근 30일': () => [daysAgo(29), ymd(new Date())],
  '최근 90일': () => [daysAgo(89), ymd(new Date())],
  '이번 달': () => { const t = new Date(); return [ymd(new Date(t.getFullYear(), t.getMonth(), 1)), ymd(t)]; },
  '지난 달': () => {
    const t = new Date();
    return [ymd(new Date(t.getFullYear(), t.getMonth() - 1, 1)), ymd(new Date(t.getFullYear(), t.getMonth(), 0))];
  },
};

export default function UserTab({ initialUser, onGoDay }: { initialUser?: string; onGoDay: (d: string) => void }) {
  const [users, setUsers] = useState<{ username: string; name: string }[]>([]);
  const [q, setQ] = useState('');
  const [user, setUser] = useState(initialUser || '');
  const [period, setPeriod] = useState('최근 30일');
  const [data, setData] = useState<UserResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [amt, setAmt] = useState<Record<string, number>>({});
  const [memo, setMemo] = useState<Record<string, string>>({});
  const [open, setOpen] = useState('');
  const [items, setItems] = useState<Record<string, ItemsResp>>({});

  useEffect(() => {
    billing.users().then(r => setUsers(r.rows)).catch(e => setErr(e.message || String(e)));
  }, []);

  const load = useCallback(async (u: string, p: string) => {
    if (!u) { setData(null); return; }
    const [f, t] = PERIODS[p]();
    setLoading(true); setErr('');
    try {
      setData(await billing.user(u, f, t));
      setSel(new Set()); setAmt({}); setMemo({}); setItems({}); setOpen('');
    } catch (e: any) { setErr(e.message || String(e)); setData(null); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(user, period); }, [user, period, load]);

  const rows = data?.rows || [];
  const billed = rows.filter(r => r.status === 'billed');
  const picked = billed.filter(r => sel.has(r.date));
  const payOf = (r: UserInvRow) => amt[r.date] ?? r.total_amount;
  const pickedSum = picked.reduce((a, r) => a + payOf(r), 0);

  const toggle = (d: string) => setSel(s => { const n = new Set(s); n.has(d) ? n.delete(d) : n.add(d); return n; });
  const toggleAll = () => setSel(s => s.size === billed.length ? new Set() : new Set(billed.map(r => r.date)));

  const openItems = async (d: string) => {
    if (open === d) { setOpen(''); return; }
    setOpen(d);
    if (!items[d]) {
      try { const r = await billing.items(d, user); setItems(m => ({ ...m, [d]: r })); }
      catch (e: any) { setErr(e.message || String(e)); }
    }
  };

  const doPaid = async () => {
    if (!data || !picked.length) return;
    if (!window.confirm(`${data.name} ${picked.length}일 입금완료 처리합니다 (입금액 합계 ${num(pickedSum)}원).`)) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await billing.userPaid(user, picked.map(x => ({ date: x.date, paid_amount: payOf(x), memo: memo[x.date] || '' })));
      setMsg(`✅ ${data.name} ${r.paid}일 · ${num(r.amount)}원 입금완료`
        + (r.skipped.length ? ` (건너뜀 ${r.skipped.length}일 — 이미 처리됐거나 청구 전)` : ''));
      await load(user, period);
    } catch (e: any) { setErr(`입금완료 실패: ${e.message || e}`); }
    finally { setBusy(false); }
  };

  const doUnpay = async (r: UserInvRow) => {
    if (!data) return;
    if (!window.confirm(`${data.name} ${r.date} 입금완료를 취소하고 '청구됨'으로 되돌립니다.`
      + (r.pay_method === 'deposit' ? '\n💳 예치금으로 결제한 건이라 예치금으로 돌아갑니다.' : ''))) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const x = await billing.unpay(r.date, user);
      setMsg(`✅ ${r.date} 입금완료 취소` + (x.deposit_refunded ? ` — 예치금 반환(잔액 ${num(x.deposit_refunded.balance)}원)` : ''));
      await load(user, period);
    } catch (e: any) { setErr(`입금완료 취소 실패: ${e.message || e}`); }
    finally { setBusy(false); }
  };

  const shown = users.filter(u => !q || u.name.includes(q) || u.username.toLowerCase().includes(q.toLowerCase()));
  const s = data?.summary;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="이름·아이디로 찾기"
          className="border rounded-lg px-3 py-2 text-sm bg-white w-40" />
        <select value={user} onChange={e => setUser(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm bg-white min-w-[180px]">
          <option value="">판매자 선택 ({shown.length}명)</option>
          {shown.map(u => (
            <option key={u.username} value={u.username}>
              {u.name !== u.username ? `${u.name} (${u.username})` : u.username}
            </option>
          ))}
        </select>
        <select value={period} onChange={e => setPeriod(e.target.value)} className="border rounded-lg px-3 py-2 text-sm bg-white">
          {Object.keys(PERIODS).map(k => <option key={k}>{k}</option>)}
        </select>
        {user && (
          <button onClick={() => load(user, period)} className="p-2 border rounded-lg bg-white" title="새로고침">
            <RefreshCw size={16} className={clsx(loading && 'animate-spin')} />
          </button>
        )}
      </div>

      {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm whitespace-pre-wrap">❌ {err}</div>}
      {msg && <div className="bg-green-50 text-green-800 p-3 rounded-lg text-sm">{msg}</div>}

      {!user && <div className="bg-white rounded-xl border p-6 text-center text-sm text-gray-500">판매자를 고르세요.</div>}

      {s && data && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Kpi label={`청구 합계 (${data.date_from.slice(5)}~${data.date_to.slice(5)})`} v={s.total} />
          <Kpi label="청구 전" v={s.draft} />
          <Kpi label="청구·미입금" v={s.billed} sub={`${s.billed_n}일`} warn={s.billed_n > 0} />
          <Kpi label="입금완료" v={s.paid} ok />
          <Kpi label="예치금 잔액" v={data.balance} red={data.balance < 0} />
        </div>
      )}

      {data && rows.length === 0 && (
        <div className="bg-white rounded-xl border p-6 text-center text-sm text-gray-500">이 기간 청구가 없습니다.</div>
      )}

      {rows.length > 0 && (
        <div className="bg-white rounded-xl border overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-600 text-xs">
              <tr>
                <th className="p-2 w-8">
                  <input type="checkbox" disabled={!billed.length}
                    checked={billed.length > 0 && sel.size === billed.length} onChange={toggleAll} title="청구됨 전체" />
                </th>
                <th className="p-2 text-left">정산일</th>
                <th className="p-2 text-left">상태</th>
                <th className="p-2 text-right">품목</th>
                <th className="p-2 text-right">청구액</th>
                <th className="p-2 text-right">실입금액</th>
                <th className="p-2 text-left">메모</th>
                <th className="p-2 text-left hidden md:table-cell">결제·일시</th>
                <th className="p-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const on = sel.has(r.date);
                return (
                  <FragRow key={r.date}>
                    <tr className={clsx('border-t', on && 'bg-blue-50')}>
                      <td className="p-2 text-center">
                        {r.status === 'billed' && <input type="checkbox" checked={on} onChange={() => toggle(r.date)} />}
                      </td>
                      <td className="p-2 whitespace-nowrap">
                        <button className="underline decoration-dotted" onClick={() => onGoDay(r.date)} title="그 날짜 정산리스트로">{r.date}</button>
                      </td>
                      <td className="p-2"><span className={clsx('text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap', ST_CLS[r.status])}>{ST_LABEL[r.status] || r.status}</span></td>
                      <td className="p-2 text-right">{r.item_count}</td>
                      <td className="p-2 text-right font-semibold whitespace-nowrap">{num(r.total_amount)}</td>
                      <td className="p-2 text-right whitespace-nowrap">
                        {on ? (
                          <input type="number" min={0} className="w-28 border rounded px-1 py-0.5 text-right"
                            value={payOf(r)} onChange={e => setAmt(m => ({ ...m, [r.date]: Math.max(0, Number(e.target.value) || 0) }))}
                            title="실입금액 (부분 입금이면 고치세요)" />
                        ) : (r.paid_amount ? num(r.paid_amount) : '')}
                      </td>
                      <td className="p-2">
                        {on ? (
                          <input className="w-32 border rounded px-1 py-0.5 text-xs" value={memo[r.date] || ''}
                            onChange={e => setMemo(m => ({ ...m, [r.date]: e.target.value }))} placeholder="메모" />
                        ) : <span className="text-xs text-gray-500">{r.memo}</span>}
                      </td>
                      <td className="p-2 text-[11px] text-gray-500 hidden md:table-cell whitespace-nowrap">
                        {r.pay_method === 'deposit' ? '💳 예치금 ' : r.pay_method === 'account' ? '🏦 계좌 ' : ''}
                        {r.paid_at ? `입금 ${r.paid_at.slice(5)}` : r.billed_at ? `청구 ${r.billed_at.slice(5)}` : ''}
                      </td>
                      <td className="p-2 whitespace-nowrap text-right">
                        {r.status === 'paid' && (
                          <button disabled={busy} onClick={() => doUnpay(r)} className="text-[11px] text-gray-500 underline mr-2">입금 취소</button>
                        )}
                        <button onClick={() => openItems(r.date)} className="text-blue-600 inline-flex items-center text-xs">
                          {open === r.date ? <ChevronDown size={14} /> : <ChevronRight size={14} />} 내역
                        </button>
                      </td>
                    </tr>
                    {open === r.date && (
                      <tr className="border-t bg-gray-50">
                        <td colSpan={9} className="p-2">
                          {!items[r.date] ? <div className="text-xs text-gray-500 p-2">불러오는 중…</div> : <ItemList it={items[r.date]} />}
                        </td>
                      </tr>
                    )}
                  </FragRow>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {billed.length > 0 && (
        <div className="bg-white rounded-xl border p-3 space-y-2 sticky bottom-2 shadow-md">
          <div className="text-xs text-gray-500">
            입금된 날짜를 체크하세요(청구됨만 체크됩니다). 여러 날치를 한 번에 보냈으면 모두 체크합니다.
            금액이 다르면 실입금액을 고칠 수 있습니다(부분 입금).
          </div>
          <Btn primary disabled={busy || !picked.length} onClick={doPaid}>
            💰 입금완료 저장 {picked.length}일 ({num(pickedSum)}원)
          </Btn>
        </div>
      )}
    </div>
  );
}

function FragRow({ children }: { children: React.ReactNode }) { return <>{children}</>; }

function ItemList({ it }: { it: ItemsResp }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs bg-white rounded border">
        <thead className="bg-gray-100 text-gray-600">
          <tr><th className="p-1.5 text-left">근거</th><th className="p-1.5 text-left">상품명</th>
            <th className="p-1.5 text-right">수량</th><th className="p-1.5 text-right">팩단가</th><th className="p-1.5 text-right">금액</th></tr>
        </thead>
        <tbody>
          {it.items.map(x => (
            <tr key={x.id} className="border-t">
              <td className="p-1.5 whitespace-nowrap">{x.source}</td>
              <td className="p-1.5 min-w-[180px]">{x.product_name}</td>
              <td className="p-1.5 text-right">{x.qty}</td>
              <td className="p-1.5 text-right">{num(x.unit_price)}</td>
              <td className="p-1.5 text-right font-medium">{num(x.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="text-xs text-gray-600 mt-1">물건값 합계 {num(it.goods_total)}원 · 청구액 {num(it.invoice_total)}원</div>
    </div>
  );
}

function Kpi({ label, v, sub, warn, ok, red }: { label: string; v: number; sub?: string; warn?: boolean; ok?: boolean; red?: boolean }) {
  return (
    <div className={clsx('bg-white rounded-xl p-3 border-l-4 shadow-sm',
      red ? 'border-red-500' : warn ? 'border-yellow-400' : ok ? 'border-green-500' : 'border-primary')}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className={clsx('text-lg font-bold', red && 'text-red-600')}>{num(v)}원</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  );
}
