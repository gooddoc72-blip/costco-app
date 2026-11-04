'use client';
/**
 * 제품 DB 정리 도구 — 묶음 분류 · 코스트코 번호 연결 제안 · 옵션별 번호 · 가격 변동 이력.
 * 펼칠 때만 불러온다(묶음 분류는 상품명 분석에 1초 넘게 걸린다).
 */
import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { num } from '@/lib/fmt';
import { pdb, PackRow, LinkRow, OptRow, PriceHist } from '@/lib/client/pdb';

type Err = (s: string) => void;

function Section({ title, children }: { title: string; children: (open: boolean) => React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-white rounded-xl border border-gray-100">
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-center gap-1 px-3 py-2.5 text-sm font-medium text-left">
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />} {title}
      </button>
      {open && <div className="px-3 pb-3">{children(open)}</div>}
    </div>
  );
}

export default function PdbTools({ admin, me, onErr, onChanged }: {
  admin: boolean; me: string; onErr: Err; onChanged: () => void;
}) {
  return (
    <div className="space-y-2">
      <Section title="🔢 묶음 상품 분류">{() => <PackTool onErr={onErr} onChanged={onChanged} />}</Section>
      <Section title="🔗 코스트코 번호 연결 제안">{() => <LinkTool onErr={onErr} onChanged={onChanged} />}</Section>
      <Section title="🏷 옵션별 코스트코 상품번호">{() => <OptionTool admin={admin} me={me} onErr={onErr} />}</Section>
      <Section title="📈 코스트코 가격 변동 이력">{() => <HistoryTool onErr={onErr} />}</Section>
    </div>
  );
}

