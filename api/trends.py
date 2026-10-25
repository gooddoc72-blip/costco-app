"""🔥 SNS 트렌드 TOP 30 API — Next.js /m/trends (관리자 전용).

조사는 몇 분 걸려서 백그라운드 스레드로 돌리고, 진행 로그는 /status로 본다.
"""
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import trend_research as tr
from api.billing import require_admin

router = APIRouter(prefix="/api/trends", tags=["trends"])

_RUN = {'running': False, 'lines': [], 'started_at': '', 'error': ''}
_LOCK = threading.Lock()


def _log(line):
    _RUN['lines'].append(f"{datetime.now().strftime('%H:%M:%S')} {line}")
    del _RUN['lines'][:-200]


def _with_decisions(r):
    if r:
        r['decisions'] = tr.get_decisions(r['id'])
    return r


@router.get("/latest")
def latest(admin: dict = Depends(require_admin)):
    try:
        import trend_council
        record = trend_council.track_record()
    except Exception:
        record = ''
    return {'report': _with_decisions(tr.get_report()), 'reports': tr.list_reports(),
            'has_youtube': bool(tr.keys()['youtube']), 'track_record': record}


@router.get("/report/{rid}")
def report(rid: int, admin: dict = Depends(require_admin)):
    r = tr.get_report(rid)
    if not r:
        raise HTTPException(status_code=404, detail="보고서가 없습니다")
    return {'report': _with_decisions(r)}


class DecideReq(BaseModel):
    costco_no: str
    decision: str = ''      # approve | hold | '' (취소)
    memo: str = ''


@router.post("/report/{rid}/decide")
def decide(rid: int, req: DecideReq, admin: dict = Depends(require_admin)):
    """관리자 승인 — 승인한 상품만 2주 뒤 판정 성적(적중률)에 들어간다."""
    if req.decision not in ('approve', 'hold', ''):
        raise HTTPException(status_code=400, detail="decision은 approve·hold·빈 값 중 하나")
    if not tr.get_report(rid):
        raise HTTPException(status_code=404, detail="보고서가 없습니다")
    tr.set_decision(rid, req.costco_no, req.decision, req.memo, by=admin['username'])
    return {'decisions': tr.get_decisions(rid)}


@router.get("/status")
def status(admin: dict = Depends(require_admin)):
    return dict(_RUN)


@router.post("/run")
def run(admin: dict = Depends(require_admin)):
    with _LOCK:
        if _RUN['running']:
            raise HTTPException(status_code=409, detail="이미 조사 중입니다")
        _RUN.update(running=True, lines=[], started_at=datetime.now().strftime('%Y-%m-%d %H:%M'), error='')

    def _go():
        try:
            # 새 스레드라 AI 사용량 귀속을 다시 정한다(안 하면 '미귀속'으로 쌓인다)
            import ai_service
            ai_service.set_current_user(admin['username'])
            tr.run(by=admin['username'], log=_log)
        except Exception as e:
            _RUN['error'] = str(e)
            _log(f'오류: {e}')
        finally:
            _RUN['running'] = False
    threading.Thread(target=_go, daemon=True).start()
    return {'started': True}


class KeyReq(BaseModel):
    key: str


@router.post("/youtube-key")
def youtube_key(req: KeyReq, admin: dict = Depends(require_admin)):
    from db import set_global_setting
    set_global_setting('youtube_api_key', req.key.strip())
    return {'ok': True, 'has_youtube': bool(req.key.strip())}
