'use client';
/**
 * 📷 사진으로 네이버 신상품 등록 — PC '네이버 등록 › 제품사진(여러 장) + 가격사진으로
 * 신상품 등록'의 모바일판.
 *
 * ① 제품 사진(여러 장, 첫 장이 대표) ② 가격표 사진 ③ 라벨 사진(선택)
 * → 사진이 갖춰지면 자동 분석(상품명·상품번호·카테고리·SEO 상품명·판매가·
 *   한글표시사항(코스트코 사이트 → 없으면 라벨)·상세설명·연관태그)
 * → 확인·수정 → 🛍 네이버 등록(이것만 사용자가 누른다).
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
import { nreg, waitJob, NregStatus, NregPreview, NregResult, Img, Tag, NregPresets, Delivery, Benefits } from '@/lib/client/nreg';

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

function blockedNow(st: NregStatus) {
  return !st.has_key || !st.has_ai || !!st.quota?.blocked;
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

  // 사진만 올리면 끝까지 자동 — 제품·가격표가 갖춰지면 2초 뒤(라벨 추가 여유) 분석 시작.
  // 분석이 상품명·카테고리·판매가·한글표시사항·상세설명·연관태그까지 다 채운다. 등록만 확인.
  const autoKey = useRef('');
  useEffect(() => {
    if (!prod.length || !price || pv || busy || !st || blockedNow(st)) return;
    const k = `${prod[0].name}${prod[0].size}|${price.name}${price.size}`;
    if (autoKey.current === k) return;
    const t = setTimeout(() => { autoKey.current = k; analyze(); }, 2000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prod, price, label, pv, busy, st]);

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
    autoKey.current = '';
    setProd([]); setPrice(null); setLabel(null); setPv(null); setDone(note); loadStatus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  const q = st?.quota;
  const blocked = !!st && blockedNow(st);
  return (
    <>
      <Header title="📷 사진으로 네이버 등록" subtitle="사진만 올리면 자동으로 채웁니다 — 등록만 확인" />
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

          <p className="text-xs text-gray-500 pt-1">
            제품·가격표 사진이 갖춰지면 <b>자동으로</b> 상품명·카테고리·판매가·한글표시사항·상세설명·태그까지 만듭니다.
          </p>
          {pv && !busy && (
            <button onClick={() => { setPv(null); analyze(); }} disabled={blocked || !prod.length || !price}
              className="w-full mt-1 border rounded-lg py-2 text-sm disabled:opacity-40">🔄 처음부터 다시 분석</button>
          )}
        </section>

        {msg && <div className="text-sm text-gray-500 px-1">⏳ {msg}</div>}
        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}

        {pv && <PreviewForm key={pv.costco_no + pv.name} pv={pv} prod={prod} labelFile={label} busy={busy} setBusy={setBusy}
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

function PreviewForm({ pv, prod, labelFile, busy, setBusy, setMsg, setErr, blocked, onDone }: {
  pv: NregPreview; prod: File[]; labelFile: File | null; busy: boolean; blocked: boolean;
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
  const [desc, setDesc] = useState(pv.desc || '');
  const [rows, setRows] = useState<[string, string][]>(pv.spec_rows || []);
  const [rowsSrc, setRowsSrc] = useState<string>(pv.spec_src || '');
  const [food, setFood] = useState(pv.food);
  const [tags, setTags] = useState<Tag[]>(pv.tags || []);
  const [newTag, setNewTag] = useState('');
  const [sub, setSub] = useState('');          // 상세설명·태그·라벨 다시 만들기 진행 중 표시
  const [note, setNote] = useState([pv.label_err, pv.desc_err, pv.tags_err].filter(Boolean).join(' · '));
  // 🚚 배송 설정·🎁 혜택 — 저장된 기본값으로 시작, 이 건만 바꿀 수 있다(PC 사진등록과 같다)
  const [ps, setPs] = useState<NregPresets | null>(null);
  const [dlv, setDlv] = useState<Delivery | null>(null);
  const [bn, setBn] = useState<Benefits>({});
  useEffect(() => {
    nreg.presets().then(r => { setPs(r); setDlv(r.delivery); setBn(r.benefits); })
      .catch(e => setNote('배송·혜택 설정을 못 불러왔습니다: ' + (e as Error).message));
  }, []);

  // 분석이 끝난 뒤 라벨 사진을 넣으면 그 자리에서 읽어 표를 채운다(코스트코 표가 없을 때)
  const labelSeen = useRef<File | null>(null);
  useEffect(() => {
    if (!labelFile || labelSeen.current === labelFile) return;
    labelSeen.current = labelFile;
    if (pv.food) return;                       // 분석 때 이미 읽음
    (async () => {
      setSub('🍱 라벨 읽는 중…');
      try {
        const { job } = await nreg.label(await toImg(labelFile, 0));
        const r = await waitJob<{ food: Record<string, string>; rows: [string, string][] }>(job, () => {});
        setFood(r.food);
        if (rowsSrc !== 'costco' || !rows.length) { setRows(r.rows); setRowsSrc('label'); }
        if (r.food.volume && !vol.trim()) setVol(r.food.volume);
      } catch (e) { setNote((e as Error).message); }
      finally { setSub(''); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labelFile]);

  async function redoDesc() {
    setSub('📝 상세설명 쓰는 중…'); setNote('');
    try {
      const { job } = await nreg.desc(await toImg(prod[0], 0), name, cat.full);
      setDesc((await waitJob<{ desc: string }>(job, () => {})).desc);
    } catch (e) { setNote((e as Error).message); }
    finally { setSub(''); }
  }
  async function redoTags() {
    setSub('🏷 태그 만드는 중…'); setNote('');
    try {
      const { job } = await nreg.tags(name, cat.full);
      setTags((await waitJob<{ tags: Tag[] }>(job, () => {})).tags);
    } catch (e) { setNote((e as Error).message); }
    finally { setSub(''); }
  }
  async function costcoSpec() {
    if (!cno.trim()) return;
    setSub('🌐 코스트코 표시사항 가져오는 중…'); setNote('');
    try { const r = await nreg.spec(cno.trim()); setRows(r.rows); setRowsSrc('costco'); }
    catch (e) { setNote((e as Error).message); }
    finally { setSub(''); }
  }
  function addTag() {
    const t = newTag.trim().replace(/^#/, '');
    if (!t || tags.length >= 10 || tags.some(x => x.text === t)) return;
    setTags([...tags, { code: null, text: t }]); setNewTag('');
  }
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
  const can = !busy && !sub && !blocked && !!prod.length && !!name.trim() && !!vol.trim() && !!cno.trim()
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
        desc, food, spec_rows: rows.filter(([k, v]) => k.trim() && v.trim()),
        tags: tags.map(t => ({ code: t.code, text: t.text })), ai_desc: true, ai_tags: true,
        delivery: dlv, benefits: ps ? bn : null,
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
              onChange={e => {
                const v = parseInt(e.target.value.replace(/\D/g, ''), 10) || 0; setCship(v); recalcSale({ customer_ship: v });
                // 계산기의 고객 배송비를 실제 등록 배송비에도 반영 (PC와 같다)
                setDlv(d => d && ({ ...d, base_fee: v,
                  fee_type: v <= 0 ? 'FREE' : (d.fee_type === 'FREE' ? 'PAID' : d.fee_type) }));
              }} />
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

      {ps && dlv && <DeliveryBox ps={ps} d={dlv} onChange={nd => {
        setDlv(nd);
        const cs = nd.fee_type === 'FREE' ? 0 : nd.base_fee;   // 배송비가 바뀌면 판매가도 다시
        if (cs !== cship) { setCship(cs); recalcSale({ customer_ship: cs }); }
      }} />}
      {ps && <BenefitBox ps={ps} b={bn} onChange={setBn} />}

      <details className="text-sm">
        <summary className="text-xs text-gray-600 cursor-pointer">🏷 브랜드·제조사·모델명 {brand ? `(${brand})` : ''}</summary>
        <div className="grid grid-cols-3 gap-2 mt-1">
          <Field label="브랜드"><input value={brand} onChange={e => setBrand(e.target.value)} className={inp} /></Field>
          <Field label="제조사"><input value={maker} onChange={e => setMaker(e.target.value)} className={inp} /></Field>
          <Field label="모델명"><input value={model} onChange={e => setModel(e.target.value)} className={inp} /></Field>
        </div>
      </details>

      {sub && <div className="text-xs text-blue-700 bg-blue-50 rounded-lg p-2">⏳ {sub}</div>}
      {note && <div className="text-xs text-amber-800 bg-amber-50 rounded-lg p-2">⚠️ {note}</div>}

      {/* 📝 상세설명 */}
      <div className="border rounded-lg p-2 space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold">📝 상세설명 <span className="font-normal text-gray-500">(상세페이지 상품명 아래)</span></span>
          <button onClick={redoDesc} disabled={!!sub || busy} className="text-xs border rounded px-2 py-1 disabled:opacity-40">🤖 다시 쓰기</button>
        </div>
        <textarea value={desc} onChange={e => setDesc(e.target.value)} rows={5} className={inp}
          placeholder="비워 두면 등록할 때 AI가 사진을 보고 씁니다" />
      </div>

      {/* 🇰🇷 한글표시사항 */}
      <div className="border rounded-lg p-2 space-y-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold">🇰🇷 한글표시사항 <span className="font-normal text-gray-500">
            ({rowsSrc === 'costco' ? '코스트코 사이트' : rowsSrc === 'label' ? '라벨 사진 판독' : '없음'} · 상세페이지 표)</span></span>
          <button onClick={costcoSpec} disabled={!!sub || busy || !cno.trim()} className="text-xs border rounded px-2 py-1 disabled:opacity-40">🌐 코스트코에서</button>
        </div>
        {!rows.length && <div className="text-xs text-gray-500">코스트코 사이트에 표시사항이 없습니다. 위 ③에 라벨 사진을 넣으면 읽어서 채웁니다.</div>}
        {rows.map(([k, v], i) => (
          <div key={i} className="flex gap-1 items-start">
            <input value={k} onChange={e => setRows(rs => rs.map((r, j) => j === i ? [e.target.value, r[1]] : r))}
              className="w-24 shrink-0 border border-gray-200 rounded px-1.5 py-1 text-xs bg-gray-50" />
            <textarea value={v} rows={Math.min(4, Math.max(1, Math.ceil(v.length / 28)))}
              onChange={e => setRows(rs => rs.map((r, j) => j === i ? [r[0], e.target.value] : r))}
              className="flex-1 border border-gray-200 rounded px-1.5 py-1 text-xs" />
            <button onClick={() => setRows(rs => rs.filter((_, j) => j !== i))} className="text-xs text-gray-400 px-1">✕</button>
          </div>
        ))}
        <button onClick={() => setRows(rs => [...rs, ['', '']])} className="text-xs underline text-blue-700">+ 항목 추가</button>
      </div>

      {/* 🏷 연관태그 */}
      <div className="border rounded-lg p-2 space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold">🏷 연관태그 <span className="font-normal text-gray-500">({tags.length}/10 · 검색 노출용)</span></span>
          <button onClick={redoTags} disabled={!!sub || busy} className="text-xs border rounded px-2 py-1 disabled:opacity-40">🤖 다시 만들기</button>
        </div>
        <div className="flex flex-wrap gap-1">
          {tags.map(t => (
            <span key={t.text} className={clsx('text-xs rounded-full px-2 py-0.5 flex items-center gap-1',
              t.code ? 'bg-green-50 text-green-800' : 'bg-gray-100 text-gray-700')}>
              #{t.text}{t.volume ? <span className="text-[10px] text-gray-500">{num(t.volume)}</span> : null}
              <button onClick={() => setTags(ts => ts.filter(x => x.text !== t.text))} className="text-gray-400">✕</button>
            </span>
          ))}
          {!tags.length && <span className="text-xs text-gray-500">비워 두면 등록할 때 AI가 만듭니다</span>}
        </div>
        <form className="flex gap-1" onSubmit={e => { e.preventDefault(); addTag(); }}>
          <input value={newTag} onChange={e => setNewTag(e.target.value)} placeholder="직접 추가 (예: 캠핑간식)"
            className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs" />
          <button disabled={tags.length >= 10} className="text-xs border rounded px-2 disabled:opacity-40">추가</button>
        </form>
        <div className="text-[11px] text-gray-400">초록 = 네이버 태그사전에 있는 태그(검색 반영), 회색 = 직접 입력</div>
      </div>

      <button onClick={register} disabled={!can}
        className="w-full bg-green-600 text-white rounded-lg py-3 font-medium disabled:opacity-40">
        {busy ? '⏳ 등록 중…' : `🛍 ${num(sale)}원으로 네이버 등록 (사진 ${prod.length}장)`}
      </button>
    </section>
  );
}

