#!/bin/bash
# 매일 자동 키워드 순위 체크 — 사용자별로 한 번씩 호출.
# /etc/cron.d/costcobiz-rank 또는 사용자 crontab에서 실행.
#
# 환경:
#   CRON_SECRET  Next.js와 같은 값 (서비스 .env에도 있어야 함)
#   USERS        공백 구분 사용자명 (예: "admin user1 user2")
#   BASE_URL     기본값 http://127.0.0.1:3000
#
# 예시 cron 라인 (매일 12:00):
#   0 12 * * * /opt/costco-app/mobile/deploy/cron-rank-check.sh >> /var/log/costcobiz-rank.log 2>&1

set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1:3000}"
if [ -z "${CRON_SECRET:-}" ]; then
  echo "[$(date '+%F %T')] CRON_SECRET not set — abort" >&2
  exit 1
fi
if [ -z "${USERS:-}" ]; then
  echo "[$(date '+%F %T')] USERS not set — abort" >&2
  exit 1
fi

for U in $USERS; do
  echo "[$(date '+%F %T')] rank-check user=$U"
  if ! curl -fsS -X POST -H "Authorization: Bearer $CRON_SECRET" \
       "$BASE_URL/api/cron/rank-check?user=$U"; then
    echo "[$(date '+%F %T')] FAILED user=$U" >&2
  fi
  echo
done
