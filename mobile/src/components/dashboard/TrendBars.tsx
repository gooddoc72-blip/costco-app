/** 최근 7일 매출/수익 막대 — 의존성 없는 SVG 바 차트 */
import { won } from '@/lib/fmt';
import type { TrendPoint } from '@/lib/services/dashboard';

interface Props { points: TrendPoint[] }

export default function TrendBars({ points }: Props) {
  if (points.length === 0) {
    return (
      <section className="bg-white rounded-xl p-3 border text-center text-xs text-gray-400 py-6">
        최근 7일 발송 내역 없음
      </section>
    );
  }
  const max = Math.max(1, ...points.map(p => p.sales));
  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border">
      <div className="text-xs font-semibold text-gray-700 mb-2">📊 최근 7일 매출</div>
      <div className="flex items-end justify-between gap-1 h-24">
        {points.map(p => {
          const h = Math.max(4, Math.round((p.sales / max) * 80));
          const profitH = Math.max(2, Math.round((Math.max(0, p.profit) / max) * 80));
          const md = p.date.slice(5);
          return (
            <div key={p.date} className="flex-1 flex flex-col items-center group relative">
              <div className="absolute -top-7 hidden group-hover:block bg-gray-800 text-white text-[10px] rounded px-1.5 py-0.5 whitespace-nowrap z-10">
                {won(p.sales)} / 수익 {won(p.profit)}
              </div>
              <div className="w-full flex flex-col-reverse" style={{ height: 80 }}>
                <div className="bg-blue-300 rounded-t" style={{ height: h }} />
                <div className="bg-green-500 rounded-t -mb-1" style={{ height: profitH }} />
              </div>
              <div className="text-[9px] text-gray-500 mt-1">{md}</div>
            </div>
          );
        })}
      </div>
      <div className="flex gap-3 mt-2 text-[10px] text-gray-500">
        <span className="flex items-center gap-1"><i className="w-2 h-2 bg-blue-300 inline-block rounded-sm" /> 매출</span>
        <span className="flex items-center gap-1"><i className="w-2 h-2 bg-green-500 inline-block rounded-sm" /> 수익</span>
      </div>
    </section>
  );
}
