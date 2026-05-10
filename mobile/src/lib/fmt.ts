export function won(n: number | null | undefined): string {
  if (n == null) return '0원';
  return `${Math.round(n).toLocaleString('ko-KR')}원`;
}

export function num(n: number | null | undefined): string {
  if (n == null) return '0';
  return Math.round(n).toLocaleString('ko-KR');
}

export function pct(a: number, b: number): string {
  if (!b) return '—';
  return `${(((a - b) / b) * 100).toFixed(1)}%`;
}
