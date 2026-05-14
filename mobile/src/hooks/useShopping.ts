import { useEffect, useState } from 'react';
import {
  fetchShopping, sendShopping, saveShopping, submitShoppingToAdmin,
  type ShoppingPageData, type ShoppingSendResponse,
  type SaveDailyResponse, type SubmitAdminResponse,
} from '@/lib/client/shopping';

export function useShopping(date: string) {
  const [data, setData] = useState<ShoppingPageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sendResult, setSendResult] = useState<ShoppingSendResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveDailyResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<SubmitAdminResponse | null>(null);

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

  const onSave = async () => {
    setSaving(true); setError(null); setSaveResult(null);
    try {
      const r = await saveShopping(date);
      setSaveResult(r);
    } catch (e: any) {
      setError(e?.message || '저장 실패');
    } finally {
      setSaving(false);
    }
  };

  const onSubmit = async () => {
    setSubmitting(true); setError(null); setSubmitResult(null);
    try {
      const r = await submitShoppingToAdmin(date);
      setSubmitResult(r);
      if (!r.ok) setError(r.error || '제출 실패');
    } catch (e: any) {
      setError(e?.message || '제출 실패');
    } finally {
      setSubmitting(false);
    }
  };

  return {
    data, loading, error,
    sending, sendResult, onSend,
    saving, saveResult, onSave,
    submitting, submitResult, onSubmit,
  };
}
