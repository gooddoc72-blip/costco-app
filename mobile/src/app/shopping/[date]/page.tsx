'use client';
/**
 * 🛒 코스트코 장보기 목록 — 한 날짜 주문을 집계해 구매 목록 + 카톡 발송.
 * 페이지는 얇은 orchestrator.
 */
import { useParams } from 'next/navigation';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import ShoppingSummary from '@/components/shopping/ShoppingSummary';
import ShoppingTable from '@/components/shopping/ShoppingTable';
import { useShopping } from '@/hooks/useShopping';

export default function ShoppingPage() {
  const params = useParams<{ date: string }>();
  const date = decodeURIComponent(params.date);
  const s = useShopping(date);

  return (
    <>
      <Header title={`🛒 장보기 ${date}`} subtitle={s.data ? `${s.data.items.length}종` : '로딩...'} />
      <main className="px-4 pt-4 pb-32 space-y-3">
        {s.error && (
          <div className="bg-red-50 text-red-700 text-sm p-3 rounded-lg">❌ {s.error}</div>
        )}
        {s.data && (
          <>
            <ShoppingSummary
              data={s.data}
              sending={s.sending}
              sendResult={s.sendResult}
              onSend={s.onSend}
              saving={s.saving}
              saveResult={s.saveResult}
              onSave={s.onSave}
              submitting={s.submitting}
              submitResult={s.submitResult}
              onSubmit={s.onSubmit}
            />
            <ShoppingTable data={s.data} />
          </>
        )}
        {s.loading && !s.data && (
          <div className="text-center text-gray-400 py-16">불러오는 중...</div>
        )}
      </main>
      <BottomNav />
    </>
  );
}
