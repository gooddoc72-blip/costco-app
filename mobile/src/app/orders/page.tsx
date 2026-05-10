'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { apiGet } from '@/lib/api';
import { won } from '@/lib/fmt';

interface DateRow {
  date: string;
  orders: number;
  qty: number;
  sales: number;
  profit: number;
}

export default function OrdersPage() {
  const [rows, setRows] = useState<DateRow[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    apiGet<{ dates: DateRow[] }>('/api/orders/dates')
      .then((r) => setRows(r.dates))
      .catch((e) => setError(e?.message || '데이터 로드 실패'));
  }, []);

  return (
    <>
      <Header title="주문 내역" subtitle="날짜별로 확인" />
      <main className="px-4 pt-3 pb-20">
        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mb-3">
            {error}
          </div>
        )}
        {rows.length === 0 && !error && (
          <div className="text-center text-gray-400 py-16">주문 내역이 없습니다</div>
        )}
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.date}>
              <Link
                href={`/orders/${r.date}`}
                className="block bg-white rounded-xl px-4 py-3 shadow-sm border border-gray-100 active:bg-gray-50"
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-gray-900">{r.date}</span>
                  <span className="text-xs text-gray-500">{r.orders}건 · {r.qty}개</span>
                </div>
                <div className="flex justify-between mt-1 text-sm">
                  <span className="text-gray-600">매출 {won(r.sales)}</span>
                  <span className="text-green-600 font-medium">수익 {won(r.profit)}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </main>
      <BottomNav />
    </>
  );
}
