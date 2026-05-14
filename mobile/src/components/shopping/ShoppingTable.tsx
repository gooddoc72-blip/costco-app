/** 장보기 상품 카드 리스트 — 수량/정산금액/택배비 표시 */
import { won } from '@/lib/fmt';
import type { ShoppingPageData } from '@/lib/client/shopping';

export default function ShoppingTable({ data }: { data: ShoppingPageData }) {
  if (data.items.length === 0) {
    return <div className="text-center text-sm text-gray-400 py-8">주문이 없습니다.</div>;
  }
  return (
    <section className="space-y-2">
      {data.items.map((it, i) => (
        <div key={i} className="bg-white rounded-xl p-3 border space-y-1">
          <div className="text-sm font-medium truncate" title={it.productName}>
            {it.productName}
          </div>
          <div className="flex flex-wrap gap-1 text-[10px] text-gray-500">
            {it.productNo && <span className="bg-gray-100 px-1.5 py-0.5 rounded">{it.productNo}</span>}
            {it.optionInfo && <span className="bg-gray-100 px-1.5 py-0.5 rounded">{it.optionInfo}</span>}
          </div>
          <div className="grid grid-cols-3 gap-1 text-xs pt-1">
            <Cell label="수량" value={`${it.qty}`} />
            <Cell label="정산금액" value={won(it.totalSettlement)} highlight />
            <Cell label="택배비" value={won(it.shippingFee)} />
          </div>
        </div>
      ))}
    </section>
  );
}

function Cell({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="text-center bg-gray-50 rounded p-1">
      <div className="text-[9px] text-gray-500">{label}</div>
      <div className={`font-semibold ${highlight ? 'text-blue-700' : 'text-gray-900'}`}>{value}</div>
    </div>
  );
}
