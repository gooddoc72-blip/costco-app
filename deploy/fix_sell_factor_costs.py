#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sell_factor 오적용으로 오염된 cost_price 일괄 정정 (1회성 마이그레이션).

배경
----
상품명의 'x N개'를 무조건 '1주문 = N개'로 해석하던 시절이 있었다.
'340ml x 20개'처럼 **용량 뒤** 표기는 상품 스펙인데 이걸 sell_factor로 잡는 바람에
  · 구입가가 N배로 부풀거나(15,690 → 313,800)
  · 그 값을 '단가 일괄적용'으로 되돌리면 단가가 1/N로 저장돼(15,690 → 784)
정산 데이터(cost_price)에 그대로 박제됐다.

이 스크립트는 **버그로만 설명되는 값**(정확히 ×sf 또는 ÷sf)만 골라 되돌린다.
사람이 손으로 넣은 임의값은 건드리지 않는다.

사용법
------
  python3 fix_sell_factor_costs.py              # 점검만 (기본, 아무것도 안 바꿈)
  python3 fix_sell_factor_costs.py --apply      # 백업 후 실제 정정
  python3 fix_sell_factor_costs.py --apply --user oxo
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

APP_DIR = os.environ.get("COSTCO_APP_DIR", "/opt/costco-app")
DATA_DIR = os.path.join(APP_DIR, "data")
sys.path.insert(0, APP_DIR)

from services import match_product_to_db                    # noqa: E402

# 옛 규칙 — 가드 없이 'x N개'를 그대로 배수로 썼다. 오염 여부 판정용으로만 재현한다.
_OLD_RE = re.compile(r'x\s*(\d+)\s*개', re.IGNORECASE)

# 새 규칙 — utils.extract_sell_factor와 동일. 배포 전에도 돌 수 있게 사본을 둔다.
#   (일회성 마이그레이션이라 이 사본은 실행 후 수명이 끝난다)
_SPEC_UNIT_RE = re.compile(
    r'(?:'
    r'(?:g|kg|ml|l|ℓ|리터|그램|캡슐|정|매|포)'
    r'|\d\s*(?:mg|cc|oz|온스|밀리리터|장|알|입|ea|인분|cm|미터)'
    r')\s*$',
    re.IGNORECASE)
_SELL_FACTOR_RE = re.compile(r'[x×]\s*(\d+)\s*개', re.IGNORECASE)


def extract_sell_factor(name_str, lo=2, hi=50):
    text = str(name_str or '')
    for m in _SELL_FACTOR_RE.finditer(text):
        if _SPEC_UNIT_RE.search(text[:m.start()]):
            continue
        v = int(m.group(1))
        if lo <= v <= hi:
            return v
    return 1
TARGET_TABLES = ("profit_settlements", "daily_orders", "order_history")


def old_sell_factor(name):
    m = _OLD_RE.search(str(name or ''))
    if not m:
        return 1
    v = int(m.group(1))
    return v if 1 < v <= 50 else 1


_SHARED_CACHE = None


def _load_shared():
    """공유 제품 DB 1회 로드 (사용자 수만큼 다시 읽지 않게)."""
    global _SHARED_CACHE
    if _SHARED_CACHE is None:
        try:
            c = sqlite3.connect("file:%s?mode=ro" % os.path.join(DATA_DIR, "auth.db"), uri=True)
            c.row_factory = sqlite3.Row
            _SHARED_CACHE = [dict(r) for r in c.execute("select * from shared_products")]
            c.close()
        except Exception:
            _SHARED_CACHE = []
    return _SHARED_CACHE


def user_dbs(only_user=None):
    for f in sorted(os.listdir(DATA_DIR)):
        if not f.endswith(".db") or f in ("auth.db",):
            continue
        name = f[:-3]
        if only_user and name != only_user:
            continue
        yield name, os.path.join(DATA_DIR, f)


