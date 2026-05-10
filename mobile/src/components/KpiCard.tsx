import { won, num, pct } from '@/lib/fmt';
import clsx from 'clsx';

interface KpiCardProps {
  label: string;
  value: number;
  prev?: number;
  unit?: 'won' | 'count';
  accent?: 'red' | 'blue' | 'green';
}

export default function KpiCard({ label, value, prev, unit = 'won', accent = 'red' }: KpiCardProps) {
  const fmtVal = unit === 'won' ? won(value) : num(value);
  const diff = prev != null ? value - prev : null;
  const diffPct = prev != null && prev !== 0 ? ((value - prev) / prev) * 100 : null;
  const positive = diff != null && diff >= 0;

  return (
    <div
      className={clsx(
        'rounded-xl p-4 shadow-sm bg-white',
        accent === 'red' && 'border-l-4 border-primary',
        accent === 'blue' && 'border-l-4 border-blue-500',
        accent === 'green' && 'border-l-4 border-green-500',
      )}
    >
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="text-xl font-bold text-gray-900">{fmtVal}</p>
      {diffPct != null && (
        <p
          className={clsx(
            'text-xs mt-1',
            positive ? 'text-green-600' : 'text-red-500',
          )}
        >
          {positive ? '▲' : '▼'} {Math.abs(diffPct).toFixed(1)}%{' '}
          <span className="text-gray-400">vs 이전</span>
        </p>
      )}
    </div>
  );
}
