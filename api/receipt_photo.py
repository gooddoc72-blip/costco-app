"""영수증 사진 판독 API — Next.js /m/receipt(매입가 업데이트)의 📷 사진 판독이 부른다.

전에는 Next.js 안에 따로 만든 TS 판독기(lib/vision/receiptPhoto.ts, 8/3)가 읽었다.
그 판독기는 그 뒤 파이썬 쪽에 들어간 고침을 하나도 못 받았다 — gemini-2.5-flash 고정
(새 키는 404)·thinking 없음·쿠폰 줄(CPN/IRC)·'2×' 수량·뒤 마이너스·만료일 날짜·
세로 조각 판독·단가 보정·상품번호 교정이 전부 빠져 있어 오류가 잦았다.
이제 영수증 정산과 **같은 파이썬 판독기**(ai_service.parse_receipt_photo)를 쓴다.

판독은 사진 한 장에 수십 초~2분이라 nginx 60초를 넘긴다 → 뒤에서 돌리고 화면은 job을 본다.
"""
import base64
import threading
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.stock import require_login

router = APIRouter(prefix="/api/rphoto", tags=["receipt-photo"])

_JOBS = {}
_JOBS_MAX = 30


class UpFile(BaseModel):
    name: str = ''
    content_b64: str


class ParseReq(BaseModel):
    files: List[UpFile]


def parse_photos(files, log=None):
    """[(이름, bytes)] → {items, errors, checks}. 저장하지 않는다.

    items는 화면(ReceiptItem) 모양: productNo·productName·qty·unitPrice(정가 단가)·receiptDate.
    """
    import ai_service
    import receipt_settle as rs
    from price_tag_service import _image

    ak, gk = ai_service.get_ai_keys()
    if not (ak or gk):
        raise ValueError('AI 키가 없습니다 — 관리자에게 문의하세요')
    raw, errors, checks, sigs = [], [], [], []
    # 사진은 동시에 읽는다(11/11) — 순서·중복 판정은 아래에서 원래 순서대로
    okn, oki = [], []
    for name, data in files:
        try:
            okn.append(name)
            oki.append(_image(base64.b64encode(data).decode(), name))
        except ValueError as e:
            okn.pop()
            errors.append({'file': name, 'error': str(e)})
    if log:
        log(f'사진 {len(oki)}장 동시에 읽는 중')
    res = ai_service.parse_receipt_photos(
        ak, oki, gemini_key=gk,
        on_done=(lambda n, t: log(f'{n}/{t}장 끝')) if log else None)
    for name, (d, err) in zip(okn, res):
        if err or not d:
            errors.append({'file': name, 'error': err or '판독 실패'})
            continue
        sig = rs.receipt_signature(d)
        if sig and any(rs.same_receipt_sig(sig, s) for s in sigs):
            errors.append({'file': name, 'error': f'앞 사진과 같은 영수증({sig[0]} {sig[1]})이라 건너뜀'})
            continue
        if sig:
            sigs.append(sig)
        rd = str(d.get('purchase_date') or '')
        n0 = len(raw)
        for it in (d.get('items') or []):
            if str(it.get('상품명') or '').strip():
                raw.append(dict(it, receipt_date=rd, _file=name))
        if len(raw) == n0:
            errors.append({'file': name, 'error': '품목을 읽지 못했습니다 (더 밝고 반듯하게 다시 촬영)'})
            continue
        iss = list(d.get('_check') or [])
        iss.append('저장해 둔 판독 결과(같은 사진)' if d.get('_cached') else f"{d.get('_secs', 0)}초")
        if d.get('_rotated'):
            iss.append(f"사진이 기울어/뒤집혀 {d['_rotated']}° 돌려 다시 읽음")
        if d.get('_tiled'):
            iss.append('세로로 잘라 다시 읽음')
        if d.get('_repaired'):
            iss.append(f"단가 {d['_repaired']}줄 보정")
        checks.append({'file': name, 'provider': str(d.get('_provider') or ''),
                       'verified': bool(d.get('_verified', True)), 'issues': iss})

    try:
        raw, snap = rs.snap_items_to_catalog(raw)
    except Exception:
        snap = []
    if snap and checks:
        checks[-1]['issues'] = checks[-1]['issues'] + ['상품번호·이름 교정: ' + s for s in snap]

    # 같은 상품번호는 영수증 날짜가 최신인 것 우선 (PDF 경로와 같은 규칙)
    by_no, no_num = {}, []
    for it in raw:
        row = {'productNo': ''.join(ch for ch in str(it.get('상품번호') or '') if ch.isdigit()),
               'productName': str(it.get('상품명') or ''),
               'qty': int(it.get('수량') or 1),
               'unitPrice': int(it.get('단가') or 0),
               'receiptDate': str(it.get('receipt_date') or ''),
               'sourceFile': it.get('_file') or ''}
        if not row['productNo']:
            no_num.append(row)
            continue
        prev = by_no.get(row['productNo'])
        if not prev or row['receiptDate'] > prev['receiptDate']:
            by_no[row['productNo']] = row
    return {'items': list(by_no.values()) + no_num, 'errors': errors, 'checks': checks}


@router.post("/parse")
def parse(req: ParseReq, user: dict = Depends(require_login)):
    files = []
    for f in req.files:
        try:
            files.append((f.name or 'receipt.jpg', base64.b64decode(f.content_b64)))
        except Exception:
            raise HTTPException(status_code=400, detail=f"{f.name}: base64가 아닙니다")
    if not files:
        raise HTTPException(status_code=400, detail="사진이 없습니다")
    job = uuid.uuid4().hex[:12]
    st = {'running': True, 'lines': [], 'result': None, 'error': '', 'owner': user['username'],
          'started': datetime.now().strftime('%H:%M:%S')}
    _JOBS[job] = st
    for old in list(_JOBS)[:-_JOBS_MAX]:
        _JOBS.pop(old, None)

    def _go():
        try:
            import ai_service
            ai_service.set_current_user(user['username'])   # 새 스레드 — AI 사용량 귀속
            st['result'] = parse_photos(files, log=lambda m: st['lines'].append(m))
        except Exception as e:
            st['error'] = str(e)
        finally:
            st['running'] = False
    threading.Thread(target=_go, daemon=True).start()
    return {'job': job}


@router.get("/job/{job}")
def job_status(job: str, user: dict = Depends(require_login)):
    st = _JOBS.get(job)
    if not st or (st['owner'] != user['username'] and not user.get('is_admin')):
        raise HTTPException(status_code=404, detail="작업이 없습니다(서버가 다시 시작됐을 수 있습니다 — 다시 올려 주세요)")
    return {k: v for k, v in st.items() if k != 'owner'}
