@echo off
REM ── 코코비즈 로컬 설치판 실행 런처 ───────────────────────────────
REM 설치 폴더 구조: <설치폴더>\run_costco.bat, \python\(포터블파이썬), \app\(코드)
chcp 65001 >nul
cd /d "%~dp0"

REM 1-PC 인증 활성화 + 인증서버 지정
set "COSTCO_LOCAL=1"
set "COSTCO_LICENSE_SERVER=https://cocobiz.shop"

REM 포터블 파이썬으로 Streamlit 실행 (브라우저 자동 열림)
"%~dp0python\python.exe" -m streamlit run "%~dp0app\app.py" ^
    --server.port=8501 ^
    --server.headless=false ^
    --browser.gatherUsageStats=false ^
    --global.developmentMode=false

REM 종료 시 창 유지(오류 확인용)
echo.
echo [프로그램이 종료되었습니다. 창을 닫아도 됩니다.]
pause >nul
