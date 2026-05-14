/** 장보기 요약 + 발송/인쇄/저장/관리자 제출 버튼 */
import Link from 'next/link';
import { won } from '@/lib/fmt';
import { Send, Printer, Save, UserCheck } from 'lucide-react';
import type {
  ShoppingPageData, ShoppingSendResponse,
  SaveDailyResponse, SubmitAdminResponse,
} from '@/lib/client/shopping';

interface Props {
  data: ShoppingPageData;
  sending: boolean;
  sendResult: ShoppingSendResponse | null;
  onSend: () => void;
  saving: boolean;
  saveResult: SaveDailyResponse | null;
  onSave: () => void;
  submitting: boolean;
  submitResult: SubmitAdminResponse | null;
  onSubmit: () => void;
}

export default function ShoppingSummary(p: Props) {
  const { data } = p;
  const noItems = data.items.length === 0;
  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border space-y-2">
      <div className="grid grid-cols-3 gap-2 text-xs">
        <Stat label="종 수" value={`${data.items.length}종`} />
        <Stat label="미등록" value={`${data.unregistered}종`} color="text-amber-600" />
        <Stat label="예상총액" value={won(data.totalExpected)} color="text-red-600" />
      </div>
      <div className="grid grid-cols-3 gap-2">
        <button onClick={p.onSend} disabled={p.sending || noItems}
          className="col-span-2 bg-blue-600 text-white py-2 rounded-lg flex items-center justify-center gap-2 disabled:bg-gray-300 text-sm font-medium">
          <Send size={14} /> {p.sending ? '발송 중…' : '📱 카톡/텔레그램'}
        </button>
        <Link href={`/shopping/${data.date}/print`} target="_blank"
          aria-disabled={noItems}
          className={`py-2 rounded-lg flex items-center justify-center gap-1 text-sm font-medium border ${
            noItems
              ? 'bg-gray-100 text-gray-400 border-gray-200 pointer-events-none'
              : 'bg-white text-gray-700 border-gray-300'
          }`}>
          <Printer size={14} /> 인쇄
        </Link>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button onClick={p.onSave} disabled={p.saving || noItems}
          className="bg-green-600 text-white py-2 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300 text-sm font-medium">
          <Save size={14} /> {p.saving ? '저장 중…' : '💾 수익계산 저장'}
        </button>
        <button onClick={p.onSubmit} disabled={p.submitting || noItems}
          className="bg-purple-600 text-white py-2 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300 text-sm font-medium">
          <UserCheck size={14} /> {p.submitting ? '제출 중…' : '👑 관리자 제출'}
        </button>
      </div>

      {p.sendResult && (
        <Banner ok={p.sendResult.ok}
          msg={p.sendResult.ok
            ? `${p.sendResult.channel} 발송 완료 (${p.sendResult.msgLength}자${p.sendResult.detail ? `, ${p.sendResult.detail}` : ''})`
            : p.sendResult.error || '실패'} />
      )}
      {p.saveResult && (
        <Banner ok msg={`수익계산 저장 ${p.saveResult.saved}건 (매칭 ${p.saveResult.matched} · 수익 ${won(p.saveResult.totalProfit)})`} />
      )}
      {p.submitResult && (
        <Banner ok={p.submitResult.ok}
          msg={p.submitResult.ok
            ? `관리자에게 제출됨 — ${p.submitResult.totalItems}건 (${won(p.submitResult.totalAmount)})`
            : p.submitResult.error || '실패'} />
      )}
    </section>
  );
}

function Banner({ ok, msg }: { ok: boolean; msg: string }) {
  return (
    <div className={`text-xs p-2 rounded ${ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
      {ok ? '✅ ' : '❌ '}{msg}
    </div>
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
