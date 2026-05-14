'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import { Home, ShoppingBag, Receipt, Truck, MoreHorizontal } from 'lucide-react';
import clsx from 'clsx';
import MoreMenu from './MoreMenu';

const items = [
  { href: '/dashboard', label: '대시보드', icon: Home },
  { href: '/orders', label: '주문', icon: ShoppingBag },
  { href: '/tracking', label: '송장', icon: Truck },
  { href: '/profit', label: '수익', icon: Receipt },
];

export default function BottomNav() {
  const pathname = usePathname();
  const [moreOpen, setMoreOpen] = useState(false);
  return (
    <>
      <nav className="fixed bottom-0 left-1/2 -translate-x-1/2 w-full max-w-[480px] bg-white border-t border-gray-200 safe-bottom z-40">
        <ul className="flex justify-around h-14">
          {items.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + '/');
            return (
              <li key={href} className="flex-1">
                <Link href={href}
                  className={clsx('flex flex-col items-center justify-center h-full text-xs',
                    active ? 'text-primary' : 'text-gray-500')}>
                  <Icon size={22} strokeWidth={active ? 2.5 : 2} />
                  <span className="mt-0.5">{label}</span>
                </Link>
              </li>
            );
          })}
          <li className="flex-1">
            <button onClick={() => setMoreOpen(true)}
              className={clsx('w-full flex flex-col items-center justify-center h-full text-xs',
                moreOpen ? 'text-primary' : 'text-gray-500')}>
              <MoreHorizontal size={22} />
              <span className="mt-0.5">더보기</span>
            </button>
          </li>
        </ul>
      </nav>
      <MoreMenu open={moreOpen} onClose={() => setMoreOpen(false)} />
    </>
  );
}
