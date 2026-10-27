'use client';
/**
 * 화면 폭 — 기본은 폰 폭(480px) 카드, PC 업무 화면은 넓게.
 *
 * 관리자 업무 화면(정산·청구 등)은 표가 넓어 480px 안에서는 PC에서도 가로
 * 스크롤만 생긴다. 그런 경로만 넓게 펴고, 나머지 화면은 지금 모양 그대로 둔다.
 */
import { usePathname } from 'next/navigation';
import clsx from 'clsx';

/** 넓게 펼 경로(앞부분 일치) */
const WIDE = ['/billing', '/receipt-settle', '/trends', '/stock'];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const p = usePathname() || '';
  const wide = WIDE.some(w => p === w || p.startsWith(w + '/'));
  return (
    <div className={clsx('mx-auto min-h-screen bg-gray-50 relative',
      wide ? 'max-w-[1400px]' : 'max-w-[480px] shadow-xl md:shadow-2xl')}>
      {children}
    </div>
  );
}
