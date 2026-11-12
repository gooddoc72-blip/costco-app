'use client';
/**
 * 🧾 영수증 올리기 — PDF·사진을 읽어 표에 채우고(저장 안 함), 사람이 확인·수정한 뒤에만 저장한다.
 * 읽는 즉시 저장하면 판독이 틀린 값이 재고·가격DB에 들어간다(9/8 그릭요거트 수량 64 사고).
 * 저장은 그 날짜를 통째로 교체한다 — 하루 영수증이 여러 장이면 한 번에 같이 올린다.
 *
 * 휴대폰에서 '💾 저장을 눌러도 반응이 없다'(10/28): 결과·오류가 페이지 맨 위에만 떠 표 아래에선
 * 안 보였고, 점검 전엔 저장 버튼이 꺼져 있었고, window.confirm은 브라우저가 대화상자를 막으면
 * 묻지도 않고 '취소'가 된다. 그래서 저장은 점검을 스스로 돌리고, 확인은 화면 안에서 받고,
 * 결과는 이 칸 안에 띄운다.
 */
import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { fileToB64, ReceiptCheck, ReceiptRow, rsettle } from '@/lib/client/rsettle';
import { num } from '@/lib/fmt';

const blank = (d: string): ReceiptRow => ({ receipt_date: d, costco_no: '', name: '', qty: 1, list_price: 0, discount: 0, unit_price: 0 });

/** 한 번 읽은 결과 — 같은 영수증을 다시 읽으면 이걸 빼고 새 값으로 바꾼다(수량을 더하지 않게). */
type Read = { files: string[]; sig: string; rows: ReceiptRow[]; ids: string[] };
/** 영수증 키 두 개('date|time|total|txn|승인|회원')가 **다른 영수증이 확실한가** — 파이썬
 *  receipt_settle.same_receipt_sig의 반대. 승인·회원번호가 읽혔으면 그걸로, 아니면 거래번호로.
 *  11/12: 같은 시각(13:35)·같은 금액(25,580) 요거트 영수증 2장이 하나로 덮어써져 6개가 4개가 됐다. */
function idDiffer(a: string, b: string) {
  const p = a.split('|'), q = b.split('|');
  if (p[0] !== q[0] || (p[1] || '') !== (q[1] || '') || p[2] !== q[2]) return true;
  const strong = [4, 5].filter(i => p[i] && q[i]);
  if (strong.some(i => p[i] !== q[i])) return true;
  if (strong.length) return false;
  return !!(p[3] && q[3] && p[3] !== q[3]);
}
/** 두 판독의 영수증이 서로 전부 다른 영수증인가(키가 없으면 모른다 → false) */
const allDiffer = (a: string[], b: string[]) =>
  a.length > 0 && b.length > 0 && a.every(x => b.every(y => idDiffer(x, y)));
const same = (a: ReceiptRow, b: ReceiptRow) => !!a.costco_no && a.costco_no === b.costco_no && a.receipt_date === b.receipt_date;
const sigOf = (rs: ReceiptRow[]) => rs.map(r => `${r.costco_no}|${r.receipt_date}|${r.qty}`).sort().join(',');
/** 상품번호 묶음 — date를 주면 그 날짜(또는 날짜 빈) 줄만 */
const nosOf = (rs: ReceiptRow[], date?: string) =>
  new Set(rs.filter(r => r.costco_no && (!date || !r.receipt_date || r.receipt_date === date)).map(r => r.costco_no));
/** 같은 영수증인가 — 작은 쪽의 절반 이상(최소 2개, 1품목이면 1개)이 겹치면 같은 영수증을 다시 찍은 것.
 *  파이썬 receipt_settle.same_receipt_nos와 같은 기준. 같은 날 두 번 장 본 영수증은 한두 개만 겹친다. */
