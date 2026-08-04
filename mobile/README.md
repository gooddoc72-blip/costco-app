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

## 운영 배포 (2026-08 적용)

`https://cocobiz.shop/m/` — 기존 화면을 건드리지 않고 `/m` 하위경로에 격리 배포.

| 항목 | 값 |
|---|---|
| basePath | `/m` (`next.config.js`, `NEXT_PUBLIC_BASE_PATH`) |
| 포트 | 3000 (systemd `costco-mobile`) |
| nginx | 기존 `costco-app` 설정에 `location /m/` 블록만 추가 |
| 데이터 | `COSTCO_DATA_DIR=/opt/costco-app/data` (Streamlit과 같은 SQLite) |
| 세션 | `sid` 쿠키 — Streamlit 세션 테이블 공용 |

```bash
# 코드 갱신 후 재배포
cd /opt/costco-app && git pull origin main
cd mobile && npm install --no-audit --no-fund
NODE_OPTIONS=--max-old-space-size=1024 npm run build   # 2GB RAM이라 힙 상한 필수
sudo systemctl restart costco-mobile
```

⚠️ `deploy/setup.sh` / `deploy/nginx-snippet.conf`는 **전면 컷오버**용이라
   그대로 실행하면 Streamlit이 `/legacy/`로 밀린다. 위 절차를 쓸 것.

⚠️ 절대경로를 직접 쓸 때(`fetch('/api/..')`, `location.href`)는 반드시
   `src/lib/basePath.ts`의 `withBase()`를 거칠 것 — Next는 basePath를 안 붙인다.
