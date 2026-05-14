'use client';
/**
 * 📈 키워드 순위 체크 페이지.
 */
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import AddKeywordForm from '@/components/rank/AddKeywordForm';
import RankList from '@/components/rank/RankList';
import { useRank } from '@/hooks/useRank';
import { RefreshCw } from 'lucide-react';

export default function RankPage() {
  const r = useRank();
  return (
    <>
      <Header title="📈 키워드 순위" subtitle="네이버 쇼핑 검색 노출 추적" />
      <main className="px-4 pt-4 pb-32 space-y-3">
        {r.error && <div className="bg-red-50 text-red-700 text-sm p-3 rounded-lg">❌ {r.error}</div>}

        {r.data && !r.data.hasApiKey && (
          <div className="bg-amber-50 text-amber-800 text-xs p-3 rounded-lg">
            ⚠️ 네이버 Open API 키 미등록 — 설정 페이지에서 등록해주세요. (커머스 API와 별개)
          </div>
        )}

        <AddKeywordForm onAdd={r.onAdd} />

        {r.data && r.data.rows.length > 0 && (
          <button onClick={r.onCheckAll} disabled={r.checkingAll || !r.data.hasApiKey}
            className="w-full bg-purple-600 text-white py-2 rounded-lg flex items-center justify-center gap-2 text-sm font-medium disabled:bg-gray-300">
            <RefreshCw size={14} className={r.checkingAll ? 'animate-spin' : ''} />
            {r.checkingAll ? `체크 중… (${r.data.rows.length}건)` : `🔄 전체 체크 (${r.data.rows.length}건)`}
          </button>
        )}

        {r.summary && (
          <div className={`text-xs p-2 rounded ${r.summary.errors.length > 0 ? 'bg-amber-50 text-amber-800' : 'bg-green-50 text-green-800'}`}>
            ✅ 체크 완료 — 매칭 {r.summary.matched}건 / 미발견 {r.summary.notFound}건
            {r.summary.errors.length > 0 && <div className="mt-1 text-[10px]">⚠️ {r.summary.errors.length}건 오류</div>}
          </div>
        )}

        {r.loading && !r.data && <div className="text-center text-gray-400 py-8">불러오는 중…</div>}

        {r.data && (
          <RankList
            rows={r.data.rows}
            busyId={r.busyId}
            onCheckOne={r.onCheckOne}
            onDelete={r.onDelete}
          />
        )}
      </main>
      <BottomNav />
    </>
  );
}
