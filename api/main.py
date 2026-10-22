"""수익계산 FastAPI 서비스 (Phase2).

Streamlit 앱과 **같은 세션(auth.db sessions)**을 재사용한다. 별도 systemd 서비스로
포트에 뜨고, nginx가 /api/* 만 이쪽으로 라우팅한다(Phase4). 로그인은 Streamlit과 공유.

재사용 순수 코어:
  · pages_lib.profit_calc.loader.build_settlement_df  — 세션 비의존 주문 df
  · pages_lib.profit_calc.compute.compute_rows        — 매칭·구입가(순수, 패리티 0)

범위: 조회(구입가·매칭·수입) / 저장 / 삭제 / 영수증. GET 구입가(cost)는 영수증 매칭·
   수동 키워드 오버라이드·타인재고 웃돈까지 DB에서 재구성해 반영(pages_lib.profit_calc.enrich)
   → Streamlit(신규 세션)과 구입가 일치. 세션 한정(영수증 picker 라이브 선택)과 profit 축
   (실정산확정·포장배정 박스비)은 아직 미반영.
"""
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from pydantic import BaseModel

from db import (
    get_session_user, get_user_info,
    get_all_products, get_shared_products, get_all_settings,
    save_profit_settlements, get_profit_settlements,
    get_recent_receipt_items, get_receipt_dates,
    get_user_db, ingest_rank_rows, get_latest_ranks,
)
from services import match_product_to_db
from pages_lib.profit_calc.loader import build_settlement_df
from pages_lib.profit_calc.compute import compute_rows, compute_profit, settled_shipping
from pages_lib.profit_calc.enrich import (
    build_receipt_maps, build_kw_overrides, load_surcharge_map,
)

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
    product_no: str
    option_info: str
    qty: int
    order_amount: int
    settlement_amount: int
    shipping_fee: int
    extra_shipping: int
    settled_shipping: int
    cost_price: int
    delivery_cost: int
    box_cost: int
    profit: int
    match_source: str
    matched_name: str
    matched_pno: str
    matched_keyword: str
    split_qty: int


class ProfitSummary(BaseModel):
    settlement: int
    settled_shipping: int
    cost: int
    delivery: int
    box: int
    profit: int


class ProfitResponse(BaseModel):
    date: str
    source: Optional[str] = None
    count: int
    rows: list[ProfitRow]
    summary: ProfitSummary
    saved: bool = False
    note: str = ("ps(정산저장) 소스면 저장값 그대로(대시보드 일치). 그 외는 DB매칭 구입가"
                 "(영수증·수동 키워드 오버라이드·타인재고 웃돈 반영) + 설정 기본 택배/박스비."
                 " 영수증 picker 라이브 선택·실정산확정·포장배정 박스비는 미반영.")


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


def _settings_defaults(username):
    """행별 기본 택배비/박스비 — page.py와 동일 폴백(1800/300, 이상치 방어)."""
    s = get_all_settings(username)
    try:
        ship = int(s.get('shipping_cost') or 2000)
    except (TypeError, ValueError):
        ship = 1800
    try:
        box = int(s.get('box_cost') or 300)
    except (TypeError, ValueError):
        box = 300
    if ship > 100000:
        ship = 1800
    if box > 10000:
        box = 300
    return ship, box


@app.get("/api/profit/{date}", response_model=ProfitResponse)
def get_profit(date: str, user: dict = Depends(require_user)):
    """날짜별 정산표 — build_settlement_df + compute_rows(구입가·매칭) + compute_profit(수입).

    ps(정산저장) 소스: 저장된 값(구입가·택배·박스) 그대로 → 대시보드와 정확 일치.
    그 외 소스: DB매칭 구입가 + 설정 기본 택배/박스비로 수입 추정.
    """
    username = user["username"]
    # 계산은 자동 저장과 같은 함수(pages_lib.profit_calc.dispatch_profit)를 쓴다
    from pages_lib.profit_calc.dispatch_profit import compute_dispatch_rows
    rows, src_label, _kind, saved = compute_dispatch_rows(username, date, use_saved=True)
    out = [ProfitRow(**{k: r[k] for k in (
        'order_no', 'recipient', 'product_name', 'product_no', 'option_info', 'qty',
        'order_amount', 'settlement_amount', 'shipping_fee', 'extra_shipping',
        'settled_shipping', 'cost_price', 'delivery_cost', 'box_cost', 'profit',
        'match_source', 'matched_name', 'matched_pno', 'split_qty')},
        matched_keyword=r.get('matched_name', '') or '') for r in rows]
    summary = ProfitSummary(
        settlement=sum(r['settlement_amount'] for r in rows),
        settled_shipping=sum(r['settled_shipping'] for r in rows),
        cost=sum(r['cost_price'] for r in rows),
        delivery=sum(r['delivery_cost'] for r in rows),
        box=sum(r['box_cost'] for r in rows),
        profit=sum(r['profit'] for r in rows))
    return ProfitResponse(date=date, source=src_label, count=len(out), rows=out,
                          summary=summary, saved=saved)


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
        import db_settle as _dsx
        _dsx.remove_items(user["username"], onos)
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


