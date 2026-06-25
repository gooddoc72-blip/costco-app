# 코코비즈 로컬 설치판 빌드 가이드

웹(cocobiz.shop)과 별도로, **각 PC에 설치해 본인 API키로 단독 실행**하는 설치판을 만듭니다.
**1-PC 사용 인증**(1키=1PC, 서버 검증)이 적용됩니다.

## 구성
- `run_costco.bat` — 실행 런처 (COSTCO_LOCAL=1 설정 + Streamlit 실행 + 브라우저 자동 열기)
- `build.bat` — 포터블 Python(3.11) + 의존성 + 앱코드 묶기 → `_build\` 생성
- `installer.iss` — Inno Setup 설치 프로그램 정의

## 빌드 순서 (빌드 PC에서)
1. **build.bat 더블클릭** → `local_build\_build\` 에 `python\`, `app\`, `run_costco.bat` 생성
   - 인터넷 필요 (Python 임베디드 + pip 패키지 다운로드)
2. **Inno Setup 설치** (https://jrsoftware.org/isdl.php)
3. `installer.iss` 를 Inno Setup으로 **컴파일** → `Output\CocoBiz_Setup_1.0.0.exe` 생성
4. 이 setup.exe 를 사용자에게 배포

## 설치/실행 (사용자 PC)
1. `CocoBiz_Setup.exe` 설치 → 바탕화면 "코코비즈" 아이콘
2. 실행 → 브라우저에 앱 열림
3. **최초 1회 라이선스키 입력(활성화)** — 관리자에게 받은 `COCO-XXXX-XXXX-XXXX`
   - 이 PC에 바인딩됨(1키=1PC). 다른 PC에선 같은 키 사용 불가.
4. 설정 탭에서 **본인 네이버/쿠팡 API 키 입력** 후 사용

## 라이선스 발급 (관리자)
- 웹(cocobiz.shop) → 관리자 → **🔑 로컬 설치형 라이선스** → 발급
- PC 교체 시: 해당 키 **🔄 PC 해제** → 사용자가 새 PC에서 재활성화

## 참고/주의
- **첫 빌드 시 의존성 설치에 시간이 걸립니다** (pandas/plotly 등). 일부 패키지가 임베디드 Python에서 실패하면, 일반 Python venv로 빌드하는 방식으로 전환 가능(문의).
- `playwright`(순위 크롤링)는 브라우저 별도 설치 필요 시 `python\python.exe -m playwright install chromium` 1회 실행.
- 아이콘: `app\favicon.ico` 가 있으면 사용, 없으면 기본 아이콘.
- 인증 서버: 기본 `https://cocobiz.shop`. 변경 시 `run_costco.bat`의 `COSTCO_LICENSE_SERVER` 수정.
