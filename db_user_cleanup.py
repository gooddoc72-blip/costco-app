"""사용하지 않는 사용자 정리 — 활동 조회 + 안전 삭제.

관리자 › 회원 관리의 삭제 버튼은 한 번 누르면 확인 없이 사용자 DB 파일을
지웠고(os.remove), 그 사람의 예약 작업(crontab)은 그대로 남아 없는 계정으로
매일 돌았다. 예치금 잔액·미입금 청구가 있는 계정도 그냥 지워졌다.

여기서는
  · 누가 안 쓰는지 근거(마지막 로그인·주문 수집·발송)를 숫자로 보여 주고
  · 돈이 걸린 계정(예치금 잔액 ≠ 0, 미입금 청구)은 지우지 못하게 막고
  · 지울 때 사용자 DB를 지우지 않고 data/deleted_users/로 옮겨 둔다(복구 가능)
  · 그 사람의 세션과 예약 작업도 함께 치운다.
정산·예치금 원장(auth.db)의 기록은 남긴다 — 지난 청구·입금 이력이다.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

from db_core import DATA_DIR, get_auth_db

DELETED_DIR = os.path.join(DATA_DIR, "deleted_users")


def _user_db_path(username):
    return os.path.join(DATA_DIR, f"{username}.db")


def _max(conn, sql):
    try:
        r = conn.execute(sql).fetchone()
        return str(r[0] or '') if r else ''
    except Exception:
        return ''


def _count(conn, sql):
    try:
        r = conn.execute(sql).fetchone()
        return int(r[0] or 0) if r else 0
    except Exception:
        return 0


def _cron_lines():
    if sys.platform.startswith("win"):
        return []
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        return r.stdout.splitlines() if r.returncode == 0 else []
    except Exception:
        return []


def _is_users_cron(line, username):
    """'# COSTCO_TASK:<작업이름>_<사용자>' 표시가 붙은 그 사람 줄인가."""
    i = line.find("# COSTCO_TASK:")
    return i >= 0 and line[i:].strip().endswith(f"_{username}")


def user_activity(days=60):
    """관리자가 아닌 사용자별 활동 요약. 반환: [dict]

    미사용(unused) = 기준일(오늘−days) 이후 로그인·주문 수집·발송이 모두 없음.
    삭제 가능(deletable) = 예치금 잔액 0 · 미입금(청구됨) 청구 없음.
    """
    from db_auth import get_all_users
    _cut = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d")
    a = get_auth_db()
    a.row_factory = sqlite3.Row
    try:
        _login = {str(r[0]): str(r[1] or '') for r in a.execute(
            "SELECT username, MAX(created_at) FROM sessions GROUP BY username")}
    except Exception:
        _login = {}
    try:
        _bal = {str(r[0]): int(r[1] or 0) for r in a.execute(
            "SELECT username, COALESCE(SUM(amount),0) FROM deposit_ledger GROUP BY username")}
    except Exception:
        _bal = {}
    try:
        _unpaid = {str(r[0]): (int(r[1] or 0), int(r[2] or 0)) for r in a.execute(
            "SELECT username, COUNT(*), COALESCE(SUM(total_amount),0) FROM settle_invoice "
            "WHERE status='billed' GROUP BY username")}
    except Exception:
        _unpaid = {}
    a.close()
    _cron = _cron_lines()

    out = []
    for u in get_all_users():
        if u.get('is_admin'):
            continue
        n = u['username']
        _p = _user_db_path(n)
        _last_order = _last_disp = ''
        _n_orders = 0
        _size = os.path.getsize(_p) if os.path.exists(_p) else 0
        if _size:
            try:
                c = sqlite3.connect(_p, timeout=10)
                _last_order = _max(c, "SELECT MAX(created_at) FROM order_history")
                _n_orders = _count(c, "SELECT COUNT(*) FROM order_history")
                _last_disp = _max(c, "SELECT MAX(created_at) FROM dispatch_log")
                c.close()
            except Exception:
                pass
        _last_login = _login.get(n, '')
        _last_any = max(_last_login[:10], _last_order[:10], _last_disp[:10],
                        str(u.get('created_at') or '')[:10])
        _ub = _bal.get(n, 0)
        _uu = _unpaid.get(n, (0, 0))
        _block = []
        if _ub:
            _block.append(f"예치금 잔액 {_ub:,}원")
        if _uu[0]:
            _block.append(f"미입금 청구 {_uu[0]}건 {_uu[1]:,}원")
        out.append({
            'username': n, 'display_name': u.get('display_name') or n,
            'status': u.get('status') or 'active',
            'created_at': str(u.get('created_at') or '')[:10],
            'last_login': _last_login[:16], 'last_order': _last_order[:16],
            'last_dispatch': _last_disp[:16], 'orders': _n_orders,
            'last_any': _last_any,
            'cron': sum(1 for ln in _cron if _is_users_cron(ln, n)),
            'db_mb': round(_size / 1048576, 1),
            'balance': _ub, 'unpaid_n': _uu[0], 'unpaid_amt': _uu[1],
            'unused': _last_any < _cut,
            'deletable': not _block, 'block_reason': " · ".join(_block),
        })
    out.sort(key=lambda r: r['last_any'])
    return out


def safe_delete_user(username, by=''):
    """사용자 삭제 — 돈이 걸렸으면 막고, DB는 옮겨 두고, 세션·예약 작업을 치운다.

    반환: (ok, 메시지)
    """
    n = str(username or '').strip()
    if not n:
        return False, "사용자 이름이 없습니다."
    a = get_auth_db()
    try:
        row = a.execute("SELECT is_admin FROM users WHERE username=?", (n,)).fetchone()
        if not row:
            return False, f"{n}: 그런 사용자가 없습니다."
        if int(row[0] or 0):
            return False, f"{n}: 관리자 계정은 지울 수 없습니다."
        try:
            _b = int(a.execute("SELECT COALESCE(SUM(amount),0) FROM deposit_ledger "
                               "WHERE username=?", (n,)).fetchone()[0] or 0)
        except Exception:
            _b = 0
        try:
            _u = a.execute("SELECT COUNT(*), COALESCE(SUM(total_amount),0) FROM settle_invoice "
                           "WHERE username=? AND status='billed'", (n,)).fetchone()
        except Exception:
            _u = (0, 0)
    finally:
        a.close()
    if _b:
        return False, (f"{n}: 예치금 잔액 {_b:,}원이 남아 있어 지울 수 없습니다 — "
                       "환불·조정으로 0을 만든 뒤 지우세요.")
    if int(_u[0] or 0):
        return False, (f"{n}: 입금 안 된 청구 {int(_u[0])}건({int(_u[1] or 0):,}원)이 있어 "
                       "지울 수 없습니다 — 입금 처리 후 지우세요.")

    # 1) 사용자 DB → 백업 폴더로 이동(지우지 않는다)
    _moved = ''
    _src = _user_db_path(n)
    if os.path.exists(_src):
        os.makedirs(DELETED_DIR, exist_ok=True)
        _dst = os.path.join(DELETED_DIR, f"{n}_{datetime.now().strftime('%Y%m%d%H%M%S')}.db")
        shutil.move(_src, _dst)
        _moved = _dst
        for _ext in ("-wal", "-shm"):
            if os.path.exists(_src + _ext):
                try:
                    shutil.move(_src + _ext, _dst + _ext)
                except Exception:
                    pass

    # 2) 계정·세션 삭제 (정산·예치금 원장은 남긴다)
    a = get_auth_db()
    try:
        a.execute("DELETE FROM users WHERE username=? AND is_admin=0", (n,))
        a.execute("DELETE FROM sessions WHERE username=?", (n,))
        a.commit()
    finally:
        a.close()

    # 3) 예약 작업(crontab) 정리
    _cron_removed = 0
    _lines = _cron_lines()
    if _lines:
        _keep = [ln for ln in _lines if not _is_users_cron(ln, n)]
        _cron_removed = len(_lines) - len(_keep)
        if _cron_removed:
            try:
                subprocess.run(["crontab", "-"], input="\n".join(_keep) + "\n",
                               text=True, capture_output=True, timeout=10)
            except Exception:
                _cron_removed = 0

    # 4) 기록
    try:
        os.makedirs(DELETED_DIR, exist_ok=True)
        with open(os.path.join(DELETED_DIR, "log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({'at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                'username': n, 'by': by, 'db_backup': _moved,
                                'cron_removed': _cron_removed}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return True, (f"{n} 삭제 — 사용자 DB는 {os.path.basename(_moved) or '(없음)'}로 보관, "
                  f"예약 작업 {_cron_removed}개 정리")
