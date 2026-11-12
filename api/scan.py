"""바코드 스캔 API — Next.js 새 화면(/m/scan)이 부른다. 계산은 scan_service가 한다.

조회·바코드 연결은 로그인한 사용자 누구나(포장·출고 때도 쓴다).
이미 다른 상품에 붙은 바코드를 바꾸는 일과 매장 가격 저장은 관리자만.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import scan_service as ss
from api.stock import require_login

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.get("/lookup/{code}")
def lookup(code: str, user: dict = Depends(require_login)):
    return ss.lookup(code, user["username"], bool(user.get("is_admin")))


class LinkReq(BaseModel):
    barcode: str
    product_no: str


@router.post("/link")
def link(req: LinkReq, user: dict = Depends(require_login)):
    try:
        ss.link(req.barcode, req.product_no, user["username"], bool(user.get("is_admin")))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ss.lookup(req.barcode, user["username"], bool(user.get("is_admin")))


class PriceReq(BaseModel):
    code: str = ""
    product_no: str
    name: str = ""
    label_barcode: str = ""
    new_price: int
    final_price: int = 0
    discount: int = 0
    sale_end: str = ""


@router.post("/price")
def price(req: PriceReq, user: dict = Depends(require_login)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="매장 가격 저장은 관리자만 할 수 있습니다")
    if req.new_price <= 0:
        raise HTTPException(status_code=400, detail="가격을 넣어 주세요")
    r = ss.save_price(user["username"], req.model_dump(), code=req.code)
    return {**r, 'item': ss.lookup(req.code or req.product_no, user["username"], True)}
