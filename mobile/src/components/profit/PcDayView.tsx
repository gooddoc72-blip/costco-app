'use client';
/**
 * 📊 일별 정산표 — 그날 발송(+온라인몰 직배송) 주문의 구입가·수익.
 *
 * 숫자는 전부 서버가 계산한다. 단가·택배·박스·영수증 품목을 고치면 고친 값만 서버로
 * 보내 다시 계산한 표를 받는다(0.5초 모아서). 저장하기 전에는 DB가 바뀌지 않는다.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { num } from '@/lib/fmt';
import { pc, PcDay, PcOverrides, PcOverride, PcRow, PcReceipt } from '@/lib/client/pc';

const PG = 20;

const SRC_STYLE: Record<string, { cls: string; badge: string }> = {
  '정산청구': { cls: 'bg-violet-50', badge: '🧾' },
  '영수증': { cls: 'bg-green-50', badge: '🟢' },
  'DB-번호': { cls: 'bg-blue-50', badge: '🔵' },
  'DB-키워드': { cls: 'bg-orange-50', badge: '🟠' },
  '수동입력': { cls: 'bg-white', badge: '✏️' },
  '미매칭': { cls: 'bg-yellow-50', badge: '🟡' },
};
function srcStyle(s: string) {
  const k = Object.keys(SRC_STYLE).find(x => s.startsWith(x));
  return k ? SRC_STYLE[k] : { cls: 'bg-white', badge: '' };
}

function kstYesterday() {
  return new Date(Date.now() + 9 * 3600e3 - 864e5).toISOString().slice(0, 10);
}

export default function PcDayView({ initialDate, onErr }: { initialDate: string; onErr: (s: string) => void }) {
  const [date, setDate] = useState(initialDate || kstYesterday());
  const [data, setData] = useState<PcDay | null>(null);
  const [ov, setOv] = useState<PcOverrides>({});
  const [loading, setLoading] = useState(false);
  const [calc, setCalc] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);
  const [moveDup, setMoveDup] = useState(false);
  const [priceDb, setPriceDb] = useState(false);
  const [pick, setPick] = useState<PcRow | null>(null);
  const [receipts, setReceipts] = useState<PcReceipt[] | null>(null);
  const reqId = useRef(0);
  const sent = useRef('{}');          // 서버가 마지막으로 계산한 고친 값 — 같으면 다시 안 부른다

  const load = useCallback(async (d: string) => {
    setLoading(true); setOv({}); sent.current = '{}'; setSel(new Set()); setPage(1); setMoveDup(false);
    try { setData(await pc.day(d)); } catch (e: any) { onErr(e.message || String(e)); setData(null); }
    finally { setLoading(false); }
  }, [onErr]);
  useEffect(() => { setMsg(''); load(date); }, [date, load]);
  useEffect(() => {
    const u = new URL(window.location.href);
    u.searchParams.set('date', date);
    window.history.replaceState(null, '', u.toString());
  }, [date]);

  // 고친 값 → 0.5초 모아 서버 재계산
  useEffect(() => {
    const key = JSON.stringify(ov);
    if (key === sent.current || !data) return;
    sent.current = key;
    const id = ++reqId.current;
    setCalc(true);
    const t = setTimeout(async () => {
      try {
        const d = await pc.preview(date, ov);
        if (id === reqId.current) setData(d);
      } catch (e: any) { onErr(e.message || String(e)); }
      finally { if (id === reqId.current) setCalc(false); }
    }, 500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ov]);

  const setRowOv = (o: string, patch: PcOverride | null) => setOv(s => {
    const n = { ...s };
    if (patch === null) delete n[o];
    else n[o] = { ...(n[o] || {}), ...patch };
    return n;
  });

  const rows = data?.rows || [];
  const pages = Math.max(1, Math.ceil(rows.length / PG));
  const pageRows = rows.slice((page - 1) * PG, page * PG);
  const edits = Object.keys(ov).length;
  const kwCount = rows.filter(r => r.match_source.startsWith('DB-키워드')).length;
  const elsewhere = data?.elsewhere || {};
  const elseByDate = useMemo(() => {
    const m: Record<string, number> = {};
    Object.values(elsewhere).forEach(d => { m[d] = (m[d] || 0) + 1; });
    return m;
  }, [elsewhere]);

  const [bulkUnit, setBulkUnit] = useState(0);
  const [bulkShip, setBulkShip] = useState<number | null>(null);
  const [bulkBox, setBulkBox] = useState<number | null>(null);
  const targets = sel.size ? Array.from(sel) : rows.map(r => r.order_no);
  const applyBulk = (patch: (o: string) => PcOverride, list: string[]) =>
    setOv(s => { const n = { ...s }; list.forEach(o => { n[o] = { ...(n[o] || {}), ...patch(o) }; }); return n; });

  const save = async () => {
    setBusy(true); setMsg('');
    try {
      const r = await pc.save(date, ov, moveDup, priceDb);
      setMsg(`✅ ${num(r.saved)}건 정산저장` + (r.prices ? ` · 제품가격 DB ${r.prices}건 반영` : '')
        + (r.links ? ` · 코스트코번호 자동 연결 ${r.links}건` : '') + (r.note ? `\n${r.note}` : ''));
      await load(date);
    } catch (e: any) { onErr(e.message || String(e)); }
    finally { setBusy(false); }
  };
  const del = async () => {
    const list = Array.from(sel);
    if (!list.length || !confirm(`선택한 ${list.length}건을 정산에서 영구 삭제할까요?\n발송기록·주문이력·정산저장·영수증 정산 품목에서 모두 지워집니다(취소건 정리용).`)) return;
    setBusy(true);
    try {
      const r = await pc.remove(date, list);
      await load(date);
      setMsg(`🗑 ${r.deleted}건 삭제했습니다.`);
    } catch (e: any) { onErr(e.message || String(e)); }
    finally { setBusy(false); }
  };
  const openPick = (r: PcRow) => {
    setPick(r);
    if (!receipts) pc.receipts().then(x => setReceipts(x.items)).catch(e => onErr(e.message));
  };

  const s = data?.summary;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-gray-500">발송일<br />
          <input type="date" value={date} onChange={e => e.target.value && setDate(e.target.value)}
            className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm" /></label>
        <button onClick={() => load(date)} className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm">📋 다시 불러오기</button>
        {(calc || loading) && <span className="text-xs text-gray-400">{loading ? '불러오는 중…' : '계산 중…'}</span>}
      </div>

      {msg && <div className="bg-green-50 text-green-700 p-3 rounded-lg text-sm whitespace-pre-wrap">{msg}</div>}
      {data?.no_ship_day && (
        <div className="bg-blue-50 text-blue-800 p-3 rounded-lg text-sm">📅 <b>택배 휴무일(주말·공휴일)입니다.</b> 이날 주문은 다음 영업일에 발송되므로 <b>발송한 날짜에서 정산저장</b>하세요.</div>
      )}
      {data?.label && <div className="text-xs text-gray-500">{data.label}</div>}

      {data && rows.length > 0 && (
        <>
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-1.5 text-center text-xs">
            {['정산청구', '영수증', 'DB-번호', 'DB-키워드', '수동입력', '미매칭'].map(k => (
              <div key={k} className={clsx('rounded-lg py-1.5', SRC_STYLE[k].cls)}>
                {SRC_STYLE[k].badge} {k}<div className="font-bold text-sm">{data.counts[k] || 0}건</div>
              </div>
            ))}
          </div>
          {kwCount > 0 && <div className="bg-orange-50 text-orange-800 p-2.5 rounded-lg text-xs">
            ⚠️ <b>{kwCount}건이 키워드만으로 매칭</b>됐습니다 — 상품번호 매칭이 아니라 틀릴 수 있습니다. 🟠 행을 확인하고, 틀렸으면 🧾로 영수증 품목을 고르거나 단가를 고치세요.</div>}
          {Object.keys(elsewhere).length > 0 && (
            <div className="bg-amber-50 text-amber-900 p-2.5 rounded-lg text-xs space-y-1">
              <div>⚠️ 이 날짜 주문 중 <b>{Object.keys(elsewhere).length}건은 다른 날짜에 이미 정산저장</b>돼 있습니다
                ({Object.entries(elseByDate).sort().map(([d, n]) => `${d} ${n}건`).join(', ')}). 저장할 때 건너뜁니다.</div>
              <label className="flex items-center gap-1"><input type="checkbox" checked={moveDup} onChange={e => setMoveDup(e.target.checked)} />
                🔁 이 날짜로 옮기기 — 다른 날짜의 저장분을 지우고 이 날짜로 저장(실제 발송일이 이 날짜일 때만)</label>
            </div>
          )}

          {s && (
            <div className="bg-white rounded-xl border border-gray-100 p-3 text-sm grid sm:grid-cols-3 gap-2">
              <div><div className="text-xs text-gray-400">수입</div>
                정산 {num(s.settlement)} + 배송비 {num(s.settled_shipping)} = <b>{num(s.income)}원</b></div>
              <div><div className="text-xs text-gray-400">지출</div>
                구입가 {num(s.cost)} + 택배 {num(s.delivery)} + 박스 {num(s.box)} = <b>{num(s.expense)}원</b></div>
              <div><div className="text-xs text-gray-400">순수익 (구입가 있는 {s.costed}건)</div>
                <b className={clsx('text-lg', s.profit >= 0 ? 'text-emerald-600' : 'text-red-600')}>{num(s.profit)}원</b></div>
              {s.nocost > 0 && <div className="sm:col-span-3 text-xs text-orange-700">구입가가 없는 {s.nocost}건(정산 {num(s.nocost_settlement)}원)은 합계에서 빠져 있습니다.</div>}
            </div>
          )}

          <div className="bg-white rounded-xl border border-gray-100 p-2.5 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-gray-500">{sel.size ? `${sel.size}건 선택` : '선택 없음 → 택배·박스는 전체에 적용'}</span>
            <input type="number" min={0} step={100} value={bulkUnit || ''} placeholder="1주문 단가"
              onChange={e => setBulkUnit(+e.target.value || 0)} className="w-24 border border-gray-200 rounded px-2 py-1" />
            <button disabled={!sel.size || !bulkUnit} onClick={() => applyBulk(() => ({ unit_cost: bulkUnit }), Array.from(sel))}
              className="border rounded px-2 py-1 disabled:opacity-40">💰 단가 적용</button>
            <input type="number" min={0} step={100} value={bulkShip ?? data.defaults.delivery}
              onChange={e => setBulkShip(+e.target.value || 0)} className="w-20 border border-gray-200 rounded px-2 py-1" />
            <button onClick={() => applyBulk(() => ({ delivery: bulkShip ?? data.defaults.delivery }), targets)}
              className="border rounded px-2 py-1">🚚 발송비 적용</button>
            <input type="number" min={0} step={100} value={bulkBox ?? data.defaults.box}
              onChange={e => setBulkBox(+e.target.value || 0)} className="w-20 border border-gray-200 rounded px-2 py-1" />
            <button onClick={() => applyBulk(() => ({ box: bulkBox ?? data.defaults.box }), targets)}
              className="border rounded px-2 py-1">📦 박스비 적용</button>
            <button disabled={!sel.size || busy} onClick={del} className="ml-auto text-red-600 border border-red-200 rounded px-2 py-1 disabled:opacity-40">🗑 선택 삭제</button>
          </div>

          <div className="flex items-center justify-between text-xs text-gray-500 px-1">
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={pageRows.length > 0 && pageRows.every(r => sel.has(r.order_no))}
                onChange={e => setSel(s0 => { const n = new Set(s0); pageRows.forEach(r => e.target.checked ? n.add(r.order_no) : n.delete(r.order_no)); return n; })} />
              이 페이지 전체 선택</label>
            <span>{rows.length}건 · {page}/{pages} 페이지</span>
          </div>

          <div className="space-y-1.5">
            {pageRows.map(r => (
              <RowCard key={r.order_no} r={r} edited={!!ov[r.order_no]} selected={sel.has(r.order_no)}
                onSelect={v => setSel(s0 => { const n = new Set(s0); v ? n.add(r.order_no) : n.delete(r.order_no); return n; })}
                onPatch={p => setRowOv(r.order_no, p)} onPick={() => openPick(r)} />
            ))}
          </div>
          {pages > 1 && (
            <div className="flex items-center justify-center gap-2 text-sm">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="px-3 py-1 border rounded disabled:opacity-40">이전</button>
              <span>{page} / {pages}</span>
              <button disabled={page >= pages} onClick={() => setPage(p => p + 1)} className="px-3 py-1 border rounded disabled:opacity-40">다음</button>
            </div>
          )}

          <div className="sticky bottom-16 bg-white/95 backdrop-blur border border-gray-200 rounded-xl p-3 space-y-2 shadow">
            <label className="flex items-center gap-1 text-xs text-gray-600">
              <input type="checkbox" checked={priceDb} onChange={e => setPriceDb(e.target.checked)} />
              구입가를 고친 상품은 제품가격 DB 단가도 바꾸기 (주문 상품번호로 찾아 1팩 단가로 환산)</label>
            <div className="flex gap-2">
              <button onClick={save} disabled={busy || calc}
                className="flex-1 bg-blue-600 text-white rounded-lg py-2.5 font-medium disabled:bg-gray-300">
                💾 정산저장 — 이 날짜 {rows.length}건{edits ? ` (고친 ${edits}건 포함)` : ''}</button>
              {edits > 0 && <button onClick={() => setOv({})} className="px-3 border border-gray-200 rounded-lg text-sm">고친 값 취소</button>}
            </div>
          </div>
        </>
      )}
      {data && !rows.length && !loading && (
        <div className="bg-blue-50 text-blue-700 p-4 rounded-lg text-sm">📭 {date}에 발송된 주문이 없습니다. 수익계산은 송장 등록으로 발송처리된 건(온라인몰 직배송 포함)만 합니다.</div>
      )}

      {pick && <ReceiptPicker row={pick} items={receipts} cur={ov[pick.order_no]}
        onClose={() => setPick(null)}
        onPick={it => { setRowOv(pick.order_no, { receipt_pno: it.pno, receipt_name: it.name, receipt_unit: it.unit, unit_cost: undefined }); setPick(null); }}
        onClear={() => { setRowOv(pick.order_no, null); setPick(null); }} />}
    </div>
  );
}

function RowCard({ r, edited, selected, onSelect, onPatch, onPick }: {
  r: PcRow; edited: boolean; selected: boolean;
  onSelect: (v: boolean) => void; onPatch: (p: PcOverride) => void; onPick: () => void;
}) {
  const st = srcStyle(r.match_source);
  const unit = r.qty > 0 ? Math.floor(r.cost_price / Math.max(1, r.qty)) : 0;
  return (
    <div className={clsx('rounded-xl border p-2.5 text-sm', st.cls, edited ? 'border-blue-300' : 'border-gray-100')}>
      <div className="flex items-start gap-2">
        <input type="checkbox" className="mt-1" checked={selected} onChange={e => onSelect(e.target.checked)} />
        <div className="min-w-0 flex-1">
          <div className="leading-snug">
            <span className="mr-1">{st.badge}</span>
            {r.split_qty > 1 && <span className="text-[11px] font-bold bg-violet-100 text-violet-700 rounded px-1 mr-1">소분÷{r.split_qty}</span>}
            {r.matched_pno && <span className="text-[11px] font-semibold bg-blue-100 text-blue-700 rounded px-1 mr-1">#{r.matched_pno}</span>}
            {r.product_name}
            {r.option_info && !['nan', 'none', '-'].includes(r.option_info.toLowerCase()) && <div className="text-xs text-gray-400">└ {r.option_info}</div>}
          </div>
          <div className="text-xs text-gray-500 mt-0.5 flex flex-wrap gap-x-2">
            <span>{r.recipient}</span><span>수량 {r.qty}</span>
            <span className="text-gray-400">{r.match_source}{r.matched_name && r.matched_name !== r.product_name ? ` · ${r.matched_name.slice(0, 30)}` : ''}</span>
            {r.saved && <span className="text-emerald-600">저장됨</span>}
          </div>
        </div>
        <div className={clsx('text-right font-bold whitespace-nowrap', r.cost_price <= 0 ? 'text-gray-400' : r.profit >= 0 ? 'text-emerald-600' : 'text-red-600')}>
          {num(r.profit)}<div className="text-[10px] font-normal text-gray-400">수입</div>
        </div>
      </div>
      <div className="grid grid-cols-5 gap-1.5 mt-2 text-xs items-end">
        <div><div className="text-[10px] text-gray-400">정산예정</div>{r.actual ? '✅' : ''}{num(r.settlement_amount)}</div>
        <div><div className="text-[10px] text-gray-400">고객택배비</div>{num(r.shipping_fee)}</div>
        <NumEdit label={`단가×${r.qty}`} value={unit} onCommit={v => onPatch({ unit_cost: v, receipt_name: undefined, receipt_pno: undefined, receipt_unit: undefined })} />
        <NumEdit label="발송비" value={r.delivery_cost} onCommit={v => onPatch({ delivery: v })} />
        <NumEdit label="박스비" value={r.box_cost} onCommit={v => onPatch({ box: v })} />
      </div>
      <div className="flex justify-between mt-1.5 text-xs">
        <span className="text-gray-500">구입가 <b>{num(r.cost_price)}</b></span>
        <button onClick={onPick} className={clsx('px-2 py-0.5 rounded border', r.cost_price <= 0 ? 'border-orange-300 text-orange-700' : 'border-gray-200 text-gray-600')}>
          🧾 영수증에서 고르기</button>
      </div>
    </div>
  );
}

/** 입력 중에는 서버를 부르지 않고, 칸을 벗어나거나 Enter를 칠 때 한 번 반영한다. */
function NumEdit({ label, value, onCommit }: { label: string; value: number; onCommit: (v: number) => void }) {
  const [v, setV] = useState(String(value));
  useEffect(() => { setV(String(value)); }, [value]);
  const commit = () => { const n = Math.max(0, Math.round(+v || 0)); if (n !== value) onCommit(n); };
  return (
    <label className="block">
      <span className="text-[10px] text-gray-400">{label} ✏️</span>
      <input type="number" min={0} step={100} value={v} onChange={e => setV(e.target.value)}
        onBlur={commit} onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
        className="w-full border border-gray-200 rounded px-1.5 py-1 bg-white" />
    </label>
  );
}

