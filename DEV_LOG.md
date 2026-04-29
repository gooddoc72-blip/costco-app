# 코스트코 핫딜 자동화 — 개발 로그

## 프로젝트 개요
코스트코 코리아 상품을 크롤링하여 네이버 스마트스토어에 자동 등록하는 Streamlit 멀티유저 앱.

- **앱 실행**: `start_server.bat` → `http://localhost:8501`
- **주요 파일**: `app.py`, `costco_crawler.py`, `naver_api.py`, `auto_task.py`
- **DB**: `data/auth.db` (SQLite — 사용자, 제품, 주문 통합)

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
├── 📋 주문 관리
├── 📦 제품 DB
├── 🔍 네이버 검색
├── ⚙️ 설정
└── 🛡️ 관리자 (admin 전용)
```

### DB 주요 테이블
- `users` — 회원 (role: admin/user/pending)
- `shared_products` — 공유 제품 DB (크롤링/영수증 수집)
- `user_products` — 사용자별 판매가/배송비
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

### OCC 응답 파싱 (`_parse_occ_products`)
```python
# 가격: price.value (dict 형식)
price_obj = item.get("price", {})
price = int(price_obj.get("value", 0))

# 이미지: images[].url (상대 경로 → 절대 URL)
images = item.get("images", [])
# imageType="PRIMARY", format="product" 우선
image_url = COSTCO_BASE + img["url"]  # https://www.costco.co.kr/medias/...

# 상품코드: code
product_no = item.get("code")
```

---

## UI 주요 사항 (`app.py`)

### 제품 DB 탭 컬럼 구성
```python
HDR = [0.9, 2.8, 2.0, 1.05, 1.05, 0.6, 1.2, 1.1, 1.0, 0.75, 0.65]
HDR_LABELS = ['상품번호','코스트코 상품명','매칭키','매장가🔒','온라인가🔒',
              '소분🔒','판매가(네이버)✏️','고객배송비✏️','업데이트','수정','삭제']
```

### 가격 표시 (price_type)
```python
pt_cur = p.get('price_type') or '매장'  # NULL 방지: or 사용
if pt_cur == '온라인':
    # 파란색 🌐 온라인가 컬럼 표시, 매장가는 -
else:
    # 초록색 매장가 컬럼 표시, 온라인가는 -
```
- 크롤링 수집 → `price_type='온라인'`
- 영수증 업로드 → `price_type='매장'`

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

### 행 간격 CSS
```css
div[data-testid="stHorizontalBlock"] { margin-bottom: -0.4rem; }
```

---

## 이미지 저장 현황 및 계획
- **현재**: `image_url` 컬럼에 코스트코 CDN URL 저장만 됨
- **필요**: 네이버 상품등록용 로컬 이미지 다운로드
- **계획**: 크롤링 시 `data/images/{product_no}.jpg` 다운로드 → 네이버 API 업로드

---

## 웹 배포 계획
- **권장**: VPS (DigitalOcean/Vultr $6~12/월)
- **이유**: Playwright(헤드리스 Chrome) + SQLite 영속성 + 브라우저 프로필 필요
- **배포 방식**: `install.bat` → `setup_server_boot.bat` (Windows 서비스 등록)

---

## 미완성 / 다음 작업 목록

| 우선순위 | 작업 | 상태 |
|----------|------|------|
| 🔴 높음 | 이미지 로컬 다운로드 (`data/images/`) | 미구현 |
| 🔴 높음 | 네이버 자동 상품등록 연동 | 미구현 |
| 🟡 중간 | 웹 배포 (VPS 세팅) | 계획중 |
| 🟡 중간 | 정기 크롤링 스케줄러 (`auto_task.py`) | 부분구현 |
| 🟢 낮음 | 제품 DB 이미지 썸네일 표시 | 미구현 |
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

---

*최종 업데이트: 2026-04-29*
