"""영수증 정산 API — Next.js 새 화면(/m/receipt-settle)이 부른다.

처리는 전부 receipt_settle_service(→ 기존 receipt_settle·settle_core 함수)가 한다.
여기서는 로그인 확인과 입력 검사만 한다.

영수증 판독(특히 사진 AI 판독)은 한 장에 수십 초 걸려 nginx 대기 시간(60초)을 넘길 수 있다 —
트렌드 조사처럼 뒤에서 돌리고 화면은 /receipt/parse/{job}으로 진행을 본다. 파일은 base64로
받는다(api/auto_settle과 같은 이유).
"""
import base64
import threading
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import receipt_settle_service as svc
from api.billing import require_admin

router = APIRouter(prefix="/api/rsettle", tags=["receipt-settle"])

_JOBS = {}          # 판독 작업 — {job: {running, lines, result, error, started}}
_JOBS_MAX = 20


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


class UnmatchedDecision(BaseModel):
    username: str
    order_no: str
    how: str                    # stock | amount | link | skip
    owner: str = ''
    qty: int | None = None
    amount: int | None = None
    memo: str = ''


class UnmatchedReq(BaseModel):
    decisions: list[UnmatchedDecision]


class AssignPick(BaseModel):
    costco_no: str
    packs: int
    memo: str = ''


class AssignReq(BaseModel):
    mode: str                   # bill | stock | return
    username: str = ''
    bill_stock: bool = True
    picks: list[AssignPick]


def _dump(m):
    return m.model_dump() if hasattr(m, 'model_dump') else m.dict()


class UpFile(BaseModel):
    name: str
    content_b64: str


class ParseReq(BaseModel):
    files: list[UpFile]


class ReceiptRow(BaseModel):
    receipt_date: str = ''
    costco_no: str = ''
    name: str = ''
    qty: int = 1
    list_price: int = 0
    discount: int = 0
    unit_price: int = 0


class ReceiptReq(BaseModel):
    rows: list[ReceiptRow]


@router.get("/day/{d}/receipt")
def receipt_get(d: str, admin: dict = Depends(require_admin)):
    """그 날짜에 저장된 영수증 줄 — 다시 열어 고치거나 확인할 때."""
    return {'rows': svc.saved_receipt(_d(d))}


@router.post("/receipt/parse")
def receipt_parse(req: ParseReq, admin: dict = Depends(require_admin)):
    """영수증 PDF·사진 판독 시작 — 저장하지 않는다. 결과는 /receipt/parse/{job}."""
    files = []
    for f in req.files:
        try:
            files.append((f.name, base64.b64decode(f.content_b64)))
        except Exception:
            raise HTTPException(status_code=400, detail=f"{f.name}: base64가 아닙니다")
    if not files:
        raise HTTPException(status_code=400, detail="파일이 없습니다")
    return {'job': _start_job(lambda log: svc.parse_files(files, log=log), admin)}


def _start_job(fn, admin):
    """오래 걸리는 일을 뒤에서 돌린다 — fn(log) 의 반환값이 result. 반환: 작업 번호"""
    job = uuid.uuid4().hex[:12]
    st = {'running': True, 'lines': [], 'result': None, 'error': '', 'started': datetime.now().strftime('%H:%M:%S')}
    _JOBS[job] = st
    for old in list(_JOBS)[:-_JOBS_MAX]:
        _JOBS.pop(old, None)

    def _go():
        try:
            import ai_service
            ai_service.set_current_user(admin['username'])   # 새 스레드 — AI 사용량 귀속
            st['result'] = fn(lambda m: st['lines'].append(m))
        except Exception as e:
            st['error'] = str(e)
        finally:
            st['running'] = False
    threading.Thread(target=_go, daemon=True).start()
    return job


@router.get("/receipt/parse/{job}")
@router.get("/job/{job}")
def job_status(job: str, admin: dict = Depends(require_admin)):
    st = _JOBS.get(job)
    if not st:
        raise HTTPException(status_code=404, detail="작업이 없습니다(서버가 다시 시작됐을 수 있습니다 — 다시 하세요)")
    return st


# ── 발송 파일 (누락 발송건) ─────────────────────────────────────
class DispatchSaveReq(BaseModel):
    date: str
    skip_existing: bool = True
    use_row_date: bool = True
    move_wrong_date: bool = True


class UnknownAssign(BaseModel):
    idx: int
    username: str
    qty: int | None = None


class DispatchUnknownReq(DispatchSaveReq):
    assign: list[UnknownAssign]