def scan_db(username, path, apply_changes):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    tabs = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    if "products" not in tabs:
        conn.close()
        return []

    prods = [dict(r) for r in conn.execute("select * from products")]
    shared = _load_shared()

    findings = []
    for tbl in TARGET_TABLES:
        if tbl not in tabs:
            continue
        cols = {c[1] for c in conn.execute("PRAGMA table_info(%s)" % tbl)}
        if not {"cost_price", "qty", "product_name"} <= cols:
            continue
        pno_col = "product_no" if "product_no" in cols else None
        for row in conn.execute("select * from %s where cost_price > 0" % tbl):
            r = dict(row)
            name = str(r.get("product_name") or "")
            p = match_product_to_db(username, name,
                                    product_no=(str(r.get(pno_col) or "") or None) if pno_col else None,
                                    _user_prods=prods, _shared_prods=shared or None)
            if not p:
                continue
            unit = int(p.get("unit_price") or 0)
            sq = max(1, int(p.get("split_qty") or 1))
            if unit <= 0:
                continue

            # 'x N개'는 주문 상품명뿐 아니라 **제품DB에 등록된 정식 상품명**에도 있다.
            #   784 사고가 후자였다 — 주문명엔 x가 없는데 제품 정식명의 'x 20개' 때문에
            #   제품 단가가 1/20로 저장돼 모든 주문 행이 깎여 나갔다.
            names = [name, p.get("costco_name"), p.get("store_product_name"),
                     p.get("match_keyword")]
            cands = {old_sell_factor(n) for n in names if n}
            cands -= {extract_sell_factor(n) for n in names if n}   # 새 규칙과 같은 값은 오염 아님
            cands.discard(1)
            if not cands:
                continue

            qty = max(1, int(r.get("qty") or 1))
            cur = int(r.get("cost_price") or 0)
            sf_new = extract_sell_factor(name)
            correct = (unit // sq) * qty * sf_new
            if cur == correct:
                continue
            for n_old in sorted(cands, reverse=True):
                bug_div = (unit // n_old // sq) * qty * sf_new   # 단가가 1/N로 저장된 뒤 계산됨
                bug_mul = (unit // sq) * qty * n_old             # sell_factor가 N배로 곱해짐
                if cur not in (bug_div, bug_mul):
                    continue                   # 버그로 설명 안 되는 수동값 → 보존
                findings.append({
                    "table": tbl, "id": r.get("id"), "name": name, "qty": qty,
                    "sf_old": n_old, "unit": unit, "split": sq,
                    "before": cur, "after": correct,
                    "kind": "÷%d" % n_old if cur == bug_div else "×%d" % n_old,
                })
                break

    if apply_changes and findings:
        for f in findings:
            conn.execute("update %s set cost_price=? where id=?" % f["table"],
                         (f["after"], f["id"]))
        conn.commit()
    conn.close()
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 정정 (기본은 점검만)")
    ap.add_argument("--user", default=None, help="특정 사용자 DB만")
    args = ap.parse_args()

    total = 0
    for username, path in user_dbs(args.user):
        if args.apply:
            bak = "%s.bak_%s" % (path, datetime.now().strftime("%Y%m%d_%H%M%S"))
            shutil.copy2(path, bak)
        findings = scan_db(username, path, args.apply)
        if not findings:
            continue
        total += len(findings)
        print("=== %s : %d건" % (username, len(findings)))
        by_name = {}
        for f in findings:
            k = (f["name"][:45], f["kind"], f["unit"])
            by_name.setdefault(k, []).append(f)
        for (nm, kind, unit), rows in sorted(by_name.items(), key=lambda x: -len(x[1])):
            ex = rows[0]
            print("   [%s] %-45s 단가%s  %d건  예) %s → %s (수량 %d)"
                  % (kind, nm, "{:,}".format(unit), len(rows),
                     "{:,}".format(ex["before"]), "{:,}".format(ex["after"]), ex["qty"]))
    print("─" * 60)
    print(("정정 완료 %d건 (백업본 *.bak_* 생성됨)" if args.apply
           else "점검 결과 %d건 — 실제 반영하려면 --apply") % total)


if __name__ == "__main__":
    main()
