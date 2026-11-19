'use client';
/**
 * 🛍 사진으로 네이버 등록 — 가격표·바코드를 찍으면 상품을 찾아 판매가를 보여 주고,
 * 확인을 누르면 본인 스마트스토어에 올린다.
 *
 * 등록은 PC 장바구니 등록과 같은 서버 함수(register_one)라 카테고리 자동 판단·
 * 상세설명·등록 수량 집계가 똑같다. 1분 넘게 걸릴 수 있어 서버가 백그라운드로 돌리고
 * 화면은 2초마다 결과를 묻는다.
 */
import { useEffect, useRef, useState } from 'react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import BarcodeScanner from '@/components/scan/BarcodeScanner';
import { num } from '@/lib/fmt';
import { scan } from '@/lib/client/scan';
import { shrinkJpegB64 } from '@/lib/client/ptag';
import { nreg, NregPreview, NregQuota } from '@/lib/client/nreg';

type Cur =
  | { state: 'loading'; code: string }
  | { state: 'error'; code: string; error: string }
  | { state: 'done'; item: NregPreview };

interface Log { name: string; ok: boolean; msg: string }

export default function NaverRegisterPage() {
  const [st, setSt] = useState<{ has_key: boolean; quota: NregQuota } | null>(null);
  const [cur, setCur] = useState<Cur | null>(null);
  const [logs, setLogs] = useState<Log[]>([]);
  const [code, setCode] = useState('');
  const shown = useRef('');

  const loadStatus = () => nreg.status().then(setSt).catch(() => {});
  useEffect(() => { loadStatus(); }, []);

  async function find(c: string) {
    const d = c.replace(/\D/g, '');
    if (!d || shown.current === d) return;
    shown.current = d;
    setCur({ state: 'loading', code: d });
    try {
      setCur({ state: 'done', item: await nreg.preview(d) });
    } catch (e) {
      setCur({ state: 'error', code: d, error: (e as Error).message });
    }
  }

  const q = st?.quota;
  return (
    <>
      <Header title="🛍 네이버 상품등록" subtitle="가격표·바코드를 찍으면 바로 스토어에 올립니다" />
      <main className="px-4 pt-3 pb-20 space-y-3">
        {st && !st.has_key && (
          <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">
            네이버 커머스 API 키가 없습니다. 설정에서 먼저 넣어 주세요.
          </div>
        )}
        {q && (
          <div className="text-xs text-gray-500 px-1">
            등록 수량 {q.used}건{q.limit ? ` / 한도 ${q.limit}건 (남은 ${q.remaining}건)` : ' (한도 없음)'}
            {q.blocked && <b className="text-red-600"> — 한도 소진</b>}
          </div>
        )}

        <div className="bg-white rounded-xl border border-gray-100 p-3 space-y-2">
          <BarcodeScanner onCode={find} paused={cur?.state === 'loading'}
            onPhotoAi={async f => {
              const r = await scan.photoCode(f.name, await shrinkJpegB64(f));
              shown.current = '';
              return r.code;
            }} />
          <form className="flex gap-2" onSubmit={e => { e.preventDefault(); shown.current = ''; find(code); }}>
            <input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric"
              placeholder="상품번호·바코드 직접 입력" className="flex-1 border rounded-lg px-3 py-2 text-sm" />
            <button className="px-4 rounded-lg bg-gray-800 text-white text-sm">찾기</button>
          </form>
        </div>

        {cur?.state === 'loading' && <div className="text-sm text-gray-400 px-1">⏳ {cur.code} 찾는 중…</div>}
        {cur?.state === 'error' && (
          <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {cur.code} — {cur.error}</div>
        )}
        {cur?.state === 'done' && (
          <RegCard key={cur.item.product_no} it={cur.item} blocked={!st?.has_key || !!q?.blocked}
            onDone={(ok, msg) => {
              setLogs(l => [{ name: cur.item.name, ok, msg }, ...l].slice(0, 20));
              if (ok) { setCur(null); shown.current = ''; loadStatus(); }
            }} />
        )}

        {!!logs.length && (
          <section className="bg-white rounded-xl border border-gray-100 p-3">
            <h2 className="text-sm font-semibold text-gray-700 mb-2">이번에 올린 것</h2>
            <ul className="space-y-1 text-xs">
              {logs.map((l, i) => (
                <li key={i} className={l.ok ? 'text-green-700' : 'text-red-600'}>
                  {l.ok ? '✅' : '❌'} {l.name} — {l.msg}
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
      <BottomNav />
    </>
  );
}

function RegCard({ it, blocked, onDone }: {
  it: NregPreview; blocked: boolean; onDone: (ok: boolean, msg: string) => void;
}) {
  const [price, setPrice] = useState(String(it.sale_price || ''));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const p = parseInt(price.replace(/\D/g, ''), 10) || 0;
  const can = !blocked && !it.registered && it.has_image && p >= 1000 && !busy;

  async function go() {
    if (!can || !confirm(`${it.name}\n${num(p)}원으로 네이버에 등록할까요?`)) return;
    setBusy(true); setErr(''); setMsg('등록 중… (1분 정도 걸릴 수 있어요)');
    try {
      const { job } = await nreg.register(it.product_no, p);
      const poll = async () => {
        try {
          const j = await nreg.job(job);
          if (j.status === 'running') { timer.current = setTimeout(poll, 2000); return; }
          setBusy(false);
          if (j.status === 'ok') { setMsg(''); onDone(true, `#${j.origin_no} · ${num(p)}원 · ${j.msg}`); }
          else { setMsg(''); setErr(j.msg); onDone(false, j.msg); }
        } catch (e) { setBusy(false); setMsg(''); setErr((e as Error).message); }
      };
      timer.current = setTimeout(poll, 2000);
    } catch (e) {
      setBusy(false); setMsg(''); setErr((e as Error).message);
    }
  }

  return (
    <div className="bg-white rounded-xl border border-gray-100 p-3 space-y-3">
      <div className="flex gap-3">
        {it.image
          ? <img src={it.image} alt="" className="w-20 h-20 object-contain rounded-lg border" />
          : <div className="w-20 h-20 rounded-lg border bg-gray-50 text-[10px] text-gray-400 flex items-center justify-center">이미지 없음</div>}
        <div className="min-w-0">
          <div className="text-sm font-semibold text-gray-900">{it.name}</div>
          <div className="text-xs text-gray-500 mt-1">#{it.product_no} · 코스트코가 {num(it.cost)}원</div>
          {it.split_qty > 1 && (
            <div className="text-xs text-purple-700 mt-0.5">소분 ÷{it.split_qty} 상품 — 판매가를 꼭 확인하세요</div>
          )}
        </div>
      </div>

      {it.registered ? (
        <div className="bg-gray-50 text-gray-600 p-2 rounded-lg text-sm">이미 등록된 상품입니다 (#{it.registered})</div>
      ) : !it.has_image ? (
        <div className="bg-red-50 text-red-700 p-2 rounded-lg text-sm">상품 이미지가 없어 등록할 수 없습니다</div>
      ) : (
        <>
          <label className="block">
            <span className="text-xs text-gray-500">
              판매가 (원가+택배비 3,000원에 마진 {it.margin}%·수수료 반영 — 바꿔도 됩니다)
            </span>
            <input value={price} onChange={e => setPrice(e.target.value)} inputMode="numeric" disabled={busy}
              className="mt-1 w-full border rounded-lg px-3 py-2 text-lg font-bold" />
          </label>
          <button onClick={go} disabled={!can}
            className="w-full bg-green-600 text-white rounded-lg py-3 font-medium disabled:opacity-50">
            {busy ? '⏳ 등록 중…' : `🛍 ${num(p)}원으로 네이버 등록`}
          </button>
        </>
      )}
      {msg && <div className="text-xs text-gray-500">{msg}</div>}
      {err && <div className="bg-red-50 text-red-700 p-2 rounded-lg text-sm">❌ {err}</div>}
    </div>
  );
}
