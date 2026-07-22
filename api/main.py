"""수익계산 FastAPI 서비스 (Phase2).

Streamlit 앱과 **같은 세션(auth.db sessions)**을 재사용한다. 별도 systemd 서비스로
포트에 뜨고, nginx가 /api/* 만 이쪽으로 라우팅한다(Phase4). 로그인은 Streamlit과 공유.

재사용 순수 코어:
  · pages_lib.profit_calc.loader.build_settlement_df  — 세션 비의존 주문 df
  · pages_lib.profit_calc.compute.compute_rows        — 매칭·구입가(순수, 패리티 0)

⚠️ Phase2 범위: 조회(구입가·매칭) / 저장 / 삭제 / 영수증. 행별 '수입 총액' 공식은
   page.py에서 순수함수로 추출 + 패리티 테스트 후 노출 예정(돈 계산 원칙). 지금 GET은
   영수증/세션 오버라이드 없이 **DB 기준 구입가**만 계산한다(Streamlit과 값이 다를 수 있음).
"""
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from pydantic import BaseModel

from db import (
    get_session_user, get_user_info,
    get_all_products, get_shared_products,
    save_profit_settlements, get_profit_settlements,
    get_recent_receipt_items, get_receipt_dates,
    get_user_db,
)
from services import match_product_to_db
from pages_lib.profit_calc.loader import build_settlement_df
from pages_lib.profit_calc.compute import compute_rows

app = FastAPI(title="Costco 수익계산 API", version="0.2.0")


# ── 인증: Streamlit 세션(sid) 재사용 ──────────────────────────
def _extract_token(request: Request) -> str:
    # 우선순위: Authorization: Bearer → 쿠키 sid → 쿼리 sid (Streamlit URL 방식)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.cookies.get("sid")
            or request.query_params.get("sid")
            or "")


def require_user(request: Request) -> dict:
    token = _extract_token(request)
    username = get_session_user(token)
    if not username:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다 (유효한 sid 없음)")
    info = get_user_info(username) or {"username": username, "is_admin": 0}
    return info


def require_admin(user: dict = Depends(require_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="관리자 전용")
    return user


# ── 응답 모델 ────────────────────────────────────────────────
class ProfitRow(BaseModel):
    order_no: str
    recipient: str
    product_name: str
    qty: int
    settlement_amount: int
    shipping_fee: int
    extra_shipping: int
    cost_price: int
    match_source: str
    matched_name: str
    matched_pno: str
    split_qty: int


class ProfitResponse(BaseModel):
    date: str
    source: Optional[str] = None
    count: int
    rows: list[ProfitRow]
    note: str = "DB 기준 구입가(영수증·오버라이드 미반영). 수입 총액은 Phase2.5."


class SaveRequest(BaseModel):
    rows: list[dict]


class DeleteRequest(BaseModel):
    order_nos: list[str]


# ── 엔드포인트 ───────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"ok": True, "service": "profit-api", "version": app.version}


@app.get("/api/me")
def me(user: dict = Depends(require_user)):
    return {"username": user["username"], "is_admin": bool(user.get("is_admin"))}


def _int(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


@app.get("/api/profit/{date}", response_model=ProfitResponse)
def get_profit(date: str, user: dict = Depends(require_user)):
    """날짜별 정산표 — build_settlement_df + compute_rows(구입가·매칭)."""
    username = user["username"]
    df, src_label, _kind = build_settlement_df(username, date)
    if df is None or df.empty:
        return ProfitResponse(date=date, source=src_label, count=0, rows=[])

    preload_user = get_all_products(username)
    preload_shared = get_shared_products()
    _memo: dict = {}

    def match_fn(nm, pno):
        if pno:
            return match_product_to_db(username, nm, product_no=pno,
                                       _user_prods=preload_user, _shared_prods=preload_shared)
        if nm not in _memo:
            _memo[nm] = match_product_to_db(username, nm, product_no='',
                                            _user_prods=preload_user, _shared_prods=preload_shared)
        return _memo[nm]

    has_pno = 'product_no' in df.columns
    has_cost = '구입가격' in df.columns
    records = df.to_dict('records')
    rows_in = []
    for rec in records:
        rows_in.append({
            'idx': str(rec.get('_sk', '')),
            '수취인명': rec.get('수취인명', ''),
            '상품명': rec.get('상품명', ''),
            'product_no': (str(rec.get('product_no', '') or '') if has_pno else ''),
            '수량': rec.get('수량', 1),
            '구입가격': (rec.get('구입가격', 0) if has_cost else 0),
        })
    # 영수증/오버라이드는 Phase2.5 — 지금은 DB 기준 매칭만.
    results, _links = compute_rows(rows_in, match_fn=match_fn, calc_date_str=date)

    out = []
    for rec, res in zip(records, results):
        out.append(ProfitRow(
            order_no=str(rec.get('_sk', '')),
            recipient=str(rec.get('수취인명', '') or ''),
            product_name=str(rec.get('상품명', '') or ''),
            qty=_int(rec.get('수량', 1), 1),
            settlement_amount=_int(rec.get('정산예정금액', 0)),
            shipping_fee=_int(rec.get('배송비 합계', 0)),
            extra_shipping=_int(rec.get('제주/도서 추가배송비', 0)),
            cost_price=_int(res.get('cost', 0)),
            match_source=str(res.get('source', '')),
            matched_name=str(res.get('matched_name', '')),
            matched_pno=str(res.get('matched_pno', '')),
            split_qty=_int(res.get('sqty', 1), 1),
        ))
    return ProfitResponse(date=date, source=src_label, count=len(out), rows=out)


@app.post("/api/profit/{date}/save")
def save_profit(date: str, req: SaveRequest, user: dict = Depends(require_user)):
    """정산저장 — profit_settlements UPSERT (프론트가 계산·편집한 행을 그대로 저장)."""
    n = save_profit_settlements(user["username"], date, req.rows)
    return {"saved": n, "date": date}


@app.get("/api/profit/{date}/settlements")
def list_settlements(date: str, user: dict = Depends(require_user)):
    """저장된 profit_settlements 원본 조회 (복원/디버그용)."""
    rows = get_profit_settlements(user["username"], date)
    return {"date": date, "count": len(rows), "rows": rows}


@app.delete("/api/profit/{date}")
def delete_profit(date: str, req: DeleteRequest, user: dict = Depends(require_user)):
    """선택 주문 영구 삭제 — order_no 기준 4개 테이블 + 영수증정산 정리.
    (page.py 일괄삭제 로직과 동일: dispatch_log/order_history/profit_settlements/daily_orders)"""
    onos = [str(o).strip() for o in (req.order_nos or []) if str(o).strip() and str(o).strip() != 'nan']
    if not onos:
        return {"deleted": 0, "date": date}
    conn = get_user_db(user["username"])
    ph = ",".join("?" * len(onos))
    for tbl in ("dispatch_log", "order_history", "profit_settlements", "daily_orders"):
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE order_no IN ({ph})", onos)
        except Exception:
            pass
    conn.commit()
    conn.close()
    try:
        from db_receipt_settle import remove_settlement_items
        remove_settlement_items(user["username"], onos)
    except Exception:
        pass
    return {"deleted": len(onos), "date": date}


@app.get("/api/receipt/dates")
def receipt_dates(user: dict = Depends(require_user)):
    return {"dates": get_receipt_dates(user["username"])}


@app.get("/api/receipt/recent")
def receipt_recent(days: int = 90, user: dict = Depends(require_user)):
    items = get_recent_receipt_items(user["username"], days=days)
    return {"count": len(items), "items": items}
