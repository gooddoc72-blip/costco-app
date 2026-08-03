'use client';
/**
 * 🧾 영수증 등록 — PDF 업로드 → 파싱 → 매입가 반영.
 */
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { Upload, Save, RefreshCw, Camera } from 'lucide-react';
import { won } from '@/lib/fmt';
import { useReceipt } from '@/hooks/useReceipt';

export default function ReceiptPage() {
  const r = useReceipt();
  return (
    <>
      <Header title="🧾 영수증" subtitle="사진 촬영 · PDF 업로드 → 매입가 갱신" />
      <main className="px-4 pt-4 pb-32 space-y-3">

        {/* 📷 휴대폰 촬영 — capture 속성으로 카메라가 바로 열린다 */}
        <section className="bg-white rounded-xl p-3 border space-y-2">
          <div className="text-sm font-semibold flex items-center gap-1">
            <Camera size={14} /> 영수증 사진 촬영 (AI 판독)
          </div>
          <label className="block">
            <span className="sr-only">영수증 촬영</span>
            <input type="file" accept="image/*" capture="environment" multiple disabled={r.busy}
              onChange={e => { if (e.target.files) { r.onUploadPhotos(e.target.files); e.target.value = ''; } }}
              className="w-full text-xs border border-gray-200 rounded p-2 file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:bg-emerald-50 file:text-emerald-700" />
          </label>
          <p className="text-[11px] text-gray-500">
            영수증이 화면에 꽉 차게, 구겨짐 없이 찍어주세요. 여러 장 한번에 올릴 수 있습니다.
          </p>
          {r.busy && <p className="text-xs text-gray-500">⏳ AI 판독 중… (장당 10~30초)</p>}
        </section>

        <section className="bg-white rounded-xl p-3 border space-y-2">
          <div className="text-sm font-semibold flex items-center gap-1">
            <Upload size={14} /> PDF 다중 업로드
          </div>
          <input type="file" accept=".pdf" multiple disabled={r.busy}
            onChange={e => { if (e.target.files) { r.onUpload(e.target.files); e.target.value = ''; } }}
            className="w-full text-xs border border-gray-200 rounded p-2 file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:bg-blue-50 file:text-blue-700" />
          {r.busy && <p className="text-xs text-gray-500">⏳ 처리 중…</p>}
        </section>

        {r.error && <div className="bg-red-50 text-red-700 text-sm p-3 rounded-lg">❌ {r.error}</div>}

        {/* 사진 판독 자가검증 — 금액·수량이 영수증과 안 맞으면 값을 믿지 말 것 */}
        {r.checks && r.checks.length > 0 && (
          <section className="space-y-2">
            {r.checks.map((c, i) => (
              <div key={i}
                className={`text-xs p-3 rounded-lg border ${c.verified
                  ? 'bg-emerald-50 border-emerald-200 text-emerald-900'
                  : 'bg-red-50 border-red-200 text-red-900'}`}>
                <div className="font-semibold">
                  {c.verified ? '✔' : '⚠️'} {c.file}
                  <span className="ml-1 font-normal opacity-70">
                    ({c.provider === 'gemini' ? 'Gemini' : 'Claude'})
                  </span>
                </div>
                {c.verified && c.issues.length === 0
                  ? <div className="mt-1 opacity-80">금액·수량 자가검증 통과</div>
                  : <ul className="mt-1 list-disc list-inside space-y-0.5">
                      {c.issues.slice(0, 5).map((s, k) => <li key={k}>{s}</li>)}
                    </ul>}
              </div>
            ))}
          </section>
        )}

        {r.parsed && (
          <>
            {r.parsed.errors.length > 0 && (
              <details className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-xs">
                <summary className="font-semibold text-amber-800">⚠️ {r.parsed.errors.length}건 파싱 실패</summary>
                <ul className="mt-2 space-y-1">
                  {r.parsed.errors.map((e, i) => (
                    <li key={i} className="text-amber-900">
                      <b>{e.file}</b>: {e.error}
                      {e.preview && <pre className="text-[10px] text-gray-500 mt-1 whitespace-pre-wrap">{e.preview}</pre>}
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <section className="bg-white rounded-xl p-3 border">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm font-semibold">📋 파싱 결과 {r.parsed.items.length}건</div>
                <div className="flex gap-2">
                  <button onClick={r.reset} className="text-xs px-2 py-1 border rounded">
                    <RefreshCw size={11} className="inline mr-1" /> 초기화
                  </button>
                  <button onClick={r.onApply} disabled={r.busy || r.parsed.items.length === 0}
                    className="text-xs px-3 py-1 bg-green-600 text-white rounded disabled:bg-gray-300">
                    <Save size={11} className="inline mr-1" /> 매입가 반영
                  </button>
                </div>
              </div>
              <div className="max-h-80 overflow-y-auto">
                <table className="w-full text-[11px]">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr>
                      <th className="p-1 text-left">상품번호</th>
                      <th className="p-1 text-left">상품명</th>
                      <th className="p-1 text-right">수량</th>
                      <th className="p-1 text-right">단가</th>
                      <th className="p-1 text-left">영수증일</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.parsed.items.slice(0, 200).map((it, i) => (
                      <tr key={i} className="border-b">
                        <td className="p-1">{it.productNo}</td>
                        <td className="p-1 truncate max-w-[140px]" title={it.productName}>{it.productName}</td>
                        <td className="p-1 text-right">{it.qty}</td>
                        <td className="p-1 text-right">{won(it.unitPrice)}</td>
                        <td className="p-1 text-gray-500">{it.receiptDate || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}

        {r.applied && (
          <section className="bg-green-50 border border-green-200 rounded-xl p-3 text-xs space-y-2">
            <div className="font-semibold text-green-900">✅ 반영 완료</div>
            <div className="grid grid-cols-3 gap-2">
              <Stat label="업데이트" value={`${r.applied.updated}`} color="text-green-700" />
              <Stat label="거부 (박스 의심)" value={`${r.applied.rejected}`} color="text-amber-700" />
              <Stat label="미발견" value={`${r.applied.notFound}`} color="text-gray-600" />
            </div>
            {r.applied.warnings.length > 0 && (
              <details>
                <summary className="text-amber-700">⚠️ 경고 {r.applied.warnings.length}건</summary>
                <ul className="mt-1 text-[10px] text-amber-900">
                  {r.applied.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </details>
            )}
          </section>
        )}

        <div className="bg-blue-50 text-blue-800 text-xs p-3 rounded-lg">
          📌 product_no 기준 정확 매칭만 반영. 매입가가 기존의 5배 초과면 박스가격으로 의심해 거부됩니다.
          이름 매칭(fuzzy)은 향후 추가 예정.
        </div>
      </main>
      <BottomNav />
    </>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center bg-white rounded p-2">
      <div className="text-gray-500">{label}</div>
      <div className={`font-bold ${color || 'text-gray-900'}`}>{value}</div>
    </div>
  );
}
