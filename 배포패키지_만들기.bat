@echo off
chcp 65001 >nul
title 코스트코핫딜 - 배포 패키지 만들기

set "APPDIR=%~dp0"
set "APPDIR=%APPDIR:~0,-1%"
set "TODAY=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%"
set "ZIPNAME=코스트코핫딜_설치파일_%TODAY%.zip"
set "OUTZIP=%APPDIR%\%ZIPNAME%"

echo.
echo  ================================================
echo   배포 패키지 만들기
echo  ================================================
echo.
echo  포함 파일:
echo    app.py / requirements.txt
echo    install.bat / start_server.bat
echo    stop_server.bat / setup_server_boot.bat
echo    naver_api.py (있을 경우)
echo.
echo  제외 파일: data 폴더 (개인정보), __pycache__
echo.

:: 기존 zip 삭제
if exist "%OUTZIP%" del "%OUTZIP%"

:: PowerShell로 zip 생성
powershell -Command ^
  "$src = '%APPDIR%';" ^
  "$zip = '%OUTZIP%';" ^
  "$include = @('app.py','requirements.txt','install.bat','install.ps1','start_server.bat','start_server.ps1','stop_server.bat','setup_server_boot.bat','naver_api.py','auto_task.py','costco_crawler.py');" ^
  "$files = $include | ForEach-Object { Join-Path $src $_ } | Where-Object { Test-Path $_ };" ^
  "Add-Type -Assembly System.IO.Compression.FileSystem;" ^
  "$mode = [System.IO.Compression.CompressionLevel]::Optimal;" ^
  "$zip_obj = [System.IO.Compression.ZipFile]::Open($zip, 'Create');" ^
  "foreach ($f in $files) { $entry = [System.IO.Path]::GetFileName($f); [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip_obj, $f, $entry, $mode) | Out-Null };" ^
  "$zip_obj.Dispose();" ^
  "Write-Host '완료'"

if exist "%OUTZIP%" (
    echo.
    echo  ================================================
    echo   패키지 생성 완료!
    echo  ================================================
    echo.
    echo   파일명: %ZIPNAME%
    echo   위치:   %APPDIR%
    echo.
    echo   배포 방법:
    echo   1. 위 ZIP 파일을 상대방에게 전달
    echo   2. 상대방이 ZIP 압축 해제
    echo   3. install.bat 실행
    echo   4. start_server.bat 으로 앱 시작
    echo   5. http://localhost:8501 접속
    echo   6. 회원가입 탭에서 계정 생성 신청
    echo   7. 관리자(admin)가 승인 후 사용 가능
    echo.
    :: 파일 탐색기로 바로 열기
    explorer /select,"%OUTZIP%"
) else (
    echo  [오류] ZIP 생성 실패. PowerShell 오류를 확인하세요.
)

echo.
pause
