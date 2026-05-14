import { useState } from 'react';
import {
  uploadSettlementCsv, fetchMatch, deleteSettleDate,
  type SettlementPageData, type UploadResult,
} from '@/lib/client/settlement';

function yesterday(daysBack = 1): string {
  const d = new Date();
  d.setDate(d.getDate() - daysBack);
  return d.toISOString().slice(0, 10);
}

export function useSettlement() {
  const [settleDate, setSettleDate] = useState(yesterday(1));
  const [shipDate, setShipDate] = useState(yesterday(2));
  const [data, setData] = useState<SettlementPageData | null>(null);
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onLoad = async () => {
    setLoading(true); setError(null);
    try {
      setData(await fetchMatch(settleDate, shipDate));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onUpload = async (file: File) => {
    setLoading(true); setError(null);
    try {
      const r = await uploadSettlementCsv(file);
      setUpload(r);
      await onLoad();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onClear = async () => {
    if (!confirm(`${settleDate} 정산 데이터를 삭제할까요?`)) return;
    setLoading(true);
    try {
      await deleteSettleDate(settleDate);
      await onLoad();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return {
    settleDate, shipDate, setSettleDate, setShipDate,
    data, upload, loading, error,
    onLoad, onUpload, onClear,
  };
}