function sameReceipt(a: Set<string>, b: Set<string>) {
  if (!a.size || !b.size) return false;
  let n = 0; a.forEach(x => { if (b.has(x)) n++; });
  const m = Math.min(a.size, b.size);
  // 1품목 영수증이 긴 영수증의 한 품목과 겹치는 건 같은 영수증의 증거가 아니다(파이썬과 같게, 11/11)
  if (m === 1 && Math.max(a.size, b.size) > 1) return false;
  return n >= Math.min(2, m) && n * 2 >= m;
}
const dateOf = (rs: ReceiptRow[]) => (rs.find(r => r.receipt_date)?.receipt_date || '');
function addRows(base: ReceiptRow[], got: ReceiptRow[]) {
  const out = [...base];
  for (const g of got) {
    const i = out.findIndex(r => same(r, g));
    if (i >= 0) out[i] = { ...out[i], qty: out[i].qty + g.qty, discount: out[i].discount + g.discount };
    else out.push(g);
  }
  return out;
}
function subRows(base: ReceiptRow[], part: ReceiptRow[]) {
  let out = [...base];
  for (const p of part) {
    const i = out.findIndex(r => same(r, p));
    if (i < 0) continue;
    const q = out[i].qty - p.qty;
    out = q > 0 ? out.map((r, j) => j === i ? { ...r, qty: q, discount: Math.max(0, r.discount - p.discount) } : r)
      : out.filter((_, j) => j !== i);
  }
  return out;
}

