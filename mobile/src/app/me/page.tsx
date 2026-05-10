'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Header from '@/components/Header';
import BottomNav from '@/components/BottomNav';
import { apiGet, apiPost } from '@/lib/api';
import { LogOut, ExternalLink } from 'lucide-react';

interface Me {
  username: string;
  display_name: string;
  is_admin: boolean;
}

export default function MePage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    apiGet<Me>('/api/auth/me').then(setMe).catch(() => router.push('/login'));
  }, [router]);

  async function logout() {
    setLoading(true);
    try {
      await apiPost('/api/auth/logout', {});
    } catch {}
    router.push('/login');
  }

  return (
    <>
      <Header title="내 정보" />
      <main className="px-4 pt-4 pb-20">
        {me && (
          <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-12 h-12 rounded-full bg-primary text-white flex items-center justify-center font-bold text-lg">
                {me.display_name?.[0] || me.username[0]}
              </div>
              <div>
                <p className="font-bold text-gray-900">
                  {me.display_name}
                  {me.is_admin && <span className="ml-2 text-xs bg-primary text-white px-2 py-0.5 rounded">관리자</span>}
                </p>
                <p className="text-xs text-gray-500">{me.username}</p>
              </div>
            </div>
          </div>
        )}

        <a
          href="https://costcobiz.shop"
          className="mt-3 flex items-center justify-between bg-white rounded-xl px-4 py-3 shadow-sm border border-gray-100"
        >
          <span className="text-sm text-gray-700">PC 버전으로 이동</span>
          <ExternalLink size={16} className="text-gray-400" />
        </a>

        <button
          onClick={logout}
          disabled={loading}
          className="mt-3 w-full flex items-center justify-center gap-2 bg-white rounded-xl px-4 py-3 shadow-sm border border-red-200 text-red-600 disabled:opacity-50"
        >
          <LogOut size={16} />
          <span className="text-sm font-medium">{loading ? '로그아웃 중...' : '로그아웃'}</span>
        </button>
      </main>
      <BottomNav />
    </>
  );
}
