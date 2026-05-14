'use client';
/**
 * 👑 관리자 — DB 정리 도구 + 시스템 작업.
 * 페이지는 얇은 orchestrator.
 */
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { Wrench, Play } from 'lucide-react';
import { useAdminMigration } from '@/hooks/useAdminMigration';
import ShoppingSubmissions from '@/components/admin/ShoppingSubmissions';

const ACTIONS = [
  {
    key: 'fix-order-dates',
    title: '주문 날짜 정상화',
    desc: '옛 Streamlit 시절 시스템 강제 날짜로 저장된 주문을 실제 결제일로 복원. raw_json에서 payDate 추출.',
  },
  {
    key: 'backfill-matched-product',
    title: '주문 매칭 보강',
    desc: '옛 주문에 matched_product_id가 비어있는 경우 자동 매칭하여 채움. 상품명 변경에도 안 깨지는 영구 링크 생성.',
  },
  {
    key: 'all',
    title: '🚀 전체 실행',
    desc: '위 두 가지를 순서대로 모두 실행.',
  },
] as const;

export default function AdminPage() {
  const m = useAdminMigration();

  return (
    <>
      <Header title="👑 관리자" subtitle="DB 정리 · 시스템 작업" />
      <main className="px-4 pt-4 pb-32 space-y-4">

        <section className="bg-white rounded-xl p-4 shadow-sm border">
          <div className="flex items-center gap-2 mb-3 font-semibold text-gray-900">
            <Wrench size={18} /> DB 정리 마이그레이션
          </div>
          <p className="text-xs text-gray-500 mb-3">
            ⚠️ 한 번만 실행하면 됩니다. 안전한 작업이지만 백업 권장.
          </p>
          <div className="space-y-2">
            {ACTIONS.map(a => (
              <button key={a.key} onClick={() => m.run(a.key)} disabled={m.running}
                className="w-full text-left p-3 border rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed">
                <div className="flex items-center gap-2 font-medium text-sm">
                  <Play size={14} /> {a.title}
                </div>
                <p className="text-[11px] text-gray-500 mt-1">{a.desc}</p>
              </button>
            ))}
          </div>
        </section>

        {m.error && (
          <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {m.error}</div>
        )}

        {m.results && (
          <section className="bg-green-50 border border-green-200 rounded-xl p-4">
            <div className="font-semibold text-green-900 mb-2">✅ 실행 결과</div>
            <pre className="text-xs text-green-800 whitespace-pre-wrap overflow-x-auto">
              {JSON.stringify(m.results, null, 2)}
            </pre>
          </section>
        )}

        <ShoppingSubmissions />
      </main>
      <BottomNav />
    </>
  );
}
