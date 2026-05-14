/** 배송비 수수료 분석 패널 */
import { fmt } from '@/lib/fmt';
import type { SettlementPageData } from '@/lib/client/settlement';

export default function ShippingPanel({ data }: { data: SettlementPageData }) {
  const sa = data.shipping;
  if (sa.rows.length === 0) return null;
  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border space-y-2">
      <div className="text-sm font-semibold">🚚 배송비 수수료 분석</div>
      <p className="text-[11px] text-gray-500">
        고객 결제 배송비 vs 네이버 정산 배송비 — 차이 = 네이버 수수료
      </p>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <Stat label="고객 결제" value={`${fmt(sa.totalCustomerShipping)}원`} />
        <Stat label="정산받음" value={`${fmt(sa.totalSettledShipping)}원`} />
        <Stat label="수수료 합" value={`${fmt(sa.totalCommission)}원`} color="text-red-600" />
        <Stat label="평균 수수료율" value={`${sa.avgCommissionRate}%`} />
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
