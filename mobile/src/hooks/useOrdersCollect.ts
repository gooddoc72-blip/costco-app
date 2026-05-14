import { useState } from 'react';
import { collectOrders, type CollectResponse } from '@/lib/client/orders';

export function useOrdersCollect() {
  const [result, setResult] = useState<CollectResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const collect = async (hoursBack: number, statusFilter?: string[]) => {
    setLoading(true); setError(null);
    try {
      const r = await collectOrders(hoursBack, statusFilter);
      setResult(r);
    } catch (e: any) {
      setError(e.message || '실패');
    } finally {
      setLoading(false);
    }
  };

  return { result, loading, error, collect };
}
