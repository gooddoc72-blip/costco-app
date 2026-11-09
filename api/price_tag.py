"""가격표 사진 API — Next.js 새 화면(/m/pricetag)이 부른다.

판독·비교·저장·되돌리기는 price_tag_service가 한다. 공유 매입가를 바꾸는 일이라
관리자만 쓴다(Streamlit 제품 DB의 같은 기능도 관리자 전용).
사진은 한 장씩 보낸다 — 여러 장을 한 요청에 담으면 판독이 길어져 nginx 60초에 끊긴다.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import price_tag_service as pts
from api.stock import require_login

router = APIRouter(prefix="/api/ptag", tags=["price-tag"])


def _admin(user: dict = Depends(require_login)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="관리자만 쓸 수 있습니다")
    return user


class ReadReq(BaseModel):
    name: str = ""
    content_b64: str


@router.post("/read")
def read(req: ReadReq, user: dict = Depends(_admin)):
    return pts.read(req.content_b64, req.name)


@router.get("/lookup/{code}")
def lookup(code: str, user: dict = Depends(_admin)):
    r = pts.lookup(code)
    if not r:
        raise HTTPException(status_code=404, detail=f"공유DB에 {code}가 없습니다")
    return r


class SaveRow(BaseModel):
    product_no: str = ""
    name: str = ""
    barcode: str = ""
    new_price: int = 0
    final_price: int = 0
    discount: int = 0
    sale_end: str = ""
    photo: str = ""


class SaveReq(BaseModel):
    rows: List[SaveRow]


@router.post("/save")
def save(req: SaveReq, user: dict = Depends(_admin)):
    return pts.save(user["username"], [r.model_dump() for r in req.rows])


@router.get("/history")
def history(limit: int = 30, user: dict = Depends(_admin)):
    return {"batches": pts.history(min(max(1, limit), 100))}


class UndoReq(BaseModel):
    batch: str


@router.post("/undo")
def undo(req: UndoReq, user: dict = Depends(_admin)):
    try:
        return pts.undo(user["username"], req.batch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
