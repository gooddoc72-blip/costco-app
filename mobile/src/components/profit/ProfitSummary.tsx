/** 수익계산 상단 합계 카드 */
import { fmt } from '@/lib/fmt';

interface Totals {
  totalSettlement: number;
  totalShipSettle: number;
  totalCost: number;
  totalProfit: number;
}

export default function ProfitSummary({ totals }: { totals: Totals }) {
  return (
    <div className="bg-white rounded-xl p-3 mb-3 shadow-sm border grid grid-cols-3 gap-2 text-xs">
      <div>
        <div className="text-gray-500">수입</div>
        <div className="font-bold text-gray-900">
          {fmt(totals.totalSettlement + totals.totalShipSettle)}원
        </div>
        <div className="text-[10px] text-gray-400 mt-0.5">
          정산 {fmt(totals.totalSettlement)} + 배송 {fmt(totals.totalShipSettle)}
        </div>
      </div>
      <div>
        <div className="text-gray-500">지출</div>
        <div className="font-bold text-gray-900">{fmt(totals.totalCost)}원</div>
      </div>
      <div>
        <div className="text-gray-500">순수익</div>
        <div className={`font-bold ${totals.totalProfit >= 0 ? 'text-green-600' : 'text-red-600'}`}>
          {totals.totalProfit >= 0 ? '+' : ''}{fmt(totals.totalProfit)}원
        </div>
      </div>
    </div>
  );
}
