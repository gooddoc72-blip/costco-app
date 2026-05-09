#!/bin/bash
# =============================================================
# nginx + SSL 설정 스크립트
# 사용법: sudo bash 3_setup_nginx.sh 도메인명
# 예시:   sudo bash 3_setup_nginx.sh costco.mydomain.com
# =============================================================
set -e

DOMAIN="${1}"
if [ -z "$DOMAIN" ]; then
    echo "❌ 도메인을 인자로 입력해주세요."
    echo "   사용법: sudo bash 3_setup_nginx.sh 도메인명"
    exit 1
fi

EMAIL="admin@${DOMAIN}"

echo "도메인: $DOMAIN"

echo "=============================="
echo " [1/3] nginx 설정 파일 생성"
echo "=============================="
cat > /etc/nginx/sites-available/costco-app << EOF
map \$http_upgrade \$connection_upgrade {
    default upgrade;
    '' close;
}

server {
    listen 80;
    server_name ${DOMAIN};

    # Streamlit WebSocket + HTTP 프록시
    location / {
        proxy_pass         http://127.0.0.1:8501;
        proxy_http_version 1.1;

        proxy_set_header   Upgrade            \$http_upgrade;
        proxy_set_header   Connection         \$connection_upgrade;
        proxy_set_header   Host               \$host;
        proxy_set_header   X-Real-IP          \$remote_addr;
        proxy_set_header   X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto  \$scheme;

        proxy_read_timeout    86400;
        proxy_send_timeout    86400;
        proxy_connect_timeout 86400;

        # 업로드 파일 크기 제한 (50MB)
        client_max_body_size 50M;
    }

    # Streamlit 정적 파일
    location /_stcore/stream {
        proxy_pass         http://127.0.0.1:8501/_stcore/stream;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade   \$http_upgrade;
        proxy_set_header   Connection \$connection_upgrade;
        proxy_read_timeout 86400;
    }
}
EOF

ln -sf /etc/nginx/sites-available/costco-app /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
echo "nginx 설정 완료"

echo "=============================="
echo " [2/3] SSL 인증서 발급 (Let's Encrypt)"
echo "=============================="
certbot --nginx -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --redirect
echo "SSL 인증서 발급 완료"

echo "=============================="
echo " [3/3] 자동 갱신 확인"
echo "=============================="
certbot renew --dry-run
systemctl enable certbot.timer || true

echo ""
echo "✅ 완료! https://${DOMAIN} 으로 접속하세요."