export default function ReceiptPanel({ day, count, onSaved, onErr }: {
  day: string; count: number; onSaved: (msg: string) => void; onErr: (m: string) => void;
}) {
  const [open, setOpen] = useState(count === 0);
  const [rows, setRows] = useState<ReceiptRow[] | null>(null);
  const [unsaved, setUnsaved] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [fails, setFails] = useState<{ file: string; error: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [chk, setChk] = useState<ReceiptCheck | null>(null);
  const [ask, setAsk] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const noteRef = useRef<HTMLDivElement>(null);
  const reads = useRef<Read[]>([]);
  // 🔄 다시 판독 — 이번 판독은 표를 비우고 새로 채운다(저장하면 그날 영수증이 새 값으로 통째로 바뀐다)
  const fresh = useRef(false);
  // 저장된 영수증에 이미 다 있는 품목을 또 읽어 더했을 때 — 되돌릴 수 있게 그 판독을 들고 있는다
  const [overlap, setOverlap] = useState<ReceiptRow[] | null>(null);
  const [askDel, setAskDel] = useState(false);

  useEffect(() => { setRows(null); setUnsaved(false); setChk(null); setAsk(false); setFails([]); setLines([]); setOpen(count === 0); reads.current = []; setOverlap(null); setAskDel(false); }, [day, count]);

  // 🧹 표만 비운다(DB는 그대로) — 판독을 처음부터 다시 할 때
  const clearTable = () => {
    setRows(null); setUnsaved(false); setChk(null); setAsk(false); setFails([]); setLines([]);
    reads.current = []; setOverlap(null);
    setNote({ ok: true, text: '🧹 표를 비웠습니다 — 저장된 영수증은 그대로입니다.' });
  };

  // 🗑 그 날짜에 저장된 영수증을 지운다 — 잘못 저장한 것을 처음부터 다시 올릴 때
  const delSaved = async () => {
    setAskDel(false); setBusy(true); setNote(null);
    try {
      const r = await rsettle.receiptDelete(day);
      setRows(null); setUnsaved(false); setChk(null); setAsk(false); reads.current = []; setOverlap(null);
      const t = `🗑 ${day} 저장된 영수증 ${r.deleted}줄을 지웠습니다 — 영수증을 다시 올려 저장한 뒤 다시 매칭하세요.`;
      setNote({ ok: true, text: t });
      onSaved(t);
    } catch (e: any) { fail('영수증 삭제 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };
  useEffect(() => { setNote(null); }, [day]);
  useEffect(() => { if (note) noteRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, [note]);
  const fail = (t: string) => { setNote({ ok: false, text: t }); onErr(t); };

  const loadSaved = async () => {
    setBusy(true);
    try { const r = await rsettle.receipt(day); setRows(r.rows.length ? r.rows : [blank(day)]); setUnsaved(false); setChk(null); setAsk(false); reads.current = []; setOverlap(null); }
    catch (e: any) { fail(e.message || String(e)); }
    finally { setBusy(false); }
  };

  const upload = async (fl: FileList | null) => {
    if (!fl?.length) return;
    setBusy(true); setLines([]); setFails([]); setNote(null); setOverlap(null);
    try {
      const fkeys = Array.from(fl).map(f => `${f.name}|${f.size}`);
      const files = await Promise.all(Array.from(fl).map(async f => ({ name: f.name, content_b64: await fileToB64(f) })));
      const { job } = await rsettle.parseStart(files, fresh.current);
      for (;;) {
        await new Promise(r => setTimeout(r, 2000));
        const s = await rsettle.parseStatus(job);
        setLines(s.lines);
        if (s.running) continue;
        if (s.error) throw new Error(s.error);
        const got = s.result?.rows || [];
        setFails(s.result?.fails || []);
        // 다른 영수증이면 표에 있는 것과 합친다(같은 상품·같은 날짜는 수량을 더한다).
        // 같은 영수증을 다시 읽은 것(같은 파일이거나 품목·수량이 똑같음)이면 앞 판독을 빼고
        // 새 값으로 바꾼다 — 판독을 두 번 해서 수량이 두 배가 되던 것(10/28).
        // ⚠️ 11/10: 같은 영수증으로 보는 기준이 '품목·수량이 똑같음'뿐이라, 다시 찍어 한 줄만
        //    다르게 읽혀도 수량이 더해졌다. 이제 같은 날짜에 상품번호가 절반 이상 겹치면 같은
        //    영수증으로 보고 덮어쓴다 — 앞 판독이면 그 판독을 빼고, 불러온 저장 영수증이면 겹친
        //    품목 줄을 새 값으로 바꾼다.
        const isFresh = fresh.current; fresh.current = false;
        if (isFresh) reads.current = [];
        const sig = sigOf(got);
        const gd = dateOf(got), gn = nosOf(got);
        const ids = s.result?.receipts || [];
        // 같은 파일이면 무조건 같은 영수증. 아니면 영수증 번호(승인·회원·거래번호)가 전부 다르면
        // 품목·수량이 똑같아도 다른 영수증이다(두 사람이 같은 요거트를 같은 시각에 산 영수증).
        const dups = reads.current.filter(r => r.files.some(f => fkeys.includes(f))
          || (!allDiffer(ids, r.ids) && ((sig && r.sig === sig)
            || ((!gd || !dateOf(r.rows) || dateOf(r.rows) === gd) && sameReceipt(gn, nosOf(r.rows))))));
        const others = reads.current.filter(r => !dups.includes(r));
        reads.current = [...others, { files: fkeys, sig, rows: got, ids }];
        let base = isFresh ? [] : (rows || []).filter(r => r.costco_no || r.name);
        for (const d of dups) base = subRows(base, d.rows);
        // 덮어쓰기 비교는 **불러온 저장 영수증 몫**만 — 이번에 앞서 읽은 다른 영수증 줄은 뺀다
        // (안 그러면 앞서 읽은 요거트 영수증 줄을 '표에 있던 같은 영수증'으로 보고 덮어쓴다)
        let saved = base;
        for (const o of others) saved = subRows(saved, o.rows);
        const bn = nosOf(saved, gd || undefined);
        let over = 0;
        if (!dups.length && sameReceipt(gn, bn)) {
          saved = saved.filter(r => {
            const hit = gn.has(r.costco_no) && (!gd || !r.receipt_date || r.receipt_date === gd);
            if (hit) over++;
            return !hit;
          });
          base = saved;
          for (const o of others) base = addRows(base, o.rows);
        }
        const out = addRows(base, got);
        setRows(out.length ? out : [blank(day)]);
        setUnsaved(true); setChk(null); setAsk(false);
        if (isFresh) setNote({ ok: true, text: '🔄 표를 비우고 새로 읽었습니다 — 확인 후 💾 저장하면 그날 저장된 영수증이 이 값으로 통째로 바뀝니다.' });
        else if (dups.length) setNote({ ok: true, text: '🔁 같은 영수증을 다시 읽어 앞 판독값을 새 값으로 바꿨습니다 — 수량을 더하지 않았습니다.' });
        else if (over) setNote({ ok: true, text: `🔁 표에 있던 같은 영수증 ${over}품목을 새 판독값으로 덮어썼습니다 — 수량을 더하지 않았습니다.` });
        break;
      }
    } catch (e: any) { fail('영수증 읽기 실패: ' + (e.message || e)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ''; }
  };

  const put = (i: number, p: Partial<ReceiptRow>) => {
    setRows(rs => (rs || []).map((r, j) => {
      if (j !== i) return r;
      const n = { ...r, ...p };
      // 정가·할인·수량을 고치면 실단가를 다시 계산한다(판독이 틀려 고친 값이 청구에 반영되게)
      if (('list_price' in p || 'discount' in p || 'qty' in p) && n.list_price > 0)
        n.unit_price = Math.max(0, Math.floor((n.list_price * n.qty - n.discount) / Math.max(1, n.qty)));
      return n;
    }));
    setUnsaved(true); setChk(null); setAsk(false);
  };

  const check = async (): Promise<ReceiptCheck | null> => {
    if (!rows) return null;
    setBusy(true); setNote(null);
    try { const c = await rsettle.receiptCheck(day, rows); setChk(c); return c; }
    catch (e: any) { fail('점검 실패: ' + (e.message || e)); return null; }
    finally { setBusy(false); }
  };

  // 💾 첫 누름: 점검이 없으면 돌리고 화면 안 확인 칸을 연다. 실제 저장은 확인 칸의 버튼으로.
  const askSave = async () => {
    const c = chk || await check();
    if (!c) return;
    if (!c.count) { fail('저장할 줄이 없습니다 — 코스트코 번호와 실단가가 있는 줄만 저장됩니다(회색 줄 확인).'); return; }
    setAsk(true);
  };

  const save = async () => {
    if (!rows || !chk) return;
    setAsk(false); setBusy(true); setNote(null);
    try {
      const r = await rsettle.receiptSave(day, rows);
      setUnsaved(false); setChk(null);
      const t = `💾 영수증 ${r.count}종 저장 (${r.dates.join(', ')}) — 신규 ${r.new} · 갱신 ${r.updated} · 가격DB 반영 ${r.priced}`
        + (r.removed ? ` · 🧹 이번에 없는 옛 품목 ${r.removed}줄 지움` : '')
        + (r.filled ? ` · 📅 날짜 없는 ${r.filled}줄은 ${r.fill_date}로` : '') + ' — 이제 다시 매칭하세요.';
      setNote({ ok: true, text: t });
      onSaved(t);
    } catch (e: any) { fail('영수증 저장 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };

  const tot = (rows || []).reduce((s, r) => s + r.unit_price * r.qty, 0);
  return (
    <section className="bg-white rounded-xl shadow-sm border">
      <button onClick={() => setOpen(!open)} className="w-full px-3 py-2 flex items-center gap-2 text-left">
        <span className="font-bold text-sm">🧾 영수증 올리기</span>
        <span className="text-xs text-gray-500">저장된 영수증 {count}품목{unsaved ? ' · ⚠️ 저장 안 한 표가 있습니다' : ''}</span>
        <span className="flex-1" /><span className="text-xs text-gray-400">{open ? '접기' : '펼치기'}</span>
      </button>
      {note && (
        <div ref={noteRef} className={clsx('mx-3 mb-2 p-2 rounded text-xs whitespace-pre-wrap', note.ok ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-700')}>
          {note.ok ? '' : '❌ '}{note.text}
        </div>)}
      {open && (
        <div className="px-3 pb-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <label onClick={() => { fresh.current = false; }} className={clsx('px-3 py-2 rounded-lg bg-primary text-white text-sm font-bold cursor-pointer', busy && 'opacity-50 pointer-events-none')}>
              📄 PDF·사진 올리기 (여러 장)
              <input ref={fileRef} type="file" multiple accept=".pdf,image/*" className="hidden" onChange={e => upload(e.target.files)} />
            </label>
            <button onClick={loadSaved} disabled={busy} className="px-3 py-2 rounded-lg border text-sm disabled:opacity-50">📂 저장된 영수증 불러오기</button>
            {rows && <button onClick={() => { setRows(rs => [...(rs || []), blank(day)]); setUnsaved(true); }} className="px-3 py-2 rounded-lg border text-sm">➕ 줄 추가</button>}
            {rows && <button onClick={() => { fresh.current = true; fileRef.current?.click(); }} disabled={busy} className="px-3 py-2 rounded-lg border border-amber-400 text-amber-800 text-sm disabled:opacity-50">🔄 다시 판독 (표 비우고 읽기)</button>}
            {rows && <button onClick={clearTable} disabled={busy} className="px-3 py-2 rounded-lg border text-sm disabled:opacity-50">🧹 표 비우기</button>}
            {count > 0 && <button onClick={() => setAskDel(true)} disabled={busy || askDel} className="px-3 py-2 rounded-lg border border-red-300 text-red-700 text-sm disabled:opacity-50">🗑 저장된 영수증 삭제</button>}
          </div>
          {askDel && (
            <div className="p-2 rounded border-2 border-red-400 bg-red-50 text-xs space-y-2">
              <div><b>{day}</b>에 저장된 영수증 <b>{count}품목</b>을 모두 지웁니다. 되돌릴 수 없습니다.<br />
                지운 뒤 영수증을 다시 올려 저장하고 다시 매칭해야 정산에 반영됩니다. (가격DB 매장가는 그대로 둡니다)</div>
              <div className="flex gap-2">
                <button onClick={() => setAskDel(false)} disabled={busy} className="flex-1 py-2 rounded-lg border bg-white text-sm">취소</button>
                <button onClick={delSaved} disabled={busy} className="flex-1 py-2 rounded-lg bg-red-600 text-white font-bold text-sm disabled:opacity-40">🗑 지웁니다</button>
              </div>
            </div>)}
          <div className="text-[11px] text-gray-500">읽은 값은 표에만 채워지고 <b>💾 저장 전에는 DB에 들어가지 않습니다.</b> 하루 영수증이 여러 장이면 <b>한 번에 같이</b> 올리세요 — 저장은 그 날짜를 통째로 교체합니다.</div>
          {busy && !!lines.length && <div className="text-xs text-gray-600">⏳ {lines[lines.length - 1]}</div>}
          {fails.map((f, i) => <div key={i} className="text-xs text-orange-700">⚠️ {f.file} — {f.error}. 표에 직접 넣을 수 있습니다.</div>)}
          {overlap && (
            <div className="p-2 rounded bg-amber-50 text-amber-900 text-xs space-y-1">
              <div>⚠️ 방금 읽은 {overlap.length}품목이 표에 <b>이미 같은 수량 이상</b> 있어 수량을 더했습니다. 이미 올린 영수증을 다시 읽은 거라면 되돌리세요.</div>
              <button onClick={() => {
                setRows(rs => { const o = subRows((rs || []).filter(r => r.costco_no || r.name), overlap); return o.length ? o : [blank(day)]; });
                reads.current = reads.current.filter(r => r.rows !== overlap);
                setOverlap(null); setChk(null); setAsk(false); setUnsaved(true);
                setNote({ ok: true, text: '↩️ 방금 읽은 수량을 뺐습니다.' });
              }} className="px-2 py-1 rounded border border-amber-400 bg-white">↩️ 방금 읽은 것 빼기 (같은 영수증)</button>
            </div>)}
          {rows && (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 text-gray-500"><tr>
                    <th className="p-1 text-left">영수증일</th><th className="p-1 text-left">코스트코 번호</th><th className="p-1 text-left">상품명</th>
                    <th className="p-1 text-right">수량</th><th className="p-1 text-right">정가</th><th className="p-1 text-right">할인(합계)</th>
                    <th className="p-1 text-right">실단가</th><th className="p-1 text-right">금액</th><th />
                  </tr></thead>
                  <tbody>{rows.map((r, i) => (
                    <tr key={i} className={clsx('border-t', (!r.costco_no || !r.unit_price) && 'bg-gray-50 text-gray-400')}>
                      <td className="p-1"><input value={r.receipt_date} onChange={e => put(i, { receipt_date: e.target.value })} placeholder="YYYY-MM-DD" className="w-24 border rounded px-1 py-0.5" /></td>
                      <td className="p-1"><input value={r.costco_no} onChange={e => put(i, { costco_no: e.target.value.trim() })} className="w-20 border rounded px-1 py-0.5 font-mono" /></td>
                      <td className="p-1"><input value={r.name} onChange={e => put(i, { name: e.target.value })} className="w-48 border rounded px-1 py-0.5" /></td>
                      <td className="p-1 text-right"><input type="number" min={1} value={r.qty} onChange={e => put(i, { qty: Math.max(1, Number(e.target.value)) })} className="w-14 border rounded px-1 py-0.5 text-right" /></td>
                      <td className="p-1 text-right"><input type="number" min={0} step={100} value={r.list_price} onChange={e => put(i, { list_price: Math.max(0, Number(e.target.value)) })} className="w-20 border rounded px-1 py-0.5 text-right" /></td>
                      <td className="p-1 text-right"><input type="number" min={0} step={100} value={r.discount} onChange={e => put(i, { discount: Math.max(0, Number(e.target.value)) })} className="w-20 border rounded px-1 py-0.5 text-right" /></td>
                      <td className="p-1 text-right"><input type="number" min={0} step={100} value={r.unit_price} onChange={e => put(i, { unit_price: Math.max(0, Number(e.target.value)) })} className="w-20 border rounded px-1 py-0.5 text-right" /></td>
                      <td className="p-1 text-right">{num(r.unit_price * r.qty)}</td>
                      <td className="p-1"><button onClick={() => { setRows(rs => (rs || []).filter((_, j) => j !== i)); setUnsaved(true); setChk(null); setAsk(false); }} className="text-gray-400 hover:text-red-600">✕</button></td>
                    </tr>))}</tbody>
                </table>
              </div>
              <div className="text-xs text-gray-600">{rows.filter(r => r.costco_no && r.unit_price).length}종 · {num(tot)}원 — <b>실단가</b>(쿠폰 할인을 뺀 실제 낸 단가)로 청구하고, <b>정가</b>는 가격DB에 들어갑니다. 번호·실단가가 없는 회색 줄은 저장되지 않습니다.</div>
              {chk && (
                <div className={clsx('p-2 rounded text-xs space-y-1', chk.outliers.length ? 'bg-red-50 text-red-800' : 'bg-green-50 text-green-800')}>
                  <div>저장할 {chk.count}종 · 총수량 {chk.qty} · {num(chk.amount)}원 · 날짜 {chk.dates.join(', ')}
                    {chk.dropped ? ` · 번호·단가 없는 ${chk.dropped}줄 제외` : ''}{chk.filled ? ` · 📅 날짜 없는 ${chk.filled}줄은 ${chk.fill_date}로 저장` : ''}</div>
                  {chk.outliers.map((o, i) => <div key={i}>🚨 <b>{o.name}</b> 수량 {o.qty} × {num(o.unit_price)} = {num(o.amount)}원 ({o.why}) — 판독이 맞는지 확인하세요</div>)}
                  {!chk.outliers.length && <div>✅ 의심스러운 줄이 없습니다.</div>}
                </div>)}
              {ask && chk && (
                <div className="p-2 rounded border-2 border-primary text-xs space-y-2">
                  <div>영수증 <b>{chk.count}종 · {num(chk.amount)}원</b>을 저장합니다 ({chk.dates.join(', ')}).<br />
                    그 날짜 영수증을 통째로 교체합니다 — 이번에 없는 옛 품목은 지워집니다.</div>
                  {!!chk.outliers.length && <div className="text-red-700 font-semibold">⚠️ 위 의심스러운 줄 {chk.outliers.length}건을 확인했나요?</div>}
                  <div className="flex gap-2">
                    <button onClick={() => setAsk(false)} disabled={busy} className="flex-1 py-2 rounded-lg border text-sm">취소</button>
                    <button onClick={save} disabled={busy} className="flex-1 py-2 rounded-lg bg-primary text-white font-bold text-sm disabled:opacity-40">💾 저장합니다</button>
                  </div>
                </div>)}
              <div className="flex gap-2">
                <button onClick={check} disabled={busy} className="flex-1 py-2 rounded-lg border-2 border-primary text-primary font-bold text-sm disabled:opacity-40">🔍 저장 전 점검</button>
                <button onClick={askSave} disabled={busy || ask} className="flex-1 py-2 rounded-lg bg-primary text-white font-bold text-sm disabled:opacity-40">{busy ? '⏳ 처리 중…' : '💾 영수증 저장'}</button>
              </div>
            </>)}
        </div>)}
    </section>
  );
}
