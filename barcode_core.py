"""바코드 규칙 한곳 — 코스트코 상품번호를 중심으로 바코드 두 종류를 나눠 둔다.

🏷 매장 바코드 (store) — 매대 가격표의 바코드. 상품당 1개, shared_products.barcode.
   가격 기록·변동 추이에만 쓴다. 가격표 사진·매장 가격 확인에서 들어온다.
   생김새: 상품번호를 앞 0으로 채우고 끝에 한 자리를 붙인다 (859695 → 08596950).
📦 상품 바코드 (box) — 포장지·박스의 제조사 바코드(880… 등). 상품당 여러 개,
   product_barcodes(kind='box'). 재고(창고 입고 확인·출고)와 네이버 판매자 바코드에 쓴다.
   바코드마다 units = '한 번 찍으면 소분 몇 개'(낱개 1, 겉박스 = 분할수량).

종류는 '어느 화면에서 찍었나'로 정한다(가격 화면 = 매장, 창고 화면 = 상품).
숫자 규칙(is_store)은 창고 화면에서 가격표를 잘못 찍은 경우를 걸러 내는 안전장치다.
"""
from datetime import datetime

from db_core import get_auth_db, retry_on_lock


def digits(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit())


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_barcodes (
            barcode    TEXT PRIMARY KEY,
            product_no TEXT NOT NULL,
            kind       TEXT DEFAULT 'box',   -- box(상품 포장). 예전 label 줄은 점검 화면에서 정리
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )""")
    try:
        conn.execute("ALTER TABLE product_barcodes ADD COLUMN units INTEGER DEFAULT 1")
    except Exception:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pbc_pno ON product_barcodes(product_no)")


def pno_candidates(c):
    """매장 바코드 → 상품번호 후보(순서대로). 4~7자리만 — 상품번호 길이."""
    out = []
    for s in (c[:-1], c.lstrip('0')[:-1], c.lstrip('0'), c[1:-1]):
        s = s.lstrip('0')
        if 4 <= len(s) <= 7 and s != c and s not in out:
            out.append(s)
    return out


def gtin_ok(code):
    """국제 상품 바코드(EAN-8/UPC-A/EAN-13/GTIN-14) 끝자리 검증."""
    c = digits(code)
    if len(c) not in (8, 12, 13, 14) or c != str(code or '').strip():
        return False
    body, chk = c[:-1], int(c[-1])
    s = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - s % 10) % 10 == chk


def is_store(code, product_no=''):
    """매장(가격표) 바코드로 보이나. product_no를 주면 그 상품의 매장 바코드인지 본다."""
    c, p = digits(code), digits(product_no)
    if not c:
        return False
    if p:
        return c == p or p in pno_candidates(c)
    from db_products import find_shared_by_code
    sp = find_shared_by_code(c)
    if sp and (digits(sp.get('barcode')) == c or digits(sp.get('product_no')) == c):
        return True
    for cand in pno_candidates(c):
        if len(c) <= 9:          # 매장 바코드는 8자리 안팎 — 13자리 880…을 잘못 걸지 않게
            sp = find_shared_by_code(cand)
            if sp and digits(sp.get('product_no')) == cand:
                return True
    return False


def set_store_barcode(product_no, code, username='', overwrite=False):
    """상품의 매장 바코드 칸을 채운다(비어 있을 때만, overwrite면 덮어씀)."""
    from db_products import find_shared_by_code, set_shared_barcode
    p, c = digits(product_no), digits(code)
    sp = find_shared_by_code(p) if p else None
    if not sp or not c or c == p:
        return False
    if digits(sp.get('barcode')) and not overwrite:
        return False
    return set_shared_barcode(p, c, username)


# ── 📦 상품 바코드 ───────────────────────────────────────────────

def boxes_of(product_no):
    """→ [{barcode, units, created_by, created_at}] (상품 포장 바코드만)."""
    conn = get_auth_db()
    try:
        ensure(conn)
        rows = conn.execute(
            "SELECT barcode, COALESCE(units,1), created_by, created_at FROM product_barcodes "
            " WHERE product_no=? AND COALESCE(kind,'box')='box' ORDER BY created_at",
            (digits(product_no),)).fetchall()
    finally:
        conn.close()
    return [{'barcode': str(b), 'units': int(u or 1), 'created_by': str(by or ''),
             'created_at': str(at or '')} for b, u, by, at in rows]


def resolve_box(code):
    """상품 바코드 → {barcode, product_no, units} | None. 매장 바코드 줄은 돌려주지 않는다."""
    c = digits(code)
    if not c:
        return None
    conn = get_auth_db()
    try:
        ensure(conn)
        r = conn.execute("SELECT product_no, COALESCE(units,1) FROM product_barcodes "
                         " WHERE barcode=? AND COALESCE(kind,'box')='box'", (c,)).fetchone()
    finally:
        conn.close()
    return {'barcode': c, 'product_no': str(r[0]), 'units': int(r[1] or 1)} if r else None


def _owner_of(c):
    conn = get_auth_db()
    try:
        ensure(conn)
        r = conn.execute("SELECT product_no, COALESCE(kind,'box') FROM product_barcodes "
                         " WHERE barcode=?", (c,)).fetchone()
    finally:
        conn.close()
    return (str(r[0]), str(r[1])) if r else ('', '')


def link_box(code, product_no, username, is_admin=False, units=1):
    """상품 포장 바코드를 상품번호에 연결. 반환 {'linked', 'product_no', 'prev', 'units'}.

    - 매장 가격표 바코드면 거부(ValueError) — 재고에는 상품 포장 바코드만 쓴다.
    - 이미 다른 상품에 붙은 바코드를 바꾸는 건 관리자만(PermissionError).
    """
    from db_products import find_shared_by_code
    c, p = digits(code), digits(product_no)
    units = max(1, int(units or 1))
    if not c or not p:
        raise ValueError("바코드와 상품번호가 필요합니다")
    if not find_shared_by_code(p):
        raise ValueError(f"제품 DB에 상품번호 {p}가 없습니다")
    if is_store(c, p) or is_store(c):
        raise ValueError("매장 가격표 바코드입니다 — 상품 포장에 인쇄된 바코드를 찍어 주세요")
    prev, kind = _owner_of(c)
    if prev == p and kind == 'box':
        cur = resolve_box(c) or {}
        if int(cur.get('units') or 1) == units:
            return {'linked': False, 'product_no': p, 'prev': prev, 'units': units}
    elif prev and prev != p and not is_admin:
        raise PermissionError(f"이 바코드는 이미 상품번호 {prev}에 연결돼 있습니다 — 관리자에게 바꿔 달라고 하세요")

    def _w():
        conn = get_auth_db()
        try:
            ensure(conn)
            conn.execute(
                "INSERT INTO product_barcodes (barcode, product_no, kind, units, created_by, created_at) "
                "VALUES (?,?,'box',?,?,?) ON CONFLICT(barcode) DO UPDATE SET "
                "product_no=excluded.product_no, kind='box', units=excluded.units, "
                "created_by=excluded.created_by, created_at=excluded.created_at",
                (c, p, units, str(username or ''), _now()))
            conn.commit()
        finally:
            conn.close()
    retry_on_lock(_w)
    return {'linked': True, 'product_no': p, 'prev': prev, 'units': units}


def unlink_box(code, username, is_admin=False):
    if not is_admin:
        raise PermissionError("바코드 연결 해제는 관리자만 할 수 있습니다")
    c = digits(code)

    def _w():
        conn = get_auth_db()
        try:
            ensure(conn)
            conn.execute("DELETE FROM product_barcodes WHERE barcode=?", (c,))
            conn.commit()
        finally:
            conn.close()
    retry_on_lock(_w)
    return True


def naver_barcode(product_no, passed=''):
    """네이버 판매자 바코드 — 상품 포장 바코드만. 매장 바코드는 절대 보내지 않는다.

    넘겨받은 값이 매장 바코드가 아니면 그 값, 아니면 연결된 상품 바코드(낱개 우선).
    """
    p, b = digits(product_no), digits(passed)
    if b and not (is_store(b, p) if p else is_store(b)):
        sbc = ''
        if p:
            try:
                from db_products import find_shared_by_code
                sbc = digits((find_shared_by_code(p) or {}).get('barcode'))
            except Exception:
                sbc = ''
        if b != sbc:                      # 상품 DB에 매장 바코드로 저장된 값이면 보내지 않음
            return b
    if not p:
        return ''
    try:
        bx = boxes_of(p)
    except Exception:
        return ''
    bx.sort(key=lambda x: (x['units'] != 1, x['created_at']))
    return bx[0]['barcode'] if bx else ''


# ── 기존 데이터 점검 (읽기 전용) + 정리 ────────────────────────────

def audit():
    """섞여 들어간 바코드 목록. 고치지 않는다.

    store_suspect: 상품 DB 매장 바코드 칸인데 매장 바코드 모양이 아닌 값
      why='gtin'  → 상품 포장 바코드로 보임 (권장: 상품 바코드로 옮기기)
      why='odd'   → 판독 오류일 수 있음 (권장: 확인 후 지우기 또는 그대로)
    label_rows:   product_barcodes에 들어간 매장 바코드 줄(예전 스캔 자동연결)
                  (권장: 매장 바코드 칸으로 옮기기)
    """
    conn = get_auth_db()
    try:
        ensure(conn)
        sp = conn.execute(
            "SELECT product_no, COALESCE(costco_name,''), TRIM(COALESCE(barcode,'')) "
            "  FROM shared_products WHERE TRIM(COALESCE(barcode,''))<>''").fetchall()
        pb = conn.execute(
            "SELECT b.barcode, b.product_no, COALESCE(b.kind,'box'), COALESCE(b.units,1), "
            "       COALESCE(s.costco_name,''), TRIM(COALESCE(s.barcode,'')) "
            "  FROM product_barcodes b LEFT JOIN shared_products s ON s.product_no=b.product_no"
        ).fetchall()
    finally:
        conn.close()
    store_suspect, label_rows = [], []
    for pno, name, bc in sp:
        c = digits(bc)
        if not c or is_store(c, pno):
            continue
        store_suspect.append({'product_no': str(pno), 'name': str(name), 'barcode': c,
                              'why': 'gtin' if gtin_ok(c) and len(c) >= 12 else 'odd'})
    for bc, pno, kind, units, name, sbc in pb:
        if kind == 'label' or is_store(bc, pno):
            label_rows.append({'product_no': str(pno), 'name': str(name), 'barcode': str(bc),
                               'kind': str(kind), 'store_barcode': str(sbc or '')})
    return {'store_suspect': store_suspect, 'label_rows': label_rows,
            'box_total': sum(1 for r in pb if r[2] == 'box' and not is_store(r[0], r[1]))}


def audit_fix(username, items):
    """관리자가 고른 정리. items: [{action, product_no, barcode}]
      to_box     매장 칸 값 → 상품 바코드로 옮기고 매장 칸 비움
      clear      매장 칸 비움
      to_store   product_barcodes의 매장 바코드 줄 → 매장 칸(비어 있으면)으로 옮기고 줄 삭제
    반환 {done, skipped[]}"""
    done, skipped = 0, []
    for it in items or []:
        a, p, c = str(it.get('action') or ''), digits(it.get('product_no')), digits(it.get('barcode'))
        if not (p and c):
            continue
        try:
            if a in ('to_box', 'clear'):
                def _w(p=p, c=c, a=a):
                    conn = get_auth_db()
                    try:
                        ensure(conn)
                        cur = conn.execute("SELECT TRIM(COALESCE(barcode,'')) FROM shared_products "
                                           " WHERE product_no=?", (p,)).fetchone()
                        if not cur or digits(cur[0]) != c:
                            return False
                        if a == 'to_box':
                            row = conn.execute("SELECT product_no FROM product_barcodes WHERE barcode=?",
                                               (c,)).fetchone()
                            if row and str(row[0]) != p:
                                raise ValueError(f"{c}는 이미 {row[0]}에 연결돼 있음")
                            conn.execute(
                                "INSERT INTO product_barcodes (barcode, product_no, kind, units, created_by, "
                                "created_at) VALUES (?,?,'box',1,?,?) ON CONFLICT(barcode) DO UPDATE SET "
                                "kind='box'", (c, p, username, _now()))
                        conn.execute("UPDATE shared_products SET barcode='', updated_at=?, updated_by=? "
                                     " WHERE product_no=?",
                                     (datetime.now().strftime("%Y-%m-%d %H:%M"), username, p))
                        conn.commit()
                        return True
                    finally:
                        conn.close()
                ok = retry_on_lock(_w)
            elif a == 'to_store':
                set_store_barcode(p, c, username)

                def _w2(c=c, p=p):
                    conn = get_auth_db()
                    try:
                        conn.execute("DELETE FROM product_barcodes WHERE barcode=? AND product_no=?", (c, p))
                        conn.commit()
                        return True
                    finally:
                        conn.close()
                ok = retry_on_lock(_w2)
            else:
                ok = False
            if ok:
                done += 1
            else:
                skipped.append({'barcode': c, 'why': '이미 바뀜 또는 알 수 없는 작업'})
        except Exception as e:
            skipped.append({'barcode': c, 'why': str(e)})
    return {'done': done, 'skipped': skipped}
