"""직접구매자 영수증 — 관리자 영수증(receipt_items)과 **따로** 저장한다 (auth.db).

왜 따로인가:
  receipt_items는 관리자가 매장에서 산 물건의 장부다. 공용 가용재고
  (build_stock_pool)·재고 현황·미정산 날짜·영수증 정산 '불러오기'가 전부 이
  테이블을 통째로 읽는다. 직접구매자가 자기 돈으로 산 영수증이 여기 섞이면
    · 그 물건이 관리자 창고 재고로 잡혀 남의 주문을 메꿨다고 계산되고
    · 관리자가 그날 영수증을 불러와 저장하면 관리자 명의로 한 번 더 들어간다.
  테이블을 나누면 관리자 쪽 코드는 한 줄도 몰라도 된다.

직접구매자 정산 기록은 settle_item에 source='self'로 남고, 재고 lot은
memo '직접구매영수증 …'으로 들어간다 — 둘 다 공용 재고 계산에서 빠진다
(receipt_settle.build_stock_pool / get_stock_status / receipt_lot_units).
"""
from datetime import datetime

from db_core import get_auth_db

#: 재고 lot memo 머리말 — 관리자 영수증 배정('영수증정산')과 겹치면 안 된다.
LOT_MEMO_PREFIX = '직접구매영수증'


def _conn():
    conn = get_auth_db(row=True)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS self_receipt_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT NOT NULL,
            receipt_date TEXT NOT NULL,
            product_no   TEXT DEFAULT '',
            product_name TEXT NOT NULL,
            qty          INTEGER DEFAULT 1,
            unit_price   INTEGER DEFAULT 0,
            discount     INTEGER DEFAULT 0,
            list_price   INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT '',
            UNIQUE(username, receipt_date, product_no, product_name)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sri_user_date "
                 "ON self_receipt_items(username, receipt_date)")
    return conn


def save_items(username, items):
    """그 사람의 그 날짜 영수증을 **통째로 교체**한다. 반환: (저장, 지운 옛 행)

    재업로드 = 그날 영수증을 다시 쓰는 일이다. 잘못 읽힌 옛 줄이 남으면
    고쳐 올려도 그 수량이 재고에 계속 남는다(관리자 영수증과 같은 이유).
    """
    _u = str(username or '').strip()
    rows = {}
    for it in (items or []):
        rd = str(it.get('receipt_date') or '').strip()
        nm = str(it.get('상품명') or '').strip()
        if not (_u and rd and nm):
            continue
        price = int(it.get('단가') or 0)
        rows[(rd, str(it.get('상품번호') or '').strip(), nm)] = (
            max(1, int(it.get('수량') or 1)), price, int(it.get('할인') or 0),
            int(it.get('정가단가') or 0) or price)
    if not rows:
        return 0, 0
    dates = sorted({k[0] for k in rows})
    ph = ",".join("?" * len(dates))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        removed = conn.execute(
            "DELETE FROM self_receipt_items WHERE username=? AND receipt_date IN (%s)" % ph,
            [_u] + dates).rowcount
        for (rd, pno, nm), (qty, price, disc, listp) in rows.items():
            conn.execute(
                "INSERT OR REPLACE INTO self_receipt_items (username, receipt_date, "
                "product_no, product_name, qty, unit_price, discount, list_price, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_u, rd, pno, nm, qty, price, disc, listp, now))
        conn.commit()
    finally:
        conn.close()
    return len(rows), max(0, removed - len(rows))


def update_latest_prices(username, items):
    """영수증 정가로 공유 가격DB(매장가)를 갱신한다 — **더 최근 영수증일 때만.**

    영수증은 누가 올렸든 코스트코가 찍어 준 그날 가격이다. 직접구매자 영수증이
    관리자 것보다 최근이면 그게 최신가다. 반대로 지난주 영수증을 늦게 올렸다고
    어제 가격을 되돌리면 안 된다 — 그래서 가격DB의 매장가 날짜와 비교한다.
    (upsert_shared_store_price에도 날짜 가드가 있지만, 가드에 걸려도 상품명·
     수정자를 덮고 가격 이력에 한 줄을 남긴다. 여기서 먼저 거른다.)

    쿠폰가가 아니라 **정가**를 넣는다 — 일회성 할인을 표준가로 굳히지 않는다.
    반환: {'new': n, 'changed': n, 'same': n, 'older': n, 'skip': n}
    """
    from db_products import upsert_shared_store_price
    res = {'new': 0, 'changed': 0, 'same': 0, 'older': 0, 'skip': 0}
    conn = get_auth_db(row=True)
    try:
        cur, img = {}, {}
        for r in conn.execute("SELECT product_no, COALESCE(store_updated_at,'') d, "
                              "COALESCE(image_url,'') i FROM shared_products "
                              "WHERE TRIM(COALESCE(product_no,''))<>''"):
            _k = str(r['product_no']).strip()
            cur[_k] = str(r['d'] or '')[:10]
            img[_k] = str(r['i'] or '')
    finally:
        conn.close()
    for it in (items or []):
        pno = str(it.get('상품번호') or '').strip()
        nm = str(it.get('상품명') or '').strip()
        rd = str(it.get('receipt_date') or '').strip()[:10]
        price = int(it.get('정가단가') or 0) or int(it.get('단가') or 0)
        if not (pno and nm and rd and price > 0):
            res['skip'] += 1
            continue
        if cur.get(pno) and rd < cur[pno]:
            res['older'] += 1
            continue
        try:
            _r = upsert_shared_store_price(
                costco_name=nm, keyword=nm, price=price, product_no=pno,
                updated_by=str(username), receipt_date=rd, force_store=False,
                # image_url을 안 넘기면 ''로 UPDATE돼 상품 이미지가 지워진다
                image_url=img.get(pno, ''), source='self-receipt')
            res[str((_r or {}).get('status') or 'same')] = \
                res.get(str((_r or {}).get('status') or 'same'), 0) + 1
            cur[pno] = rd
        except Exception:
            res['skip'] += 1
    return res


def dates(username, limit=60):
    """그 사람이 영수증을 올린 날짜들(최신순) — [(날짜, 품목수)]."""
    conn = _conn()
    try:
        return [(r['receipt_date'], r['c']) for r in conn.execute(
            "SELECT receipt_date, COUNT(*) c FROM self_receipt_items WHERE username=? "
            "GROUP BY receipt_date ORDER BY receipt_date DESC LIMIT ?",
            (str(username), int(limit)))]
    finally:
        conn.close()


def items_by_date(username, receipt_date):
    """그 날짜 품목 — 화면 표 모양 그대로."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM self_receipt_items WHERE username=? AND receipt_date=? ORDER BY id",
            (str(username), str(receipt_date))).fetchall()
    finally:
        conn.close()
    return [{'상품번호': r['product_no'] or '', '상품명': r['product_name'] or '',
             '수량': int(r['qty'] or 1), '단가': int(r['unit_price'] or 0),
             '할인': int(r['discount'] or 0),
             '정가단가': int(r['list_price'] or 0) or int(r['unit_price'] or 0),
             'receipt_date': r['receipt_date'] or ''} for r in rows]
