"""daily_orders 결제일 재분산 마이그레이션 (1회용).

문제: save_daily_orders 옛 버전이 모든 미발송 주문을 단일 날짜로 저장
해결: order_history의 실제 결제일을 매칭하여 daily_orders.order_date 갱신

매칭 키: (recipient, product_name, qty, settlement)
백업: daily_orders_backup_YYYYMMDD 테이블 자동 생성
"""
import sqlite3
import os
import sys
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def migrate(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    if not os.path.exists(db_path):
        print(f"[SKIP] {username}.db 없음")
        return

    print(f"\n[{username}.db] 마이그레이션 시작")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. 백업 테이블 생성
    backup_name = f"daily_orders_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn.execute(f"CREATE TABLE {backup_name} AS SELECT * FROM daily_orders")
    backup_count = conn.execute(f"SELECT COUNT(*) FROM {backup_name}").fetchone()[0]
    print(f"  [BACKUP] {backup_name} ({backup_count}행)")

    # 2. 현재 분포 (마이그 전)
    print("  [BEFORE] daily_orders 날짜별 분포:")
    rows = conn.execute("""
        SELECT order_date, COUNT(*) cnt FROM daily_orders
        GROUP BY order_date ORDER BY order_date DESC
    """).fetchall()
    for r in rows:
        print(f"    {r['order_date']}: {r['cnt']}건")

    # 3. 매칭 - order_history 결제일로 갱신
    do_rows = conn.execute("""
        SELECT id, order_date, recipient, product_name, qty, settlement
        FROM daily_orders
    """).fetchall()
    print(f"  [MATCH] daily_orders {len(do_rows)}행 매칭 시도")

    updated = 0
    not_matched = 0
    same_date = 0

    for r in do_rows:
        # 1차: recipient + product_name + qty + settlement 모두 일치
        oh_row = conn.execute("""
            SELECT order_date FROM order_history
            WHERE recipient = ? AND product_name = ?
              AND qty = ? AND settlement = ?
              AND order_date IS NOT NULL AND order_date != ''
            ORDER BY id DESC LIMIT 1
        """, (r['recipient'], r['product_name'], r['qty'], r['settlement'])).fetchone()

        # 2차: settlement 제외 (settlement 변경 가능성)
        if not oh_row:
            oh_row = conn.execute("""
                SELECT order_date FROM order_history
                WHERE recipient = ? AND product_name = ? AND qty = ?
                  AND order_date IS NOT NULL AND order_date != ''
                ORDER BY id DESC LIMIT 1
            """, (r['recipient'], r['product_name'], r['qty'])).fetchone()

        if oh_row:
            new_date = str(oh_row['order_date'])[:10]
            if new_date and new_date != r['order_date']:
                conn.execute(
                    "UPDATE daily_orders SET order_date=? WHERE id=?",
                    (new_date, r['id'])
                )
                updated += 1
            else:
                same_date += 1
        else:
            not_matched += 1

    conn.commit()

    print(f"  [RESULT] 갱신 {updated}건 / 동일 {same_date}건 / 매칭실패 {not_matched}건")

    # 4. 마이그 후 분포
    print("  [AFTER] daily_orders 날짜별 분포:")
    rows = conn.execute("""
        SELECT order_date, COUNT(*) cnt,
               SUM(settlement) sum_s, SUM(profit) sum_p
        FROM daily_orders
        GROUP BY order_date ORDER BY order_date DESC
    """).fetchall()
    for r in rows:
        print(f"    {r['order_date']}: {r['cnt']:>4}건  정산 {r['sum_s']:>12,}  수익 {r['sum_p']:>10,}")

    conn.close()
    print(f"  [DONE] 백업: {backup_name}")


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else None
    if not targets:
        # data 폴더의 모든 사용자 DB 자동 검색
        targets = []
        for f in os.listdir(DATA_DIR):
            if f.endswith(".db") and f != "auth.db":
                targets.append(f[:-3])

    print(f"대상 사용자: {targets}")
    for u in targets:
        try:
            migrate(u)
        except Exception as e:
            print(f"[ERROR] {u}: {e}")

    print("\n=== 마이그레이션 완료 ===")
