# 코스트코핫딜 배포 패키지 생성 스크립트
param([string]$AppDir = "")

$ErrorActionPreference = "Stop"

if (-not $AppDir) {
    $AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$AppDir = $AppDir.TrimEnd('\').TrimEnd('.')
if (-not (Test-Path $AppDir)) {
    $AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $AppDir

$today   = Get-Date -Format "yyyyMMdd"
$zipName = "costco_hotdeal_$today.zip"
$outZip  = Join-Path $AppDir $zipName

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Costco Hotdeal - Build Package" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. README 생성 ─────────────────────────────────────
$readmePath = Join-Path $AppDir "README.md"
$lines = @(
    "# Costco Hotdeal - Installation Guide",
    "",
    "## Quick Install",
    "1. Unzip the archive",
    "2. Run ``install.bat``  (auto-installs Python packages + desktop shortcut)",
    "3. Run ``start_server.bat``",
    "4. Open browser: http://localhost:8501",
    "5. Sign up and wait for admin approval",
    "",
    "## Daily Use",
    "- Double-click the desktop shortcut: Costco Hotdeal Management",
    "- Or run ``start_server.bat`` directly",
    "",
    "## Boot-time Auto Start (optional)",
    "- Run ``setup_server_boot.bat`` as Administrator",
    "",
    "## API Setup Guide",
    "- See the [Settings Guide] tab inside the app",
    "  - Naver Commerce API (order sync + auto-ship)",
    "  - KakaoTalk notifications (shopping list alerts)",
    "  - Naver Open API (keyword rank tracking)",
    "",
    "## Automation",
    "- Configure tasks in the [Automation] tab",
    "  - Task 1: Daily shopping list via KakaoTalk",
    "  - Task 2: CJ auto-ship + Naver dispatch",
    "  - Task 4: Keyword rank auto-check",
    "",
    "## Requirements",
    "- Windows 10 or later",
    "- Python 3.8+ (install.bat will guide you if missing)",
    "- Internet connection",
    "",
    "## Troubleshooting",
    "| Issue | Fix |",
    "|---|---|",
    "| Port 8501 conflict | Run stop_server.bat then start_server.bat |",
    "| Package install error | Run install.bat as Administrator |",
    "| API error | See FAQ in the Settings Guide tab |",
    "",
    "---",
    "Build: $today"
)
$lines | Set-Content -Path $readmePath -Encoding UTF8
Write-Host "  [1/3] README.md created" -ForegroundColor Green

# ── 2. 스테이징 폴더에 파일 복사 ───────────────────────
$tmp = Join-Path $env:TEMP ("costco_deploy_" + (Get-Date -Format "yyyyMMddHHmmss"))
New-Item -ItemType Directory -Path $tmp | Out-Null

$rootFiles = @(
    "app.py", "db.py", "services.py", "utils.py", "ui_theme.py",
    "naver_api.py", "coupang_api.py", "auto_task.py", "costco_crawler.py",
    "requirements.txt", "README.md", "icon.ico",
    "install.bat", "install.ps1",
    "start_server.bat", "start_server.ps1",
    "stop_server.bat", "setup_server_boot.bat", "setup_schedule.bat"
)

$copied  = 0
$missing = @()

Write-Host "  [2/3] Copying files..." -ForegroundColor Yellow
Write-Host ""

foreach ($f in $rootFiles) {
    $src = Join-Path $AppDir $f
    if (Test-Path $src) {
        Copy-Item $src $tmp
        Write-Host ("    + " + $f) -ForegroundColor Green
        $copied++
    } else {
        $missing += $f
        Write-Host ("    - MISSING: " + $f) -ForegroundColor DarkYellow
    }
}

# pages_lib 폴더 복사 (__pycache__ 제외)
$pLibSrc = Join-Path $AppDir "pages_lib"
$pLibDst = Join-Path $tmp "pages_lib"
New-Item -ItemType Directory -Path $pLibDst | Out-Null

$pyFiles = Get-ChildItem $pLibSrc -Filter "*.py"
foreach ($pf in $pyFiles) {
    Copy-Item $pf.FullName $pLibDst
    Write-Host ("    + pages_lib\" + $pf.Name) -ForegroundColor Cyan
    $copied++
}

# ── 3. ZIP 생성 ────────────────────────────────────────
Write-Host ""
Write-Host "  [3/3] Compressing to ZIP..." -ForegroundColor Yellow

if (Test-Path $outZip) { Remove-Item $outZip -Force }
Compress-Archive -Path (Join-Path $tmp "*") -DestinationPath $outZip -Force
Remove-Item $tmp -Recurse -Force

# ── 결과 출력 ──────────────────────────────────────────
if (Test-Path $outZip) {
    $sizeMB = [math]::Round((Get-Item $outZip).Length / 1MB, 2)

    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host "  Package created successfully!" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host ("  File : " + $zipName)        -ForegroundColor White
    Write-Host ("  Size : " + $sizeMB + " MB") -ForegroundColor White
    Write-Host ("  Files: " + $copied)         -ForegroundColor White

    if ($missing.Count -gt 0) {
        Write-Host ""
        Write-Host "  WARNING - skipped (not found):" -ForegroundColor DarkYellow
        foreach ($m in $missing) { Write-Host ("    - " + $m) -ForegroundColor DarkYellow }
    }

    Write-Host ""
    Write-Host "  How to distribute:" -ForegroundColor Yellow
    Write-Host "  1. Send the ZIP file to the user (email / USB)" -ForegroundColor White
    Write-Host "  2. User unzips the archive"                     -ForegroundColor White
    Write-Host "  3. User runs install.bat"                       -ForegroundColor White
    Write-Host "  4. User runs start_server.bat"                  -ForegroundColor White
    Write-Host "  5. Open http://localhost:8501 -> sign up"       -ForegroundColor White
    Write-Host ""

    Start-Process explorer.exe -ArgumentList ("/select,`"" + $outZip + "`"")
} else {
    Write-Host ""
    Write-Host "  ERROR: ZIP creation failed!" -ForegroundColor Red
    exit 1
}
