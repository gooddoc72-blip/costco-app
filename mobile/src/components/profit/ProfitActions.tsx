/** 수익계산 액션 바: 전체선택 / 저장 / 초기화 */
import { Save, RotateCcw, CheckSquare, Square } from 'lucide-react';

interface Props {
  allSelected: boolean;
  selectedCount: number;
  saving: boolean;
  onToggleAll: () => void;
  onSave: () => void;
  onReset: () => void;
}

export default function ProfitActions(p: Props) {
  return (
    <div className="flex gap-2 mb-2">
      <button onClick={p.onToggleAll}
        className="flex items-center gap-1 px-3 py-2 rounded-lg border bg-white text-sm">
        {p.allSelected ? <CheckSquare size={16} /> : <Square size={16} />}
        전체
      </button>
      <button onClick={p.onSave}
        disabled={p.selectedCount === 0 || p.saving}
        className="flex-1 flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium disabled:bg-gray-300">
        <Save size={16} /> {p.selectedCount}개 저장
      </button>
      <button onClick={p.onReset}
        className="flex items-center gap-1 px-3 py-2 rounded-lg border bg-white text-sm">
        <RotateCcw size={16} /> 초기화
      </button>
    </div>
  );
}
