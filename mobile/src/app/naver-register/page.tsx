'use client';
/**
 * 📷 사진으로 네이버 신상품 등록 — PC '네이버 등록 › 제품사진(여러 장) + 가격사진으로
 * 신상품 등록'의 모바일판.
 *
 * ① 제품 사진(여러 장, 첫 장이 대표) ② 가격표 사진 ③ 라벨 사진(선택)
 * → 🔎 사진 분석 (상품명·코스트코가·상품번호·카테고리·SEO 상품명·판매가)
 * → 확인·수정 → 🛍 네이버 등록.
 *
 * 분석·판매가·등록은 PC와 같은 서버 함수(photo_register_service)다. 둘 다 1분 넘게
 * 걸릴 수 있어 서버가 백그라운드로 돌리고 화면은 2초마다 진행을 묻는다.
 */
import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { num } from '@/lib/fmt';
import { shrinkJpegB64 } from '@/lib/client/ptag';
import { nreg, waitJob, NregStatus, NregPreview, NregResult, Img } from '@/lib/client/nreg';

async function toImg(f: File, i: number): Promise<Img> {
  return { name: `p${i}.jpg`, content_b64: await shrinkJpegB64(f, 1600) };
}

function Thumb({ f, label, onDel, onLeft }: { f: File; label: string; onDel: () => void; onLeft?: () => void }) {
  const [u, setU] = useState('');
  useEffect(() => { const x = URL.createObjectURL(f); setU(x); return () => URL.revokeObjectURL(x); }, [f]);
  return (
    <div className="relative w-20 shrink-0">
      {u && <img src={u} alt="" className="w-20 h-20 object-cover rounded-lg border" />}
      <div className="text-[10px] text-center text-gray-500">{label}</div>
      <button onClick={onDel} className="absolute -top-1 -right-1 bg-black/70 text-white rounded-full w-5 h-5 text-xs">✕</button>
      {onLeft && <button onClick={onLeft} className="absolute bottom-5 left-0 bg-white/90 border rounded px-1 text-[10px]">◀ 앞으로</button>}
    </div>
  );
}

function PickBtn({ label, multiple, onPick }: { label: string; multiple?: boolean; onPick: (fs: File[]) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <label className="text-center border border-blue-300 text-blue-700 rounded-lg py-2 text-sm cursor-pointer">
        📸 {label} 찍기
        <input type="file" accept="image/*" capture="environment" className="hidden"
          onChange={e => { const fs = Array.from(e.target.files || []); e.target.value = ''; if (fs.length) onPick(fs); }} />
      </label>
      <label className="text-center border border-gray-300 text-gray-700 rounded-lg py-2 text-sm cursor-pointer">
        🖼 앨범에서
        <input type="file" accept="image/*" multiple={multiple} className="hidden"
          onChange={e => { const fs = Array.from(e.target.files || []); e.target.value = ''; if (fs.length) onPick(fs); }} />
      </label>
    </div>
  );
}

