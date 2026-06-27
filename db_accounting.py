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
    r = conn.execute(
        """SELECT
            COUNT(*) cnt,
            COALESCE(SUM(order_amount),0)       sales,        -- 총매출(주문금액)
            COALESCE(SUM(settlement_amount),0)  settle,       -- 정산수령액
            COALESCE(SUM(shipping_fee),0)       ship,         -- 고객결제 배송비
            COALESCE(SUM(cost_price),0)         cost,         -- 매출원가(매입가)
            COALESCE(SUM(delivery_cost),0)      delivery,     -- 운반비(택배원가)
            COALESCE(SUM(box_cost),0)           box,          -- 포장비
            COALESCE(SUM(profit),0)             profit        -- 저장된 순이익
        FROM profit_settlements
        WHERE settlement_date BETWEEN ? AND ? """,
        (date_from, date_to)
    ).fetchone()
    conn.close()
    d = {k: int(r[k] or 0) for k in r.keys()}
    # 플랫폼 지급수수료(추정) = 총매출 + 고객배송비 − 정산수령액
    d['commission'] = max(0, d['sales'] + d['ship'] - d['settle'])
    # 매출총이익 / 영업이익(광고비 제외)
    d['gross_profit'] = d['sales'] - d['cost']
    d['operating_profit'] = (d['sales'] + d['ship']
                             - d['cost'] - d['delivery'] - d['box'] - d['commission'])
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
