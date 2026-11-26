"""📷 사진으로 네이버 신상품 등록 — Next.js 새 화면(/m/naver-register)이 부른다.

PC '네이버 등록 › 📷 제품사진(여러 장) + 가격사진으로 신상품 등록'의 모바일판.
분석·등록 로직은 photo_register_service에 있다(PC와 같은 함수·같은 판매가 공식).

분석(AI 2~3번 + 카테고리 + SEO)과 등록(사진 업로드 + AI 설명·태그 + 등록)은 둘 다
1분을 넘기기도 해 nginx에서 끊긴다 → 백그라운드 스레드로 돌리고 /job/{id}로 결과를
본다(uvicorn workers=1이라 메모리 작업표로 충분). 각자 본인 스토어 API 키로 올라가고
본인 등록 수량에 잡힌다.
"""
import base64
import threading
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import photo_register_service as prs
from api.stock import require_login

router = APIRouter(prefix="/api/nreg", tags=["naver-register"])

_JOBS = {}            # id → {user, kind, status: running|ok|fail, msg, result, at}
_LOCK = threading.Lock()


def _quota(username):
    try:
        from db_naver_reg import naver_reg_quota
        return naver_reg_quota(username)
    except Exception:
        return {'limit': 0, 'used': 0, 'remaining': None, 'blocked': False}


class Img(BaseModel):
    name: str = ""
    content_b64: str


def _bytes(img: Img):
    try:
        b = base64.b64decode(img.content_b64.split(',')[-1])
    except Exception:
        raise HTTPException(status_code=400, detail=f"사진을 읽지 못했습니다: {img.name}")
    n = img.name.lower()
    mt = 'image/png' if n.endswith('.png') else 'image/webp' if n.endswith('.webp') else 'image/jpeg'
    return b, mt


def _start(user, kind, fn):
    u = user["username"]
    with _LOCK:
        if any(j['user'] == u and j['kind'] == kind and j['status'] == 'running'
               for j in _JOBS.values()):
            raise HTTPException(status_code=409, detail='이미 진행 중입니다 — 끝날 때까지 기다려 주세요')
        for k in [k for k, j in _JOBS.items() if time.time() - j['at'] > 3600]:
            _JOBS.pop(k, None)
        jid = uuid.uuid4().hex[:12]
        _JOBS[jid] = {'user': u, 'kind': kind, 'status': 'running', 'msg': '시작…',
                      'result': None, 'at': time.time()}

    def log(m):
        with _LOCK:
            _JOBS[jid]['msg'] = str(m)

    def run():
        try:
            r = fn(log)
            with _LOCK:
                _JOBS[jid].update(status='ok', msg='완료', result=r)
        except Exception as e:     # 스레드 안 오류도 화면에 보여 준다
            with _LOCK:
                _JOBS[jid].update(status='fail', msg=str(e) or '실패')

    threading.Thread(target=run, daemon=True).start()
    return {'job': jid}


@router.get("/status")
def status(user: dict = Depends(require_login)):
    return {**prs.status(user["username"]), 'quota': _quota(user["username"])}


class AnalyzeReq(BaseModel):
    prod: Img
    price: Img
    label: Optional[Img] = None


@router.post("/analyze")
def analyze(req: AnalyzeReq, user: dict = Depends(require_login)):
    """제품사진(대표 1장) + 가격사진 (+ 라벨) → 미리보기. 등록은 하지 않는다."""
    pb, pm = _bytes(req.prod)
    qb, qm = _bytes(req.price)
    lb, lm = _bytes(req.label) if req.label else (None, 'image/jpeg')
    u = user["username"]

    def fn(log):
        log('제품 사진 분석 중…')
        try:
            return prs.analyze(u, pb, pm, qb, qm, lb, lm, log=log)
        except ValueError as e:
            raise RuntimeError(str(e))
    return _start(user, 'analyze', fn)


class PriceReq(BaseModel):
    cost: int = 0
    margin: float = 0
    ship_cost: int = 0
    box_cost: int = 0
    customer_ship: int = 0
    sale: int = 0


@router.post("/price")
def price(req: PriceReq, user: dict = Depends(require_login)):
    """마진율 → 판매가(sale 0이면), 그리고 그 판매가의 실제 마진. PC 마진계산기와 같은 식."""
    sale = req.sale or prs.sale_price(req.cost, req.margin, req.ship_cost, req.box_cost,
                                      req.customer_ship)
    return {'sale': sale, **prs.margin_info(sale, req.cost, req.ship_cost, req.box_cost,
                                            req.customer_ship)}


@router.get("/presets")
def presets(user: dict = Depends(require_login)):
    """🚚 배송 설정·🎁 혜택 — 저장된 기본값 + 고를 수 있는 값(PC와 같은 설정)."""
    return prs.presets(user["username"])


class PresetReq(BaseModel):
    value: dict


@router.post("/presets/{kind}")
def save_preset(kind: str, req: PresetReq, user: dict = Depends(require_login)):
    if kind not in ('delivery', 'benefit'):
        raise HTTPException(status_code=404, detail='없는 설정')
    return {'value': prs.save_preset(user["username"], kind, req.value)}


