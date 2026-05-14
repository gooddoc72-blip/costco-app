#!/bin/bash
# 자동 키워드 순위 체크 cron 설치 (root 권한 필요).
#
# 사용법:
#   sudo CRON_SECRET=xxxxxx USERS="admin user1" bash deploy/cron-install.sh
#
# 사용자별로 매일 12:00에 순위 체크 실행. systemd 환경에선 별도 .service/.timer 가능하지만
# 단순 cron이 가장 적은 셋업으로 동작한다.

set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "root 권한 필요 (sudo 실행)" >&2; exit 1
fi
: "${CRON_SECRET:?CRON_SECRET 환경변수 필요}"
: "${USERS:?USERS 환경변수 필요 (공백 구분)}"

SCRIPT="/opt/costco-app/mobile/deploy/cron-rank-check.sh"
LOG="/var/log/costcobiz-rank.log"

if [ ! -x "$SCRIPT" ]; then
  chmod +x "$SCRIPT"
fi
touch "$LOG"; chown ubuntu:ubuntu "$LOG"

CRON_FILE=/etc/cron.d/costcobiz-rank
cat > "$CRON_FILE" <<EOF
# costcobiz — 매일 12:00 자동 순위 체크
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_SECRET=$CRON_SECRET
USERS=$USERS
BASE_URL=http://127.0.0.1:3000
0 12 * * * ubuntu $SCRIPT >> $LOG 2>&1
EOF
chmod 644 "$CRON_FILE"
echo "✅ cron 등록: $CRON_FILE"
echo "   확인: sudo cat $CRON_FILE"
echo "   로그: tail -f $LOG"
echo ""
echo "⚠️  중요 — costco-mobile.service의 Environment에도 CRON_SECRET 추가 필요:"
echo "   sudo systemctl edit costco-mobile"
echo "   [Service]"
echo "   Environment=\"CRON_SECRET=$CRON_SECRET\""
echo "   그 후: sudo systemctl restart costco-mobile"
