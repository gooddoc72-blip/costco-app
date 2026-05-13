'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { Calendar, ArrowRight } from 'lucide-react';

function yesterdayStr(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

export default function ProfitPage() {
  const router = useRouter();
  const [date, setDate] = useState(yesterdayStr());

  const onGo = () => {
    if (date) router.push(`/profit/${date}`);
  };

  return (
    <>
      <Header title="수익 계산" subtitle="발송한 주문 기준" />
      <main className="px-4 pt-4 pb-20">
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <div className="flex items-center gap-2 mb-3 text-gray-700">
            <Calendar size={18} />
            <span className="font-medium">계산할 발송일 선택</span>
          </div>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg text-base"
          />
          <button
            onClick={onGo}
            disabled={!date}
            className="mt-3 w-full bg-blue-600 text-white font-medium py-2.5 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300"
          >
            정산표 보기 <ArrowRight size={16} />
          </button>
          <div className="mt-4 text-xs text-gray-500 space-y-1">
            <p>📌 그 날짜에 일괄발송 처리한 주문만 표시됩니다.</p>
            <p>📌 가격 수정은 네이버 상품번호 기준으로 개별 저장됩니다.</p>
            <p>📌 같은 코스트코 상품번호라도 네이버별 가격이 독립적으로 관리됩니다.</p>
          </div>
        </div>
      </main>
      <BottomNav />
    </>
  );
}
