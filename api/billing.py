"""정산·청구 API — Streamlit '정산·청구' 화면을 새 화면(/m/billing)으로 옮기는 백엔드.

계산과 저장은 **기존 파이썬 함수를 그대로** 부른다(db_settle·db_deposit). 같은 돈
계산을 화면 쪽(TypeScript)에 다시 짜면 두 곳의 금액이 갈라진다 — 모바일 영수증
'매입가 반영'이 영수증 기록을 안 남겨 PC와 어긋난 일이 그 예다.

여기서 하는 일은 Streamlit 화면(pages_lib/settle_billing_page.py)의 버튼과 1:1이다.
  · 일별: 정산리스트 · 품목 내역 · 청구 · 입금완료 · 입금완료 취소 · 예치금 차감
  · 예치금: 잔액 현황 · 판매자별 내역(누적 잔액) · 예치 등록
  · 미입금자
관리자 전용.
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import db_settle as _ds
import db_deposit as _dep
from db import get_session_user, get_user_info, get_all_users, get_receipt_items_by_date

router = APIRouter(prefix="/api/billing", tags=["billing"])

#: Streamlit 화면과 같은 표시 — 예치금으로 결제된 청구서 메모
DEP_MEMO = '예치금 차감'


def _token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("sid") or request.query_params.get("sid") or ""


def require_admin(request: Request) -> dict:
    username = get_session_user(_token(request))
    if not username:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    info = get_user_info(username) or {"username": username, "is_admin": 0}
    if not info.get("is_admin"):
        raise HTTPException(status_code=403, detail="관리자 전용")
    return info


def _names() -> dict:
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def _i(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _check_date(d: str) -> str:
    try:
        return str(date.fromisoformat(str(d)[:10]))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"날짜 형식이 아닙니다: {d}")


# ── 일별 ─────────────────────────────────────────────────────
@router.get("/day/{settle_date}")
def day(settle_date: str, admin: dict = Depends(require_admin)):
    """그날 정산리스트 — 사람별 청구서 + 예치금 잔액·차감 여부 + 합계."""
    ds = _check_date(settle_date)
    names = _names()
    invs = _ds.list_invoices(ds) or []
    bal = _dep.balances()
    ded = _dep.deducted_map(ds)
    rows = [{
        'username': i['username'], 'name': names.get(i['username'], i['username']),
        'status': i['status'], 'item_count': _i(i['item_count']),
        'total_amount': _i(i['total_amount']), 'paid_amount': _i(i['paid_amount']),
        'pay_method': ('deposit' if i['username'] in ded
                       else ('account' if i['status'] == 'paid' else '')),
        'deducted': _i(ded.get(i['username'])),
        'balance': _i(bal.get(i['username'], 0)),
        'billed_at': str(i.get('billed_at') or '')[:16],
        'paid_at': str(i.get('paid_at') or '')[:16],
        'memo': str(i.get('memo') or ''),
    } for i in invs]
    _sum = lambda st, k='total_amount': sum(r[k] for r in rows if r['status'] == st)
    return {
        'date': ds,
        'rows': rows,
        'summary': {
            'total': sum(r['total_amount'] for r in rows), 'users': len(rows),
            'draft': _sum('draft'), 'draft_n': sum(r['status'] == 'draft' for r in rows),
            'billed': _sum('billed'), 'billed_n': sum(r['status'] == 'billed' for r in rows),
            'paid': _sum('paid', 'paid_amount'), 'paid_n': sum(r['status'] == 'paid' for r in rows),
            'deposit_n': len(ded),
        },
        'recent': [{'date': r['settle_date'], 'users': _i(r['users']), 'total': _i(r['total']),
                    'paid': _i(r.get('paid'))} for r in (_ds.settled_dates(limit=14) or [])],
    }


def _receipt_price_map(items):
    """{(영수증날짜, 코스트코번호): 영수증 줄} — 정가·할인 표시용(Streamlit과 같은 규칙)."""
    out = {}
    for rd in {str(it.get('receipt_date') or '') for it in (items or [])}:
        if not rd:
            continue
        try:
            for ri in (get_receipt_items_by_date('', rd) or []):
                out[(rd, str(ri.get('상품번호') or ''))] = ri
        except Exception:
            continue
    return out


@router.get("/day/{settle_date}/items/{username}")
def day_items(settle_date: str, username: str, admin: dict = Depends(require_admin)):
    """청구 근거 — 그 사람 그날 품목(정가·팩당 할인 포함)."""
    ds = _check_date(settle_date)
    items = _ds.get_items(ds, username=username) or []
    rmap = _receipt_price_map(items)
    out = []
    for it in items:
        ri = rmap.get((str(it.get('receipt_date') or ''), str(it.get('product_no') or ''))) or {}
        lp, pu = _i(ri.get('정가단가')), _i(ri.get('단가'))
        out.append({
            'id': _i(it['id']),
            'source': _ds.SOURCE_LABEL.get(it['source'], it['source']),
            'product_name': str(it['product_name'] or ''),
            'product_no': str(it['product_no'] or ''),
            'qty': _i(it['qty']) or 1,
            'list_price': lp,
            'discount_per_pack': max(0, lp - pu) if (lp and pu) else 0,
            'unit_price': _i(it['unit_price']), 'amount': _i(it['amount']),
            'order_no': str(it['order_no'] or ''), 'memo': str(it.get('memo') or ''),
        })
    inv = _ds.get_invoice(ds, username) or {}
    return {'date': ds, 'username': username, 'items': out,
            'goods_total': sum(r['amount'] for r in out),
            'invoice_total': _i(inv.get('total_amount')),
            'status': inv.get('status') or ''}


class BillReq(BaseModel):
    usernames: list[str]


@router.post("/day/{settle_date}/bill")
def bill(settle_date: str, req: BillReq, admin: dict = Depends(require_admin)):
    """청구 — 청구 전(draft)인 사람만 청구됨으로. 0원은 Streamlit처럼 제외."""
    ds = _check_date(settle_date)
    draft = {i['username']: _i(i['total_amount'])
             for i in (_ds.list_invoices(ds) or []) if i['status'] == 'draft'}
    targets = [u for u in req.usernames if draft.get(u, 0) > 0]
    if not targets:
        raise HTTPException(status_code=400, detail="청구할 대상이 없습니다(청구 전·0원 초과만)")
    n = _ds.mark_billed(ds, usernames=targets, by=admin['username'])
    return {'billed': n, 'amount': sum(draft[u] for u in targets)}


class PaidRow(BaseModel):
    username: str
    paid_amount: Optional[int] = None
    memo: str = ''


class PaidReq(BaseModel):
    rows: list[PaidRow]


@router.post("/day/{settle_date}/paid")
def paid(settle_date: str, req: PaidReq, admin: dict = Depends(require_admin)):
    """입금완료 — 청구됨(billed)인 사람만. 금액이 다르면 부분 입금."""
    ds = _check_date(settle_date)
    billed = {i['username']: _i(i['total_amount'])
              for i in (_ds.list_invoices(ds) or []) if i['status'] == 'billed'}
    done = []
    for r in req.rows:
        if r.username not in billed:
            continue
        amt = billed[r.username] if r.paid_amount is None else max(0, int(r.paid_amount))
        _ds.mark_paid(ds, r.username, paid_amount=amt, memo=r.memo or '')
        done.append(r.username)
    if not done:
        raise HTTPException(status_code=400, detail="입금 처리할 청구가 없습니다(청구됨 상태만)")
    return {'paid': len(done)}


class UnpayReq(BaseModel):
    username: str


@router.post("/day/{settle_date}/unpay")
def unpay(settle_date: str, req: UnpayReq, admin: dict = Depends(require_admin)):
    """입금완료 취소 — 청구서와 예치금을 **반드시 함께** 되돌린다(Streamlit _unpay와 같음)."""
    ds = _check_date(settle_date)
    inv = _ds.get_invoice(ds, req.username)
    if not inv or inv.get('status') != 'paid':
        raise HTTPException(status_code=400, detail="입금완료 상태가 아닙니다")
    was_dep = bool(_dep.deducted_map(ds).get(req.username))
    _ds.unmark_paid(ds, req.username)
    refunded = None
    if was_dep:
        r = _dep.undo_deduct(ds, req.username, by=admin['username'])
        refunded = {'balance': _i(r.get('balance'))}
    return {'ok': True, 'deposit_refunded': refunded}


class DeductReq(BaseModel):
    usernames: list[str]


@router.post("/day/{settle_date}/deduct")
def deduct(settle_date: str, req: DeductReq, admin: dict = Depends(require_admin)):
    """예치금 차감 — 청구(없으면 함께) → 차감 → 입금완료(메모 '예치금 차감'). Streamlit과 같은 순서."""
    ds = _check_date(settle_date)
    invs = {i['username']: i for i in (_ds.list_invoices(ds) or [])}
    already = _dep.deducted_map(ds)
    done, skipped = [], []
    for u in req.usernames:
        i = invs.get(u)
        amt = _i((i or {}).get('total_amount'))
        if not i or i['status'] == 'paid' or amt <= 0 or already.get(u):
            skipped.append(u)
            continue
        _ds.mark_billed(ds, usernames=[u], by=admin['username'])
        _dep.deduct(ds, u, amt, by=admin['username'])
        _ds.mark_paid(ds, u, paid_amount=amt, memo=DEP_MEMO)
        done.append({'username': u, 'amount': amt})
    if not done:
        raise HTTPException(status_code=400, detail="차감할 청구가 없습니다")
    return {'deducted': done, 'skipped': skipped,
            'amount': sum(d['amount'] for d in done)}


# ── 사용자별 — 한 사람의 여러 날치를 한 번에 ─────────────────
#   입금은 사람 단위로 여러 날치를 한 번에 보내는 일이 많다. 날짜별로만 보면
#   그 사람의 미입금 날짜를 하루씩 찾아다니며 처리해야 한다(Streamlit _tab_user).
@router.get("/user/{username}")
def user_invoices(username: str, date_from: str = '', date_to: str = '',
                  admin: dict = Depends(require_admin)):
    """한 사람의 기간 청구서(날짜별) + 상태별 합계 + 예치금 잔액."""
    t = _check_date(date_to) if date_to else str(date.today())
    f = _check_date(date_from) if date_from else str(date.fromisoformat(t) - timedelta(days=29))
    invs = sorted(_ds.list_invoices(f, t, username=username) or [],
                  key=lambda i: str(i['settle_date']))
    rows, deps = [], {}
    for i in invs:
        d = str(i['settle_date'])
        if i['status'] == 'paid' and d not in deps:
            deps[d] = _dep.deducted_map(d)
        rows.append({
            'date': d, 'status': i['status'], 'item_count': _i(i['item_count']),
            'total_amount': _i(i['total_amount']), 'paid_amount': _i(i['paid_amount']),
            'pay_method': ('deposit' if username in deps.get(d, {})
                           else ('account' if i['status'] == 'paid' else '')),
            'billed_at': str(i.get('billed_at') or '')[:16],
            'paid_at': str(i.get('paid_at') or '')[:16], 'memo': str(i.get('memo') or ''),
        })
    _s = lambda st, k='total_amount': sum(r[k] for r in rows if r['status'] == st)
    return {
        'username': username, 'name': _names().get(username, username),
        'date_from': f, 'date_to': t, 'rows': rows,
        'balance': _i(_dep.balances().get(username, 0)),
        'summary': {'total': sum(r['total_amount'] for r in rows),
                    'draft': _s('draft'), 'billed': _s('billed'),
                    'billed_n': sum(r['status'] == 'billed' for r in rows),
                    'paid': _s('paid', 'paid_amount')},
    }


class UserPaidRow(BaseModel):
    date: str
    paid_amount: Optional[int] = None
    memo: str = ''


class UserPaidReq(BaseModel):
    rows: list[UserPaidRow]


@router.post("/user/{username}/paid")
def user_paid(username: str, req: UserPaidReq, admin: dict = Depends(require_admin)):
    """여러 날치 입금완료 — 청구됨(billed)인 날만. 금액이 다르면 부분 입금."""
    done, skipped = [], []
    for r in req.rows:
        d = _check_date(r.date)
        inv = _ds.get_invoice(d, username)
        if not inv or inv.get('status') != 'billed':
            skipped.append(d)
            continue
        amt = _i(inv['total_amount']) if r.paid_amount is None else max(0, int(r.paid_amount))
        _ds.mark_paid(d, username, paid_amount=amt, memo=r.memo or '')
        done.append({'date': d, 'amount': amt})
    if not done:
        raise HTTPException(status_code=400, detail="입금 처리할 청구가 없습니다(청구됨 상태만)")
    return {'paid': len(done), 'amount': sum(x['amount'] for x in done), 'skipped': skipped}


@router.get("/users")
def users(admin: dict = Depends(require_admin)):
    """판매자 목록(이름순) — 사용자별 화면의 선택칸."""
    return {'rows': sorted([{'username': u, 'name': n} for u, n in _names().items()],
                           key=lambda x: x['name'])}


# ── 예치금 ───────────────────────────────────────────────────
@router.get("/deposits")
def deposits(admin: dict = Depends(require_admin)):
    """예치금 잔액 현황 — 사람별 입금·차감·조정·적립·잔액 + 청구 합계."""
    names = _names()
    bal, sm, tt = _dep.balances(), _dep.summaries(), _dep.totals()
    try:
        inv = _ds.totals_by_user()
    except Exception:
        inv = {}
    rows = []
    for u in names:
        s = sm.get(u) or {}
        billed = _i((inv.get(u) or {}).get('billed'))
        rows.append({
            'username': u, 'name': names[u],
            'charged': _i(s.get('charged')), 'spent': _i(s.get('spent')),
            'adjusted': _i(s.get('adjusted')), 'returned': _i(s.get('returned')),
            'balance': _i(bal.get(u, 0)), 'billed': billed,
            'other_paid': billed - _i(s.get('spent')),
            'last_charge': str(s.get('last_charge') or ''),
        })
    rows.sort(key=lambda r: r['balance'])
    return {'rows': rows, 'totals': {k: _i(v) for k, v in tt.items() if not isinstance(v, dict)},
            'kind_label': _dep.KIND_LABEL}


@router.get("/deposits/{username}")
def deposit_history(username: str, date_from: str = '', date_to: str = '',
                    admin: dict = Depends(require_admin)):
    """한 사람의 예치금 내역 — 그때그때 누적 잔액 포함(기간과 무관하게 처음부터 누적)."""
    allr = _dep.ledger(username=username, limit=5000) or []
    asc = sorted(allr, key=lambda x: (str(x['tx_date']), int(x['id'])))
    run, bal_at = 0, {}
    for r in asc:
        run += _i(r['amount'])
        bal_at[int(r['id'])] = run
    f = str(date_from or '2000-01-01')[:10]
    t = str(date_to or date.today())[:10]
    inn = [r for r in asc if f <= str(r['tx_date'])[:10] <= t]
    sumk = lambda k: sum(_i(r['amount']) for r in inn if r['kind'] == k)
    return {
        'username': username, 'name': _names().get(username, username),
        'balance': run,
        'period': {'charged': sumk('charge'), 'spent': -sumk('spend'),
                   'adjusted': sumk('adjust') + sumk('return_credit')},
        'rows': [{'id': int(r['id']), 'date': str(r['tx_date'])[:10],
                  'kind': r['kind'], 'kind_label': _dep.KIND_LABEL.get(r['kind'], r['kind']),
                  'amount': _i(r['amount']), 'balance': bal_at.get(int(r['id']), 0),
                  'settle_date': r.get('settle_date') or '', 'memo': r.get('memo') or '',
                  'by': r.get('created_by') or ''} for r in reversed(inn)],
    }


class ChargeReq(BaseModel):
    username: str
    amount: int
    tx_date: Optional[str] = None
    memo: str = ''


@router.post("/deposits/charge")
def deposit_charge(req: ChargeReq, admin: dict = Depends(require_admin)):
    """예치 등록 — 판매자가 보낸 돈."""
    if req.username not in _names():
        raise HTTPException(status_code=400, detail="없는 판매자입니다")
    r = _dep.charge(req.username, int(req.amount),
                    tx_date=_check_date(req.tx_date) if req.tx_date else None,
                    memo=req.memo, by=admin['username'])
    if not r.get('ok'):
        raise HTTPException(status_code=400, detail=r.get('msg') or '예치 등록 실패')
    return {'ok': True, 'balance': _i(r.get('balance'))}


# ── 미입금자 ─────────────────────────────────────────────────
@router.get("/unpaid")
def unpaid(admin: dict = Depends(require_admin)):
    """청구했는데 아직 안 들어온 돈 — 사람별 누적. 청구 전(draft)은 미입금이 아니다."""
    names = _names()
    rows = _ds.unpaid_by_user() or []
    return {'rows': [{'username': r['username'], 'name': names.get(r['username'], r['username']),
                      'days': _i(r.get('days')), 'amount': _i(r.get('amount')),
                      'oldest': str(r.get('oldest') or ''), 'newest': str(r.get('newest') or '')}
                     for r in rows],
            'total': sum(_i(r.get('amount')) for r in rows)}


@router.get("/unpaid/{username}")
def unpaid_detail(username: str, admin: dict = Depends(require_admin)):
    """한 사람의 미입금 청구서(날짜별)."""
    rows = [i for i in (_ds.unpaid_invoices() or []) if i['username'] == username]
    return {'rows': [{'date': i['settle_date'], 'amount': _i(i['total_amount']),
                      'items': _i(i['item_count']), 'billed_at': str(i.get('billed_at') or '')[:16]}
                     for i in rows]}
