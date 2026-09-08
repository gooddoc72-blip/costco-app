# -*- coding: utf-8 -*-
"""흩어진 영수증을 auth.db 한 곳으로 옮긴다.

영수증은 관리자만 등록하는 공용 사실인데 receipt_items가 사용자별 DB에
있었고, 재고·매칭 코드는 모든 사용자 DB를 훑었다. 그래서 옛 영수증 페이지로
개인이 올린 것(oxo 8월 93행)이 공용 재고 풀에 섞였다.
저장소가 흩어져 있으면 '무엇이 재고인가'가 볼 때마다 달라진다.

같은 (날짜, 상품번호, 상품명)이 여러 DB에 있으면 admin 것을 우선한다 —
관리자가 올린 것이 정본이다.

사용: python migrate_receipts_to_auth.py [--apply]
"""
import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APPLY = '--apply' in sys.argv

from db_core import DATA_DIR
from db_stats import _receipt_conn

dst = _receipt_conn()
have = {(r[0], r[1], r[2]) for r in
        dst.execute("SELECT receipt_date, product_no, product_name FROM receipt_items")}
print("auth.db 기존 %d행 · %s" % (len(have), "적용" if APPLY else "미리보기"))

# admin 을 먼저 읽어 정본으로 삼는다
paths = sorted(glob.glob(os.path.join(DATA_DIR, '*.db')),
               key=lambda p: (os.path.basename(p) != 'admin.db', p))
added = skipped = 0
for path in paths:
    u = os.path.basename(path)[:-3]
    if u == 'auth' or '.bak' in u or '.backup' in u:
        continue
    try:
        src = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
        src.row_factory = sqlite3.Row
        cols = {r[1] for r in src.execute("PRAGMA table_info(receipt_items)")}
        if not cols:
            src.close()
            continue
        _d = "COALESCE(discount,0)" if 'discount' in cols else "0"
        _l = "COALESCE(list_price,0)" if 'list_price' in cols else "0"
        rows = src.execute(
            "SELECT receipt_date, product_no, product_name, qty, unit_price, "
            "%s AS d, %s AS l FROM receipt_items ORDER BY id" % (_d, _l)).fetchall()
        src.close()
    except Exception as e:
        print("  %-14s 읽기 실패: %s" % (u, str(e)[:40]))
        continue
    if not rows:
        continue
    _a = _s = 0
    for r in rows:
        key = (r['receipt_date'] or '', str(r['product_no'] or ''),
               r['product_name'] or '')
        if not key[0] or not key[2]:
            continue
        if key in have:
            _s += 1
            continue
        have.add(key)
        _a += 1
        if APPLY:
            dst.execute(
                "INSERT OR IGNORE INTO receipt_items (receipt_date, product_no, "
                "product_name, qty, unit_price, discount, list_price, uploaded_by, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,'')",
                (key[0], key[1], key[2], int(r['qty'] or 1), int(r['unit_price'] or 0),
                 int(r['d'] or 0), int(r['l'] or 0) or int(r['unit_price'] or 0), u))
    added += _a
    skipped += _s
    print("  %-14s %4d행 → 신규 %4d · 이미있음 %4d" % (u, len(rows), _a, _s))

if APPLY:
    dst.commit()
n = dst.execute("SELECT COUNT(*) FROM receipt_items").fetchone()[0]
dst.close()
print("합계: 신규 %d · 중복 %d · auth.db 최종 %d행" % (added, skipped, n))
if not APPLY:
    print("실제로 옮기려면: python migrate_receipts_to_auth.py --apply")
