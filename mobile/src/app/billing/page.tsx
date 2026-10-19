'use client';
/**
 * 💳 정산·청구 (관리자) — Streamlit '정산·청구' 화면을 옮긴 첫 화면.
 *
 * Streamlit은 체크 하나만 바꿔도 화면 전체를 처음부터 다시 계산했다. 여기서는
 * 바꾼 칸만 다시 그리고, 저장 버튼을 눌렀을 때만 서버(파이썬 API)를 부른다.
 * 금액 계산·저장은 전부 기존 파이썬 함수(/api/billing/*)가 한다.
 *
 * PC에서는 넓게(AppShell이 /billing 경로를 넓은 화면으로 둔다), 폰에서는 표가 가로로 스크롤된다.
 */
import { useState } from 'react';
import clsx from 'clsx';
import Header from '@/components/Header';
import DayTab from '@/components/billing/DayTab';
import DepositTab from '@/components/billing/DepositTab';
import UnpaidTab from '@/components/billing/UnpaidTab';
import UserTab from '@/components/billing/UserTab';

const TABS = [
  { key: 'day', label: '📋 일별 정산·청구' },
  { key: 'user', label: '👤 사용자별 입금' },
  { key: 'deposit', label: '💳 예치금' },
  { key: 'unpaid', label: '🔴 미입금자' },
] as const;
type TabKey = typeof TABS[number]['key'];

export default function BillingPage() {
  const [tab, setTab] = useState<TabKey>('day');
  const [jump, setJump] = useState<string>('');
  const [jumpUser, setJumpUser] = useState<string>('');

  return (
    <>
      <Header title="💳 정산 · 청구" subtitle="청구액은 물건값만 · 택배·포장비 별도" right={
        <a href="/app/" className="text-xs text-gray-500 underline">기존 화면</a>
      } />
      <main className="px-3 md:px-6 pt-3 pb-16 space-y-3">
        <div className="flex gap-1 overflow-x-auto">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={clsx('px-3 py-2 rounded-lg text-sm whitespace-nowrap border',
                tab === t.key ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-700')}>
              {t.label}
            </button>
          ))}
        </div>
        {tab === 'day' && <DayTab key={jump || 'default'} initialDay={jump || undefined} />}
        {tab === 'user' && <UserTab key={jumpUser || 'default'} initialUser={jumpUser || undefined}
          onGoDay={d => { setJump(d); setTab('day'); }} />}
        {tab === 'deposit' && <DepositTab />}
        {tab === 'unpaid' && <UnpaidTab onGoDay={d => { setJump(d); setTab('day'); }}
          onGoUser={u => { setJumpUser(u); setTab('user'); }} />}
        <p className="text-[11px] text-gray-400">
          아직 기존 화면에서만 되는 것: 품목 삭제(오매칭) · 청구 전 초기화 · 사용자별 엑셀 · 월별 정리 · 정산 이력 · 예치금 수동 조정/삭제.
        </p>
      </main>
    </>
  );
}
