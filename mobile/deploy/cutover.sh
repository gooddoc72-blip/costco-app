#!/bin/bash
# PC→Next.js 전환 스크립트 (Streamlit은 /legacy/*에 격리)
# 실행 전 반드시 mobile 코드를 빌드해두고 systemd 서비스가 살아있는지 확인.
#
# 사용법:
#   cd /opt/costco-app/mobile
#   sudo bash deploy/cutover.sh
#
# 롤백:
#   sudo cp /etc/nginx/sites-available/costco-app.bak.<TIMESTAMP> /etc/nginx/sites-available/costco-app
#   sudo nginx -t && sudo systemctl reload nginx

set -e

cd /opt/costco-app/mobile

echo "[1/4] Next.js 최신 빌드"
sudo -u ubuntu npm install
sudo -u ubuntu npm run build

echo "[2/4] systemd 재시작"
sudo systemctl restart costco-mobile
sleep 2
sudo systemctl --no-pager status costco-mobile | head -8

echo "[3/4] nginx 설정 백업 + 교체"
TS=$(date +%s)
sudo cp /etc/nginx/sites-available/costco-app /etc/nginx/sites-available/costco-app.bak.${TS}
sudo cp deploy/nginx-snippet.conf /etc/nginx/sites-available/costco-app

echo "[4/4] nginx 검증 + 리로드"
sudo nginx -t
sudo systemctl reload nginx

echo
echo "✅ 완료. https://cocobiz.shop 접속해서 확인."
echo "   - 기본 페이지: Next.js (대시보드/주문/제품/송장/수익/설정)"
echo "   - 안 옮긴 기능: https://cocobiz.shop/legacy/ — Streamlit"
echo
echo "롤백:"
echo "   sudo cp /etc/nginx/sites-available/costco-app.bak.${TS} /etc/nginx/sites-available/costco-app"
echo "   sudo nginx -t && sudo systemctl reload nginx"
