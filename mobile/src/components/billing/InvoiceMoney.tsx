'use client';
/**
 * 청구서 '돈 정리' — 한 사람의 하루치를 위에서 아래로 읽히게 보여 준다.
 *
 *   🛒 내 구매 (예치금에서 차감 / 계좌로 받음 / 아직 낼 돈)
 *   💳 재고 판매 적립 (남이 내 재고를 씀)
 *   = 이날 예치금 변화 · 지금 예치금 잔액
 *
 * 예전 한 줄('청구금액 − 예치금 차감 = 남은 청구 · 예치금 잔액 −25,990(이날 차감 후)')은
 * 빼기처럼 읽히고, 잔액이 그날 결제 직후 값이라 나중에 들어온 재고 적립이 빠져 있어
 * 헷갈렸다(11/11). 금액은 모두 파이썬 원장 값(ItemsResp.money)이다.
 */
import clsx from 'clsx';
import { ItemsResp } from '@/lib/client/billing';
import { num } from '@/lib/fmt';

const signed = (n: number) => (n < 0 ? `−${num(-n)}` : n > 0 ? `+${num(n)}` : '0');
const balWord = (n: number) => (n < 0 ? `${num(-n)}원 부족` : n > 0 ? `${num(n)}원 남음` : '0원');

export default function InvoiceMoney({ it }: { it: ItemsResp }) {
  const m = it.money;
  if (!m) return null;
  const md = (it.date || '').slice(5).replace('-', '/');
  const bought = it.items.filter(x => x.amount !== 0);
  const ownStock = it.items.filter(x => x.amount === 0 && x.source === '재고');
  const ownQty = ownStock.reduce((a, x) => a + x.qty, 0);
  const ownNames = Array.from(new Set(ownStock.map(x => x.product_name.slice(0, 24))));

  // 같은 상품은 한 줄로 — 누가 몇 개 썼는지는 괄호에
  const cg = new Map<string, { name: string; qty: number; amount: number; who: Map<string, number> }>();
  for (const c of m.credits) {
    const k = c.product_name || c.order_no;
    const g = cg.get(k) || { name: c.product_name || '(상품명 없음)', qty: 0, amount: 0, who: new Map() };
    g.qty += c.qty; g.amount += c.amount;
    g.who.set(c.buyer_name || c.buyer, (g.who.get(c.buyer_name || c.buyer) || 0) + c.qty);
    cg.set(k, g);
  }

  const payLine = m.deposit_paid
    ? <span className="text-green-700">✅ 낼 돈 없음 — {md} 물건값 {num(m.deposit_paid)}원을 예치금에서 냈습니다.</span>
    : m.account_paid
      ? <span className="text-green-700">✅ 낼 돈 없음 — 계좌로 {num(m.account_paid)}원 받았습니다.</span>
      : m.status === 'draft'
        ? <span className="text-gray-600">청구 전 — 청구하면 {num(m.total)}원을 받습니다.</span>
        : m.total > 0
          ? <span className="text-amber-700">⚠️ 아직 낼 돈 {num(m.left)}원</span>
          : <span className="text-gray-600">이날 낼 돈 없음</span>;

  return (
    <div className="bg-white rounded-lg border p-3 space-y-3 text-sm">
      <section className="space-y-1">
        <div className="font-semibold">🛒 내 구매</div>
        {bought.length === 0 && <div className="text-xs text-gray-500 pl-5">돈이 드는 품목 없음</div>}
        {bought.map(x => (
          <div key={x.id} className="flex justify-between gap-2 pl-5 text-xs">
            <span className="truncate">{x.product_name}{x.product_no && <span className="text-gray-400"> ({x.product_no})</span>}</span>
            <span className="whitespace-nowrap">{x.qty}개 · <b>{num(x.amount)}원</b></span>
          </div>
        ))}
        {ownQty > 0 && (
          <div className="pl-5 text-xs text-gray-500">
            ※ {ownNames.join(', ')} {ownQty}개는 <b>내 재고에서 보냄 → 0원</b> (이미 산 물건)
          </div>
        )}
        <div className="pl-5 text-xs">{payLine}</div>
      </section>

      {cg.size > 0 && (
        <section className="space-y-1">
          <div className="font-semibold">💳 재고 판매 적립 <span className="text-xs font-normal text-gray-500">— 남이 내 재고를 써서 받은 돈</span></div>
          {Array.from(cg.values()).map(g => (
            <div key={g.name} className="flex justify-between gap-2 pl-5 text-xs">
              <span className="truncate">
                {g.name} <span className="text-gray-500">({Array.from(g.who.entries()).map(([w, q]) => `${w} ${q}`).join(' · ')})</span>
              </span>
              <span className="whitespace-nowrap text-green-700">{g.qty}개 · <b>+{num(g.amount)}원</b></span>
            </div>
          ))}
        </section>
      )}

      {(m.deposit_paid > 0 || m.credit_sum > 0) && (
        <section className="border-t pt-2 space-y-1">
          <div className="flex justify-between gap-2">
            <span className="font-semibold">= {md} 예치금 변화</span>
            <span className={clsx('font-bold whitespace-nowrap', m.day_net < 0 ? 'text-red-600' : 'text-green-700')}>
              {m.deposit_paid > 0 && `−${num(m.deposit_paid)}`}
              {m.deposit_paid > 0 && m.credit_sum > 0 && ' '}
              {m.credit_sum > 0 && `+${num(m.credit_sum)}`}
              {m.deposit_paid > 0 && m.credit_sum > 0 && ` = ${signed(m.day_net)}`}원
            </span>
          </div>
        </section>
      )}
      <div className={clsx('flex justify-between gap-2 text-xs', (m.deposit_paid > 0 || m.credit_sum > 0) ? '' : 'border-t pt-2')}>
        <span className="text-gray-600">지금 예치금 잔액 <span className="text-gray-400">(다른 날 정산까지 모두 포함)</span></span>
        <span className={clsx('font-semibold whitespace-nowrap', m.balance_now < 0 && 'text-red-600')}>{balWord(m.balance_now)}</span>
      </div>
    </div>
  );
}
