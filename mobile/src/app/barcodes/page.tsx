'use client';
/**
 * 🧾 바코드 점검 (관리자) — 코스트코 상품번호 기준으로 바코드 두 종류가 제자리에 있는지 본다.
 *
 * 🏷 매장 바코드(가격표) = 상품 DB 바코드 칸, 가격 추이용.
 * 📦 상품 바코드(포장)   = 바코드 연결표, 재고·네이버 판매자 바코드용.
 * 규칙은 파이썬 barcode_core. 이 화면은 섞여 들어간 값을 보여 주고, 관리자가 고른 것만 고친다.
 */
import { useCallback, useEffect, useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { call } from '@/lib/client/billing';

type Suspect = { product_no: string; name: string; barcode: string; why: 'gtin' | 'odd' };
type LabelRow = { product_no: string; name: string; barcode: string; kind: string; store_barcode: string };
type Audit = { store_suspect: Suspect[]; label_rows: LabelRow[]; box_total: number };
type Act = 'to_box' | 'clear' | 'to_store' | '';

export default function BarcodeAuditPage() {
  const [me, setMe] = useState<{ is_admin: boolean } | null>(null);
  const [a, setA] = useState<Audit | null>(null);
  const [pick, setPick] = useState<Record<string, Act>>({});
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setErr('');
    call<Audit>('GET', '/api/scan/barcode-audit').then(r => {
      setA(r);
      const d: Record<string, Act> = {};
      r.store_suspect.forEach(x => { d[`s:${x.product_no}:${x.barcode}`] = x.why === 'gtin' ? 'to_box' : ''; });
      r.label_rows.forEach(x => { d[`l:${x.product_no}:${x.barcode}`] = 'to_store'; });
      setPick(d);
    }).catch(e => setErr(e.message));
  }, []);
  useEffect(() => {
    call<{ is_admin: boolean }>('GET', '/api/stock/me').then(setMe).catch(e => setErr(e.message));
  }, []);
  useEffect(() => { if (me?.is_admin) load(); }, [me, load]);

  const chosen = Object.entries(pick).filter(([, v]) => v);
  const apply = async () => {
    if (!chosen.length) return;
    if (!confirm(`${chosen.length}건을 정리할까요?`)) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const items = chosen.map(([k, action]) => {
        const [, product_no, barcode] = k.split(':');
        return { action, product_no, barcode };
      });
      const r = await call<{ done: number; skipped: { barcode: string; why: string }[] }>(
        'POST', '/api/scan/barcode-fix', { items });
      setMsg(`✅ ${r.done}건 정리` + (r.skipped.length ? ` · 건너뜀 ${r.skipped.length}건 (${r.skipped.map(s => `${s.barcode}: ${s.why}`).join(', ')})` : ''));
      load();
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  if (me && !me.is_admin) {
    return (<><Header title="🧾 바코드 점검" /><main className="p-4 text-sm text-gray-500">관리자만 쓸 수 있는 화면입니다.</main><BottomNav /></>);
  }

  const sel = (k: string, opts: [Act, string][]) => (
    <select value={pick[k] ?? ''} onChange={e => setPick(p => ({ ...p, [k]: e.target.value as Act }))}
      className="border border-gray-200 rounded px-1 py-1 text-xs">
      {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  );

  return (
    <>
      <Header title="🧾 바코드 점검" subtitle="매장 바코드(가격용)와 상품 바코드(재고용)를 제자리로" />
      <main className="px-3 md:px-6 pt-3 pb-24 space-y-3 text-sm">
        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg">❌ {err}</div>}
        {msg && <div className="bg-green-50 text-green-700 p-3 rounded-lg">{msg}</div>}

        <div className="bg-white rounded-xl border border-gray-100 p-3 text-xs text-gray-600 space-y-1">
          <div>🏷 <b>매장 바코드</b>: 가격표 바코드. 상품마다 1개, <b>가격 추이</b>에만 씁니다.</div>
          <div>📦 <b>상품 바코드</b>: 포장 바코드. 여러 개 가능, <b>재고·출고·네이버</b>에 씁니다.</div>
          {a && <div className="text-gray-400">지금 연결된 상품 바코드 {a.box_total}개</div>}
        </div>

        {!a && !err && <div className="text-xs text-gray-400">점검 중…</div>}

        {a && (
          <>
            <section className="space-y-2">
              <div className="font-bold text-gray-700">① 매장 바코드 칸에 이상한 값 {a.store_suspect.length}건</div>
              <p className="text-xs text-gray-500">
                📦 표시는 상품 포장 바코드로 보입니다. 상품 바코드로 옮기기를 권장합니다.
                ❓ 표시는 가격표를 잘못 읽었을 수 있으니 확인하세요.
              </p>
              {a.store_suspect.map(x => {
                const k = `s:${x.product_no}:${x.barcode}`;
                return (
                  <div key={k} className="bg-white rounded-xl border border-gray-100 p-3 flex items-center gap-2">
                    <span>{x.why === 'gtin' ? '📦' : '❓'}</span>
                    <div className="flex-1 min-w-0">
                      <div className="truncate font-medium">{x.name || '(이름 없음)'}</div>
                      <div className="text-xs text-gray-500">{x.product_no} · {x.barcode}</div>
                    </div>
                    {sel(k, [['', '그대로 둠'], ['to_box', '상품 바코드로 옮기기'], ['clear', '지우기']])}
                  </div>
                );
              })}
            </section>

            <section className="space-y-2">
              <div className="font-bold text-gray-700">② 상품 바코드 표에 들어간 매장 바코드 {a.label_rows.length}건</div>
              <p className="text-xs text-gray-500">예전 스캔에서 자동 연결된 가격표 바코드입니다. 매장 바코드 칸으로 옮기는 것을 권장합니다.</p>
              {a.label_rows.map(x => {
                const k = `l:${x.product_no}:${x.barcode}`;
                return (
                  <div key={k} className="bg-white rounded-xl border border-gray-100 p-3 flex items-center gap-2">
                    <span>🏷</span>
                    <div className="flex-1 min-w-0">
                      <div className="truncate font-medium">{x.name || '(이름 없음)'}</div>
                      <div className="text-xs text-gray-500">
                        {x.product_no} · {x.barcode}
                        {x.store_barcode && x.store_barcode !== x.barcode && ` · 매장 칸에는 이미 ${x.store_barcode}`}
                      </div>
                    </div>
                    {sel(k, [['', '그대로 둠'], ['to_store', '매장 바코드로 옮기기']])}
                  </div>
                );
              })}
            </section>

            {!a.store_suspect.length && !a.label_rows.length
              ? <div className="bg-green-50 text-green-700 p-3 rounded-lg">✅ 섞인 바코드가 없습니다.</div>
              : (
                <button disabled={busy || !chosen.length} onClick={apply}
                  className="w-full bg-blue-600 text-white rounded-lg py-3 font-medium disabled:opacity-40">
                  🔧 고른 {chosen.length}건 정리
                </button>
              )}
          </>
        )}
      </main>
      <BottomNav />
    </>
  );
}
