$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Python 명령어 찾기
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ("$ver" -match "Python 3") {
            $pythonCmd = $cmd
            break
        }
    } catch { continue }
}

if (-not $pythonCmd) {
    Write-Host "[오류] Python을 찾을 수 없습니다. Python이 설치되어 있는지 확인하세요." -ForegroundColor Red
    Read-Host "엔터를 누르면 종료"
    exit 1
}

# IP 주소 표시
$ips = @()
try {
    $ips = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.*" }).IPAddress
} catch { }

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  코스트코 핫딜 관리 - 서버 시작" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  로컬 접속:  http://localhost:8501" -ForegroundColor Green
foreach ($ip in $ips) {
    Write-Host "  네트워크:   http://${ip}:8501" -ForegroundColor Green
}
Write-Host ""
Write-Host "  종료하려면 이 창을 닫거나 Ctrl+C" -ForegroundColor DarkGray
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# 브라우저 자동 열기 (3초 후)
$job = Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Start-Process "http://localhost:8501"
}

# Streamlit 실행 (python -m streamlit 방식 - PATH 문제 없음)
& $pythonCmd -m streamlit run app.py `
    --server.address=0.0.0.0 `
    --server.port=8501 `
    --server.headless=true `
    --browser.gatherUsageStats=false

Write-Host ""
Write-Host "서버가 종료되었습니다." -ForegroundColor Yellow
Read-Host "엔터를 누르면 창이 닫힙니다"