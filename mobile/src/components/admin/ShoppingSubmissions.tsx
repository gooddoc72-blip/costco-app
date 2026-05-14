'use client';
/** 사용자 장보기 제출 목록 (관리자) */
import { useState } from 'react';
import { Trash2, ChevronDown, ChevronRight } from 'lucide-react';
import { won } from '@/lib/fmt';
import { useAdminShopping } from '@/hooks/useAdminShopping';
import type { AdminSubmission } from '@/lib/client/adminShopping';

export default function ShoppingSubmissions() {
  const a = useAdminShopping();
  return (
    <section className="bg-white rounded-xl p-4 shadow-sm border">
      <div className="font-semibold text-gray-900 mb-2">🛒 사용자 장보기 제출</div>
      {a.error && <div className="bg-red-50 text-red-700 text-xs p-2 rounded mb-2">{a.error}</div>}
      {a.loading && a.submissions.length === 0 && <p className="text-xs text-gray-400">불러오는 중…</p>}
      {!a.loading && a.submissions.length === 0 && (
        <p className="text-xs text-gray-400">제출된 장보기 목록이 없습니다.</p>
      )}
      <ul className="space-y-2">
        {a.submissions.map(s => (
          <SubmissionRow key={s.id} s={s} onDelete={a.onDelete} />
        ))}
      </ul>
    </section>
  );
}

function SubmissionRow({ s, onDelete }: { s: AdminSubmission; onDelete: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-gray-50">
        <button onClick={() => setOpen(o => !o)} className="flex items-center gap-1 text-xs flex-1 text-left">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span className="font-semibold">{s.username}</span>
          <span className="text-gray-500"> · {s.orderDate}</span>
          <span className="ml-auto text-gray-700">{s.totalItems}건 / {won(s.totalAmount)}</span>
        </button>
        <button onClick={() => onDelete(s.id)} className="ml-2 text-red-500" title="삭제">
          <Trash2 size={12} />
        </button>
      </div>
      {open && (
        <div className="max-h-72 overflow-y-auto">
          <table className="w-full text-[11px]">
            <thead className="bg-gray-100 sticky top-0">
              <tr>
                <th className="p-1 text-left">상품</th>
                <th className="p-1 text-right">주문</th>
                <th className="p-1 text-right">구매</th>
                <th className="p-1 text-right">팩단가</th>
                <th className="p-1 text-right">예상</th>
              </tr>
            </thead>
            <tbody>
              {s.items.map((it, i) => (
                <tr key={i} className="border-b">
                  <td className="p-1 truncate max-w-[160px]" title={it.productName}>
                    {it.productName}{it.optionInfo ? ` · ${it.optionInfo}` : ''}
                  </td>
                  <td className="p-1 text-right">{it.qty}</td>
                  <td className="p-1 text-right font-semibold">{it.costcoQty}</td>
                  <td className="p-1 text-right">{it.unitPrice == null ? '-' : won(it.unitPrice)}</td>
                  <td className="p-1 text-right">{it.expectedCost == null ? '-' : won(it.expectedCost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-2 py-1 text-[10px] text-gray-500 bg-gray-50 border-t">
            제출: {s.submittedAt}
          </div>
        </div>
      )}
    </li>
  );
}
