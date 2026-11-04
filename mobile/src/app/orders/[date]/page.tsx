'use client';
/** 옛 주소(/orders/날짜) — 저장된 주문은 /orders 아래 '저장된 주문'에서 날짜별로 본다. */
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function OrdersDateRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace('/orders'); }, [router]);
  return null;
}
