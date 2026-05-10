# costcobiz mobile (PWA)

## Phase 1 — 골격 + PWA 설정 (완료)

```bash
cd mobile
npm install
cp .env.example .env.local
npm run dev
```

http://localhost:3000

## 구조

| 경로 | 역할 |
|---|---|
| `src/app/page.tsx` | 홈(스플래시) |
| `src/app/layout.tsx` | 루트 레이아웃 + PWA 메타 |
| `src/lib/db.ts` | SQLite (auth.db / admin.db / {user}.db) 접근 |
| `src/lib/session.ts` | Streamlit 세션 토큰 호환 검증 |
| `public/manifest.json` | PWA manifest |
| `public/sw.js` | Service Worker (Phase 4에서 캐싱 추가) |

## 다음 Phase

- **2** — DB 접근 layer + REST API
- **3** — 로그인 페이지 (기존 토큰 호환)
- **4** — 대시보드 (KPI 카드 + 차트)
- **5** — 주문 확인/상태 관리
- **6** — 수익 계산 (영수증 매칭)
- **7** — nginx 모바일/PC 자동 분기
