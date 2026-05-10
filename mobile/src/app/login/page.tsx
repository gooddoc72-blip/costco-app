'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { apiPost } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await apiPost('/api/auth/login', { username, password, remember });
      router.push('/dashboard');
    } catch (e: any) {
      setError(e?.message || '로그인 실패');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex flex-col px-6 pt-20">
      <div className="text-center mb-8">
        <div className="text-5xl mb-2">📦</div>
        <h1 className="text-2xl font-bold text-primary">costcobiz</h1>
        <p className="text-xs text-gray-500 mt-1">코스트코 주문 수익 관리</p>
      </div>
      <form onSubmit={onSubmit} className="space-y-4 max-w-sm mx-auto w-full">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">아이디</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            required
            className="w-full h-12 px-4 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-primary outline-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">비밀번호</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            className="w-full h-12 px-4 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-primary outline-none"
          />
        </div>
        <label className="flex items-center text-sm text-gray-700">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="mr-2 w-4 h-4 accent-primary"
          />
          자동 로그인 (30일간 유지)
        </label>

        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full h-12 bg-primary hover:bg-primary-dark disabled:opacity-60 text-white font-medium rounded-lg transition"
        >
          {loading ? '로그인 중...' : '로그인'}
        </button>
      </form>

      <p className="text-center text-xs text-gray-400 mt-12">
        PC 버전: <a href="https://costcobiz.shop" className="underline">costcobiz.shop</a>
      </p>
    </main>
  );
}
