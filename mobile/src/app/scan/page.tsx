'use client';
/**
 * 📷 바코드 스캔 — 매장 가격 확인 (이후 입고·출고·실사가 같은 화면에 붙는다).
 *
 * 바코드를 찍으면 파이썬 /api/scan이 상품을 찾는다. 한 상품에 바코드가 여러 개라
 * (가격표 바코드 ≠ 박스 바코드) 처음 보는 바코드는 한 번만 상품을 골라 연결한다.
 * - 아는 상품: 지난 매입가·재고를 보여 주고, 같으면 [✅ 가격 같음] 한 번, 바뀌었으면 정가만 입력.
 * - 처음 보는 바코드: 상품번호를 넣거나 가격표 사진 1장으로 판독(/m/pricetag와 같은 판독기).
 * 매장 가격 저장은 관리자만 — /m/pricetag의 '최근 저장'에서 되돌릴 수 있다.
 */
import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import BarcodeScanner from '@/components/scan/BarcodeScanner';
import { num } from '@/lib/fmt';
import { call } from '@/lib/client/billing';
import { scan, ScanItem, ScanFound } from '@/lib/client/scan';
import { ptag, PtRow, shrinkJpegB64 } from '@/lib/client/ptag';

type Mode = 'price' | 'in' | 'out' | 'count';
const MODES: { key: Mode; label: string; ready: boolean }[] = [
  { key: 'price', label: '🏷 가격 확인', ready: true },
  { key: 'out', label: '🚚 출고', ready: false },
  { key: 'in', label: '📦 입고', ready: false },
  { key: 'count', label: '📋 실사', ready: false },
];

type Cur =
  | { state: 'loading'; code: string }
  | { state: 'error'; code: string; error: string }
  | { state: 'done'; item: ScanItem };

interface Log { code: string; name: string; note: string; at: string }

export default function ScanPage() {
  const [me, setMe] = useState<{ username: string; is_admin: boolean } | null>(null);
  const [mode, setMode] = useState<Mode>('price');
  const [cur, setCur] = useState<Cur | null>(null);
  const [log, setLog] = useState<Log[]>([]);

  useEffect(() => {
    call<{ username: string; is_admin: boolean }>('GET', '/api/stock/me').then(setMe).catch(() => {});
  }, []);

  const addLog = (code: string, name: string, note: string) =>
    setLog(xs => [{ code, name, note, at: new Date().toTimeString().slice(0, 5) }, ...xs].slice(0, 20));

  const onCode = useCallback(async (code: string) => {
    setCur({ state: 'loading', code });
    try {
      const item = await scan.lookup(code);
      setCur({ state: 'done', item });
    } catch (e: any) {
      setCur({ state: 'error', code, error: e.message || String(e) });
    }
  }, []);

  return (
    <>
      <Header title="📷 바코드 스캔" subtitle="바코드를 찍으면 상품·매입가·재고를 바로 보여 줍니다" />
      <main className="px-3 md:px-6 pt-3 pb-24 space-y-3 max-w-xl mx-auto">
        <div className="grid grid-cols-4 gap-1 text-xs">
          {MODES.map(m => (
            <button key={m.key} disabled={!m.ready} onClick={() => setMode(m.key)}
              className={clsx('rounded-lg py-2 border',
                mode === m.key ? 'bg-gray-900 text-white border-gray-900' : 'bg-white border-gray-200',
                !m.ready && 'opacity-40')}>
              {m.label}{!m.ready && <div className="text-[10px]">준비 중</div>}
            </button>
          ))}
        </div>

        <div className="bg-white rounded-xl border border-gray-100 p-3">
          <BarcodeScanner onCode={onCode} paused={cur?.state === 'loading'} />
        </div>

        {cur?.state === 'loading' && <div className="text-sm text-gray-400 px-1">⏳ {cur.code} 찾는 중…</div>}
        {cur?.state === 'error' && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {cur.code} — {cur.error}</div>}
        {cur?.state === 'done' && cur.item.found && (
          <FoundCard key={cur.item.code + cur.item.product_no} it={cur.item} admin={!!me?.is_admin}
            onSaved={(it, note) => { setCur({ state: 'done', item: it }); addLog(it.code, it.name, note); }} />
        )}
        {cur?.state === 'done' && !cur.item.found && (
          <UnknownCard key={cur.item.code} code={cur.item.code} admin={!!me?.is_admin}
            onResolved={(it, note) => { setCur({ state: 'done', item: it }); addLog(it.code, it.found ? it.name : '', note); }} />
        )}

        {!!log.length && (
          <div className="bg-white rounded-xl border border-gray-100 p-3 text-xs">
            <div className="font-medium text-gray-700 mb-1">오늘 처리한 상품</div>
            {log.map((l, i) => (
              <div key={i} className="flex justify-between gap-2 py-1 border-t border-gray-50 first:border-0">
                <span className="truncate">{l.at} · {l.name || l.code}</span>
                <span className="text-gray-500 shrink-0">{l.note}</span>
              </div>
            ))}
          </div>
        )}
      </main>
      <BottomNav />
    </>
  );
}

