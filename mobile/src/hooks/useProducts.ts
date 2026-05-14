import { useEffect, useState, useCallback } from 'react';
import {
  fetchProducts,
  patchProduct,
  deleteProductRequest,
  unlockProduct,
  type ProductRow,
} from '@/lib/client/products';

export function useProducts() {
  const [rows, setRows] = useState<ProductRow[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<number | null>(null);

  const load = useCallback(async (q: string) => {
    setLoading(true); setError(null);
    try {
      const r = await fetchProducts(q);
      setRows(r.rows);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(''); }, [load]);

  const onSearch = (q: string) => { setSearch(q); load(q); };

  const onUpdate = async (id: number, patch: Partial<ProductRow>) => {
    setSaving(id); setError(null);
    try {
      await patchProduct(id, {
        unitPrice: patch.unitPrice,
        splitQty: patch.splitQty,
        matchKeyword: patch.matchKeyword,
        costcoName: patch.costcoName,
      });
      setRows(prev => prev.map(r => r.id === id ? { ...r, ...patch } : r));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(null);
    }
  };

  const onDelete = async (id: number) => {
    setSaving(id); setError(null);
    try {
      await deleteProductRequest(id);
      setRows(prev => prev.filter(r => r.id !== id));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(null);
    }
  };

  const onUnlock = async (id: number) => {
    setSaving(id); setError(null);
    try {
      await unlockProduct(id);
      // 복귀된 원본 번호 즉시 반영을 위해 재조회
      await load(search);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(null);
    }
  };

  return { rows, search, loading, error, saving, onSearch, onUpdate, onDelete, onUnlock };
}
