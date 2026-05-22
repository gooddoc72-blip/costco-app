#!/bin/bash
# costcobiz mobile 서버 최초 설치
# 사용법: cd /opt/costco-app/mobile && sudo bash deploy/setup.sh
set -e

cd /opt/costco-app/mobile

echo "[1/5] Node.js 20 설치 (없으면)"
if ! command -v node >/dev/null || [ "$(node -v | cut -dv -f2 | cut -d. -f1)" -lt 20 ]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
node -v && npm -v

echo "[2/5] 의존성 설치 + 빌드"
sudo -u ubuntu npm install
sudo -u ubuntu npm run build

echo "[3/5] systemd 서비스 등록"
sudo cp deploy/costco-mobile.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable costco-mobile
sudo systemctl restart costco-mobile
sleep 2
sudo systemctl status costco-mobile --no-pager

echo "[4/5] nginx 설정 교체 (백업 후)"
sudo cp /etc/nginx/sites-available/costco-app /etc/nginx/sites-available/costco-app.bak.$(date +%s)
sudo cp deploy/nginx-snippet.conf /etc/nginx/sites-available/costco-app
sudo nginx -t && sudo systemctl reload nginx

echo "[5/5] 완료!"
echo "  PC:    https://cocobiz.shop"
echo "  모바일: https://cocobiz.shop (자동 분기) 또는 https://cocobiz.shop/m/"
