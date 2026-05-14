'use client';
/**
 * 일일 주문 수집 — 네이버/쿠팡 API → order_history 저장.
 * 페이지는 얇은 orchestrator.
 */
import { useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import CollectControls from '@/components/orders/CollectControls';
import CollectResults from '@/components/orders/CollectResults';
import { useOrdersCollect } from '@/hooks/useOrdersCollect';
import type { Platform } from '@/lib/services/orders';

export default function OrdersCollectPage() {
  const [platform, setPlatform] = useState<Platform>('naver');
  const [hoursBack, setHoursBack] = useState(48);
  const [daysBack, setDaysBack] = useState(7);
  const [statusFilter, setStatusFilter] = useState('ALL');
  const { result, loading, error, collect } = useOrdersCollect();

  const handleCollect = () => {
    if (platform === 'coupang') {
      const filter = statusFilter === 'ALL' ? ['ACCEPT', 'INSTRUCT'] : [statusFilter];
      collect({ platform, daysBack, statusFilter: filter });
    } else {
      const filter = statusFilter === 'ALL' ? ['PAYED', 'READY'] : [statusFilter];
      collect({ platform, hoursBack, statusFilter: filter });
    }
  };

  const handlePlatformChange = (p: Platform) => {
    setPlatform(p);
    setStatusFilter('ALL');
  };

  return (
    <>
      <Header title="📋 일일 주문 수집" subtitle="네이버 / 쿠팡 API → order_history" />
      <main className="px-4 pt-4 pb-32 space-y-4">
        <CollectControls
          platform={platform}
          hoursBack={hoursBack}
          daysBack={daysBack}
          statusFilter={statusFilter}
          loading={loading}
          onPlatformChange={handlePlatformChange}
          onHoursChange={setHoursBack}
          onDaysChange={setDaysBack}
          onStatusChange={setStatusFilter}
          onCollect={handleCollect}
        />

        {error && (
          <div className="bg-red-50 text-red-700 text-sm p-3 rounded-lg whitespace-pre-wrap">{error}</div>
        )}

        {result && <CollectResults result={result} />}

        <div className="bg-blue-50 text-blue-700 text-xs p-3 rounded-lg">
          📌 가져온 주문은 order_history에 저장됩니다 (UNIQUE 주문번호).
          이후 송장번호 페이지에서 발송처리하면 dispatch_log에 자동 기록되어 수익계산 대상이 됩니다.
        </div>
      </main>
      <BottomNav />
    </>
  );
}
