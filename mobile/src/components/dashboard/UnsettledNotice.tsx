'use client';
/**
 * 💰 미정산 금액 — 대시보드 맨 위 카드 + 로그인 팝업(하루 한 번, 금액이 바뀌면 다시).
 * 계산은 서버(unsettled.py) 한 곳 — PC 홈 카드와 같은 숫자다.
 * 미정산이 없는 사용자에게는 아무것도 그리지 않는다.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { call } from '@/lib/client/billing';

interface Item { amount: number; unpaid: number; balance: number; days: number; oldest: string }
interface Row extends Item { username: string; name: string }
type Res =
  | { admin: false; item: Item | null }
  | { admin: true; count: number; total: number; rows: Row[] };

const SEEN_KEY = 'unsettled_seen';
const won = (n: number) => n.toLocaleString('ko-KR');
const today = () => new Date().toLocaleDateString('sv-SE');   // YYYY-MM-DD

export default function UnsettledNotice() {
  const [res, setRes] = useState<Res | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    call<Res>('GET', '/api/billing/me/unsettled').then(r => {
      setRes(r);
      if (!r.admin && r.item) {
        let seen = '';
        try { seen = localStorage.getItem(SEEN_KEY) || ''; } catch { /* 저장소 못 씀 */ }
        if (seen !== `${today()}|${r.item.amount}`) setOpen(true);
      }
    }).catch(() => {});
  }, []);

  if (!res) return null;

  if (res.admin) {
    if (!res.count) return null;
    return (
      <Link href="/billing"
        className="block bg-red-50 border border-red-200 text-red-800 rounded-xl p-3 text-sm">
        <div className="font-bold">💰 미정산 {res.count}명 · 합계 {won(res.total)}원 ▶</div>
        <div className="text-xs mt-1 opacity-80">
          {res.rows.slice(0, 5).map(r => `${r.name} ${won(r.amount)}원`).join(' · ')}
          {res.count > 5 ? ` 외 ${res.count - 5}명` : ''}
        </div>
      </Link>
    );
  }

  const it = res.item;
  if (!it) return null;
  const balTxt = it.balance < 0 ? ` + 예치금 부족분 ${won(-it.balance)}원`
    : it.balance > 0 ? ` − 예치금 ${won(it.balance)}원` : '';

  function hideToday() {
    try { localStorage.setItem(SEEN_KEY, `${today()}|${it!.amount}`); } catch { /* 무시 */ }
    setOpen(false);
  }

  return (
    <>
      <button onClick={() => setOpen(true)}
        className="w-full text-left bg-red-50 border border-red-200 text-red-800 rounded-xl p-3 text-sm">
        <div className="font-bold">💰 미정산 금액 {won(it.amount)}원</div>
        <div className="text-xs mt-1 opacity-80">
          {it.days
            ? <>미입금 {it.days}일치{it.oldest ? ` · ${it.oldest}부터` : ''} · 미입금 청구 {won(it.unpaid)}원{balTxt}</>
            : <>예치금 부족분 {won(-it.balance)}원 (차감은 됐는데 예치금이 모자람)</>}
        </div>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center px-6"
          onClick={() => setOpen(false)}>
          <div className="bg-white rounded-2xl w-full max-w-sm p-5 shadow-xl"
            onClick={e => e.stopPropagation()}>
            <div className="text-base font-bold text-gray-900">💰 미정산 금액 안내</div>
            <div className="text-2xl font-bold text-red-600 mt-3">{won(it.amount)}원</div>
            <ul className="text-sm text-gray-700 mt-3 space-y-1">
              <li>미입금 청구 <b>{won(it.unpaid)}원</b> ({it.days}일치)</li>
              {it.balance !== 0 && <li>예치금 잔액 <b>{won(it.balance)}원</b></li>}
              {it.oldest && <li>가장 오래된 미입금: <b>{it.oldest}</b></li>}
            </ul>
            <p className="text-xs text-gray-500 mt-3">입금하시면 관리자가 확인 후 정산 처리합니다.</p>
            <div className="grid grid-cols-2 gap-2 mt-4">
              <button onClick={hideToday}
                className="py-2.5 rounded-lg border border-gray-200 text-sm text-gray-700">
                오늘 그만 보기
              </button>
              <button onClick={() => setOpen(false)}
                className="py-2.5 rounded-lg bg-primary text-white text-sm font-medium">
                확인
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
