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
cafe24_api.py           # 카페24 Admin API 클라이언트
cafe24_register_service.py  # 카페24→네이버 대행등록 1건 로직 (UI·크론 공용)
db_cafe24_queue.py      # 카페24 대행등록 배치 대기열 (auth.db)
db_naver_reg.py         # 네이버 등록 사용자별 기록·한도 (auth.db/naver_register_log)
                        #   기록 지점은 naver_api.register_product 한 곳 — 토큰으로
                        #   주인을 되짚어 수동·무인·대행 전 경로를 자동 집계
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
  receipt_settle_page.py  # 영수증 정산 (관리자) — 매칭·정산 요청·잔량 재고 입고
  settle_billing_page.py  # 정산·청구 (관리자) — 정산리스트·청구·입금완료·미입금자
  my_purchase_page.py     # 내 구매내역 정산 (사용자) — 일별/월별 청구·입금 상태
  purchase_settle_page.py # 구매가·매핑 관리 (관리자) — 청구가 아니라 매핑 도구
  billing_page.py         # 포장 관리 (관리자) — 포장 단가·배정·사용자 택배/포장비
db_settle.py            # 정산 원장 (청구액의 유일한 정본)
db_deposit.py           # 예치금 원장 (잔액의 유일한 정본 — 합계로만 계산)
settle_core.py          # 정산 파이프라인 (매칭 결과 → 원장 → 청구서 → 재고)
migrate_to_settle_ledger.py  # 옛 저장소 → 새 원장 이관 (--dry-run / --verify)
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
| **📊 정산저장** (체크박스 선택 후) | `profit_settlements` + `settlement_overrides` 저장. 제품가격DB 미변경. |
| **💾 제품가격 DB 저장** (하단) | 수동 수정된 단가를 `products` DB에 반영 + `profit_settlements` 동시 저장. |
| **💾 정산 데이터 저장** (최하단) | `daily_orders` + `products` + `profit_settlements` 동시 저장. |

### 정산저장 후 복원 메커니즘
- 복원 순서: `profit_settlements` 1순위 → `daily_orders` fallback (하위호환)
- `settlement_overrides` 영구 오버라이드 자동 적용 (저장값 없는 행에만)
- 복원 데이터: `ship_{sk}`, `box_{sk}`, `cost_overrides[key]`, `kw_overrides[sk]`
- `_do_restored_{date}` 플래그로 세션당 1회 실행

---

## 수익 계산 DB 구조 (db_profit_calc.py)

### profit_settlements — 수익계산 결과 (날짜별)
| 컬럼 | 설명 |
|------|------|
| settlement_date | 정산 날짜 (YYYY-MM-DD) |
| order_no | 상품주문번호 |
| recipient, product_name | 복합 UNIQUE 키 |
| cost_price, delivery_cost, box_cost | 행별 비용 |
| matched_keyword, match_source | 매칭 정보 |
| split_qty, sell_factor | 소분/묶음 정보 |

### settlement_overrides — 정산매칭 오버라이드 (영구)
| 컬럼 | 설명 |
|------|------|
| recipient, product_name | 영구 UNIQUE 키 |
| override_keyword | 수동 키워드 매핑 |
| override_cost | 수동 단가 |

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
- `profit_settlements`: `UNIQUE(settlement_date, recipient, product_name)` — 수익계산 결과
- `settlement_overrides`: `UNIQUE(recipient, product_name)` — 영구 오버라이드
- `daily_orders`: `delivery_cost`(행별 택배원가), `box_cost`(행별 박스원가) — 하위호환용
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

---

## 정산·청구 구조 (2026-09-09 전면 재작성)

### 왜 바꿨나
"A가 9/8에 얼마 내야 하나"에 답하는 저장소가 **넷**이었다.
`receipt_settle_items` · `purchase_settle_snapshot` · `billing_ledger` · `daily_billing`.
넷이 서로 다른 시점에 서로 다른 방법으로 채워져 화면마다 금액이 달랐고
(9/7 oxo: 원장 780,720 vs 스냅샷 184,560), 한쪽을 고치면 다른 쪽이 틀어졌다.

