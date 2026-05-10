"""daily_orders 잘못된 order_date 재배치 마이그레이션

문제: save_daily_orders의 affected_dates DELETE 버그로 인해
      A날짜에 업로드한 주문이 B날짜로 잘못 저장된 경우가 있음.
해결: order_history (UPSERT라 정확)를 기준으로 daily_orders의 order_date 재배치.

매칭 키: (recipient, product_name, qty, settlement)
- 같은 사람이 같은 상품을 여러 날 주문할 수도 있으니 settlement까지 사용

실행: python migrate_daily_orders_realign.py [username]
백업: data/{username}.db.backup_realign_TIMESTAMP
"""
import sqlite3
import os
import sys
import shutil
from datetime import datetime
from collections import defaultdict


def realign(db_path, dry_run=True):
    if not os.path.exists(db_path):
        print(f"[ERR] DB 없음: {db_path}")
        return

    # 백업
    if not dry_run:
        backup = f"{db_path}.backup_realign_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(db_path, backup)
        print(f"[BACKUP] 백업: {backup}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1) order_history로 (recipient, product_name, qty, settlement) → 정확한 order_date 맵 만들기
    oh_map = {}
    for r in conn.execute("""
        SELECT order_date, recipient, product_name, qty, settlement
        FROM order_history
        WHERE order_date IS NOT NULL AND order_date != ''
    """).fetchall():
        key = (r['recipient'], r['product_name'], int(r['qty'] or 0), int(r['settlement'] or 0))
        # 첫 번째 매칭 우선, 같은 키 여러 개면 가장 이른 날짜 (실제 결제 시점)
        if key not in oh_map or r['order_date'] < oh_map[key]:
            oh_map[key] = r['order_date']

    print(f"[INFO] order_history 키 {len(oh_map)}개 로드")

    # 2) daily_orders 전체 스캔 → 잘못 저장된 행 찾기
    do_rows = conn.execute("""
        SELECT id, order_date, recipient, product_name, qty, settlement
        FROM daily_orders
    """).fetchall()
    print(f"[INFO] daily_orders {len(do_rows)}개 스캔")

    to_move = []  # (id, current_date, target_date)
    not_found_in_oh = []  # order_history에 매칭 없는 daily_orders 행

    for r in do_rows:
        key = (r['recipient'], r['product_name'], int(r['qty'] or 0), int(r['settlement'] or 0))
        target_date = oh_map.get(key)
        if target_date and target_date != r['order_date']:
            to_move.append((r['id'], r['order_date'], target_date))
        elif target_date is None:
            not_found_in_oh.append(r)

    print(f"[MOVE] 이동 대상 {len(to_move)}건")
    print(f"[?] order_history에 없는 행 {len(not_found_in_oh)}건 (그대로 유지)")

    # 3) 이동 전후 카운트 미리보기
    if to_move:
        # 날짜별 변화 요약
        before = defaultdict(int)
        after = defaultdict(int)
        for r in do_rows:
            before[r['order_date']] += 1
        for r in do_rows:
            new_date = oh_map.get(
                (r['recipient'], r['product_name'], int(r['qty'] or 0), int(r['settlement'] or 0))
            )
            after[new_date or r['order_date']] += 1

        print()
        print("=== 날짜별 변화 (예상) ===")
        all_dates = sorted(set(list(before.keys()) + list(after.keys())), reverse=True)[:15]
        for d in all_dates:
            b = before.get(d, 0)
            a = after.get(d, 0)
            diff = a - b
            mark = "+" if diff > 0 else ("-" if diff < 0 else " ")
            print(f"  {d}: {b:4d} → {a:4d}  {mark}{abs(diff)}")

    # 4) DRY RUN이 아니면 실제 UPDATE
    if not dry_run and to_move:
        print()
        print("[!] 실제 UPDATE 실행...")
        for row_id, _, target_date in to_move:
            conn.execute("UPDATE daily_orders SET order_date=? WHERE id=?", (target_date, row_id))
        conn.commit()
        print(f"[OK] {len(to_move)}건 이동 완료")
    elif dry_run:
        print()
        print("[DRY] DRY RUN — 실제 변경 안 됨. 적용하려면 --apply 옵션으로 실행")

    conn.close()


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    apply = '--apply' in sys.argv

    if not args:
        # data/ 안의 모든 .db 파일 (admin.db, auth.db 제외)
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        for fn in os.listdir(data_dir):
            if fn.endswith('.db') and fn not in ('auth.db',):
                print(f"=== {fn} ===")
                realign(os.path.join(data_dir, fn), dry_run=not apply)
                print()
    else:
        for username in args:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', f'{username}.db')
            print(f"=== {username} ===")
            realign(db_path, dry_run=not apply)
            print()
