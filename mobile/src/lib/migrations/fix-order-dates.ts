/**
 * Migration: order_history.order_date를 실제 결제일로 재정렬.
 *
 * 옛 Streamlit 코드가 모든 주문을 "오늘" 또는 사용자 선택 날짜로 강제 저장.
 * raw_json에 실제 payDate가 보존되어 있으면 그것으로 복원.
 */
import { getUserDb } from '@/lib/db';

export interface FixOrderDatesResult {
  scanned: number;
  fixed: number;
  noJsonData: number;
}

function extractPayDate(rawJson: string): string | null {
  if (!rawJson) return null;
  try {
    const j = JSON.parse(rawJson);
    // 네이버 API 응답 구조 다양 — 가능한 위치들 시도
    const candidates = [
      j?.productOrder?.paymentDate,
      j?.paymentDate,
      j?.order?.paymentDate,
      j?.['결제일'],
    ];
    for (const c of candidates) {
      if (typeof c === 'string' && c.length >= 10) {
        return c.slice(0, 10);
      }
    }
  } catch {}
  return null;
}

export function fixOrderDates(username: string): FixOrderDatesResult {
  const db = getUserDb(username);
  const rows = db.prepare(
    "SELECT id, order_date, raw_json FROM order_history WHERE raw_json IS NOT NULL AND raw_json != ''"
  ).all() as any[];

  let fixed = 0;
  let noJsonData = 0;
  const updateStmt = db.prepare("UPDATE order_history SET order_date = ? WHERE id = ?");

  const tx = db.transaction(() => {
    for (const row of rows) {
      const payDate = extractPayDate(row.raw_json);
      if (!payDate) {
        noJsonData++;
        continue;
      }
      if (payDate !== row.order_date) {
        updateStmt.run(payDate, row.id);
        fixed++;
      }
    }
  });
  tx();

  return { scanned: rows.length, fixed, noJsonData };
}
