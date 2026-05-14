'use client';
import { useState } from 'react';
import { Plus } from 'lucide-react';

interface Props {
  onAdd: (p: { productKeyword: string; searchKeyword: string; naverProductNo?: string; storeName?: string }) => Promise<void>;
}

export default function AddKeywordForm({ onAdd }: Props) {
  const [open, setOpen] = useState(false);
  const [pk, setPk] = useState('');
  const [sk, setSk] = useState('');
  const [pno, setPno] = useState('');
  const [store, setStore] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onAdd({ productKeyword: pk, searchKeyword: sk, naverProductNo: pno, storeName: store });
      setPk(''); setSk(''); setPno(''); setStore('');
      setOpen(false);
    } catch { /* error already in hook */ }
    finally { setSubmitting(false); }
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)}
        className="w-full bg-blue-600 text-white py-2 rounded-lg flex items-center justify-center gap-1 text-sm font-medium">
        <Plus size={14} /> 키워드 추가
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="bg-white rounded-xl p-3 border space-y-2">
      <div className="text-sm font-semibold">➕ 새 키워드 추적</div>
      <Field label="상품 키워드 (매칭용)" value={pk} onChange={setPk} placeholder="예: 코스트코 견과류 1kg" required />
      <Field label="네이버 검색 키워드" value={sk} onChange={setSk} placeholder="예: 코스트코 견과류" required />
      <Field label="네이버 상품번호 (선택)" value={pno} onChange={setPno} placeholder="productId — 정확 매칭에 사용" />
      <Field label="내 스토어명 (선택)" value={store} onChange={setStore} placeholder="예: 코스트코핫딜" />
      <div className="flex gap-2">
        <button type="submit" disabled={submitting || !pk.trim() || !sk.trim()}
          className="flex-1 bg-blue-600 text-white py-2 rounded text-sm disabled:bg-gray-300">
          {submitting ? '추가 중…' : '추가'}
        </button>
        <button type="button" onClick={() => setOpen(false)}
          className="px-3 bg-gray-100 text-gray-700 py-2 rounded text-sm">
          취소
        </button>
      </div>
    </form>
  );
}

function Field({ label, value, onChange, placeholder, required }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; required?: boolean;
}) {
  return (
    <label className="block text-xs">
      <span className="text-gray-600">{label}{required ? ' *' : ''}</span>
      <input type="text" value={value} onChange={e => onChange(e.target.value)}
        placeholder={placeholder} required={required}
        className="mt-0.5 w-full px-2 py-1.5 border border-gray-200 rounded text-sm" />
    </label>
  );
}
