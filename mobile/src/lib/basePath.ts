/**
 * basePath 헬퍼 — 앱이 하위 경로(/m)에 올라갈 때 쓰는 절대경로 접두사.
 *
 * Next.js는 <Link>·router·정적자산에는 basePath를 자동으로 붙이지만
 * fetch('/api/...')나 location.href='/login' 같은 '직접 쓴 절대경로'에는 붙이지 않는다.
 * 그런 곳은 반드시 이 헬퍼를 거칠 것. (안 그러면 /m 배포에서 404)
 *
 * 값은 빌드시 next.config.js의 basePath와 같아야 한다 (NEXT_PUBLIC_BASE_PATH).
 */
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || '';

export function withBase(path: string): string {
  if (!path.startsWith('/')) return path;          // 상대경로·외부URL은 그대로
  if (!BASE_PATH) return path;
  if (path.startsWith(BASE_PATH + '/')) return path;   // 이미 붙은 경우
  return BASE_PATH + path;
}
