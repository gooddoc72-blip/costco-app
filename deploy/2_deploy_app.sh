#!/bin/bash
# =============================================================
# 앱 배포 스크립트 (코드 업로드 후 실행)
# 사용법: sudo bash 2_deploy_app.sh
# =============================================================
set -e

APP_USER="costco"
APP_DIR="/opt/costco-app"
VENV_DIR="$APP_DIR/venv"

echo "=============================="
echo " [1/5] 코드 복사"
echo "=============================="
# 현재 디렉토리의 코드를 앱 디렉토리로 복사
# (이 스크립트는 앱 소스 루트에서 실행)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
rsync -av --exclude='deploy/' \
    --exclude='*.pyc' \
    --exclude='__pycache__/' \
    --exclude='.git/' \
    --exclude='data/' \
    --exclude='.env' \
    "$SCRIPT_DIR/" "$APP_DIR/"

chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
echo "코드 복사 완료: $APP_DIR"

echo "=============================="
echo " [2/5] Python 가상환경 설정"
echo "=============================="
if [ ! -d "$VENV_DIR" ]; then
    sudo -u "$APP_USER" python3.11 -m venv "$VENV_DIR"
    echo "가상환경 생성 완료"
fi

sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
echo "패키지 설치 완료"

echo "=============================="
echo " [3/5] Playwright 브라우저 설치"
echo "=============================="
sudo -u "$APP_USER" "$VENV_DIR/bin/playwright" install chromium || true

echo "=============================="
echo " [4/5] .env 파일 확인"
echo "=============================="
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "⚠️  .env 파일을 반드시 수정하세요:"
    echo "    nano $APP_DIR/.env"
    echo ""
fi
chmod 600 "$APP_DIR/.env"
chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"

echo "=============================="
echo " [5/5] systemd 서비스 등록"
echo "=============================="
cp "$APP_DIR/deploy/costco-app.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable costco-app
systemctl restart costco-app
sleep 2
systemctl status costco-app --no-pager

echo ""
echo "✅ 배포 완료!"
echo "다음 단계: 3_setup_nginx.sh 실행 (도메인 준비된 경우)"