function ReceiptPicker({ row, items, cur, onClose, onPick, onClear }: {
  row: PcRow; items: PcReceipt[] | null; cur?: PcOverride;
  onClose: () => void; onPick: (it: PcReceipt) => void; onClear: () => void;
}) {
  const [q, setQ] = useState('');
  const list = (items || []).filter(it => !q.trim() || it.name.toLowerCase().includes(q.trim().toLowerCase()) || it.pno.includes(q.trim()));
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative bg-white w-full max-w-lg rounded-t-2xl sm:rounded-2xl p-4 max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="text-sm font-bold">🧾 영수증 품목 고르기</div>
        <div className="text-xs text-gray-500 mb-2">{row.product_name.slice(0, 60)} · 수량 {row.qty}</div>
        {cur?.receipt_name && <div className="text-xs bg-green-50 text-green-700 p-2 rounded mb-2">✅ 지금: {cur.receipt_name} (#{cur.receipt_pno})</div>}
        <input autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder="상품명 / 상품번호 검색"
          className="border border-gray-200 rounded-lg px-2 py-1.5 text-sm mb-2" />
        <div className="overflow-y-auto space-y-1 flex-1">
          {!items && <div className="text-xs text-gray-400">불러오는 중…</div>}
          {items && <div className="text-xs text-gray-400">{list.length}개 (최근 90일 영수증)</div>}
          {list.slice(0, 40).map(it => (
            <button key={it.pno + it.name} onClick={() => onPick(it)}
              className="w-full text-left border border-gray-100 rounded-lg px-2 py-1.5 text-sm hover:bg-blue-50">
              {it.name}<div className="text-xs text-gray-500">💰 {num(it.unit)}원 × {row.qty} = {num(it.unit * row.qty)}원 · #{it.pno} · {it.date}</div>
            </button>
          ))}
        </div>
        <div className="flex gap-2 mt-2">
          {cur && <button onClick={onClear} className="flex-1 border border-gray-200 rounded-lg py-2 text-sm">❌ 이 행 고친 값 모두 취소</button>}
          <button onClick={onClose} className="flex-1 bg-gray-800 text-white rounded-lg py-2 text-sm">닫기</button>
        </div>
      </div>
    </div>
  );
}
