/** 제품 검색바 */
import { Search } from 'lucide-react';
import { useState } from 'react';

interface Props {
  initial: string;
  loading: boolean;
  onSubmit: (q: string) => void;
}

export default function ProductSearch({ initial, loading, onSubmit }: Props) {
  const [v, setV] = useState(initial);
  return (
    <form
      onSubmit={e => { e.preventDefault(); onSubmit(v); }}
      className="bg-white rounded-xl p-3 shadow-sm border flex items-center gap-2"
    >
      <Search size={16} className="text-gray-400" />
      <input
        type="text" value={v} onChange={e => setV(e.target.value)}
        placeholder="상품명 · 코스트코번호 · 네이버상품번호"
        className="flex-1 text-sm outline-none"
      />
      <button type="submit" disabled={loading}
        className="px-3 py-1 bg-blue-600 text-white text-xs rounded-lg disabled:bg-gray-300">
        {loading ? '검색…' : '검색'}
      </button>
    </form>
  );
}
