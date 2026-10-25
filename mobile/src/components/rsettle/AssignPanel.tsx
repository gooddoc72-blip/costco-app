'use client';
/**
 * ⚠️ 배정할 영수증 품목 — 주문을 못 찾은 품목 + 주문에 붙고 남은 수량(팩 단위).
 * 받을 사람을 고른 뒤 그 사람에게 청구 / 그 사람 재고로 입고 / 반품요청(사람 안 고름).
 * 남은 수량 계산(이미 입고·반품한 양 빼기)은 파이썬이 한다.
 */
import { useState } from 'react';
import clsx from 'clsx';
import { rsettle, RsView } from '@/lib/client/rsettle';
import { num } from '@/lib/fmt';

interface P { on: boolean; packs: number; memo: string }

export default function AssignPanel({ v, day, busy, setBusy, onDone, onErr }: {
  v: RsView; day: string; busy: boolean; setBusy: (b: boolean) => void;
  onDone: (nv: RsView, msg: string) => void; onErr: (m: string) => void;
}) {
  const rows = v.assign?.rows || [];
  const [sel, setSel] = useState<Record<string, P>>({});
  const [user, setUser] = useState('');
  const [billStock, setBillStock] = useState(true);
  const users = Object.entries(v.names).sort((a, b) => a[1].localeCompare(b[1]));
  const picked = rows.filter(r => sel[r.costco_no]?.on);
  const amt = picked.reduce((s, r) => s + r.price * (sel[r.costco_no]?.packs || 0), 0);
  const uname = v.names[user] || user;

  const go = async (mode: 'bill' | 'stock' | 'return') => {
    const t = mode === 'bill' ? `${picked.length}종 ${num(amt)}원을 ${uname}에게 청구합니다.`
      : mode === 'stock' ? `${picked.length}종을 ${uname} 재고로 입고합니다.` + (billStock ? `\n구매비용 ${num(amt)}원을 지금 ${uname}에게 청구합니다.` : '\n청구 없이 넣고, 나갈 때 청구합니다.')
      : `${picked.length}종 ${num(amt)}원을 반품요청으로 뺍니다 — 청구·재고 어디에도 들어가지 않습니다.`;
    if (!window.confirm(t)) return;
    setBusy(true);
    try {
      const r = await rsettle.assign(day, mode, user, picked.map(p => ({ costco_no: p.costco_no, packs: sel[p.costco_no].packs, memo: sel[p.costco_no].memo })), billStock);
      setSel({});
      const x = r.result;
      onDone(r, mode === 'bill' ? `🧑‍💼 ${x.count}종 ${num(x.amount)}원을 ${uname}에게 청구 추가 — 정산 요청을 눌러 저장하세요.`
        : mode === 'stock' ? `📦 ${uname} 재고로 ${x.count}종 입고` + (x.amount ? ` · 💳 구매비용 ${num(x.amount)}원 청구` : '')
          + (x.skipped ? ` · 이미 입고돼 건너뜀 ${x.skipped}종` : '')
          + (x.dupe?.length ? ` · ⚠️ 오늘 이미 이 사람 재고로 들어간 것: ${x.dupe.map(d => `${d.name} ${d.have}개`).join(', ')} — 이번 것까지 더해집니다` : '')
          + (x.failed?.length ? ` · ❌ ${x.failed.join(' / ')}` : '')
          : `↩️ ${x.count}종 ${num(x.amount)}원을 반품요청으로 뺐습니다.`);
    } catch (e: any) { onErr('배정 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };

  if (v.assign?.error) return <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">🚫 {v.assign.error}</div>;
  if (!rows.length) return null;
  return (
    <section className="bg-white rounded-xl shadow-sm overflow-x-auto border-2 border-yellow-200">
      <div className="px-3 py-2 border-b space-y-1">
        <div className="font-bold text-sm">⚠️ 배정할 영수증 품목 {rows.length}건 — 주문 못 찾음 · 팔고 남음</div>
        <div className="text-xs text-gray-600">받을 사람을 고르고 체크한 품목을 <b>청구</b>(교환·추가 발송분)하거나 <b>재고로 입고</b>(안 팔려서 남음)하세요.
          잘못 산 물건은 <b>↩️ 반품요청</b>(사람 안 고름). 여러 사람이 나눠 가지면 한 명씩 나눠 배정하세요 — 남은 수량이 줄어든 채 목록에 남습니다.</div>
        <div className="text-xs text-gray-500">💡 한 주문에 여러 팩이 나간 경우(예: 김 2팩을 한 건으로 발송)는 여기서 고치지 말고 배치 상세에서 그 주문의 수량·금액을 고치세요.</div>
      </div>
      <table className="w-full text-xs">
        <thead className="bg-gray-50 text-gray-500"><tr>
          <th className="p-2">배정</th><th className="p-2 text-left">상품</th><th className="p-2 text-left">구분</th><th className="p-2 text-left">장보기 요청자</th>
          <th className="p-2 text-right">정가</th><th className="p-2 text-right">팩단가</th><th className="p-2 text-right">영수증</th>
          <th className="p-2 text-right">남은 팩</th><th className="p-2 text-right">배정 팩</th><th className="p-2 text-left">메모</th>
        </tr></thead>
        <tbody>{rows.map(r => {
          const p = sel[r.costco_no] || { on: false, packs: r.packs_left, memo: '' };
          const put = (x: Partial<P>) => setSel(m => ({ ...m, [r.costco_no]: { ...p, ...x } }));
          return (
            <tr key={r.costco_no} className={clsx('border-t', p.on && 'bg-blue-50')}>
              <td className="p-2 text-center"><input type="checkbox" checked={p.on} onChange={e => put({ on: e.target.checked })} /></td>
              <td className="p-2 min-w-[160px]">{r.name}<div className="text-gray-400 font-mono">{r.costco_no}{r.split_qty > 1 ? ` · 소분÷${r.split_qty}` : ''}</div></td>
              <td className="p-2">{r.kind}</td>
              <td className="p-2">{r.requester_name && <button onClick={() => setUser(r.requester)} className="underline text-blue-700">{r.requester_name}</button>}</td>
              <td className="p-2 text-right">{r.list_price && r.list_price !== r.price ? num(r.list_price) : ''}</td>
              <td className="p-2 text-right">{num(r.price)}</td>
              <td className="p-2 text-right">{r.receipt_qty}</td>
              <td className="p-2 text-right font-semibold">{r.packs_left}</td>
              <td className="p-1 text-right"><input type="number" min={1} max={r.packs_left} value={p.packs} disabled={!p.on}
                onChange={e => put({ packs: Math.min(r.packs_left, Math.max(1, Number(e.target.value))) })}
                className="w-14 border rounded px-1 py-1 text-right disabled:bg-gray-50" /></td>
              <td className="p-1"><input value={p.memo} disabled={!p.on} onChange={e => put({ memo: e.target.value })} placeholder="사유"
                className="w-36 border rounded px-1 py-1 disabled:bg-gray-50" /></td>
            </tr>);
        })}</tbody>
      </table>
      <div className="p-3 border-t space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>받을 사용자</span>
          <select value={user} onChange={e => setUser(e.target.value)} className="border rounded px-2 py-1 bg-white">
            <option value="">— 고르세요 —</option>
            {users.map(([u, n]) => <option key={u} value={u}>{n} ({u})</option>)}
          </select>
          {!!picked.length && <span className="text-gray-600">체크 {picked.length}종 · {num(amt)}원</span>}
        </div>
        <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={billStock} onChange={e => setBillStock(e.target.checked)} />
          💳 재고로 입고할 때 구매비용 지금 청구 (관리자가 대신 사서 보관 — 나중에 본인 주문으로 나갈 때는 다시 청구 안 함)</label>
        <div className="grid grid-cols-3 gap-2">
          <button onClick={() => go('bill')} disabled={busy || !picked.length || !user} className="py-2 rounded-lg bg-primary text-white font-bold text-sm disabled:opacity-40">🧑‍💼 {uname || '사용자'}에게 청구</button>
          <button onClick={() => go('stock')} disabled={busy || !picked.length || !user} className="py-2 rounded-lg border-2 border-primary text-primary font-bold text-sm disabled:opacity-40">📦 {uname || '사용자'} 재고로 입고</button>
          <button onClick={() => go('return')} disabled={busy || !picked.length} className="py-2 rounded-lg border font-bold text-sm disabled:opacity-40">↩️ 반품요청</button>
        </div>
      </div>
    </section>
  );
}
