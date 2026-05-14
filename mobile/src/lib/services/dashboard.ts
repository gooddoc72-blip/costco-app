/**
 * Dashboard Service — 기간 계산 + repo 집계.
 * UI는 결과 객체만 보고 렌더링.
 */
import {
  fetchKpi, fetchTrend, fetchAlerts,
  type KpiBucket, type TrendPoint, type DashboardAlerts,
} from '@/lib/repositories/dashboard';

export type { KpiBucket, TrendPoint, DashboardAlerts };

export interface DashboardData {
  today: KpiBucket;
  week: KpiBucket;
  month: KpiBucket;
  lastWeek: KpiBucket;
  lastMonth: KpiBucket;
  trend7: TrendPoint[];
  alerts: DashboardAlerts;
}

function fmt(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function weekRange(base: Date): [Date, Date] {
  // 월요일 시작
  const dow = base.getDay() === 0 ? 6 : base.getDay() - 1;
  const start = new Date(base); start.setDate(base.getDate() - dow);
  const end = new Date(start); end.setDate(start.getDate() + 6);
  return [start, end];
}

function monthRange(base: Date): [Date, Date] {
  return [
    new Date(base.getFullYear(), base.getMonth(), 1),
    new Date(base.getFullYear(), base.getMonth() + 1, 0),
  ];
}

export function getDashboard(username: string): DashboardData {
  const today = new Date();
  const [wStart, wEnd] = weekRange(today);
  const [mStart, mEnd] = monthRange(today);

  const lwBase = new Date(today); lwBase.setDate(today.getDate() - 7);
  const [lwStart, lwEnd] = weekRange(lwBase);

  const lmBase = new Date(today.getFullYear(), today.getMonth() - 1, 15);
  const [lmStart, lmEnd] = monthRange(lmBase);

  return {
    today: fetchKpi(username, fmt(today), fmt(today)),
    week: fetchKpi(username, fmt(wStart), fmt(wEnd)),
    month: fetchKpi(username, fmt(mStart), fmt(mEnd)),
    lastWeek: fetchKpi(username, fmt(lwStart), fmt(lwEnd)),
    lastMonth: fetchKpi(username, fmt(lmStart), fmt(lmEnd)),
    trend7: fetchTrend(username, 7),
    alerts: fetchAlerts(username),
  };
}
