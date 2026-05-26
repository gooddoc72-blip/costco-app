# 코스트코 자동화 프로그램 — Claude 작업 가이드

## 프로젝트 개요
- **운영 URL**: cocobiz.shop (Cafe24 VPS, Ubuntu 22.04)
- **배포 방식**: GitHub main push → GitHub Actions → SSH `git pull` → `systemctl restart costco-app`
- **SSH 키**: `C:\Users\blocklabs02\.ssh\costco_key` (ubuntu@cocobiz.shop)
- **앱 경로**: `/opt/costco-app` (서비스명: costco-app)
- **GitHub Actions 장애 시**: SSH 직접 접속 후 수동 배포

```bash
# 수동 배포 명령
ssh -i C:/Users/blocklabs02/.ssh/costco_key ubuntu@cocobiz.shop \
  "cd /opt/costco-app && git pull origin main && sudo systemctl restart costco-app"
```

---

## 기술 스택
- **백엔드**: Python 3.11, Streamlit, SQLite
- **API**: 네이버 커머스 API, 쿠팡 Wing Open API (HMAC-SHA256)
- **DB**: `data/auth.db` (공유), `data/{username}.db` (사용자별)
- **구조**: `app.py` (라우터) + `pages_lib/*.py` (페이지별 render 함수)

---

## 파일 구조
```
app.py                  # 메인 라우터 (st.navigation)
services.py             # 비즈니스 로직 (매칭, 비용계산)
coupang_api.py          # 쿠팡 Wing Open API 클라이언트
naver_api.py            # 네이버 커머스 API 클라이언트
db.py                   # DB 함수 re-export 레이어
db_core.py              # DB 경로/연결
db_auth.py              # 인증/세션
db_products.py          # 제품 DB
db_orders.py            # 주문/발송 이력
db_stats.py             # 통계/영수증
db_ranks.py             # 순위 추적
auto_task.py            # 자동화 태스크 (cron)
pages_lib/
  profit_calc_page.py   # 수익계산 탭
  order_upload_page.py  # 주문 업로드 탭
  settings_page.py      # 설정 탭
  receipt_page.py       # 영수증 등록 탭
  product_db_page.py    # 제품 DB 탭
  rank_check_page.py    # 순위 체크 탭
  automation_page.py    # 자동화 탭
  tracking_page.py      # 송장번호 탭
  admin_page.py         # 관리자 탭
  guide_page.py         # 설정 가이드 탭
deploy/
  4_update.sh           # 수동 업데이트 스크립트
  costco-app.service    # systemd 서비스 정의
.github/workflows/deploy.yml  # GitHub Actions 자동배포
```

---

## 핵심 비즈니스 로직

### 수익 계산 공식
```
수입 = (정산예정금액 + 실정산배송비) - (구입가격 + 택배원가 + 박스원가)
실정산배송비 = 배송비합계 × (1 - naver_ship_fee_commission_rate%)
구입가격 = (unit_price ÷ split_qty) × 수량 × sell_factor
sell_factor = "x N개" 패턴 (상품명에서 추출, 1~50)
split_qty = 소분 단위 (코스트코 묶음을 N개로 나눠 판매)
```

### 상품 매칭 우선순위 (profit_calc_page.py)
1. 수동 키워드 오버라이드 (`kw_overrides`)
2. 네이버 상품번호(`naver_origin_pno`) 매칭
3. 코스트코 상품번호(`product_no`) 매칭
4. 키워드 토큰 매칭
5. saved_cost (DB 저장값) fallback

### 소분 판매 (소분÷N 배지)
- `split_qty > 1`이면 보라색 `[소분÷N]` 배지 표시
- `naver_origin_pno` 기준으로 소분 상품 매칭 (코스트코 번호보다 정확)
- DB 저장 시 `naver_origin_pno` 자동 링크

---

## 수익 계산 탭 버튼 역할

| 버튼 | 역할 |
|------|------|
| **📊 정산저장** (체크박스 선택 후) | 현재 정산 데이터를 `daily_orders` DB에 저장. 제품가격DB 미변경. |
| **💾 제품가격 DB 저장** (하단) | 수동 수정된 단가를 `products` DB에 반영. |
| **💾 정산 데이터 저장** (최하단) | `daily_orders` + 수정된 `products` 동시 저장. |

### 정산저장 후 복원 메커니즘
- `save_daily_orders`: 행별 `택배원가`/`박스원가` DB 저장 (전역값 아님)
- 페이지 재진입 시: `daily_orders` → `session_state` 자동 복원
  - `ship_{sk}` / `box_{sk}`: 행별 발송비/박스비
  - `cost_overrides[key]`: 저장된 구입가격

