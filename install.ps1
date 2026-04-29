$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  코스트코 핫딜 관리 프로그램 - 설치" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Python 확인
Write-Host "[1/4] Python 확인 중..." -ForegroundColor Yellow

$pythonCmd = $null
$candidates = @("python", "python3", "py")
foreach ($cmd in $candidates) {
    try {
        $ver = & $cmd --version 2>&1
        $verStr = "$ver"
        if ($verStr -match "Python 3[.](\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 8) {
                $pythonCmd = $cmd
                Write-Host "      Python 발견: $verStr" -ForegroundColor Green
                break
            }
        }
    } catch {
        continue
    }
}

if (-not $pythonCmd) {
    Write-Host ""
    Write-Host "[오류] Python 3.8 이상이 설치되어 있지 않습니다." -ForegroundColor Red
    Write-Host ""
    Write-Host "  설치 방법:" -ForegroundColor White
    Write-Host "  1. https://www.python.org/downloads/ 접속" -ForegroundColor White
    Write-Host "  2. Download Python 3.x.x 클릭 후 설치" -ForegroundColor White
    Write-Host "  3. 설치 시 Add Python to PATH 반드시 체크!" -ForegroundColor Yellow
    Write-Host "  4. 설치 완료 후 이 파일 다시 실행" -ForegroundColor White
    Write-Host ""
    Read-Host "엔터를 누르면 종료됩니다"
    exit 1
}

# 2. pip 패키지 설치
Write-Host ""
Write-Host "[2/5] 필요 패키지 설치 중... (수 분 소요될 수 있음)" -ForegroundColor Yellow

$reqFile = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path $reqFile)) {
    Write-Host "[오류] requirements.txt 파일이 없습니다." -ForegroundColor Red
    Read-Host "엔터를 누르면 종료됩니다"
    exit 1
}

& $pythonCmd -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "[경고] pip 업그레이드 실패 (무시하고 계속)" -ForegroundColor DarkYellow
}

& $pythonCmd -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "[오류] 패키지 설치 실패했습니다." -ForegroundColor Red
    Read-Host "엔터를 누르면 종료됩니다"
    exit 1
}
Write-Host "      패키지 설치 완료!" -ForegroundColor Green

# 2-1. Playwright 브라우저 설치 (코스트코 크롤링용)
Write-Host ""
Write-Host "[2-1/5] Playwright 브라우저(Chromium) 설치 중..." -ForegroundColor Yellow
Write-Host "        (코스트코 쇼핑몰 크롤링에 필요합니다. 수 분 소요)" -ForegroundColor Gray
& $pythonCmd -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Host "      [경고] Playwright 브라우저 설치 실패 (크롤링 기능 비활성화)" -ForegroundColor DarkYellow
    Write-Host "             나중에 수동 실행: python -m playwright install chromium" -ForegroundColor Gray
} else {
    Write-Host "      Playwright Chromium 설치 완료!" -ForegroundColor Green
}

# 3. 바탕화면 바로가기 생성
Write-Host ""
Write-Host "[3/5] 바탕화면 바로가기 생성 중..." -ForegroundColor Yellow

$desktopPath = [System.Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "코스트코 핫딜 관리.lnk"
$startBat = Join-Path $ScriptDir "start_server.bat"

try {
    $WShell = New-Object -ComObject WScript.Shell
    $sc = $WShell.CreateShortcut($shortcutPath)
    $sc.TargetPath = $startBat
    $sc.WorkingDirectory = $ScriptDir
    $sc.Description = "코스트코 핫딜 관리 프로그램 시작"
    $sc.IconLocation = "shell32.dll,14"
    $sc.Save()
    Write-Host "      바탕화면에 바로가기 생성 완료!" -ForegroundColor Green
} catch {
    Write-Host "      (바로가기 생성 실패 - 무시하고 계속)" -ForegroundColor DarkYellow
}

# 4. data 폴더 생성
Write-Host ""
Write-Host "[4/5] 데이터 폴더 준비 중..." -ForegroundColor Yellow

$dataDir = Join-Path $ScriptDir "data"
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}
Write-Host "      완료!" -ForegroundColor Green

# 완료 안내
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  설치 완료!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  사용 방법:" -ForegroundColor White
Write-Host "  1. 바탕화면의 '코스트코 핫딜 관리' 바로가기 실행" -ForegroundColor White
Write-Host "     (또는 start_server.bat 직접 실행)" -ForegroundColor White
Write-Host "  2. 브라우저에서 http://localhost:8501 접속" -ForegroundColor White
Write-Host "  3. '회원가입' 탭에서 계정 신청" -ForegroundColor White
Write-Host "  4. 관리자 승인 후 사용 가능" -ForegroundColor White
Write-Host ""
Write-Host "  관리자 계정 (최초 로그인):" -ForegroundColor Yellow
Write-Host "    아이디: admin" -ForegroundColor Yellow
Write-Host "    비밀번호: admin1234" -ForegroundColor Yellow
Write-Host ""

$answer = Read-Host "지금 바로 서버를 시작하시겠습니까? (y/n)"
if ($answer -match "^[yY]") {
    $startBat2 = Join-Path $ScriptDir "start_server.bat"
    if (Test-Path $startBat2) {
        Write-Host ""
        Write-Host "서버를 시작합니다..." -ForegroundColor Cyan
        Start-Process "cmd.exe" -ArgumentList "/c `"$startBat2`""
        Start-Sleep -Seconds 3
        Start-Process "http://localhost:8501"
    }
}

Write-Host ""
Write-Host "설치가 완료되었습니다. 이 창을 닫아도 됩니다." -ForegroundColor Green
Write-Host ""