import { useState } from 'react';
import {
  parseReceipts, parseReceiptPhotos, applyReceipts,
  type ReceiptItem, type ParseSummary, type ApplyResult, type PhotoCheck,
} from '@/lib/client/receipt';

export function useReceipt() {
  const [parsed, setParsed] = useState<ParseSummary | null>(null);
  const [checks, setChecks] = useState<PhotoCheck[] | null>(null);
  const [applied, setApplied] = useState<ApplyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onUpload = async (files: FileList) => {
    if (files.length === 0) return;
    setBusy(true); setError(null); setApplied(null); setChecks(null);
    try {
      const r = await parseReceipts(Array.from(files));
      setParsed(r);
    } catch (e: any) {
      setError(e?.message);
    } finally { setBusy(false); }
  };

  /** 📷 휴대폰 촬영 사진 판독 (AI) — PDF와 결과 형태가 같아 이후 흐름은 공용 */
  const onUploadPhotos = async (files: FileList) => {
    if (files.length === 0) return;
    setBusy(true); setError(null); setApplied(null); setChecks(null);
    try {
      const r = await parseReceiptPhotos(Array.from(files));
      setParsed({ items: r.items, errors: r.errors });
      setChecks(r.checks || null);
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

  const reset = () => { setParsed(null); setApplied(null); setError(null); setChecks(null); };

  return { parsed, checks, applied, busy, error, onUpload, onUploadPhotos, onApply, reset };
}
