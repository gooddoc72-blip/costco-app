// Service Worker 골격 — Phase 1: 단순 설치만, 캐싱은 Phase 4에서 추가
const CACHE_NAME = 'costcobiz-v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))),
    ),
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Phase 1: 패스스루
});
