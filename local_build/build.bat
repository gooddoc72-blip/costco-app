@echo off
REM ── 코코비즈 로컬 설치판 빌드 (포터블 Python + 의존성 + 코드) ──────
REM 결과물: local_build\_build\  (python\, app\, run_costco.bat)
REM → 이후 installer.iss 로 Inno Setup 패키징
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYVER=3.11.9"
set "BUILD=%~dp0_build"
set "PYDIR=%BUILD%\python"
set "APPDIR=%BUILD%\app"

echo ============================================
echo  코코비즈 로컬판 빌드 (Python %PYVER% 포터블)
echo ============================================
if exist "%BUILD%" rmdir /s /q "%BUILD%"
mkdir "%PYDIR%" "%APPDIR%"

echo [1/5] Python 임베디드 다운로드...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip' -OutFile '%BUILD%\py.zip'" || goto :err
powershell -NoProfile -Command "Expand-Archive -Path '%BUILD%\py.zip' -DestinationPath '%PYDIR%' -Force" || goto :err
del "%BUILD%\py.zip"

echo [2/5] pip 활성화...
for %%F in ("%PYDIR%\python*._pth") do (
  powershell -NoProfile -Command "(Get-Content '%%F') -replace '#\s*import site','import site' | Set-Content '%%F'"
)
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%PYDIR%\get-pip.py'" || goto :err
"%PYDIR%\python.exe" "%PYDIR%\get-pip.py" --no-warn-script-location || goto :err

echo [3/5] 의존성 설치 (requirements.txt)...
"%PYDIR%\python.exe" -m pip install --no-warn-script-location -r "%~dp0..\requirements.txt" || goto :err

echo [4/5] 앱 코드 복사...
robocopy "%~dp0.." "%APPDIR%" /E /XD .git .venv local_build _build __pycache__ data deploy /XF *.pyc .env >nul

echo [5/5] 런처 복사...
copy /y "%~dp0run_costco.bat" "%BUILD%\run_costco.bat" >nul

echo.
echo ============================================
echo  빌드 완료: %BUILD%
echo  다음: Inno Setup으로 installer.iss 컴파일
echo ============================================
pause
exit /b 0

:err
echo.
echo [오류] 빌드 실패 — 위 메시지를 확인하세요.
pause
exit /b 1
