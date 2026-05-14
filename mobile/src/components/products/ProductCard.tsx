/** 제품 1건 카드 — 단가/분할수량 인라인 편집 + 삭제 + 분리 해제 */
import { useState } from 'react';
import { Save, Trash2, Unlock } from 'lucide-react';
import { fmt } from '@/lib/fmt';
import type { ProductRow } from '@/lib/client/products';

interface Props {
  row: ProductRow;
  saving: boolean;
  groupSize?: number;  // 같은 코스트코 번호 행 수 (>=2면 그룹 강조)
  onUpdate: (id: number, patch: Partial<ProductRow>) => void;
  onDelete: (id: number) => void;
  onUnlock?: (id: number) => void;
}

export default function ProductCard({ row, saving, groupSize = 1, onUpdate, onDelete, onUnlock }: Props) {
  const grouped = groupSize >= 2;
  const isSplit = !!row.costcoNoDisplay && !row.productNo;
  const [price, setPrice] = useState(row.unitPrice);
  const [split, setSplit] = useState(row.splitQty);
  const changed = price !== row.unitPrice || split !== row.splitQty;

  const handleSave = () => {
    onUpdate(row.id, { unitPrice: price, splitQty: split });
  };

  const handleDelete = () => {
    if (!confirm(`'${row.costcoName || row.matchKeyword}' 삭제할까요?`)) return;
    onDelete(row.id);
  };

  return (
    <div className={`bg-white rounded-xl p-3 shadow-sm border space-y-2 ${grouped ? 'border-l-4 border-l-blue-400' : ''}`}>
      <div className="text-sm font-medium text-gray-900 truncate" title={row.costcoName}>
        {row.costcoName || row.matchKeyword || '(이름없음)'}
      </div>
      <div className="flex flex-wrap gap-1 text-[10px] text-gray-500">
        {row.productNo && (
          <span className={`px-1.5 py-0.5 rounded ${grouped ? 'bg-blue-100 text-blue-800 font-semibold' : 'bg-gray-100'}`}>
            코스트코 {row.productNo}{grouped ? ` · 그룹 ${groupSize}` : ''}
          </span>
        )}
        {isSplit && (
          <span className="bg-red-50 text-red-700 px-1.5 py-0.5 rounded border border-red-200" title="가격 분리됨 — 코스트코 번호 매칭 제외">
            🔒 분리 {row.costcoNoDisplay}
          </span>
        )}
        {row.naverOriginPno && <span className="bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">네이버 원 {row.naverOriginPno}</span>}
        {row.naverChannelPno && <span className="bg-indigo-50 text-indigo-700 px-1.5 py-0.5 rounded">네이버 채널 {row.naverChannelPno}</span>}
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <label className="block">
          <span className="text-gray-500">박스 단가</span>
          <input type="number" value={price} onChange={e => setPrice(Number(e.target.value) || 0)}
            className="mt-0.5 w-full px-2 py-1.5 border border-gray-200 rounded text-right" />
        </label>
        <label className="block">
          <span className="text-gray-500">분할수량</span>
          <input type="number" min={1} value={split} onChange={e => setSplit(Math.max(1, Number(e.target.value) || 1))}
            className="mt-0.5 w-full px-2 py-1.5 border border-gray-200 rounded text-right" />
        </label>
      </div>
      <div className="flex items-center justify-between text-[11px] text-gray-500">
        <span>판매가 {fmt(row.salePrice)}원</span>
        <span>수정 {row.updatedAt || '-'}</span>
      </div>
      <div className="flex gap-2">
        <button onClick={handleSave} disabled={!changed || saving}
          className="flex-1 bg-blue-600 text-white text-xs py-1.5 rounded flex items-center justify-center gap-1 disabled:bg-gray-300">
          <Save size={12} /> {saving ? '저장…' : '저장'}
        </button>
        {isSplit && onUnlock && (
          <button onClick={() => onUnlock(row.id)} disabled={saving}
            className="px-3 py-1.5 bg-amber-50 text-amber-700 text-xs rounded flex items-center gap-1 disabled:opacity-40 border border-amber-200"
            title="분리 해제 — 코스트코 번호 매칭 복귀">
            <Unlock size={12} />
          </button>
        )}
        <button onClick={handleDelete} disabled={saving}
          className="px-3 py-1.5 bg-red-50 text-red-600 text-xs rounded flex items-center gap-1 disabled:opacity-40">
          <Trash2 size={12} />
        </button>
      </div>
    </div>
  );
}
