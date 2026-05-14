export async function fetchSettings(): Promise<Record<string, any>> {
  const res = await fetch('/api/settings');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function saveSettings(payload: Record<string, any>): Promise<void> {
  const res = await fetch('/api/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await res.text());
}
