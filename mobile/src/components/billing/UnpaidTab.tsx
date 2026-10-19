'use client';
/** 🔴 미입금자 — 청구했는데 아직 안 들어온 돈(청구 전은 미입금이 아니다). */
import { useEffect, useState } from 'react';
import clsx from 'clsx';
import { billing, UnpaidRow } from '@/lib/client/billing';
import { num } from '@/lib/fmt';

export default function UnpaidTab({ onGoDay, onGoUser }: { onGoDay: (d: string) => void; onGoUser?: (u: string) => void }) {
  const [rows, setRows] = useState<UnpaidRow[]>([]);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState('');
  const [pick, setPick] = useState('');
  const [detail, setDetail] = useState<{ date: string; amount: number; items: number; billed_at: string }[]>([]);

  useEffect(() => {
    billing.unpaid().then(r => { setRows(r.rows); setTotal(r.total); }).catch(e => setErr(e.message || String(e)));
  }, []);
  useEffect(() => {
    if (!pick) { setDetail([]); return; }
    billing.unpaidDetail(pick).then(r => setDetail(r.rows)).catch(e => setErr(e.message || String(e)));
  }, [pick]);

  return (
    <div className="space-y-4">
      {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}
      <div className="bg-white rounded-xl p-3 border-l-4 border-red-500 shadow-sm">
        <div className="text-xs text-gray-500">미입금 합계</div>
        <div className="text-lg font-bold text-red-600">{num(total)}원</div>
        <div className="text-[11px] text-gray-500">{rows.length}명</div>
      </div>
      {rows.length === 0 ? <div className="bg-white rounded-xl border p-6 text-center text-sm text-gray-500">미입금 청구가 없습니다.</div> : (
        <div className="bg-white rounded-xl border overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-600 text-xs">
              <tr><th className="p-2 text-left">판매자</th><th className="p-2 text-right">미입금 일수</th>
                <th className="p-2 text-right">금액</th><th className="p-2 text-left">가장 오래된</th><th className="p-2 text-left">가장 최근</th></tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.username} onClick={() => setPick(p => p === r.username ? '' : r.username)}
                  className={clsx('border-t cursor-pointer hover:bg-gray-50', pick === r.username && 'bg-blue-50')}>
                  <td className="p-2 font-medium">{r.name}</td><td className="p-2 text-right">{r.days}일</td>
                  <td className="p-2 text-right font-semibold">{num(r.amount)}</td>
                  <td className="p-2">{r.oldest}</td><td className="p-2">{r.newest}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pick && detail.length > 0 && (
        <div className="bg-white rounded-xl border p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="font-semibold">{rows.find(r => r.username === pick)?.name} 미입금 날짜</div>
            {onGoUser && <button className="text-sm text-blue-600 underline" onClick={() => onGoUser(pick)}>👤 이 사람 여러 날치 한 번에 입금 처리</button>}
          </div>
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-600"><tr><th className="p-1.5 text-left">정산일</th><th className="p-1.5 text-right">품목</th><th className="p-1.5 text-right">금액</th><th className="p-1.5 text-left">청구 일시</th><th></th></tr></thead>
            <tbody>
              {detail.map(d => (
                <tr key={d.date} className="border-t">
                  <td className="p-1.5">{d.date}</td><td className="p-1.5 text-right">{d.items}</td>
                  <td className="p-1.5 text-right">{num(d.amount)}</td><td className="p-1.5">{d.billed_at}</td>
                  <td className="p-1.5 text-right"><button className="text-blue-600 underline" onClick={() => onGoDay(d.date)}>그 날짜로 가서 입금 처리</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
