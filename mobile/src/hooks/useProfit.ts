/**
 * useProfit — 수익계산 페이지의 state + 비즈니스 호출 캡슐화.
 * UI 컴포넌트는 이 hook만 사용 → UI/로직 분리.
 */
import { useEffect, useMemo, useState } from 'react';
import { fetchProfit, savePrices, type ProfitFetchResult } from '@/lib/client/profit';
import {
  autoCostFromRow, calcProfit, extractSellFactor, unitPriceFromCost,
} from '@/lib/pricing';
import type { ProfitRow, PriceSaveItem } from '@/lib/types';

export function useProfit(date: string) {
  const [data, setData] = useState<ProfitFetchResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [costOverrides, setCostOverrides] = useState<Record<string, number>>({});
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  // 초기 로드
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await fetchProfit(date);
        if (!cancelled) setData(result);
      } catch (e: any) {
        if (!cancelled) setError(e.message || '로드 실패');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [date]);

  const rowCost = (row: ProfitRow): number =>
    costOverrides[row.orderNo] !== undefined
      ? costOverrides[row.orderNo]
      : autoCostFromRow(row);

  const onCostChange = (orderNo: string, value: number) =>
    setCostOverrides(prev => ({ ...prev, [orderNo]: value }));

  const toggleRow = (orderNo: string) =>
    setSelectedRows(prev => {
      const next = new Set(prev);
      next.has(orderNo) ? next.delete(orderNo) : next.add(orderNo);
      return next;
    });

  const toggleAll = () => {
    if (!data) return;
    if (selectedRows.size === data.rows.length) setSelectedRows(new Set());
    else setSelectedRows(new Set(data.rows.map(r => r.orderNo)));
  };

  const resetOverrides = () => {
    setCostOverrides({});
    setSelectedRows(new Set());
  };

  const saveSelected = async () => {
    if (!data || selectedRows.size === 0) return;
    setSaving(true); setSaveMsg(null);

    const items: PriceSaveItem[] = [];
    for (const row of data.rows) {
      if (!selectedRows.has(row.orderNo)) continue;
      const cost = rowCost(row);
      if (cost <= 0) continue;
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
      const result = await savePrices(items);
      // 로컬 state에도 즉시 반영 (서버 refetch 불필요)
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
      setCostOverrides({});
      setSelectedRows(new Set());
      setSaveMsg(`✅ ${result.saved}개 저장 완료`);
    } catch (e: any) {
      setSaveMsg(`❌ ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  // 합계 계산 (전체 행)
  const totals = useMemo(() => {
    if (!data) return null;
    let totalSettlement = 0, totalShipSettle = 0, totalCost = 0, totalProfit = 0;
    for (const r of data.rows) {
      const cost = rowCost(r);
      const c = calcProfit(r, cost, data.settings);
      totalSettlement += r.settlement;
      totalShipSettle += c.shippingSettleAmount;
      totalCost += c.totalCost;
      totalProfit += c.profit;
    }
    return { totalSettlement, totalShipSettle, totalCost, totalProfit };
  }, [data, costOverrides]);

  return {
    data, loading, error, totals,
    costOverrides, selectedRows, saving, saveMsg,
    rowCost, onCostChange, toggleRow, toggleAll, resetOverrides, saveSelected,
  };
}