const numIn = (s: string) => parseInt(s.replace(/\D/g, ''), 10) || 0;

function dlvLabel(ps: NregPresets, d: Delivery) {
  const t = ps.fee_types.find(x => x.code === d.fee_type)?.label || d.fee_type;
  const c = ps.companies.find(x => x.code === d.company)?.label || d.company;
  let s = t;
  if (d.fee_type === 'UNIT_QUANTITY_PAID') s += ` ${num(d.base_fee)}원 / ${d.repeat_quantity}개마다`;
  else if (d.fee_type === 'CONDITIONAL_FREE') s += ` ${num(d.base_fee)}원 (${num(d.free_conditional_amount)}원 이상 무료)`;
  else if (d.fee_type !== 'FREE') s += ` ${num(d.base_fee)}원`;
  return `${s} · ${c}`;
}

/** 🚚 배송 설정 — 저장된 기본값(PC 상품DB '배송 설정'과 같은 값)에서 시작, 이 건만 바꾼다. */
function DeliveryBox({ ps, d, onChange }: { ps: NregPresets; d: Delivery; onChange: (d: Delivery) => void }) {
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const set = (p: Partial<Delivery>) => {
    const nd = { ...d, ...p };
    if (nd.fee_type !== 'FREE' && nd.base_fee < 10) nd.base_fee = 3000;   // 네이버 최소 10원
    onChange(nd); setMsg('');
  };
  async function save() {
    setSaving(true);
    try { await nreg.savePreset('delivery', d); setMsg('✅ 기본 배송 설정으로 저장했습니다 — 모든 등록 경로에 적용'); }
    catch (e) { setMsg('저장 실패: ' + (e as Error).message); }
    finally { setSaving(false); }
  }
  return (
    <details className="border rounded-lg p-2 text-sm">
      <summary className="text-xs font-semibold cursor-pointer">🚚 배송 설정 <span className="font-normal text-gray-500">· {dlvLabel(ps, d)}</span></summary>
      <div className="space-y-2 mt-2">
        <div className="text-[11px] text-gray-500">기본값은 저장된 배송 설정입니다. 여기서 바꾸면 <b>이번 등록 건에만</b> 적용됩니다.</div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="배송비 유형">
            <select value={d.fee_type} onChange={e => set({ fee_type: e.target.value })} className={inp}>
              {ps.fee_types.map(t => <option key={t.code} value={t.code}>{t.label}</option>)}
            </select>
          </Field>
          <Field label="택배사">
            <select value={d.company} onChange={e => set({ company: e.target.value })} className={inp}>
              {ps.companies.map(c => <option key={c.code} value={c.code}>{c.label}</option>)}
            </select>
          </Field>
        </div>
        {d.fee_type !== 'FREE' && (
          <div className="grid grid-cols-2 gap-2">
            <Field label="구매자 부담 배송비">
              <input value={d.base_fee} inputMode="numeric" className={inp} onChange={e => onChange({ ...d, base_fee: numIn(e.target.value) })} />
            </Field>
            {d.fee_type === 'UNIT_QUANTITY_PAID' && (
              <Field label="반복부과 수량" hint="이 수량마다 배송비를 다시 부과">
                <input value={d.repeat_quantity} inputMode="numeric" className={inp} onChange={e => set({ repeat_quantity: Math.max(1, numIn(e.target.value)) })} />
              </Field>
            )}
            {d.fee_type === 'CONDITIONAL_FREE' && (
              <Field label="무료 조건금액">
                <input value={d.free_conditional_amount} inputMode="numeric" className={inp} onChange={e => set({ free_conditional_amount: numIn(e.target.value) })} />
              </Field>
            )}
          </div>
        )}
        <div className="grid grid-cols-2 gap-2">
          <Field label="반품 배송비"><input value={d.return_fee} inputMode="numeric" className={inp} onChange={e => set({ return_fee: numIn(e.target.value) })} /></Field>
          <Field label="교환 배송비"><input value={d.exchange_fee} inputMode="numeric" className={inp} onChange={e => set({ exchange_fee: numIn(e.target.value) })} /></Field>
        </div>
        {d.fee_type === 'FREE' && <div className="text-xs text-amber-700">⚠️ 무료배송입니다 — 판매가에 택배비가 들어 있지 않으면 판매 건마다 택배비만큼 손해입니다.</div>}
        {d.fee_type !== 'FREE' && d.base_fee < 10 && <div className="text-xs text-red-600">구매자 부담 배송비는 10원 이상이어야 합니다.</div>}
        <button onClick={save} disabled={saving} className="text-xs border rounded px-2 py-1 disabled:opacity-40">💾 이 설정을 기본 배송 설정으로 저장</button>
        {msg && <div className="text-xs text-gray-600">{msg}</div>}
      </div>
    </details>
  );
}

