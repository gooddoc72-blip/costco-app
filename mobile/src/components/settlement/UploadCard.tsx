/** CSV 업로드 카드 */
import { Upload } from 'lucide-react';
import { fmt } from '@/lib/fmt';
import type { UploadResult } from '@/lib/client/settlement';

interface Props {
  upload: UploadResult | null;
  loading: boolean;
  onFile: (f: File) => void;
}

export default function UploadCard({ upload, loading, onFile }: Props) {
  return (
    <section className="bg-white rounded-xl p-3 shadow-sm border space-y-2">
      <div className="text-sm font-semibold flex items-center gap-1">
        <Upload size={14} /> 네이버 정산 CSV 업로드
      </div>
      <p className="text-[11px] text-gray-500">스마트스토어 → 정산관리 → 빠른정산 건별 다운로드 (EUC-KR)</p>
      <label className="block">
        <input type="file" accept=".csv" disabled={loading}
          onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ''; }}
          className="w-full text-xs border border-gray-200 rounded p-2 file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:bg-blue-50 file:text-blue-700" />
      </label>
      {upload && (
        <div className="grid grid-cols-2 gap-2 text-xs pt-2">
          <Cell label="주문" value={`${upload.parsed}건`} hint={`빠른 ${upload.quickN} / 공제 ${upload.claimN}`} />
          <Cell label="DB 저장" value={`${upload.saved}건`} />
          <Cell label="상품 정산" value={`${fmt(upload.productSum)}원`} />
          <Cell label="배송 정산" value={`${fmt(upload.shippingSum)}원`} />
        </div>
      )}
    </section>
  );
}

function Cell({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-gray-50 rounded p-2">
      <div className="text-[10px] text-gray-500">{label}</div>
      <div className="font-bold text-gray-900">{value}</div>
      {hint && <div className="text-[9px] text-gray-400">{hint}</div>}
    </div>
  );
}
