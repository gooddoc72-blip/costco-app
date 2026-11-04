'use client';
/** 옛 주소(/orders/collect) — 주문 수집은 /orders 한 화면으로 합쳤다. */
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function OrdersCollectRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace('/orders'); }, [router]);
  return null;
}
