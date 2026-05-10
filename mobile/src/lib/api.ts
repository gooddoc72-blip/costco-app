/**
 * 클라이언트 fetch 헬퍼 — credentials 항상 포함, 401 시 로그인 페이지로 이동
 */
export async function apiGet<T = unknown>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: 'include', cache: 'no-store' });
  if (res.status === 401 && typeof window !== 'undefined') {
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function apiPost<T = unknown>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.error || `${res.status} ${res.statusText}`);
  }
  return res.json();
}