// ── 🔢 묶음 상품 분류 ──
function PackTool({ onErr, onChanged }: { onErr: Err; onChanged: () => void }) {
  const [rows, setRows] = useState<PackRow[] | null>(null);
  const [pick, setPick] = useState<Record<number, 'content' | 'pack'>>({});
  const load = useCallback(() => {
    pdb.pack().then(r => { setRows(r.rows); setPick({}); }).catch(e => onErr(e.message));
  }, [onErr]);
  useEffect(load, [load]);
  if (!rows) return <div className="text-xs text-gray-400">불러오는 중… (상품명 분석에 몇 초 걸립니다)</div>;
  if (!rows.length) return <div className="text-xs text-gray-500">분류가 필요한 상품이 없습니다.</div>;
  const risky = rows.filter(r => r.risky).length;
  const save = async (r: PackRow) => {
    const p = pick[r.id] ?? (r.risky ? 'content' : 'pack');
    try {
      await pdb.setPack(r.id, p === 'content' ? 1 : Math.max(2, r.name_factor));
      load(); onChanged();
    } catch (e: any) { onErr(e.message); }
  };
  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-500">
        상품명의 <b>&quot;x N개&quot;</b>는 상품마다 뜻이 다릅니다. <code>신라면 120g x 30개</code>는 30개들이 한 박스(내용물 설명)라
        곱하면 안 되고, <code>그릭요거트 x 2개</code>는 2개를 함께 파는 것이라 곱해야 합니다. 지정 전까지는 현재 계산 그대로입니다.
      </p>
      {risky > 0 && (
        <div className="text-xs bg-orange-50 text-orange-700 p-2 rounded-lg">
          ⚠️ {risky}건은 소분수가 1인데 상품명의 숫자만큼 구입가격이 곱해지고 있습니다. 내용물 설명이면 &apos;내용물 설명&apos;을 고르세요.
        </div>
      )}
      {rows.map(r => {
        const p = pick[r.id] ?? (r.risky ? 'content' : 'pack');
        return (
          <div key={r.id} className="border border-gray-100 rounded-lg p-2 text-sm">
            <div className="font-medium">{r.risky ? '⚠️ ' : ''}{r.name}</div>
            <div className="text-xs text-gray-500">
              매입가 {num(r.unit_price)}원 · 소분 {r.split_qty} · 현재 배수 <b>×{r.applied}</b> → 1주문 구입가격 <b>{num(r.cost_now)}원</b>
              {r.name_factor > 50 && ` (상품명은 x${r.name_factor}, 50 초과라 1로 처리 중)`}
            </div>
            <div className="flex flex-wrap items-center gap-3 mt-1 text-xs">
              <label><input type="radio" checked={p === 'content'} onChange={() => setPick(s => ({ ...s, [r.id]: 'content' }))} /> 내용물 설명 (곱하지 않음)</label>
              <label><input type="radio" checked={p === 'pack'} onChange={() => setPick(s => ({ ...s, [r.id]: 'pack' }))} /> {r.name_factor}개 묶음 (곱함)</label>
              <button onClick={() => save(r)} className="ml-auto bg-blue-600 text-white px-3 py-1 rounded">저장</button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── 🔗 코스트코 번호 연결 제안 ──
function LinkTool({ onErr, onChanged }: { onErr: Err; onChanged: () => void }) {
  const [rows, setRows] = useState<LinkRow[] | null>(null);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState('');
  const load = useCallback(() => {
    pdb.links().then(r => { setRows(r.rows); setOff(new Set()); }).catch(e => onErr(e.message));
  }, [onErr]);
  useEffect(load, [load]);
  if (!rows) return <div className="text-xs text-gray-400">불러오는 중…</div>;
  if (!rows.length) return <div className="text-xs text-gray-500">{msg || '연결할 제안이 없습니다.'}</div>;
  const picked = rows.filter(r => !off.has(r.naver_no));
  const apply = async () => {
    try {
      const r = await pdb.applyLinks(picked.map(x => ({ naver_no: x.naver_no, costco_no: x.costco_no })));
      setMsg(`✅ ${r.linked}개 상품에 코스트코 번호를 연결했습니다.`);
      load(); onChanged();
    } catch (e: any) { onErr(e.message); }
  };
  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-500">
        코스트코 번호가 빈 내 상품 중, 지난 영수증 정산에서 항상 같은 코스트코 번호로 청구된 것입니다.
        연결하면 원가·정산이 이름 추정 대신 번호로 맞춰집니다.
      </p>
      {msg && <div className="text-xs text-green-700">{msg}</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr className="text-gray-400 text-left"><th></th><th>내 상품(네이버)</th><th>네이버번호</th><th>코스트코번호</th><th>코스트코 상품명</th><th className="text-right">정산</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.naver_no} className="border-t border-gray-50">
                <td><input type="checkbox" checked={!off.has(r.naver_no)} onChange={() => setOff(s => {
                  const n = new Set(s); n.has(r.naver_no) ? n.delete(r.naver_no) : n.add(r.naver_no); return n;
                })} /></td>
                <td className="py-1">{r.name.slice(0, 40)}</td><td>{r.naver_no}</td><td>{r.costco_no}</td>
                <td>{r.costco_name.slice(0, 30)}</td><td className="text-right">{r.times}회</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button onClick={apply} disabled={!picked.length}
        className="bg-blue-600 text-white text-sm px-3 py-1.5 rounded-lg disabled:bg-gray-300">🔗 {picked.length}건 연결</button>
    </div>
  );
}

// ── 🏷 옵션별 코스트코 상품번호 ──
function daysAgo(n: number) {
  const d = new Date(Date.now() + 9 * 3600e3 - n * 864e5);   // 한국 날짜
  return d.toISOString().slice(0, 10);
}

function OptionTool({ admin, me, onErr }: { admin: boolean; me: string; onErr: Err }) {
  const [sellers, setSellers] = useState<{ username: string; name: string }[]>([]);
  const [u, setU] = useState(me);
  const [from, setFrom] = useState(daysAgo(60));
  const [to, setTo] = useState(daysAgo(0));
  const [rows, setRows] = useState<OptRow[] | null>(null);
  const [dupe, setDupe] = useState(0);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState('');
  useEffect(() => { if (admin) pdb.sellers().then(r => setSellers(r.rows)).catch(() => {}); }, [admin]);
  const load = useCallback(() => {
    setRows(null); setEdits({});
    pdb.options(from, to, u).then(r => { setRows(r.rows); setDupe(r.dupe_products); }).catch(e => onErr(e.message));
  }, [from, to, u, onErr]);
  useEffect(load, [load]);
  const k = (r: OptRow) => `${r.naver_pno}|${r.option_code}`;
  const changed = (rows || []).filter(r => edits[k(r)] !== undefined && edits[k(r)].trim() !== r.costco_no);
  const save = async () => {
    try {
      const r = await pdb.saveOptions(u, changed.map(x => ({
        naver_pno: x.naver_pno, option_code: x.option_code, option_name: x.option_info,
        product_name: x.product_name, costco_pno: edits[k(x)].trim(),
      })));
      setMsg(`✅ 저장 ${r.saved}건${r.removed ? ` · 지움 ${r.removed}건` : ''} — 다음 주문 수집부터 이 번호로 잡힙니다. 이미 저장된 주문은 바뀌지 않습니다.`);
      load();
    } catch (e: any) { onErr(e.message); }
  };
  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-500">
        한 상품에 서로 다른 코스트코 상품이 옵션으로 묶여 있으면 옵션마다 번호를 지정하세요. 지정하면 영수증 매칭·재고 차감이 옵션 단위로 맞습니다.
        같은 상품의 수량 차이일 뿐이면 묶음 분류를 쓰세요.
      </p>
      <div className="flex flex-wrap gap-2 text-xs items-end">
        {admin && sellers.length > 0 && (
          <label>판매자<br />
            <select value={u} onChange={e => setU(e.target.value)} className="border border-gray-200 rounded px-2 py-1">
              <option value={me}>{me} (나)</option>
              {sellers.filter(s => s.username !== me).map(s => <option key={s.username} value={s.username}>{s.name}</option>)}
            </select>
          </label>
        )}
        <label>주문일 시작<br /><input type="date" value={from} onChange={e => setFrom(e.target.value)} className="border border-gray-200 rounded px-2 py-1" /></label>
        <label>끝<br /><input type="date" value={to} onChange={e => setTo(e.target.value)} className="border border-gray-200 rounded px-2 py-1" /></label>
      </div>
      {msg && <div className="text-xs text-green-700">{msg}</div>}
      {!rows ? <div className="text-xs text-gray-400">불러오는 중…</div>
        : !rows.length ? <div className="text-xs text-gray-500">그 기간 주문에 옵션이 붙은 건이 없습니다.</div>
          : <>
            {dupe > 0 && <div className="text-xs bg-orange-50 text-orange-700 p-2 rounded-lg">
              ⚠️ 옵션이 여러 개인데 같은 코스트코번호가 붙은 상품 {dupe}종 — 옵션마다 다른 상품이라면 번호를 나눠 적으세요.</div>}
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="text-gray-400 text-left"><th>상품명</th><th>옵션</th><th className="text-right">팔린</th><th>현재 붙은 번호</th><th>코스트코번호</th></tr></thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={k(r)} className="border-t border-gray-50">
                      <td className="py-1">{r.product_name}<div className="text-gray-400">{r.naver_pno}</div></td>
                      <td>{r.option_info}</td><td className="text-right">{r.cnt}</td><td>{r.current_no}</td>
                      <td><input value={edits[k(r)] ?? r.costco_no} onChange={e => setEdits(s => ({ ...s, [k(r)]: e.target.value }))}
                        placeholder="비우면 매핑 삭제" className="w-28 border border-gray-200 rounded px-1.5 py-0.5" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button onClick={save} disabled={!changed.length}
              className="bg-blue-600 text-white text-sm px-3 py-1.5 rounded-lg disabled:bg-gray-300">💾 옵션 매핑 {changed.length}건 저장</button>
          </>}
    </div>
  );
}

// ── 📈 코스트코 가격 변동 이력 ──
function HistoryTool({ onErr }: { onErr: Err }) {
  const [rows, setRows] = useState<PriceHist[] | null>(null);
  const load = useCallback(() => { pdb.priceHistory().then(r => setRows(r.rows)).catch(e => onErr(e.message)); }, [onErr]);
  useEffect(load, [load]);
  if (!rows) return <div className="text-xs text-gray-400">불러오는 중…</div>;
  if (!rows.length) return <div className="text-xs text-gray-500">아직 가격 변동 이력이 없습니다. 영수증을 올리면 자동으로 감지됩니다.</div>;
  const clear = async () => {
    if (!confirm('가격 변동 이력을 전부 지울까요? 되돌릴 수 없습니다.')) return;
    try { await pdb.clearPriceHistory(); load(); } catch (e: any) { onErr(e.message); }
  };
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr className="text-gray-400 text-left"><th>일시</th><th>상품명</th><th className="text-right">이전가</th><th className="text-right">새 매입가</th><th className="text-right">변동</th><th>고객배송비</th><th>알림</th><th>네이버</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="border-t border-gray-50">
                <td className="py-1 whitespace-nowrap">{String(r.created_at).slice(0, 16)}</td>
                <td>{r.costco_name}</td>
                <td className="text-right">{num(r.old_cost)}</td><td className="text-right">{num(r.new_cost)}</td>
                <td className={'text-right whitespace-nowrap ' + (r.diff > 0 ? 'text-red-600' : 'text-blue-600')}>
                  {r.diff > 0 ? '🔺' : '🔻'} {r.diff > 0 ? '+' : ''}{num(r.diff)} ({(+r.diff_pct || 0).toFixed(1)}%)</td>
                <td>{+r.shipping_fee ? num(r.shipping_fee) : '무료'}</td>
                <td>{r.notified ? '✅' : '-'}</td><td>{r.naver_updated ? '✅' : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button onClick={clear} className="text-xs text-red-600 border border-red-200 px-2 py-1 rounded">🗑 이력 전체 삭제</button>
    </div>
  );
}
