"""
DB 공통 — 경로 상수 및 연결 헬퍼
모든 db_*.py 모듈이 이 파일을 import.
"""
import os
import time
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUTH_DB  = os.path.join(DATA_DIR, "auth.db")
os.makedirs(DATA_DIR, exist_ok=True)

AUTH_TIMEOUT = 30.0   # 초. 기본 5초는 12시 크론 동시실행 구간에서 부족했다.


def _init_auth_pragmas():
    """auth.db를 WAL 모드로 전환 (파일 헤더에 영구 기록 — 1회만 실행되면 됨).

    왜 필요한가: auth.db는 전 사용자 공용이라 12시에 크론 태스크 수십 개와
    Streamlit 앱이 동시에 연다. 기본 rollback journal(delete) 모드에서는
    쓰기 1건이 모든 읽기를 막아, 5초 busy_timeout을 넘기면
    'database is locked'로 장보기 목록 관리자 제출이 통째로 실패했다.
    WAL은 읽기와 쓰기가 서로를 막지 않아 이 충돌이 사라진다.
    전환 실패해도 기존 모드로 계속 동작하므로 예외는 삼킨다.
    """
    try:
        conn = sqlite3.connect(AUTH_DB, timeout=AUTH_TIMEOUT)
        try:
            if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


_init_auth_pragmas()


def get_auth_db(timeout=AUTH_TIMEOUT, row=False):
    """auth.db 연결 — 넉넉한 busy_timeout 적용. 쓰기 경로는 이걸 쓸 것."""
    conn = sqlite3.connect(AUTH_DB, timeout=timeout, check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    conn.execute("PRAGMA synchronous=NORMAL")   # WAL에서 안전하면서 쓰기 지연↓
    if row:
        conn.row_factory = sqlite3.Row
    return conn


def retry_on_lock(fn, *args, attempts=4, base_delay=0.4, **kwargs):
    """'database is locked'만 지수 백오프로 재시도. 다른 오류는 즉시 전파.

    WAL로 대부분 사라지지만, 쓰기끼리 정면 충돌하는 순간은 남는다.
    """
    for _i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or _i == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** _i))


def get_user_db(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
