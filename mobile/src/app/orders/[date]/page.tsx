'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { apiGet } from '@/lib/api';
import { won } from '@/lib/fmt';

interface OrderRow {
  id: number;
  recipient: string;
  product_name: string;
  product_no: string;
  option_info: string;
  qty: number;
  order_amount: number;
  shipping_fee: number;
  settlement: number;
  cost_price: number;
  delivery_cost: number;
  box_cost: number;
  profit: number;
  matched: number;
}

export default function OrderDateDetailPage() {
  const params = useParams<{ date: string }>();
  const date = decodeURIComponent(params.date);
  const [rows, setRows] = useState<OrderRow[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    apiGet<{ orders: OrderRow[] }>(`/api/orders?date=${encodeURIComponent(date)}`)
      .then((r) => setRows(r.orders))
      .catch((e) => setError(e?.message || '데이터 로드 실패'));
  }, [date]);

  const totalSales = rows.reduce((a, r) => a + (r.order_amount || 0), 0);
  const totalProfit = rows.reduce((a, r) => a + (r.profit || 0), 0);

  return (
    <>
      <Header title={date} subtitle={`${rows.length}건 · 매출 ${won(totalSales)} · 수익 ${won(totalProfit)}`} />
      <main className="px-4 pt-3 pb-20">
        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mb-3">
            {error}
          </div>
        )}
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.id} className="bg-white rounded-xl px-4 py-3 shadow-sm border border-gray-100">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-gray-500">{r.recipient}</p>
                  <p className="text-sm font-medium text-gray-900 truncate">
                    {r.product_name}
                  </p>
                  {r.option_info && (
                    <p className="text-xs text-gray-400 truncate mt-0.5">{r.option_info}</p>
                  )}
                </div>
                <span
                  className={
                    'text-[10px] px-2 py-0.5 rounded-full whitespace-nowrap ' +
                    (r.matched
                      ? 'bg-green-50 text-green-700 border border-green-200'
                      : 'bg-gray-50 text-gray-500 border border-gray-200')
                  }
                >
                  {r.matched ? '매칭됨' : '미매칭'}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2 mt-2 text-xs">
                <div>
                  <span className="text-gray-400">수량</span>
                  <p className="font-semibold">{r.qty}</p>
                </div>
                <div>
                  <span className="text-gray-400">매출</span>
                  <p className="font-semibold">{won(r.order_amount)}</p>
                </div>
                <div>
                  <span className="text-gray-400">수익</span>
                  <p
                    className={
                      'font-semibold ' + (r.profit >= 0 ? 'text-green-600' : 'text-red-500')
                    }
                  >
                    {won(r.profit)}
                  </p>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </main>
      <BottomNav />
    </>
  );
}
