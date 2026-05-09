#!/bin/bash
# =============================================================
# Cafe24 VPS 초기 서버 설정 스크립트
# Ubuntu 22.04 LTS 기준
# 사용법: sudo bash 1_server_setup.sh
# =============================================================
set -e

APP_USER="costco"
APP_DIR="/opt/costco-app"
PYTHON_VERSION="3.11"

echo "=============================="
echo " [1/7] 시스템 패키지 업데이트"
echo "=============================="
apt-get update -y
apt-get upgrade -y
apt-get install -y \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    nginx certbot python3-certbot-nginx \
    git curl wget unzip ufw \
    build-essential libssl-dev libffi-dev \
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libasound2

echo "=============================="
echo " [2/7] 앱 전용 사용자 생성"
echo "=============================="
if ! id "$APP_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$APP_USER"
    echo "사용자 '$APP_USER' 생성 완료"
else
    echo "사용자 '$APP_USER' 이미 존재"
fi

echo "=============================="
echo " [3/7] 앱 디렉토리 생성"
echo "=============================="
mkdir -p "$APP_DIR/data"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
chmod 750 "$APP_DIR"
chmod 770 "$APP_DIR/data"

echo "=============================="
echo " [4/7] UFW 방화벽 설정"
echo "=============================="
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
echo "방화벽 설정 완료"

echo "=============================="
echo " [5/7] nginx 설정"
echo "=============================="
systemctl enable nginx
systemctl start nginx

echo "=============================="
echo " [6/7] 시스템 설정 최적화"
echo "=============================="
# 파일 디스크립터 한도 증가
cat >> /etc/security/limits.conf << 'EOF'
* soft nofile 65535
* hard nofile 65535
EOF

# SQLite 동시성 최적화를 위한 스왑 설정 (2GB RAM 이하 서버)
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "스왑 2GB 생성 완료"
fi

echo "=============================="
echo " [7/7] 완료"
echo "=============================="
echo ""
echo "다음 단계: 2_deploy_app.sh 실행"
