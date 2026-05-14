import { useState } from 'react';
import {
  parseReceipts, applyReceipts,
  type ReceiptItem, type ParseSummary, type ApplyResult,
} from '@/lib/client/receipt';

export function useReceipt() {
  const [parsed, setParsed] = useState<ParseSummary | null>(null);
  const [applied, setApplied] = useState<ApplyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onUpload = async (files: FileList) => {
    if (files.length === 0) return;
    setBusy(true); setError(null); setApplied(null);
    try {
      const r = await parseReceipts(Array.from(files));
      setParsed(r);
    } catch (e: any) {
      setError(e?.message);
    } finally { setBusy(false); }
  };

  const onApply = async () => {
    if (!parsed || parsed.items.length === 0) return;
    setBusy(true); setError(null);
    try {
      const r = await applyReceipts(parsed.items);
      setApplied(r);
    } catch (e: any) {
      setError(e?.message);
    } finally { setBusy(false); }
  };

  const reset = () => { setParsed(null); setApplied(null); setError(null); };

  return { parsed, applied, busy, error, onUpload, onApply, reset };
}
