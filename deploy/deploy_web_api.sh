#!/usr/bin/env bash
# Phase4 — 수익계산 API 서비스 + React 정적앱 배포 (VPS에서 실행).
#   API: costco-api.service (uvicorn 8610). React: /opt/costco-app/web-out (nginx /calc/).
# 정적 out/ 은 로컬에서 빌드해 scp 로 web-out/ 에 올린 뒤 이 스크립트 실행.
set -euo pipefail
APP=/opt/costco-app

echo "== 1) fastapi/uvicorn 설치 확인 =="
"$APP/venv/bin/pip" install --quiet 'fastapi>=0.110' 'uvicorn>=0.29'

echo "== 2) systemd 유닛 설치 =="
sudo cp "$APP/deploy/costco-api.service" /etc/systemd/system/costco-api.service
sudo systemctl daemon-reload
sudo systemctl enable costco-api.service
sudo systemctl restart costco-api.service
sleep 2
systemctl is-active costco-api.service
curl -s http://127.0.0.1:8610/api/health && echo

echo "== 3) nginx location 추가 안내 =="
echo "  /etc/nginx/sites-enabled/costco-app 의 443 server{} 안에"
echo "  deploy/nginx-additions.conf 의 location 2개를 추가 후:"
echo "    sudo nginx -t && sudo systemctl reload nginx"
echo "완료: API 기동됨. nginx 반영은 수동 확인(운영 안전)."
