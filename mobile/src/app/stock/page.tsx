'use client';
/**
 * 📦 재고 조회 — 사용자별 재고 카드 · 통합 검색 · 이상 점검. 읽기 전용.
 *
 * 재고 장부 계산은 맞는데 입고를 늦게 넣거나, 번호를 못 찾은 발송이 차감을 건너뛰거나,
 * 같은 재고를 두 번 넣어도 그걸 볼 자리가 없었다. 여기서는 찾아서 보여 주기만 하고,
 * 고치는 건 기존 재고 관리 화면(수동 입출고·입고 취소·재고 넘기기)에서 한다.
 * 관리자는 전부, 판매자는 본인 재고 카드만 본다.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import { RefreshCw, Search, ChevronDown, ChevronRight } from 'lucide-react';
import Header from '@/components/Header';
import { stock, StOverview, StCard, StSearch, StChecks, StLot, StMove } from '@/lib/client/stock';
import { num } from '@/lib/fmt';

type Tab = 'user' | 'search' | 'checks';

const KIND_LABEL: Record<string, string> = {
  math: '장부 불일치', dup: '중복 입고 의심', cancel: '취소 주문 차감', return: '반품 미등록',
  late: '늦게 등록한 입고', unpaid: '미결제 재고', admin: '주인 없는 재고', unresolved: '번호 못 찾은 발송',
  old: '30일 넘은 재고', adjust: '수동 조정으로 비움', nopno: '번호 없는 재고',
};

export default function StockPage() {
  const [me, setMe] = useState<{ username: string; is_admin: boolean } | null>(null);
  const [tab, setTab] = useState<Tab>('user');
  const [user, setUser] = useState('');
  const [q, setQ] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    stock.me().then(m => {
      setMe(m);
      const t = (p.get('tab') as Tab) || 'user';
      setTab(m.is_admin ? t : 'user');
      setUser(p.get('u') || (m.is_admin ? '' : m.username));
      setQ(p.get('q') || '');
    }).catch(e => setErr(e.message || String(e)));
  }, []);

  const openUser = (u: string) => { setUser(u); setTab('user'); window.scrollTo(0, 0); };
  const openSearch = (s: string) => { setQ(s); setTab('search'); window.scrollTo(0, 0); };

  return (
    <>
      <Header title="📦 재고 조회" subtitle="사용자별 재고 · 어디로 나갔나 · 이상 점검" right={
        <a href="/app/" className="text-xs text-gray-500 underline">재고 고치기(기존 화면)</a>
      } />
      <main className="px-3 md:px-6 pt-3 pb-24 space-y-4">
        {me?.is_admin && (
          <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit">
            {([['user', '👤 사용자별'], ['search', '🔍 검색'], ['checks', '🩺 이상 점검']] as [Tab, string][]).map(([t, l]) => (
              <button key={t} onClick={() => setTab(t)}
                className={clsx('px-3 py-1.5 rounded-md text-sm', tab === t ? 'bg-white shadow font-bold' : 'text-gray-600')}>{l}</button>
            ))}
          </div>
        )}
        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}
        {me && tab === 'user' && <UserTab admin={me.is_admin} user={user} setUser={setUser} onErr={setErr} openSearch={openSearch} />}
        {me?.is_admin && tab === 'search' && <SearchTab q={q} setQ={setQ} onErr={setErr} openUser={openUser} />}
        {me?.is_admin && tab === 'checks' && <ChecksTab onErr={setErr} openUser={openUser} openSearch={openSearch} />}
      </main>
    </>
  );
}

// ── 👤 사용자별 ─────────────────────────────────────────────
function UserTab({ admin, user, setUser, onErr, openSearch }: {
  admin: boolean; user: string; setUser: (u: string) => void; onErr: (s: string) => void; openSearch: (q: string) => void;
}) {
  const [ov, setOv] = useState<StOverview | null>(null);
  const [card, setCard] = useState<StCard | null>(null);
  const [showEmpty, setShowEmpty] = useState(false);
  const [loading, setLoading] = useState(false);

  const loadOv = useCallback(async () => {
    if (!admin) return;
    try { setOv(await stock.overview()); } catch (e: any) { onErr(e.message || String(e)); }
  }, [admin, onErr]);
  useEffect(() => { loadOv(); }, [loadOv]);
  useEffect(() => {
    if (!user) { setCard(null); return; }
    setLoading(true);
    stock.user(user).then(setCard).catch(e => onErr(e.message || String(e))).finally(() => setLoading(false));
  }, [user, onErr]);

  const lots = useMemo(() => (card?.lots || []).filter(l => showEmpty || l.qty_left > 0), [card, showEmpty]);
  const nm = card?.names || ov?.names || {};

  return (
    <div className="space-y-4">
      {admin && ov && (
        <section className="bg-white rounded-xl shadow-sm overflow-x-auto">
          <div className="px-3 py-2 text-sm font-bold flex items-center gap-2">
            재고 있는 사용자 {ov.owners.length}명 · {num(ov.total.units)}개 · {num(ov.total.amount)}원
            {ov.total.unpaid_units > 0 && <span className="text-orange-600 font-normal text-xs">미결제 {ov.total.unpaid_units}개</span>}
            <button onClick={loadOv} className="ml-auto p-1 border rounded"><RefreshCw size={14} /></button>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs text-gray-500">
              <tr><th className="text-left px-3 py-1.5">보유자</th><th className="text-right px-2">품목</th><th className="text-right px-2">남은 개수</th>
                <th className="text-right px-2">재고금액</th><th className="text-right px-2">미결제</th><th className="text-right px-2">30일↑</th>
                <th className="text-right px-3">받을 정산</th></tr>
            </thead>
            <tbody>
              {ov.owners.map(o => (
                <tr key={o.username} onClick={() => setUser(o.username)}
                  className={clsx('border-t cursor-pointer hover:bg-blue-50', user === o.username && 'bg-blue-50 font-bold')}>
                  <td className="px-3 py-1.5">{o.name} <span className="text-xs text-gray-400">{o.username}</span></td>
                  <td className="text-right px-2">{o.items}</td>
                  <td className="text-right px-2">{num(o.units)}</td>
                  <td className="text-right px-2">{num(o.amount)}</td>
                  <td className={clsx('text-right px-2', o.unpaid_units && 'text-orange-600')}>{o.unpaid_units || '—'}</td>
                  <td className={clsx('text-right px-2', o.old_units && 'text-red-600')}>{o.old_units || '—'}</td>
                  <td className="text-right px-3">{o.pending_credit ? num(o.pending_credit) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-3 py-2 flex items-center gap-2 text-xs text-gray-500 border-t">
            재고가 0인 사용자도 볼 수 있습니다:
            <select value={user} onChange={e => setUser(e.target.value)} className="border rounded px-2 py-1 bg-white">
              <option value="">— 사용자 선택 —</option>
              {Object.entries(ov.names).sort((a, b) => a[1].localeCompare(b[1])).map(([u, n]) =>
                <option key={u} value={u}>{n} ({u})</option>)}
            </select>
          </div>
        </section>
      )}

      {loading && <div className="text-sm text-gray-500">불러오는 중…</div>}
      {card && !loading && (
        <section className="space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            <Kpi label={`${card.name} 남은 재고`} value={`${num(card.summary.units)}개`} sub={`${card.summary.items}품목`} />
            <Kpi label="재고금액(구입가)" value={`${num(card.summary.amount)}원`} />
            <Kpi label="미결제" value={`${num(card.summary.unpaid_units)}개`} warn={!!card.summary.unpaid_units}
              sub="팔릴 때 구입가로 청구" />
            <Kpi label="30일 넘음" value={`${num(card.summary.old_units)}개`} warn={!!card.summary.old_units} sub="매장 반품 기한 지남" />
            <Kpi label="지금까지 입고" value={`${num(card.summary.in_units)}개`}
              sub={card.summary.out_by.map(o => `${o.how} ${o.qty}`).join(' · ') || '나간 것 없음'} />
          </div>

          <div className="bg-white rounded-xl shadow-sm">
            <div className="px-3 py-2 flex items-center gap-3 text-sm font-bold border-b">
              입고별 내역 — 눌러서 어디로 나갔는지 보기
              <label className="ml-auto text-xs font-normal flex items-center gap-1">
                <input type="checkbox" checked={showEmpty} onChange={e => setShowEmpty(e.target.checked)} />
                다 나간 입고도 보기 ({(card.lots || []).length}건)
              </label>
            </div>
            {lots.length === 0 && <div className="p-4 text-sm text-gray-500">남은 재고가 없습니다.</div>}
            {lots.map(l => <LotRow key={l.id} l={l} nm={nm} openSearch={openSearch} />)}
          </div>

          {card.used_others.length > 0 && (
            <details className="bg-white rounded-xl shadow-sm">
              <summary className="px-3 py-2 cursor-pointer text-sm font-bold">🔀 {card.name} 님이 남의 재고로 보낸 건 {card.used_others.length}건</summary>
              <MoveTable moves={card.used_others} nm={nm} showOwner openSearch={openSearch} />
            </details>
          )}
          {card.credits.length > 0 && (
            <details className="bg-white rounded-xl shadow-sm">
              <summary className="px-3 py-2 cursor-pointer text-sm font-bold">💰 재고·반품 적립 {card.credits.length}건 · 합계 {num(card.credits.reduce((s, c) => s + c.amount, 0))}원</summary>
              <table className="w-full text-xs">
                <tbody>
                  {card.credits.map((c, i) => (
                    <tr key={i} className="border-t"><td className="px-3 py-1">{c.tx_date}</td>
                      <td className={clsx('text-right px-2', c.amount < 0 && 'text-red-600')}>{num(c.amount)}</td>
                      <td className="px-3 text-gray-600">{c.memo}</td></tr>))}
                </tbody>
              </table>
            </details>
          )}
        </section>
      )}
    </div>
  );
}

function LotRow({ l, nm, openSearch }: { l: StLot; nm: Record<string, string>; openSearch: (q: string) => void }) {
  const [open, setOpen] = useState(false);
  const late = l.created_at && l.created_at.slice(0, 10) > l.received_at;
  return (
    <div className="border-b last:border-0">
      <button onClick={() => setOpen(!open)} className="w-full text-left px-3 py-2 hover:bg-gray-50 flex items-start gap-2">
        {open ? <ChevronDown size={16} className="mt-0.5 shrink-0" /> : <ChevronRight size={16} className="mt-0.5 shrink-0" />}
        <div className="flex-1 min-w-0">
          <div className="text-sm">
            <b>{l.product_name || '(이름 없음)'}</b> <span className="text-xs text-gray-400">#{l.id} · {l.product_no || '번호 없음'}</span>
          </div>
          <div className="text-xs text-gray-500 flex flex-wrap gap-x-2">
            <span>입고 {l.received_at}{late && <span className="text-orange-600"> (등록 {l.created_at.slice(5, 10)})</span>}</span>
            <span>개당 {num(l.unit_cost)}원</span>
            {l.paid ? <span className="text-green-700">결제됨</span> : <span className="text-orange-600">미결제</span>}
            {l.status !== 'ACTIVE' && <span>{l.status}</span>}
            {l.qty_left > 0 && l.age > 30 && <span className="text-red-600">{l.age}일 경과</span>}
            {l.memo && <span className="truncate max-w-[22rem]">{l.memo}</span>}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className={clsx('text-sm font-bold', l.qty_left ? 'text-blue-700' : 'text-gray-400')}>남은 {l.qty_left}</div>
          <div className="text-xs text-gray-400">입고 {l.qty_in} · 나감 {l.qty_in - l.qty_left}</div>
        </div>
      </button>
      {open && (
        <div className="pl-9 pr-3 pb-3">
          {l.moves.length === 0
            ? <div className="text-xs text-gray-500">아직 나간 것이 없습니다.</div>
            : <MoveTable moves={l.moves} nm={nm} openSearch={openSearch} running={l.qty_in} />}
        </div>
      )}
    </div>
  );
}

function MoveTable({ moves, nm, showOwner, openSearch, running }: {
  moves: StMove[]; nm: Record<string, string>; showOwner?: boolean; openSearch: (q: string) => void; running?: number;
}) {
  let left = running ?? 0;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-gray-500"><tr>
          <th className="text-left px-2 py-1">날짜</th><th className="text-left px-2">어떻게</th>
          <th className="text-left px-2">{showOwner ? '재고 주인' : '판매자'}</th>
          {showOwner && <th className="text-left px-2">상품</th>}
          <th className="text-right px-2">수량</th>{running !== undefined && <th className="text-right px-2">남음</th>}
          <th className="text-right px-2">웃돈</th><th className="text-left px-2">주문번호</th><th className="text-left px-2">정산</th>
        </tr></thead>
        <tbody>
          {moves.map(m => {
            left -= m.qty;
            return (
              <tr key={m.id} className={clsx('border-t', m.platform === 'adjust' && 'bg-yellow-50')}>
                <td className="px-2 py-1 whitespace-nowrap">{m.date}</td>
                <td className="px-2 whitespace-nowrap">{m.how}{m.is_cross ? ' 🔀' : ''}</td>
                <td className="px-2 whitespace-nowrap">{showOwner ? (nm[m.owner] || m.owner) : (nm[m.seller] || m.seller)}</td>
                {showOwner && <td className="px-2">{(m.product_name || '').slice(0, 24)}</td>}
                <td className="text-right px-2">{m.qty}</td>
                {running !== undefined && <td className="text-right px-2 text-gray-500">{left}</td>}
                <td className="text-right px-2">{m.surcharge ? num(m.surcharge) : '—'}</td>
                <td className="px-2">
                  {m.order_no && !m.order_no.startsWith('ADJ-')
                    ? <button onClick={() => openSearch(m.order_no)} className="text-blue-600 underline">{m.order_no}</button>
                    : <span className="text-gray-400">{m.platform === 'adjust' ? '주문 기록 없음' : m.order_no}</span>}
                </td>
                <td className="px-2 whitespace-nowrap">{m.is_cross ? (m.settle_status === 'SETTLED' ? '✅' : '⏳ 대기') : ''}</td>
              </tr>);
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── 🔍 검색 ────────────────────────────────────────────────
function SearchTab({ q, setQ, onErr, openUser }: {
  q: string; setQ: (s: string) => void; onErr: (s: string) => void; openUser: (u: string) => void;
}) {
  const [text, setText] = useState(q);
  const [r, setR] = useState<StSearch | null>(null);
  const [loading, setLoading] = useState(false);
  const run = useCallback(async (s: string) => {
    if (s.trim().length < 2) return;
    setLoading(true);
    try { setR(await stock.search(s.trim())); } catch (e: any) { onErr(e.message || String(e)); }
    finally { setLoading(false); }
  }, [onErr]);
  useEffect(() => { setText(q); if (q) run(q); }, [q, run]);
  const nm = r?.names || {};
  const isOrder = /^[0-9-]{8,}$/.test(r?.q || '');

  return (
    <div className="space-y-3">
      <form onSubmit={e => { e.preventDefault(); setQ(text); run(text); }} className="flex gap-2">
        <input value={text} onChange={e => setText(e.target.value)} autoFocus
          placeholder="상품명 · 코스트코 번호 · 주문번호 · 사용자 이름"
          className="flex-1 border rounded-lg px-3 py-2 text-sm bg-white" />
        <button type="submit" className="flex items-center gap-1 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm">
          <Search size={16} /> 검색</button>
      </form>
      <div className="text-xs text-gray-500">주문번호를 넣으면 그 주문이 <b>누구 재고에서 나갔는지 / 재고에서 안 나갔는지</b>, 정산·적립까지 한 번에 봅니다.</div>
      {loading && <div className="text-sm text-gray-500">찾는 중…</div>}
      {r && !loading && (
        <>
          {isOrder && (
            <section className="bg-white rounded-xl shadow-sm p-3 text-sm space-y-1">
              <div className="font-bold">주문 {r.q}</div>
              {r.dispatch.length === 0 && <div className="text-gray-500">발송 기록 없음</div>}
              {r.dispatch.map((d, i) => (
                <div key={i}>📮 {d.date} <b>{nm[d.username] || d.username}</b> 발송 ({d.platform}) · {d.product_name.slice(0, 40)}
                  {d.status && <span className={clsx('ml-1', /취소|반품/.test(d.status) ? 'text-red-600 font-bold' : 'text-gray-500')}>[{d.status}]</span>}</div>))}
              <div className={clsx('font-bold', r.moves.length ? 'text-blue-700' : 'text-orange-600')}>
                {r.moves.length
                  ? `📦 재고에서 나감 — ${r.moves.map(m => `${nm[m.owner] || m.owner} 재고 lot ${m.lot_id} ${m.qty}개(${m.how})`).join(' · ')}`
                  : '📦 재고 차감 없음 — 매장에서 새로 샀거나, 재고에서 보냈는데 빠지지 않은 주문'}
              </div>
              {r.settle.map((s, i) => (
                <div key={i}>🧾 {s.settle_date} 정산 · {nm[s.username] || s.username} · {s.product_name.slice(0, 30)} {s.qty}개 · {num(s.amount)}원
                  · {s.source}{s.stock_owner && ` · 재고 주인 ${nm[s.stock_owner] || s.stock_owner}`}</div>))}
              {r.deposits.map((d, i) => (
                <div key={i}>💰 {d.tx_date} {nm[d.username] || d.username} 예치금 {num(d.amount)}원 · {d.memo}</div>))}
            </section>
          )}
          <section className="bg-white rounded-xl shadow-sm">
            <div className="px-3 py-2 text-sm font-bold border-b">입고 {r.lots.length}건</div>
            {r.lots.length === 0 && <div className="p-3 text-sm text-gray-500">해당 입고가 없습니다.</div>}
            {Object.entries(groupBy(r.lots, l => l.owner)).map(([o, ls]) => (
              <div key={o}>
                <div className="px-3 py-1 bg-gray-50 text-xs flex items-center gap-2">
                  <b>{nm[o] || o}</b> 남은 {ls.reduce((s, l) => s + l.qty_left, 0)}개
                  <button onClick={() => openUser(o)} className="text-blue-600 underline">재고 카드</button>
                </div>
                {ls.map(l => <LotRow key={l.id} l={l} nm={nm} openSearch={s => { setQ(s); }} />)}
              </div>
            ))}
          </section>
          {!isOrder && r.deposits.length > 0 && (
            <section className="bg-white rounded-xl shadow-sm p-3 text-xs space-y-1">
              <div className="font-bold text-sm">예치금 적립 기록</div>
              {r.deposits.map((d, i) => <div key={i}>{d.tx_date} {nm[d.username] || d.username} {num(d.amount)}원 · {d.memo}</div>)}
            </section>
          )}
        </>
      )}
    </div>
  );
}

// ── 🩺 이상 점검 ───────────────────────────────────────────
function ChecksTab({ onErr, openUser, openSearch }: {
  onErr: (s: string) => void; openUser: (u: string) => void; openSearch: (q: string) => void;
}) {
  const [c, setC] = useState<StChecks | null>(null);
  const [loading, setLoading] = useState(false);
  const [kind, setKind] = useState('');
  const load = useCallback(async (force = false) => {
    setLoading(true);
    try { setC(await stock.checks(force)); } catch (e: any) { onErr(e.message || String(e)); }
    finally { setLoading(false); }
  }, [onErr]);
  useEffect(() => { load(); }, [load]);
  const items = (c?.items || []).filter(i => !kind || i.kind === kind);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => setKind('')} className={clsx('px-2 py-1 rounded-full text-xs border', !kind && 'bg-gray-900 text-white')}>
          전체 {c?.items.length ?? 0}</button>
        {Object.entries(c?.counts || {}).map(([k, n]) => (
          <button key={k} onClick={() => setKind(k)}
            className={clsx('px-2 py-1 rounded-full text-xs border', kind === k && 'bg-gray-900 text-white')}>{KIND_LABEL[k] || k} {n}</button>))}
        <button onClick={() => load(true)} className="ml-auto flex items-center gap-1 px-2 py-1 border rounded text-xs bg-white">
          <RefreshCw size={14} className={clsx(loading && 'animate-spin')} /> 다시 점검</button>
      </div>
      <div className="text-xs text-gray-500">
        {c ? `${c.checked_at} 점검` : '점검 중… (발송 대조 때문에 10초쯤 걸립니다)'} · 여기서는 찾기만 합니다.
        고치기는 기존 화면 재고 관리 → 📊 전체 재고의 <b>🛠 재고 고치기</b>·<b>🔁 재고 넘기기</b>에서 하세요.
      </div>
      {c && items.length === 0 && <div className="bg-green-50 text-green-800 p-3 rounded-lg text-sm">✅ 이상 없음</div>}
      {items.map((i, n) => (
        <div key={n} className={clsx('bg-white rounded-xl shadow-sm p-3 border-l-4',
          i.level === 'high' ? 'border-red-500' : i.level === 'mid' ? 'border-orange-400' : 'border-gray-300')}>
          <div className="text-sm font-bold">{i.title}</div>
          <div className="text-xs text-gray-600 mt-0.5">{i.detail}</div>
          <div className="flex flex-wrap gap-3 mt-1 text-xs">
            {i.fix && <span className="text-gray-500">👉 {i.fix}</span>}
            {i.owner && <button onClick={() => openUser(i.owner)} className="text-blue-600 underline">{i.owner_name} 재고 카드</button>}
          </div>
          {i.rows.length > 0 && (
            <details className="mt-1">
              <summary className="text-xs cursor-pointer text-gray-500">해당 발송 {i.rows.length}건</summary>
              <div className="text-xs mt-1 space-y-0.5">
                {i.rows.map(r => (
                  <div key={r.order_no}>{r.date} <button onClick={() => openSearch(r.order_no)} className="text-blue-600 underline">{r.order_no}</button> {r.product_name.slice(0, 36)}</div>))}
              </div>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

function groupBy<T>(xs: T[], f: (x: T) => string): Record<string, T[]> {
  const o: Record<string, T[]> = {};
  for (const x of xs) (o[f(x)] ||= []).push(x);
  return o;
}

function Kpi({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) {
  return (
    <div className={clsx('bg-white rounded-xl shadow-sm p-3', warn && 'ring-1 ring-orange-300')}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className={clsx('text-lg font-bold', warn && 'text-orange-600')}>{value}</div>
      {sub && <div className="text-[11px] text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}
