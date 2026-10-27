"""재고 조회 API — Next.js 새 화면(/m/stock)이 부른다. 읽기 전용.

계산은 stock_audit가 한다. 사용자별 현황·검색·이상 점검은 관리자 전용,
재고 카드는 관리자 또는 본인.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

import stock_audit as sa
from api.billing import require_admin, _token
from db import get_session_user, get_user_info

router = APIRouter(prefix="/api/stock", tags=["stock"])


def require_login(request: Request) -> dict:
    username = get_session_user(_token(request))
    if not username:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return get_user_info(username) or {"username": username, "is_admin": 0}


@router.get("/me")
def me(user: dict = Depends(require_login)):
    return {"username": user["username"], "is_admin": bool(user.get("is_admin"))}


@router.get("/overview")
def overview(_: dict = Depends(require_admin)):
    return sa.overview()


@router.get("/user/{username}")
def user_card(username: str, empty: int = 1, user: dict = Depends(require_login)):
    if not user.get("is_admin") and user["username"] != username:
        raise HTTPException(status_code=403, detail="본인 재고만 볼 수 있습니다")
    return sa.user_card(username, include_empty=bool(empty))


@router.get("/search")
def search(q: str = Query("", max_length=80), _: dict = Depends(require_admin)):
    return sa.search(q)


@router.get("/checks")
def checks(force: int = 0, _: dict = Depends(require_admin)):
    return sa.checks(force=bool(force))
