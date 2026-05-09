import sqlite3, os
db = os.path.join(os.path.dirname(__file__), 'data', 'admin.db')
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT id, recipient, product_name, qty, settlement, shipping_fee,
           cost_price, delivery_cost, box_cost, profit
    FROM daily_orders WHERE order_date='2026-05-07'
    ORDER BY product_name
""").fetchall()

print(f"총 {len(rows)}행\n")
print(f"{'수취인':<8} {'수량':>3} {'정산예정':>8} {'고객택배':>8} {'구입가':>8} {'발송비':>6} {'박스':>5} {'저장수입':>9} {'재계산':>9} {'일치':>4}")
print("-"*80)
mismatch = 0
for r in rows:
    calc = (r['settlement'] + r['shipping_fee']) - (r['cost_price'] + r['delivery_cost'] + r['box_cost'])
    ok = "OK" if calc == r['profit'] else "XX"
    if calc != r['profit']:
        mismatch += 1
    name = str(r['recipient'] or '')[:7]
    print(f"{name:<8} {r['qty']:>3} {r['settlement']:>8} {r['shipping_fee']:>8} {r['cost_price']:>8} {r['delivery_cost']:>6} {r['box_cost']:>5} {r['profit']:>9} {calc:>9} {ok:>4}")

print(f"\n불일치 행: {mismatch}건 / 전체 {len(rows)}건")
conn.close()
