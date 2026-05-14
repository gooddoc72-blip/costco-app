/** 장보기 상품 카드 리스트 (분리=하늘, 묶음=노랑) */
import { won } from '@/lib/fmt';
import type { ShoppingPageData } from '@/lib/client/shopping';

export default function ShoppingTable({ data }: { data: ShoppingPageData }) {
  if (data.items.length === 0) {
    return <div className="text-center text-sm text-gray-400 py-8">주문이 없습니다.</div>;
  }
  return (
    <section className="space-y-2">
      {data.items.map((it, i) => {
        const bg = it.splitQty > 1 ? 'bg-sky-50 border-sky-200'
                 : it.packQty > 1 ? 'bg-yellow-50 border-yellow-200'
                 : 'bg-white border-gray-200';
        return (
          <div key={i} className={`rounded-xl p-3 border ${bg} space-y-1`}>
            <div className="text-sm font-medium truncate" title={it.productName}>
              {it.productName}
            </div>
            <div className="flex flex-wrap gap-1 text-[10px] text-gray-500">
              {it.productNo && <span className="bg-white px-1.5 py-0.5 rounded border">{it.productNo}</span>}
              {it.optionInfo && <span className="bg-white px-1.5 py-0.5 rounded border">{it.optionInfo}</span>}
              {it.splitQty > 1 && <span className="bg-sky-100 text-sky-700 px-1.5 py-0.5 rounded">분리 {it.splitQty}</span>}
              {it.packQty > 1 && <span className="bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded">묶음 {it.packQty}</span>}
            </div>
            <div className="grid grid-cols-4 gap-1 text-xs pt-1">
              <Cell label="주문건수" value={`${it.orderCount}`} />
              <Cell label="주문수량" value={`${it.qty}`} />
              <Cell label="구매수량" value={`${it.costcoQty}`} highlight />
              <Cell label="배송비" value={`${won(it.shippingFee)}`} />
            </div>
            <div className="flex items-center justify-between text-xs pt-1">
              <span className="text-gray-500">팩단가</span>
              <span className="font-medium">{it.unitPrice == null ? '미등록' : won(it.unitPrice)}</span>
            </div>
            <div className="flex items-center justify-between text-sm font-bold">
              <span className="text-gray-700">예상금액</span>
              <span className={it.expectedCost == null ? 'text-gray-400' : 'text-red-600'}>
                {it.expectedCost == null ? '-' : won(it.expectedCost)}
              </span>
            </div>
          </div>
        );
      })}
    </section>
  );
}

function Cell({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="text-center bg-white/70 rounded p-1">
      <div className="text-[9px] text-gray-500">{label}</div>
      <div className={`font-semibold ${highlight ? 'text-blue-700' : 'text-gray-900'}`}>{value}</div>
    </div>
  );
}
