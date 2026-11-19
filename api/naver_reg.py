"""사진으로 네이버 등록 — Next.js 새 화면(/m/naver-register)이 부른다.

가격표·바코드 사진(화면의 바코드 스캐너, 못 읽으면 /api/scan/photo-code AI) → 공유 상품 찾기 → 판매가 미리보기 → 등록.
등록 자체는 PC 장바구니 등록과 같은 naver_register_service.register_one을 쓴다
(카테고리 자동 판단·이미지 업로드·상세설명·등록 수량 집계가 PC와 똑같다).
판매가 기본값도 PC 장바구니와 같다: compute_sale_price(상품, 'cafe24_naver_margin'%).

등록은 이미지 업로드·AI 상세설명 때문에 1분을 넘기기도 해 nginx 60초에 끊긴다.
그래서 백그라운드 스레드로 돌리고 /job/{id}로 결과를 본다(uvicorn workers=1).
사진 한 장 = 상품 한 개. 각자 본인 스토어 API 키로 올라가고 본인 등록 수량에 잡힌다.
"""
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.stock import require_login

router = APIRouter(prefix="/api/nreg", tags=["naver-register"])

_JOBS = {}            # id → {user, status: running|ok|fail, msg, origin_no, at}
_LOCK = threading.Lock()


def _settings(username):
    from db_products import get_all_settings
    return get_all_settings(username) or {}


def _digits(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit())


def _merged(username, pno):
    """이 사용자 기준 상품 1건(공유 + 본인 등록 표시). 없으면 None."""
    from db import get_all_products_merged
    for p in get_all_products_merged(username):
        if str(p.get('product_no') or '').strip() == pno:
            return p
    return None


def _quota(username):
    try:
        from db_naver_reg import naver_reg_quota
        return naver_reg_quota(username)
    except Exception:
        return {'limit': 0, 'used': 0, 'remaining': None, 'blocked': False}


@router.get("/status")
def status(user: dict = Depends(require_login)):
    """등록 가능 여부 — API 키·등록 한도."""
    s = _settings(user["username"])
    return {'has_key': bool(s.get('api_client_id') and s.get('api_client_secret')),
            'quota': _quota(user["username"])}


@router.get("/preview/{code}")
def preview(code: str, user: dict = Depends(require_login)):
    """번호·바코드 → 상품·판매가 미리보기. 등록은 하지 않는다."""
    import scan_service as ss
    from naver_register_service import compute_sale_price
    sp, _how = ss._find(_digits(code))
    if not sp:
        raise HTTPException(status_code=404,
                            detail=f"'{_digits(code)}' 상품이 공유 DB에 없습니다 (먼저 코스트코 수집 필요)")
    pno = str(sp.get('product_no') or '').strip()
    p = _merged(user["username"], pno) or sp
    s = _settings(user["username"])
    try:
        margin = float(s.get('cafe24_naver_margin') or 10)
    except (TypeError, ValueError):
        margin = 10.0
    return {
        'product_no': pno,
        'name': str(p.get('costco_name') or ''),
        'image': str(p.get('image_url') or ''),
        'cost': int(p.get('online_price') or 0) or int(p.get('unit_price') or 0),
        'split_qty': int(p.get('split_qty') or 1),
        'margin': margin,
        'sale_price': compute_sale_price(p, margin),
        'registered': str(p.get('naver_product_no') or '').strip(),
        'has_image': bool(p.get('local_image') or p.get('image_url')),
    }


class RegReq(BaseModel):
    product_no: str
    sale_price: int


