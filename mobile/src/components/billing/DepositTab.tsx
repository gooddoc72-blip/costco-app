'use client';
/**
 * 💳 예치금 — 잔액 현황 · 판매자별 내역(그때그때 잔액) · 예치 등록.
 * 잔액은 파이썬 원장 합계에서만 나온다(여기서 계산하지 않는다).
 */
import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import { billing, DepHistResp, DepResp } from '@/lib/client/billing';
import { num } from '@/lib/fmt';
import { Btn } from './DayTab';

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function daysAgo(n: number): string {
  const d = new Date(); d.setDate(d.getDate() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const PERIODS: Record<string, () => [string, string]> = {
  '전체 기간': () => ['', ''],
  '최근 30일': () => [daysAgo(29), today()],
  '최근 90일': () => [daysAgo(89), today()],
  '이번 달': () => [today().slice(0, 8) + '01', today()],
};

export default function DepositTab() {
  const [data, setData] = useState<DepResp | null>(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [onlyUsed, setOnlyUsed] = useState(true);
  const [pick, setPick] = useState('');
  const [period, setPeriod] = useState('전체 기간');
  const [hist, setHist] = useState<DepHistResp | null>(null);
  const [form, setForm] = useState({ username: '', amount: 0, tx_date: today(), memo: '' });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setErr(''); setData(await billing.deposits()); } catch (e: any) { setErr(e.message || String(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const loadHist = useCallback(async (u: string, p: string) => {
    if (!u) { setHist(null); return; }
    const [f, t] = PERIODS[p]();
    try { setHist(await billing.depositHistory(u, f, t)); } catch (e: any) { setErr(e.message || String(e)); }
  }, []);
  useEffect(() => { loadHist(pick, period); }, [pick, period, loadHist]);

  const rows = (data?.rows || []).filter(r => !onlyUsed || r.charged || r.spent || r.balance || r.adjusted || r.returned);
  const tt = data?.totals || {};
  const neg = (data?.rows || []).filter(r => r.balance < 0);

  const charge = async () => {
    if (!form.username || form.amount <= 0) return;
    const name = data?.rows.find(r => r.username === form.username)?.name || form.username;
    if (!window.confirm(`${name}에게 ${num(form.amount)}원 예치를 등록합니다 (입금일 ${form.tx_date}).`)) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await billing.charge(form.username, form.amount, form.tx_date, form.memo);
      setMsg(`✅ ${name} ${num(form.amount)}원 예치 — 잔액 ${num(r.balance)}원`);
      setForm(f => ({ ...f, amount: 0, memo: '' }));
      await load();
      if (pick === form.username) await loadHist(pick, period);
    } catch (e: any) { setErr(`예치 등록 실패: ${e.message || e}`); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}
      {msg && <div className="bg-green-50 text-green-800 p-3 rounded-lg text-sm">{msg}</div>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card label="예치 입금 총액" v={tt.charged || 0} />
        <Card label="구매 차감 누계" v={tt.spent || 0} />
        <Card label="예치금 총잔액" v={tt.net || 0} />
        <Card label="마이너스" v={neg.reduce((a, r) => a + r.balance, 0)} sub={`${neg.length}명`} red={neg.length > 0} />
      </div>
      {neg.length > 0 && (
        <div className="text-xs text-red-600">⚠️ 추가 예치가 필요한 판매자: {neg.map(r => `${r.name} ${num(-r.balance)}원`).join(' · ')}</div>
      )}

      <div className="bg-white rounded-xl border overflow-x-auto">
        <div className="flex items-center justify-between p-2 text-xs text-gray-500">
          <span>👆 판매자를 누르면 아래에 그 사람 예치금 내역이 펼쳐집니다.</span>
          <label className="flex items-center gap-1"><input type="checkbox" checked={onlyUsed} onChange={e => setOnlyUsed(e.target.checked)} /> 예치금 쓰는 사람만</label>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600 text-xs">
            <tr>
              <th className="p-2 text-left">판매자</th><th className="p-2 text-right">누적 입금</th>
              <th className="p-2 text-right">예치금 차감</th><th className="p-2 text-right hidden md:table-cell">조정</th>
              <th className="p-2 text-right hidden md:table-cell">반품 적립</th><th className="p-2 text-right">잔액</th>
              <th className="p-2 text-right hidden md:table-cell">청구 합계</th><th className="p-2 text-right hidden md:table-cell">예치금 외 결제</th>
              <th className="p-2 text-left hidden md:table-cell">최근 입금일</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.username} onClick={() => setPick(p => p === r.username ? '' : r.username)}
                className={clsx('border-t cursor-pointer hover:bg-gray-50', pick === r.username && 'bg-blue-50')}>
                <td className="p-2 font-medium whitespace-nowrap">{r.name}</td>
                <td className="p-2 text-right">{num(r.charged)}</td>
                <td className="p-2 text-right">{num(r.spent)}</td>
                <td className="p-2 text-right hidden md:table-cell">{num(r.adjusted)}</td>
                <td className="p-2 text-right hidden md:table-cell">{num(r.returned)}</td>
                <td className={clsx('p-2 text-right font-semibold', r.balance < 0 && 'text-red-600')}>{num(r.balance)}</td>
                <td className="p-2 text-right hidden md:table-cell">{num(r.billed)}</td>
                <td className="p-2 text-right hidden md:table-cell">{num(r.other_paid)}</td>
                <td className="p-2 hidden md:table-cell text-xs text-gray-500">{r.last_charge}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="p-2 text-[11px] text-gray-500">
          잔액 = 누적 입금 − 예치금 차감 (± 조정 + 반품 적립). 청구 합계에서 빼는 것이 아닙니다 — 계좌로 받았거나 아직 못 받은 청구는 예치금에서 빠지지 않습니다(예치금 외 결제).
        </div>
      </div>

      {pick && hist && (
        <div className="bg-white rounded-xl border p-3 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-semibold">💳 {hist.name} 예치금 내역 · 현재 잔액 <span className={clsx(hist.balance < 0 && 'text-red-600')}>{num(hist.balance)}원</span></div>
            <select value={period} onChange={e => setPeriod(e.target.value)} className="border rounded px-2 py-1 text-sm">
              {Object.keys(PERIODS).map(k => <option key={k}>{k}</option>)}
            </select>
          </div>
          <div className="text-xs text-gray-600">
            기간 예치 입금 {num(hist.period.charged)}원 · 구매 차감 {num(hist.period.spent)}원 · 적립·조정 {num(hist.period.adjusted)}원
          </div>
          {hist.rows.length === 0 ? <div className="text-xs text-gray-500">이 기간 움직임이 없습니다.</div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 text-gray-600">
                  <tr><th className="p-1.5 text-left">날짜</th><th className="p-1.5 text-left">구분</th>
                    <th className="p-1.5 text-right">금액</th><th className="p-1.5 text-right">잔액</th>
                    <th className="p-1.5 text-left">정산일</th><th className="p-1.5 text-left">메모</th><th className="p-1.5 text-left hidden md:table-cell">처리자</th></tr>
                </thead>
                <tbody>
                  {hist.rows.map(r => (
                    <tr key={r.id} className="border-t">
                      <td className="p-1.5 whitespace-nowrap">{r.date}</td>
                      <td className="p-1.5 whitespace-nowrap">{r.kind_label}</td>
                      <td className={clsx('p-1.5 text-right', r.amount < 0 ? 'text-red-600' : 'text-green-700')}>{num(r.amount)}</td>
                      <td className="p-1.5 text-right">{num(r.balance)}</td>
                      <td className="p-1.5">{r.settle_date}</td>
                      <td className="p-1.5">{r.memo}</td>
                      <td className="p-1.5 hidden md:table-cell">{r.by}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-[11px] text-gray-500 mt-1">최신순 · 잔액 = 그 시점까지 누적 · '차감 취소됨'은 '차감 되돌림'과 짝을 이뤄 상쇄됩니다.</div>
            </div>
          )}
        </div>
      )}

      <div className="bg-white rounded-xl border p-3 space-y-2">
        <div className="font-semibold">➕ 예치 등록</div>
        <div className="text-xs text-gray-500">판매자가 보낸 돈을 확인하고 올립니다. 입금일은 통장에 찍힌 날로 두세요.</div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
          <select value={form.username} onChange={e => setForm(f => ({ ...f, username: e.target.value }))} className="border rounded px-2 py-2 text-sm">
            <option value="">판매자 선택</option>
            {(data?.rows || []).slice().sort((a, b) => a.name.localeCompare(b.name, 'ko')).map(r => <option key={r.username} value={r.username}>{r.name}</option>)}
          </select>
          <input type="number" min={0} step={10000} placeholder="예치 금액" value={form.amount || ''}
            onChange={e => setForm(f => ({ ...f, amount: Math.max(0, Number(e.target.value) || 0) }))} className="border rounded px-2 py-2 text-sm" />
          <input type="date" value={form.tx_date} onChange={e => setForm(f => ({ ...f, tx_date: e.target.value }))} className="border rounded px-2 py-2 text-sm" />
          <input placeholder="메모 (은행·입금자명)" value={form.memo} onChange={e => setForm(f => ({ ...f, memo: e.target.value }))} className="border rounded px-2 py-2 text-sm" />
        </div>
        <Btn primary disabled={busy || !form.username || form.amount <= 0} onClick={charge}>➕ 예치 등록</Btn>
        <div className="text-[11px] text-gray-500">수동 조정·잘못 올린 예치 삭제는 아직 기존 화면(정산·청구 › 예치금)에서 합니다.</div>
      </div>
    </div>
  );
}

function Card({ label, v, sub, red }: { label: string; v: number; sub?: string; red?: boolean }) {
  return (
    <div className={clsx('bg-white rounded-xl p-3 border-l-4 shadow-sm', red ? 'border-red-500' : 'border-blue-500')}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className={clsx('text-lg font-bold', red && 'text-red-600')}>{num(v)}원</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  );
}
