"""
공통 유틸리티 함수 — UI/DB 의존성 없음
app.py, auto_task.py, services.py 공용
"""
import re
from datetime import datetime, timedelta


def fmt(n):
    if n is None:
        return "-"
    return f"{int(n):,}"


def to_id_str(val):
    try:
        return str(int(float(val)))
    except Exception:
        return str(val).strip()


def extract_pack_qty(option_str, name_str=""):
    """옵션정보·상품명에서 묶음수량 추출 (예: '2구', '3개묶음', '1+1' → 2, 3, 2)"""
    text = f"{option_str or ''} {name_str or ''}".strip()
    if not text:
        return 1
    m = re.search(r'(\d)\s*\+\s*(\d)', text)
    if m:
        v = int(m.group(1)) + int(m.group(2))
        if 1 < v <= 30:
            return v
    for pat in [r'(\d+)\s*구\b', r'(\d+)\s*개\s*묶음', r'(\d+)\s*개\s*세트',
                r'(\d+)\s*p(?:ack)?\b', r'(\d+)\s*set\b', r'x\s*(\d+)\b']:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = int(m.group(1))
            if 1 < v <= 30:
                return v
    return 1


def clean_name(name):
    import pandas as pd
    if pd.isna(name) or not isinstance(name, str):
        return ""
    name = str(name).replace("'", "").replace("‘", "").replace("’", "")
    return name.replace(" ", "").lower()


def has_meaningful_char(s):
    korean = sum(1 for c in s if '가' <= c <= '힣')
    english = sum(1 for c in s if c.isalpha() and not ('가' <= c <= '힣'))
    return korean >= 1 or english >= 3


def get_ngrams(name, n):
    cleaned = clean_name(name)
    if len(cleaned) < n:
        return set()
    return set(cleaned[i:i+n] for i in range(len(cleaned) - n + 1)
               if has_meaningful_char(cleaned[i:i+n]))


def calc_match_score(name_a, name_b):
    match_3 = get_ngrams(name_a, 3) & get_ngrams(name_b, 3)
    s3 = len(match_3)
    if s3 == 0:
        return 0
    s5 = len(get_ngrams(name_a, 5) & get_ngrams(name_b, 5))
    return s3 + s5 * 3


MIN_MATCH_SCORE = 1


def get_week_range():
    today = datetime.today()
    mon = today - timedelta(days=today.weekday())
    return mon.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def get_month_range():
    today = datetime.today()
    return today.strftime("%Y-%m-01"), today.strftime("%Y-%m-%d")
