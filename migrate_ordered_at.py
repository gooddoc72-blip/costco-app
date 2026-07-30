"""order_history.ordered_at 백필 (1회성, 재실행 안전).

order_date는 '수집일'(날짜만)이라 마감시각(12:00) 전/후를 구분할 수 없다.
raw_json에 남아 있는 주문일시/결제일에서 실제 주문시각을 복원한다.
raw_json이 없는 옛 행은 비워 두고(=마감 판정 제외, 보수적) 다음 수집 때 채워진다.

실행: venv/bin/python3 migrate_ordered_at.py [사용자명 ...]   (인자 없으면 data/*.db 전체)
"""
import os
import sys
import glob
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
SKIP = {'auth.db'}


def migrate(db_path):
    name = os.path.basename(db_path)
    conn = sqlite3.connect(db_path)
    try:
        _tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='order_history'").fetchone()
        if not _tbl:
            print(f"  {name}: order_history 없음 → skip")
            return
        cols = {r[1] for r in conn.execute("PRAGMA table_info(order_history)")}
        if 'ordered_at' not in cols:
            conn.execute("ALTER TABLE order_history ADD COLUMN ordered_at TEXT DEFAULT ''")
            conn.commit()
            print(f"  {name}: ordered_at 컬럼 추가")
        n = 0
        for _key in ('주문일시', '결제일', '주문일'):
            cur = conn.execute(
                f"""UPDATE order_history
                       SET ordered_at = REPLACE(json_extract(raw_json, '$.{_key}'), 'T', ' ')
                     WHERE COALESCE(ordered_at, '') = ''
                       AND COALESCE(raw_json, '') != ''
                       AND json_valid(raw_json)
                       AND LENGTH(COALESCE(json_extract(raw_json, '$.{_key}'), '')) >= 16""")
            n += cur.rowcount
        conn.commit()
        _filled = conn.execute(
            "SELECT COUNT(*) FROM order_history WHERE COALESCE(ordered_at,'') != ''").fetchone()[0]
        _total = conn.execute("SELECT COUNT(*) FROM order_history").fetchone()[0]
        print(f"  {name}: 이번 백필 {n}건 / 시각보유 {_filled}건 / 전체 {_total}건")
    finally:
        conn.close()


def main():
    args = sys.argv[1:]
    if args:
        paths = [os.path.join(DATA, f"{a}.db") for a in args]
    else:
        paths = [p for p in sorted(glob.glob(os.path.join(DATA, '*.db')))
                 if os.path.basename(p) not in SKIP]
    print(f"대상 DB {len(paths)}개")
    for p in paths:
        if not os.path.exists(p):
            print(f"  {p} 없음 → skip")
            continue
        migrate(p)
    print("완료")


if __name__ == '__main__':
    main()
