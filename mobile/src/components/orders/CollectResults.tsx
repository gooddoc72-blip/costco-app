/** 주문 수집 결과 표시 */
import { fmt } from '@/lib/fmt';
import type { CollectResponse } from '@/lib/client/orders';

export default function CollectResults({ result }: { result: CollectResponse }) {
  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border space-y-2">
      <div className="grid grid-cols-3 gap-2 text-xs">
        <Stat label="조회" value={`${result.fetched}건`} />
        <Stat label="신규" value={`${result.inserted}건`} color="text-green-600" />
        <Stat label="갱신" value={`${result.updated}건`} color="text-blue-600" />
      </div>
      {result.errors.length > 0 && (
        <div className="bg-red-50 text-red-700 text-xs p-2 rounded">
          ⚠️ {result.errors.length}건 실패
        </div>
      )}
      <div className="max-h-96 overflow-y-auto">
        <table className="w-full text-[11px]">
          <thead className="bg-gray-50 sticky top-0">
            <tr>
              <th className="p-1 text-left">수취인</th>
              <th className="p-1 text-left">상품</th>
              <th className="p-1 text-right">수량</th>
              <th className="p-1 text-right">정산예정</th>
              <th className="p-1 text-right">배송비</th>
            </tr>
          </thead>
          <tbody>
            {result.rows.slice(0, 100).map((r, i) => (
              <tr key={i} className="border-b border-gray-100">
                <td className="p-1">{r.recipient}</td>
                <td className="p-1 truncate max-w-[140px]" title={r.productName}>{r.productName}</td>
                <td className="p-1 text-right">{r.qty}</td>
                <td className="p-1 text-right">{fmt(r.settlement)}</td>
                <td className="p-1 text-right text-gray-500">{fmt(r.shippingFee)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {result.rows.length > 100 && (
          <p className="text-[10px] text-gray-400 mt-1">({result.rows.length - 100}건 더 있음)</p>
        )}
      </div>
    </section>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center bg-gray-50 rounded p-2">
      <div className="text-gray-500">{label}</div>
      <div className={`font-bold ${color || 'text-gray-900'}`}>{value}</div>
    </div>
  );
}