@router.get("/categories")
def categories(q: str, user: dict = Depends(require_login)):
    import naver_api
    from db_products import get_all_settings
    s = get_all_settings(user["username"]) or {}
    r, err = naver_api.search_naver_categories(s.get('api_client_id', ''),
                                               s.get('api_client_secret', ''), q.strip())
    if err and not r:
        raise HTTPException(status_code=400, detail=str(err))
    return {'items': (r or [])[:30]}


class RegReq(BaseModel):
    images: List[Img]
    name: str
    name_raw: str = ""
    volume: str
    sale_price: int
    cost_tag: int = 0
    customer_ship: Optional[int] = None
    cat_id: str
    cat_full: str = ""
    costco_no: str
    barcode: str = ""
    brand: str = ""
    manufacturer: str = ""
    model_name: str = ""
    desc: str = ""
    food: Optional[dict] = None
    spec_rows: Optional[List[List[str]]] = None   # 한글표시사항 표 [[항목, 값]] (화면에서 고친 그대로)
    tags: Optional[List[dict]] = None             # 화면에서 고른 연관태그 [{code, text}]
    ai_desc: bool = True                          # 설명이 비었으면 등록 때 AI가 쓴다
    ai_tags: bool = True                          # 태그가 비었으면 등록 때 AI가 만든다
    delivery: Optional[dict] = None               # 🚚 이 건 배송 설정(없으면 저장된 기본값)
    benefits: Optional[dict] = None               # 🎁 이 건 혜택 포인트(없으면 저장된 기본값)


@router.post("/register")
def register(req: RegReq, user: dict = Depends(require_login)):
    u = user["username"]
    st = prs.status(u)
    if not st['has_key']:
        raise HTTPException(status_code=400, detail='네이버 커머스 API 키가 없습니다 (설정에서 먼저 넣어 주세요)')
    q = _quota(u)
    if q.get('blocked'):
        raise HTTPException(status_code=403,
                            detail=f"네이버 등록 한도 소진 ({q.get('used', 0)}/{q.get('limit', 0)})")
    if not req.images:
        raise HTTPException(status_code=400, detail='제품 사진이 없습니다')
    imgs = [_bytes(i) for i in req.images[:10]]
    f = req.model_dump(exclude={'images'})
    is_admin = bool(user.get("is_admin"))

    def fn(log):
        try:
            return prs.register(u, is_admin, imgs, f, log=log)
        except ValueError as e:
            raise RuntimeError(str(e))
    return _start(user, 'register', fn)


# ── 미리보기에서 다시 만들기 (PC의 🤖 버튼들과 같다) ──
class DescReq(BaseModel):
    image: Img
    name: str
    cat_full: str = ""


@router.post("/desc")
def desc(req: DescReq, user: dict = Depends(require_login)):
    """🤖 AI 상세설명 다시 쓰기 — 대표 사진 + 상품명."""
    b, mt = _bytes(req.image)
    u = user["username"]

    def fn(log):
        log('상세설명 쓰는 중…')
        try:
            return {'desc': prs.gen_desc(u, b, mt, req.name, req.cat_full)}
        except ValueError as e:
            raise RuntimeError(str(e))
    return _start(user, 'desc', fn)


class LabelReq(BaseModel):
    image: Img


@router.post("/label")
def label(req: LabelReq, user: dict = Depends(require_login)):
    """🍱 표시사항(라벨) 사진 → 한글표시사항 표."""
    b, mt = _bytes(req.image)
    u = user["username"]

    def fn(log):
        log('라벨 읽는 중… (원재료·영양성분)')
        try:
            return prs.gen_label(u, b, mt)
        except ValueError as e:
            raise RuntimeError(str(e))
    return _start(user, 'label', fn)


@router.get("/spec/{costco_no}")
def spec(costco_no: str, user: dict = Depends(require_login)):
    """🌐 코스트코 사이트의 한글표시사항(상품번호로 조회)."""
    rows = prs.costco_spec_rows(costco_no)
    if not rows:
        raise HTTPException(status_code=404,
                            detail=f"코스트코 사이트에서 {costco_no}의 표시사항을 찾지 못했습니다")
    return {'rows': rows}


class TagsReq(BaseModel):
    name: str
    cat_full: str = ""


@router.post("/tags")
def tags(req: TagsReq, user: dict = Depends(require_login)):
    """🏷 AI 연관태그 다시 만들기 — 후보 → 네이버 태그사전 검증 → 제한태그 제거."""
    u = user["username"]

    def fn(log):
        log('태그 만드는 중… (AI 후보 → 태그사전 검증)')
        try:
            return {'tags': prs.gen_tags(u, req.name, req.cat_full)}
        except ValueError as e:
            raise RuntimeError(str(e))
    return _start(user, 'tags', fn)


@router.get("/job/{jid}")
def job(jid: str, user: dict = Depends(require_login)):
    with _LOCK:
        j = _JOBS.get(jid)
        if not j or j['user'] != user["username"]:
            raise HTTPException(status_code=404, detail='작업을 찾지 못했습니다')
        return {k: j[k] for k in ('status', 'msg', 'result')}
