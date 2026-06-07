# 코스트코 핫딜 자동화 — 개발 로그

## 프로젝트 개요
코스트코 코리아 상품을 크롤링하여 네이버 스마트스토어에 자동 등록하는 Streamlit 멀티유저 앱.

- **앱 실행**: `start_server.bat` → `http://localhost:8501`
- **주요 파일**: `app.py`, `costco_crawler.py`, `naver_api.py`, `auto_task.py`
- **DB**: `data/auth.db` (SQLite — 사용자, 제품, 주문 통합)
- **GitHub**: https://github.com/gooddoc72-blip/costco-order

---

## 아키텍처

### 기술 스택
| 항목 | 내용 |
|------|------|
| 프레임워크 | Streamlit (멀티유저, `st.radio` 탭 네비게이션) |
| 크롤러 | Playwright (headless Chromium, 영구 브라우저 프로필) |
| DB | SQLite (`data/auth.db`) |
| 네이버 API | `naver_api.py` |

### 탭 구조 (`app.py`)
```
메인 메뉴 (st.radio, key="main_tab")
├── 🏠 홈
├── 📋 주문 업로드
├── 📮 송장번호 등록
├── 🧾 영수증 등록
├── 💰 수익 계산
├── 📊 대시보드
├── 📦 제품 DB
├── ⚙️ 설정
├── 🤖 자동화
└── 👑 관리자 (admin 전용)
```

### DB 주요 테이블
- `users` — 회원 (role: admin/user/pending)
- `shared_products` — 공유 제품 DB (크롤링/영수증 수집)
- `user_products` (= `data/{username}.db` products) — 사용자별 판매가/배송비/네이버상품번호
- `orders` — 주문 내역

---

## 크롤러 (`costco_crawler.py`)

### 핵심 구조
- **실행 방식**: Streamlit 이벤트 루프 충돌 방지를 위해 **subprocess 격리** 실행
  ```
  crawl_category() → subprocess: python costco_crawler.py --do-crawl params.json output.json
                                → _crawl_direct() [Playwright 직접 실행]
  ```
- **인증**: 영구 브라우저 프로필 (`data/costco_browser_profile/`) 재사용

### OCC REST API 방식 (핵심)
코스트코 코리아는 SAP Spartacus (Angular SPA) 기반.
DOM 파싱 불가 → **OCC REST API 직접 fetch()**로 전환.

```
실제 API URL:
https://www.costco.co.kr/rest/v2/korea/products/search
  ?fields=FULL&query=&category={코드}&pageSize=100&currentPage=0&lang=ko&curr=KRW

검색:
  ?fields=FULL&query={키워드}&pageSize=100&lang=ko&curr=KRW
```

브라우저 세션 쿠키로 인증 → `page.evaluate(fetch(apiUrl, {credentials: "include"}))`

### 카테고리 코드 (`cos_*` 형식)
```python
CATEGORIES = {
    "식품":       "/c/cos_10",
    "신선식품":   "/c/cos_10.10",
    "냉동식품":   "/c/cos_10.14",
    "과자/간식":  "/c/cos_10.5",
    "커피/음료":  "/c/cos_10.2",
    "가공식품":   "/c/cos_10.3",
    "생활용품":   "/c/cos_9",
    "세제/청소":  "/c/cos_2.7",
    "화장지":     "/c/cos_2.6",
    "가전/디지털":"/c/cos_1",
    "주방가전":   "/c/cos_1.9",
    "뷰티/화장품":"/c/cos_8",
    "건강/영양제":"/c/cos_12",
    "의류/패션":  "/c/cos_6",
    "스포츠/레저":"/c/cos_4",
    "캠핑":       "/c/cos_4.2",
    "완구":       "/c/cos_3.5",
    "반려동물":   "/c/cos_10.9",
    "자동차용품": "/c/cos_9.7",
    "가구/침구":  "/c/cos_2",
    "보석/시계":  "/c/cos_7",
    "커클랜드":   "/c/KirklandSignature",
    "신상품":     "/c/whatsnew",
    "스페셜할인": "/c/SpecialPriceOffers",
}
# ❌ KR_ALL_*, KR_ALL_BEAUTY 등은 모두 404 — 사용 불가
```

### DB 저장 (`save_to_shared_products`)
```python
save_to_shared_products(products, updated_by='crawler', category='신선식품')
```
- `updated_by='crawler'` 필수 — 이 값으로 price_type='온라인' 자동 설정
- `category` 파라미터로 카테고리명 저장 → 제품 DB 카테고리 필터에 사용

### 앱에서 크롤링 호출 시 주의
```python
# ✅ 반드시 updated_by='crawler' 사용
result = _cc.run_crawl(..., updated_by='crawler')
# ❌ updated_by=USERNAME 사용 시 price_type이 '매장'으로 표시됨
```

---

## UI 주요 사항 (`app.py`)

### 제품 DB 탭 컬럼 구성
```python
HDR = [0.9, 4.6, 1.05, 1.05, 0.6, 1.2, 1.1, 1.0, 0.6, 0.6, 0.55]
HDR_LABELS = ['상품번호','코스트코 상품명','매장가🔒','온라인가🔒',
              '소분🔒','판매가(네이버)✏️','고객배송비✏️','업데이트','수정','🛍등록','삭제']
# 매칭키(match_keyword)는 DB 내부 키로 존재하지만 UI에서는 숨김
```

