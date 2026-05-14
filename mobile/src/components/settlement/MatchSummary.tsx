/** 매칭 요약 + 탭 (차액 / 누락 / 일치 / 정산만) */
import { useState } from 'react';
import { fmt } from '@/lib/fmt';
import type { SettlementPageData } from '@/lib/client/settlement';

interface Props { data: SettlementPageData }
type TabKey = 'mismatched' | 'missing' | 'matched' | 'orphan';

export default function MatchSummary({ data }: Props) {
  const s = data.match.summary;
  const [tab, setTab] = useState<TabKey>('mismatched');

  const tabs: Array<{ key: TabKey; label: string; n: number }> = [
    { key: 'mismatched', label: '⚠️ 차액', n: s.mismatchedN },
    { key: 'missing',    label: '❌ 누락', n: s.missingN },
    { key: 'matched',    label: '✅ 일치', n: s.matchedN },
    { key: 'orphan',     label: '🔍 정산만', n: s.orphanN },
  ];

  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border space-y-2">
      <div className="grid grid-cols-3 gap-2 text-xs">
        <Stat label="발송" value={`${s.shippedN}건`} />
        <Stat label="정산" value={`${s.settledN}건`} />
        <Stat label="차액" value={`${fmt(s.totalDiff)}원`} color={s.totalDiff === 0 ? 'text-gray-900' : 'text-red-600'} />
      </div>

      <div className="flex gap-1 overflow-x-auto pb-1">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-2 py-1 text-[11px] rounded whitespace-nowrap ${
              tab === t.key ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700'
            }`}>
            {t.label} {t.n}
          </button>
        ))}
      </div>

      <TabTable tab={tab} data={data} />
    </section>
  );
}

function TabTable({ tab, data }: { tab: TabKey; data: SettlementPageData }) {
  if (tab === 'mismatched' || tab === 'matched') {
    const rows = tab === 'mismatched' ? data.match.mismatched : data.match.matched;
    if (rows.length === 0) return <Empty />;
    return (
      <div className="overflow-x-auto max-h-80">
        <table className="w-full text-[11px]">
          <thead className="bg-gray-50 sticky top-0">
            <tr>
              <th className="p-1 text-left">주문</th>
              <th className="p-1 text-right">예상</th>
              <th className="p-1 text-right">실제</th>
              <th className="p-1 text-right">차액</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 200).map(r => (
              <tr key={r.productOrderNo} className="border-b border-gray-100">
                <td className="p-1 truncate max-w-[80px]" title={r.productOrderNo}>{r.recipient || r.productOrderNo}</td>
                <td className="p-1 text-right">{fmt(r.expected)}</td>
                <td className="p-1 text-right">{fmt(r.actual)}</td>
                <td className={`p-1 text-right ${r.diff === 0 ? '' : 'text-red-600'}`}>{fmt(r.diff)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (tab === 'missing') {
    if (data.match.missing.length === 0) return <Empty />;
    return (
      <div className="overflow-x-auto max-h-80">
        <table className="w-full text-[11px]">
          <thead className="bg-gray-50 sticky top-0">
            <tr>
              <th className="p-1 text-left">수취인</th>
              <th className="p-1 text-left">상품</th>
              <th className="p-1 text-right">예상</th>
            </tr>
          </thead>
          <tbody>
            {data.match.missing.slice(0, 200).map(r => (
              <tr key={r.productOrderNo} className="border-b border-gray-100">
                <td className="p-1">{r.recipient}</td>
                <td className="p-1 truncate max-w-[120px]" title={r.productName}>{r.productName}</td>
                <td className="p-1 text-right">{fmt(r.expected)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  // orphan
  if (data.match.orphan.length === 0) return <Empty />;
  return (
    <div className="overflow-x-auto max-h-80">
      <table className="w-full text-[11px]">
        <thead className="bg-gray-50 sticky top-0">
          <tr>
            <th className="p-1 text-left">상품주문번호</th>
            <th className="p-1 text-right">정산금액</th>
          </tr>
        </thead>
        <tbody>
          {data.match.orphan.slice(0, 200).map(r => (
            <tr key={r.productOrderNo} className="border-b border-gray-100">
              <td className="p-1">{r.productOrderNo}</td>
              <td className="p-1 text-right">{fmt(r.settleAmount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Empty() {
  return <div className="text-center text-xs text-gray-400 py-4">해당 없음</div>;
}
function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center bg-gray-50 rounded p-2">
      <div className="text-gray-500">{label}</div>
      <div className={`font-bold ${color || 'text-gray-900'}`}>{value}</div>
    </div>
  );
}
