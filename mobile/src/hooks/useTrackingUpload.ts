import { useState } from 'react';
import { parseTrackingCsv, readFileAsEucKr, type ParsedTrackingRow } from '@/lib/csv';
import { postTrackingLog } from '@/lib/client/tracking';

export function useTrackingUpload() {
  const [rows, setRows] = useState<ParsedTrackingRow[]>([]);
  const [fileName, setFileName] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const parseFile = async (file: File) => {
    setParseError(null); setSaveMsg(null);
    setFileName(file.name);
    try {
      const text = await readFileAsEucKr(file);
      const parsed = parseTrackingCsv(text);
      if (parsed.length === 0) throw new Error('유효 데이터 없음');
      setRows(parsed);
    } catch (e: any) {
      setRows([]);
      setParseError(e.message || '파싱 실패');
    }
  };

  const save = async (dispatchDate: string, courier: string, platform: string) => {
    if (rows.length === 0) return;
    setSaving(true); setSaveMsg(null);
    try {
      const result = await postTrackingLog(
        rows.map(r => ({ orderNo: r.orderNo, trackingNo: r.trackingNo, courier, platform })),
        dispatchDate
      );
      let msg = `✅ ${result.saved}건 dispatch_log 저장 완료`;
      if (result.errors?.length) msg += `\n⚠️ ${result.errors.length}건 실패`;
      setSaveMsg(msg);
    } catch (e: any) {
      setSaveMsg('❌ ' + (e.message || '저장 실패'));
    } finally {
      setSaving(false);
    }
  };

  return { rows, fileName, parseError, saving, saveMsg, parseFile, save };
}
