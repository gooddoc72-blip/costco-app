"""네이버 등록 수량(한도) 점검 — 읽기 전용. DB를 고치지 않는다.

실행(서버, 앱 폴더에서):  python3 tools/diag_naver_reg.py
특정 사용자만:            python3 tools/diag_naver_reg.py 사용자아이디

보는 것
  1) 사용자별: 한도 / 한도 대상(카페24 대행 제외) / 누적 / 경로별
  2) 같은 네이버 API 키(client_id)를 여러 계정이 쓰는지 — 쓰면 기록이 엉뚱한 계정으로 간다
  3) 기록의 토큰 태그가 그 계정의 지금 키와 다른 건 — 다른 사람 키로 올라간 흔적
  4) 상품DB에는 네이버 원상품번호가 있는데 기록에 없는 건 — 기록이 빠진 등록
  5) 주인을 못 찾은 기록(미매칭 토큰)
"""
import os
import sqlite3
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
EXEMPT = ('cafe24',)


def ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def setting(conn, key):
    try:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str((r['value'] if r else '') or '').strip()
    except sqlite3.Error:
        return ''


def main(only=''):
    auth = ro(os.path.join(DATA, "auth.db"))
    users = [dict(r) for r in auth.execute(
        "SELECT username, display_name, is_admin, status FROM users ORDER BY username")]
    try:
        logs = [dict(r) for r in auth.execute(
            "SELECT username, client_tag, origin_no, source, actor, created_at "
            "  FROM naver_register_log WHERE TRIM(COALESCE(origin_no,''))<>'' ORDER BY id")]
    except sqlite3.Error:
        print("naver_register_log 테이블이 없습니다 — 기록이 한 건도 없음")
        logs = []

    keys, limits, store_pnos = {}, {}, {}
    for u in users:
        p = os.path.join(DATA, f"{u['username']}.db")
        if not os.path.exists(p):
            continue
        try:
            c = ro(p)
        except sqlite3.Error:
            continue
        keys[u['username']] = setting(c, 'api_client_id')
        limits[u['username']] = setting(c, 'naver_register_limit')
        try:
            store_pnos[u['username']] = {str(r[0]).strip() for r in c.execute(
                "SELECT naver_origin_pno FROM products "
                " WHERE TRIM(COALESCE(naver_origin_pno,''))<>''")}
        except sqlite3.Error:
            store_pnos[u['username']] = set()
        c.close()

    # 2) 같은 키를 여러 계정이
    by_key = defaultdict(list)
    for un, k in keys.items():
        if k:
            by_key[k].append(un)
    dup = {k: v for k, v in by_key.items() if len(v) > 1}
    print("=" * 70)
    print("② 같은 네이버 API 키를 쓰는 계정")
    if not dup:
        print("   없음")
    for k, v in dup.items():
        print(f"   ⚠️ 키 {k[:10]}… → {', '.join(v)}  "
              f"(기록·한도는 이 중 '{v[-1]}' 한 계정에만 잡힙니다)")

    per = defaultdict(list)
    for l in logs:
        per[str(l['username'] or '')].append(l)

    print("=" * 70)
    print("① 사용자별 수량  (한도 대상 = 카페24 대행 제외, 같은 상품 재등록은 1건)")
    print(f"   {'사용자':<16}{'한도':>8}{'한도대상':>9}{'누적':>7}  경로별 / 점검")
    names = {u['username']: u for u in users}
    for un in sorted(set(per) | set(keys)):
        if only and un != only:
            continue
        rows = per.get(un, [])
        seen, first_src = set(), Counter()
        counted = set()
        for l in rows:
            o = l['origin_no']
            if o not in seen:
                seen.add(o)
                first_src[l['source'] or ''] += 1
            if (l['source'] or '') not in EXEMPT:
                counted.add(o)
        lim = limits.get(un, '') or '0'
        notes = []
        # 3) 지금 키와 다른 토큰으로 기록된 건
        cur = (keys.get(un) or '')[:10]
        other_tag = Counter(l['client_tag'] for l in rows
                            if l['client_tag'] and cur and l['client_tag'] != cur)
        if other_tag:
            notes.append("다른 키로 기록 " + ", ".join(f"{t}…:{n}" for t, n in other_tag.items()))
        # 4) 상품DB엔 있는데 기록에 없는 원상품번호
        miss = store_pnos.get(un, set()) - seen
        if miss:
            notes.append(f"상품DB엔 있는데 기록 없음 {len(miss)}건")
        if un and un not in names:
            notes.append("계정 없음(삭제?)")
        if not un:
            continue
        src = " ".join(f"{k or '기타'}:{v}" for k, v in first_src.items())
        print(f"   {un:<16}{lim:>8}{len(counted):>9}{len(seen):>7}  {src}"
              + (("\n" + " " * 43 + " ⚠️ " + " / ".join(notes)) if notes else ""))

    # 5) 미매칭
    um = per.get('', [])
    print("=" * 70)
    print("⑤ 주인을 못 찾은 기록(미매칭 토큰)")
    if not um:
        print("   없음")
    else:
        for t, n in Counter(l['client_tag'] for l in um).most_common():
            owner = [u for u, k in keys.items() if k and k[:10] == t]
            print(f"   토큰 {t or '(빈값)'}… {n}건  → 지금 이 키를 가진 계정: "
                  f"{', '.join(owner) or '없음'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else '')
