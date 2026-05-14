'use client';
/** 1년 순위 추이 — 의존성 없는 SVG. y축 거꾸로 (1위가 최상단) */
import type { HistoryPoint } from '@/lib/client/rank';

const W = 440, H = 200, PAD_L = 32, PAD_R = 10, PAD_T = 10, PAD_B = 28;

function bestOf(p: HistoryPoint): number | null {
  const arr = [p.wonbu, p.compare, p.solo].filter(v => v != null) as number[];
  return arr.length === 0 ? null : Math.min(...arr);
}

export default function YearChart({ points }: { points: HistoryPoint[] }) {
  if (points.length === 0) {
    return <p className="text-center text-xs text-gray-400 py-4">순위 이력이 없습니다.</p>;
  }
  const ts = points.map(p => new Date(p.checkedAt).getTime());
  const tMin = Math.min(...ts), tMax = Math.max(...ts);
  const tRange = Math.max(1, tMax - tMin);
  const allRanks = points.map(bestOf).filter(v => v != null) as number[];
  if (allRanks.length === 0) {
    return <p className="text-center text-xs text-gray-400 py-4">유효한 순위 데이터가 없습니다.</p>;
  }
  const rMin = 1;
  const rMax = Math.max(50, Math.max(...allRanks));

  const x = (t: number) => PAD_L + ((t - tMin) / tRange) * (W - PAD_L - PAD_R);
  const y = (r: number) => PAD_T + ((r - rMin) / (rMax - rMin)) * (H - PAD_T - PAD_B);

  function line(values: Array<{ t: number; v: number | null }>, color: string, label: string) {
    const segs: string[] = [];
    let pen = 'M';
    for (const p of values) {
      if (p.v == null) { pen = 'M'; continue; }
      segs.push(`${pen}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`);
      pen = 'L';
    }
    const dots = values
      .filter(p => p.v != null)
      .map(p => <circle key={p.t} cx={x(p.t)} cy={y(p.v as number)} r={2.5} fill={color} />);
    return (
      <g key={label}>
        <path d={segs.join(' ')} stroke={color} strokeWidth={1.5} fill="none" />
        {dots}
      </g>
    );
  }

  const datasets = [
    { color: '#e74c3c', label: '원부',   values: points.map(p => ({ t: new Date(p.checkedAt).getTime(), v: p.wonbu })) },
    { color: '#ff7f0e', label: '가격비교', values: points.map(p => ({ t: new Date(p.checkedAt).getTime(), v: p.compare })) },
    { color: '#1f77b4', label: '단독',   values: points.map(p => ({ t: new Date(p.checkedAt).getTime(), v: p.solo })) },
  ];

  // y축 마커: 1, 10, 30, rMax
  const yMarks = Array.from(new Set([1, 10, 30, rMax])).filter(v => v >= rMin && v <= rMax);
  const fmtDate = (t: number) => new Date(t).toISOString().slice(5, 10);

  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border">
      <div className="text-xs font-semibold mb-1">📈 1년 변동 추이 (낮을수록 좋음)</div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {/* y축 가이드 */}
        {yMarks.map(m => (
          <g key={m}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(m)} y2={y(m)} stroke="#eee" strokeDasharray={m === 10 ? '4,2' : '2,3'} />
            <text x={4} y={y(m) + 3} fontSize="9" fill="#888">{m}</text>
          </g>
        ))}
        {/* x축 시작/끝 */}
        <text x={PAD_L} y={H - 6} fontSize="9" fill="#888">{fmtDate(tMin)}</text>
        <text x={W - 30} y={H - 6} fontSize="9" fill="#888">{fmtDate(tMax)}</text>
        {datasets.map(d => line(d.values, d.color, d.label))}
      </svg>
      <div className="flex gap-3 text-[10px] text-gray-500 mt-1">
        {datasets.map(d => (
          <span key={d.label} className="flex items-center gap-1">
            <i className="inline-block w-2 h-2 rounded-sm" style={{ background: d.color }} /> {d.label}
          </span>
        ))}
      </div>
    </section>
  );
}
