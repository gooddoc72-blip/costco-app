"""세무회계 집계 레이어 (Phase 1) — 기존 데이터(profit_settlements)에서 손익·장부 산출.

설계: 하나의 거래(매출/매입/경비)를 회계 형식으로 집계 → 손익계산서·간편장부.
사업자유형(일반/간이/법인)·장부방식은 settings로 보기 전환(상위 페이지에서 처리).
"""
from db_core import get_user_db


def get_pl_summary(username: str, date_from: str, date_to: str) -> dict:
    """손익계산서 집계 (정산일 기준 profit_settlements).
    Returns: 매출/매출원가/운반비/포장비/지급수수료(추정)/영업이익 등.
    """
    conn = get_user_db(username)
    # 순이익은 달력/수익계산과 동일하게 재계산(원가 0행은 제외) — 저장 profit 컬럼은 신뢰 안 함
    r = conn.execute(
        """SELECT
            COUNT(*) cnt,
            COALESCE(SUM(order_amount),0)       sales,        -- 총매출(주문금액, 수수료 차감 전)
            COALESCE(SUM(settlement_amount),0)  settle,       -- 정산수령액(수수료 차감 후)
            COALESCE(SUM(shipping_fee),0)       ship,         -- 고객결제 배송비(전액 정산)
            COALESCE(SUM(cost_price),0)         cost,         -- 매출원가(매입가)
            COALESCE(SUM(delivery_cost),0)      delivery,     -- 운반비(택배원가)
            COALESCE(SUM(box_cost),0)           box,          -- 포장비
            COALESCE(SUM(CASE WHEN cost_price>0 THEN
                settlement_amount + COALESCE(shipping_fee,0)
                - cost_price - COALESCE(delivery_cost,0) - COALESCE(box_cost,0)
                ELSE 0 END),0)                  net_profit    -- 순이익(재계산)
        FROM profit_settlements
        WHERE settlement_date BETWEEN ? AND ? """,
        (date_from, date_to)
    ).fetchone()
    conn.close()
    d = {k: int(r[k] or 0) for k in r.keys()}
    # 플랫폼 지급수수료(추정) = 총매출 − 정산수령 (배송비는 전액 정산되므로 매출에서만 차감)
    d['commission'] = max(0, d['sales'] - d['settle'])
    # 매출(실수령 정산 + 배송비) 기준
    d['revenue'] = d['settle'] + d['ship']
    d['operating_profit'] = d['net_profit']
    return d


def get_ledger_rows(username: str, date_from: str, date_to: str, limit: int = 5000) -> list:
    """간편장부용 거래 리스트 (정산 확정 건). 각 행 = 매출 1건 + 관련 비용.
    """
    conn = get_user_db(username)
    rows = conn.execute(
        """SELECT settlement_date, order_no, recipient, product_name, qty,
                  order_amount, settlement_amount, shipping_fee,
                  cost_price, delivery_cost, box_cost, profit, match_source
           FROM profit_settlements
           WHERE settlement_date BETWEEN ? AND ?
           ORDER BY settlement_date, product_name LIMIT ?""",
        (date_from, date_to, int(limit))
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_monthly_pl(username: str, date_from: str, date_to: str) -> list:
    """월별 손익 추이 — [{month, sales, cost, profit}, ...]."""
    conn = get_user_db(username)
    rows = conn.execute(
        """SELECT substr(settlement_date,1,7) month,
                  COALESCE(SUM(order_amount),0) sales,
                  COALESCE(SUM(cost_price),0) cost,
                  COALESCE(SUM(profit),0) profit,
                  COUNT(*) cnt
           FROM profit_settlements
           WHERE settlement_date BETWEEN ? AND ?
           GROUP BY substr(settlement_date,1,7) ORDER BY month""",
        (date_from, date_to)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sheet_ledger_rows(username: str, date_from: str, date_to: str) -> list:
    """구글시트 장부(원장 A~E열) 붙여넣기용 — 날짜별 매출·수수료 2줄.

    통장에는 PG/플랫폼 정산금이 수수료를 뺀 순액으로 들어온다. 장부에서는 그 입금 줄을
    '제외(이체·중복)'로 빼고 이 두 줄로 대신 잡는다 → 매출(총액)과 지급수수료가 나뉜다.
      매출   = 주문금액 + 고객 결제 배송비          (입금)
      수수료 = 주문금액 − 정산수령액                (출금)
      매출 − 수수료 = 정산수령 + 배송비 = 통장 입금액
    매입원가·택배·포장은 통장/카드 출금으로 이미 잡히므로 내보내지 않는다(이중 계산 방지).
    내용 문구('코코비즈 매출'/'코코비즈 수수료')는 시트 분류규칙 키워드와 맞춰져 있다.
    """
    conn = get_user_db(username)
    rows = conn.execute(
        """SELECT settlement_date d, COUNT(*) cnt,
                  COALESCE(SUM(order_amount),0)      sales,
                  COALESCE(SUM(settlement_amount),0) settle,
                  COALESCE(SUM(shipping_fee),0)      ship
           FROM profit_settlements
           WHERE settlement_date BETWEEN ? AND ?
           GROUP BY settlement_date ORDER BY settlement_date""",
        (date_from, date_to)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        sales, settle, ship = int(r['sales']), int(r['settle']), int(r['ship'])
        revenue = sales + ship
        commission = max(0, sales - settle)
        if revenue:
            out.append({'날짜': r['d'], '내용': f"코코비즈 매출 {int(r['cnt'])}건",
                        '출금(지출)': None, '입금(수입)': revenue, '통장·카드': '코코비즈'})
        if commission:
            out.append({'날짜': r['d'], '내용': "코코비즈 수수료",
                        '출금(지출)': commission, '입금(수입)': None, '통장·카드': '코코비즈'})
    return out