### 이제 둘뿐 (auth.db)
| 테이블 | 뜻 |
|--------|-----|
| `settle_item` | 정산 품목 한 줄 = **청구 근거** 한 줄 (무엇을 얼마에 넘겼나) |
| `settle_invoice` | 날짜×사용자 한 줄 = **청구서** (합계 + 청구·입금 상태) |
| `settle_draft` | 매칭 초안 (저장 ≠ 정산) |

`settle_invoice` 금액은 **언제나** `recompute_invoice()`가 `settle_item` 합계에서 만든다.
사람이 손으로 고치는 금액 필드가 없어야 둘이 어긋날 수 없다.
단, `status='paid'`인 청구서는 금액을 건드리지 않는다 — 받은 돈과 청구액이
달라지면 무엇을 받은 것인지 설명할 수 없다.

### 상태
`draft`(정산완료·청구 전) → `billed`(청구됨) → `paid`(입금완료)
미입금자 = `billed` 상태만. 청구하지 않은 돈을 안 냈다고 할 수는 없다.

### 업무 흐름 ↔ 화면
| 단계 | 화면 |
|------|------|
| ① 당일 주문 수집 → 매장 구매 | 일일 주문 수집 · 장보기 목록 |
| ② 관리자 택배 발송 | 송장번호 (발송처리) |
| ③ 각 사용자 송장 등록 | 송장번호 |
| ④ 익일 영수증 등록 → 매칭 → **정산 요청** | 관리자 › 영수증 정산 |
| ⑤ 정산리스트 | 관리자 › **정산·청구** |
| ⑥ 사용자 일별 확인 | 내 구매내역 정산 |
| ⑦ 청구 → 입금완료 체크 | 관리자 › **정산·청구** |
| ⑧ 미입금자 리스트 | 관리자 › **정산·청구** |

### 청구액 공식 (일별)
```
청구액 = 물건값(settle_item 합계)          ← 이게 전부다
```
**택배비·포장비는 청구서에 싣지 않는다 — 별도로 청구한다.** 물건값과 한
청구서에 섞으면 사용자가 "이 금액이 왜 이런가"를 물건 내역만으로 확인할 수 없고,
단가를 고쳐 다시 정산할 때마다 비용까지 함께 흔들린다.

`settle_invoice.ship_fee`·`pack_fee` 칸은 남아 있지만 새 정산은 0으로 둔다
(`settle_core.finalize(with_fees=False)`가 기본). 화면에서는 참고 수치로만
보여 준다 — 그날 택배비 = 발송건수 × 사용자 shipping_cost,
포장비 = 배정된 주문은 order_packaging, 없으면 사용자 box_cost.

### 직접구매 계정 (self_purchase)
매칭 대상과 청구 대상은 다른 판단이다.
- `matchable_users()` — 관리자만 뺀다. **직접구매 계정도 영수증 매칭·재고 정리를
  한다.** 주문 구입가(수익계산)와 재고 차감의 근거는 청구와 무관하게 필요하다.
- `billable_users()` — 청구 대상. 직접 사는 사람은 자기 돈으로 자기가 샀으니
  청구할 것이 없다.

청구서는 `recompute_invoice`·`set_fees` 두 곳에서만 만들어지므로 거기서 막는다.
품목(`settle_item`)은 그대로 남겨 재고 사용량 집계와 원가 반영에 쓴다.
관리자 › 회원 관리에서 켜고 끈다.

### 정산 저장 규칙
- `save_settlement()` → `merge_items()`: **이번 회차에 매칭된 주문만** 갱신.
  영수증을 나눠 올리는 날, 통째로 교체하면 오전 정산분이 사라진다.
- 같은 주문이 다시 오면 덮어쓴다 — 잘못 붙은 매칭은 다시 돌려 고친다.
- 행을 없애려면 **정산 취소**(`delete_settlement`) — 입금완료분은 남긴다.
- 금액 0원 행은 원장에 안 넣는다. 0원 청구는 그대로 손실이다.