### 제품 DB 카테고리 버튼
- 상품에 category 컬럼이 채워진 경우 자동으로 카테고리 버튼 생성
- 크롤링 시 run_crawl()이 category='카테고리명' 전달 → save_to_shared_products()에서 저장
- **기존 크롤링 데이터(category 비어있음)**: 다시 크롤링하면 채워짐

### 가격 표시 (price_type)
```python
pt_cur = p.get('price_type') or '매장'  # NULL 방지: or 사용
if pt_cur == '온라인':
    # 파란색 🌐 온라인가 컬럼 표시, 매장가는 -
else:
    # 초록색 매장가 컬럼 표시, 온라인가는 -
```
- 크롤링 수집 → `price_type='온라인'`, `updated_by='crawler'`
- 영수증 업로드 → `price_type='매장'`, `updated_by=USERNAME`

### 이미지 썸네일
```python
# 57×57px (이전 38×38에서 1.5배 확대)
f"<img src='{_thumb}' width='57' height='57' style='object-fit:cover;border-radius:6px;...'>"
```

### 네이버 상품 등록 (🛍 버튼)
- 제품 DB 각 행의 🛍 버튼 클릭 → 탭 상단에 등록 폼 표시
- 등록 완료 후 네이버 상품번호가 ✅로 표시
- `naver_api.upload_product_image()` → 이미지 업로드 → `naver_api.register_product()` → 상품 생성
- 설정 탭 "네이버 상품 등록 기본값"에서 기본 카테고리 ID / A/S 전화번호 저장

### 크롤링 탭 프리셋 버튼
```python
PRESETS = {
    "🏗️ 최초구축": [17개 주요 카테고리],
    "🔄 정기갱신": ["신선식품","냉동식품","과자/간식","커피/음료","가공식품"],
    "🔥 핫딜시즌": ["스페셜할인","커클랜드","신상품"],
    "🆕 새상품탐색": ["신상품","스페셜할인"],
}
```

### 탭 이동 (크롤링 완료 후)
```python
st.session_state['main_tab'] = "📦 제품 DB"
st.rerun()
```

---

## 네이버 API (`naver_api.py`)

### 주요 함수
| 함수 | 설명 |
|------|------|
| `get_token()` | OAuth 토큰 발급 |
| `get_new_orders()` | 주문 조회 (최근 48시간) |
| `ship_orders()` | 일괄 발송처리 |
| `upload_product_image()` | 이미지 → 네이버 CDN 업로드 |
| `register_product()` | 스마트스토어 상품 등록 |
| `update_product_price()` | 상품 가격 수정 |
| `send_telegram()` / `send_kakao()` | 알림 발송 |

### 상품 등록 필수 파라미터
```python
register_product(client_id, client_secret, {
    "name": "상품명",
    "sale_price": 29900,
    "image_url": "네이버CDN URL (upload_product_image 반환값)",
    "category_id": "50000803",  # 네이버 쇼핑 리프 카테고리 ID
    "stock": 100,
    "shipping_fee": 0,          # 0=무료
    "after_service_tel": "010-0000-0000",
    "origin_code": "03",        # 03=국내산, 04=해외산
})
```

### 네이버 카테고리 ID 확인 방법
스마트스토어 센터 → 상품관리 → 상품 등록 → 카테고리 선택 화면에서 확인

---

## 자동화 (`auto_task.py`)

### Task 구조
| Task | 설명 | 실행 |
|------|------|------|
| Task 1 (shopping) | 배송준비 주문 조회 → 장보기 목록 카카오/텔레그램 발송 | `--task shopping` |
| Task 2 (shipping) | CJ 접수 + 네이버 일괄 발송처리 | `--task shipping` |
| Task 3 (crawl) | 코스트코 정기 크롤링 → 공유 DB 업데이트 | `--task crawl` |

### Task 3 설정
- 자동화 탭 (admin 전용) → Task 3 섹션
- 실행 시간, 카테고리 선택, 최대 수집 수 설정
- Windows 작업 스케줄러 자동 등록/삭제
- 코스트코 계정: `data/auth.db` app_settings에서 읽음
- 크롤링 카테고리: `data/admin.db` settings.auto_crawl_categories (JSON)

---

## shared_products 테이블 컬럼 전체
```sql
CREATE TABLE shared_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_no TEXT DEFAULT '',         -- 코스트코 상품코드
    costco_name TEXT NOT NULL,           -- 코스트코 상품명
    match_keyword TEXT UNIQUE NOT NULL,  -- 내부 매칭 키 (UI에서 숨김)
    unit_price INTEGER NOT NULL,         -- 매입가
    split_qty INTEGER DEFAULT 1,         -- 소분 수량
    updated_by TEXT DEFAULT '',          -- 'crawler' or 유저명
    updated_at TEXT NOT NULL,
    price_type TEXT DEFAULT '매장',      -- '온라인' or '매장'
    image_url TEXT DEFAULT '',           -- 코스트코 CDN URL
    local_image TEXT DEFAULT '',         -- 로컬 저장 경로 data/images/
    naver_category_id TEXT DEFAULT '',   -- 네이버 카테고리 ID
    category TEXT DEFAULT ''             -- 코스트코 카테고리명 (크롤링 시 자동)
)
```

