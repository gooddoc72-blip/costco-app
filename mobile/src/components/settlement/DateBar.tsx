/** 정산일/발송일 입력 + 조회/삭제 버튼 */
interface Props {
  settleDate: string;
  shipDate: string;
  loading: boolean;
  onSettleChange: (v: string) => void;
  onShipChange: (v: string) => void;
  onLoad: () => void;
  onClear: () => void;
}

export default function DateBar(p: Props) {
  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs">
          <span className="text-gray-500">정산일</span>
          <input type="date" value={p.settleDate} onChange={e => p.onSettleChange(e.target.value)}
            className="mt-0.5 w-full px-2 py-1.5 border border-gray-200 rounded" />
        </label>
        <label className="text-xs">
          <span className="text-gray-500">매칭 발송일</span>
          <input type="date" value={p.shipDate} onChange={e => p.onShipChange(e.target.value)}
            className="mt-0.5 w-full px-2 py-1.5 border border-gray-200 rounded" />
        </label>
      </div>
      <div className="flex gap-2">
        <button onClick={p.onLoad} disabled={p.loading}
          className="flex-1 bg-blue-600 text-white text-xs py-2 rounded disabled:bg-gray-300">
          {p.loading ? '조회 중…' : '조회'}
        </button>
        <button onClick={p.onClear} disabled={p.loading}
          className="px-3 bg-red-50 text-red-600 text-xs py-2 rounded disabled:opacity-40">
          정산일 삭제
        </button>
      </div>
    </section>
  );
}
