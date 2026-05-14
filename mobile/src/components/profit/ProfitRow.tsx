/** 수익계산 행 1개 — 체크박스, 정보, 가격 입력 */
import { fmt } from '@/lib/fmt';
import { calcProfit } from '@/lib/pricing';
import type { ProfitRow as ProfitRowType, Settings } from '@/lib/types';

interface Props {
  row: ProfitRowType;
  cost: number;
  settings: Settings;
  isSelected: boolean;
  isModified: boolean;
  onToggle: () => void;
  onCostChange: (v: number) => void;
}

export default function ProfitRow(p: Props) {
  const calc = calcProfit(p.row, p.cost, p.settings);
  const profitColor = calc.profit >= 0 ? 'text-green-600' : 'text-red-600';
  return (
    <div className={`bg-white rounded-xl p-3 shadow-sm border ${p.isSelected ? 'border-blue-400 bg-blue-50' : 'border-gray-100'}`}>
      <div className="flex items-start gap-2">
        <input type="checkbox" checked={p.isSelected} onChange={p.onToggle} className="mt-1 w-4 h-4" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1 text-xs">
            <span className="font-medium text-gray-700">{p.row.recipient}</span>
            {p.row.naverChannelPno && (
              <span className="text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded text-[10px]">#{p.row.naverChannelPno}</span>
            )}
            {p.row.matchSource !== '미매칭' && (
              <span className="text-[10px] text-gray-400">{p.row.matchSource}</span>
            )}
          </div>
          <div className="text-sm text-gray-900 mt-1 break-words">{p.row.productName}</div>
          {p.row.optionInfo && (
            <div className="text-[11px] text-gray-500 mt-0.5">{p.row.optionInfo}</div>
          )}
          <div className="grid grid-cols-4 gap-1 mt-2 text-[11px]">
            <Info label="수량" value={String(p.row.qty)} />
            <Info label="정산" value={fmt(p.row.settlement)} />
            <Info label="배송" value={fmt(p.row.customerShippingFee)} />
            <Info label="수익" value={(calc.profit >= 0 ? '+' : '') + fmt(calc.profit)} valueClass={`font-bold ${profitColor}`} />
          </div>
          {/* 단가(1주문) 입력 — 수량 자동 곱셈해 합계 cost로 환산 */}
          <div className="flex items-center gap-2 mt-2">
            <label className="text-[11px] text-gray-500">단가</label>
            <input
              type="number"
              value={Math.floor(p.cost / Math.max(1, p.row.qty))}
              onChange={(e) => p.onCostChange((parseInt(e.target.value) || 0) * Math.max(1, p.row.qty))}
              className={`flex-1 px-2 py-1 text-sm border rounded ${p.isModified ? 'border-orange-400 bg-orange-50' : 'border-gray-200'}`}
              step={100}
              min={0}
              title={`× 수량 ${p.row.qty} = 합계 ${fmt(p.cost)}`}
            />
            <span className="text-[10px] text-gray-500 whitespace-nowrap">×{p.row.qty} = {fmt(p.cost)}</span>
            {p.isModified && <span className="text-[10px] text-orange-600">수정</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

function Info({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div>
      <div className="text-gray-400">{label}</div>
      <div className={valueClass}>{value}</div>
    </div>
  );
}
