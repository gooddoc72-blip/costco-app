/**
 * CSV 파서 — 택배사 PIDPIC/접수파일에서 주문번호/송장번호 추출.
 * UI/네트워크 의존 없음 — 순수 함수.
 */

export interface ParsedTrackingRow {
  orderNo: string;
  trackingNo: string;
}

const ORDER_KEYWORDS = ['주문번호', '고객주문', 'order', '주문 번호'];
const TRACK_KEYWORDS = ['운송장', '송장', 'tracking', '운송 장', '운송번호', 'waybill'];

/** 파일을 EUC-KR로 읽기 (한국 택배사 CSV 표준) */
export function readFileAsEucKr(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsText(file, 'euc-kr');
  });
}

/** 헤더에서 주문번호/송장번호 컬럼 인덱스 찾기 */
function findColumn(headers: string[], keywords: string[]): number {
  return headers.findIndex(h =>
    keywords.some(kw => h.toLowerCase().includes(kw.toLowerCase()))
  );
}

/** CSV/TSV 텍스트 → 파싱된 행 리스트 */
export function parseTrackingCsv(text: string): ParsedTrackingRow[] {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (lines.length < 2) return [];

  const sep = lines[0].includes('\t') ? '\t' : ',';
  const headers = lines[0].split(sep).map(s => s.trim().replace(/^"|"$/g, ''));

  const ordIdx = findColumn(headers, ORDER_KEYWORDS);
  const trkIdx = findColumn(headers, TRACK_KEYWORDS);

  if (ordIdx < 0 || trkIdx < 0 || ordIdx === trkIdx) {
    throw new Error(
      `주문번호/송장번호 컬럼을 못 찾았습니다. 발견된 컬럼: ${headers.join(', ')}`
    );
  }

  const rows: ParsedTrackingRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(sep).map(s =>
      s.trim().replace(/^"|"$/g, '').replace(/-/g, '')
    );
    const o = cells[ordIdx];
    const t = cells[trkIdx];
    if (o && t && o.length > 5 && t.length > 5 && t !== 'nan') {
      rows.push({ orderNo: o, trackingNo: t });
    }
  }
  return rows;
}
