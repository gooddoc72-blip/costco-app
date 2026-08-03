/**
 * 🧾 영수증 '사진' 판독 (휴대폰 촬영) — Gemini 우선 → Claude 폴백.
 *
 * PDF 파서(costcoReceipt.ts)와 같은 ReceiptItem[]을 돌려주므로,
 * 이후 매입가 반영 흐름(/api/receipt/apply)은 그대로 재사용된다.
 *
 * 프롬프트·검증 규칙은 파이썬 ai_service.py와 동일하게 맞췄다.
 * (한쪽만 고치면 PC/모바일 판독 결과가 갈리므로 바꿀 때 같이 볼 것)
 */
import type { ReceiptItem } from '@/lib/pdf/costcoReceipt';
import type { AiKeys } from '@/lib/services/aiKeys';

const GEMINI_MODEL = 'gemini-2.5-flash';
const CLAUDE_VISION_MODEL = 'claude-sonnet-5';
const TIMEOUT_MS = 90_000;

const SYSTEM = [
  '너는 한국 대형마트(코스트코 / 이마트 트레이더스) 영수증 판독 전문가다. ',
  '휴대폰으로 찍은 영수증 사진을 보고 JSON으로만 출력한다(설명·마크다운 금지).\n',
  '출력 형식:\n',
  '{"purchase_date":"YYYY-MM-DD","store_type":"코스트코|트레이더스","total_qty":정수,',
  '"item_kinds":정수,"total_amount":정수,"discount_amount":정수,',
  '"items":[{"product_no":"상품번호","name":"상품명","qty":정수,"unit_price":정수,',
  '"amount":정수,"discount":정수}]}\n',
  '규칙:\n',
  '- purchase_date: 영수증의 거래일자. 2자리 연도는 20xx로. 안 보이면 "".\n',
  '- total_qty: 구매수량 합계. item_kinds: 품목 종수(줄 수).\n',
  '- total_amount: 실제 결제한 최종 합계금액(할인 반영 후). 숫자만.\n',
  '- discount_amount: 할인·쿠폰 합계. 없으면 0. 양수로.\n',
  '- items: 품목 줄마다 1개. 상품번호(보통 6~7자리)가 있으면 넣고 없으면 "". ',
  'unit_price=단가, amount=금액. 안 보이는 값은 0.\n',
  '- 흐리거나 잘려서 안 보이는 값은 절대 지어내지 말고 빈 문자열/0으로 둔다.',
].join('');

const USER_TEXT =
  '이 영수증에서 구매일자·매장·구매수량·구매금액·할인금액과 품목 내역(상품번호·상품명·수량·단가·금액)을 ' +
  '읽어 JSON으로 출력해줘.';

export interface PhotoParseResult {
  items: ReceiptItem[];
  provider: 'gemini' | 'claude' | '';
  verified: boolean;
  issues: string[];
  error?: string;
}

function extractJson(text: string): any | null {
  const s = (text || '').trim();
  const i = s.indexOf('{');
  const j = s.lastIndexOf('}');
  if (i < 0 || j <= i) return null;
  try {
    const v = JSON.parse(s.slice(i, j + 1));
    return v && typeof v === 'object' ? v : null;
  } catch {
    return null;
  }
}

function num(v: any): number {
  const n = parseInt(String(v ?? '').replace(/[,원\s]/g, ''), 10);
  return Number.isFinite(n) ? n : 0;
}

