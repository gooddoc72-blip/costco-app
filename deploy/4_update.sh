#!/bin/bash
# =============================================================
# 앱 업데이트 스크립트 (이후 코드 변경 시 사용)
# 사용법: sudo bash 4_update.sh
# =============================================================
set -e

APP_USER="costco"
APP_DIR="/opt/costco-app"
VENV_DIR="$APP_DIR/venv"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[1/3] 코드 업데이트..."
rsync -av --exclude='deploy/' \
    --exclude='*.pyc' \
    --exclude='__pycache__/' \
    --exclude='.git/' \
    --exclude='data/' \
    --exclude='.env' \
    "$SCRIPT_DIR/" "$APP_DIR/"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

echo "[2/3] 패키지 업데이트..."
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "[3/3] 서비스 재시작..."
systemctl restart costco-app
sleep 2
systemctl status costco-app --no-pager

echo "✅ 업데이트 완료!"
