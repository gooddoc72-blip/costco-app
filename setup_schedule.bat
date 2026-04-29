@echo off
chcp 65001 >nul
title Auto Task Scheduler Setup
echo.
echo  ================================================
echo   12:30 Daily Auto Task - SETUP
echo  ================================================
echo.

:: Get current folder path safely
set "APPDIR=%~dp0"
set "APPDIR=%APPDIR:~0,-1%"

if not exist "%APPDIR%\auto_task.py" (
    echo  [ERROR] auto_task.py not found in %APPDIR%
    echo  Please check if the file is in the same folder.
    pause
    exit /b 1
)

echo  Creating Windows scheduled task...
echo  Task: Daily 12:00 PM - Order fetch + Shopping list + Kakao
echo.

schtasks /create /tn "CostcoHotdeal_1200" /tr "python \"%APPDIR%\auto_task.py\"" /sc daily /st 12:00 /f

if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to create task.
    echo  Try running this script as Administrator.
) else (
    echo.
    echo  ================================================
    echo   SETUP COMPLETE!
    echo  ================================================
    echo.
    echo   Task name: CostcoHotdeal_1200
    echo   Schedule: Every day at 12:00 PM
    echo   Working Folder: %APPDIR%
    echo.
    echo   To test now: python "%APPDIR%\auto_task.py"
    echo.
)
pause