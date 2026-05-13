'use client';
/**
 * 송장번호 등록 — 택배사 PIDPIC/접수 CSV 업로드 → dispatch_log 저장
 *
 * 단순 워크플로:
 *  1. 발송일 선택
 *  2. CSV 업로드 (주문번호, 송장번호 컬럼 자동 인식)
 *  3. 검토 후 일괄 등록 → dispatch_log
 */
import { useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { Upload, Save, Truck } from 'lucide-react';

interface ParsedRow {
  orderNo: string;
  trackingNo: string;
}

const COURIERS = ['CJ대한통운', '롯데택배', '한진택배', '우체국택배', '로젠택배'] as const;

function todayStr() { return new Date().toISOString().slice(0, 10); }

export default function TrackingPage() {
  const [dispatchDate, setDispatchDate] = useState(todayStr());
  const [courier, setCourier] = useState<typeof COURIERS[number]>('CJ대한통운');
  const [platform, setPlatform] = useState<'naver' | 'coupang'>('naver');
  const [rows, setRows] = useState<ParsedRow[]>([]);
  const [fileName, setFileName] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const parseFile = async (file: File) => {
    setParseError(null); setSaveMsg(null);
    setFileName(file.name);
    try {
      const text = await readAsText(file);
      const parsed = parseCSV(text);
      if (parsed.length === 0) throw new Error('유효 데이터 없음');
      setRows(parsed);
    } catch (e: any) {
      setRows([]);
      setParseError(e.message || '파싱 실패');
    }
  };

  const saveAll = async () => {
    if (rows.length === 0) return;
    setSaving(true); setSaveMsg(null);
    try {
      const res = await fetch('/api/tracking/log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: rows.map(r => ({
            orderNo: r.orderNo,
            trackingNo: r.trackingNo,
            courier,
            platform,
          })),
          dispatchDate,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || '저장 실패');
      setSaveMsg(`✅ ${json.saved}건 dispatch_log 저장 완료 — 수익계산에서 확인하세요`);
      if (json.errors?.length) {
        setSaveMsg(prev => prev + `\n⚠️ ${json.errors.length}건 실패`);
      }
    } catch (e: any) {
      setSaveMsg('❌ ' + (e.message || '저장 실패'));
    } finally { setSaving(false); }
  };

  return (
    <>
      <Header title="📮 송장번호 등록" subtitle="일괄발송 → dispatch_log 저장" />
      <main className="px-4 pt-4 pb-32 space-y-4">

        {/* 발송일 + 택배사 + 플랫폼 */}
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

        {/* 파일 업로드 */}
        <section className="bg-white rounded-xl p-4 shadow-sm border">
          <div className="flex items-center gap-2 mb-2 text-sm font-medium text-gray-900">
            <Upload size={16} /> 택배사 CSV 업로드
          </div>
          <input
            type="file"
            accept=".csv,.txt"
            onChange={(e) => e.target.files?.[0] && parseFile(e.target.files[0])}
            className="block w-full text-xs"
          />
          <p className="text-[10px] text-gray-500 mt-1">
            CSV에서 "주문번호" / "송장번호" 컬럼 자동 인식 (롯데 PIDPIC, CJ 접수내역 등 모두 지원)
          </p>
          {fileName && <div className="text-xs text-gray-600 mt-1">📄 {fileName}</div>}
          {parseError && <div className="text-xs text-red-600 mt-1">❌ {parseError}</div>}
          {rows.length > 0 && (
            <div className="text-xs text-green-700 mt-1">✅ {rows.length}건 인식</div>
          )}
        </section>

        {/* 파싱 결과 표 */}
        {rows.length > 0 && (
          <section className="bg-white rounded-xl p-3 shadow-sm border">
            <div className="text-sm font-medium text-gray-900 mb-2">검토 ({rows.length}건)</div>
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50">
                  <tr><th className="p-1 text-left">주문번호</th><th className="p-1 text-left">송장번호</th></tr>
                </thead>
                <tbody>
                  {rows.slice(0, 200).map((r, i) => (
                    <tr key={i} className="border-b border-gray-100">
                      <td className="p-1 font-mono">{r.orderNo}</td>
                      <td className="p-1 font-mono">{r.trackingNo}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length > 200 && <p className="text-[10px] text-gray-400 mt-1">({rows.length - 200}건 더 있음)</p>}
            </div>
          </section>
        )}

        {/* 저장 버튼 */}
        <div className="fixed bottom-16 left-0 right-0 p-3 bg-white border-t z-10">
          {saveMsg && <div className="text-sm mb-2 whitespace-pre-wrap">{saveMsg}</div>}
          <button onClick={saveAll}
            disabled={rows.length === 0 || saving}
            className="w-full bg-blue-600 text-white font-medium py-2.5 rounded-lg flex items-center justify-center gap-1 disabled:bg-gray-300">
            <Save size={16} /> {saving ? '저장 중...' : `${rows.length}건 dispatch_log 저장`}
          </button>
        </div>
      </main>
      <BottomNav />
    </>
  );
}

function readAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = reject;
    // EUC-KR / CP949 우선
    r.readAsText(file, 'euc-kr');
  });
}

function parseCSV(text: string): ParsedRow[] {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (lines.length < 2) return [];
  // 헤더에서 주문번호 / 송장번호 컬럼 인덱스 찾기
  const sep = lines[0].includes('\t') ? '\t' : ',';
  const headers = lines[0].split(sep).map(s => s.trim().replace(/^"|"$/g, ''));
  const ORDER_KWS = ['주문번호', '고객주문', 'order', '주문 번호'];
  const TRACK_KWS = ['운송장', '송장', 'tracking', '운송 장', '운송번호', 'waybill'];
  const findCol = (kws: string[]) => headers.findIndex(h =>
    kws.some(kw => h.toLowerCase().includes(kw.toLowerCase()))
  );
  const ordIdx = findCol(ORDER_KWS);
  const trkIdx = findCol(TRACK_KWS);
  if (ordIdx < 0 || trkIdx < 0 || ordIdx === trkIdx) {
    throw new Error(`주문번호/송장번호 컬럼을 못 찾았습니다. 발견된 컬럼: ${headers.join(', ')}`);
  }
  const rows: ParsedRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(sep).map(s => s.trim().replace(/^"|"$/g, '').replace(/-/g, ''));
    const o = cells[ordIdx];
    const t = cells[trkIdx];
    if (o && t && o.length > 5 && t.length > 5 && t !== 'nan') {
      rows.push({ orderNo: o, trackingNo: t });
    }
  }
  return rows;
}
