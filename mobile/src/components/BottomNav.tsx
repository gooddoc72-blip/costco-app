'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, ShoppingBag, Receipt, Truck, Settings } from 'lucide-react';
import clsx from 'clsx';

const items = [
  { href: '/dashboard', label: '대시보드', icon: Home },
  { href: '/orders', label: '주문', icon: ShoppingBag },
  { href: '/tracking', label: '송장', icon: Truck },
  { href: '/profit', label: '수익', icon: Receipt },
  { href: '/settings', label: '설정', icon: Settings },
];

export default function BottomNav() {
  const pathname = usePathname();
  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 safe-bottom z-40">
      <ul className="flex justify-around h-14">
        {items.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + '/');
          return (
            <li key={href} className="flex-1">
              <Link
                href={href}
                className={clsx(
                  'flex flex-col items-center justify-center h-full text-xs',
                  active ? 'text-primary' : 'text-gray-500',
                )}
              >
                <Icon size={22} strokeWidth={active ? 2.5 : 2} />
                <span className="mt-0.5">{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
