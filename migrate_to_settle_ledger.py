"""옛 정산 저장소 → 새 정산 원장(settle_item · settle_invoice) 이관.

옛 테이블은 지우지 않는다. 옮긴 값이 이상하면 대조할 원본이 있어야 하고,
되돌릴 길을 막고 하는 이관은 사고가 나면 복구할 방법이 없다.

  receipt_settle_items    → settle_item      (품목 = 청구 근거)
  purchase_settle_snapshot→ settle_invoice   (fees_total만 — 금액은 품목에서 다시 계산)

같은 (날짜, 사용자, 주문)이 옛 배치에 여러 번 있으면 가장 최근 행만 쓴다.
하루에 정산을 여러 번 돌린 날은 회차마다 행이 쌓여 있어 그대로 옮기면 두 배로
청구된다 — 실제로 그게 옛 구조의 대표적인 사고였다.

사용법:
    python migrate_to_settle_ledger.py --dry-run    # 무엇이 옮겨지는지만 본다
    python migrate_to_settle_ledger.py              # 실제 이관
    python migrate_to_settle_ledger.py --verify     # 이관 후 옛 값과 대조
"""
import sqlite3
import sys

# 윈도우 콘솔은 기본이 cp949라 한글·기호에서 죽는다.
# 이관 결과를 못 읽고 끝나면 설사 잘 돌았더라도 확인할 수가 없다.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

from db_core import AUTH_DB
import db_settle as ds


def _tables():
    conn = sqlite3.connect(AUTH_DB)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def verify():
    """옛 원장 합계 vs 새 청구서 합계 — 날짜×사용자로 대조한다.

    금액이 다른 줄만 찍는다. 전부 같으면 조용히 끝난다.
    """
    have = _tables()
    if 'receipt_settle_items' not in have:
        print("옛 테이블(receipt_settle_items)이 없습니다 — 대조할 것이 없습니다.")
        return 0
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    old = {}
    for r in conn.execute(
            "SELECT order_date d, username u, SUM(amount) a FROM receipt_settle_items "
            "WHERE id IN (SELECT MAX(id) FROM receipt_settle_items "
            "             GROUP BY order_date, username, order_no) "
            "GROUP BY order_date, username"):
        old[(str(r['d']), str(r['u']))] = int(r['a'] or 0)
    new = {}
    for r in conn.execute("SELECT settle_date d, username u, goods_amount g "
                          "FROM settle_invoice"):
        new[(str(r['d']), str(r['u']))] = int(r['g'] or 0)
    conn.close()

    diff = 0
    for k in sorted(set(old) | set(new)):
        o, n = old.get(k, 0), new.get(k, 0)
        if o != n:
            diff += 1
            print(f"  차이 {k[0]} {k[1]}: 옛 {o:,} → 새 {n:,} ({n - o:+,})")
    if diff:
        print(f"\n! {diff}건이 다릅니다. 하루에 여러 번 정산한 날은 옛 합계 쪽이 "
              "중복이라 새 값이 맞습니다 — 위 목록에서 확인하세요.")
    else:
        print(f"OK 전부 일치 ({len(new)}건).")
    return diff


def main():
    args = set(sys.argv[1:])
    if '--verify' in args:
        return verify()

    dry = '--dry-run' in args
    have = _tables()
    print(f"auth.db: {AUTH_DB}")
    print("옛 테이블: " + ", ".join(
        t for t in ('receipt_settle_items', 'purchase_settle_snapshot',
                    'daily_billing', 'billing_ledger') if t in have) or "(없음)")

    ds.ensure()
    res = ds.migrate_legacy(dry_run=dry)
    tag = "[미리보기] " if dry else ""
    print(f"{tag}품목 {res['items']}건 · 청구서 {res['invoices']}건 이관"
          + (f" · 이미 있어 건너뜀 {res['skipped_existing']}건"
             if res['skipped_existing'] else ""))
    if dry:
        print("\n실제로 옮기려면 --dry-run 없이 다시 실행하세요.")
    else:
        print("\n대조하려면: python migrate_to_settle_ledger.py --verify")
        print("옛 테이블은 그대로 남아 있습니다 — 문제가 없다고 확인한 뒤에 지우세요.")
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
