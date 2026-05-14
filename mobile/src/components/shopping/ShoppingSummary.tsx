/** 장보기 요약 + 발송/인쇄 버튼 */
import Link from 'next/link';
import { won } from '@/lib/fmt';
import { Send, Printer } from 'lucide-react';
import type { ShoppingPageData } from '@/lib/client/shopping';
import type { ShoppingSendResponse } from '@/lib/client/shopping';

interface Props {
  data: ShoppingPageData;
  sending: boolean;
  sendResult: ShoppingSendResponse | null;
  onSend: () => void;
}

export default function ShoppingSummary({ data, sending, sendResult, onSend }: Props) {
  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border space-y-2">
      <div className="grid grid-cols-3 gap-2 text-xs">
        <Stat label="종 수" value={`${data.items.length}종`} />
        <Stat label="미등록" value={`${data.unregistered}종`} color="text-amber-600" />
        <Stat label="예상총액" value={won(data.totalExpected)} color="text-red-600" />
      </div>
      <div className="grid grid-cols-3 gap-2">
        <button onClick={onSend} disabled={sending || data.items.length === 0}
          className="col-span-2 bg-blue-600 text-white py-2 rounded-lg flex items-center justify-center gap-2 disabled:bg-gray-300 text-sm font-medium">
          <Send size={14} /> {sending ? '발송 중…' : '📱 카톡/텔레그램'}
        </button>
        <Link href={`/shopping/${data.date}/print`} target="_blank"
          className={`py-2 rounded-lg flex items-center justify-center gap-1 text-sm font-medium border ${
            data.items.length === 0
              ? 'bg-gray-100 text-gray-400 border-gray-200 pointer-events-none'
              : 'bg-white text-gray-700 border-gray-300'
          }`}>
          <Printer size={14} /> 인쇄
        </Link>
      </div>
      {sendResult && (
        <div className={`text-xs p-2 rounded ${sendResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {sendResult.ok
            ? `✅ ${sendResult.channel} 발송 완료 (${sendResult.msgLength}자${sendResult.detail ? `, ${sendResult.detail}` : ''})`
            : `❌ ${sendResult.error}`}
        </div>
      )}
    </section>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center bg-gray-50 rounded p-2">
      <div className="text-gray-500">{label}</div>
      <div className={`font-bold ${color || 'text-gray-900'}`}>{value}</div>
    </div>
  );
}
