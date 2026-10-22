"""매일 자동 정산 API — PC 예약 작업(tools/daily_settle)이 부른다. 관리자 전용.

  POST /api/admin/auto/renew     sid 갱신 — 매일 돌면 30일 만료를 넘지 않는다
  POST /api/admin/auto/dispatch  CJ 발송 파일(xlsx/csv) → 발송 기록
  POST /api/admin/auto/receipt   그날 영수증 파일들 → 영수증 저장(확신 없으면 저장 안 함)
  POST /api/admin/auto/match     그날 발송건 × 영수증 매칭 → 초안 저장(정산 요청은 안 함)
파일은 base64로 받는다 — multipart를 쓰면 python-multipart 의존성이 하나 더 는다.
처리 규칙은 auto_settle.py 머리말.
"""
import base64
import io
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import auto_settle as _as
from api.billing import require_admin, _check_date
from db import create_session

router = APIRouter(prefix="/api/admin/auto", tags=["auto-settle"])


class FileIn(BaseModel):
    name: str
    content_b64: str


class ReceiptIn(BaseModel):
    date: str
    files: List[FileIn]


class MatchIn(BaseModel):
    date: str


def _bytes(f: FileIn) -> bytes:
    try:
        return base64.b64decode(f.content_b64)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{f.name}: base64가 아닙니다")


@router.post("/renew")
def renew(user: dict = Depends(require_admin)):
    return {"sid": create_session(user["username"])}


@router.post("/dispatch")
def dispatch(f: FileIn, user: dict = Depends(require_admin)):
    import pandas as pd
    raw = io.BytesIO(_bytes(f))
    try:
        df = (pd.read_csv(raw, dtype=str) if f.name.lower().endswith('.csv')
              else pd.read_excel(raw, dtype=str))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{f.name} 읽기 실패: {e}")
    df = df.fillna('')
    return _as.upload_dispatch(df.to_dict('records'), [str(c) for c in df.columns])


@router.post("/receipt")
def receipt(body: ReceiptIn, user: dict = Depends(require_admin)):
    return _as.upload_receipt(_check_date(body.date),
                              [(f.name, _bytes(f)) for f in body.files], user["username"])


@router.post("/match")
def match(body: MatchIn, user: dict = Depends(require_admin)):
    return _as.match_day(_check_date(body.date), user["username"])
