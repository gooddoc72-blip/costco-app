'use client';
/**
 * 📦 제품 DB — 검색 / 인라인 가격 수정 / 삭제.
 * 페이지는 얇은 orchestrator.
 */
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import ProductSearch from '@/components/products/ProductSearch';
import ProductCard from '@/components/products/ProductCard';
import { useProducts } from '@/hooks/useProducts';

export default function ProductsPage() {
  const p = useProducts();
  return (
    <>
      <Header title="📦 제품 DB" subtitle="박스 단가 · 분할수량 · 네이버 상품번호" />
      <main className="px-4 pt-4 pb-32 space-y-3">
        <ProductSearch initial={p.search} loading={p.loading} onSubmit={p.onSearch} />

        {p.error && (
          <div className="bg-red-50 text-red-700 text-sm p-3 rounded-lg">❌ {p.error}</div>
        )}

        <div className="text-xs text-gray-500 px-1">
          {p.loading ? '불러오는 중…' : `${p.rows.length}건`}
        </div>

        <div className="space-y-2">
          {p.rows.map(row => (
            <ProductCard
              key={row.id}
              row={row}
              saving={p.saving === row.id}
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