export default function NaverRegisterPage() {
  const [st, setSt] = useState<NregStatus | null>(null);
  const [prod, setProd] = useState<File[]>([]);
  const [price, setPrice] = useState<File | null>(null);
  const [label, setLabel] = useState<File | null>(null);
  const [pv, setPv] = useState<NregPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [done, setDone] = useState('');

  const loadStatus = () => nreg.status().then(setSt).catch(e => setErr(e.message));
  useEffect(() => { loadStatus(); }, []);

  async function analyze() {
    if (!prod.length || !price) return;
    setBusy(true); setErr(''); setDone(''); setPv(null); setMsg('사진 올리는 중…');
    try {
      const { job } = await nreg.analyze(await toImg(prod[0], 0), await toImg(price, 0),
        label ? await toImg(label, 0) : null);
      setPv(await waitJob<NregPreview>(job, setMsg));
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); setMsg(''); }
  }

  function reset(note: string) {
    setProd([]); setPrice(null); setLabel(null); setPv(null); setDone(note); loadStatus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  const q = st?.quota;
  const blocked = !!st && (!st.has_key || !st.has_ai || !!q?.blocked);
  return (
    <>
      <Header title="📷 사진으로 네이버 등록" subtitle="제품 사진 + 가격표 사진 → 신상품 등록" />
      <main className="px-4 pt-3 pb-20 space-y-3">
        {st && !st.has_key && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">네이버 커머스 API 키가 없습니다. 설정에서 먼저 넣어 주세요.</div>}
        {st && !st.has_ai && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">AI 키가 없어 사진을 분석할 수 없습니다. 관리자에게 공용 AI 키 설정을 요청하세요.</div>}
        {q && (
          <div className="text-xs text-gray-500 px-1">
            등록 수량 {q.used}건{q.limit ? ` / 한도 ${q.limit}건 (남은 ${q.remaining}건)` : ' (한도 없음)'}
            {q.blocked && <b className="text-red-600"> — 한도 소진</b>}
            {st?.delivery ? ` · 배송: ${st.delivery}` : ''}
          </div>
        )}
        {done && <div className="bg-green-50 text-green-800 p-3 rounded-lg text-sm">{done}</div>}

        <section className="bg-white rounded-xl border border-gray-100 p-3 space-y-2">
          <div className="text-sm font-semibold">① 제품 사진 (여러 장 — 첫 장이 대표이미지)</div>
          {!!prod.length && (
            <div className="flex gap-2 overflow-x-auto pb-1">
              {prod.map((f, i) => (
                <Thumb key={i + f.name + f.size} f={f} label={i ? String(i + 1) : '대표'}
                  onDel={() => setProd(p => p.filter((_, j) => j !== i))}
                  onLeft={i ? () => setProd(p => { const a = [...p]; [a[i - 1], a[i]] = [a[i], a[i - 1]]; return a; }) : undefined} />
              ))}
            </div>
          )}
          <PickBtn label="제품" multiple onPick={fs => setProd(p => [...p, ...fs].slice(0, 10))} />

          <div className="text-sm font-semibold pt-2">② 가격 사진 (코스트코 가격표)</div>
          {price && <Thumb f={price} label="가격표" onDel={() => setPrice(null)} />}
          {!price && <PickBtn label="가격표" onPick={fs => setPrice(fs[0])} />}

          <div className="text-sm font-semibold pt-2">③ 표시사항(라벨) 사진 <span className="font-normal text-gray-500">— 식품이면 선택</span></div>
          {label && <Thumb f={label} label="라벨" onDel={() => setLabel(null)} />}
          {!label && <PickBtn label="라벨" onPick={fs => setLabel(fs[0])} />}

          <button onClick={analyze} disabled={busy || blocked || !prod.length || !price}
            className="w-full mt-2 bg-primary text-white rounded-lg py-3 font-medium disabled:opacity-40">
            {busy && !pv ? '⏳ 분석 중…' : '🔎 사진 분석 (미리보기)'}
          </button>
        </section>

        {msg && <div className="text-sm text-gray-500 px-1">⏳ {msg}</div>}
        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}

        {pv && <PreviewForm key={pv.costco_no + pv.name} pv={pv} prod={prod} busy={busy} setBusy={setBusy}
          setMsg={setMsg} setErr={setErr} blocked={blocked}
          onDone={r => reset(`✅ 네이버 등록 완료 #${r.origin_no || '번호 미수신'} — ${r.name} / ${num(r.sale)}원 · 사진 ${r.images}장`
            + (r.tags ? ` · 태그 ${r.tags}개` : '') + (r.price_msg ? ` · ${r.price_msg}` : '')
            + (r.warning ? ` · ⚠️ ${r.warning}` : '') + ' — 다음 상품을 올리세요.')} />}
      </main>
      <BottomNav />
    </>
  );
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-gray-400 mt-0.5">{hint}</span>}
    </label>
  );
}
const inp = 'mt-1 w-full border rounded-lg px-3 py-2 text-sm';

