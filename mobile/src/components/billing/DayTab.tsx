'use client';
/**
 * 📋 일별 정산·청구 — 정산리스트 · 품목 내역 · 청구 · 예치금 차감 · 입금완료 · 입금 취소.
 *
 * 화면 전체를 다시 그리지 않는다. 체크·금액 입력은 이 표 안에서만 바뀌고,
 * 버튼을 눌렀을 때만 서버에 저장한 뒤 이 날짜 표를 다시 받아 온다.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import { billing, DayResp, DayRow, ItemsResp } from '@/lib/client/billing';
import { num } from '@/lib/fmt';

const ST_LABEL: Record<string, string> = { draft: '정산완료(청구 전)', billed: '청구됨', paid: '입금완료' };
const ST_CLS: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-700', billed: 'bg-yellow-100 text-yellow-800', paid: 'bg-green-100 text-green-800',
};

function yesterday(): string {
  const d = new Date(); d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export default function DayTab({ initialDay }: { initialDay?: string }) {
  const [day, setDay] = useState(initialDay || yesterday());
  const [data, setData] = useState<DayResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [payAmt, setPayAmt] = useState<Record<string, number>>({});
  const [open, setOpen] = useState<string>('');
  const [items, setItems] = useState<Record<string, ItemsResp>>({});

  const load = useCallback(async (d: string) => {
    setLoading(true); setErr('');
    try {
      const r = await billing.day(d);
      setData(r); setSel(new Set()); setPayAmt({}); setItems({}); setOpen('');
    } catch (e: any) { setErr(e.message || String(e)); setData(null); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(day); }, [day, load]);

  const rows = data?.rows || [];
  const picked = rows.filter(r => sel.has(r.username));
  const toBill = picked.filter(r => r.status === 'draft' && r.total_amount > 0);
  const toDeduct = picked.filter(r => r.status !== 'paid' && r.total_amount > 0 && !r.deducted);
  const toPay = picked.filter(r => r.status === 'billed');
  const short = toDeduct.filter(r => r.balance < r.total_amount);
  const sum = (xs: DayRow[], f: (r: DayRow) => number = r => r.total_amount) => xs.reduce((a, r) => a + f(r), 0);

  const toggle = (u: string) => setSel(s => { const n = new Set(s); n.has(u) ? n.delete(u) : n.add(u); return n; });
  const toggleAll = () => setSel(s => s.size === rows.length ? new Set() : new Set(rows.map(r => r.username)));

  const openItems = async (u: string) => {
    if (open === u) { setOpen(''); return; }
    setOpen(u);
    if (!items[u]) {
      try { const r = await billing.items(day, u); setItems(m => ({ ...m, [u]: r })); }
      catch (e: any) { setErr(e.message || String(e)); }
    }
  };

  const run = async (label: string, confirmText: string, fn: () => Promise<string>) => {
    if (!window.confirm(confirmText)) return;
    setBusy(true); setErr(''); setMsg('');
    try { const m = await fn(); setMsg(`✅ ${label} — ${m}`); await load(day); }
    catch (e: any) { setErr(`${label} 실패: ${e.message || e}`); }
    finally { setBusy(false); }
  };

  const doBill = () => run('청구', `${toBill.length}명에게 ${num(sum(toBill))}원을 청구합니다.`,
    async () => { const r = await billing.bill(day, toBill.map(x => x.username)); return `${r.billed}명 · ${num(r.amount)}원`; });
  const doDeduct = () => run('예치금 차감',
    `${toDeduct.length}명 예치금에서 ${num(sum(toDeduct))}원을 차감하고 입금완료로 처리합니다.`
      + (short.length ? `\n\n⚠️ 잔액이 모자란 ${short.length}명은 마이너스가 됩니다.` : ''),
    async () => { const r = await billing.deduct(day, toDeduct.map(x => x.username)); return `${r.deducted.length}명 · ${num(r.amount)}원`; });
  const doPaid = () => run('입금완료',
    `${toPay.length}명 입금완료 처리합니다 (입금액 합계 ${num(sum(toPay, r => payAmt[r.username] ?? r.total_amount))}원).`,
    async () => {
      const r = await billing.paid(day, toPay.map(x => ({ username: x.username, paid_amount: payAmt[x.username] ?? x.total_amount })));
      return `${r.paid}명`;
    });
  const doUnpay = (r: DayRow) => run('입금완료 취소',
    `${r.name} 입금완료를 취소하고 '청구됨'으로 되돌립니다.`
      + (r.pay_method === 'deposit' ? `\n💳 예치금으로 결제한 건이라 ${num(r.deducted)}원이 예치금으로 돌아갑니다.` : ''),
    async () => {
      const x = await billing.unpay(day, r.username);
      return x.deposit_refunded ? `예치금 반환(잔액 ${num(x.deposit_refunded.balance)}원)` : '청구됨으로 되돌림';
    });

  const s = data?.summary;
  return (
    <div className="space-y-4">
      {/* 날짜 */}
      <div className="flex flex-wrap items-center gap-2">
        <input type="date" value={day} onChange={e => e.target.value && setDay(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm bg-white" />
        <button onClick={() => load(day)} className="p-2 border rounded-lg bg-white" title="새로고침">
          <RefreshCw size={16} className={clsx(loading && 'animate-spin')} />
        </button>
        {data?.recent?.slice(0, 7).map(r => (
          <button key={r.date} onClick={() => setDay(r.date)}
            className={clsx('text-xs px-2 py-1 rounded-full border',
              r.date === day ? 'bg-primary text-white border-primary' : 'bg-white text-gray-600')}>
            {r.date.slice(5)} · {r.users}명 {num(r.total)}원
          </button>
        ))}
      </div>

      {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm whitespace-pre-wrap">❌ {err}</div>}
      {msg && <div className="bg-green-50 text-green-800 p-3 rounded-lg text-sm">{msg}</div>}

      {s && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Kpi label="총 청구액" value={s.total} sub={`${s.users}명`} />
          <Kpi label="청구 전" value={s.draft} sub={`${s.draft_n}명`} />
          <Kpi label="청구·미입금" value={s.billed} sub={`${s.billed_n}명`} warn={s.billed_n > 0} />
          <Kpi label="입금완료" value={s.paid} sub={`${s.paid_n}명 · 예치금 ${s.deposit_n}명`} ok />
        </div>
      )}

      {!loading && data && rows.length === 0 && (
        <div className="bg-white rounded-xl p-6 text-center text-sm text-gray-500 border">
          {day} 정산 내역이 없습니다. 영수증 정산에서 그날 영수증을 매칭해 정산하세요.
        </div>
      )}

      {rows.length > 0 && (
        <div className="bg-white rounded-xl border overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-600 text-xs">
              <tr>
                <th className="p-2 w-8"><input type="checkbox" checked={sel.size === rows.length} onChange={toggleAll} /></th>
                <th className="p-2 text-left">판매자</th>
                <th className="p-2 text-left">상태</th>
                <th className="p-2 text-right">품목</th>
                <th className="p-2 text-right">청구액</th>
                <th className="p-2 text-left">결제</th>
                <th className="p-2 text-right">입금액</th>
                <th className="p-2 text-right">예치금 잔액</th>
                <th className="p-2 text-left hidden md:table-cell">청구·입금 일시</th>
                <th className="p-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <RowFrag key={r.username} r={r} checked={sel.has(r.username)} onCheck={() => toggle(r.username)}
                  open={open === r.username} onOpen={() => openItems(r.username)} items={items[r.username]}
                  payAmt={payAmt[r.username]} onPayAmt={v => setPayAmt(m => ({ ...m, [r.username]: v }))}
                  showPayInput={sel.has(r.username) && r.status === 'billed'}
                  onUnpay={() => doUnpay(r)} busy={busy} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows.length > 0 && (
        <div className="bg-white rounded-xl border p-3 space-y-2 sticky bottom-2 shadow-md">
          <div className="text-xs text-gray-500">
            선택 {picked.length}명 — 판매자를 체크한 뒤 할 일을 누르세요. 청구액은 <b>물건값만</b>입니다(택배·포장비 별도).
          </div>
          {short.length > 0 && (
            <div className="text-xs text-red-600">
              ⚠️ 예치금이 모자란 판매자: {short.map(r => `${r.name} ${num(r.total_amount - r.balance)}원 부족`).join(' · ')}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <Btn disabled={busy || !toBill.length} onClick={doBill} primary>
              📨 청구 {toBill.length}명 ({num(sum(toBill))}원)
            </Btn>
            <Btn disabled={busy || !toDeduct.length} onClick={doDeduct}>
              💳 예치금 차감 {toDeduct.length}명 ({num(sum(toDeduct))}원)
            </Btn>
            <Btn disabled={busy || !toPay.length} onClick={doPaid}>
              💰 입금완료 {toPay.length}명
            </Btn>
          </div>
        </div>
      )}
    </div>
  );
}

function RowFrag(p: {
  r: DayRow; checked: boolean; onCheck: () => void; open: boolean; onOpen: () => void;
  items?: ItemsResp; payAmt?: number; onPayAmt: (v: number) => void; showPayInput: boolean;
  onUnpay: () => void; busy: boolean;
}) {
  const { r } = p;
  return (
    <>
      <tr className={clsx('border-t', p.checked && 'bg-blue-50')}>
        <td className="p-2 text-center"><input type="checkbox" checked={p.checked} onChange={p.onCheck} /></td>
        <td className="p-2 font-medium whitespace-nowrap">{r.name}</td>
        <td className="p-2"><span className={clsx('text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap', ST_CLS[r.status])}>{ST_LABEL[r.status] || r.status}</span></td>
        <td className="p-2 text-right">{r.item_count}</td>
        <td className="p-2 text-right font-semibold whitespace-nowrap">{num(r.total_amount)}</td>
        <td className="p-2 whitespace-nowrap text-xs">{r.pay_method === 'deposit' ? '💳 예치금' : r.pay_method === 'account' ? '🏦 계좌입금' : ''}</td>
        <td className="p-2 text-right whitespace-nowrap">
          {p.showPayInput ? (
            <input type="number" className="w-28 border rounded px-1 py-0.5 text-right"
              value={p.payAmt ?? r.total_amount} min={0}
              onChange={e => p.onPayAmt(Math.max(0, Number(e.target.value) || 0))} title="실입금액 (부분 입금이면 고치세요)" />
          ) : (r.paid_amount ? num(r.paid_amount) : '')}
        </td>
        <td className={clsx('p-2 text-right whitespace-nowrap', r.balance < 0 && 'text-red-600')}>{num(r.balance)}</td>
        <td className="p-2 text-[11px] text-gray-500 hidden md:table-cell whitespace-nowrap">
          {r.billed_at && <>청구 {r.billed_at.slice(5)}<br /></>}{r.paid_at && <>입금 {r.paid_at.slice(5)}</>}
        </td>
        <td className="p-2 whitespace-nowrap text-right">
          {r.status === 'paid' && (
            <button disabled={p.busy} onClick={p.onUnpay} className="text-[11px] text-gray-500 underline mr-2">입금 취소</button>
          )}
          <button onClick={p.onOpen} className="text-blue-600 inline-flex items-center text-xs">
            {p.open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} 내역
          </button>
        </td>
      </tr>
      {p.open && (
        <tr className="border-t bg-gray-50">
          <td colSpan={10} className="p-2">
            {!p.items ? <div className="text-xs text-gray-500 p-2">불러오는 중…</div> : <Items it={p.items} />}
          </td>
        </tr>
      )}
    </>
  );
}

function Items({ it }: { it: ItemsResp }) {
  const nDisc = it.items.filter(x => x.discount_per_pack > 0).length;
  const saved = it.items.reduce((a, x) => a + x.discount_per_pack * x.qty, 0);
  const diff = it.goods_total - it.invoice_total;
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-xs bg-white rounded border">
          <thead className="bg-gray-100 text-gray-600">
            <tr>
              <th className="p-1.5 text-left">근거</th><th className="p-1.5 text-left">상품명</th>
              <th className="p-1.5 text-left">코스트코번호</th><th className="p-1.5 text-right">수량</th>
              <th className="p-1.5 text-right">정가</th><th className="p-1.5 text-right">할인(팩당)</th>
              <th className="p-1.5 text-right">팩단가</th><th className="p-1.5 text-right">금액</th>
              <th className="p-1.5 text-left hidden md:table-cell">주문번호</th>
            </tr>
          </thead>
          <tbody>
            {it.items.map(x => (
              <tr key={x.id} className="border-t">
                <td className="p-1.5 whitespace-nowrap">{x.source}</td>
                <td className="p-1.5 min-w-[180px]">{x.product_name}{x.memo && <span className="text-gray-400"> · {x.memo}</span>}</td>
                <td className="p-1.5">{x.product_no}</td>
                <td className="p-1.5 text-right">{x.qty}</td>
                <td className="p-1.5 text-right">{x.list_price ? num(x.list_price) : '—'}</td>
                <td className="p-1.5 text-right">{x.discount_per_pack ? num(x.discount_per_pack) : ''}</td>
                <td className="p-1.5 text-right">{num(x.unit_price)}</td>
                <td className="p-1.5 text-right font-medium">{num(x.amount)}</td>
                <td className="p-1.5 text-gray-500 hidden md:table-cell">{x.order_no}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-gray-600">
        물건값 합계 {num(it.goods_total)}원 = 청구액 {num(it.invoice_total)}원 — 택배비·포장비는 별도 청구합니다.
        {nDisc > 0 && <span className="text-green-700"> · 💰 할인 반영 {nDisc}품목 {num(saved)}원</span>}
      </div>
      {diff !== 0 && (
        <div className="text-xs text-red-600">
          ⚠️ 물건값 합계와 청구액이 {num(Math.abs(diff))}원 다릅니다 — 입금완료 뒤 품목이 바뀌었거나 청구서가 다시 계산되지 않았습니다.
          고치는 일은 아직 기존 화면(정산·청구)에서 합니다.
        </div>
      )}
    </div>
  );
}

function Kpi({ label, value, sub, warn, ok }: { label: string; value: number; sub?: string; warn?: boolean; ok?: boolean }) {
  return (
    <div className={clsx('bg-white rounded-xl p-3 border-l-4 shadow-sm',
      warn ? 'border-yellow-400' : ok ? 'border-green-500' : 'border-primary')}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-bold">{num(value)}원</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  );
}

export function Btn({ children, onClick, disabled, primary }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; primary?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled}
      className={clsx('px-3 py-2 rounded-lg text-sm font-medium border disabled:opacity-40 disabled:cursor-not-allowed',
        primary ? 'bg-primary text-white border-primary' : 'bg-white text-gray-800 hover:bg-gray-50')}>
      {children}
    </button>
  );
}