# ── 순위 수집 결과 수신 (외부 수집기 → 코코비즈) ─────────────
# 네이버 쇼핑검색 API 폐지(2026-06) + 로그인 세션 차단(2026-08)으로 서버에서 직접
# 순위를 뜨는 경로가 모두 막혔다. 측정은 실사용 IP를 쓰는 로컬 수집기(Crawler Pro)가
# 맡고, 서버는 결과만 받아 기존 화면(캘린더·하락감지)에 그대로 태운다.
class RankRow(BaseModel):
    platform: str = "naver"          # 'coupang' | 'naver'
    search_keyword: str              # 검색한 키워드
    product_keyword: str = ""        # 추적 항목 표시명 (비우면 검색 키워드)
    product_id: str = ""             # 쿠팡 productId / 네이버 nvMid
    vendor_item_id: str = ""         # 쿠팡 vendorItemId (아이템위너 판정용)
    store_name: str = ""             # 네이버 스토어 슬러그
    rank_exposure: Optional[int] = None   # 광고 포함 '보이는 순위'
    rank_organic: Optional[int] = None    # 광고 제외 대표 순위 (미노출이면 None)
    rank_in_catalog: Optional[int] = None # 네이버 가격비교 판매처 목록 내 순위
    mall_count: Optional[int] = None      # 그 카탈로그의 총 판매처 수
    is_item_winner: Optional[str] = None  # 'O' | 'X' (쿠팡)
    is_ad: Optional[int] = None
    page: Optional[int] = None
    checked_at: str = ""             # 'YYYY-MM-DD HH:MM' (비우면 서버 시각)
    tracking_id: Optional[int] = None  # /api/ranks/latest 로 받아간 항목 id (있으면 식별값 대신 이걸로 저장)


class RankIngestRequest(BaseModel):
    rows: list[RankRow]
    source: str = "crawler_pro"


class AdminRankRow(RankRow):
    username: str                    # 이 결과를 저장할 사용자


class AdminRankIngestRequest(BaseModel):
    rows: list[AdminRankRow]
    source: str = "crawler_pro_admin"


@app.post("/api/ranks/ingest")
def ranks_ingest(req: RankIngestRequest, user: dict = Depends(require_user)):
    """수집기가 측정한 순위를 저장한다. 추적 항목이 없으면 자동 생성된다."""
    if not req.rows:
        return {"saved": 0, "created": 0, "skipped": 0}
    if len(req.rows) > 2000:
        raise HTTPException(status_code=413, detail="한 번에 2000건까지 보낼 수 있습니다")
    result = ingest_rank_rows(
        user["username"],
        [r.model_dump() for r in req.rows],
        source=(req.source or "crawler_pro")[:40],
    )
    return result


@app.get("/api/ranks/latest")
def ranks_latest(user: dict = Depends(require_user)):
    """추적 중인 항목별 최신 순위 — 수집기가 '무엇을 추적 중인지' 받아갈 때도 쓴다."""
    rows = get_latest_ranks(user["username"])
    return {"count": len(rows), "items": rows}


# ── 관리자 일괄 수집: 관리자 sid 하나로 전체 사용자의 순위체크를 잰다 ──────
# 사용자마다 PC에서 수집기를 돌릴 필요 없이, 관리자 PC 한 대가 모든 사용자의 목록을
# 받아 재고 사용자별 DB에 나눠 저장한다. 같은 키워드는 한 번만 검색하면 되므로
# 사용자가 늘어도 검색 횟수는 '서로 다른 키워드 수'만큼만 는다.
def _rank_users():
    """순위를 수집할 사용자 — 활성 상태이고 DB 파일이 이미 있는 사용자만.
    (get_user_db는 없는 파일을 새로 만들어 버리므로 존재를 먼저 확인한다)"""
    import os
    from db import get_all_users, DATA_DIR
    out = []
    for u in get_all_users():
        name = u.get("username") or ""
        if not name or (u.get("status") or "active") != "active":
            continue
        if os.path.isfile(os.path.join(DATA_DIR, f"{name}.db")):
            out.append(name)
    return out


@app.get("/api/admin/ranks/targets")
def admin_rank_targets(admin: dict = Depends(require_admin)):
    """전체 사용자의 추적 항목(각 항목에 username 포함)."""
    items, users_with_items = [], 0
    for name in _rank_users():
        try:
            rows = get_latest_ranks(name)
        except Exception:
            continue
        if rows:
            users_with_items += 1
        for r in rows:
            r["username"] = name
            items.append(r)
    return {"count": len(items), "users": users_with_items, "items": items}


@app.post("/api/admin/ranks/ingest")
def admin_rank_ingest(req: AdminRankIngestRequest, admin: dict = Depends(require_admin)):
    """관리자가 잰 결과를 사용자별로 저장한다. tracking_id가 있는 행만 받는다
    (남의 계정에 추적 항목을 새로 만들지 않기 위해)."""
    if len(req.rows) > 2000:
        raise HTTPException(status_code=413, detail="한 번에 2000건까지 보낼 수 있습니다")
    valid = set(_rank_users())
    grouped, total = {}, {"saved": 0, "created": 0, "skipped": 0}
    for r in req.rows:
        if r.username not in valid or not r.tracking_id:
            total["skipped"] += 1
            continue
        d = r.model_dump()
        d.pop("username", None)
        grouped.setdefault(r.username, []).append(d)
    source = (req.source or "crawler_pro_admin")[:40]
    for name, rows in grouped.items():
        res = ingest_rank_rows(name, rows, source=source, create_missing=False)
        for k in total:
            total[k] += res.get(k, 0)
    total["users"] = len(grouped)
    return total


# ── 정산·청구 (새 화면 /m/billing 백엔드) ─────────────────────
from api.billing import router as _billing_router  # noqa: E402
app.include_router(_billing_router)

# ── 매일 자동 정산 (PC 예약 작업 → 발송 파일·영수증 업로드·매칭 초안) ──
from api.auto_settle import router as _auto_settle_router  # noqa: E402
app.include_router(_auto_settle_router)
