'use client';
/**
 * 🖨 장보기 인쇄 페이지 — 매장에서 보기 좋게.
 * 로드 직후 자동 window.print() 호출.
 */
import { useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useShopping } from '@/hooks/useShopping';
import { won } from '@/lib/fmt';

export default function ShoppingPrintPage() {
  const params = useParams<{ date: string }>();
  const router = useRouter();
  const date = decodeURIComponent(params.date);
  const { data, loading, error } = useShopping(date);

  useEffect(() => {
    if (data && data.items.length > 0) {
      const t = setTimeout(() => window.print(), 300);
      return () => clearTimeout(t);
    }
  }, [data]);

  if (loading) return <p className="text-center text-gray-400 py-8">불러오는 중…</p>;
  if (error)   return <p className="text-red-600 text-sm">❌ {error}</p>;
  if (!data || data.items.length === 0)
    return <p className="text-center text-gray-400 py-8">주문이 없습니다.</p>;

  return (
    <div className="font-[맑은_고딕,sans-serif]">
      <div className="flex items-center justify-between mb-3 no-print">
        <button onClick={() => window.print()}
          className="bg-blue-600 text-white px-4 py-2 rounded text-sm">🖨 인쇄</button>
        <button onClick={() => router.back()}
          className="border px-4 py-2 rounded text-sm">닫기</button>
      </div>
      <h1 className="text-xl font-bold mb-1">🛒 코스트코 장보기 — {date}</h1>
      <p className="text-sm text-gray-600 mb-3">
        총 {data.items.length}종 · 예상 구매 총액 {won(data.totalExpected)}
      </p>
      <table className="w-full text-[13px] border-collapse">
        <thead>
          <tr className="bg-gray-100">
            <th className="border-b p-2 text-left">상품번호</th>
            <th className="border-b p-2 text-left">상품명</th>
            <th className="border-b p-2 text-left">옵션정보</th>
            <th className="border-b p-2 text-right">주문수량</th>
            <th className="border-b p-2 text-right">구매수량</th>
            <th className="border-b p-2 text-right">팩단가</th>
            <th className="border-b p-2 text-right">예상금액</th>
            <th className="border-b p-2 text-right">배송비</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((it, i) => (
            <tr key={i} className="border-b">
              <td className="p-2">{it.productNo}</td>
              <td className="p-2">{it.productName}</td>
              <td className="p-2">{it.optionInfo || '-'}</td>
              <td className="p-2 text-right">{it.qty}</td>
              <td className="p-2 text-right font-semibold">{it.costcoQty}</td>
              <td className="p-2 text-right">{it.unitPrice == null ? '-' : won(it.unitPrice)}</td>
              <td className="p-2 text-right font-semibold">
                {it.expectedCost == null ? '-' : won(it.expectedCost)}
              </td>
              <td className="p-2 text-right text-gray-600">{won(it.shippingFee)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-4 text-base font-bold">💰 예상 구매 총액: {won(data.totalExpected)}</p>
    </div>
  );
}
