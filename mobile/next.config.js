/**
 * basePath — 기존 사이트(/app Streamlit, /api FastAPI, /calc 정적앱)와 충돌하지 않도록
 * 모바일 앱은 하위 경로에 격리 배포한다. 기본 '/m'.
 * ⚠️ 직접 쓴 절대경로(fetch('/api/...'), location.href)는 src/lib/basePath.ts의
 *    withBase()를 반드시 거칠 것 — Next는 그런 문자열엔 basePath를 안 붙인다.
 */
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? '/m';

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  basePath: BASE_PATH || undefined,
  env: { NEXT_PUBLIC_BASE_PATH: BASE_PATH },
  // SQLite/bcrypt 네이티브 모듈은 번들에 넣지 않고 런타임 require로 넘긴다.
  // ⚠️ Next 14 키 이름은 experimental.serverComponentsExternalPackages.
  //    (serverExternalPackages는 Next 15부터 — 14에서는 무시돼 빌드가 깨졌다)
  experimental: {
    serverComponentsExternalPackages: ['better-sqlite3', 'bcrypt'],
  },
  // PWA 헤더
  async headers() {
    return [
      {
        source: '/manifest.json',
        headers: [
          { key: 'Cache-Control', value: 'public, max-age=0, must-revalidate' },
        ],
      },
      {
        source: '/sw.js',
        headers: [
          { key: 'Cache-Control', value: 'public, max-age=0, must-revalidate' },
          { key: 'Content-Type', value: 'application/javascript; charset=utf-8' },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
