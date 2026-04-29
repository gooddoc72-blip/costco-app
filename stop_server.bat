@echo off
chcp 949 >nul
title 코스트코핫딜 - 서버 종료

echo.
echo  Streamlit 서버 종료 중...

taskkill /f /im streamlit.exe >nul 2>&1

for /f "tokens=2" %%a in ('tasklist /fi "IMAGENAME eq python.exe" /fo list 2^>nul ^| findstr /i "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "streamlit" >nul
    if not errorlevel 1 (
        taskkill /f /pid %%a >nul 2>&1
        echo  PID %%a 종료됨
    )
)

echo.
echo  서버가 종료되었습니다.
echo  다시 시작하려면 start_server.bat 을 실행하세요.
echo.
timeout /t 3 >nul