function StockLine({ it }: { it: ScanFound }) {
  const s = it.stock;
  return (
    <div className="text-xs text-gray-600">
      📦 재고: 내 것 <b>{s.mine}</b>개 · 전체 <b>{s.total}</b>개
      {!!s.owners?.length && (
        <span className="text-gray-400"> ({s.owners.map(o => `${o.owner} ${o.qty}`).join(', ')})</span>
      )}
    </div>
  );
}

function FoundCard({ it, admin, onSaved }: {
  it: ScanFound; admin: boolean; onSaved: (it: ScanItem & { found: true }, note: string) => void;
}) {
  const [price, setPrice] = useState('');
  const [disc, setDisc] = useState('');
  const [end, setEnd] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const p = parseInt(price.replace(/\D/g, '') || '0', 10);
  const d = parseInt(disc.replace(/\D/g, '') || '0', 10);
  const pct = it.cur_price && p ? Math.round((p - it.cur_price) / it.cur_price * 1000) / 10 : null;

  const save = async (newPrice: number, same: boolean, discount = d, saleEnd = end) => {
    if (newPrice <= 0) return;
    const pc = it.cur_price ? Math.round((newPrice - it.cur_price) / it.cur_price * 1000) / 10 : null;
    if (!same && pc !== null && Math.abs(pc) > 30
        && !confirm(`지난 매입가 ${num(it.cur_price)}원보다 ${pc}% 바뀝니다. 맞나요?`)) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await scan.price({
        code: it.code, product_no: it.product_no, name: it.name, label_barcode: it.label_barcode,
        new_price: newPrice, final_price: discount ? Math.max(0, newPrice - discount) : newPrice,
        discount, sale_end: saleEnd,
      });
      const note = same || r.same ? '✅ 가격 같음' : `💰 ${num(it.cur_price)} → ${num(newPrice)}`;
      setMsg(note + (discount ? ` · 할인 ${num(discount)}원${saleEnd ? ` (~${saleEnd})` : ''} 기록` : ''));
      if (r.item.found) onSaved(r.item, note);
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  /**
   * 가격표 사진 → 가격 자동 수정. 바코드엔 가격이 없어서 가격은 사진(AI 판독)으로 읽는다.
   * 같은 상품번호이고 변동이 30% 이내면 바로 저장, 아니면 칸에 채워 두고 확인을 받는다.
   */
  const readTag = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true); setErr(''); setMsg('⏳ 가격표 판독 중…');
    let r: Awaited<ReturnType<typeof ptag.read>>;
    try {
      r = await ptag.read(f.name, await shrinkJpegB64(f));
    } catch (e: any) { setErr(e.message || String(e)); setMsg(''); setBusy(false); return; }
    setBusy(false); setMsg('');
    if (!r.ok) { setErr(r.error); return; }
    if (!r.new_price) { setErr('가격표에서 가격을 못 읽었습니다 — 다시 찍거나 직접 넣어 주세요'); return; }
    setPrice(String(r.new_price)); setDisc(r.discount ? String(r.discount) : ''); setEnd(r.sale_end || '');
    const other = r.product_no && r.product_no !== it.product_no;
    const pc = it.cur_price ? Math.abs((r.new_price - it.cur_price) / it.cur_price) : 0;
    if (other) {
      setErr(`사진의 상품번호(${r.product_no})가 찍은 상품(${it.product_no})과 다릅니다 — 가격만 칸에 넣었습니다. 맞으면 저장하세요.`);
      return;
    }
    if (pc > 0.3) {
      setErr(`읽은 정가 ${num(r.new_price)}원이 지난 매입가와 30% 넘게 다릅니다 — 칸을 확인하고 저장하세요.`);
      return;
    }
    await save(r.new_price, r.new_price === it.cur_price, r.discount || 0, r.sale_end || '');
  };

  return (
    <div className="bg-white rounded-xl border border-gray-100 p-3 space-y-2 text-sm">
      <div className="flex gap-3">
        {it.image && <img src={it.image} alt="" className="w-16 h-16 object-cover rounded border" />}
        <div className="min-w-0">
          <div className="font-medium leading-snug">{it.name}</div>
          <div className="text-xs text-gray-500">
            상품번호 {it.product_no}{it.split_qty > 1 ? ` · 소분 ${it.split_qty}` : ''}
            {it.code !== it.product_no ? ` · 찍은 바코드 ${it.code}` : ''}
          </div>
        </div>
      </div>
      {it.auto_linked && (
        <div className="bg-blue-50 text-blue-700 p-2 rounded text-xs">
          🔗 바코드 {it.code}를 상품번호 {it.product_no}에 자동 등록했습니다 — 다음부터 바로 찾습니다.
        </div>
      )}
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-gray-500">지난 매입가{it.price_at ? ` (${it.price_at} 확인)` : ''}</span>
        <span className="text-lg font-bold">{it.cur_price ? `${num(it.cur_price)}원` : '없음'}</span>
      </div>
      {!!it.online_price && <div className="text-xs text-gray-400 text-right">온라인 {num(it.online_price)}원</div>}
      <StockLine it={it} />

      {msg && <div className="bg-green-50 text-green-700 p-2 rounded text-xs">{msg}</div>}
      {err && <div className="bg-red-50 text-red-700 p-2 rounded text-xs">❌ {err}</div>}

      {admin ? (
        <div className="space-y-2 pt-1 border-t border-gray-100">
          {!!it.cur_price && (
            <button disabled={busy} onClick={() => save(it.cur_price, true)}
              className="w-full bg-green-600 text-white rounded-lg py-3 font-medium disabled:opacity-50">
              ✅ 가격 같음 ({num(it.cur_price)}원)
            </button>
          )}
          <label className={clsx('block text-center border border-blue-300 text-blue-700 rounded-lg py-2.5 cursor-pointer',
            busy && 'opacity-50 pointer-events-none')}>
            📸 가격표 찍어 가격 자동 수정
            <input type="file" accept="image/*" capture="environment" className="hidden"
              onChange={e => { readTag(e.target.files?.[0]); e.target.value = ''; }} />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <input value={price} onChange={e => setPrice(e.target.value)} inputMode="numeric"
              placeholder="바뀐 정가" className="border rounded-lg px-3 py-2" />
            <input value={disc} onChange={e => setDisc(e.target.value)} inputMode="numeric"
              placeholder="할인 금액 (선택)" className="border rounded-lg px-3 py-2" />
          </div>
          {!!d && (
            <input type="date" value={end} onChange={e => setEnd(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-xs" aria-label="할인 종료일" />
          )}
          {!!p && (
            <div className={clsx('text-xs', pct !== null && Math.abs(pct) > 30 ? 'text-amber-700' : 'text-gray-500')}>
              {num(it.cur_price)} → <b>{num(p)}</b>원{pct !== null ? ` (${pct > 0 ? '+' : ''}${pct}%)` : ''}
              {d ? ` · 할인가 ${num(Math.max(0, p - d))}원은 기록만` : ''}
            </div>
          )}
          <button disabled={busy || !p} onClick={() => save(p, false)}
            className="w-full bg-blue-600 text-white rounded-lg py-2.5 font-medium disabled:opacity-40">
            💾 정가 저장
          </button>
          <p className="text-[11px] text-gray-400">정가가 매입가가 됩니다. 할인가는 기간 한정이라 기록만 남깁니다.</p>
        </div>
      ) : (
        <p className="text-xs text-gray-400 border-t pt-2">매장 가격 저장은 관리자만 할 수 있습니다.</p>
      )}
    </div>
  );
}

function UnknownCard({ code, admin, onResolved }: {
  code: string; admin: boolean; onResolved: (it: ScanItem, note: string) => void;
}) {
  const [pno, setPno] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [row, setRow] = useState<PtRow | null>(null);

  const link = async () => {
    const p = pno.replace(/\D/g, '');
    if (!p) return;
    setBusy(true); setErr('');
    try {
      const it = await scan.link(code, p);
      onResolved(it, '🔗 바코드 연결');
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  const photo = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true); setErr(''); setRow(null);
    try {
      const r = await ptag.read(f.name, await shrinkJpegB64(f));
      if (!r.ok) throw new Error(r.error);
      if (!r.product_no) throw new Error('가격표에서 상품번호를 못 읽었습니다 — 다시 찍거나 번호를 넣어 주세요');
      setRow(r);
      setPno(r.product_no);
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  const saveRow = async () => {
    if (!row || !row.product_no || row.new_price <= 0) return;
    setBusy(true); setErr('');
    try {
      const r = await scan.price({
        code, product_no: row.product_no, name: row.name || row.tag_name, label_barcode: row.barcode,
        new_price: row.new_price, final_price: row.final_price, discount: row.discount, sale_end: row.sale_end,
      });
      onResolved(r.item, row.status === 'new' ? '🆕 신규 등록' : '🔗 연결 + 가격 저장');
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="bg-white rounded-xl border border-amber-200 p-3 space-y-2 text-sm">
      <div className="font-medium">❓ 처음 보는 바코드 <span className="font-mono">{code}</span></div>
      <p className="text-xs text-gray-500">어떤 상품인지 한 번만 알려 주면, 다음부터는 찍기만 해도 바로 나옵니다.</p>
      {err && <div className="bg-red-50 text-red-700 p-2 rounded text-xs">❌ {err}</div>}

      <div className="flex gap-2">
        <input value={pno} onChange={e => setPno(e.target.value)} inputMode="numeric"
          placeholder="코스트코 상품번호" className="flex-1 border rounded-lg px-3 py-2" />
        <button disabled={busy || !pno} onClick={link}
          className="border border-gray-300 rounded-lg px-3 disabled:opacity-40">🔗 연결</button>
      </div>

      {admin && (
        <label className={clsx('block text-center border border-dashed border-gray-300 rounded-lg py-2.5 cursor-pointer',
          busy && 'opacity-50 pointer-events-none')}>
          {busy && !row ? '⏳ 가격표 판독 중…' : '📷 가격표 사진으로 찾기 (상품번호·가격 자동)'}
          <input type="file" accept="image/*" capture="environment" className="hidden"
            onChange={e => { photo(e.target.files?.[0]); e.target.value = ''; }} />
        </label>
      )}

      {row && (
        <div className="bg-gray-50 rounded-lg p-2 space-y-1.5">
          <div className="font-medium">{row.name || row.tag_name}</div>
          <div className="text-xs text-gray-500">
            상품번호 {row.product_no}
            {row.status === 'new' ? ' · 🆕 제품 DB에 없는 상품 (새로 만듭니다)' : ` · 지난 매입가 ${num(row.cur_price)}원`}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500 shrink-0">정가</span>
            <input value={row.new_price || ''} inputMode="numeric"
              onChange={e => setRow({ ...row, new_price: parseInt(e.target.value.replace(/\D/g, '') || '0', 10) })}
              className="flex-1 border rounded px-2 py-1.5" />
            <span className="text-xs">원</span>
          </div>
          {!!row.discount && <div className="text-xs text-gray-500">할인 {num(row.discount)}원{row.sale_end ? ` (~${row.sale_end})` : ''} — 기록만</div>}
          <button disabled={busy || row.new_price <= 0} onClick={saveRow}
            className="w-full bg-blue-600 text-white rounded-lg py-2.5 font-medium disabled:opacity-40">
            💾 저장 + 이 바코드 연결
          </button>
        </div>
      )}
    </div>
  );
}
