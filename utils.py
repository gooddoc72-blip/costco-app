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
    """옵션정보에서만 묶음수량 추출 (예: '2구', '3개묶음', '1+1' → 2, 3, 2).

    상품명(name_str)의 'x N개' 패턴은 상품 속성(예: '2.49kg x 144개' 박스 정보)이지
    주문 처리 변수가 아니므로 자동 추출하지 않음. 상품 속성 ≠ 주문 수량.
    """
    text = (option_str or '').strip()
    if not text:
        return 1
    m = re.search(r'(\d)\s*\+\s*(\d)', text)
    if m:
        v = int(m.group(1)) + int(m.group(2))
        if 1 < v <= 30:
            return v
    for pat in [r'(\d+)\s*구\b', r'(\d+)\s*개\s*묶음', r'(\d+)\s*개\s*세트',
                r'(\d+)\s*p(?:ack)?\b', r'(\d+)\s*set\b',
                r'\bx\s*(\d+)(?!\d)', r'×\s*(\d+)(?!\d)']:
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


class ProductMatcher:
    """지능형 상품 매칭 엔진"""
    _BRAND_MAP = {
        'kirkland': '커클랜드', 'starbucks': '스타벅스', 'merci': '메르시',
        'swissmiss': '스위스미스', 'godiva': '고디바', 'cetaphil': '세타필',
        'neutrogena': '뉴트로지나', 'nutrigrain': '뉴트리그레인', 'caprisun': '카프리썬',
        'perrier': '페리에', 'sanpellegrino': '산펠레그리노', 'evian': '에비앙',
        'philips': '필립스', 'braun': '브라운', 'dyson': '다이슨',
        'bounty': '바운티', 'downy': '다우니', 'tide': '타이드',
    }
    _BRAND_MAP.update({v: k for k, v in _BRAND_MAP.items()})

    @classmethod
    def normalize_name(cls, name):
        if not name: return ""
        name = str(name).lower()
        name = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', name)
        name = re.sub(r'코스트코|대행|무료배송|특가|할인|정품|공식', '', name)
        for k, v in cls._BRAND_MAP.items():
            name = name.replace(k, v)
        name = re.sub(r'(\d+)\s*(kg|g|ml|l|매|개|팩|장|봉|p|set)', r'\1\2', name)
        return re.sub(r'[^\w가-힣]', '', name)

    @classmethod
    def extract_features(cls, name):
        cn = cls.normalize_name(name)
        if not cn: return None
        tn = set(cn[i:i+2] for i in range(len(cn)-1)) if len(cn) >= 2 else set([cn])
        sn = set(re.findall(r'\d+(?:g|kg|ml|l|매|개|팩|장|봉|p|set)', cn))
        
        # Extract brands found
        bn = set()
        for k in cls._BRAND_MAP.values():
            if k in cn:
                bn.add(k)

        return {"cn": cn, "tn": tn, "sn": sn, "bn": bn}

    @classmethod
    def get_score_from_features(cls, fa, fb):
        if not fa or not fb: 
            return {"total": 0, "jaccard": 0, "token": 0, "keyword": 0, "size": 0, "brand": 0}
            
        ta, tb = fa["tn"], fb["tn"]
        intersection = ta & tb
        union = ta | tb
        if not union: 
            return {"total": 0, "jaccard": 0, "token": 0, "keyword": 0, "size": 0, "brand": 0}
            
        jaccard = len(intersection) / len(union)
        token_score = len(intersection) / min(len(ta), len(tb))
        
        sa, sb = fa["sn"], fb["sn"]
        penalty = 1.0
        size_score = 1.0 if sa and sb and (sa & sb) else 0.0
        if sa and sb and not (sa & sb):
            # 규격(수량, 용량)이 다른 경우 확실히 분리하기 위해 강력한 페널티 (0.1)
            penalty = 0.1
            
        ba, bb = fa["bn"], fb["bn"]
        brand_score = 1.0 if ba and bb and (ba & bb) else 0.0
        if ba and bb and not (ba & bb):
            # 브랜드가 다른 경우에도 강력한 페널티 (0.2)
            penalty *= 0.2
            
        total = (jaccard * 0.4 + token_score * 0.6) * penalty
        return {"total": total, "jaccard": jaccard, "token": token_score, "keyword": token_score, "size": size_score, "brand": brand_score}

    @classmethod
    def get_score(cls, a, b):
        fa = cls.extract_features(a)
        fb = cls.extract_features(b)
        return cls.get_score_from_features(fa, fb)


MIN_MATCH_SCORE = 1


def get_week_range():
    today = datetime.today()
    mon = today - timedelta(days=today.weekday())
    return mon.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def get_month_range():
    today = datetime.today()
    return today.strftime("%Y-%m-01"), today.strftime("%Y-%m-%d")
