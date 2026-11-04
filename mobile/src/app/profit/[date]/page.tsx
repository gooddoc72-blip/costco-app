'use client';
/** 옛 주소(/profit/날짜) — 수익 계산 화면으로 날짜를 넘겨 보낸다. */
import { useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';

export default function ProfitDateRedirect() {
  const { date } = useParams<{ date: string }>();
  const router = useRouter();
  useEffect(() => { router.replace(`/profit?date=${date}`); }, [date, router]);
  return null;
}
