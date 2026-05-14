export interface TrackingLogItem {
  orderNo: string;
  trackingNo?: string;
  courier?: string;
  platform?: string;
}

export async function postTrackingLog(
  items: TrackingLogItem[], dispatchDate: string
): Promise<{ saved: number; errors: string[] }> {
  const res = await fetch('/api/tracking/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items, dispatchDate }),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '저장 실패');
  return json;
}