function PreviewForm({ pv, prod, busy, setBusy, setMsg, setErr, blocked, onDone }: {
  pv: NregPreview; prod: File[]; busy: boolean; blocked: boolean;
  setBusy: (b: boolean) => void; setMsg: (m: string) => void; setErr: (e: string) => void;
  onDone: (r: NregResult) => void;
}) {
  const [name, setName] = useState(pv.name);
  const [vol, setVol] = useState(pv.volume);
  const [cno, setCno] = useState(pv.costco_no);
  const [cat, setCat] = useState({ id: pv.cat_id, full: pv.cat_full, src: pv.cat_src });
  const [ckw, setCkw] = useState('');
  const [cres, setCres] = useState<{ id: string; full_name: string }[]>([]);
  const [brand, setBrand] = useState(pv.brand);
  const [maker, setMaker] = useState(pv.manufacturer);
  const [model, setModel] = useState(pv.model_name);
  const [cost, setCost] = useState(pv.cost);
  const [margin, setMargin] = useState(pv.margin);
  const [cship, setCship] = useState(pv.customer_ship);
  const [sale, setSale] = useState(pv.sale);
  const [mi, setMi] = useState<{ margin: number; margin_rate: number } | null>(null);
  const [desc, setDesc] = useState('');
  const [aiDesc, setAiDesc] = useState(true);
  const [aiTags, setAiTags] = useState(true);
  const tm = useRef<ReturnType<typeof setTimeout> | null>(null);

  const base = { cost, ship_cost: pv.ship_cost, box_cost: pv.box_cost, customer_ship: cship, margin };
  // 마진율·매입가·고객배송비를 바꾸면 판매가를 다시 채운다 (PC 마진계산기와 같다)
  function recalcSale(over: Partial<typeof base>) {
    nreg.price({ ...base, ...over }).then(r => { setSale(r.sale); setMi(r); }).catch(() => {});
  }
  // 판매가를 직접 고치면 마진만 다시 본다
  useEffect(() => {
    if (tm.current) clearTimeout(tm.current);
    tm.current = setTimeout(() => {
      nreg.price({ ...base, sale }).then(r => setMi(r)).catch(() => {});
    }, 300);
    return () => { if (tm.current) clearTimeout(tm.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sale, cost, cship]);

  const volInName = !vol.trim() || name.replace(/\s/g, '').includes(vol.replace(/\s/g, ''));
  const can = !busy && !blocked && !!prod.length && !!name.trim() && !!vol.trim() && !!cno.trim()
    && !!cat.id.trim() && sale > 0;

  async function register() {
    if (!can || !confirm(`${name}\n${num(sale)}원으로 네이버에 등록할까요?`)) return;
    setBusy(true); setErr(''); setMsg('사진 올리는 중…');
    try {
      const images: Img[] = [];
      for (let i = 0; i < prod.length; i++) images.push({ name: `p${i}.jpg`, content_b64: await shrinkJpegB64(prod[i], 1600) });
      const { job } = await nreg.register({
        images, name: name.trim(), name_raw: pv.name_raw || pv.name, volume: vol.trim(), sale_price: sale,
        cost_tag: pv.cost_tag, customer_ship: cship, cat_id: cat.id.trim(), cat_full: cat.full,
        costco_no: cno.trim(), barcode: pv.barcode, brand, manufacturer: maker, model_name: model,
        desc, food: pv.food, ai_desc: aiDesc, ai_tags: aiTags,
      });
      onDone(await waitJob<NregResult>(job, setMsg));
    } catch (e) { setErr('등록 실패: ' + (e as Error).message); }
    finally { setBusy(false); setMsg(''); }
  }

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-3 space-y-3">
      <div className="text-sm font-semibold">미리보기 — 확인하고 고친 뒤 등록하세요</div>

      <Field label="상품명" hint={pv.name_raw ? `🔑 SEO 자동적용 (앞단 ${pv.seo_front.join(', ') || '-'}) · 사진 판독 원본: ${pv.name_raw}` : undefined}>
        <textarea value={name} onChange={e => setName(e.target.value)} rows={2} className={inp} />
      </Field>
      {pv.name_raw && (
        <button onClick={() => setName(name === pv.name_raw ? pv.name : pv.name_raw)} className="text-xs underline text-blue-700">
          {name === pv.name_raw ? '🔑 SEO명 적용' : '↩ 원본명으로'}
        </button>
      )}

      <div className="grid grid-cols-2 gap-2">
        <Field label="용량/중량 *"><input value={vol} onChange={e => setVol(e.target.value)} className={inp} placeholder="예: 1.5kg" /></Field>
        <Field label="코스트코 상품번호 *"><input value={cno} onChange={e => setCno(e.target.value)} inputMode="numeric" className={inp} /></Field>
      </div>
      {!vol.trim() && <div className="text-xs text-red-600">용량/중량은 필수입니다 — 상품명에 들어갑니다.</div>}
      {vol.trim() && !volInName && <div className="text-xs text-amber-700">상품명에 용량이 없어 등록할 때 뒤에 붙습니다.</div>}

      <div>
        <span className="text-xs text-gray-500">카테고리 *</span>
        <div className={clsx('mt-1 text-sm rounded-lg px-3 py-2', cat.id ? 'bg-gray-50' : 'bg-red-50 text-red-700')}>
          {cat.id ? <>📂 {cat.full || cat.id} <span className="text-[11px] text-gray-500">({cat.id}{cat.src ? ` · ${cat.src}` : ''})</span></>
            : '카테고리를 못 정했습니다 — 아래에서 검색해 고르세요'}
        </div>
        <form className="flex gap-2 mt-1" onSubmit={e => {
          e.preventDefault(); if (!ckw.trim()) return;
          nreg.categories(ckw).then(r => setCres(r.items)).catch(x => setErr(x.message));
        }}>
          <input value={ckw} onChange={e => setCkw(e.target.value)} placeholder="카테고리 검색 (예: 장갑, 우동)" className="flex-1 border rounded-lg px-3 py-1.5 text-sm" />
          <button className="px-3 rounded-lg border text-sm">🔍</button>
        </form>
        {!!cres.length && (
          <ul className="mt-1 border rounded-lg max-h-48 overflow-y-auto text-xs divide-y">
            {cres.map(c => (
              <li key={c.id}><button className="w-full text-left px-2 py-1.5"
                onClick={() => { setCat({ id: c.id, full: c.full_name, src: '직접 고름' }); setCres([]); setCkw(''); }}>
                {c.full_name} <span className="text-gray-400">{c.id}</span></button></li>
            ))}
          </ul>
        )}
      </div>

      <div className="border rounded-lg p-2 space-y-2">
        <div className="text-xs font-semibold">🧮 판매가</div>
        <div className="grid grid-cols-3 gap-2">
          <Field label={pv.cost_db ? '매입가 (가격 DB)' : '매입가 (가격표)'}>
            <input value={cost || ''} inputMode="numeric" className={inp}
              onChange={e => { const v = parseInt(e.target.value.replace(/\D/g, ''), 10) || 0; setCost(v); recalcSale({ cost: v }); }} />
          </Field>
          <Field label="마진율 %">
            <input value={margin} inputMode="decimal" className={inp}
              onChange={e => { const v = parseFloat(e.target.value) || 0; setMargin(v); recalcSale({ margin: v }); }} />
          </Field>
          <Field label="고객 배송비">
            <input value={cship} inputMode="numeric" className={inp}
              onChange={e => { const v = parseInt(e.target.value.replace(/\D/g, ''), 10) || 0; setCship(v); recalcSale({ customer_ship: v }); }} />
          </Field>
        </div>
        <Field label="네이버 판매가">
          <input value={sale || ''} inputMode="numeric" className={clsx(inp, 'text-lg font-bold')}
            onChange={e => setSale(parseInt(e.target.value.replace(/\D/g, ''), 10) || 0)} />
        </Field>
        <div className="text-xs text-gray-500">
          {pv.cost_db && pv.cost_tag && pv.cost_db !== pv.cost_tag ? `가격표 판독가 ${num(pv.cost_tag)}원 · ` : ''}
          택배비 {num(pv.ship_cost)} · 포장비 {num(pv.box_cost)} 반영
          {mi && <> · 마진 <b className={mi.margin < 0 ? 'text-red-600' : 'text-green-700'}>{num(mi.margin)}원 ({mi.margin_rate}%)</b></>}
          {mi && mi.margin < 0 && <span className="text-red-600"> — 역마진</span>}
        </div>
      </div>

      <details className="text-sm">
        <summary className="text-xs text-gray-600 cursor-pointer">🏷 브랜드·제조사·모델명 {brand ? `(${brand})` : ''}</summary>
        <div className="grid grid-cols-3 gap-2 mt-1">
          <Field label="브랜드"><input value={brand} onChange={e => setBrand(e.target.value)} className={inp} /></Field>
          <Field label="제조사"><input value={maker} onChange={e => setMaker(e.target.value)} className={inp} /></Field>
          <Field label="모델명"><input value={model} onChange={e => setModel(e.target.value)} className={inp} /></Field>
        </div>
      </details>

      {pv.food && Object.values(pv.food).some(Boolean) && (
        <div className="text-xs text-gray-600 bg-gray-50 rounded-lg p-2">🍱 라벨 정보가 상세페이지 표로 들어갑니다
          {pv.food.food_type ? ` · ${pv.food.food_type}` : ''}{pv.food.volume ? ` · ${pv.food.volume}` : ''}</div>
      )}

      <Field label="제품 설명 (비우면 AI가 사진을 보고 씁니다)">
        <textarea value={desc} onChange={e => setDesc(e.target.value)} rows={3} className={inp} />
      </Field>
      <div className="flex gap-4 text-sm">
        <label className="flex items-center gap-1"><input type="checkbox" checked={aiDesc} onChange={e => setAiDesc(e.target.checked)} /> AI 상세설명</label>
        <label className="flex items-center gap-1"><input type="checkbox" checked={aiTags} onChange={e => setAiTags(e.target.checked)} /> AI 연관태그</label>
      </div>

      <button onClick={register} disabled={!can}
        className="w-full bg-green-600 text-white rounded-lg py-3 font-medium disabled:opacity-40">
        {busy ? '⏳ 등록 중…' : `🛍 ${num(sale)}원으로 네이버 등록 (사진 ${prod.length}장)`}
      </button>
    </section>
  );
}
