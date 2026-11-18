"""미정산 금액 — 로그인 팝업·홈 카드(PC·/m)가 같은 숫자를 쓰도록 한 곳에서 계산한다.

미정산 금액 = 청구했는데 아직 입금·차감 안 된 청구서 전체(기간 무관) − 예치금 잔액.
정산·청구 화면 맨 위 결론 줄과 같은 식이다. 예치금이 미입금을 다 덮으면 0 이하라
미정산이 없는 것으로 본다(팝업도 안 띄운다). 예치금이 마이너스면 그만큼 더해진다.
"""
from datetime import date

import db_settle as _ds
import db_deposit as _dep

SEEN_KEY = 'unsettled_popup_seen'      # 사용자 설정: '날짜|금액' — 오늘 그만 보기


def _i(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _calc(invs, bal):
    unpaid = sum(_i(i.get('total_amount')) for i in invs)
    amount = unpaid - _i(bal)
    if amount <= 0:
        return None
    dates = sorted(str(i.get('settle_date') or '') for i in invs if i.get('settle_date'))
    return {'amount': amount, 'unpaid': unpaid, 'balance': _i(bal),
            'days': len(invs), 'oldest': dates[0] if dates else ''}


def unsettled(username):
    """{amount, unpaid, balance, days, oldest} — 미정산이 없으면 None."""
    u = str(username or '').strip()
    if not u:
        return None
    try:
        invs = _ds.list_invoices('2000-01-01', str(date.today()), username=u,
                                 status='billed') or []
        return _calc(invs, _dep.balance(u))
    except Exception:
        return None


def unsettled_all():
    """관리자용 — [{username, amount, unpaid, balance, days, oldest}] 금액 큰 순."""
    try:
        invs = _ds.list_invoices('2000-01-01', str(date.today()), status='billed') or []
        bals = _dep.balances()
    except Exception:
        return []
    by = {}
    for i in invs:
        by.setdefault(str(i['username']), []).append(i)
    users = set(by) | {u for u, b in bals.items() if _i(b) < 0}
    out = []
    for u in users:
        r = _calc(by.get(u, []), bals.get(u, 0))
        if r:
            out.append({'username': u, **r})
    out.sort(key=lambda r: -r['amount'])
    return out


def seen_today(username, amount):
    """오늘 이 금액으로 '오늘 그만 보기'를 눌렀나. 금액이 바뀌면 다시 띄운다."""
    try:
        from db_products import get_setting
        return get_setting(username, SEEN_KEY) == f"{date.today()}|{int(amount)}"
    except Exception:
        return False


def mark_seen(username, amount):
    try:
        from db_products import set_setting
        set_setting(username, SEEN_KEY, f"{date.today()}|{int(amount)}")
    except Exception:
        pass
