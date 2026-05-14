import { useEffect, useState } from 'react';
import { fetchDashboard, type DashboardData } from '@/lib/client/dashboard';

export function useDashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchDashboard()
      .then(d => { if (alive) setData(d); })
      .catch(e => { if (alive) setError(e?.message || '로드 실패'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  return { data, loading, error };
}
