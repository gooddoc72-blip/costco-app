'use client';

import { useEffect, useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import KpiCard from '@/components/KpiCard';
import { apiGet } from '@/lib/api';
import { won, num } from '@/lib/fmt';

interface Bucket {
  cnt: number;
  qty: number;
  sales: number;
  profit: number;
}

interface Kpi {
  today: Bucket;
  week: Bucket;
  month: Bucket;
  last_week: Bucket;
  last_month: Bucket;
}

interface Me {
  username: string;
  display_name: string;
}

export default function DashboardPage() {
  const [kpi, setKpi] = useState<Kpi | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([apiGet<Me>('/api/auth/me'), apiGet<Kpi>('/api/dashboard/kpi')])
      .then(([m, k]) => {
        setMe(m);
        setKpi(k);
      })
      .catch((e) => setError(e?.message || '데이터 로드 실패'));
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

        {!kpi && !error && (
          <div className="text-center text-gray-400 py-16">불러오는 중...</div>
        )}

        {kpi && (
          <>
            <section>
              <h2 className="text-sm font-semibold text-gray-700 mb-2">오늘</h2>
              <div className="grid grid-cols-2 gap-3">
                <KpiCard label="매출" value={kpi.today.sales} unit="won" />
                <KpiCard label="수익" value={kpi.today.profit} unit="won" accent="green" />
                <KpiCard label="주문" value={kpi.today.cnt} unit="count" accent="blue" />
                <KpiCard label="수량" value={kpi.today.qty} unit="count" accent="blue" />
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-gray-700 mb-2 mt-4">이번 주</h2>
              <div className="grid grid-cols-2 gap-3">
                <KpiCard
                  label="매출"
                  value={kpi.week.sales}
                  prev={kpi.last_week.sales}
                  unit="won"
                />
                <KpiCard
                  label="수익"
                  value={kpi.week.profit}
                  prev={kpi.last_week.profit}
                  unit="won"
                  accent="green"
                />
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-gray-700 mb-2 mt-4">이번 달</h2>
              <div className="grid grid-cols-2 gap-3">
                <KpiCard
                  label="매출"
                  value={kpi.month.sales}
                  prev={kpi.last_month.sales}
                  unit="won"
                />
                <KpiCard
                  label="수익"
                  value={kpi.month.profit}
                  prev={kpi.last_month.profit}
                  unit="won"
                  accent="green"
                />
                <KpiCard label="주문" value={kpi.month.cnt} unit="count" accent="blue" />
                <KpiCard label="수량" value={kpi.month.qty} unit="count" accent="blue" />
              </div>
            </section>
          </>
        )}
      </main>
      <BottomNav />
    </>
  );
}
