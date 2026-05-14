import { useCallback, useEffect, useState } from 'react';
import {
  fetchRanks, createKeyword, deleteKeyword, checkAll, checkOne,
  type RankPageData, type CheckSummary,
} from '@/lib/client/rank';

export function useRank() {
  const [data, setData] = useState<RankPageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<CheckSummary | null>(null);
  const [checkingAll, setCheckingAll] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true); setError(null);
    try { setData(await fetchRanks()); }
    catch (e: any) { setError(e?.message || '로드 실패'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const onAdd = async (p: {
    productKeyword: string; searchKeyword: string;
    naverProductNo?: string; storeName?: string;
  }) => {
    setError(null);
    try { await createKeyword(p); await reload(); }
    catch (e: any) { setError(e?.message || '추가 실패'); throw e; }
  };

  const onDelete = async (id: number) => {
    if (!confirm('삭제할까요?')) return;
    setBusyId(id);
    try { await deleteKeyword(id); await reload(); }
    catch (e: any) { setError(e?.message); }
    finally { setBusyId(null); }
  };

  const onCheckOne = async (id: number) => {
    setBusyId(id); setError(null);
    try { await checkOne(id); await reload(); }
    catch (e: any) { setError(e?.message); }
    finally { setBusyId(null); }
  };

  const onCheckAll = async () => {
    setCheckingAll(true); setError(null); setSummary(null);
    try {
      const s = await checkAll();
      setSummary(s);
      await reload();
    } catch (e: any) {
      setError(e?.message);
    } finally {
      setCheckingAll(false);
    }
  };

  return {
    data, loading, error, busyId, summary, checkingAll,
    reload, onAdd, onDelete, onCheckOne, onCheckAll,
  };
}