### 미매칭 잔량 → 사용자 재고
구입내역 중 어느 주문에도 안 붙은 잔량은 `settle_core.leftovers()`가 뽑고,
보유자를 **그날 장보기 목록 요청자**로 채워 준다. 관리자가 확인·체크한 것만
`inventory_lots`로 입고된다(자동 입고 안 함 — 유령 재고가 남의 판매에서
차감되며 교차정산 웃돈까지 발생시키는 되돌리기 어려운 사고).

---

## 예치금 (선불) — db_deposit.py

### 뜻
사용자가 돈을 미리 맡기고, 그날 구매금액을 거기서 뺀다. 후불 청구(입금 대기)를
없애려는 것이지 대체하는 것은 아니다 — 예치금을 안 쓰는 사용자는 종전대로
청구 → 계좌 입금으로 간다.

### 테이블 하나뿐 (auth.db) — `deposit_ledger`
| kind | 뜻 | 부호 |
|------|-----|------|
| `charge` | 예치(입금) | + |
| `spend` | 그날 구매분 차감 | − |
| `spend_void` | 되돌려진 차감 (금액은 남는다) | − |
| `refund` | 차감 되돌림 (spend_void와 짝) | + |
| `adjust` | 관리자 수동 조정 (사유 필수) | ± |

**잔액 = 원장 전체 합계.** 저장하는 잔액 컬럼이 없어야 잔액과 내역이 어긋날 수
없다(정산 원장과 같은 이유). 되돌림도 행을 지우지 않고 `spend_void` + `refund`
두 줄로 상쇄한다 — "차감했다가 되돌렸다"가 사용자 내역에 보여야 한다.

하루 한 번만 차감된다: `UNIQUE(username, settle_date) WHERE kind='spend'`
부분 인덱스. 버튼을 두 번 눌러도 두 번 안 빠지고, 되돌린 뒤엔 다시 차감된다.

### 차감은 자동이 아니다
영수증 정산은 하루에 여러 회차로 나눠 돌리고 회차마다 금액이 바뀐다. 바뀔
때마다 자동으로 빠지면 사용자 잔액이 조용히 오르내린다. 금액이 확정되는
순간(관리자가 청구를 결정하는 순간)에 **버튼으로 한 번만** 뺀다.

### 청구서와의 관계
예치금 차감은 **입금의 한 방법**이지 별도 상태가 아니다. 차감할 때
`mark_billed` → `deduct` → `mark_paid(memo='예치금 차감')` 순서로 불러
그날 청구서를 `paid`로 만든다. 그래야 미입금자·월별 정리가 그대로 맞는다.
되돌릴 때는 `unmark_paid` + `undo_deduct`를 **반드시 같이** 부른다 — 한쪽만
되돌리면 사용자는 쓰지도 않은 돈이 빠진 채로 남는다.

### 잔액이 모자라도 막지 않는다
구매는 이미 일어난 일이라 원장이 그것을 부정하면 그날 청구서가 어디에도
안 남는다. 차감은 되고 잔액이 마이너스가 되며, 관리자·사용자 화면 양쪽이
"추가 예치 필요"로 크게 알린다. 차감 화면은 부족한 사용자를 기본 미선택으로
두고 경고만 띄운다.

### 화면
| 화면 | 하는 일 |
|------|---------|
| 관리자 › 정산·청구 › **일별** | 정산리스트에 예치금 잔액·결제수단 표시, 💳 예치금 차감, 입금완료 취소(예치금 동시 반환) |
| 관리자 › 정산·청구 › **💳 예치금** | 잔액 현황 · 예치 등록 · 수동 조정 · 원장 조회·삭제 |
| 사용자 › 내 구매내역 정산 | 상단 잔액 배너, 일별에 '💳 예치금에서 차감됨', **💳 예치금** 탭(잔액·예치·차감 내역) |

---

### 이관
```bash
python migrate_to_settle_ledger.py --dry-run   # 미리보기
python migrate_to_settle_ledger.py             # 이관
python migrate_to_settle_ledger.py --verify    # 옛 값과 대조
```
옛 테이블은 **지우지 않는다**. 옮긴 값이 이상하면 대조할 원본이 있어야 한다.
