/**
 * POST /api/receipt/parse-photo — 휴대폰으로 찍은 영수증 '사진' 다중 업로드 + AI 판독.
 * (PDF는 /api/receipt/parse, DB 저장은 /api/receipt/apply — 저장 경로는 공용)
 */
import { NextRequest, NextResponse } from 'next/server';
import { getSessionUser } from '@/lib/session';
import { getAiKeys } from '@/lib/services/aiKeys';
import { parseReceiptPhoto } from '@/lib/vision/receiptPhoto';
import type { ReceiptItem } from '@/lib/pdf/costcoReceipt';

export const runtime = 'nodejs';
export const maxDuration = 120;   // 사진 판독은 장당 수십 초까지 걸린다

export async function POST(req: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const form = await req.formData();
  const files = form.getAll('files').filter((f): f is File => f instanceof File);
  if (files.length === 0) return NextResponse.json({ error: 'files required' }, { status: 400 });

  const keys = getAiKeys(user.username);
  if (!keys.geminiKey && !keys.anthropicKey) {
    return NextResponse.json(
      { error: 'AI 키 미설정 — PC 설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키를 등록하세요.' },
      { status: 400 },
    );
  }

  const all: ReceiptItem[] = [];
  const errors: Array<{ file: string; error: string }> = [];
  const checks: Array<{ file: string; provider: string; verified: boolean; issues: string[] }> = [];

  for (const f of files) {
    const name = f.name || '사진';
    try {
      const buf = Buffer.from(await f.arrayBuffer());
      const r = await parseReceiptPhoto(buf, f.type || 'image/jpeg', keys, name);
      if (r.error) {
        errors.push({ file: name, error: r.error });
        continue;
      }
      if (r.items.length === 0) {
        errors.push({ file: name, error: '품목을 읽지 못했습니다 (더 밝고 반듯하게 다시 촬영)' });
        continue;
      }
      all.push(...r.items);
      checks.push({ file: name, provider: r.provider, verified: r.verified, issues: r.issues });
    } catch (e: any) {
      errors.push({ file: name, error: e?.message || '판독 실패' });
    }
  }

  // 같은 상품번호는 영수증 날짜가 최신인 것 우선 (PDF 경로와 동일 규칙)
  const byNo = new Map<string, ReceiptItem>();
  const noNumber: ReceiptItem[] = [];
  for (const it of all) {
    if (!it.productNo) { noNumber.push(it); continue; }
    const prev = byNo.get(it.productNo);
    if (!prev || it.receiptDate > prev.receiptDate) byNo.set(it.productNo, it);
  }

  return NextResponse.json({
    items: [...byNo.values(), ...noNumber],
    errors,
    checks,
  });
}
