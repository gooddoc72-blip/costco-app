'use client';
/**
 * 🧾 영수증 올리기 — PDF·사진을 읽어 표에 채우고(저장 안 함), 사람이 확인·수정한 뒤에만 저장한다.
 * 읽는 즉시 저장하면 판독이 틀린 값이 재고·가격DB에 들어간다(9/8 그릭요거트 수량 64 사고).
 * 저장은 그 날짜를 통째로 교체한다 — 하루 영수증이 여러 장이면 한 번에 같이 올린다.
 */
import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { fileToB64, ReceiptCheck, ReceiptRow, rsettle } from '@/lib/client/rsettle';
import { num } from '@/lib/fmt';

const blank = (d: string): ReceiptRow => ({ receipt_date: d, costco_no: '', name: '', qty: 1, list_price: 0, discount: 0, unit_price: 0 });

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
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setRows(null); setUnsaved(false); setChk(null); setFails([]); setLines([]); setOpen(count === 0); }, [day, count]);

  const loadSaved = async () => {
    setBusy(true);
    try { const r = await rsettle.receipt(day); setRows(r.rows.length ? r.rows : [blank(day)]); setUnsaved(false); setChk(null); }
    catch (e: any) { onErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  const upload = async (fl: FileList | null) => {
    if (!fl?.length) return;
    setBusy(true); setLines([]); setFails([]);
    try {
      const files = await Promise.all(Array.from(fl).map(async f => ({ name: f.name, content_b64: await fileToB64(f) })));
      const { job } = await rsettle.parseStart(files);
      for (;;) {
        await new Promise(r => setTimeout(r, 2000));
        const s = await rsettle.parseStatus(job);
        setLines(s.lines);
        if (s.running) continue;
        if (s.error) throw new Error(s.error);
        const got = s.result?.rows || [];
        setFails(s.result?.fails || []);
        // 이미 표에 있는 것(앞서 읽은 파일·불러온 영수증)과 합친다 — 같은 상품·같은 날짜는 수량을 더한다
        setRows(prev => {
          const base = (prev || []).filter(r => r.costco_no || r.name);
          const out = [...base];
          for (const g of got) {
            const i = out.findIndex(r => r.costco_no && r.costco_no === g.costco_no && r.receipt_date === g.receipt_date);
            if (i >= 0) out[i] = { ...out[i], qty: out[i].qty + g.qty, discount: out[i].discount + g.discount };
            else out.push(g);
          }
          return out.length ? out : [blank(day)];
        });
        setUnsaved(true); setChk(null);
        break;
      }
    } catch (e: any) { onErr('영수증 읽기 실패: ' + (e.message || e)); }
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
    setUnsaved(true); setChk(null);
  };

  const check = async () => {
    if (!rows) return;
    setBusy(true);
    try { setChk(await rsettle.receiptCheck(day, rows)); }
    catch (e: any) { onErr(e.message || String(e)); }
    finally { setBusy(false); }
  };

  const save = async () => {
    if (!rows || !chk) return;
    const t = `영수증 ${chk.count}종 · ${num(chk.amount)}원을 저장합니다 (${chk.dates.join(', ')}).\n\n그 날짜 영수증을 통째로 교체합니다 — 이번에 없는 옛 품목은 지워집니다.`
      + (chk.outliers.length ? `\n\n⚠️ 의심스러운 줄 ${chk.outliers.length}건을 확인했나요?` : '');
    if (!window.confirm(t)) return;
    setBusy(true);
    try {
      const r = await rsettle.receiptSave(day, rows);
      setUnsaved(false); setChk(null);
      onSaved(`💾 영수증 ${r.count}종 저장 (${r.dates.join(', ')}) — 신규 ${r.new} · 갱신 ${r.updated} · 가격DB 반영 ${r.priced}`
        + (r.removed ? ` · 🧹 이번에 없는 옛 품목 ${r.removed}줄 지움` : '')
        + (r.filled ? ` · 📅 날짜 없는 ${r.filled}줄은 ${r.fill_date}로` : '') + ' — 이제 다시 매칭하세요.');
    } catch (e: any) { onErr('영수증 저장 실패: ' + (e.message || e)); }
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
      {open && (
        <div className="px-3 pb-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <label className={clsx('px-3 py-2 rounded-lg bg-primary text-white text-sm font-bold cursor-pointer', busy && 'opacity-50 pointer-events-none')}>
              📄 PDF·사진 올리기 (여러 장)
              <input ref={fileRef} type="file" multiple accept=".pdf,image/*" className="hidden" onChange={e => upload(e.target.files)} />
            </label>
            <button onClick={loadSaved} disabled={busy} className="px-3 py-2 rounded-lg border text-sm disabled:opacity-50">📂 저장된 영수증 불러오기</button>
            {rows && <button onClick={() => { setRows(rs => [...(rs || []), blank(day)]); setUnsaved(true); }} className="px-3 py-2 rounded-lg border text-sm">➕ 줄 추가</button>}
          </div>
          <div className="text-[11px] text-gray-500">읽은 값은 표에만 채워지고 <b>💾 저장 전에는 DB에 들어가지 않습니다.</b> 하루 영수증이 여러 장이면 <b>한 번에 같이</b> 올리세요 — 저장은 그 날짜를 통째로 교체합니다.</div>
          {busy && !!lines.length && <div className="text-xs text-gray-600">⏳ {lines[lines.length - 1]}</div>}
          {fails.map((f, i) => <div key={i} className="text-xs text-orange-700">⚠️ {f.file} — {f.error}. 표에 직접 넣을 수 있습니다.</div>)}
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
                      <td className="p-1"><button onClick={() => { setRows(rs => (rs || []).filter((_, j) => j !== i)); setUnsaved(true); setChk(null); }} className="text-gray-400 hover:text-red-600">✕</button></td>
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
              <div className="flex gap-2">
                <button onClick={check} disabled={busy} className="flex-1 py-2 rounded-lg border-2 border-primary text-primary font-bold text-sm disabled:opacity-40">🔍 저장 전 점검</button>
                <button onClick={save} disabled={busy || !chk || !chk.count} className="flex-1 py-2 rounded-lg bg-primary text-white font-bold text-sm disabled:opacity-40">💾 영수증 저장</button>
              </div>
            </>)}
        </div>)}
    </section>
  );
}