---

## 데이터 로딩 우선순위 (profit_calc_page.py)
1. `dispatch_log` (발송 처리된 주문) — 최우선
2. `daily_orders` (저장된 주문 데이터)
3. `order_history` (주문 이력)

---

## session_state 키 규칙
| 접두사 | 용도 |
|--------|------|
| `sel_p_{sk}` | 행 선택 체크박스 |
| `c_{sk}` | 행별 단가 입력 |
| `k_{sk}` | 행별 키워드 오버라이드 |
| `ship_{sk}` | 행별 택배원가 |
| `box_{sk}` | 행별 박스원가 |
| `_buf_c_{sk}` / `_buf_k_{sk}` | 위젯 상태 버퍼 (rerun 전 적용) |
| `_do_restored_{date}` | 일별 복원 완료 플래그 |
| `_pcalc_match_cache` | 매칭 결과 캐시 |

---

## 주요 커밋 히스토리 (2026-05-26)

| 커밋 | 내용 |
|------|------|
| `58a963f` | 수익계산: 기본 택배비/박스비 정산표에서 직접 수정 가능 |
| `7ca0023` | 수익계산: 행별 택배원가/박스원가 number_input 위젯 추가 |
| `e1b74a2` | 수익계산: 정산저장(수익계산용) / 제품가격 DB 저장 버튼 역할 분리 |
| `aa6c063` | 정산저장: 행별 발송비·박스비·구입가 DB 저장 + 페이지 재진입 시 자동 복원 |

---

## 주의사항

### 코드 수정 시
- **수정 범위 격리**: 요청된 부분만 수정, 주변 코드 건드리지 않음
- **덮어쓰기 금지**: Edit 도구로 최소 범위만 수정
- `import re`는 루프 밖에서 `import re as _re`로 1회만

### 배포 시
- GitHub Actions가 실패하면 `githubstatus.com` 확인
- Actions 장애 시 SSH 직접 배포 사용
- `ssh_key`: `C:/Users/blocklabs02/.ssh/costco_key`

### DB 스키마
- `daily_orders`: `delivery_cost`(행별 택배원가), `box_cost`(행별 박스원가) 컬럼 있음
- `products`: `naver_origin_pno` 컬럼 — 소분 매칭용 네이버 상품번호
- `dispatch_log`: 발송 처리 기록, `order_no` 기준

---

## 세션 로그 (2026-05-26) — 세션 10

### 완료된 작업

**1. 소분판매 매칭 및 표시 개선**
- `services.py`: `_index_products`에 `by_naver_pno` 인덱스 추가
- `match_product_to_db`: `naver_origin_pno` 1순위 매칭
- `profit_calc_page.py`, `order_upload_page.py`: `[소분÷N]` 보라색 배지 표시

**2. 수익 계산 버그 3개 수정**
- `split_qty` 우선순위 오류: `max(_sq_user, _sq_shared)` → `_sq = _sq_user if up else _sq_shared`
- `sell_factor` 누락 (`compute_costs_for_df`): `calc_cost(p, qty * _sell_factor)` 적용
- `sell_factor` 누락 (정산 데이터 저장): `_denom_s2 = max(1, _qty * _sell_factor_s2)` 적용

**3. 행별 발송비/박스비 편집 기능**
- 수익계산 정산표 각 행에 `발송비✏️` / `박스비✏️` number_input 위젯 추가
- 기본값 = 전역 설정값, 행별 개별 변경 가능 (기본 설정 미변경)
- 컬럼 레이아웃: `[0.3, 7.5, 1.3, 1.0, 1.0, 0.6]`
- session_state 키: `ship_{sk}`, `box_{sk}`

**4. 버튼 역할 명확화**
- "선택 저장" → "📊 정산저장" (제품DB 미변경, daily_orders에만 저장)
- "수정사항 반영" → "💾 제품가격 DB 저장" (단가를 products DB에 반영)

**5. 정산저장 영속성 수정**
- `db_orders.py save_daily_orders`: 행별 `택배원가`/`박스원가` 저장
- `profit_calc_page.py`: 페이지 재진입 시 `daily_orders` → `session_state` 자동 복원
  - `_do_restored_{date}` 플래그로 세션당 1회만 실행

**6. GitHub Actions 장애 대응**
- Actions 장애(`degraded_performance`) 감지 → SSH 직접 배포로 전환
- `C:/Users/blocklabs02/.ssh/costco_key` 로컬 키 확인 완료
