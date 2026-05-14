'use client';
import { RefreshCw, Trash2 } from 'lucide-react';
import type { LatestRow } from '@/lib/client/rank';

interface Props {
  rows: LatestRow[];
  busyId: number | null;
  onCheckOne: (id: number) => void;
  onDelete: (id: number) => void;
}

export default function RankList({ rows, busyId, onCheckOne, onDelete }: Props) {
  if (rows.length === 0) {
    return <div className="text-center text-xs text-gray-400 py-6">추적 중인 키워드가 없습니다.</div>;
  }
  return (
    <ul className="space-y-2">
      {rows.map(r => <Row key={r.id} r={r} busy={busyId === r.id} onCheckOne={onCheckOne} onDelete={onDelete} />)}
    </ul>
  );
}

function bestRank(r: LatestRow): { rank: number | null; type: string } {
  const m: Record<string, number | null> = {
    원부: r.rankPriceCompare,
    가격비교: r.rankCompare,
    단독: r.rankTotal,
  };
  let best: number | null = null;
  let type = '';
  for (const [k, v] of Object.entries(m)) {
    if (v != null && (best == null || v < best)) { best = v; type = k; }
  }
  return { rank: best, type };
}

function Row({ r, busy, onCheckOne, onDelete }: { r: LatestRow; busy: boolean; onCheckOne: (id: number) => void; onDelete: (id: number) => void }) {
  const { rank, type } = bestRank(r);
  const color = rank == null ? 'bg-gray-100 text-gray-500'
    : rank <= 10 ? 'bg-green-100 text-green-800'
    : rank <= 30 ? 'bg-amber-100 text-amber-800'
    : 'bg-gray-100 text-gray-700';
  return (
    <li className="bg-white rounded-xl border p-3 flex items-center gap-2">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate" title={r.productKeyword}>{r.productKeyword}</div>
        <div className="text-[11px] text-gray-500 truncate">🔍 {r.searchKeyword}{r.storeName ? ` · ${r.storeName}` : ''}</div>
        {r.checkedAt && <div className="text-[10px] text-gray-400">최종: {r.checkedAt}</div>}
      </div>
      <div className={`px-2 py-1 rounded text-xs font-bold ${color}`}>
        {rank == null ? '미체크' : `${rank}위`}
        {type && <span className="ml-1 font-normal text-[10px]">({type})</span>}
      </div>
      <button onClick={() => onCheckOne(r.id)} disabled={busy} className="p-1.5 text-blue-600 disabled:opacity-40" title="순위 체크">
        <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
      </button>
      <button onClick={() => onDelete(r.id)} disabled={busy} className="p-1.5 text-red-500 disabled:opacity-40" title="삭제">
        <Trash2 size={14} />
      </button>
    </li>
  );
}
