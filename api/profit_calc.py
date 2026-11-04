"""수익계산 API — Next.js 새 화면(/m/profit)이 부른다.

계산·저장은 profit_service가 Streamlit 수익계산과 같은 함수로 한다. 화면에서 고친 값은
overrides로 받아 서버가 다시 계산하고, 저장할 때도 서버 계산값을 저장한다(브라우저가 보낸
수익을 그대로 믿지 않는다 — 옛 /api/profit/{date}/save는 그랬다).

경로가 /api/profit이 아닌 이유: 그 경로는 /calc 베타 화면이 쓰고 있다.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import profit_service as ps
from api.stock import require_login

router = APIRouter(prefix="/api/pc", tags=["profit-calc"])


class Override(BaseModel):
    unit_cost: Optional[int] = None      # 1주문 단가 (× 수량 = 구입가)
    keyword: Optional[str] = None        # 이 상품으로 매칭
    receipt_pno: Optional[str] = None    # 영수증 품목 고르기
    receipt_name: Optional[str] = None
    receipt_unit: Optional[int] = None
    delivery: Optional[int] = None
    box: Optional[int] = None


def _ov(d: Dict[str, Override]):
    return {k: {f: v for f, v in o.model_dump().items() if v is not None} for k, o in (d or {}).items()}


def _target(user: dict, username: str) -> str:
    u = (username or '').strip() or user["username"]
    if u != user["username"] and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="본인 수익만 볼 수 있습니다")
    return u


@router.get("/day/{date}")
def day(date: str, user: dict = Depends(require_login)):
    return ps.day(user["username"], date)


class PreviewReq(BaseModel):
    overrides: Dict[str, Override] = {}


@router.post("/day/{date}/preview")
def preview(date: str, req: PreviewReq, user: dict = Depends(require_login)):
    """고친 값으로 다시 계산만 한다(저장 안 함)."""
    return ps.day(user["username"], date, overrides=_ov(req.overrides))


class SaveReq(BaseModel):
    overrides: Dict[str, Override] = {}
    move_dup: bool = False
    update_price_db: bool = False


@router.post("/day/{date}/save")
def save(date: str, req: SaveReq, user: dict = Depends(require_login)):
    return ps.save_day(user["username"], date, overrides=_ov(req.overrides),
                       move_dup=req.move_dup, update_price_db=req.update_price_db)


class DeleteReq(BaseModel):
    order_nos: List[str]


@router.post("/day/{date}/delete")
def delete(date: str, req: DeleteReq, user: dict = Depends(require_login)):
    return {"deleted": ps.delete_orders(user["username"], req.order_nos)}


@router.get("/receipts")
def receipts(days: int = 90, user: dict = Depends(require_login)):
    return {"items": ps.receipts(user["username"], days=max(7, min(365, days)))}


# ── 📅 월별 ──
@router.get("/month")
def month(ym: str = "", username: str = "", user: dict = Depends(require_login)):
    return ps.month(_target(user, username), ym)


@router.get("/users")
def users(user: dict = Depends(require_login)):
    """월별 사용자 선택 — 관리자만 목록을 받는다."""
    if not user.get("is_admin"):
        return {"rows": []}
    from db_auth import get_all_users
    return {"rows": [{"username": u["username"], "name": u.get("display_name") or u["username"]}
                     for u in (get_all_users() or []) if (u.get("status") or "active") == "active"]}


class MonthEditReq(BaseModel):
    username: str = ""
    edits: Dict[int, Dict[str, int]]     # {id: {settlement_amount|cost_price|delivery_cost|box_cost: 값}}


_EDITABLE = {"settlement_amount", "cost_price", "delivery_cost", "box_cost"}


@router.post("/month/edit")
def month_edit(req: MonthEditReq, user: dict = Depends(require_login)):
    from db_profit_calc import update_profit_rows
    edits = {int(i): {k: max(0, int(v)) for k, v in ch.items() if k in _EDITABLE}
             for i, ch in req.edits.items()}
    edits = {i: ch for i, ch in edits.items() if ch}
    return {"updated": update_profit_rows(_target(user, req.username), edits)}


class MonthDelReq(BaseModel):
    username: str = ""
    ids: List[int]


@router.post("/month/delete")
def month_delete(req: MonthDelReq, user: dict = Depends(require_login)):
    """중복 정리 — 같은 주문의 다른 날짜 저장 행을 지운다."""
    from db_profit_calc import delete_profit_rows
    return {"deleted": delete_profit_rows(_target(user, req.username), req.ids)}
