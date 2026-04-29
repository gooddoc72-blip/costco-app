@echo off
chcp 65001 >nul
title 코스트코핫딜 - 서버 부팅시 자동시작 설정

:: 관리자 권한 확인
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [오류] 관리자 권한이 필요합니다!
    echo  이 파일을 우클릭 후 "관리자 권한으로 실행"을 선택하세요.
    pause
    exit /b 1
)

set "APPDIR=%~dp0"
set "APPDIR=%APPDIR:~0,-1%"

echo.
echo  ================================================
echo   서버 자동시작 + 방화벽 설정
echo  ================================================
echo.

:: ── 1. 작업 스케줄러: 로그인 시 자동 서버 시작 ──
echo  [1/3] 부팅 자동시작 스케줄러 등록 중...

schtasks /create ^
    /tn "CostcoHotdeal_Server" ^
    /tr "cmd /c \"start \"CostcoServer\" /min \"%APPDIR%\start_server.bat\"\"" ^
    /sc onlogon ^
    /delay 0000:30 ^
    /f

if errorlevel 1 (
    echo   [실패] 스케줄러 등록 실패
) else (
    echo   [성공] 로그인시 자동 서버 시작 등록 완료 (30초 지연)
)

:: ── 2. Windows 방화벽: 포트 8501 인바운드 허용 ──
echo.
echo  [2/3] 방화벽 포트 8501 개방 중...

netsh advfirewall firewall delete rule name="CostcoHotdeal_8501" >nul 2>&1

netsh advfirewall firewall add rule ^
    name="CostcoHotdeal_8501" ^
    dir=in ^
    action=allow ^
    protocol=TCP ^
    localport=8501 ^
    description="Costco Hotdeal Streamlit Server"

if errorlevel 1 (
    echo   [실패] 방화벽 규칙 추가 실패
) else (
    echo   [성공] 포트 8501 인바운드 허용 완료
)

:: ── 3. 현재 IP 정보 출력 ──
echo.
echo  [3/3] 현재 서버 접속 주소:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    set ip=%%a
    setlocal enabledelayedexpansion
    set ip=!ip: =!
    if not "!ip!"=="" echo     http://!ip!:8501
    endlocal
)
echo     http://localhost:8501  (이 컴퓨터에서)
echo.
echo  ================================================
echo   설정 완료!
echo  ================================================
echo.
echo  ■ 다음 로그인부터 서버가 자동으로 시작됩니다.
echo  ■ 지금 바로 시작: start_server.bat 실행
echo  ■ 서버 중지: stop_server.bat 실행
echo.
echo  ■ 외부(인터넷) 접속을 위해:
echo    1. 공유기 포트포워딩 설정 (외부포트 8501 → 내부 위 IP:8501)
echo    2. DDNS 설정 (권장: https://www.duckdns.org)
echo       - 도메인 등록 후 yourname.duckdns.org:8501 로 접속 가능
echo.
pause
