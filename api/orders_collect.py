"""일일 주문 수집 API — Next.js 새 화면(/m/orders/collect)이 부른다.

수집·저장은 order_collect_service가 Streamlit '일일 주문 수집'과 같은 함수로 한다.
수집은 네이버 조회·주소 보완·발송상태 확인까지 수십 초 걸려 nginx 대기(60초)를 넘길 수
있다 — 뒤에서 돌리고 화면은 /api/oc/job/{job}으로 진행을 본다(영수증 정산 API와 같은 방식).
엑셀은 base64로 받는다.
"""
import base64
import threading
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import order_collect_service as oc
from api.stock import require_login

router = APIRouter(prefix="/api/oc", tags=["orders-collect"])

_JOBS = {}
_JOBS_MAX = 30


def _start(user, fn):
    job = uuid.uuid4().hex[:12]
    st = {'running': True, 'lines': [], 'result': None, 'error': '', 'owner': user['username'],
          'started': datetime.now().strftime('%H:%M:%S')}
    _JOBS[job] = st
    for old in list(_JOBS)[:-_JOBS_MAX]:
        _JOBS.pop(old, None)

    def _go():
        try:
            st['result'] = fn(lambda m: st['lines'].append(str(m)))
        except Exception as e:
            st['error'] = str(e)
        finally:
            st['running'] = False
    threading.Thread(target=_go, daemon=True).start()
    return {'job': job}


@router.get("/job/{job}")
def job_status(job: str, user: dict = Depends(require_login)):
    st = _JOBS.get(job)
    if not st or st['owner'] != user['username']:
        raise HTTPException(status_code=404, detail="작업이 없습니다(서버가 다시 시작됐을 수 있습니다 — 다시 하세요)")
    return {k: v for k, v in st.items() if k != 'owner'}


@router.get("/status")
def status(user: dict = Depends(require_login)):
    return oc.status(user["username"])


class CollectReq(BaseModel):
    platform: str                       # naver | coupang | cafe24
    date_from: str
    date_to: str
    status: str = ""
    incl_prev: bool = True
    incl_after_cutoff: bool = False


@router.post("/collect")
def collect(req: CollectReq, user: dict = Depends(require_login)):
    u = user["username"]
    if req.platform == 'naver':
        fn = lambda log: oc.collect_naver(u, req.date_from, req.date_to, req.status or 'PAYED',  # noqa: E731
                                          req.incl_prev, req.incl_after_cutoff, log=log)
    elif req.platform == 'coupang':
        fn = lambda log: oc.collect_coupang(u, req.date_from, req.date_to, req.status or 'ALL',  # noqa: E731
                                            req.incl_after_cutoff, log=log)
    elif req.platform == 'cafe24':
        fn = lambda log: oc.collect_cafe24(u, req.date_from, req.date_to, log=log)  # noqa: E731
    else:
        raise HTTPException(status_code=400, detail="알 수 없는 플랫폼입니다")
    return _start(user, fn)


class FileIn(BaseModel):
    name: str
    content_b64: str


class ExcelReq(BaseModel):
    files: List[FileIn]
    password: str = ""
    order_date: str = ""
    incl_after_cutoff: bool = False


@router.post("/excel")
def excel(req: ExcelReq, user: dict = Depends(require_login)):
    files = []
    for f in req.files:
        try:
            files.append((f.name, base64.b64decode(f.content_b64)))
        except Exception:
            raise HTTPException(status_code=400, detail=f"{f.name}: base64가 아닙니다")
    if not files:
        raise HTTPException(status_code=400, detail="파일이 없습니다")
    u = user["username"]
    return _start(user, lambda log: oc.collect_excel(u, files, req.password, req.order_date,
                                                     req.incl_after_cutoff, log=log))


@router.get("/active")
def active(incl_after_cutoff: int = 0, user: dict = Depends(require_login)):
    return oc.active(user["username"], incl_after_cutoff=bool(incl_after_cutoff))


class SaveReq(BaseModel):
    order_date: str
    order_nos: Optional[List[str]] = None
    incl_after_cutoff: bool = False


@router.post("/save")
def save(req: SaveReq, user: dict = Depends(require_login)):
    try:
        return oc.save_daily(user["username"], req.order_date, req.order_nos, req.incl_after_cutoff)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sync")
def sync(user: dict = Depends(require_login)):
    u = user["username"]
    return _start(user, lambda log: oc.sync(u))


class ExcludeReq(BaseModel):
    order_nos: List[str]
    also_daily: bool = False


@router.post("/exclude")
def exclude(req: ExcludeReq, user: dict = Depends(require_login)):
    return oc.exclude(user["username"], req.order_nos, req.also_daily)


@router.get("/excluded")
def excluded(user: dict = Depends(require_login)):
    from db import get_excluded_orders
    return {"rows": get_excluded_orders(user["username"])}


class NosReq(BaseModel):
    order_nos: List[str]


@router.post("/restore")
def restore(req: NosReq, user: dict = Depends(require_login)):
    from db import remove_excluded_orders
    return {"restored": remove_excluded_orders(user["username"], req.order_nos)}


@router.get("/search")
def search(q: str = Query("", max_length=80), product: str = Query("", max_length=80),
           date_from: str = "", date_to: str = "", user: dict = Depends(require_login)):
    from db import search_order_history
    rows = search_order_history(user["username"], keyword=q.strip(), product_name=product.strip(),
                                date_from=date_from, date_to=date_to, limit=300)
    keep = ('order_no', 'order_date', 'ordered_at', 'recipient', 'buyer', 'product_name', 'option_info',
            'qty', 'order_amount', 'settlement', 'status', 'tracking_no', 'courier')
    return {"rows": [{k: r.get(k) for k in keep} for r in rows]}


# ── 저장된 주문(daily_orders) ──
@router.get("/daily")
def daily_dates(user: dict = Depends(require_login)):
    from db import get_daily_order_date_counts
    return {"dates": [{"date": d, "count": n} for d, n in get_daily_order_date_counts(user["username"])]}


@router.get("/daily/{order_date}")
def daily_rows(order_date: str, user: dict = Depends(require_login)):
    from db import get_daily_orders
    keep = ('order_no', 'recipient', 'product_name', 'option_info', 'qty', 'order_amount',
            'settlement', 'cost_price')
    return {"rows": [{k: r.get(k) for k in keep} for r in (get_daily_orders(user["username"], order_date) or [])]}


class DailyDelReq(BaseModel):
    order_date: str
    order_nos: Optional[List[str]] = None     # 비우면 그 날짜 전체


@router.post("/daily/delete")
def daily_delete(req: DailyDelReq, user: dict = Depends(require_login)):
    from db import delete_daily_orders
    return {"deleted": delete_daily_orders(user["username"], order_date=req.order_date,
                                           order_nos=req.order_nos or None)}
