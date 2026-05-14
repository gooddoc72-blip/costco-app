'use client';
/**
 * 📦 제품 DB — 검색 / 인라인 가격 수정 / 삭제.
 * 같은 코스트코 번호의 행이 2개 이상이면 그룹 배지로 시각 표시 (방식 A).
 * 페이지는 얇은 orchestrator.
 */
import { useMemo } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import ProductSearch from '@/components/products/ProductSearch';
import ProductCard from '@/components/products/ProductCard';
import { useProducts } from '@/hooks/useProducts';

export default function ProductsPage() {
  const p = useProducts();

  // 같은 코스트코 번호 그룹 카운트 — 2개 이상이면 시각 강조
  const groupCount = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of p.rows) {
      if (!r.productNo) continue;
      m.set(r.productNo, (m.get(r.productNo) || 0) + 1);
    }
    return m;
  }, [p.rows]);
  const groupedSet = useMemo(
    () => new Set(Array.from(groupCount.entries()).filter(([, n]) => n > 1).map(([k]) => k)),
    [groupCount],
  );

  return (
    <>
      <Header title="📦 제품 DB" subtitle="박스 단가 · 분할수량 · 네이버 상품번호" />
      <main className="px-4 pt-4 pb-32 space-y-3">
        <ProductSearch initial={p.search} loading={p.loading} onSubmit={p.onSearch} />

        {p.error && (
          <div className="bg-red-50 text-red-700 text-sm p-3 rounded-lg">❌ {p.error}</div>
        )}

        <div className="flex items-center justify-between text-xs text-gray-500 px-1">
          <span>{p.loading ? '불러오는 중…' : `${p.rows.length}건`}</span>
          {groupedSet.size > 0 && (
            <span className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
              🔗 같은 코스트코 번호 {groupedSet.size}개 그룹
            </span>
          )}
        </div>

        <div className="space-y-2">
          {p.rows.map(row => (
            <ProductCard
              key={row.id}
              row={row}
              saving={p.saving === row.id}
              groupSize={row.productNo ? (groupCount.get(row.productNo) || 1) : 1}
              onUpdate={p.onUpdate}
              onDelete={p.onDelete}
            />
          ))}
        </div>

        {!p.loading && p.rows.length === 0 && (
          <div className="text-center text-sm text-gray-400 py-8">
            검색 결과가 없습니다.
          </div>
        )}
      </main>
      <BottomNav />
    </>
  );
}
