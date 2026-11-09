'use client';
/**
 * 🏷 가격표 사진 → 공유 매입가 (관리자).
 *
 * 매장에서 가격표를 여러 장 찍으면 한 장씩 판독해(파이썬 /api/ptag — Streamlit 제품 DB의
 * '바코드·사진으로 매입가 수정'과 같은 판독기) 현재 매입가와 나란히 보여 준다.
 * 저장하는 값은 **정가**다 — 할인가는 기간 한정이라 매입가로 굳히지 않고 기록만 남긴다.
 * 30% 넘게 바뀐 줄은 판독 실수일 수 있어 기본으로 체크하지 않는다.
 * 저장한 묶음은 아래 '최근 저장'에서 통째로 되돌릴 수 있다.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { num } from '@/lib/fmt';
import { call } from '@/lib/client/billing';
import { ptag, PtRow, PtStatus, PtBatch, shrinkJpegB64 } from '@/lib/client/ptag';

type Item =
  | { key: string; photo: string; state: 'reading' }
  | { key: string; photo: string; state: 'fail'; error: string; file: File }
  | { key: string; photo: string; state: 'done'; row: PtRow; pick: boolean };

const LABEL: Record<PtStatus, [string, string]> = {
  change: ['가격 변경', 'bg-blue-50 text-blue-700'],
  same: ['변동 없음', 'bg-gray-100 text-gray-500'],
  suspect: ['확인 필요 (30% 넘게 변동)', 'bg-amber-50 text-amber-800'],
  new: ['신규 상품', 'bg-green-50 text-green-700'],
  no_pno: ['상품번호 못 읽음', 'bg-red-50 text-red-700'],
  no_price: ['가격 못 읽음', 'bg-red-50 text-red-700'],
};
const AUTO_PICK: PtStatus[] = ['change', 'new'];

export default function PriceTagPage() {
  const [me, setMe] = useState<{ username: string; is_admin: boolean } | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [hist, setHist] = useState<PtBatch[] | null>(null);
  const queue = useRef<{ key: string; file: File }[]>([]);
  const running = useRef(0);

  useEffect(() => {
    call<{ username: string; is_admin: boolean }>('GET', '/api/stock/me').then(setMe).catch(e => setErr(e.message));
  }, []);
  const loadHist = useCallback(() => {
    ptag.history().then(r => setHist(r.batches)).catch(e => setErr(e.message));
  }, []);
  useEffect(() => { if (me?.is_admin) loadHist(); }, [me, loadHist]);

  const patch = (key: string, f: (it: Item) => Item) => setItems(xs => xs.map(x => (x.key === key ? f(x) : x)));

  // 한 번에 두 장씩 판독 — 한 요청에 몰면 nginx 60초에 끊기고, 하나씩이면 느리다
  const pump = useCallback(() => {
    while (running.current < 2 && queue.current.length) {
      const job = queue.current.shift()!;
      running.current++;
      (async () => {
        try {
          const b64 = await shrinkJpegB64(job.file);
          const r = await ptag.read(job.file.name, b64);
          patch(job.key, it => (r.ok
            ? { key: it.key, photo: it.photo, state: 'done', row: r, pick: AUTO_PICK.includes(r.status) }
            : { key: it.key, photo: it.photo, state: 'fail', error: r.error, file: job.file }));
        } catch (e: any) {
          patch(job.key, it => ({ key: it.key, photo: it.photo, state: 'fail', error: e.message || String(e), file: job.file }));
        } finally {
          running.current--;
          pump();
        }
      })();
    }
  }, []);

  const add = (files: FileList | null) => {
    if (!files || !files.length) return;
    setMsg('');
    const fresh = Array.from(files).map((file, i) => ({ key: `${Date.now()}-${i}-${file.name}`, file }));
    setItems(xs => [...fresh.map(j => ({ key: j.key, photo: j.file.name, state: 'reading' as const })), ...xs]);
    queue.current.push(...fresh);
    pump();
  };
  const retry = (it: Extract<Item, { state: 'fail' }>) => {
    patch(it.key, x => ({ key: x.key, photo: x.photo, state: 'reading' }));
    queue.current.push({ key: it.key, file: it.file });
    pump();
  };

  const setRow = (key: string, p: Partial<PtRow>, pick?: boolean) =>
    patch(key, it => (it.state === 'done' ? { ...it, row: { ...it.row, ...p }, pick: pick ?? it.pick } : it));

  // 판독이 틀린 번호를 고치면 공유DB에서 그 상품을 다시 찾는다
  const fixNo = async (key: string, code: string) => {
    const c = code.replace(/\D/g, '');
    if (!c) return;
    try {
      const s = await ptag.lookup(c);
      patch(key, it => {
        if (it.state !== 'done') return it;
        const np = it.row.new_price;
        const pct = s.cur_price && np ? Math.round((np - s.cur_price) / s.cur_price * 1000) / 10 : null;
        const st: PtStatus = !np ? 'no_price' : s.cur_price === np ? 'same'
          : (pct !== null && Math.abs(pct) > 30) ? 'suspect' : 'change';
        return { ...it, row: { ...it.row, product_no: s.product_no, name: s.name, cur_price: s.cur_price,
                               online_price: s.online_price, change_pct: pct, status: st } };
      });
    } catch (e: any) {
      setRow(key, { product_no: c, cur_price: 0, change_pct: null, status: 'new' });
      setMsg(`ℹ️ ${c}는 공유DB에 없어 신규 상품으로 저장됩니다 — 상품명을 확인하세요.`);
    }
  };

  const done = items.filter((x): x is Extract<Item, { state: 'done' }> => x.state === 'done');
  const picked = done.filter(x => x.pick && x.row.product_no && x.row.new_price > 0);
  const reading = items.filter(x => x.state === 'reading').length;

  const save = async () => {
    if (!picked.length) return;
    const sus = picked.filter(x => x.row.status === 'suspect').length;
    if (!confirm(`${picked.length}건을 공유 매입가에 저장할까요?` + (sus ? `\n⚠️ 30% 넘게 바뀐 줄 ${sus}건이 포함돼 있습니다.` : ''))) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await ptag.save(picked.map(x => ({
        product_no: x.row.product_no, name: x.row.name, barcode: x.row.barcode, new_price: x.row.new_price,
        final_price: x.row.final_price, discount: x.row.discount, sale_end: x.row.sale_end, photo: x.photo })));
      const keys = new Set(picked.map(x => x.key));
      setItems(xs => xs.filter(x => !keys.has(x.key)));
      setMsg(`✅ ${r.saved}건 저장` + (r.same ? ` · 같은 값 ${r.same}건` : '')
        + (r.skipped.length ? ` · 건너뜀 ${r.skipped.length}건` : '') + ' — 아래 최근 저장에서 되돌릴 수 있습니다.');
      loadHist();
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  const undo = async (b: PtBatch) => {
    if (!confirm(`${b.created_at} 저장 ${b.count}건을 이전 매입가로 되돌릴까요?\n(새로 만든 상품은 지우지 않고 그대로 둡니다)`)) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await ptag.undo(b.batch);
      setMsg(`↩️ ${r.restored}건 되돌림` + (r.kept_new ? ` · 신규 상품 ${r.kept_new}건은 그대로 둠` : ''));
      loadHist();
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  if (me && !me.is_admin) {
    return (<><Header title="🏷 가격표 사진" /><main className="p-4 text-sm text-gray-500">관리자만 쓸 수 있는 화면입니다.</main><BottomNav /></>);
  }

  return (
    <>
      <Header title="🏷 가격표 사진 → 매입가" subtitle="매장 가격표를 찍으면 공유 매입가(정가)를 고칩니다" />
      <main className="px-3 md:px-6 pt-3 pb-24 space-y-3">
        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}
        {msg && <div className="bg-green-50 text-green-700 p-3 rounded-lg text-sm">{msg}</div>}

        <div className="bg-white rounded-xl border border-gray-100 p-3 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <label className="bg-blue-600 text-white text-sm font-medium rounded-lg py-3 text-center cursor-pointer">
              📷 가격표 찍기
              <input type="file" accept="image/*" capture="environment" className="hidden"
                onChange={e => { add(e.target.files); e.target.value = ''; }} />
            </label>
            <label className="border border-gray-300 text-sm font-medium rounded-lg py-3 text-center cursor-pointer">
              🖼 사진 여러 장 고르기
              <input type="file" accept="image/*" multiple className="hidden"
                onChange={e => { add(e.target.files); e.target.value = ''; }} />
            </label>
          </div>
          <p className="text-xs text-gray-500">
            가격표의 <b>좌측 상단 상품번호</b>와 <b>가격</b>이 선명하게 나오도록 가까이 찍어 주세요.
            할인 중이면 <b>정가</b>를 매입가로 저장하고, 할인가·종료일은 기록만 남깁니다.
          </p>
        </div>

        {!!items.length && (
          <div className="flex items-center justify-between text-xs text-gray-500 px-1">
            <span>{items.length}장{reading ? ` · 판독 중 ${reading}장` : ''} · 저장할 줄 {picked.length}건</span>
            <button onClick={() => setItems([])} className="underline">목록 비우기</button>
          </div>
        )}

        <div className="grid gap-2 lg:grid-cols-2">
          {items.map(it => (
            <div key={it.key} className="bg-white rounded-xl border border-gray-100 p-3 text-sm">
              {it.state === 'reading' && <div className="text-gray-400">⏳ {it.photo} 판독 중…</div>}
              {it.state === 'fail' && (
                <div className="flex items-center justify-between gap-2">
                  <span className="text-red-600 text-xs">⚠️ {it.photo} — {it.error}</span>
                  <button onClick={() => retry(it)} className="border rounded px-2 py-1 text-xs">🔄 다시</button>
                </div>
              )}
              {it.state === 'done' && <RowCard it={it} onPick={p => setRow(it.key, {}, p)}
                onPrice={v => setRow(it.key, { new_price: v })} onName={v => setRow(it.key, { name: v })}
                onNo={v => fixNo(it.key, v)} />}
            </div>
          ))}
        </div>

        {!!done.length && (
          <button disabled={busy || !picked.length || reading > 0} onClick={save}
            className="w-full bg-blue-600 text-white rounded-lg py-3 text-sm font-medium disabled:opacity-40">
            💾 체크한 {picked.length}건 공유 매입가에 저장{reading ? ' (판독이 끝나면)' : ''}
          </button>
        )}

        <div className="pt-4">
          <div className="text-sm font-bold text-gray-700 mb-2">🕘 최근 저장 (묶음째 되돌리기)</div>
          {!hist && <div className="text-xs text-gray-400">불러오는 중…</div>}
          {hist && !hist.length && <div className="text-xs text-gray-400">아직 저장한 기록이 없습니다.</div>}
          <div className="space-y-2">
            {hist?.map(b => (
              <details key={b.batch} className="bg-white rounded-xl border border-gray-100 p-3 text-xs">
                <summary className="flex items-center justify-between gap-2 cursor-pointer">
                  <span>{b.created_at} · {b.created_by} · {b.count}건 {b.undone && <span className="text-gray-400">(되돌림)</span>}</span>
                  {!b.undone && (
                    <button disabled={busy} onClick={e => { e.preventDefault(); undo(b); }}
                      className="border border-red-200 text-red-600 rounded px-2 py-1 disabled:opacity-40">↩️ 되돌리기</button>
                  )}
                </summary>
                <div className="mt-2 space-y-0.5">
                  {b.rows.map(r => (
                    <div key={r.product_no} className="flex justify-between gap-2 border-t border-gray-50 py-0.5">
                      <span className="truncate">{r.product_no} · {r.product_name}{r.was_new ? ' (신규)' : ''}</span>
                      <span className="whitespace-nowrap">{num(r.prev_price)} → {num(r.new_price)}원
                        {r.discount ? ` · 할인 ${num(r.discount)}${r.sale_end ? `~${r.sale_end.slice(5)}` : ''}` : ''}</span>
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </div>
        </div>
      </main>
      <BottomNav />
    </>
  );
}

function RowCard({ it, onPick, onPrice, onName, onNo }: {
  it: Extract<Item, { state: 'done' }>; onPick: (v: boolean) => void; onPrice: (v: number) => void;
  onName: (v: string) => void; onNo: (v: string) => void;
}) {
  const r = it.row;
  const [no, setNo] = useState(r.product_no);
  useEffect(() => setNo(r.product_no), [r.product_no]);
  const [lab, cls] = LABEL[r.status];
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={it.pick} disabled={!r.product_no || r.new_price <= 0}
          onChange={e => onPick(e.target.checked)} className="w-4 h-4" />
        <span className={clsx('px-2 py-0.5 rounded-full text-[11px]', cls)}>{lab}</span>
        <span className="text-[11px] text-gray-400 truncate">{it.photo}</span>
      </div>
      <div className="flex gap-2 items-center">
        <input value={no} onChange={e => setNo(e.target.value)} onBlur={() => no !== r.product_no && onNo(no)}
          inputMode="numeric" placeholder="상품번호" className="w-24 border border-gray-200 rounded px-2 py-1 text-xs" />
        {r.status === 'new' || !r.cur_price
          ? <input value={r.name} onChange={e => onName(e.target.value)} placeholder="상품명"
              className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs" />
          : <span className="flex-1 truncate font-medium">{r.name}</span>}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-gray-500">현재 {r.cur_price ? `${num(r.cur_price)}원` : '—'}</span>
        <span>→</span>
        <input type="number" value={r.new_price || ''} onChange={e => onPrice(Number(e.target.value) || 0)}
          className="w-28 border border-gray-200 rounded px-2 py-1 text-sm font-semibold" />
        <span className="text-xs text-gray-500">원 (정가)</span>
        {r.change_pct !== null && (
          <span className={clsx('text-xs', r.change_pct > 0 ? 'text-red-600' : 'text-blue-600')}>
            {r.change_pct > 0 ? '+' : ''}{r.change_pct}%
          </span>
        )}
      </div>
      {(r.discount > 0 || r.online_price > 0) && (
        <div className="text-xs text-gray-500">
          {r.discount > 0 && <>🏷 할인 중: 최종가 {num(r.final_price)}원 (−{num(r.discount)}){r.sale_end && ` · ${r.sale_end}까지`} · 매입가에는 정가 저장 </>}
          {r.online_price > 0 && <>· 온라인가 {num(r.online_price)}원</>}
        </div>
      )}
    </div>
  );
}