class ManualRow(BaseModel):
    username: str
    order_no: str = ''
    tracking_no: str = ''
    recipient: str = ''
    product_name: str = ''
    qty: int = 1
    courier: str = ''


class ManualReq(BaseModel):
    date: str
    rows: list[ManualRow]


class ShipReq(BaseModel):
    date: str
    users: list[str]
    courier: str
    agree: bool = False


def _dsvc():
    import dispatch_service
    return dispatch_service


@router.post("/dispatch/preview")
def dispatch_preview(req: ParseReq, admin: dict = Depends(require_admin)):
    """발송 파일(엑셀·CSV) → 주문번호로 사용자 분류. 저장하지 않는다."""
    files = []
    for f in req.files:
        try:
            files.append((f.name, base64.b64decode(f.content_b64)))
        except Exception:
            raise HTTPException(status_code=400, detail=f"{f.name}: base64가 아닙니다")
    ds = _dsvc()
    p = ds.preview(files)
    if p.get('token'):
        p['summary'] = ds.summary(p['token'], p.get('default_date') or str(date.today()))
    p['couriers'] = ds.COURIERS
    return p


@router.get("/dispatch/{token}/summary")
def dispatch_summary(token: str, the_date: str = Query('', alias='date'), admin: dict = Depends(require_admin)):
    try:
        return _dsvc().summary(token, _d(the_date) if the_date else str(date.today()))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/dispatch/{token}/save")
def dispatch_save(token: str, req: DispatchSaveReq, admin: dict = Depends(require_admin)):
    try:
        return _dsvc().save(token, _d(req.date), req.skip_existing, req.use_row_date, req.move_wrong_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/dispatch/{token}/unknown")
def dispatch_unknown(token: str, req: DispatchUnknownReq, admin: dict = Depends(require_admin)):
    try:
        return _dsvc().save_unknown(token, _d(req.date), [_dump(a) for a in req.assign],
                                    req.skip_existing, req.use_row_date, req.move_wrong_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/dispatch/manual")
def dispatch_manual(req: ManualReq, admin: dict = Depends(require_admin)):
    try:
        return _dsvc().manual(_d(req.date), [_dump(r) for r in req.rows])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/dispatch/{token}/ship")
def dispatch_ship(token: str, req: ShipReq, admin: dict = Depends(require_admin)):
    """고른 사용자 스토어에 대신 발송처리 — 고객에게 발송 알림이 가고 되돌릴 수 없다. agree 필수."""
    if not req.agree:
        raise HTTPException(status_code=400, detail="되돌릴 수 없음을 확인해야 발송처리합니다.")
    if not req.users:
        raise HTTPException(status_code=400, detail="사용자를 고르세요.")
    ds = _dsvc()
    try:
        ds._get(token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    dd = _d(req.date)
    return {'job': _start_job(lambda log: ds.ship(token, req.users, req.courier, dd, log=log), admin)}


@router.post("/day/{d}/receipt/check")
def receipt_check(d: str, req: ReceiptReq, admin: dict = Depends(require_admin)):
    """저장 전 점검 — 의심스러운 줄(수량·금액), 날짜를 채울 줄."""
    return svc.check_receipt(_d(d), [_dump(r) for r in req.rows])


@router.post("/day/{d}/receipt/save")
def receipt_save(d: str, req: ReceiptReq, admin: dict = Depends(require_admin)):
    """영수증 저장 — 그 날짜(들)를 통째로 교체. 저장 뒤 다시 매칭해야 반영된다."""
    try:
        return svc.save_receipt(_d(d), [_dump(r) for r in req.rows], by=admin['username'])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@router.post("/day/{d}/unmatched")
def unmatched(d: str, req: UnmatchedReq, admin: dict = Depends(require_admin)):
    """영수증에 없는 발송건 처리 — 재고 출고(보유자)·금액 지정·후보 연결·청구 안 함."""
    ds = _d(d)
    try:
        r = svc.apply_unmatched(ds, [_dump(x) for x in req.decisions], by=admin['username'])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'result': r, **svc.view(ds)}


@router.post("/day/{d}/assign")
def assign(d: str, req: AssignReq, admin: dict = Depends(require_admin)):
    """남은 영수증 품목 — 그 사람에게 청구·그 사람 재고로 입고·반품요청."""
    ds = _d(d)
    try:
        r = svc.apply_assign(ds, req.mode, req.username, [_dump(x) for x in req.picks],
                             bill_stock=req.bill_stock, by=admin['username'])
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
