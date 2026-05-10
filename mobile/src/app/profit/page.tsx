'use client';

import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { Receipt, ExternalLink } from 'lucide-react';

export default function ProfitPage() {
  return (
    <>
      <Header title="수익 계산" subtitle="영수증 PDF 매칭" />
      <main className="px-4 pt-4 pb-20">
        <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-100 text-center">
          <Receipt size={40} className="mx-auto text-primary mb-3" />
          <h2 className="font-bold text-gray-900 mb-2">영수증 매칭은 준비 중입니다</h2>
          <p className="text-xs text-gray-500 mb-4">
            PDF 업로드 + OCR이 모바일에서 무거워 PC에서 진행해 주세요.
            모바일에는 결과 조회만 곧 추가될 예정입니다.
          </p>
          <a
            href="https://costcobiz.shop"
            className="inline-flex items-center gap-1 text-sm text-primary font-medium"
          >
            PC 버전 열기 <ExternalLink size={14} />
          </a>
        </div>
      </main>
      <BottomNav />
    </>
  );
}
