"""영수증 정산 API — Next.js 새 화면(/m/receipt-settle)이 부른다.

처리는 전부 receipt_settle_service(→ 기존 receipt_settle·settle_core 함수)가 한다.
여기서는 로그인 확인과 입력 검사만 한다.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import receipt_settle_service as svc
from api.billing import require_admin

router = APIRouter(prefix="/api/rsettle", tags=["receipt-settle"])


def _d(s: str) -> str:
    try:
        return str(date.fromisoformat(str(s)[:10]))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"날짜 형식이 아닙니다: {s}")


class MatchReq(BaseModel):
    carry_days: int = 0


class RowAction(BaseModel):
    username: str
    order_no: str
    action: str                 # ok | unok | stock | cut | edit
    qty: int | None = None
    amount: int | None = None
    memo: str | None = None


class RowsReq(BaseModel):
    actions: list[RowAction]


class FinalizeReq(BaseModel):
    replace: bool = False


@router.get("/day/{d}")
def day(d: str, admin: dict = Depends(require_admin)):
    return svc.view(_d(d))


@router.post("/day/{d}/match")
def match(d: str, req: MatchReq, admin: dict = Depends(require_admin)):
    ds = _d(d)
    svc.run_match(ds, by=admin['username'], carry_days=max(0, min(30, req.carry_days)))
    return svc.view(ds)


@router.post("/day/{d}/rows")
def rows(d: str, req: RowsReq, admin: dict = Depends(require_admin)):
    ds = _d(d)
    try:
        r = svc.row_actions(ds, [a.model_dump() if hasattr(a, 'model_dump') else a.dict()
                                 for a in req.actions], by=admin['username'])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'result': r, **svc.view(ds)}


@router.post("/day/{d}/finalize")
def finalize(d: str, req: FinalizeReq, admin: dict = Depends(require_admin)):
    ds = _d(d)
    try:
        return svc.finalize(ds, by=admin['username'], replace=req.replace)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
