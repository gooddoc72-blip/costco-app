'use client';
/**
 * 🚚 발송 파일 올리기 — 전체 발송내역(CJ 엑셀·CSV)을 주문번호로 사용자에게 나눠 발송 기록에 넣는다.
 * 송장 등록을 안 하는 계정이 있으면 발송 기준 청구가 0원이 된다 — 관리자가 파일 하나로 정리한다.
 * 분류·저장·대신 발송처리는 파이썬(dispatch_service → dispatch_upload)이 한다.
 */
import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { dispatch, DispPreview, DispSaveRes, DispSummary, fileToB64, ShipRes } from '@/lib/client/rsettle';

export default function DispatchPanel({ day, names, onSaved, onErr }: {
  day: string; names: Record<string, string>; onSaved: (msg: string) => void; onErr: (m: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [p, setP] = useState<DispPreview | null>(null);
  const [sum, setSum] = useState<DispSummary | null>(null);
  const [dd, setDd] = useState(day);
  const [opt, setOpt] = useState({ skip_existing: true, use_row_date: true, move_wrong_date: true });
  const [asg, setAsg] = useState<Record<number, string>>({});
  const [det, setDet] = useState<DispSaveRes | null>(null);
  const [pick, setPick] = useState<Record<string, boolean>>({});
  const [courier, setCourier] = useState('CJ대한통운');
  const [agree, setAgree] = useState(false);
  const [shipRes, setShipRes] = useState<ShipRes | null>(null);
  const [shipLine, setShipLine] = useState('');
  const [man, setMan] = useState(() => Array.from({ length: 3 }, () => ({ username: '', order_no: '', tracking_no: '', recipient: '', product_name: '', qty: 1 })));
  const fileRef = useRef<HTMLInputElement>(null);
  const users = Object.entries(names).sort((a, b) => a[1].localeCompare(b[1]));
  const nm = (u: string) => names[u] || u;

  useEffect(() => { setDd(day); }, [day]);
  const refresh = async (token: string, d: string) => { try { setSum(await dispatch.summary(token, d)); } catch (e: any) { onErr(e.message || String(e)); } };

  const upload = async (fl: FileList | null) => {
    if (!fl?.length) return;
    setBusy(true); setDet(null); setShipRes(null);
    try {
      const files = await Promise.all(Array.from(fl).map(async f => ({ name: f.name, content_b64: await fileToB64(f) })));
      const r = await dispatch.preview(files);
      setP(r); setSum(r.summary || null); setAsg(Object.fromEntries((r.unknown || []).filter(u => u.pre).map(u => [u.idx, u.pre])));
      setPick({}); setAgree(false);
      if (r.default_date) setDd(r.default_date);
    } catch (e: any) { onErr('발송 파일 읽기 실패: ' + (e.message || e)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ''; }
  };

  const doneMsg = (r: DispSaveRes, what: string) => `${what} ${r.total}건 저장 — ` + Object.entries(r.saved).map(([u, c]) => `${nm(u)} ${c}건`).join(' · ')
    + (r.skipped ? ` · ⏭ 이미 있어 건너뜀 ${r.skipped}건` : '') + (r.moved ? ` · 🔁 다른 날짜에서 옮김 ${r.moved}건` : '') + ' — 다시 매칭하세요.';

  const save = async () => {
    if (!p?.token) return;
    setBusy(true);
    try { const r = await dispatch.save(p.token, dd, opt); setDet(r); onSaved(doneMsg(r, '🚚 발송 기록')); await refresh(p.token, dd); }
    catch (e: any) { onErr('발송 기록 저장 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };
  const saveUnknown = async () => {
    if (!p?.token) return;
    const a = Object.entries(asg).filter(([, u]) => u).map(([i, u]) => ({ idx: Number(i), username: u }));
    setBusy(true);
    try {
      const r = await dispatch.unknown(p.token, dd, a, opt); setDet(r); onSaved(doneMsg(r, '👤 미분류 발송건'));
      const done = new Set(a.map(x => x.idx));
      setP({ ...p, unknown: (p.unknown || []).filter(u => !done.has(u.idx)) }); setAsg({});
    } catch (e: any) { onErr('미분류 저장 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };
  const saveManual = async () => {
    setBusy(true);
    try {
      const r = await dispatch.manual(dd, man.filter(m => m.order_no || m.tracking_no || m.recipient || m.product_name));
      setDet(r); onSaved(doneMsg(r, '✍️ 직접 입력 발송건') + (r.bad ? ` · ⚠️ 주문번호·송장 둘 다 없는 ${r.bad}줄 제외` : ''));
      setMan(Array.from({ length: 3 }, () => ({ username: '', order_no: '', tracking_no: '', recipient: '', product_name: '', qty: 1 })));
    } catch (e: any) { onErr('직접 입력 실패: ' + (e.message || e)); }
    finally { setBusy(false); }
  };
  const ship = async () => {
    if (!p?.token || !sum) return;
    const us = sum.ship.filter(s => pick[s.username]).map(s => s.username);
    const n = sum.ship.filter(s => pick[s.username]).reduce((a, s) => a + s.sendable, 0);
    if (!window.confirm(`${us.length}명 · ${n}건을 각 사용자 스토어에 발송처리합니다.\n주문이 배송중으로 바뀌고 고객에게 발송 알림이 갑니다 — 되돌릴 수 없습니다.`)) return;
    setBusy(true); setShipRes(null);
    try {
      const { job } = await dispatch.ship(p.token, dd, us, courier);
      for (;;) {
        await new Promise(r => setTimeout(r, 2000));
        const s = await dispatch.job<ShipRes>(job);
        setShipLine(s.lines[s.lines.length - 1] || '');
        if (s.running) continue;
        if (s.error) throw new Error(s.error);
        setShipRes(s.result);
        const ok = Object.values(s.result || {}).reduce((a, r) => a + r.naver_ok + r.coupang_ok, 0);
        const ng = Object.values(s.result || {}).reduce((a, r) => a + r.naver_fail + r.coupang_fail, 0);
        onSaved(`🚀 스토어 발송처리 — 성공 ${ok}건 · 실패 ${ng}건 — 다시 매칭하세요.`);
        break;
      }
      setPick({}); setAgree(false); await refresh(p.token, dd);
    } catch (e: any) { onErr('발송처리 실패: ' + (e.message || e)); }
    finally { setBusy(false); setShipLine(''); }
  };

  const shipN = (sum?.ship || []).filter(s => pick[s.username]).reduce((a, s) => a + s.sendable, 0);
  const nAsg = Object.values(asg).filter(Boolean).length;
  return (
    <section className="bg-white rounded-xl shadow-sm border">
      <button onClick={() => setOpen(!open)} className="w-full px-3 py-2 flex items-center gap-2 text-left">
        <span className="font-bold text-sm">🚚 발송 파일 올리기 — 누락된 발송건</span>
        <span className="text-xs text-gray-500">택배사 전체 발송 파일을 주문번호로 사용자에게 나눠 발송 기록에 넣습니다</span>
        <span className="flex-1" /><span className="text-xs text-gray-400">{open ? '접기' : '펼치기'}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <label className={clsx('px-3 py-2 rounded-lg bg-primary text-white font-bold cursor-pointer', busy && 'opacity-50 pointer-events-none')}>
              📄 발송 파일 올리기 (xlsx·xls·csv, 여러 개)
              <input ref={fileRef} type="file" multiple accept=".xlsx,.xls,.csv" className="hidden" onChange={e => upload(e.target.files)} />
            </label>
            <span className="text-xs text-gray-500">같은 파일을 두 번 올려도 중복 저장되지 않습니다.</span>
          </div>

          {p && (
            <div className="space-y-2">
              <div className="text-xs text-gray-600">📄 {p.files.map(f => `${f.file} ${f.rows}행`).join(' · ')}
                {p.columns && <> · 🔗 주문번호={p.columns.order_no || '없음'} · 송장={p.columns.tracking_no || '없음'} · 수취인={p.columns.recipient || '없음'}</>}</div>
              {p.bad.map((b, i) => <div key={i} className="text-xs text-red-600">⚠️ {b.file} — {b.error}</div>)}
              {(p.notes || []).map((n, i) => <div key={i} className="text-xs text-blue-700">🔎 {n}</div>)}
              {p.error && <div className="text-sm text-red-600">❌ {p.error}</div>}
              {!!p.dup_owner && <div className="text-xs text-orange-700">⚠️ 두 사용자에 걸친 주문번호 {p.dup_owner}건 — 분류가 부정확할 수 있습니다.</div>}
              {!!p.external && <div className="text-xs text-gray-500">🏢 코코비즈 밖 업체 발송 {p.external}건은 뺐습니다(발송고객코드 기준).</div>}
              {!!p.already_saved && <div className="text-xs text-gray-500">✅ 미분류 중 이미 저장된 {p.already_saved}건은 그 사용자 발송건으로 넣었습니다.</div>}
              {!!p.combined?.length && <details className="text-xs"><summary className="cursor-pointer">📦 합배송 {p.combined.length}건 — 대표 주문과 같은 송장으로 함께 기록·발송처리</summary>
                {p.combined.map((c, i) => <div key={i}>· {nm(c.username)} {c.order_no} ← {c.with} · {c.recipient} · {c.product_name}</div>)}</details>}
              {p.token && (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <span>발송일(청구 귀속일)</span>
                    <input type="date" value={dd} onChange={e => { setDd(e.target.value); p.token && refresh(p.token, e.target.value); }} className="border rounded px-2 py-1" />
                    {!!p.dates?.length && <span className="text-xs text-gray-500">파일의 날짜: {p.dates.map(x => `${x.date} ${x.count}건`).join(' · ')}</span>}
                  </div>
                  {sum && (
                    <>
                      <div className="font-semibold">분류 결과 — {sum.total}건 매칭 · {(p.unknown || []).length}건 미분류</div>
                      <table className="w-full text-xs"><thead className="bg-gray-50 text-gray-500"><tr>
                        <th className="p-1 text-left">사용자</th><th className="p-1 text-right">건수</th><th className="p-1 text-right">이 날짜에 이미 있음</th>
                        <th className="p-1 text-right">다른 날짜에 기록됨</th><th className="p-1 text-right">새로 저장될 건</th></tr></thead>
                        <tbody>{sum.users.map(u => <tr key={u.username} className="border-t"><td className="p-1">{nm(u.username)}</td><td className="p-1 text-right">{u.count}</td>
                          <td className="p-1 text-right">{u.same || ''}</td><td className={clsx('p-1 text-right', u.other && 'text-orange-600')}>{u.other || ''}</td>
                          <td className="p-1 text-right font-semibold">{u.new}</td></tr>)}</tbody></table>
                      {!!sum.other_dates.length && <div className="text-xs text-orange-700 bg-orange-50 p-2 rounded">⚠️ 다른 날짜로 이미 발송된 건 — {sum.other_dates.map(x => `${x.date} ${x.count}건`).join(' · ')}.
                        송장번호가 다르면 재발송(택배 두 번)이라 두 기록이 다 있어야 맞습니다 — 그때는 ‘같은 날짜 건너뛰기’를 그대로 두고 저장하면 이 날짜로 한 건 더 쌓입니다. 같은 송장이면 ‘잘못 기록된 건 옮기기’가 이 날짜로 옮깁니다.</div>}
                      <div className="flex flex-wrap gap-3 text-xs">
                        <label className="flex items-center gap-1"><input type="checkbox" checked={opt.use_row_date} disabled={!p.dates?.length} onChange={e => setOpt({ ...opt, use_row_date: e.target.checked })} /> 파일의 집화일자를 발송일로 쓰기</label>
                        <label className="flex items-center gap-1"><input type="checkbox" checked={opt.move_wrong_date} onChange={e => setOpt({ ...opt, move_wrong_date: e.target.checked })} /> 다른 날짜로 잘못 기록된 건 옮기기(같은 송장)</label>
                        <label className="flex items-center gap-1"><input type="checkbox" checked={opt.skip_existing} onChange={e => setOpt({ ...opt, skip_existing: e.target.checked })} /> 같은 날짜에 이미 있는 주문은 건너뛰기</label>
                      </div>
                      <button onClick={save} disabled={busy || !sum.total} className="w-full py-2 rounded-lg bg-primary text-white font-bold disabled:opacity-40">
                        💾 {opt.skip_existing ? sum.new_total : sum.total}건 발송 기록 저장 {opt.use_row_date && p.dates?.length ? '(파일의 집화일자)' : `(${dd})`}</button>
                    </>)}

                  {!!p.unknown?.length && (
                    <details open className="border rounded p-2">
                      <summary className="cursor-pointer font-semibold">❓ 미분류 {p.unknown.length}건 — 누구 것인지 아시면 사용자를 골라 넣으세요 (그대로 두면 청구에 안 잡힘)</summary>
                      <table className="w-full text-xs mt-1"><thead className="bg-gray-50 text-gray-500"><tr>
                        <th className="p-1 text-left">사용자 지정</th><th className="p-1 text-left">송하인</th><th className="p-1 text-left">주문번호</th>
                        <th className="p-1 text-left">수취인</th><th className="p-1 text-left">상품명</th><th className="p-1 text-right">수량</th><th className="p-1 text-left">송장</th><th className="p-1 text-left">사유</th></tr></thead>
                        <tbody>{p.unknown.map(u => (
                          <tr key={u.idx} className={clsx('border-t', asg[u.idx] && 'bg-blue-50')}>
                            <td className="p-1"><select value={asg[u.idx] || ''} onChange={e => setAsg({ ...asg, [u.idx]: e.target.value })} className="border rounded px-1 py-0.5 bg-white">
                              <option value="">— 지정 안 함 —</option>{users.map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select></td>
                            <td className="p-1">{u.sender}</td><td className="p-1 font-mono">{u.order_no}</td><td className="p-1">{u.recipient}</td>
                            <td className="p-1">{u.product_name}</td><td className="p-1 text-right">{u.qty}</td><td className="p-1 font-mono">{u.tracking_no}</td>
                            <td className="p-1 text-gray-500">{u.why}</td></tr>))}</tbody></table>
                      <button onClick={saveUnknown} disabled={busy || !nAsg} className="mt-2 px-3 py-1.5 rounded-lg border-2 border-primary text-primary font-bold disabled:opacity-40">👤 {nAsg}건을 지정한 사용자로 저장</button>
                    </details>)}

                  {!!sum?.ship.length && (
                    <div className="border rounded p-2 space-y-2">
                      <div className="font-semibold">🚀 스토어 발송처리 — 관리자가 대신</div>
                      <div className="text-xs text-gray-600">파일의 송장번호를 <b>각 사용자의 네이버·쿠팡 스토어에 등록</b>합니다. 이미 스토어로 발송처리된 주문은 빼고, <b>고른 사용자만</b> 처리합니다.</div>
                      <table className="w-full text-xs"><thead className="bg-gray-50 text-gray-500"><tr>
                        <th className="p-1">선택</th><th className="p-1 text-left">사용자</th><th className="p-1 text-right">네이버</th><th className="p-1 text-right">쿠팡</th>
                        <th className="p-1 text-right">이미 발송처리됨</th><th className="p-1 text-right">송장 없음</th><th className="p-1 text-left">상태</th></tr></thead>
                        <tbody>{sum.ship.map(s => (
                          <tr key={s.username} className="border-t">
                            <td className="p-1 text-center"><input type="checkbox" disabled={!s.sendable} checked={!!pick[s.username]} onChange={e => setPick({ ...pick, [s.username]: e.target.checked })} /></td>
                            <td className="p-1">{nm(s.username)}</td><td className="p-1 text-right">{s.naver}</td><td className="p-1 text-right">{s.coupang}</td>
                            <td className="p-1 text-right">{s.done || ''}</td><td className="p-1 text-right">{s.no_trk || ''}</td>
                            <td className={clsx('p-1', s.state.includes('없음') && s.sendable === 0 && (s.naver || s.coupang) ? 'text-orange-600' : 'text-gray-600')}>{s.state}</td></tr>))}</tbody></table>
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <select value={courier} onChange={e => setCourier(e.target.value)} className="border rounded px-2 py-1 bg-white">
                          {(p.couriers || ['CJ대한통운']).map(c => <option key={c}>{c}</option>)}</select>
                        <label className="flex items-center gap-1"><input type="checkbox" checked={agree} onChange={e => setAgree(e.target.checked)} />
                          스토어 주문이 <b>배송중으로 바뀌고 고객에게 발송 알림</b>이 갑니다 — 되돌릴 수 없음을 확인했습니다</label>
                      </div>
                      {busy && shipLine && <div className="text-xs text-gray-600">⏳ {shipLine}</div>}
                      <button onClick={ship} disabled={busy || !shipN || !agree} className="w-full py-2 rounded-lg bg-gray-900 text-white font-bold disabled:opacity-40">
                        🚀 선택한 {Object.values(pick).filter(Boolean).length}명 · {shipN}건 스토어 발송처리</button>
                      {shipRes && <table className="w-full text-xs"><thead className="bg-gray-50 text-gray-500"><tr><th className="p-1 text-left">사용자</th><th className="p-1 text-right">네이버 성공/실패</th><th className="p-1 text-right">쿠팡 성공/실패</th><th className="p-1 text-left">메모</th></tr></thead>
                        <tbody>{Object.entries(shipRes).map(([u, r]) => <tr key={u} className="border-t"><td className="p-1">{nm(u)}</td><td className="p-1 text-right">{r.naver_ok}/{r.naver_fail}</td>
                          <td className="p-1 text-right">{r.coupang_ok}/{r.coupang_fail}</td><td className="p-1 text-red-600">{r.errors.join(' / ')}</td></tr>)}</tbody></table>}
                    </div>)}
                </>)}
            </div>)}

          {det && !!det.detail.length && (
            <details className="text-xs"><summary className="cursor-pointer">줄별 결과 {det.detail.length}건 (⏭ 이미 있음 = 같은 날짜에 이미 기록돼 다시 넣지 않음)</summary>
              <table className="w-full mt-1"><tbody>{det.detail.map((x, i) => <tr key={i} className="border-t">
                <td className="p-1">{x['결과']}</td><td className="p-1">{nm(x['사용자'])}</td><td className="p-1">{x['발송일']}</td><td className="p-1 font-mono">{x['주문번호']}</td>
                <td className="p-1">{x['수취인']}</td><td className="p-1">{x['상품명']}</td><td className="p-1 font-mono">{x['송장번호']}</td></tr>)}</tbody></table></details>)}

          <details className="border rounded p-2">
            <summary className="cursor-pointer font-semibold">✍️ 발송건 직접 입력 — 파일 없이 손으로 넣기</summary>
            <div className="text-xs text-gray-600 my-1">택배사 파일에 없는 발송건(이전 주문을 뒤늦게 보낸 건 등). 주문번호를 넣으면 영수증 매칭까지 되고, 없으면 송장번호로 발송건수·택배비에만 잡힙니다. 발송일은 위의 {dd}입니다.</div>
            <table className="w-full text-xs"><thead className="bg-gray-50 text-gray-500"><tr><th className="p-1 text-left">사용자</th><th className="p-1 text-left">주문번호</th><th className="p-1 text-left">송장번호</th><th className="p-1 text-left">수취인</th><th className="p-1 text-left">상품명</th><th className="p-1 text-right">수량</th></tr></thead>
              <tbody>{man.map((m, i) => {
                const set = (x: Partial<typeof m>) => setMan(ms => ms.map((y, j) => j === i ? { ...y, ...x } : y));
                return (<tr key={i} className="border-t">
                  <td className="p-1"><select value={m.username} onChange={e => set({ username: e.target.value })} className="border rounded px-1 py-0.5 bg-white"><option value="">—</option>{users.map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select></td>
                  <td className="p-1"><input value={m.order_no} onChange={e => set({ order_no: e.target.value.trim() })} className="w-36 border rounded px-1 py-0.5 font-mono" /></td>
                  <td className="p-1"><input value={m.tracking_no} onChange={e => set({ tracking_no: e.target.value.trim() })} className="w-28 border rounded px-1 py-0.5 font-mono" /></td>
                  <td className="p-1"><input value={m.recipient} onChange={e => set({ recipient: e.target.value })} className="w-20 border rounded px-1 py-0.5" /></td>
                  <td className="p-1"><input value={m.product_name} onChange={e => set({ product_name: e.target.value })} className="w-40 border rounded px-1 py-0.5" /></td>
                  <td className="p-1 text-right"><input type="number" min={1} value={m.qty} onChange={e => set({ qty: Math.max(1, Number(e.target.value)) })} className="w-12 border rounded px-1 py-0.5 text-right" /></td></tr>);
              })}</tbody></table>
            <div className="flex gap-2 mt-2">
              <button onClick={() => setMan(ms => [...ms, { username: '', order_no: '', tracking_no: '', recipient: '', product_name: '', qty: 1 }])} className="px-2 py-1 border rounded text-xs">➕ 줄 추가</button>
              <button onClick={saveManual} disabled={busy || !man.some(m => m.username && (m.order_no || m.tracking_no))} className="px-3 py-1.5 rounded-lg border-2 border-primary text-primary font-bold text-xs disabled:opacity-40">💾 발송 기록 저장</button>
            </div>
          </details>
          <div className="text-[11px] text-gray-500">열 매핑을 직접 고치거나 보낸 업체(발송고객코드) 분류를 바꾸려면 <a href="/app/" className="underline">기존 화면</a>을 쓰세요. 저장하면 이 날짜 매칭에 ‘새로 발송된 주문’ 경고가 뜹니다 — 다시 매칭하세요.</div>
        </div>)}
    </section>
  );
}
