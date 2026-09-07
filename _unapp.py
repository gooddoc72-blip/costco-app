# -*- coding: utf-8 -*-
"""재고 화면이 '왜 재고로 잡혀 있는지'를 말하게 한다.

현재 구입재고 = 영수증 입고 − **정산에 적용된** 사용량이다.
미리보기에서 매칭만 하고 '정산 적용'을 누르지 않으면 사용량이 0이라
산 것이 전부 재고로 남는다. 화면에는 '사용 0'만 보여서 매칭이 안 된 것처럼
읽힌다 — 실제로는 매칭은 됐고 저장이 안 된 것이다.
그 차이를 화면이 직접 말해 줘야 한다.
"""
import io

p = 'receipt_settle.py'
s = io.open(p, encoding='utf-8').read()
anchor = "def get_stock_status(date_upto=None):"
assert anchor in s, 'anchor'

add = '''def unapplied_receipt_dates(date_upto=None, days=45):
    """영수증은 올렸는데 정산이 적용되지 않은 날짜들. [(날짜, 품목수), ...]

    '정산 적용'을 누르지 않으면 receipt_settle_items에 아무것도 안 남고,
    그날 산 것이 통째로 재고로 잡힌다. 재고가 실제보다 부풀어 보이는 원인 1위다.
    """
    import glob
    from datetime import datetime as _dt, timedelta as _td

    _upto = str(date_upto or _dt.now().strftime("%Y-%m-%d"))
    _from = (_dt.strptime(_upto, "%Y-%m-%d") - _td(days=int(days))).strftime("%Y-%m-%d")
    _start = get_settle_start_date()
    if _start and _start > _from:
        _from = _start

    _rc = {}
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "*.db"))):
        u = os.path.basename(f)[:-3]
        if u == "auth" or ".bak" in u or ".backup" in u:
            continue
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % f, uri=True)
            for d, n in conn.execute(
                    "SELECT receipt_date, COUNT(*) FROM receipt_items "
                    "WHERE receipt_date BETWEEN ? AND ? GROUP BY receipt_date",
                    (_from, _upto)):
                _rc[_norm(d)] = _rc.get(_norm(d), 0) + int(n or 0)
            conn.close()
        except Exception:
            continue
    if not _rc:
        return []

    # 출고 기준 정산은 주문일이 영수증일과 하루쯤 어긋난다(마감 후 주문이
    # 다음날 출고). 그래서 ±1일 안에 적용 기록이 있으면 적용된 것으로 본다.
    _sd = set()
    try:
        from db_receipt_settle import _conn as _rs_conn, _ensure as _rs_ensure
        from datetime import timedelta as _td2
        c = _rs_conn(); _rs_ensure(c)
        for (d,) in c.execute(
                "SELECT DISTINCT order_date FROM receipt_settle_items "
                "WHERE order_date BETWEEN ? AND ?",
                ((_dt.strptime(_from, "%Y-%m-%d") - _td2(days=2)).strftime("%Y-%m-%d"),
                 (_dt.strptime(_upto, "%Y-%m-%d") + _td2(days=2)).strftime("%Y-%m-%d"))):
            _s = _norm(d)
            if not _s:
                continue
            try:
                _b = _dt.strptime(_s, "%Y-%m-%d")
            except ValueError:
                continue
            for _k in (-1, 0, 1):
                _sd.add((_b + _td2(days=_k)).strftime("%Y-%m-%d"))
        c.close()
    except Exception:
        return []

    return sorted(((d, n) for d, n in _rc.items() if d and d not in _sd), reverse=True)


''' + anchor
s = s.replace(anchor, add, 1)
io.open(p, 'w', encoding='utf-8', newline='').write(s)
print('receipt_settle: unapplied_receipt_dates 추가')

# ── 화면 ──────────────────────────────────────────────────
p = 'pages_lib/receipt_settle_page.py'
s = io.open(p, encoding='utf-8').read()
old = """    _left = [r for r in rows if r['units_left'] > 0]"""
new = """    # 재고가 부풀어 보이는 원인 1위 — 미리보기에서 매칭만 하고 '정산 적용'을
    # 누르지 않으면 사용량이 0이라 산 것이 통째로 재고로 남는다.
    try:
        _unap = _rs.unapplied_receipt_dates()
    except Exception:
        _unap = []
    if _unap:
        st.error(
            "🚨 **정산이 적용되지 않은 영수증이 있습니다** — 그날 산 것이 통째로 재고로 "
            "잡혀 있습니다.\n\n"
            + "\n".join(f"- **{d}** 영수증 {n}종 — 정산 적용 0건" for d, n in _unap[:6])
            + "\n\n미리보기에서 매칭만 하고 **'정산 적용'을 누르지 않으면** 사용량이 "
              "0으로 남습니다. 위 날짜로 영수증 정산을 다시 열어 매칭 후 **정산 적용**까지 "
              "누르세요. 그러면 이 재고에서 빠집니다.")

    _left = [r for r in rows if r['units_left'] > 0]"""
assert old in s, 'stock anchor'
s = s.replace(old, new, 1)
io.open(p, 'w', encoding='utf-8', newline='').write(s)
print('receipt_settle_page: 미적용 경고 추가')
