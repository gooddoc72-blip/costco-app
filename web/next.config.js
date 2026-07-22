/** @type {import('next').NextConfig} */
// 정적 export — 로컬 빌드 후 out/ 를 nginx가 /calc/ 로 서빙(VPS에 node 불필요).
// basePath /calc: 앱은 /calc/ 하위에 위치. API 호출은 절대경로 /api/* (동일 오리진).
module.exports = {
  output: 'export',
  basePath: '/calc',
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};
