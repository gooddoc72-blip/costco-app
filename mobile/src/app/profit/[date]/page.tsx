'use client';
/**
 * 수익계산 페이지 — 얇은 orchestrator.
 * 상태/로직: useProfit hook
 * UI: components/profit/*
 */
import { useParams } from 'next/navigation';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import ProfitSummary from '@/components/profit/ProfitSummary';
import ProfitActions from '@/components/profit/ProfitActions';
import ProfitRow from '@/components/profit/ProfitRow';
import { useProfit } from '@/hooks/useProfit';

export default function ProfitDatePage() {
  const params = useParams<{ date: string }>();
  const date = params.date;
  const p = useProfit(date);

  if (p.loading) return <Wrap date={date}><div className="text-center text-gray-500">로딩 중...</div></Wrap>;
  if (p.error) return <Wrap date={date}><div className="bg-red-50 text-red-700 p-4 rounded">{p.error}</div></Wrap>;
  if (!p.data || p.data.rows.length === 0) return (
    <Wrap date={date}>
      <div className="bg-blue-50 text-blue-700 p-4 rounded">
        📭 {date}에 발송된 주문이 없습니다. 송장번호 페이지에서 발송처리하면 자동으로 수익계산 대상으로 잡힙니다.
      </div>
    </Wrap>
  );

  const allSelected = p.selectedRows.size === p.data.rows.length && p.data.rows.length > 0;

  return (
    <>
      <Header title="수익 계산" subtitle={`${date} · 발송 ${p.data.rows.length}건`} />
      <main className="px-2 pt-2 pb-32">
        {p.totals && <ProfitSummary totals={p.totals} />}
        <ProfitActions
          allSelected={allSelected}
          selectedCount={p.selectedRows.size}
          saving={p.saving}
          onToggleAll={p.toggleAll}
          onSave={p.saveSelected}
          onReset={p.resetOverrides}
        />
        {p.saveMsg && (
          <div className={`text-sm p-2 rounded mb-2 ${p.saveMsg.startsWith('✅') ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
            {p.saveMsg}
          </div>
        )}
        <div className="space-y-2">
          {p.data.rows.map((row) => (
            <ProfitRow
              key={row.orderNo}
              row={row}
              cost={p.rowCost(row)}
              settings={p.data!.settings}
              isSelected={p.selectedRows.has(row.orderNo)}
              isModified={p.costOverrides[row.orderNo] !== undefined}
              onToggle={() => p.toggleRow(row.orderNo)}
              onCostChange={(v) => p.onCostChange(row.orderNo, v)}
            />
          ))}
        </div>
      </main>
      <BottomNav />
    </>
  );
}

function Wrap({ date, children }: { date: string; children: React.ReactNode }) {
  return (
    <>
      <Header title="수익 계산" subtitle={date} />
      <main className="px-4 pt-4">{children}</main>
      <BottomNav />
    </>
  );
}
