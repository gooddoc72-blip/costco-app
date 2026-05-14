'use client';
/**
 * 대시보드 — KPI + 운영 알림 + 7일 트렌드.
 * 페이지는 얇은 orchestrator.
 */
import { useEffect, useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import KpiCard from '@/components/KpiCard';
import AlertCards from '@/components/dashboard/AlertCards';
import TrendBars from '@/components/dashboard/TrendBars';
import { apiGet } from '@/lib/api';
import { useDashboard } from '@/hooks/useDashboard';

interface Me { username: string; display_name: string }

export default function DashboardPage() {
  const { data, loading, error } = useDashboard();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    apiGet<Me>('/api/auth/me').then(setMe).catch(() => {});
  }, []);

  return (
    <>
      <Header
        title="대시보드"
        subtitle={me ? `${me.display_name} (${me.username})` : '로딩 중...'}
      />
      <main className="px-4 pt-4 pb-20 space-y-4">
        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
        {loading && !data && (
          <div className="text-center text-gray-400 py-16">불러오는 중...</div>
        )}

        {data && (
          <>
            <AlertCards alerts={data.alerts} />

            <section>
              <h2 className="text-sm font-semibold text-gray-700 mb-2">오늘</h2>
              <div className="grid grid-cols-2 gap-3">
                <KpiCard label="매출" value={data.today.sales} unit="won" />
                <KpiCard label="수익" value={data.today.profit} unit="won" accent="green" />
                <KpiCard label="주문" value={data.today.cnt} unit="count" accent="blue" />
                <KpiCard label="수량" value={data.today.qty} unit="count" accent="blue" />
              </div>
            </section>

            <TrendBars points={data.trend7} />

            <section>
              <h2 className="text-sm font-semibold text-gray-700 mb-2 mt-4">이번 주</h2>
              <div className="grid grid-cols-2 gap-3">
                <KpiCard label="매출" value={data.week.sales} prev={data.lastWeek.sales} unit="won" />
                <KpiCard label="수익" value={data.week.profit} prev={data.lastWeek.profit} unit="won" accent="green" />
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-gray-700 mb-2 mt-4">이번 달</h2>
              <div className="grid grid-cols-2 gap-3">
                <KpiCard label="매출" value={data.month.sales} prev={data.lastMonth.sales} unit="won" />
                <KpiCard label="수익" value={data.month.profit} prev={data.lastMonth.profit} unit="won" accent="green" />
                <KpiCard label="주문" value={data.month.cnt} unit="count" accent="blue" />
                <KpiCard label="수량" value={data.month.qty} unit="count" accent="blue" />
              </div>
            </section>
          </>
        )}
      </main>
      <BottomNav />
    </>
  );
}
