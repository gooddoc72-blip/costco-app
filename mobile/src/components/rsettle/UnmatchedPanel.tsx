'use client';
/**
 * 📋 영수증에 없는 발송건 — 발송은 됐는데 그날 영수증에서 상품을 못 찾은 건을 무엇이었는지 정한다.
 * 그대로 두면 청구에서 빠진다. 규칙(보유자 추천·자동 금액·빌린 재고)은 파이썬이 계산한다.
 */
import { useMemo, useState } from 'react';
import clsx from 'clsx';
import { rsettle, RsView, UnmatchedDecision } from '@/lib/client/rsettle';
import { num } from '@/lib/fmt';

type How = '' | 'stock' | 'amount' | 'link' | 'skip';
interface D { how: How; owner: string; qty?: number; amount?: number; memo: string }
const k = (r: { username: string; order_no: string }) => `${r.username}\t${r.order_no}`;

export default function UnmatchedPanel({ v, day, busy, setBusy, onDone, onErr }: {
  v: RsView; day: string; busy: boolean; setBusy: (b: boolean) => void;
  onDone: (nv: RsView, msg: string) => void; onErr: (m: string) => void;
}) {
  const det = v.unmatched_detail;
  const orders = det?.orders || [];
  const owners = det?.owners || [];
  const [dec, setDec] = useState<Record<string, D>>({});
  const get = (o: { username: string; order_no: string; pre: string }): D =>
    dec[k(o)] || { how: (o.pre === 'stock' || o.pre === 'over') ? 'stock' : '', owner: '', memo: '' };
  const put = (o: { username: string; order_no: string; pre: string }, p: Partial<D>) =>
    setDec(m => ({ ...m, [k(o)]: { ...get(o), ...p } }));

  // 화면에서 미리 보는 금액 — 확정 금액은 저장할 때 파이썬이 다시 계산한다
  const preview = (o: (typeof orders)[number], d: D) => {
    if (d.amount) return d.amount;
    if (d.how === 'link') return o.candidate?.price ? o.candidate.price * (d.qty ?? o.qty) : 0;
    if (d.how !== 'stock') return 0;
    const autoSame = (!d.owner || d.owner === o.auto_owner) && (d.qty ?? o.qty) === o.qty;
    // 본인 재고인데 0원 = 이미 낸 재고(-2) — 청구 없이 재고만 차감된다
    if (autoSame) return o.auto_amount || (o.auto_owner && o.auto_owner === o.username ? -2 : 0);
    const ow = d.owner || o.auto_owner;
    return o.stock.some(s => s.owner === ow) ? -1 : 0;   // -1: 저장 때 계산 · 0: 장부에 없음 → 금액 필수
  };
  const picked = useMemo(() => orders.filter(o => get(o).how), [orders, dec]);  // eslint-disable-line react-hooks/exhaustive-deps
  const nCarry = orders.filter(o => o.carried_from).length;
  // 지난 날짜 발송건은 접어 둔다 — 오늘 예외가 그 밑에 묻히지 않게
  const [showCarry, setShowCarry] = useState(false);
  const shown = orders.filter(o => !o.carried_from || showCarry);
  const skipCarried = () => setDec(m => {
    const n = { ...m };
    for (const o of orders) if (o.carried_from && !m[k(o)]?.how) n[k(o)] = { how: 'skip', owner: '', memo: '' };
    return n;
  });

  const apply = async () => {
    const ds: UnmatchedDecision[] = picked.map(o => {
      const d = get(o);
      return { username: o.username, order_no: o.order_no, how: d.how as UnmatchedDecision['how'], owner: d.owner || undefined,
        qty: d.qty, amount: d.amount, memo: d.memo || undefined };
    });
    setBusy(true);
    try {
      const r = await rsettle.unmatched(day, ds);
      setDec({});
      const x = r.result;
      onDone(r, `✅ ${x.added}건 ${num(x.amount)}원을 청구에 넣었습니다`
        + (x.skipped ? ` · 🚫 ${x.skipped}건 청구 안 함으로 정리` : '')
        + (x.zero ? ` · ⚠️ 금액 0원 ${x.zero}건은 넣지 않음(금액을 적으세요)` : '')
        + (x.borrowed ? ` · 🤝 빌려 출고 ${x.borrowed}건 ${num(x.borrow_amount)}원 — 빌려준 사람 재고 차감·예치금 적립까지 끝남` : '')
        + (x.loans ? ` (장부에 없던 ${x.loans}건은 빌려준 사람 재고로 등록 후 출고)` : '')
        + (x.borrow_short.length ? ` · ⚠️ 재고 모자라 차감 못 한 것: ${x.borrow_short.map(s => `${s.name}(${s.owner} −${s.short})`).join(', ')}` : '')
        + (x.borrow_error ? ` · ❌ 빌려 출고 처리 실패: ${x.borrow_error}` : '')
        + (x.borrow_paid_blocked.length ? ` · ⛔ 판매자 청구서가 이미 입금완료라 빌려 출고 안 함: ${x.borrow_paid_blocked.join(', ')}` : '')
        + (x.no_ledger.length ? ` · ⚠️ 장부에 재고가 없어 차감 안 됨: ${x.no_ledger.join(', ')}` : ''));
    } catch (e: any) { onErr('처리 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };

  if (!orders.length) return null;
  return (
    <section className="bg-white rounded-xl shadow-sm overflow-x-auto border-2 border-orange-200">
      <div className="px-3 py-2 border-b">
        <div className="font-bold text-sm">📋 영수증에 없는 발송건 {orders.length - nCarry}건 — 무엇이었는지 고르세요</div>
        <div className="text-xs text-gray-600">발송은 됐는데 그날 영수증에서 상품을 못 찾은 건입니다. <b>그대로 두면 청구에서 빠집니다.</b></div>
        {!!nCarry && (
          <div className="text-xs mt-1 flex flex-wrap items-center gap-2 text-orange-700">
            📌 지난 14일 발송 중 <b>청구도 재고 기록도 없는 {nCarry}건</b> — 여기서 처리하면 오늘 정산으로 청구됩니다.
            <button onClick={() => setShowCarry(x => !x)} className="underline">{showCarry ? '접기' : '펼치기'}</button>
            {showCarry && <button onClick={skipCarried} className="underline text-gray-600">아직 안 고른 지난 건 모두 🚫 청구 안 함으로</button>}
          </div>)}
        {det?.error && <div className="text-xs text-red-600">❌ {det.error}</div>}
      </div>
      <table className="w-full text-xs">
        <thead className="bg-gray-50 text-gray-500"><tr>
          <th className="p-2 text-left">처리</th><th className="p-2 text-left">판매자</th><th className="p-2 text-left">주문 · 수취인</th>
          <th className="p-2 text-left">상품명</th><th className="p-2 text-right">수량</th><th className="p-2 text-left">재고 현황</th>
          <th className="p-2 text-left">재고 보유자</th><th className="p-2 text-right">자동 금액</th><th className="p-2 text-right">금액</th>
          <th className="p-2 text-left">메모</th>
        </tr></thead>
        <tbody>{shown.map(o => {
          const d = get(o);
          const pv = preview(o, d);
          return (
            <tr key={k(o)} className={clsx('border-t align-top', d.how && 'bg-blue-50')}>
              <td className="p-1">
                <select value={d.how} onChange={e => put(o, { how: e.target.value as How })} className="border rounded px-1 py-1 bg-white">
                  <option value="">— 미처리 —</option>
                  <option value="stock">📦 재고 출고</option>
                  <option value="amount">✍️ 금액 지정</option>
                  {o.candidate && <option value="link">🔗 후보로 연결</option>}
                  <option value="skip">🚫 청구 안 함</option>
                </select>
              </td>
              <td className="p-2 whitespace-nowrap">{o.name}{o.carried_from && <div className="text-orange-600">📌 {o.carried_from.slice(5)} 발송</div>}{o.pre === 'over' && <div className="text-orange-600">영수증보다 많이 붙어 자동으로 옮김</div>}</td>
              <td className="p-2"><span className="font-mono">{o.order_no}</span><div className="text-gray-500">{o.recipient}</div></td>
              <td className="p-2 min-w-[160px]">{o.product_name}
                {o.candidate && <div className="text-blue-700">🔗 후보: {o.candidate.name} {num(o.candidate.price)}원</div>}
                {!!o.receipt_price && <div className="text-gray-400">참고·영수증가 {num(o.receipt_price)}원</div>}</td>
              <td className="p-1 text-right"><input type="number" min={1} value={d.qty ?? o.qty}
                onChange={e => put(o, { qty: Number(e.target.value) === o.qty ? undefined : Math.max(1, Number(e.target.value)) })}
                className="w-12 border rounded px-1 py-1 text-right" /></td>
              <td className="p-2 whitespace-nowrap">{o.stock.length ? o.stock.map(s => `${s.name} ${s.qty}`).join(' · ') : <span className="text-gray-400">없음</span>}</td>
              <td className="p-1">
                {d.how === 'stock' && (
                  <select value={d.owner} onChange={e => put(o, { owner: e.target.value })} className="border rounded px-1 py-1 bg-white max-w-[180px]">
                    <option value="">(자동: {o.auto_owner ? (v.names[o.auto_owner] || o.auto_owner) : '없음'})</option>
                    {owners.map(w => <option key={w.username} value={w.username}>{w.name} ({w.username}){w.in_ledger ? '' : ' · 장부에 재고 없음'}</option>)}
                  </select>)}
              </td>
              <td className="p-2 text-right whitespace-nowrap">{d.how === 'stock'
                ? (pv === -1 ? <span className="text-gray-400">저장 때 계산</span> : pv === -2 ? <span className="text-gray-500">0 (본인 결제 재고)</span>
                  : pv ? num(pv) : <span className="text-orange-600">금액 필수</span>)
                : d.how === 'link' ? num(pv) : (o.auto_amount ? <span className="text-gray-400">{num(o.auto_amount)}</span> : '')}</td>
              <td className="p-1 text-right"><input type="number" min={0} step={100} value={d.amount ?? ''} placeholder={d.how === 'amount' ? '필수' : '0=자동'}
                onChange={e => put(o, { amount: e.target.value === '' ? undefined : Math.max(0, Number(e.target.value)) })}
                className={clsx('w-24 border rounded px-1 py-1 text-right', d.how === 'amount' && !d.amount && 'border-orange-400')} /></td>
              <td className="p-1"><input value={d.memo} onChange={e => put(o, { memo: e.target.value })} className="w-36 border rounded px-1 py-1" /></td>
            </tr>);
        })}</tbody>
      </table>
      <div className="p-3 space-y-2 border-t">
        <div className="text-[11px] text-gray-500">
          📦 재고 출고: 본인 재고면 아직 안 낸 몫만(이미 낸 재고는 0원), 남의 재고면 할인가로 산 재고는 할인가+1,000원·정상가 재고는 산 값 —
          <b>남의 재고는 처리하는 즉시</b> 보유자 재고에서 빠지고 판매자 청구서에 들어가고 보유자 예치금에 적립됩니다(본인 재고는 확정 때 차감).
          ‘장부에 재고 없음’인 사람을 고르면 <b>금액을 직접 적으세요</b> — 그 사람 재고로 등록한 뒤 바로 빌려 출고합니다. 매칭 수정에서 끊으면 모두 되돌아갑니다.
          ✍️ 금액 지정: 금액을 꼭 적습니다. 🚫 청구 안 함: 취소·무상 재발송 — 다시 뜨지 않습니다. 0원 청구는 넣지 않습니다.
        </div>
        <button onClick={apply} disabled={busy || !picked.length}
          className="w-full py-2 rounded-lg bg-primary text-white font-bold text-sm disabled:opacity-40">
          ✅ {picked.length}건 처리해서 청구에 넣기
        </button>
      </div>
    </section>
  );
}
