"""
DB 공통 — 경로 상수 및 연결 헬퍼
모든 db_*.py 모듈이 이 파일을 import.
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUTH_DB  = os.path.join(DATA_DIR, "auth.db")
os.makedirs(DATA_DIR, exist_ok=True)


def get_user_db(username):
    db_path = os.path.join(DATA_DIR, f"{username}.db")
    conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
