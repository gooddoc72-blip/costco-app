/** 주문 수집 컨트롤 — 플랫폼 / 상태 / 시간 / 조회 버튼 */
import { Download } from 'lucide-react';
import type { Platform } from '@/lib/services/orders';

interface Props {
  platform: Platform;
  hoursBack: number;
  daysBack: number;
  statusFilter: string;
  loading: boolean;
  onPlatformChange: (v: Platform) => void;
  onHoursChange: (v: number) => void;
  onDaysChange: (v: number) => void;
  onStatusChange: (v: string) => void;
  onCollect: () => void;
}

const NAVER_STATUS = [
  { value: 'ALL', label: '전체 (신규 + 발주확인)' },
  { value: 'PAYED', label: '결제완료 (신규)' },
  { value: 'READY', label: '발주확인 (배송준비)' },
] as const;

const COUPANG_STATUS = [
  { value: 'ALL', label: '전체 (결제완료 + 상품준비중)' },
  { value: 'ACCEPT', label: '결제완료' },
  { value: 'INSTRUCT', label: '상품준비중' },
] as const;

export default function CollectControls(p: Props) {
  const statusOptions = p.platform === 'coupang' ? COUPANG_STATUS : NAVER_STATUS;
  return (
    <section className="bg-white rounded-xl p-4 shadow-sm border space-y-3">
      <div>
        <label className="block text-xs text-gray-600 mb-1">플랫폼</label>
        <div className="grid grid-cols-2 gap-2">
          {(['naver', 'coupang'] as const).map(pf => (
            <button key={pf} onClick={() => p.onPlatformChange(pf)}
              className={`py-2 rounded-lg text-sm font-medium border ${
                p.platform === pf
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white text-gray-700 border-gray-200'
              }`}>
              {pf === 'naver' ? '🟢 네이버' : '🟡 쿠팡'}
            </button>
          ))}
        </div>
      </div>
      <div>
        <label className="block text-xs text-gray-600 mb-1">조회 상태</label>
        <select value={p.statusFilter} onChange={e => p.onStatusChange(e.target.value)}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm">
          {statusOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
      {p.platform === 'naver' ? (
        <div>
          <label className="block text-xs text-gray-600 mb-1">시간 범위 (시간)</label>
          <input type="number" min={1} max={168} step={1}
            value={p.hoursBack} onChange={e => p.onHoursChange(parseInt(e.target.value) || 48)}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
          <p className="text-[10px] text-gray-400 mt-1">최근 N시간 안에 변경된 주문 (기본 48h)</p>
        </div>
      ) : (
        <div>
          <label className="block text-xs text-gray-600 mb-1">기간 (일)</label>
          <input type="number" min={1} max={31} step={1}
            value={p.daysBack} onChange={e => p.onDaysChange(parseInt(e.target.value) || 7)}
            className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
          <p className="text-[10px] text-gray-400 mt-1">최근 N일 안의 쿠팡 주문 (기본 7일)</p>
        </div>
      )}
      <button onClick={p.onCollect} disabled={p.loading}
        className="w-full bg-blue-600 text-white font-medium py-2.5 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300">
        <Download size={16} /> {p.loading ? '조회 중...' : 'API로 주문 조회'}
      </button>
    </section>
  );
}
