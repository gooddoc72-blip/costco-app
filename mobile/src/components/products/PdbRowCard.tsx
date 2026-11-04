'use client';
/**
 * 제품 DB 한 줄 — 보기 / ✏️ 수정 / 🗑 삭제.
 *
 * 공유 상품(공용 가격표에 있는 상품)은 매입가·상품명이 읽기전용이다. 관리자 영수증
 * 실단가가 원가의 근거라 판매자가 고치면 다른 판매자 원가까지 흔들린다.
 * 내 상품(공유DB에 없는 행)은 번호·이름·매입가까지 고칠 수 있다.
 */
import { useState } from 'react';
import clsx from 'clsx';
import { Pencil, Trash2, Unlock } from 'lucide-react';
import { num } from '@/lib/fmt';
import type { PdbRow, PdbSave } from '@/lib/client/pdb';

interface Props {
  row: PdbRow; admin: boolean; busy: boolean;
  onSave: (s: PdbSave) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  onUnlock: (id: number) => Promise<void>;
}

export default function PdbRowCard({ row, admin, busy, onSave, onDelete, onUnlock }: Props) {
  const [edit, setEdit] = useState(false);
  const dim = row.status !== 'sale';
  return (
    <div className={clsx('bg-white rounded-xl border p-3', edit ? 'border-blue-300' : 'border-gray-100')}>
      <div className="flex gap-3">
        {row.image_url
          ? <img src={row.image_url} alt="" className="w-12 h-12 rounded object-cover border border-gray-100 shrink-0" />
          : <div className="w-12 h-12 rounded bg-gray-100 shrink-0" />}
        <div className="min-w-0 flex-1">
          <div className={clsx('text-sm font-medium leading-snug', dim ? 'text-gray-400' : 'text-gray-900')}>
            {row.name}
            {row.status === 'oos' && <Badge cls="text-orange-600 bg-orange-50 border-orange-300">🟠 품절</Badge>}
            {row.status === 'susp' && <Badge cls="text-red-600 bg-red-50 border-red-300">🚫 판매중지</Badge>}
            {row.split_locked && <Badge cls="text-red-700 bg-red-50 border-red-200">🔒 가격분리</Badge>}
            {!row.is_shared && <Badge cls="text-gray-500 bg-gray-50 border-gray-200">내 상품</Badge>}
          </div>
          {row.sub_name && <div className="text-xs text-gray-400 truncate">{row.sub_name}</div>}
          <div className="text-xs text-gray-500 mt-0.5 flex flex-wrap gap-x-3">
            <span title="코스트코 상품번호(6~7자리)">
              코스트코 {row.costco_no
                ? <span className={row.costco_no_bad ? 'text-orange-600' : ''}>{row.costco_no_bad ? '⚠ ' : ''}{row.costco_no}</span>
                : <span className="text-orange-500">—</span>}
            </span>
            {row.naver_no && <span className="text-blue-500">🛍 {row.naver_no}{row.split_qty > 1 ? ' (소분 기준)' : ''}</span>}
            {row.category && <span>{row.category}</span>}
            {row.updated && <span>{row.updated}</span>}
          </div>
        </div>
        <div className="flex flex-col gap-1 shrink-0">
          <button onClick={() => setEdit(e => !e)} disabled={busy} title="수정"
            className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-600"><Pencil size={15} /></button>
          {row.private_id != null && (
            <button disabled={busy} title="내 상품 행 삭제"
              onClick={() => { if (confirm(`'${row.name}' 행을 삭제할까요?\n공용 가격표는 지워지지 않습니다.`)) onDelete(row.private_id!); }}
              className="p-1.5 rounded-lg hover:bg-red-50 text-red-500"><Trash2 size={15} /></button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mt-2 text-xs">
        <Cell label="매장가 🔒">
          {row.store_price > 0 ? <b className="text-green-700">{num(row.store_price)}</b> : <span className="text-gray-300">—</span>}
          {row.piece_price > 0 && <div className="text-blue-700">낱개 {num(row.piece_price)}</div>}
        </Cell>
        <Cell label="소분">{row.split_qty > 1 ? <b className="text-blue-700">÷{row.split_qty}</b> : <span className="text-gray-400">-</span>}</Cell>
        <Cell label="판매가">{row.sale_price > 0 ? <b className="text-indigo-900">{num(row.sale_price)}</b> : <span className="text-gray-300">-</span>}</Cell>
        <Cell label="고객배송비">{row.shipping_fee === 0 ? <b className="text-green-700">무료</b> : num(row.shipping_fee)}</Cell>
      </div>
      {row.online_price > 0 && <div className="text-[11px] text-blue-600 mt-1">🌐 온라인 {num(row.online_price)}원</div>}

      {edit && <EditForm row={row} admin={admin} busy={busy}
        onCancel={() => setEdit(false)}
        onUnlock={onUnlock}
        onSave={async s => { try { await onSave(s); setEdit(false); } catch { /* 오류는 위에 표시된다 */ } }} />}
    </div>
  );
}

function Badge({ cls, children }: { cls: string; children: React.ReactNode }) {
  return <span className={clsx('ml-1 inline-block text-[10px] font-semibold px-1.5 py-px rounded border align-middle', cls)}>{children}</span>;
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-50 rounded-lg px-2 py-1.5">
      <div className="text-[10px] text-gray-400">{label}</div>
      <div>{children}</div>
    </div>
  );
}

function EditForm({ row, admin, busy, onSave, onCancel, onUnlock }: {
  row: PdbRow; admin: boolean; busy: boolean;
  onSave: (s: PdbSave) => Promise<void>; onCancel: () => void; onUnlock: (id: number) => Promise<void>;
}) {
  // 가격 분리된 행의 실제 번호 칸은 비어 있다(원본은 따로 보관). 원본을 채워 저장하면
  // 해제를 누르지 않았는데 분리가 풀린다.
  const [pno, setPno] = useState(row.split_locked ? '' : row.costco_no);
  const [name, setName] = useState(row.costco_name);
  const [price, setPrice] = useState(row.unit_price);
  const [sale, setSale] = useState(row.sale_price);
  const [fee, setFee] = useState(row.shipping_fee);
  const [splitOn, setSplitOn] = useState(row.split_qty > 1);
  const [n, setN] = useState(Math.max(2, row.split_qty));
  const [ncat, setNcat] = useState(row.naver_category_id);

  const pack = row.is_shared ? row.unit_price : price;
  const curPno = row.is_shared ? row.costco_no : pno;
  const save = () => onSave({
    shared_id: row.shared_id, private_id: row.private_id, match_keyword: row.match_keyword,
    costco_name: row.is_shared ? row.costco_name : name,
    ...(row.is_shared ? {} : { product_no: pno, unit_price: price }),
    split_qty: splitOn ? n : 1, sale_price: sale, shipping_fee: fee,
    ...(row.is_shared && admin ? { naver_category_id: ncat } : {}),
  });

  return (
    <div className="mt-3 pt-3 border-t border-gray-100 space-y-2 text-sm">
      {row.split_locked && row.private_id != null && (
        <div className="flex items-center justify-between bg-red-50 text-red-700 text-xs p-2 rounded-lg">
          <span>🔒 가격 분리됨 — 코스트코 번호 매칭에서 빠져 있습니다 (원본 {row.costco_no})</span>
          <button disabled={busy} onClick={() => onUnlock(row.private_id!)}
            className="flex items-center gap-1 bg-white border border-red-200 px-2 py-1 rounded">
            <Unlock size={12} /> 해제</button>
        </div>
      )}
      {row.is_shared ? (
        <div className="text-xs text-gray-500 bg-blue-50 p-2 rounded-lg">
          🔗 공유 상품 — 매입가 <b>{num(row.unit_price)}원</b>·상품명은 읽기전용입니다(관리자 영수증 기준).
          소분·판매가·배송비만 내 값으로 저장됩니다.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <Inp label="코스트코 상품번호" value={pno} onChange={setPno} />
          <NumInp label="매입가(팩 단가)" value={price} onChange={setPrice} />
          <div className="col-span-2"><Inp label="상품명" value={name} onChange={setName} /></div>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <NumInp label="판매가(네이버)" value={sale} onChange={setSale} />
        <NumInp label="고객배송비 (0=무료)" value={fee} onChange={setFee} />
      </div>

      <div className="bg-gray-50 rounded-lg p-2 space-y-1">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={splitOn} onChange={e => setSplitOn(e.target.checked)} />
          🔵 소분 판매 <span className="text-xs text-gray-400">(코스트코 팩을 나눠 낱개로 팜)</span>
        </label>
        {splitOn ? (
          <>
            <div className="flex items-center gap-2 text-xs">
              <input type="number" min={2} max={50} value={n} onChange={e => setN(Math.max(2, Math.min(50, +e.target.value || 2)))}
                className="w-16 px-2 py-1 border border-gray-200 rounded" /> 개로 나눔
              {pack > 0 && <span>· 팩 {num(pack)}원 ÷ {n} = 낱개 원가 <b className="text-blue-700">{num(Math.floor(pack / n))}원</b></span>}
            </div>
            {pack <= 0 && <Note>⚠️ 매입가(팩 단가)가 0원입니다 — 팩 가격을 넣어야 낱개 원가가 계산됩니다.</Note>}
            {pack > 0 && row.split_qty <= 1 && (
              <Note>❗ 지금 매입가 {num(pack)}원이 <b>낱개값</b>이라면 팩 단가 <b>{num(pack * n)}원</b>으로 고쳐 넣으세요.
                그대로 두면 원가가 {num(Math.floor(pack / n))}원으로 또 깎입니다.</Note>
            )}
            {curPno.trim()
              ? <Note>⚠️ 코스트코 상품번호가 남아 있습니다 — 소분 상품은 번호를 <b>비우고</b> 네이버 상품번호로 매칭해야 공유DB 팩 가격이 이 행을 덮지 않습니다.</Note>
              : row.naver_no
                ? <div className="text-xs text-green-700">✅ 네이버 상품번호 {row.naver_no} 로 매칭됩니다 (코스트코 번호 없음 = 정상)</div>
                : <Note>⚠️ 네이버 상품번호가 없습니다 — 소분 상품은 이 번호로 매칭하므로 비어 있으면 주문과 연결되지 않습니다.</Note>}
          </>
        ) : <div className="text-xs text-gray-400">매입가 = 코스트코에서 사는 가격 그대로 (나누지 않음)</div>}
      </div>

      {row.is_shared && admin && (
        <Inp label="네이버 카테고리 ID (공유 — 모든 판매자 등록에 쓰임, 비우면 기본값)" value={ncat} onChange={setNcat} />
      )}
      <div className="flex gap-2">
        <button onClick={save} disabled={busy}
          className="flex-1 bg-blue-600 text-white rounded-lg py-2 font-medium disabled:bg-gray-300">✅ 저장</button>
        <button onClick={onCancel} className="px-4 border border-gray-200 rounded-lg">취소</button>
      </div>
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <div className="text-xs text-orange-700">{children}</div>;
}

function Inp({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <span className="text-[11px] text-gray-500">{label}</span>
      <input value={value} onChange={e => onChange(e.target.value)}
        className="w-full px-2 py-1.5 border border-gray-200 rounded-lg text-sm" />
    </label>
  );
}

function NumInp({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label className="block">
      <span className="text-[11px] text-gray-500">{label}</span>
      <input type="number" min={0} step={100} value={value} onChange={e => onChange(Math.max(0, +e.target.value || 0))}
        className="w-full px-2 py-1.5 border border-gray-200 rounded-lg text-sm" />
    </label>
  );
}
