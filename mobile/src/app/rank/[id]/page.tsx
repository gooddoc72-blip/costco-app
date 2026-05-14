'use client';
/**
 * 📈 키워드 순위 상세 — 한 달 일별 표 + 1년 추이.
 */
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import MonthCalendar from '@/components/rank/MonthCalendar';
import YearChart from '@/components/rank/YearChart';
import {
  fetchMonthly, fetchYearly,
  type MonthlyData, type HistoryPoint,
} from '@/lib/client/rank';

export default function RankDetailPage() {
  const params = useParams<{ id: string }>();
  const id = parseInt(params.id, 10);
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);

  const [monthly, setMonthly] = useState<MonthlyData | null>(null);
  const [yearly, setYearly] = useState<HistoryPoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setLoading(true); setError(null);
    Promise.all([fetchMonthly(id, year, month), fetchYearly(id)])
      .then(([m, y]) => { if (alive) { setMonthly(m); setYearly(y.history); } })
      .catch(e => { if (alive) setError(e?.message || '로드 실패'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [id, year, month]);

  const shiftMonth = (delta: number) => {
    let y = year, m = month + delta;
    if (m < 1) { m = 12; y -= 1; }
    else if (m > 12) { m = 1; y += 1; }
    setYear(y); setMonth(m);
  };

  return (
    <>
      <Header
        title={monthly?.tracking?.productKeyword || '순위 상세'}
        subtitle={monthly?.tracking ? `🔍 ${monthly.tracking.searchKeyword}` : ''}
      />
      <main className="px-4 pt-4 pb-32 space-y-3">
        {error && <div className="bg-red-50 text-red-700 text-sm p-3 rounded-lg">❌ {error}</div>}
        {loading && <div className="text-center text-gray-400 py-4">불러오는 중…</div>}

        {monthly && (
          <>
            <div className="flex items-center justify-between bg-white rounded-xl p-2 border">
              <button onClick={() => shiftMonth(-1)} className="px-3 py-1 text-sm bg-gray-100 rounded">◀</button>
              <span className="text-sm font-medium">{year}년 {month}월</span>
              <button onClick={() => shiftMonth(1)} className="px-3 py-1 text-sm bg-gray-100 rounded">▶</button>
            </div>
            <MonthCalendar year={year} month={month} days={monthly.days} />
          </>
        )}

        <YearChart points={yearly} />
      </main>
      <BottomNav />
    </>
  );
}
