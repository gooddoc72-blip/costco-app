'use client';
/** 한 달 일별 순위 셀 (1~31), 전일 대비 하락이면 빨강 */
import type { DayRank } from '@/lib/client/rank';

interface Props {
  year: number;
  month: number;
  days: Record<number, DayRank>;
}

function daysInMonth(y: number, m: number): number {
  return new Date(y, m, 0).getDate();
}

function colorFor(rank: number, isDrop: boolean): { bg: string; fg: string; border: string } {
  if (isDrop) return { bg: 'bg-red-500', fg: 'text-white', border: 'border-red-600' };
  if (rank <= 10) return { bg: 'bg-green-600', fg: 'text-white', border: 'border-green-700' };
  if (rank <= 30) return { bg: 'bg-amber-500', fg: 'text-white', border: 'border-amber-600' };
  return { bg: 'bg-gray-200', fg: 'text-gray-700', border: 'border-gray-300' };
}

export default function MonthCalendar({ year, month, days }: Props) {
  const total = daysInMonth(year, month);
  let prev: number | null = null;
  const cells: React.ReactNode[] = [];
  for (let d = 1; d <= total; d++) {
    const info = days[d];
    if (!info) {
      cells.push(
        <span key={d}
          className="inline-flex items-center justify-center w-8 h-8 text-xs bg-gray-50 text-gray-300 border border-gray-100 rounded m-0.5">
          {d}
        </span>
      );
      continue;
    }
    const isDrop = prev != null && info.best > prev;
    const c = colorFor(info.best, isDrop);
    const tip = `${d}일: ${info.bestType} ${info.best}위${isDrop ? ` ↓${info.best - (prev as number)}` : ''}`;
    cells.push(
      <span key={d} title={tip}
        className={`inline-flex items-center justify-center w-8 h-8 text-xs font-bold ${c.bg} ${c.fg} border ${c.border} rounded m-0.5`}>
        {info.best}
      </span>
    );
    prev = info.best;
  }
  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border">
      <div className="text-xs font-semibold text-gray-700 mb-1">
        📅 {year}년 {month}월 일별 순위
      </div>
      <p className="text-[10px] text-gray-500 mb-2">
        🟢 TOP10  🟠 TOP30  ⬜ 30위 초과  🔴 전일 대비 하락
      </p>
      <div className="leading-none">{cells}</div>
    </section>
  );
}
