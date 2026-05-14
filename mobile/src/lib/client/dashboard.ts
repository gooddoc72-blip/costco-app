import type { DashboardData } from '@/lib/services/dashboard';

export type { DashboardData };

export async function fetchDashboard(): Promise<DashboardData> {
  const res = await fetch('/api/dashboard/kpi');
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '대시보드 로드 실패');
  return json;
}
