export function won(n: number | null | undefined): string {
  if (n == null) return '0원';
  return `${Math.round(n).toLocaleString('ko-KR')}원`;
}

export function num(n: number | null | undefined): string {
  if (n == null) return '0';
  return Math.round(n).toLocaleString('ko-KR');
}

/** 단순 천단위 구분 (단위 없음) — 표/카드용 */
export const fmt = num;

export function pct(a: number, b: number): string {
  if (!b) return '—';
  return `${(((a - b) / b) * 100).toFixed(1)}%`;
}
