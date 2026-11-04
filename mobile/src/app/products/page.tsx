'use client';
/**
 * 📦 제품 DB — 분류 · 카테고리 · 검색 · 수정/삭제 · 정리 도구.
 *
 * 예전 /m 화면은 Next 서버가 개인 제품표만 직접 읽어서 매장가·이미지·품절 상태가
 * 없었고, 목록 조회가 basePath를 빠뜨려 운영에서는 아예 비어 있었다. 지금은
 * Streamlit 제품 DB와 같은 파이썬 함수(/api/pdb)를 부른다.
 * 네이버 API를 쓰는 관리 기능(스토어 상품 가져오기·혜택 일괄 적용·상품 등록·
 * 카페24 가격 수정·가격표 사진)은 아직 기존 화면에 있다.
 */
import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import { Search } from 'lucide-react';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import PdbRowCard from '@/components/products/PdbRowCard';
import PdbTools from '@/components/products/PdbTools';
import { pdb, PdbKind, PdbList, PdbSave } from '@/lib/client/pdb';
import { num } from '@/lib/fmt';

const PER = 20;

export default function ProductsPage() {
  const [me, setMe] = useState<{ username: string; is_admin: boolean } | null>(null);
  const [kind, setKind] = useState<PdbKind>('focus');
  const [category, setCategory] = useState('');
  const [q, setQ] = useState('');
  const [qInput, setQInput] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PdbList | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');

  useEffect(() => { pdb.me().then(setMe).catch(e => setErr(e.message)); }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await pdb.list({ kind, category, q, page, per: PER });
      setData(d);
      if (d.page !== page) setPage(d.page);
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setLoading(false); }
  }, [kind, category, q, page]);
  useEffect(() => { if (me) load(); }, [me, load]);

  const run = async <T,>(fn: () => Promise<T>, ok: (r: T) => string) => {
    setBusy(true); setErr(''); setMsg('');
    try { const r = await fn(); setMsg(ok(r)); await load(); }
    catch (e: any) { setErr(e.message || String(e)); throw e; }
    finally { setBusy(false); }
  };
  const onSave = (s: PdbSave) => run(() => pdb.save(s), () => '✅ 저장했습니다.');
  const onDelete = (id: number) => run(() => pdb.remove(id), () => '🗑 삭제했습니다.').catch(() => {});
  const onUnlock = (id: number) =>
    run(() => pdb.unlock(id), r => `✅ 분리 해제 — ${r.product_no} 매칭 복귀`).catch(() => {});

  const pickKind = (k: PdbKind) => { setKind(k); setCategory(''); setPage(1); };

  return (
    <>
      <Header title="📦 제품 DB" subtitle="매장가 · 소분 · 판매가 · 코스트코 번호"
        right={<a href="/app/" className="text-xs text-gray-500 underline">네이버 연동 기능(기존 화면)</a>} />
      <main className="px-3 md:px-6 pt-3 pb-24 space-y-3">
        {err && <div className="bg-red-50 text-red-700 p-3 rounded-lg text-sm">❌ {err}</div>}
        {msg && <div className="bg-green-50 text-green-700 p-3 rounded-lg text-sm">{msg}</div>}

        <div className="flex flex-wrap gap-1.5">
          {(data?.kinds || []).map(k => (
            <button key={k.key} onClick={() => pickKind(k.key)}
              className={clsx('px-2.5 py-1 rounded-full text-xs border',
                kind === k.key ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-200')}>
              {k.label} <span className="opacity-70">{num(k.count)}</span>
            </button>
          ))}
        </div>

        <form className="flex gap-2" onSubmit={e => { e.preventDefault(); setQ(qInput.trim()); setPage(1); }}>
          <div className="relative flex-1">
            <Search size={15} className="absolute left-2.5 top-2.5 text-gray-400" />
            <input value={qInput} onChange={e => setQInput(e.target.value)}
              placeholder="상품명 또는 상품번호 (띄어쓰기 상관없음)"
              className="w-full pl-8 pr-2 py-2 border border-gray-200 rounded-lg text-sm" />
          </div>
          <select value={category} onChange={e => { setCategory(e.target.value); setPage(1); }}
            className="border border-gray-200 rounded-lg text-sm px-2 max-w-[40%]">
            <option value="">카테고리 전체</option>
            {(data?.categories || []).map(c => <option key={c.name} value={c.name}>{c.name} ({c.count})</option>)}
          </select>
          <button className="bg-gray-800 text-white text-sm px-3 rounded-lg">검색</button>
        </form>

        <div className="flex items-center justify-between text-xs text-gray-500 px-1">
          <span>{loading ? '불러오는 중…' : `${num(data?.total || 0)}개${q ? ` · '${q}' 검색` : ''}`}
            {kind === 'focus' && !loading && ' · 안 팔린 등록 상품·온라인 카탈로그까지 보려면 전체'}</span>
          {q && <button onClick={() => { setQ(''); setQInput(''); }} className="underline">검색 지우기</button>}
        </div>

        {!!data?.extra.length && (
          <div className="bg-amber-50 rounded-xl p-3 text-xs">
            <div className="font-medium text-amber-800 mb-1">🧾 구매이력에만 있는 상품 {data.extra.length}개 — 가격DB 목록에는 없습니다</div>
            {data.extra.map(h => (
              <div key={h.product_no + h.name} className="flex justify-between gap-2 py-0.5">
                <span className="truncate">{h.product_no || '번호 없음'} · {h.name}</span>
                <span className="whitespace-nowrap">{num(h.paid)}원 · {h.receipt_date}</span>
              </div>
            ))}
          </div>
        )}

        <div className="grid gap-2 lg:grid-cols-2">
          {(data?.rows || []).map(r => (
            <PdbRowCard key={r.key} row={r} admin={!!me?.is_admin} busy={busy}
              onSave={onSave} onDelete={onDelete} onUnlock={onUnlock} />
          ))}
        </div>
        {!loading && data && !data.rows.length && (
          <div className="text-center text-sm text-gray-400 py-8">조건에 맞는 상품이 없습니다.</div>
        )}

        {data && data.pages > 1 && (
          <div className="flex items-center justify-center gap-2 text-sm">
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="px-3 py-1 border rounded disabled:opacity-40">이전</button>
            <span>{data.page} / {data.pages}</span>
            <button disabled={page >= data.pages} onClick={() => setPage(p => p + 1)} className="px-3 py-1 border rounded disabled:opacity-40">다음</button>
          </div>
        )}

        {me && (
          <div className="pt-4">
            <div className="text-sm font-bold text-gray-700 mb-2">🧹 정리 도구</div>
            <PdbTools admin={me.is_admin} me={me.username} onErr={setErr} onChanged={load} />
          </div>
        )}
      </main>
      <BottomNav />
    </>
  );
}
