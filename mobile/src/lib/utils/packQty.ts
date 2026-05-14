/**
 * 옵션 문자열에서 묶음수량 추출.
 *
 * 예: '2구' → 2, '3개묶음' → 3, '1+1' → 2, 'x2' → 2, '× 3' → 3
 * 상품명에서는 자동 추출하지 않음 (박스 정보 ≠ 주문 변수).
 *
 * 안전장치: 'x' 패턴은 앞이 숫자가 아닐 때만 인식 → '2x4cm' 같은 치수 표기 제외.
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
    /\bx\s*(\d+)(?!\d)/i,   // 'g x2', 'x 3팩' (앞이 word boundary일 때만 — '2x4cm' 같은 치수 제외)
    /×\s*(\d+)(?!\d)/,      // 전각 곱하기
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
