"""
주문 / 발송 이력 레이어
daily_orders (수익 계산용) + order_history (발송 추적용)
"""
import sqlite3
from datetime import datetime

from db_core import get_user_db


# ── 일별 주문 (수익 계산용) ──────────────────────────────

def save_daily_orders(username, order_date, orders_df, shipping_cost, box_cost):
    """주문 데이터를 daily_orders에 저장.
    각 행의 '결제일' 컬럼이 있으면 실제 결제일자로 저장(분산),
    없거나 비어있는 행은 인자로 받은 default order_date 사용.
    DELETE는 영향받는 모든 날짜에 대해 실행.
    """
    import hashlib as _hl
    import pandas as pd
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _row_order_date(r):
        for col in ('결제일', '주문일시', '주문일', 'order_date'):
            v = r.get(col) if hasattr(r, 'get') else None
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            s = str(v).strip()
            if not s:
                continue
            if 'T' in s:
                s = s.split('T', 1)[0]
            elif ' ' in s:
                s = s.split(' ', 1)[0]
            if len(s) >= 10 and s[4] == '-' and s[7] == '-':
                return s[:10]
        return order_date

    affected_dates = set()
    for _, r in orders_df.iterrows():
        affected_dates.add(_row_order_date(r))
    for d in affected_dates:
        conn.execute("DELETE FROM daily_orders WHERE order_date=?", (d,))

    for _, r in orders_df.iterrows():
        row_date = _row_order_date(r)
        cost = r.get('구입가격', 0) or 0
        ship_fee = int(r['배송비 합계'])
        settlement = int(r['정산예정금액'])
        profit = (settlement + ship_fee) - (int(cost) + shipping_cost + box_cost)
        p_no = r.get('상품번호', '')
        conn.execute("""INSERT INTO daily_orders
            (order_date,recipient,product_name,product_no,option_info,qty,
             order_amount,shipping_fee,extra_shipping,settlement,
             cost_price,delivery_cost,box_cost,profit,matched,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row_date, r['수취인명'], r['상품명'], str(p_no), r.get('옵션정보', ''),
             int(r['수량']), int(r['최종 상품별 총 주문금액']), ship_fee,
             int(r.get('제주/도서 추가배송비', 0)), settlement,
             int(cost), shipping_cost, box_cost, profit, 1 if cost > 0 else 0, now))
    conn.commit()
    conn.close()


def get_daily_orders(username, order_date):
    conn = get_user_db(username)
    rows = conn.execute(
        "SELECT * FROM daily_orders WHERE order_date=? ORDER BY product_name", (order_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recalc_daily_orders_for_products(username, product_nos):
    """영수증 업로드 등으로 products.unit_price가 갱신된 상품번호에 대해
    저장된 daily_orders의 cost_price·profit을 현재 매입가로 재계산."""
    if not product_nos:
        return 0
    pnos = [str(p).strip() for p in product_nos if p and str(p).strip()]
    if not pnos:
        return 0

    conn = get_user_db(username)
    try:
        s_row = conn.execute("SELECT value FROM settings WHERE key='shipping_cost'").fetchone()
        b_row = conn.execute("SELECT value FROM settings WHERE key='box_cost'").fetchone()
        shipping_cost = int(s_row[0]) if s_row and s_row[0] else 1800
        box_cost = int(b_row[0]) if b_row and b_row[0] else 300
    except Exception:
        shipping_cost, box_cost = 1800, 300

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cnt = 0
    for pno in pnos:
        p_row = conn.execute(
            "SELECT unit_price, split_qty FROM products "
            "WHERE product_no=? AND unit_price>0 ORDER BY updated_at DESC LIMIT 1",
            (pno,)
        ).fetchone()
        if not p_row:
            continue
        unit_price = int(p_row[0] or 0)
        split_qty  = max(1, int(p_row[1] or 1))
        if unit_price <= 0:
            continue

        # services.calc_cost와 동일 공식 사용 (지연 import: 순환 참조 회피)
        from services import calc_cost as _calc_cost
        _product = {'unit_price': unit_price, 'split_qty': split_qty}

        do_rows = conn.execute(
            "SELECT id, qty, settlement, shipping_fee, delivery_cost, box_cost "
            "FROM daily_orders WHERE product_no=?",
            (pno,)
        ).fetchall()
        for r in do_rows:
            qty        = int(r[1] or 1)
            settlement = int(r[2] or 0)
            ship_fee   = int(r[3] or 0)
            d_cost     = int(r[4] or shipping_cost)
            b_cost     = int(r[5] or box_cost)
            new_cost   = _calc_cost(_product, qty)
            new_profit = (settlement + ship_fee) - (new_cost + d_cost + b_cost)
            conn.execute(
                "UPDATE daily_orders SET cost_price=?, profit=?, matched=1, created_at=? WHERE id=?",
                (new_cost, new_profit, now, r[0])
            )
            cnt += 1
    conn.commit()
    conn.close()
    return cnt


def get_saved_dates(username):
    conn = get_user_db(username)
    rows = conn.execute(
        "SELECT DISTINCT order_date FROM daily_orders ORDER BY order_date DESC"
    ).fetchall()
    conn.close()
    return [r['order_date'] for r in rows]


# ── 주문 이력 (발송 추적용) ──────────────────────────────

def save_order_history(username, full_df, cost_df=None):
    import hashlib as _hl
    import pandas as pd
    if full_df is None or full_df.empty:
        return 0
    conn = get_user_db(username)
    s_cost = b_cost = 0
    try:
        s_cost = int(conn.execute("SELECT value FROM settings WHERE key='shipping_cost'").fetchone()['value'])
        b_cost = int(conn.execute("SELECT value FROM settings WHERE key='box_cost'").fetchone()['value'])
    except Exception:
        pass
    cost_map = {}
    if cost_df is not None and '구입가격' in cost_df.columns:
        for _, cr in cost_df.iterrows():
            key = (str(cr.get('상품명', '')), str(cr.get('수취인명', '')))
            cost_map[key] = int(cr.get('구입가격', 0) or 0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    saved = 0
    for _, r in full_df.iterrows():
        order_no = str(r.get('상품주문번호', '') or '').strip()
        if not order_no:
            raw = f"{r.get('결제일','')}{r.get('수취인명','')}{r.get('상품명','')}{r.get('수량','')}"
            order_no = f"H{_hl.md5(raw.encode()).hexdigest()[:14]}"
        order_date = str(r.get('결제일', '') or r.get('주문일', '') or now[:10])
        if 'T' in order_date:
            order_date = order_date[:10]
        qty    = int(pd.to_numeric(r.get('수량', 1), errors='coerce') or 1)
        total  = int(pd.to_numeric(r.get('최종 상품별 총 주문금액', 0), errors='coerce') or 0)
        ship   = int(pd.to_numeric(r.get('배송비 합계', 0), errors='coerce') or 0)
        settle = int(pd.to_numeric(r.get('정산예정금액', 0), errors='coerce') or 0)
        unit_p = int(pd.to_numeric(r.get('상품가격', 0), errors='coerce') or 0) if '상품가격' in r.index else total // max(qty, 1)
        cost_key   = (str(r.get('상품명', '')), str(r.get('수취인명', '')))
        cost_price = cost_map.get(cost_key, 0)
        profit = (settle + ship) - (cost_price + s_cost + b_cost) if cost_price > 0 else 0
        try:
            import json as _json
            _raw_dict = {k: ('' if pd.isna(v) else (int(v) if hasattr(v, 'item') else v))
                         for k, v in r.to_dict().items()}
            raw_json_str = _json.dumps(_raw_dict, default=str, ensure_ascii=False)
        except Exception:
            raw_json_str = ''
        try:
            conn.execute("""INSERT INTO order_history
                (order_no, order_group_no, order_date, recipient, buyer,
                 product_name, product_no, option_info, qty, unit_price,
                 order_amount, shipping_fee, settlement, status,
                 tracking_no, courier, cost_price, profit, created_at, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(order_no) DO UPDATE SET
                    status       = excluded.status,
                    tracking_no  = COALESCE(NULLIF(excluded.tracking_no, ''), order_history.tracking_no),
                    courier      = COALESCE(NULLIF(excluded.courier, ''),     order_history.courier),
                    settlement   = excluded.settlement,
                    shipping_fee = excluded.shipping_fee,
                    cost_price   = CASE WHEN excluded.cost_price > 0 THEN excluded.cost_price ELSE order_history.cost_price END,
                    profit       = CASE WHEN excluded.profit != 0     THEN excluded.profit     ELSE order_history.profit END,
                    raw_json     = COALESCE(NULLIF(excluded.raw_json, ''), order_history.raw_json)
                """,
                (order_no, str(r.get('주문번호', '') or ''), order_date,
                 str(r.get('수취인명', '') or ''), str(r.get('구매자명', '') or ''),
                 str(r.get('상품명', '') or ''), str(r.get('상품번호', '') or ''),
                 str(r.get('옵션정보', '') or ''), qty, unit_p, total, ship, settle,
                 str(r.get('주문상태', '') or ''), str(r.get('송장번호', '') or ''),
                 str(r.get('택배사', '') or ''), cost_price, profit, now, raw_json_str))
            saved += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return saved


ACTIVE_ORDER_STATUSES = (
    "PAYED", "INSTRUCT", "PRODUCT_READY",
    "결제완료", "발주확인", "발송대기",
)


def get_active_orders(username):
    conn = get_user_db(username)
    placeholders = ",".join("?" * len(ACTIVE_ORDER_STATUSES))
    rows = conn.execute(
        f"""SELECT * FROM order_history
            WHERE status IN ({placeholders})
              AND COALESCE(tracking_no, '') = ''
            ORDER BY order_date DESC, id DESC""",
        ACTIVE_ORDER_STATUSES,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


_NAVER_EXCEL_COLUMNS = [
    "상품주문번호", "주문번호", "배송속성", "풀필먼트사(주문 기준)", "택배사(주문 기준)", "배송방법(구매자 요청)",
    "배송방법", "택배사", "송장번호", "발송일", "판매채널", "구매자명", "구매자ID", "수취인명", "주문상태",
    "주문세부상태", "수량클레임 여부", "결제위치", "결제일", "상품번호", "상품명", "상품종류", "반품안심케어",
    "멤버십N배송", "옵션정보", "옵션관리코드", "수량", "옵션가격", "상품가격", "최종 상품별 할인액",
    "최초 상품별 할인액", "판매자 부담 할인액", "최종 상품별 총 주문금액", "최초 상품별 총 주문금액", "사은품",
    "발주확인일", "발송기한", "발송처리일", "송장출력일", "배송비 형태", "배송비 묶음번호", "배송비 유형",
    "배송비 합계", "제주/도서 추가배송비", "배송비 할인액", "판매자 상품코드", "판매자 내부코드1", "판매자 내부코드2",
    "수취인연락처1", "수취인연락처2", "통합배송지", "기본배송지", "상세배송지", "구매자연락처", "우편번호", "배송메세지",
    "출고지", "결제수단", "네이버페이 주문관리 수수료", "매출연동 수수료", "정산예정금액", "개인통관고유부호", "주문일시",
    "배송희망일", "구독신청회차", "구독진행회차", "구독배송희망일", "배송태그 유형", "출입방법 유형", "출입방법 내용",
    "수령위치 유형", "수령위치 내용",
]


def _db_row_to_naver_excel_row(r):
    rec = {col: "" for col in _NAVER_EXCEL_COLUMNS}
    rec.update({
        "상품주문번호": str(r.get('order_no', '') or ''),
        "주문번호":     str(r.get('order_group_no', '') or ''),
        "수취인명":     str(r.get('recipient', '') or ''),
        "구매자명":     str(r.get('buyer', '') or ''),
        "상품명":       str(r.get('product_name', '') or ''),
        "상품번호":     str(r.get('product_no', '') or ''),
        "옵션정보":     str(r.get('option_info', '') or ''),
        "수량":         int(r.get('qty', 0) or 0),
        "상품가격":     int(r.get('unit_price', 0) or 0),
        "최종 상품별 총 주문금액": int(r.get('order_amount', 0) or 0),
        "최초 상품별 총 주문금액": int(r.get('order_amount', 0) or 0),
        "배송비 합계":  int(r.get('shipping_fee', 0) or 0),
        "정산예정금액": int(r.get('settlement', 0) or 0),
        "송장번호":     str(r.get('tracking_no', '') or ''),
        "택배사":       str(r.get('courier', '') or ''),
        "결제일":       str(r.get('order_date', '') or ''),
        "주문일시":     str(r.get('order_date', '') or ''),
        "주문상태":     "발송대기",
        "주문세부상태": "발주확인",
        "배송속성":     "당일발송",
        "배송방법":     "택배,등기,소포",
        "배송방법(구매자 요청)": "택배,등기,소포",
        "판매채널":     "스마트스토어",
    })
    return rec


def active_orders_to_naver_excel_df(username):
    import json as _json
    import pandas as _pd
    rows = get_active_orders(username)
    if not rows:
        return _pd.DataFrame()
    _STATUS_KO = {
        "PAYED": "결제완료", "INSTRUCT": "발주확인",
        "PRODUCT_READY": "발송대기", "DELIVERING": "배송중",
        "DELIVERED": "배송완료", "PURCHASE_DECIDED": "구매확정",
        "CANCELED": "취소완료", "RETURNED": "반품완료",
        "EXCHANGED": "교환완료", "CANCEL_NOPAY": "미결제취소",
    }
    _SUB_STATUS_KO = {"INSTRUCT": "발주확인", "CANCEL": "취소", "RETURN": "반품", "NOT_YET": "미발주", "OK": ""}

    records = []
    for r in rows:
        rj = r.get('raw_json') or ''
        if rj:
            try:
                rec = _json.loads(rj)
                if rec.get("주문상태") in _STATUS_KO:
                    rec["주문상태"] = _STATUS_KO[rec["주문상태"]]
                if rec.get("주문세부상태") in _SUB_STATUS_KO:
                    rec["주문세부상태"] = _SUB_STATUS_KO[rec["주문세부상태"]]
                records.append(rec)
                continue
            except Exception:
                pass
        records.append(_db_row_to_naver_excel_row(r))
    return _pd.DataFrame(records)


def db_rows_to_orders_df(rows):
    import pandas as _pd
    if not rows:
        return _pd.DataFrame()
    df = _pd.DataFrame(rows)
    rename = {
        'recipient':    '수취인명',
        'buyer':        '구매자명',
        'product_name': '상품명',
        'product_no':   '상품번호',
        'option_info':  '옵션정보',
        'qty':          '수량',
        'unit_price':   '상품가격',
        'order_amount': '최종 상품별 총 주문금액',
        'shipping_fee': '배송비 합계',
        'settlement':   '정산예정금액',
        'status':       '주문상태',
        'tracking_no':  '송장번호',
        'courier':      '택배사',
        'cost_price':   '구입가격',
        'order_no':     '상품주문번호',
        'order_group_no':'주문번호',
        'order_date':   '결제일',
    }
    df = df.rename(columns=rename)
    df['제주/도서 추가배송비'] = 0
    return df


def search_order_history(username, keyword='', product_name='', date_from='', date_to='', limit=300):
    conn = get_user_db(username)
    query = "SELECT * FROM order_history WHERE 1=1"
    params = []
    if keyword:
        q = f'%{keyword}%'
        query += " AND (recipient LIKE ? OR buyer LIKE ? OR order_no LIKE ? OR order_group_no LIKE ?)"
        params.extend([q, q, q, q])
    if product_name:
        query += " AND product_name LIKE ?"
        params.append(f'%{product_name}%')
    if date_from:
        query += " AND order_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND order_date <= ?"
        params.append(date_to)
    query += " ORDER BY order_date DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
