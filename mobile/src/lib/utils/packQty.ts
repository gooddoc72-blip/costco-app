/**
 * 옵션 문자열에서 묶음수량 추출.
 *
 * 예: '2구' → 2, '3개묶음' → 3, '1+1' → 2
 * 상품명에서는 자동 추출하지 않음 (박스 정보 ≠ 주문 변수).
 */
export function extractPackQty(optionStr: string): number {
  const t = (optionStr || '').trim();
  if (!t) return 1;

  const m = t.match(/(\d)\s*\+\s*(\d)/);
  if (m) {
    const v = parseInt(m[1], 10) + parseInt(m[2], 10);
    if (v > 1 && v <= 30) return v;
  }
  const patterns = [
    /(\d+)\s*구\b/,
    /(\d+)\s*개\s*묶음/,
    /(\d+)\s*개\s*세트/,
    /(\d+)\s*p(?:ack)?\b/i,
    /(\d+)\s*set\b/i,
  ];
  for (const re of patterns) {
    const mm = t.match(re);
    if (mm) {
      const v = parseInt(mm[1], 10);
      if (v > 1 && v <= 30) return v;
    }
  }
  return 1;
}
