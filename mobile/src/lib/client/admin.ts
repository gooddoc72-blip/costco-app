export async function runMigration(action: string): Promise<{ ok: boolean; results: any }> {
  const res = await fetch('/api/admin/migrate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || '마이그레이션 실패');
  return json;
}
