/** 주문 수집 컨트롤 — 상태 / 시간 / 조회 버튼 */
import { Download } from 'lucide-react';

interface Props {
  hoursBack: number;
  statusFilter: string;
  loading: boolean;
  onHoursChange: (v: number) => void;
  onStatusChange: (v: string) => void;
  onCollect: () => void;
}

const STATUS_OPTIONS = [
  { value: 'ALL', label: '전체 (신규 + 발주확인)' },
  { value: 'PAYED', label: '결제완료 (신규)' },
  { value: 'READY', label: '발주확인 (배송준비)' },
] as const;

export default function CollectControls(p: Props) {
  return (
    <section className="bg-white rounded-xl p-4 shadow-sm border space-y-3">
      <div>
        <label className="block text-xs text-gray-600 mb-1">조회 상태</label>
        <select value={p.statusFilter} onChange={e => p.onStatusChange(e.target.value)}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm">
          {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-xs text-gray-600 mb-1">시간 범위 (시간)</label>
        <input type="number" min={1} max={168} step={1}
          value={p.hoursBack} onChange={e => p.onHoursChange(parseInt(e.target.value) || 48)}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
        <p className="text-[10px] text-gray-400 mt-1">최근 N시간 안에 변경된 주문을 가져옵니다 (기본 48h)</p>
      </div>
      <button onClick={p.onCollect} disabled={p.loading}
        className="w-full bg-blue-600 text-white font-medium py-2.5 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300">
        <Download size={16} /> {p.loading ? '조회 중...' : 'API로 주문 조회'}
      </button>
    </section>
  );
}