---

## 이미지 저장
- **CDN URL**: `image_url` 컬럼 → UI 썸네일 표시용
- **로컬**: `data/images/{product_no}.jpg` → 네이버 API 업로드용
- 크롤링 시 자동 다운로드 (`download_product_image()`)

---

## 웹 배포 계획
- **권장**: VPS (DigitalOcean/Vultr $6~12/월)
- **이유**: Playwright(헤드리스 Chrome) + SQLite 영속성 + 브라우저 프로필 필요
- **배포 방식**: `install.bat` → `setup_server_boot.bat` (Windows 서비스 등록)

---

## 미완성 / 다음 작업 목록

| 우선순위 | 작업 | 상태 |
|----------|------|------|
| 🔴 높음 | 기존 202개 상품 카테고리 배정 (재크롤링 또는 일괄 배정 기능) | 미완성 |
| 🟡 중간 | 웹 배포 (VPS 세팅) | 계획중 |
| 🟡 중간 | 네이버 카테고리 ID 검색 기능 (등록 폼에서 바로 검색) | 미구현 |
| 🟢 낮음 | 가격 변동 알림 | 미구현 |

---

## 알려진 버그 / 주의사항

1. **Playwright + Streamlit 이벤트 루프 충돌** (Windows)
   - 해결: subprocess 격리 실행 필수. `sync_playwright()`를 Streamlit 메인 스레드에서 직접 호출 금지.

2. **코스트코 세션 만료**
   - `data/costco_browser_profile/` 프로필로 세션 유지.
   - 만료 시 앱 설정에서 이메일/비번 입력 후 자동 재로그인.

3. **price_type NULL 처리**
   - `p.get('price_type', '매장')` 대신 `p.get('price_type') or '매장'` 사용.
   - 이유: DB NULL이면 dict.get()의 default 파라미터가 무시됨.

4. **get_all_products_merged() 누락 필드**
   - `price_type`, `image_url`, `local_image`, `category` 반드시 포함해야 함.
   - 누락 시 UI에서 항상 기본값('매장', 이미지 없음)으로 표시됨.

5. **updated_by='crawler' 필수**
   - 크롤링 호출 시 `updated_by=USERNAME` 사용 금지 → price_type이 '매장'으로 보임.
   - 반드시 `updated_by='crawler'` 고정.

6. **카테고리 버튼이 안 나타나는 경우**
   - shared_products.category 컬럼이 비어있으면 버튼 미표시.
   - 해결: 크롤링 재실행 (UPDATE 시 category 자동 저장).

---

## 커밋 히스토리 (주요)
| 커밋 | 내용 |
|------|------|
| `6bd4f85` | 제품DB: 매칭키제거+썸네일1.5배+카테고리버튼필터+category컬럼 |
| `0855395` | 핵심버그수정: get_all_products_merged에 price_type/image_url 누락 |
| `bfb6b7e` | price_type 버그수정: 크롤링=온라인, updated_by=crawler로 변경 |
| `46bc2d9` | 가격버그수정+네이버상품등록+정기크롤링스케줄러 |
| `efb8ba8` | 이미지 다운로드+썸네일 표시+가격표시 버그수정+카테고리 프리셋 버튼 |

---

## 2026-06-07 업데이트 — 카카오 발송 / 네이버 판매가 적용 수정

### 카카오톡 장보기 발송
- 200자 분할 → **전체 1건 발송**으로 변경 (카카오 memo/default text 실측 8000자↑ 단건 허용).
- 발송 형식 **2줄 카드형**(`• 제품명 × 총수량(건)` / `옵션·정산·택배`), 택배비 추가.
- 텔레그램 분기 임계값 2000→7000자 상향(카톡도 전체 목록 1건 수신).

### 네이버 판매가 적용 (수익 마이너스 → 네이버 판매가 검토 및 적용)
- **채널상품번호(channelProductNo)** 가 `naver_origin_pno`에 저장된 상품이 가격수정 API
  404 실패 → `products/search`로 **원상품번호(originProductNo) 자동 변환** 후 적용 + DB 영구저장.
- PATCH는 게이트웨이 미지원(`GW.NOT_FOUND`) 확인 → GET→PUT 경로로 정리.
- **배송비(택배비) 미반영 버그 수정**: `deliveryInfo.deliveryFee.baseFee` 갱신 추가
  (판매가만 바뀌고 배송비는 안 바뀌던 문제).

### 신규 커밋
| 커밋 | 내용 |
|------|------|
| `50c0fad` | 카카오 끊김 제거(전체 1건) + 장보기 2줄카드 형식(옵션·정산·택배) |
| `7d89aa4` | 네이버 판매가: 채널번호 자동변환 + 원번호 DB저장 |

---

*최종 업데이트: 2026-06-07*