/** 🎁 구매/리뷰 혜택(스마트스토어 포인트) — 저장 안 해도 이번 등록엔 화면 값이 걸린다. */
function BenefitBox({ ps, b, onChange }: { ps: NregPresets; b: Benefits; onChange: (b: Benefits) => void }) {
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const on = ps.benefit_keys.filter(k => (b[k.key] || 0) > 0);
  async function save(v: Benefits) {
    setSaving(true);
    try { await nreg.savePreset('benefit', v); setMsg('✅ 저장했습니다 — 다음 등록부터 기본값'); }
    catch (e) { setMsg('저장 실패: ' + (e as Error).message); }
    finally { setSaving(false); }
  }
  return (
    <details className="border rounded-lg p-2 text-sm">
      <summary className="text-xs font-semibold cursor-pointer">🎁 구매/리뷰 혜택 <span className="font-normal text-gray-500">
        · {on.length ? on.map(k => `${k.label} ${num(b[k.key])}`).join(', ') : '없음'}</span></summary>
      <div className="space-y-2 mt-2">
        <div className="text-[11px] text-gray-500">등록할 때 포인트가 함께 걸립니다. 0은 지급 안 함입니다.</div>
        <div className="grid grid-cols-2 gap-2">
          {ps.benefit_keys.map(k => (
            <Field key={k.key} label={k.label}>
              <input value={b[k.key] || 0} inputMode="numeric" className={inp}
                onChange={e => { onChange({ ...b, [k.key]: numIn(e.target.value) }); setMsg(''); }} />
            </Field>
          ))}
        </div>
        <div className="flex gap-2">
          <button onClick={() => save(b)} disabled={saving} className="text-xs border rounded px-2 py-1 disabled:opacity-40">💾 혜택 저장</button>
          <button onClick={() => { const z = Object.fromEntries(ps.benefit_keys.map(k => [k.key, 0])); onChange(z); save(z); }}
            disabled={saving} className="text-xs border rounded px-2 py-1 text-gray-500 disabled:opacity-40">🧹 지우기</button>
        </div>
        {msg && <div className="text-xs text-gray-600">{msg}</div>}
      </div>
    </details>
  );
}