function normDate(v: any): string {
  const m = String(v ?? '').match(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (!m) return '';
  return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`;
}

/** AI 응답 → ReceiptItem[] + 검증. 파이썬 validate_receipt와 같은 체크섬. */
function toItems(data: any, sourceFile?: string): { items: ReceiptItem[]; issues: string[]; ok: boolean } {
  const date = normDate(data?.purchase_date);
  const rawItems: any[] = Array.isArray(data?.items) ? data.items : [];
  const items: ReceiptItem[] = [];
  for (const it of rawItems) {
    const name = String(it?.name ?? '').trim();
    if (!name) continue;
    const qty = num(it?.qty) || 1;
    const unitPrice = num(it?.unit_price);
    items.push({
      productNo: String(it?.product_no ?? '').replace(/\D/g, ''),
      productName: name,
      qty,
      unitPrice,
      receiptDate: date,
      sourceFile,
    });
  }

  const issues: string[] = [];
  let critical = false;
  if (items.length === 0) return { items, issues: ['품목 내역을 하나도 못 읽음'], ok: false };

  const sumAmt = rawItems.reduce((a, it) => a + (num(it?.amount) || num(it?.unit_price) * (num(it?.qty) || 1)), 0);
  const sumDisc = rawItems.reduce((a, it) => a + num(it?.discount), 0);
  const sumQty = items.reduce((a, it) => a + it.qty, 0);
  const total = num(data?.total_amount);
  const disc = Math.abs(num(data?.discount_amount));

  if (total > 0) {
    const cands = [sumAmt, sumAmt - disc, sumAmt - sumDisc, sumAmt - disc - sumDisc];
    if (!cands.some(c => Math.abs(total - c) <= 1)) {
      issues.push(`금액 불일치 — 품목합 ${sumAmt.toLocaleString()}원 vs 결제금액 ${total.toLocaleString()}원`);
      critical = true;
    }
  } else {
    issues.push('결제금액(합계)을 못 읽음');
    critical = true;
  }

  const tq = num(data?.total_qty);
  if (tq && sumQty && tq !== sumQty) {
    issues.push(`수량 불일치 — 품목합 ${sumQty}개 vs 총수량 ${tq}개`);
    critical = true;
  }
  const kinds = num(data?.item_kinds);
  if (kinds && kinds !== items.length) {
    issues.push(`품목 수 불일치 — 읽은 ${items.length}종 vs 표기 ${kinds}종`);
  }
  if (!date) issues.push('구매일자를 못 읽음');

  return { items, issues, ok: !critical };
}

async function postJson(url: string, headers: Record<string, string>, body: any): Promise<any> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = json?.error?.message || `HTTP ${res.status}`;
      throw new Error(`[${res.status}] ${msg}`);
    }
    return json;
  } finally {
    clearTimeout(timer);
  }
}

async function geminiVision(apiKey: string, b64: string, mime: string): Promise<string> {
  const json = await postJson(
    `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`,
    { 'x-goog-api-key': apiKey },
    {
      systemInstruction: { parts: [{ text: SYSTEM }] },
      contents: [{ role: 'user', parts: [{ inline_data: { mime_type: mime, data: b64 } }, { text: USER_TEXT }] }],
      // flash는 기본 thinking이 출력토큰을 다 먹어 빈 응답이 나므로 꺼둔다
      generationConfig: { maxOutputTokens: 4000, temperature: 0, thinkingConfig: { thinkingBudget: 0 } },
    },
  );
  const parts = json?.candidates?.[0]?.content?.parts ?? [];
  return parts.map((p: any) => p?.text ?? '').join('').trim();
}

async function claudeVision(apiKey: string, b64: string, mime: string): Promise<string> {
  const json = await postJson(
    'https://api.anthropic.com/v1/messages',
    { 'x-api-key': apiKey, 'anthropic-version': '2023-06-01' },
    {
      model: CLAUDE_VISION_MODEL,
      max_tokens: 4000,
      system: SYSTEM,
      messages: [{
        role: 'user',
        content: [
          { type: 'image', source: { type: 'base64', media_type: mime, data: b64 } },
          { type: 'text', text: USER_TEXT },
        ],
      }],
    },
  );
  const blocks = json?.content ?? [];
  return blocks.filter((b: any) => b?.type === 'text').map((b: any) => b.text ?? '').join('').trim();
}

/**
 * 사진 1장 판독. Gemini로 먼저 읽고 자가검증 통과하면 채택,
 * 실패하면 Claude로 재판독 — 파이썬 parse_receipt_photo와 같은 전략.
 */
export async function parseReceiptPhoto(
  buf: Buffer, mime: string, keys: AiKeys, sourceFile?: string,
): Promise<PhotoParseResult> {
  const b64 = buf.toString('base64');
  const mediaType = /^image\/(jpeg|png|webp|gif)$/.test(mime) ? mime : 'image/jpeg';
  const empty: PhotoParseResult = { items: [], provider: '', verified: false, issues: [] };

  if (!keys.geminiKey && !keys.anthropicKey) {
    return { ...empty, error: 'AI 키 미설정 — PC 설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키를 등록하세요.' };
  }

  let gemini: { items: ReceiptItem[]; issues: string[]; ok: boolean } | null = null;
  let firstErr = '';

  if (keys.geminiKey) {
    try {
      const txt = await geminiVision(keys.geminiKey, b64, mediaType);
      const data = extractJson(txt);
      if (data) {
        gemini = toItems(data, sourceFile);
        if (gemini.ok) {
          return { items: gemini.items, provider: 'gemini', verified: true, issues: gemini.issues };
        }
      } else {
        firstErr = 'Gemini 응답이 JSON이 아님';
      }
    } catch (e: any) {
      firstErr = `Gemini 실패(${e?.message || e})`;
    }
    if (!keys.anthropicKey) {
      if (gemini) {
        return { items: gemini.items, provider: 'gemini', verified: gemini.ok, issues: gemini.issues };
      }
      return { ...empty, error: firstErr || '판독 실패' };
    }
  }

  try {
    const txt = await claudeVision(keys.anthropicKey, b64, mediaType);
    const data = extractJson(txt);
    if (data) {
      const claude = toItems(data, sourceFile);
      // 둘 다 검증 실패면 경고가 적은 쪽 (동률이면 Claude)
      if (!claude.ok && gemini && (gemini.ok || gemini.issues.length < claude.issues.length)) {
        return { items: gemini.items, provider: 'gemini', verified: gemini.ok, issues: gemini.issues };
      }
      return { items: claude.items, provider: 'claude', verified: claude.ok, issues: claude.issues };
    }
    if (gemini) {
      return { items: gemini.items, provider: 'gemini', verified: gemini.ok, issues: gemini.issues };
    }
    return { ...empty, error: 'Claude 응답이 JSON이 아님' };
  } catch (e: any) {
    if (gemini) {
      return { items: gemini.items, provider: 'gemini', verified: gemini.ok, issues: gemini.issues };
    }
    return { ...empty, error: firstErr ? `${firstErr} · Claude 실패(${e?.message || e})` : `Claude 실패(${e?.message || e})` };
  }
}
