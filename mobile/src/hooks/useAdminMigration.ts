import { useState } from 'react';
import { runMigration } from '@/lib/client/admin';

export function useAdminMigration() {
  const [results, setResults] = useState<Record<string, any> | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: string) => {
    setRunning(true); setError(null);
    try {
      const r = await runMigration(action);
      setResults(r.results);
    } catch (e: any) {
      setError(e.message || '실행 실패');
    } finally {
      setRunning(false);
    }
  };

  return { results, running, error, run };
}