def _run(jid, username, product, sale):
    import naver_register_service as nrs
    from db_auth import get_global_setting

    def done(st, msg, origin=''):
        with _LOCK:
            _JOBS[jid].update(status=st, msg=msg, origin_no=origin)

    try:
        s = _settings(username)
        api_id, api_secret = s.get('api_client_id', ''), s.get('api_client_secret', '')
        # 코스트코 판매종료·품절이면 올리지 않는다 (PC 장바구니와 같다)
        try:
            from costco_crawler import fetch_costco_status
            cs = fetch_costco_status(str(product.get('product_no') or ''))
        except Exception:
            cs = {}
        if cs.get('exists') is False or cs.get('available') is False:
            return done('fail', f"코스트코 {cs.get('reason') or '판매종료/품절'} — 등록 안 함")
        ai_key = get_global_setting('anthropic_api_key') or s.get('anthropic_api_key', '')
        try:
            import json
            cat_map = json.loads(s.get('naver_cat_mappings') or '{}')
        except Exception:
            cat_map = {}
        cat_id, cat_full, _src = nrs.resolve_category(
            api_id, api_secret, product, cat_map=cat_map, ai_key=ai_key,
            open_creds=(s.get('naver_open_client_id', ''), s.get('naver_open_client_secret', '')))
        if not cat_id:
            return done('fail', '네이버 카테고리를 못 정했습니다 — PC 네이버 등록 화면에서 직접 골라 주세요')
        origin, err = nrs.register_one(
            username, api_id, api_secret, product, cat_id,
            opts={'sale_price': sale, 'as_tel': s.get('naver_as_tel') or '1588-1234',
                  'stock': 10, 'ai_key': ai_key, 'ai_desc': True, 'with_spec': True,
                  'cat_full': cat_full or '', 'source': 'manual', 'actor': username})
        if err or not origin:
            return done('fail', str(err or '등록 실패'))
        done('ok', f"등록 완료 · {cat_full or cat_id}", str(origin))
    except Exception as e:  # 스레드 안 오류도 화면에 보여 준다
        done('fail', f'오류: {e}')


@router.post("/register")
def register(req: RegReq, user: dict = Depends(require_login)):
    u = user["username"]
    s = _settings(u)
    if not (s.get('api_client_id') and s.get('api_client_secret')):
        raise HTTPException(status_code=400, detail='네이버 커머스 API 키가 없습니다 (설정에서 먼저 넣어 주세요)')
    q = _quota(u)
    if q.get('blocked'):
        raise HTTPException(status_code=403,
                            detail=f"네이버 등록 한도 소진 ({q.get('used', 0)}/{q.get('limit', 0)})")
    pno = _digits(req.product_no)
    p = _merged(u, pno)
    if not p:
        raise HTTPException(status_code=404, detail='상품을 찾지 못했습니다')
    if str(p.get('naver_product_no') or '').strip():
        raise HTTPException(status_code=409, detail=f"이미 등록된 상품입니다 (#{p['naver_product_no']})")
    if not (p.get('local_image') or p.get('image_url')):
        raise HTTPException(status_code=400, detail='상품 이미지가 없어 등록할 수 없습니다')
    sale = int(req.sale_price or 0)
    if sale < 1000:
        raise HTTPException(status_code=400, detail='판매가를 확인해 주세요')
    with _LOCK:
        for j in _JOBS.values():
            if j['user'] == u and j['pno'] == pno and j['status'] == 'running':
                raise HTTPException(status_code=409, detail='이 상품을 이미 등록하는 중입니다')
        # 오래된 작업 정리 (1시간)
        for k in [k for k, j in _JOBS.items() if time.time() - j['at'] > 3600]:
            _JOBS.pop(k, None)
        jid = uuid.uuid4().hex[:12]
        _JOBS[jid] = {'user': u, 'pno': pno, 'status': 'running', 'msg': '등록 중…',
                      'origin_no': '', 'at': time.time()}
    threading.Thread(target=_run, args=(jid, u, p, sale), daemon=True).start()
    return {'job': jid}


@router.get("/job/{jid}")
def job(jid: str, user: dict = Depends(require_login)):
    with _LOCK:
        j = _JOBS.get(jid)
        if not j or j['user'] != user["username"]:
            raise HTTPException(status_code=404, detail='작업을 찾지 못했습니다')
        return {k: j[k] for k in ('status', 'msg', 'origin_no')}
