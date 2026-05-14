import { useCallback, useEffect, useState } from 'react';
import {
  fetchAdminSubmissions, deleteAdminSubmission,
  type AdminSubmission,
} from '@/lib/client/adminShopping';

export function useAdminShopping() {
  const [submissions, setSubmissions] = useState<AdminSubmission[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetchAdminSubmissions({ limit: 50 });
      setSubmissions(r.submissions);
    } catch (e: any) {
      setError(e?.message || '로드 실패');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onDelete = async (id: number) => {
    if (!confirm('이 제출 내역을 삭제할까요?')) return;
    try {
      await deleteAdminSubmission(id);
      setSubmissions(prev => prev.filter(s => s.id !== id));
    } catch (e: any) {
      setError(e?.message || '삭제 실패');
    }
  };

  return { submissions, loading, error, onDelete, reload: load };
}
