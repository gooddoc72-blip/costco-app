'use client';
/**
 * 💳 정산 매칭 — CSV 업로드 + 발송건 vs 정산 매칭 + 배송비 수수료.
 * 페이지는 얇은 orchestrator.
 */
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import UploadCard from '@/components/settlement/UploadCard';
import DateBar from '@/components/settlement/DateBar';
import MatchSummary from '@/components/settlement/MatchSummary';
import ShippingPanel from '@/components/settlement/ShippingPanel';
import { useSettlement } from '@/hooks/useSettlement';

export default function SettlementPage() {
  const s = useSettlement();
  return (
    <>
      <Header title="💳 정산 매칭" subtitle="네이버 빠른정산 CSV ↔ 발송건" />
      <main className="px-4 pt-4 pb-32 space-y-3">
        <UploadCard upload={s.upload} loading={s.loading} onFile={s.onUpload} />
        <DateBar
          settleDate={s.settleDate}
          shipDate={s.shipDate}
          loading={s.loading}
          onSettleChange={s.setSettleDate}
          onShipChange={s.setShipDate}
          onLoad={s.onLoad}
          onClear={s.onClear}
        />

        {s.error && (
          <div className="bg-red-50 text-red-700 text-sm p-3 rounded-lg">❌ {s.error}</div>
        )}

        {s.data && (
          <>
            <MatchSummary data={s.data} />
            <ShippingPanel data={s.data} />
          </>
        )}

        {!s.data && !s.loading && (
          <div className="bg-blue-50 text-blue-800 text-xs p-3 rounded-lg">
            📌 ① CSV 업로드 → ② 정산일/발송일 선택 → ③ 조회. 매칭 결과를 확인하세요.
          </div>
        )}
      </main>
      <BottomNav />
    </>
  );
}
