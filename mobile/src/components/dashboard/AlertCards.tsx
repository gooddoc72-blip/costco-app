/** 운영 알림 카드 — 미매칭/미발송/0원 가격 */
import Link from 'next/link';
import { AlertCircle, PackageX, DollarSign } from 'lucide-react';
import type { DashboardAlerts } from '@/lib/services/dashboard';

interface Props { alerts: DashboardAlerts }

export default function AlertCards({ alerts }: Props) {
  const items: Array<{ key: string; n: number; label: string; href: string; icon: any; color: string }> = [
    { key: 'unmatched', n: alerts.unmatched, label: '미매칭 주문', href: '/products', icon: AlertCircle, color: 'amber' },
    { key: 'pending',   n: alerts.pendingDispatch, label: '미발송 주문', href: '/tracking', icon: PackageX, color: 'red' },
    { key: 'zero',      n: alerts.zeroPrice, label: '0원 제품', href: '/products', icon: DollarSign, color: 'gray' },
  ];
  const visible = items.filter(x => x.n > 0);
  if (visible.length === 0) {
    return (
      <section className="bg-green-50 border border-green-200 text-green-800 rounded-xl p-3 text-sm">
        ✅ 운영 알림 없음
      </section>
    );
  }
  return (
    <section className="grid grid-cols-3 gap-2">
      {visible.map(it => (
        <Link key={it.key} href={it.href}
          className={`block rounded-xl p-3 text-center border ${COLORS[it.color]}`}>
          <it.icon size={18} className="mx-auto mb-1" />
          <div className="text-lg font-bold">{it.n}</div>
          <div className="text-[10px] mt-0.5 opacity-80">{it.label}</div>
        </Link>
      ))}
    </section>
  );
}

const COLORS: Record<string, string> = {
  amber: 'bg-amber-50 border-amber-200 text-amber-800',
  red: 'bg-red-50 border-red-200 text-red-800',
  gray: 'bg-gray-50 border-gray-200 text-gray-700',
};
