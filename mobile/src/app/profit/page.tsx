'use client';
/**
 * 💰 수익 계산 — 일별 정산표 · 월별 분석/점검.
 *
 * 예전 /m 화면은 Next 서버가 TypeScript로 수익을 따로 계산해 Streamlit과 숫자가 달랐다
 * (영수증 단가·저장값·온라인몰 직배송 누락, 배송비 96%만 반영, 택배 기본값 1,800원).
 * 지금은 파이썬(/api/pc)이 Streamlit 수익계산과 같은 함수로 계산한다.
 * 네이버 판매가 수정 패널은 아직 기존 화면에 있다.
 */
import { useEffect, useState } from 'react';
import clsx from 'clsx';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import PcDayView from '@/components/profit/PcDayView';
import PcMonthView from '@/components/profit/PcMonthView';
import { pc } from '@/lib/client/pc';

type Tab = 'day' | 'month';

export default function ProfitPage() {
  const [me, setMe] = useState<{ username: string; is_admin: boolean } | null>(null);
  const [tab, setTab] = useState<Tab>('day');
  const [date, setDate] = useState('');
  const [dayKey, setDayKey] = useState(0);
  const [err, setErr] = useState('');

  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    setDate(p.get('date') || '');
    if (p.get('tab') === 'month') setTab('month');
    pc.me().then(setMe).catch(e => setErr(e.message || String(e)));
  }, []);

  const openDay = (d: string) => { setDate(d); setDayKey(k => k + 1); setTab('day'); window.scrollTo(0, 0); };

  return (
    <>
      <Header title="💰 수익 계산" subtitle="발송한 주문 기준 · 구입가·택배·박스·수입"
        right={<a href="/app/" className="text-xs text-gray-500 underline">네이버 가격수정(기존 화면)</a>} />
      <main className="px-3 md:px-6 pt-3 pb-28 space-y-3">
        <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit">
          {([['day', '📊 일별'], ['month', '📅 월별·점검']] as [Tab, string][]).map(([t, l]) => (
            <button key={t} onClick={() => setTab(t)}
              className={clsx('px-3 py-1.5 rounded-md text-sm', tab === t ? 'bg-white shadow font-bold' : 'text-gray-600')}>{l}</button>
          ))}
        </div>
        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}
        {me && tab === 'day' && <PcDayView key={dayKey} initialDate={date} onErr={setErr} />}
        {me && tab === 'month' && <PcMonthView admin={me.is_admin} me={me.username} onErr={setErr} openDay={openDay} />}
      </main>
      <BottomNav />
    </>
  );
}
