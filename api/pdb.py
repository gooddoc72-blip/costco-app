"""제품 DB API — Next.js 새 화면(/m/products)이 부른다.

분류·검색·표시값은 product_db_service, 쓰기는 db_products가 한다(Streamlit 제품 DB와
같은 함수). 사용자는 자기 제품표만 본다. 옵션 매핑만 관리자가 다른 판매자를 고를 수 있다.

경로가 /api/products가 아닌 이유: 옛 /m 화면 코드 일부가 withBase 없이
'/api/products/...'를 불러 nginx를 거쳐 이 서버로 온다. 같은 이름을 쓰면 옛 코드가
엉뚱한 응답을 받는다.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import product_db_service as pds
from api.stock import require_login

router = APIRouter(prefix="/api/pdb", tags=["product-db"])


def _target(user: dict, username: str) -> str:
    """조회 대상 — 비우면 본인. 다른 사람은 관리자만."""
    u = (username or '').strip() or user["username"]
    if u != user["username"] and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="본인 상품만 볼 수 있습니다")
    return u


@router.get("/list")
def list_products(kind: str = "focus", category: str = "", q: str = Query("", max_length=80),
                  page: int = 1, per: int = 20, user: dict = Depends(require_login)):
    return pds.list_products(user["username"], kind=kind, category=category, q=q,
                             page=page, per=per, is_admin=bool(user.get("is_admin")))


class SaveReq(BaseModel):
    shared_id: Optional[int] = None
    private_id: Optional[int] = None
    match_keyword: str = ""
    costco_name: Optional[str] = None
    product_no: Optional[str] = None
    unit_price: Optional[int] = None
    split_qty: Optional[int] = None
    sale_price: Optional[int] = None
    shipping_fee: Optional[int] = None
    naver_category_id: Optional[str] = None


@router.post("/save")
def save(req: SaveReq, user: dict = Depends(require_login)):
    try:
        return pds.save_product(user["username"], req.model_dump(),
                                is_admin=bool(user.get("is_admin")))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class IdReq(BaseModel):
    id: int


@router.post("/delete")
def delete(req: IdReq, user: dict = Depends(require_login)):
    from db_products import delete_private_product
    return {"deleted": delete_private_product(user["username"], req.id)}


@router.post("/unlock")
def unlock(req: IdReq, user: dict = Depends(require_login)):
    from db_products import unlock_split_product
    orig = unlock_split_product(user["username"], req.id)
    if not orig:
        raise HTTPException(status_code=400, detail="가격 분리된 행이 아닙니다")
    return {"product_no": orig}


# ── 🔢 묶음 상품 분류 ──
@router.get("/pack")
def pack(user: dict = Depends(require_login)):
    return {"rows": pds.pack_rows(user["username"])}


class PackReq(BaseModel):
    id: int
    value: int          # 1 = 내용물 설명(곱하지 않음), N = N개 묶음


@router.post("/pack")
def pack_set(req: PackReq, user: dict = Depends(require_login)):
    from db_products import set_pack_multiplier
    set_pack_multiplier(user["username"], req.id, req.value)
    return {"ok": True}


# ── 🔗 코스트코 번호 연결 제안 ──
@router.get("/links")
def links(user: dict = Depends(require_login)):
    from db_products import suggest_costco_links
    return {"rows": suggest_costco_links(user["username"])}


class LinkItem(BaseModel):
    naver_no: str
    costco_no: str


class LinkReq(BaseModel):
    items: List[LinkItem]


@router.post("/links")
def links_apply(req: LinkReq, user: dict = Depends(require_login)):
    from db_products import link_costco_to_naver
    n = sum(link_costco_to_naver(user["username"], i.naver_no, i.costco_no) for i in req.items)
    return {"linked": n}


# ── 🏷 옵션별 코스트코번호 ──
@router.get("/options")
def options(date_from: str = "", date_to: str = "", username: str = "",
            user: dict = Depends(require_login)):
    return pds.option_rows(_target(user, username), date_from, date_to)


@router.get("/sellers")
def sellers(user: dict = Depends(require_login)):
    """옵션 매핑 판매자 선택용 — 관리자만 목록을 받는다."""
    if not user.get("is_admin"):
        return {"rows": []}
    from db_auth import get_all_users
    return {"rows": [{"username": u["username"], "name": u.get("display_name") or u["username"]}
                     for u in (get_all_users() or []) if not u.get("is_admin")]}


class OptItem(BaseModel):
    naver_pno: str
    option_code: str
    option_name: str = ""
    product_name: str = ""
    costco_pno: str = ""


class OptReq(BaseModel):
    username: str = ""
    items: List[OptItem]


@router.post("/options")
def options_save(req: OptReq, user: dict = Depends(require_login)):
    from db_products import upsert_option_map
    u = _target(user, req.username)
    rows = [{**i.model_dump(), "username": u, "costco_pno": i.costco_pno.strip()}
            for i in req.items]
    return upsert_option_map(rows, updated_by=user["username"])


# ── 📈 코스트코 가격 변동 이력 ──
@router.get("/price-history")
def price_history(user: dict = Depends(require_login)):
    from db_stats import get_price_change_history
    return {"rows": get_price_change_history(user["username"], limit=100) or []}


@router.post("/price-history/clear")
def price_history_clear(user: dict = Depends(require_login)):
    from db_products import clear_price_change_history
    return {"deleted": clear_price_change_history(user["username"])}
