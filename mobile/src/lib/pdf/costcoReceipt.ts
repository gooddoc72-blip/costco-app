/**
 * 코스트코 영수증 PDF 파서 — pdf-parse로 텍스트 추출 후 줄 단위 패턴 매칭.
 *
 * 영수증 패턴:
 *   "상품명"
 *   "<상품번호> <수량> <단가> <합계>" (예: "1234567 2 12,000 24,000 T")
 *   → 상품명/번호/수량/단가 추출
 */
import pdf from 'pdf-parse';

export interface ReceiptItem {
  productNo: string;
  productName: string;
  qty: number;
  unitPrice: number;
  receiptDate: string;
  sourceFile?: string;
}

const SKIP_KEYWORDS = ['코스트코코리아', '대표자', '부산시', '판매', '닫기', 'costco', 'http'];
const ITEM_RE = /^(\d{4,7})\s+(\d+)\s+([\d,]+)\s+[\d,\-\s]+\s*[TFN]?\s*$/;
const DATE_RE = /(?:날\s*자|날짜|date)[^\d]*(\d{2,4})[/\-.](\d{1,2})[/\-.](\d{1,2})|(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})/i;

function extractReceiptDate(lines: string[]): string {
  for (const line of lines) {
    const m = line.match(DATE_RE);
    if (!m) continue;
    let y: string, mo: string, d: string;
    if (m[1]) { y = m[1]; mo = m[2]; d = m[3]; }
    else      { y = m[4]; mo = m[5]; d = m[6]; }
    if (y.length === 2) y = '20' + y;
    return `${y}-${String(parseInt(mo, 10)).padStart(2, '0')}-${String(parseInt(d, 10)).padStart(2, '0')}`;
  }
  return '';
}

export async function parseCostcoReceiptPdf(buf: Buffer, sourceFile?: string): Promise<{
  items: ReceiptItem[];
  error?: string;
  preview?: string;
}> {
  let text: string;
  try {
    const out = await pdf(buf);
    text = out?.text || '';
  } catch (e: any) {
    return { items: [], error: `PDF 파싱 오류: ${e?.message || e}` };
  }
  if (!text.trim()) {
    return { items: [], error: '텍스트 추출 실패 (스캔/암호화 PDF)' };
  }
  const lines = text.split('\n');
  const date = extractReceiptDate(lines);
  const items: ReceiptItem[] = [];
  let skipNext = false;

  for (let i = 0; i < lines.length - 1; i++) {
    if (skipNext) { skipNext = false; continue; }
    const line = lines[i].trim();
    const next = lines[i + 1].trim();
    if (line === '*** CPN') { skipNext = true; continue; }
    if (line.includes('CPN') || line.includes('IRC')) continue;
    const m = next.match(ITEM_RE);
    if (!m) continue;
    const name = line;
    if (!name || name.length < 2) continue;
    const lower = name.toLowerCase();
    if (SKIP_KEYWORDS.some(k => lower.includes(k.toLowerCase()))) continue;
    items.push({
      productNo: m[1],
      productName: name,
      qty: parseInt(m[2], 10),
      unitPrice: parseInt(m[3].replace(/,/g, ''), 10),
      receiptDate: date,
      sourceFile,
    });
    skipNext = true;
  }
  if (items.length === 0) {
    return { items: [], error: '상품 패턴 미인식', preview: text.slice(0, 600).trim() };
  }
  return { items };
}
