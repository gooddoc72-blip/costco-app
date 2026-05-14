import { useEffect, useState } from 'react';
import { fetchShopping, sendShopping, type ShoppingPageData, type ShoppingSendResponse } from '@/lib/client/shopping';

export function useShopping(date: string) {
  const [data, setData] = useState<ShoppingPageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sendResult, setSendResult] = useState<ShoppingSendResponse | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null);
    fetchShopping(date)
      .then(d => { if (alive) setData(d); })
      .catch(e => { if (alive) setError(e?.message || '실패'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [date]);

  const onSend = async () => {
    setSending(true); setError(null); setSendResult(null);
    try {
      const r = await sendShopping(date);
      setSendResult(r);
      if (!r.ok) setError(r.error || '발송 실패');
    } catch (e: any) {
      setError(e?.message || '발송 실패');
    } finally {
      setSending(false);
    }
  };

  return { data, loading, error, sending, sendResult, onSend };
}
