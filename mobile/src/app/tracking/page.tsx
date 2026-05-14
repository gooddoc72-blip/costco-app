'use client';
import { useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { Upload, Save } from 'lucide-react';
import { useTrackingUpload } from '@/hooks/useTrackingUpload';

const COURIERS = ['CJ대한통운', '롯데택배', '한진택배', '우체국택배', '로젠택배'] as const;

function todayStr() { return new Date().toISOString().slice(0, 10); }

export default function TrackingPage() {
  const [dispatchDate, setDispatchDate] = useState(todayStr());
  const [courier, setCourier] = useState<typeof COURIERS[number]>('CJ대한통운');
  const [platform, setPlatform] = useState<'naver' | 'coupang'>('naver');
  const u = useTrackingUpload();

  return (
    <>
      <Header title="📮 송장번호 등록" subtitle="일괄발송 → dispatch_log 저장" />
      <main className="px-4 pt-4 pb-32 space-y-4">

        <section className="bg-white rounded-xl p-4 shadow-sm border space-y-3">
          <div>
            <label className="block text-xs text-gray-600 mb-1">발송일</label>
            <input type="date" value={dispatchDate} onChange={e => setDispatchDate(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs text-gray-600 mb-1">택배사</label>
              <select value={courier} onChange={e => setCourier(e.target.value as any)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm">
                {COURIERS.map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">플랫폼</label>
              <select value={platform} onChange={e => setPlatform(e.target.value as any)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm">
                <option value="naver">네이버 스마트스토어</option>
                <option value="coupang">쿠팡 Wing</option>
              </select>
            </div>
          </div>
        </section>

        <section className="bg-white rounded-xl p-4 shadow-sm border">
          <div className="flex items-center gap-2 mb-2 text-sm font-medium text-gray-900">
            <Upload size={16} /> 택배사 CSV 업로드
          </div>
          <input type="file" accept=".csv,.txt"
            onChange={(e) => e.target.files?.[0] && u.parseFile(e.target.files[0])}
            className="block w-full text-xs" />
          <p className="text-[10px] text-gray-500 mt-1">
            "주문번호" / "송장번호" 컬럼 자동 인식 (롯데 PIDPIC, CJ 접수내역 등)
          </p>
          {u.fileName && <div className="text-xs text-gray-600 mt-1">📄 {u.fileName}</div>}
          {u.parseError && <div className="text-xs text-red-600 mt-1">❌ {u.parseError}</div>}
          {u.rows.length > 0 && <div className="text-xs text-green-700 mt-1">✅ {u.rows.length}건 인식</div>}
        </section>

        {u.rows.length > 0 && (
          <section className="bg-white rounded-xl p-3 shadow-sm border">
            <div className="text-sm font-medium text-gray-900 mb-2">검토 ({u.rows.length}건)</div>
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50">
                  <tr><th className="p-1 text-left">주문번호</th><th className="p-1 text-left">송장번호</th></tr>
                </thead>
                <tbody>
                  {u.rows.slice(0, 200).map((r, i) => (
                    <tr key={i} className="border-b border-gray-100">
                      <td className="p-1 font-mono">{r.orderNo}</td>
                      <td className="p-1 font-mono">{r.trackingNo}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {u.rows.length > 200 && <p className="text-[10px] text-gray-400 mt-1">({u.rows.length - 200}건 더 있음)</p>}
            </div>
          </section>
        )}

        <div className="fixed bottom-16 left-0 right-0 p-3 bg-white border-t z-10">
          {u.saveMsg && <div className="text-sm mb-2 whitespace-pre-wrap">{u.saveMsg}</div>}
          <button onClick={() => u.save(dispatchDate, courier, platform)}
            disabled={u.rows.length === 0 || u.saving}
            className="w-full bg-blue-600 text-white font-medium py-2.5 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300">
            <Save size={16} /> {u.saving ? '저장 중...' : `${u.rows.length}건 dispatch_log 저장`}
          </button>
        </div>
      </main>
      <BottomNav />
    </>
  );
}
