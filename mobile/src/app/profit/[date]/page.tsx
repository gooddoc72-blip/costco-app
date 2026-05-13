'use client';
/**
 * 수익 계산 — 날짜별 페이지
 *
 * 핵심 설계:
 * - 서버 데이터 한번 로드, 이후 모든 인터랙션은 클라이언트 state로 즉시 반영
 * - 저장 시에만 서버 PATCH → 다시 fetch 없이 로컬 state도 함께 갱신
 * - pricing.ts 순수 함수로 저장-로드 대칭 보장
 */
import { useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { fmt } from '@/lib/fmt';
import {
  autoCostFromRow, calcProfit, computeCost, unitPriceFromCost, extractSellFactor,
} from '@/lib/pricing';
import type { ProfitRow, Settings, PriceSaveItem } from '@/lib/types';
import { Save, RotateCcw, CheckSquare, Square } from 'lucide-react';

interface PageData {
  rows: ProfitRow[];
  settings: Settings;
  date: string;
}

export default function ProfitDatePage() {
  const params = useParams<{ date: string }>();
  const router = useRouter();
  const date = params.date;

  const [data, setData] = useState<PageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 클라이언트 사이드 오버라이드 (즉시 반영용)
  const [costOverrides, setCostOverrides] = useState<Record<string, number>>({});
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  // 데이터 로드
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`/api/profit/${date}`);
        if (!res.ok) throw new Error(await res.text());
        const json = await res.json();
        setData(json);
      } catch (e: any) {
        setError(e.message || '로드 실패');
      } finally {
        setLoading(false);
      }
    })();
  }, [date]);

  // 행마다 현재 cost (override 있으면 그것, 없으면 자동계산)
  const rowCost = (row: ProfitRow): number => {
    if (costOverrides[row.orderNo] !== undefined) return costOverrides[row.orderNo];
    return autoCostFromRow(row);
  };

  // 인라인 수정
  const onCostChange = (orderNo: string, value: number) => {
    setCostOverrides(prev => ({ ...prev, [orderNo]: value }));
  };

  // 행 선택 토글
  const toggleRow = (orderNo: string) => {
    setSelectedRows(prev => {
      const next = new Set(prev);
      if (next.has(orderNo)) next.delete(orderNo);
      else next.add(orderNo);
      return next;
    });
  };

  const toggleAll = () => {
    if (!data) return;
    if (selectedRows.size === data.rows.length) {
      setSelectedRows(new Set());
    } else {
      setSelectedRows(new Set(data.rows.map(r => r.orderNo)));
    }
  };

  // 선택된 행들 저장 (네이버 원상품번호 기반)
  const saveSelected = async () => {
    if (!data || selectedRows.size === 0) return;
    setSaving(true);
    setSaveMsg(null);

    const items: PriceSaveItem[] = [];
    for (const row of data.rows) {
      if (!selectedRows.has(row.orderNo)) continue;
      const cost = rowCost(row);
      if (cost <= 0) continue;
      // 사용자 입력 cost → unit_price 역계산 (저장-로드 대칭)
      const sellFactor = extractSellFactor(row.productName);
      const boxPrice = unitPriceFromCost(cost, row.splitQty, row.qty, sellFactor);
      items.push({
        naverOriginPno: row.naverOriginPno,
        costcoProductNo: row.costcoProductNo,
        matchKeyword: row.matchKeyword || row.productName,
        boxPrice,
        splitQty: row.splitQty,
      });
    }

    try {
      const res = await fetch('/api/products/save-prices', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || '저장 실패');

      // 로컬 state에 저장된 unitPrice 반영 (즉시 일관성)
      setData(prev => {
        if (!prev) return prev;
        const updatedRows = prev.rows.map(r => {
          if (!selectedRows.has(r.orderNo)) return r;
          const cost = costOverrides[r.orderNo] ?? autoCostFromRow(r);
          const sellFactor = extractSellFactor(r.productName);
          const newUnit = unitPriceFromCost(cost, r.splitQty, r.qty, sellFactor);
          return { ...r, unitPrice: newUnit };
        });
        return { ...prev, rows: updatedRows };
      });
      // override 클리어 — 이제 자동계산 값 = 저장된 값
      setCostOverrides({});
      setSelectedRows(new Set());
      setSaveMsg(`✅ ${json.saved}개 저장 완료`);
    } catch (e: any) {
      setSaveMsg(`❌ ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const resetOverrides = () => {
    setCostOverrides({});
    setSelectedRows(new Set());
  };

  // 합계 계산
  const totals = useMemo(() => {
    if (!data) return null;
    let totalSettlement = 0, totalShipCust = 0, totalShipSettle = 0;
    let totalCost = 0, totalProfit = 0;
    let cnt = 0;
    for (const r of data.rows) {
      const cost = rowCost(r);
      const c = calcProfit(r, cost, data.settings);
      totalSettlement += r.settlement;
      totalShipCust += r.customerShippingFee;
      totalShipSettle += c.shippingSettleAmount;
      totalCost += c.totalCost;
      totalProfit += c.profit;
      cnt++;
    }
    return { cnt, totalSettlement, totalShipCust, totalShipSettle, totalCost, totalProfit };
  }, [data, costOverrides]);

  if (loading) return (
    <>
      <Header title="수익 계산" subtitle={date} />
      <main className="px-4 pt-4"><div className="text-center text-gray-500">로딩 중...</div></main>
      <BottomNav />
    </>
  );

  if (error) return (
    <>
      <Header title="수익 계산" subtitle={date} />
      <main className="px-4 pt-4"><div className="bg-red-50 text-red-700 p-4 rounded">{error}</div></main>
      <BottomNav />
    </>
  );

  if (!data || data.rows.length === 0) return (
    <>
      <Header title="수익 계산" subtitle={date} />
      <main className="px-4 pt-4">
        <div className="bg-blue-50 text-blue-700 p-4 rounded">
          📭 {date}에 발송된 주문이 없습니다. 송장번호 페이지에서 발송처리하면 자동으로 수익계산 대상으로 잡힙니다.
        </div>
      </main>
      <BottomNav />
    </>
  );

  const allSelected = selectedRows.size === data.rows.length && data.rows.length > 0;

  return (
    <>
      <Header title="수익 계산" subtitle={`${date} · 발송 ${data.rows.length}건`} />
      <main className="px-2 pt-2 pb-32">
        {/* 합계 카드 */}
        {totals && (
          <div className="bg-white rounded-xl p-3 mb-3 shadow-sm border grid grid-cols-3 gap-2 text-xs">
            <div>
              <div className="text-gray-500">수입</div>
              <div className="font-bold text-gray-900">{fmt(totals.totalSettlement + totals.totalShipSettle)}원</div>
              <div className="text-[10px] text-gray-400 mt-0.5">정산 {fmt(totals.totalSettlement)} + 배송 {fmt(totals.totalShipSettle)}</div>
            </div>
            <div>
              <div className="text-gray-500">지출</div>
              <div className="font-bold text-gray-900">{fmt(totals.totalCost)}원</div>
            </div>
            <div>
              <div className="text-gray-500">순수익</div>
              <div className={`font-bold ${totals.totalProfit >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                {totals.totalProfit >= 0 ? '+' : ''}{fmt(totals.totalProfit)}원
              </div>
            </div>
          </div>
        )}

        {/* 액션 바 */}
        <div className="flex gap-2 mb-2">
          <button
            onClick={toggleAll}
            className="flex items-center gap-1 px-3 py-2 rounded-lg border bg-white text-sm"
          >
            {allSelected ? <CheckSquare size={16} /> : <Square size={16} />}
            전체
          </button>
          <button
            onClick={saveSelected}
            disabled={selectedRows.size === 0 || saving}
            className="flex-1 flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium disabled:bg-gray-300"
          >
            <Save size={16} /> {selectedRows.size}개 저장
          </button>
          <button
            onClick={resetOverrides}
            className="flex items-center gap-1 px-3 py-2 rounded-lg border bg-white text-sm"
          >
            <RotateCcw size={16} /> 초기화
          </button>
        </div>
        {saveMsg && (
          <div className={`text-sm p-2 rounded mb-2 ${saveMsg.startsWith('✅') ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
            {saveMsg}
          </div>
        )}

        {/* 행 리스트 */}
        <div className="space-y-2">
          {data.rows.map((row) => {
            const cost = rowCost(row);
            const calc = calcProfit(row, cost, data.settings);
            const isSelected = selectedRows.has(row.orderNo);
            const isModified = costOverrides[row.orderNo] !== undefined;
            const profitColor = calc.profit >= 0 ? 'text-green-600' : 'text-red-600';
            return (
              <div
                key={row.orderNo}
                className={`bg-white rounded-xl p-3 shadow-sm border ${isSelected ? 'border-blue-400 bg-blue-50' : 'border-gray-100'}`}
              >
                <div className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleRow(row.orderNo)}
                    className="mt-1 w-4 h-4"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1 text-xs">
                      <span className="font-medium text-gray-700">{row.recipient}</span>
                      {row.naverChannelPno && (
                        <span className="text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded text-[10px]">#{row.naverChannelPno}</span>
                      )}
                      {row.matchSource !== '미매칭' && (
                        <span className="text-[10px] text-gray-400">{row.matchSource}</span>
                      )}
                    </div>
                    <div className="text-sm text-gray-900 mt-1 break-words">
                      {row.productName}
                    </div>
                    {row.optionInfo && (
                      <div className="text-[11px] text-gray-500 mt-0.5">{row.optionInfo}</div>
                    )}
                    <div className="grid grid-cols-4 gap-1 mt-2 text-[11px]">
                      <div>
                        <div className="text-gray-400">수량</div>
                        <div>{row.qty}</div>
                      </div>
                      <div>
                        <div className="text-gray-400">정산</div>
                        <div>{fmt(row.settlement)}</div>
                      </div>
                      <div>
                        <div className="text-gray-400">배송</div>
                        <div>{fmt(row.customerShippingFee)}</div>
                      </div>
                      <div>
                        <div className="text-gray-400">수익</div>
                        <div className={`font-bold ${profitColor}`}>{calc.profit >= 0 ? '+' : ''}{fmt(calc.profit)}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 mt-2">
                      <label className="text-[11px] text-gray-500">구입가</label>
                      <input
                        type="number"
                        value={cost}
                        onChange={(e) => onCostChange(row.orderNo, parseInt(e.target.value) || 0)}
                        className={`flex-1 px-2 py-1 text-sm border rounded ${isModified ? 'border-orange-400 bg-orange-50' : 'border-gray-200'}`}
                        step="100"
                        min="0"
                      />
                      {isModified && <span className="text-[10px] text-orange-600">수정됨</span>}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </main>
      <BottomNav />
    </>
  );
}
