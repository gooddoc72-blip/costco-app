"""
비즈니스 로직 레이어 — UI 의존성 없음
DB 읽기/쓰기는 db.py를 통해, 유틸은 utils.py를 통해 처리
"""
import re
import io
from functools import lru_cache
from datetime import datetime

import pandas as pd

from utils import calc_match_score, MIN_MATCH_SCORE, extract_pack_qty, get_ngrams, ProductMatcher
from db import (
    get_shared_products, get_all_products, get_all_products_merged,
    upsert_user_private,
    save_daily_orders as _db_save_daily_orders,
    save_order_history as _db_save_order_history,
)


# ── 매칭 가속 — list → dict 인덱스 캐시 (id 기반) ─────────
# 같은 list 객체에 대해 product_no/match_keyword dict을 1회만 생성하고 재사용.
# cached_user_products / cached_shared_products의 ttl 갱신 시 list가 새로 생성되어
# id 변경 → 자동 무효화.
_INDEX_CACHE: dict = {}

# ── 공유 네이버↔코스트코 매핑 캐시 ─────────────────────────
# 수동매칭으로 공유DB(shared_naver_map)에 누적된 {네이버번호: 코스트코번호} 맵.
# 주문의 네이버번호가 공유상품(코스트코번호)으로 직접 안 잡힐 때 이 맵으로 해석한다.
_SHARED_NAVER_MAP: dict = {'v': None}

def get_shared_naver_map() -> dict:
    if _SHARED_NAVER_MAP['v'] is None:
        try:
            from db_products import get_shared_naver_costco_map
            _SHARED_NAVER_MAP['v'] = get_shared_naver_costco_map() or {}
        except Exception:
            _SHARED_NAVER_MAP['v'] = {}
    return _SHARED_NAVER_MAP['v']

def invalidate_shared_naver_map():
    _SHARED_NAVER_MAP['v'] = None

def _index_products(products: list) -> dict:
    if not products:
        return {'by_pno': {}, 'by_kw': {}, 'has_pno': [], 'by_naver_pno': {}, 'by_naver_channel': {}}
    pid = id(products)
    hit = _INDEX_CACHE.get(pid)
    if hit is not None and hit.get('_n') == len(products):
        return hit
    out = {
        'by_pno': {str(p.get('product_no', '') or ''): p
                   for p in products if (p.get('product_no') or '').strip()},
        'by_kw':  {p['match_keyword']: p for p in products if p.get('match_keyword')},
        'has_pno': [p for p in products if (p.get('product_no') or '').strip()],
        # 네이버 origin번호 인덱스 — 하위호환(소분/묶음 split_qty 정확 매칭용)
        'by_naver_pno': {str(p.get('naver_origin_pno', '') or ''): p
                         for p in products if (p.get('naver_origin_pno') or '').strip()},
        # 네이버 channel번호 인덱스 — 주문(productId=channel) 정확 매칭용 (수익계산 1순위)
        'by_naver_channel': {str(p.get('naver_channel_pno', '') or ''): p
                             for p in products if (p.get('naver_channel_pno') or '').strip()},
        '_n': len(products),
    }
    if len(_INDEX_CACHE) > 16:  # 메모리 폭주 방지 — 16개 사용자 동시 캐시면 충분
        _INDEX_CACHE.clear()
    _INDEX_CACHE[pid] = out
    return out


# ── 묶음배수 해석 (단일 진실공급원) ────────────────────────
_PACK_RE = re.compile(r'x\s*(\d+)\s*개', re.IGNORECASE)


def pack_factor_from_name(product_name: str) -> int:
    """상품명 "x N개" → 배수. 2~50만 인정(그 밖은 1). 기존 로직 그대로."""
    m = _PACK_RE.search(str(product_name or ''))
    v = int(m.group(1)) if m else 1
    return v if 1 < v <= 50 else 1


def resolve_pack_factor(product: dict, product_name: str) -> int:
    """1주문이 소비하는 개수(소분 단위 기준).

    상품명의 "x N개"는 상품마다 뜻이 다르다:
      · 신라면 120g x 30개 → 30개들이 1박스 (내용물 설명) → 곱하면 안 됨
      · 그릭요거트 907g x 2개 → 소분 2개를 함께 판매      → 2배
    상품명만으로는 구분이 불가능해서 products.pack_multiplier로 명시한다.

      0 = 미지정 → 상품명에서 추출 (기존 동작 유지 — 수치 변화 없음)
      1 = 내용물 설명 → 1 (곱하지 않음)
      N = N개 묶음    → N
    """
    try:
        pm = int((product or {}).get('pack_multiplier', 0) or 0)
    except (TypeError, ValueError):
        pm = 0
    if pm >= 1:
        return min(pm, 50)
    return pack_factor_from_name(product_name)


# ── 비용 계산 (단일 공식) ──────────────────────────────────
def calc_cost(product: dict, qty: int, pack_qty: int = 1) -> int:
    """제품 dict + 수량 → 매입가.
    split_qty: 소분 단위 (예: 박스→3소분이면 단가//3)
    pack_qty:  묶음 수량 (예: 2구이면 qty*2 매입)
    """
    unit_price = int(product.get('unit_price', 0) or 0)
    split_qty = max(1, int(product.get('split_qty', 1) or 1))
    return (unit_price // split_qty) * qty * pack_qty


def get_cross_surcharge_map(username, df):
    """타인 재고로 나간 주문의 웃돈(구입가+500) 조회 — {주문번호: 웃돈합계}.

    재고 관리 대상이 아니거나 자기 재고로 나갔으면 비어 있다.
    """
    if df is None or df.empty:
        return {}
    col = None
    for c in ('상품주문번호', 'order_no', '주문번호'):
        if c in df.columns:
            col = c
            break
    if not col:
        return {}
    try:
        from db_inventory import get_surcharge_map
        onos = [str(x).strip() for x in df[col].tolist() if str(x).strip()]
        return get_surcharge_map(username, onos) or {}
    except Exception:
        return {}


def compute_costs_for_df(username, df, _user_prods=None, _shared_prods=None):
    """주문 DataFrame에 '구입가격' 컬럼을 일관된 공식으로 채워서 반환.
    이미 '구입가격' 컬럼이 있으면 0인 행만 채움 (수동 수정 보존).
    네이버/쿠팡/엑셀 모든 경로에서 동일한 매칭+계산 로직을 사용하도록 단일화.

    타인 재고로 나간 건은 구입가격에 웃돈(+500/개)이 더해진다.
    """
    if df is None or df.empty:
        return df
    user_prods = _user_prods if _user_prods is not None else get_all_products(username)
    shared_prods = _shared_prods if _shared_prods is not None else get_shared_products()
    _sur_map = get_cross_surcharge_map(username, df)
    _ono_col = None
    if _sur_map:
        for c in ('상품주문번호', 'order_no', '주문번호'):
            if c in df.columns:
                _ono_col = c
                break

    has_cost = '구입가격' in df.columns
    has_pno = '상품번호' in df.columns
    costs = []
    sqtys = []
    for _, r in df.iterrows():
        _existing_int = 0
        if has_cost:
            try:
                _existing_int = int(r.get('구입가격') or 0)
            except (TypeError, ValueError):
                _existing_int = 0
        name = str(r.get('상품명', '') or '')
        pno = str(r.get('상품번호', '') or '').strip() if has_pno else ''
        qty = 1
        try:
            qty = max(1, int(r.get('수량', 1) or 1))
        except (TypeError, ValueError):
            pass
        p = match_product_to_db(username, name, product_no=pno or None,
                                _user_prods=user_prods, _shared_prods=shared_prods)
        # 묶음배수 — 제품에 지정돼 있으면 그 값, 없으면 상품명에서 추출 (resolve_pack_factor)
        _sell_factor = resolve_pack_factor(p, name)
        # 기존 구입가격이 있으면 그대로 사용 (sqtys는 항상 append — 길이 불일치 방지)
        _c = _existing_int if _existing_int > 0 else (calc_cost(p, qty * _sell_factor) if p else 0)
        # 타인 재고로 나간 건 — 실제 원가는 구입가+500/개. 안 더하면 수익이 부풀려짐.
        if _sur_map and _ono_col and _existing_int <= 0:
            _c += int(_sur_map.get(str(r.get(_ono_col, '') or '').strip(), 0) or 0)
        costs.append(_c)
        sqtys.append(max(1, int((p or {}).get('split_qty', 1) or 1)))
    df = df.copy()
    df['구입가격'] = costs
    df['소분단위'] = sqtys
    return df


def process_and_save_orders(username, df, order_date, shipping_cost, box_cost,
                             save_history=True, save_daily=True):
    """주문 DataFrame 통합 저장 — 모든 경로(네이버/쿠팡/엑셀/auto_task)의 단일 진입점.
    1) 매입가 일관 계산 (calc_cost: split_qty 적용)
    2) save_daily_orders (수익 계산용, save_daily=True일 때만)
    3) save_order_history (송장 추적용, save_history=True일 때)
    4) update_product_info_from_orders (배송비/판매가 자동 갱신)

    반환: dict { 'orders': 저장된 행 수, 'history': 이력 저장 수,
                'fee_updates': 배송비 업데이트, 'sale_updates': 판매가 업데이트,
                'df': 구입가격 채워진 DataFrame }
    """
    result = {'orders': 0, 'history': 0, 'fee_updates': 0, 'sale_updates': 0, 'df': df}
    if df is None or df.empty:
        return result

    # 1) 매입가 계산
    df_with_costs = compute_costs_for_df(username, df)
    result['df'] = df_with_costs

    # 2) daily_orders 저장 (save_daily=True일 때만 — 사용자 명시적 저장 시점)
    if save_daily:
        try:
            _db_save_daily_orders(username, order_date, df_with_costs,
                                  int(shipping_cost), int(box_cost))
            result['orders'] = len(df_with_costs)
        except Exception as e:
            result['error_orders'] = str(e)
    else:
        result['orders'] = len(df_with_costs)  # 표시용 카운트만 채움

    # 3) order_history 저장
    if save_history:
        try:
            saved = _db_save_order_history(username, df_with_costs, cost_df=df_with_costs)
            result['history'] = saved or 0
        except Exception as e:
            result['error_history'] = str(e)

    # 4) 제품 DB 자동 업데이트 (배송비, 판매가)
    try:
        fee_cnt, sale_cnt = update_product_info_from_orders(username, df_with_costs)
        result['fee_updates'] = fee_cnt
        result['sale_updates'] = sale_cnt
    except Exception as e:
        result['error_product_update'] = str(e)

    return result


# ── 상품 매칭 ─────────────────────────────────────────────
# 토큰 매칭용 상수 (모듈 레벨 — 특징 사전계산 캐시와 공유)
_GENERIC = {
    '주스','과자','음료','우유','요거트','요구르트','빵','쿠키','초콜릿','초코',
    '코스트코','커클랜드','대용량','소용량','선물','세트','과일','채소','고기','육포',
    '신상','특가','행사','할인','정품','한정','베스트','정도','신선','냉장','냉동'
}
_STORE = {'코스트코','커클랜드','대용량','소용량','선물','세트','신상',
          '특가','행사','할인','정품','한정','베스트','신선','냉장','냉동'}
_TOKEN_RE = re.compile(r'[가-힣a-zA-Z0-9]+')
_SIZE_RE = re.compile(r'\d+\s*(?:g|kg|ml|l|개|팩|매|장|봉)')


@lru_cache(maxsize=16384)
def _token_features(s: str):
    """문자열의 토큰 매칭 특징을 1회만 계산·캐시.
    반환: (meaningful: frozenset, sizes: frozenset, core: str)
    noise(길이<2·숫자시작)는 토큰별 성질이라 쌍(pair)과 무관하게 사전계산 가능 →
    _token_score 원본과 수학적으로 동일한 meaningful/core를 산출한다.
    """
    s_low = (s or '').lower()
    toks = _TOKEN_RE.findall(s_low)
    ts = set(toks)
    noise = frozenset(t for t in ts if len(t) < 2 or t[0].isdigit())
    meaningful = frozenset(ts - _GENERIC - noise)
    sizes = frozenset(_SIZE_RE.findall(s_low))
    core = ''.join(t for t in toks if t not in noise and t not in _STORE)
    return (meaningful, sizes, core)


def _token_score_ff(fa, fb) -> float:
    """사전계산된 특징 2개로 점수 계산 — _token_score 원본과 동일 결과."""
    ma, sa, ca = fa
    mb, sb, cb = fb
    if not ma or not mb:
        return 0.0
    mcommon = ma & mb
    a_left = ma - mcommon
    b_left = mb - mcommon
    extra = 0
    for t in a_left:
        if len(t) >= 3:
            for t2 in b_left:
                if len(t2) >= 3 and (t in t2 or t2 in t):
                    extra += 0.5
                    break
    denom = min(len(ma), len(mb))
    score = (len(mcommon) + extra) / max(denom, 1)
    if sa and sb and not (sa & sb):
        score *= 0.3
    if len(ca) >= 4 and len(cb) >= 4:
        _short, _long = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
        if len(_short) >= 4 and _short in _long:
            score = max(score, 0.7)
    return min(1.0, score)


@lru_cache(maxsize=16384)
def _token_score(a: str, b: str) -> float:
    """가중 토큰 매칭 (특징 사전계산 캐시 사용 — 결과 불변, 반복 호출 시 대폭 빠름).
    Jaccard + 사이즈 페널티 + 일반어 가중치 감소 + 부분 substring + 붙임/띄어쓰기 연결매칭.
    """
    return _token_score_ff(_token_features(a), _token_features(b))


def _find_db_product(products, order_name: str, order_pno: str = '', pno_map: dict = None):
    if order_pno and pno_map:
        p = pno_map.get(str(order_pno).strip())
        if p:
            return p
    best_score, best_p = 0.0, None
    for p in products:
        for field in ('store_product_name', 'match_keyword', 'costco_name'):
            score = _token_score(order_name, p.get(field) or '')
            if score > best_score:
                best_score, best_p = score, p
    return best_p if best_score >= 0.5 else None


def match_product_to_db(username, store_product_name, product_no=None,
                        _user_prods=None, _shared_prods=None):
    """제품 매칭: shared_products 우선, 없으면 사용자 DB 폴백.
    _user_prods/_shared_prods: 배치 처리 시 미리 로드된 리스트 (N+1 방지용).

    ⭐ 0순위(소분): 개별DB에서 코스트코번호를 비우고 네이버번호+자체단가로 등록한
       '소분판매' 상품은, 네이버 상품번호로 매칭하고 그 자체 단가를 쓴다.
       (공유DB 이름매칭이 소분 가격을 가로채지 못하도록 최우선 처리)
    """
    if product_no:
        _uprods0 = _user_prods if _user_prods is not None else get_all_products(username)
        _uidx0 = _index_products(_uprods0)
        _nv0 = (_uidx0.get('by_naver_channel', {}).get(str(product_no))
                or _uidx0.get('by_naver_pno', {}).get(str(product_no)))
        if (_nv0 and not str(_nv0.get('product_no', '') or '').strip()
                and int(_nv0.get('unit_price') or 0) > 0):
            return dict(_nv0)

    sp = match_shared_product(store_product_name, product_no=product_no,
                               _shared_prods=_shared_prods)
    if sp:
        user_prods = _user_prods if _user_prods is not None else get_all_products(username)
        u_idx = _index_products(user_prods)
        up = u_idx['by_kw'].get(sp['match_keyword'], {})
        # 주문 product_no로 우선 탐색 — O(1)
        if product_no:
            # 1순위: naver_channel_pno — 주문 productId(=channel번호) 정확 매칭
            _up_by_naver = u_idx.get('by_naver_channel', {}).get(str(product_no))
            # 2순위: naver_origin_pno — 하위호환(과거 origin 저장분)
            if not _up_by_naver:
                _up_by_naver = u_idx.get('by_naver_pno', {}).get(str(product_no))
            if _up_by_naver:
                up = _up_by_naver
            else:
                # 3순위: product_no (코스트코 상품번호)
                _up_by_order_pno = u_idx['by_pno'].get(str(product_no))
                if _up_by_order_pno:
                    up = _up_by_order_pno
        # shared product_no로도 탐색 — O(1)
        if not up:
            _sp_pno = str(sp.get('product_no', '') or '')
            if _sp_pno:
                _up_by_pno = u_idx['by_pno'].get(_sp_pno)
                if _up_by_pno:
                    up = _up_by_pno
        _sq_user   = int(up.get('split_qty') or 1) if up else 1
        _sq_shared = int(sp.get('split_qty') or 1)
        # 사용자 DB 항목이 있으면 사용자 split_qty 우선 (소분/묶음 개별 설정 존중)
        # 항목 없으면 공유 DB 값 사용
        _sq = _sq_user if up else _sq_shared
        # unit_price: 공유DB(코스트코번호→가격)를 "정답"으로 최우선 사용 → 공유 수정이 즉시 반영.
        #   공유 가격이 없을(0) 때만 사용자 개인단가로 폴백. (기존엔 사용자단가 우선이라 공유 수정이 안 먹혔음)
        _up_unit_price = int(up.get('unit_price', 0) or 0) if up else 0
        _sp_unit_price = int(sp.get('unit_price', 0) or 0)
        _final_unit_price = _sp_unit_price if _sp_unit_price > 0 else _up_unit_price
        return {
            **sp,
            'unit_price':       _final_unit_price,
            'sale_price':       int(up.get('sale_price',   0) or 0) if up else 0,
            'shipping_fee':     int(up.get('shipping_fee', 0) or 0) if up else 0,
            'naver_product_no': up.get('product_no', '') if up else '',
            'split_qty':        _sq,
        }
    products = _user_prods if _user_prods is not None else get_all_products(username)
    if not products:
        return None
    # 공유 DB 미매칭 시에도 네이버 번호로 탐색 (channel 우선, origin 하위호환)
    if product_no:
        _u_idx2 = _index_products(products)
        _by_nv2 = (_u_idx2.get('by_naver_channel', {}).get(str(product_no))
                   or _u_idx2.get('by_naver_pno', {}).get(str(product_no)))
        if _by_nv2:
            _m_nv = dict(_by_nv2)
            # 네이버 listing 레코드(from_naver=1)는 매입가가 0인 경우가 많음 →
            # 같은 코스트코번호(product_no)의 매입가 레코드 / 공유상품에서 단가 보완
            if int(_m_nv.get('unit_price') or 0) == 0 and _m_nv.get('product_no'):
                _pno_key = str(_m_nv['product_no'])
                _cost_rec = next((p for p in products
                                  if str(p.get('product_no', '') or '') == _pno_key
                                  and int(p.get('unit_price') or 0) > 0), None)
                if _cost_rec:
                    _m_nv['unit_price'] = int(_cost_rec.get('unit_price') or 0)
                    if not _m_nv.get('split_qty') or int(_m_nv.get('split_qty') or 1) == 1:
                        _m_nv['split_qty'] = int(_cost_rec.get('split_qty') or 1)
                else:
                    _shared_p = _shared_prods if _shared_prods is not None else get_shared_products()
                    for sp in (_shared_p or []):
                        if str(sp.get('product_no', '') or '') == _pno_key:
                            _m_nv['unit_price'] = int(sp.get('unit_price') or 0)
                            if not _m_nv.get('split_qty') or int(_m_nv.get('split_qty') or 1) == 1:
                                _m_nv['split_qty'] = int(sp.get('split_qty') or 1)
                            break
            return _m_nv
    # product_no가 없거나 불일치 시 이름/키워드 토큰 매칭 (0.5 이상)
    candidates = []
    for p in products:
        s1 = _token_score(store_product_name, p.get('costco_name', ''))
        s2 = _token_score(store_product_name, p['match_keyword'])
        score = max(s1, s2)
        if score >= 0.5:
            candidates.append((p, score))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    matched = dict(candidates[0][0])
    # 사용자 상품의 unit_price가 0이고 product_no가 있으면 공유 상품에서 가격/split_qty 보완
    if (not matched.get('unit_price') or int(matched.get('unit_price') or 0) == 0) and matched.get('product_no'):
        shared_prods = _shared_prods if _shared_prods is not None else get_shared_products()
        for sp in (shared_prods or []):
            if str(sp.get('product_no', '') or '') == str(matched['product_no']):
                matched['unit_price'] = sp.get('unit_price') or 0
                # split_qty 미설정 시 공유 상품 명시값만 사용 (이름 자동추출 제거)
                if not matched.get('split_qty') or int(matched.get('split_qty') or 1) == 1:
                    matched['split_qty'] = int(sp.get('split_qty') or 1)
                break
    return matched


def match_shared_product(product_name, product_no=None, return_score=False,
                         _shared_prods=None):
    """이름·번호로 공유 제품 검색.
    product_no가 있으면 정확 일치 우선 (score=1.0), 그 외에는 강화된 토큰 점수 ≥ 0.6 필요.
    return_score=True면 (product, score) 튜플 반환.
    _shared_prods: 배치 처리 시 미리 로드된 리스트 (N+1 방지용).
    """
    products = _shared_prods if _shared_prods is not None else get_shared_products()
    if not products:
        return (None, 0.0) if return_score else None
    idx = _index_products(products)
    # 정확 일치 — O(1) dict lookup (코스트코번호 → 네이버 channel → origin 순)
    if product_no:
        hit = (idx['by_pno'].get(str(product_no))
               or idx.get('by_naver_channel', {}).get(str(product_no))
               or idx.get('by_naver_pno', {}).get(str(product_no)))
        if hit:
            return (hit, 1.0) if return_score else hit
        # 공유 네이버↔코스트코 매핑: 주문 네이버번호 → 코스트코번호 → 공유상품(=공유가격)
        _cp = get_shared_naver_map().get(str(product_no))
        if _cp:
            hit = idx['by_pno'].get(str(_cp))
            if hit:
                return (hit, 1.0) if return_score else hit
    best_score, best_p = 0.0, None
    for p in products:
        for field in ('match_keyword', 'costco_name'):
            score = _token_score(product_name, p.get(field) or '')
            if score > best_score:
                best_score, best_p = score, p
    if best_score >= 0.5:
        return (best_p, best_score) if return_score else best_p
    return (None, best_score) if return_score else None


# ── 주문에서 제품 정보 자동 업데이트 ─────────────────────
def update_product_info_from_orders(username, orders_df):
    """주문 목록 → 사용자 개인 DB의 shipping_fee·sale_price 동시 갱신."""
    if '상품명' not in orders_df.columns:
        return 0, 0
    merged = get_all_products_merged(username)
    if not merged:
        return 0, 0
    pno_map    = {str(p['product_no']): p for p in merged if p.get('product_no')}
    has_pno    = '상품번호' in orders_df.columns
    has_fee    = '배송비 합계' in orders_df.columns
    has_sprice = '상품가격' in orders_df.columns
    has_total  = '최종 상품별 총 주문금액' in orders_df.columns
    has_qty    = '수량' in orders_df.columns
    group_key  = ['상품명'] + (['상품번호'] if has_pno else [])
    agg_map    = {}
    for keys, grp in orders_df.groupby(group_key):
        if isinstance(keys, str):
            name, pno = keys, ''
        else:
            name = keys[0]
            pno  = str(keys[1]) if len(keys) > 1 else ''
        if has_fee:
            fees    = pd.to_numeric(grp['배송비 합계'], errors='coerce').fillna(0).astype(int)
            nz      = fees[fees > 0]
            fee_val = int(nz.max()) if len(nz) > 0 else 0
        else:
            fee_val = -1
        if has_sprice:
            prices = pd.to_numeric(grp['상품가격'], errors='coerce').fillna(0).astype(int)
        elif has_total and has_qty:
            totals = pd.to_numeric(grp['최종 상품별 총 주문금액'], errors='coerce').fillna(0).astype(int)
            qtys   = pd.to_numeric(grp['수량'], errors='coerce').fillna(1).astype(int).replace(0, 1)
            prices = totals // qtys
        else:
            prices = pd.Series([], dtype=int)
        nz_p     = prices[prices > 0] if len(prices) > 0 else pd.Series([], dtype=int)
        sale_val = int(nz_p.max()) if len(nz_p) > 0 else 0
        agg_map[(str(name), pno)] = {'fee': fee_val, 'sale': sale_val}
    fee_cnt = sale_cnt = 0
    for (name, pno), vals in agg_map.items():
        matched = _find_db_product(merged, name, pno, pno_map)
        if not matched:
            continue
        kw          = matched.get('match_keyword', '')
        costco_name = matched.get('costco_name', name)
        fee_val     = vals['fee']
        sale_val    = vals['sale']
        if fee_val >= 0 or sale_val > 0:
            upsert_user_private(
                username, kw, costco_name,
                sale_price=sale_val if sale_val > 0 else None,
                shipping_fee=fee_val if fee_val >= 0 else None,
            )
            if fee_val >= 0:  fee_cnt  += 1
            if sale_val > 0:  sale_cnt += 1
    return fee_cnt, sale_cnt


def update_product_shipping_fees(username, orders_df):
    fee_cnt, _ = update_product_info_from_orders(username, orders_df)
    return fee_cnt


def update_product_sale_price(username, orders_df):
    _, sale_cnt = update_product_info_from_orders(username, orders_df)
    return sale_cnt


# ── 가격 변동 감지 ────────────────────────────────────────
def detect_price_changes(username, parsed_items):
    """영수증 매입가 ↔ 기존 store_price (매장가) 비교.
    영수증 가격 = 매장 가격이므로 store_price만 비교 대상.
    online_price(코스트코몰)는 비교 무시.
    """
    shared     = get_shared_products()
    user_prods = get_all_products(username)
    user_fee_map = {up['match_keyword']: int(up.get('shipping_fee', 0) or 0) for up in user_prods}
    changes = []
    for item in parsed_items:
        receipt_name  = item.get('상품명', '')
        receipt_price = int(item.get('단가', 0))
        receipt_no    = str(item.get('상품번호', ''))
        if not receipt_name or receipt_price <= 0:
            continue
        sp = match_shared_product(receipt_name, product_no=receipt_no if receipt_no else None)
        if sp is None:
            continue
        # 매장가 우선, 없으면 (마이그레이션 전 데이터) unit_price 폴백
        old_price = int(sp.get('store_price') or 0)
        if old_price <= 0:
            # 폴백: price_type='매장'인 unit_price만 비교 대상
            if (sp.get('price_type') or '매장') == '매장':
                old_price = int(sp.get('unit_price') or 0)
        if old_price <= 0 or old_price == receipt_price:
            continue
        diff     = receipt_price - old_price
        diff_pct = round(diff / old_price * 100, 1)
        changes.append({
            'costco_name': sp.get('costco_name') or receipt_name,
            'old_cost': old_price, 'new_cost': receipt_price,
            'diff': diff, 'diff_pct': diff_pct,
            'product_no': sp.get('product_no', ''),
            'split_qty': int(sp.get('split_qty', 1) or 1),
            'shipping_fee': user_fee_map.get(sp['match_keyword'], 0),
            'shared_id': sp.get('id'),
        })
    return changes


def build_price_alert_msg(changes, today_str=None):
    if not today_str:
        today_str = datetime.now().strftime("%Y-%m-%d")
    up   = [c for c in changes if c['diff'] > 0]
    down = [c for c in changes if c['diff'] < 0]
    lines = [f"[코스트코 가격 변동 알림] {today_str}", ""]
    def fee_str(f):
        return "무료" if f == 0 else f"{f:,}원"
    if up:
        lines.append(f"🔺 가격 인상 ({len(up)}건)")
        for c in up:
            lines.append(f"• {c['costco_name']}\n"
                         f"  {c['old_cost']:,} → {c['new_cost']:,}원 (+{c['diff']:,}원, +{c['diff_pct']}%)\n"
                         f"  고객 배송비: {fee_str(c['shipping_fee'])}")
        lines.append("")
    if down:
        lines.append(f"🔻 가격 인하 ({len(down)}건)")
        for c in down:
            lines.append(f"• {c['costco_name']}\n"
                         f"  {c['old_cost']:,} → {c['new_cost']:,}원 ({c['diff']:,}원, {c['diff_pct']}%)\n"
                         f"  고객 배송비: {fee_str(c['shipping_fee'])}")
        lines.append("")
    lines.append("※ 앱 접속 후 네이버 판매가를 검토하고 적용해주세요.")
    return "\n".join(lines)


# ── 영수증 PDF 파싱 ───────────────────────────────────────
def parse_costco_receipt_pdf(uploaded_pdf):
    try:
        import pdfplumber
    except ImportError:
        return None, "pdfplumber 미설치 (pip install pdfplumber)"
    try:
        if hasattr(uploaded_pdf, 'read'):
            uploaded_pdf.seek(0)
            raw = io.BytesIO(uploaded_pdf.read())
        else:
            raw = uploaded_pdf
            raw.seek(0)
    except Exception as e:
        return None, f"파일 읽기 오류: {e}"
    try:
        with pdfplumber.open(raw) as pdf:
            full_text = "".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        return None, f"PDF 열기 오류: {e}"
    if not full_text.strip():
        return None, "텍스트 추출 실패 (스캔 이미지 PDF이거나 암호화된 파일)"

    lines = full_text.split('\n')

    # 영수증 날짜 추출 (YYYY/MM/DD, YYYY-MM-DD, YY/MM/DD 등)
    receipt_date = ''
    _date_re = re.compile(
        r'(?:날\s*자|날짜|date)[^\d]*(\d{2,4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})'
        r'|(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})',
        re.IGNORECASE
    )
    for _l in lines:
        _dm = _date_re.search(_l)
        if _dm:
            try:
                if _dm.group(1):   # 날자: YYYY/MM/DD 형식
                    y, mo, d = _dm.group(1), _dm.group(2), _dm.group(3)
                else:              # 순수 날짜 YYYY/MM/DD
                    y, mo, d = _dm.group(4), _dm.group(5), _dm.group(6)
                if len(y) == 2:
                    y = '20' + y
                receipt_date = f"{y}-{int(mo):02d}-{int(d):02d}"
                break
            except Exception:
                pass

    items, skip_next = [], False
    for i in range(len(lines) - 1):
        if skip_next:
            skip_next = False
            continue
        line, next_line = lines[i].strip(), lines[i + 1].strip()
        if line == '*** CPN':
            skip_next = True
            continue
        if 'CPN' in line or 'IRC' in line:
            continue
        m = re.match(r'^(\d{4,7})\s+(\d+)\s+([\d,]+)\s+([\d,\-\s]+)\s*[TFN]?\s*$', next_line)
        if m:
            name = line
            if any(x in name for x in ['코스트코코리아', '대표자', '부산시', '판매', '닫기', 'costco', 'http']):
                continue
            if not name or len(name) < 2:
                continue
            items.append({
                '상품번호': m.group(1),
                '상품명': name,
                '수량': int(m.group(2)),
                '단가': int(m.group(3).replace(',', '')),
                'receipt_date': receipt_date,
            })
            skip_next = True
    if items:
        return items, None
    preview = full_text[:800].strip()
    return None, f"상품 패턴 미인식 (텍스트 {len(full_text)}자 추출)\n\n--- 추출 원문 ---\n{preview}"


# 브랜드 영한 매핑 (네이버는 한글, 영수증은 영문 흔함)
_BRAND_MAP = {
    "MERCI":     "메르시",
    "KIRKLAND":  "커클랜드",
    "COSTCO":    "코스트코",
    "QUAKER":    "퀘이커",
    "LIPTON":    "립톤",
    "CJ":        "씨제이",
    "SAMYANG":   "삼양",
    "DOVE":      "도브",
    "HEAD":      "헤드",
    "PANTENE":   "팬틴",
    "KIRKWOOD":  "커크우드",
    "TYSON":     "타이슨",
    "NESTLE":    "네슬레",
    "FERRERO":   "페레로",
}

# 사이즈/수량 정규식 (단위 일관성)
_SIZE_PATTERN = re.compile(
    r'(\d+(?:\.\d+)?)\s*(kg|g|ml|l|개|장|매|봉|입|팩|세트|정|p)',
    re.IGNORECASE
)


def _trigrams_clean(s: str) -> set:
    """공백/특수문자 제거 후 trigram 집합"""
    s = re.sub(r'[^\w가-힣]', '', s.lower())
    return set(s[i:i+3] for i in range(len(s)-2)) if len(s) >= 3 else set()


def _jaccard_score(a: str, b: str) -> float:
    """trigram Jaccard 유사도 0~1 (양쪽 길이 차이 클 때 max-containment 보정)"""
    ta, tb = _trigrams_clean(a), _trigrams_clean(b)
    if not (ta | tb):
        return 0.0
    inter = len(ta & tb)
    jaccard = inter / len(ta | tb)
    # containment (한쪽이 다른 쪽에 얼마나 포함되는지)
    cont_a = inter / len(ta) if ta else 0
    cont_b = inter / len(tb) if tb else 0
    # 비대칭 길이 차이가 클 때 max(jaccard, max_containment*0.7) 사용
    return max(jaccard, max(cont_a, cont_b) * 0.7)


# 비교에서 제외할 일반 단어 (의미 없는 흔한 단어)
_STOP_WORDS = {
    "코스트코", "코스트코핫딜", "행사", "특가", "할인", "선물용", "대용량",
    "정품", "신상", "신상품", "한정", "베스트", "추천", "포함", "세트",
    "총", "당일출고", "오늘출발", "무료배송", "정식", "매장", "네이버",
    "총량", "낱개", "낱장", "묶음",
}


def _tokenize_keywords(s: str, min_len: int = 2) -> set:
    """상품명에서 키워드(2자 이상 단어) 추출. 사이즈/일반어 제외."""
    tokens = set()
    for m in re.findall(r'[\w가-힣]+', s.lower()):
        if len(m) < min_len:
            continue
        # 사이즈 패턴 제외 (예: "907g")
        if _SIZE_PATTERN.fullmatch(m):
            continue
        # 숫자만으로 된 토큰 제외 (수량 등)
        if m.isdigit():
            continue
        if m in _STOP_WORDS:
            continue
        tokens.add(m)
    return tokens


def _keyword_overlap_score(a: str, b: str) -> float:
    """키워드(단어 토큰) 매칭 비율 — 짧은 쪽 기준 containment.
    direct match: 정확히 같은 토큰 일치 (full credit)
    partial match: 한쪽 토큰이 다른 쪽 텍스트에 substring으로 포함 (×0.5 보수 적용)
    """
    ta, tb = _tokenize_keywords(a), _tokenize_keywords(b)
    if not ta or not tb:
        return 0.0
    overlap = ta & tb
    direct_count = len(overlap)
    # partial: direct에 없는 토큰들 중에서 substring으로 매칭되는 것
    a_str = ''.join(ta)
    b_str = ''.join(tb)
    partial_a = sum(1 for t in (ta - overlap) if len(t) >= 3 and t in b_str)
    partial_b = sum(1 for t in (tb - overlap) if len(t) >= 3 and t in a_str)
    # partial은 보수적 가중치 ×0.5 (양방향 평균 후 절반만 인정)
    partial_count = (partial_a + partial_b) / 2 * 0.5
    total_overlap = direct_count + partial_count
    return min(1.0, total_overlap / min(len(ta), len(tb)))


def _extract_sizes(s: str) -> set:
    """상품명에서 사이즈/수량 추출 → {(값, 단위), ...} 정규화"""
    found = set()
    for m in _SIZE_PATTERN.finditer(s.lower()):
        value, unit = m.group(1), m.group(2).lower()
        # 단위 정규화 (kg → g, l → ml)
        try:
            v = float(value)
        except Exception:
            continue
        if unit == "kg":
            v, unit = v * 1000, "g"
        elif unit == "l":
            v, unit = v * 1000, "ml"
        found.add((v, unit))
    return found


def _size_match_score(a: str, b: str) -> float:
    """사이즈 일치 점수
       - 양쪽 모두 사이즈 있고 일치: 1.0
       - 양쪽 모두 사이즈 있고 불일치: 0.0
       - 한쪽만 사이즈 있음: 0.4
       - 양쪽 모두 사이즈 없음: 0.5 (중립)
    """
    sa, sb = _extract_sizes(a), _extract_sizes(b)
    if not sa and not sb:
        return 0.5
    if sa and sb:
        return 1.0 if (sa & sb) else 0.0
    return 0.4


def _brand_match_score(a: str, b: str) -> float:
    """브랜드/주요 키워드 일치 (영한 매핑 포함)
       - 명확 일치: 1.0
       - 일치 없음: 0.0
    """
    a_l, b_l = a.lower(), b.lower()
    a_brands, b_brands = set(), set()
    for en, kr in _BRAND_MAP.items():
        en_l = en.lower()
        if en_l in a_l or kr in a:
            a_brands.add(en); a_brands.add(kr)
        if en_l in b_l or kr in b:
            b_brands.add(en); b_brands.add(kr)
    return 1.0 if (a_brands & b_brands) else 0.0


def _combined_match_score(a: str, b: str) -> dict:
    """가중 합산 점수 (0~1) + 페널티/보너스
       기본: 0.30×Jaccard + 0.25×키워드 + 0.25×사이즈 + 0.20×브랜드
       페널티:
         - 사이즈 양쪽 있는데 다름 → ×0.4
         - 브랜드만 같고 이름 매칭 모두 낮음 → ×0.7
       보너스:
         - 사이즈+브랜드 둘 다 정확 → +0.05
         - 키워드 ≥0.8 → +0.05
    """
    j = _jaccard_score(a, b)
    kw = _keyword_overlap_score(a, b)
    s = _size_match_score(a, b)
    br = _brand_match_score(a, b)
    total = 0.30 * j + 0.25 * kw + 0.25 * s + 0.20 * br

    # 페널티: 사이즈 명백히 다름
    sizes_a, sizes_b = _extract_sizes(a), _extract_sizes(b)
    if sizes_a and sizes_b and not (sizes_a & sizes_b):
        total *= 0.4

    # 페널티: 브랜드만 같고 이름 신호 모두 약함 (false positive 방지)
    if j < 0.20 and kw < 0.30 and br > 0:
        total *= 0.7

    # 보너스: 사이즈+브랜드 둘 다 정확
    if s == 1.0 and br == 1.0:
        total += 0.05
    # 보너스: 키워드 매칭 매우 강함
    if kw >= 0.8:
        total += 0.05

    total = max(0.0, min(1.0, total))
    return {"total": total, "jaccard": j, "keyword": kw, "size": s, "brand": br}


def match_receipt_to_naver_products(username, receipt_items, threshold=0.30):
    """영수증 상품 ↔ 사용자 네이버 등록 제품 매칭.
    이미 product_no가 DB에 저장된 영수증 항목은 매칭에서 제외 (중복 방지).
    """
    from db import get_user_db

    conn = get_user_db(username)
    # 매칭 후보 — product_no 비어있는 사용자 상품
    candidates = conn.execute("""
        SELECT id, match_keyword, costco_name, product_no, COALESCE(from_naver, 0) as from_naver
        FROM products
        WHERE product_no IS NULL OR product_no = ''
    """).fetchall()
    candidates = [dict(c) for c in candidates]

    # 이미 DB에 저장된 product_no 집합 — 영수증 항목 중 이 번호와 같으면 매칭 제외
    rows_existing = conn.execute(
        "SELECT product_no FROM products WHERE product_no IS NOT NULL AND product_no != ''"
    ).fetchall()
    existing_pnos = {str(r['product_no']).strip() for r in rows_existing if r['product_no']}
    conn.close()

    # 후보의 특징을 미리 추출하여 속도 개선
    for c in candidates:
        user_name = c['match_keyword'] or c['costco_name'] or ''
        c['features'] = ProductMatcher.extract_features(user_name)

    matched = []
    unmatched_receipt = []
    skipped_already = []     # 이미 DB에 product_no가 있어서 제외된 영수증 항목
    used_user_ids = set()

    for r in receipt_items:
        receipt_name = (r.get('상품명') or '').strip()
        receipt_pno  = str(r.get('상품번호') or '').strip()
        if not receipt_name or not receipt_pno:
            continue

        # 영수증 단가 = 매입가격 (제품 DB에 함께 반영)
        try:
            receipt_price = int(float(r.get('단가') or 0))
        except Exception:
            receipt_price = 0

        # 이미 DB에 동일 product_no 있으면 매칭 시도 자체를 건너뜀
        if receipt_pno in existing_pnos:
            skipped_already.append(r)
            continue

        receipt_features = ProductMatcher.extract_features(receipt_name)
        best, best_score_info = None, None
        
        for c in candidates:
            if c['id'] in used_user_ids:
                continue
            
            si = ProductMatcher.get_score_from_features(receipt_features, c['features'])
            if best_score_info is None or si["total"] > best_score_info["total"]:
                best, best_score_info = c, si

        if best and best_score_info and best_score_info["total"] >= threshold:
            sc = best_score_info["total"]
            if sc >= 0.70:
                tier = "확실"
            elif sc >= 0.50:
                tier = "유력"
            else:
                tier = "참고"
            matched.append({
                'user_id':       best['id'],
                'user_kw':       best['match_keyword'],
                'receipt_name':  receipt_name,
                'costco_pno':    receipt_pno,
                'unit_price':    receipt_price,
                'score':         sc,
                'jaccard':       best_score_info["jaccard"],
                'keyword_score': best_score_info["keyword"],
                'size_score':    best_score_info["size"],
                'brand_score':   best_score_info["brand"],
                'tier':          tier,
            })
            used_user_ids.add(best['id'])
        else:
            unmatched_receipt.append(r)

    matched.sort(key=lambda x: -x['score'])
    return {
        'matched':           matched,
        'unmatched_receipt': unmatched_receipt,
        'skipped_already':   skipped_already,
        'candidates_count':  len(candidates),
    }


def apply_receipt_pno_updates(username, matched_list):
    """match_receipt_to_naver_products() 결과를 DB에 적용.
    user products의 product_no(상품번호) + unit_price(매입가)를 영수증 값으로 업데이트.

    안전장치: 영수증 단가가 기존 판매가의 5배를 초과하면 박스 단위 매입가로 판단하여
    매입가 자동 적용을 거부 (product_no만 갱신). 사용자가 수동 정정 필요.

    매입가가 갱신된 상품에 대해 저장된 daily_orders의 cost_price·profit도 자동 재계산.
    """
    from db import get_user_db, recalc_daily_orders_for_products
    if not matched_list:
        return 0
    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cnt = 0
    price_updated_pnos = []
    skipped_box_price = []  # 박스가격 의심으로 매입가 미적용된 상품
    for m in matched_list:
        _price   = int(m.get('unit_price') or 0)
        _user_id = m['user_id']

        # 안전장치: 기존 sale_price/unit_price 와 비교해 박스 단가 의심 시 매입가 적용 거부
        _row = conn.execute(
            "SELECT sale_price, unit_price FROM products WHERE id=?", (_user_id,)
        ).fetchone()
        _sale  = int((_row[0] if _row else 0) or 0)
        _old_p = int((_row[1] if _row else 0) or 0)
        _is_box_suspicion = False
        if _price > 0:
            # 판매가의 5배 초과: 거의 확실히 박스 단가
            if _sale > 0 and _price > _sale * 5:
                _is_box_suspicion = True
            # 절대 임계 (200,000원 초과): sale_price 없을 때 폴백 검증
            elif _sale == 0 and _price > 200000:
                _is_box_suspicion = True

        if _price > 0 and not _is_box_suspicion:
            conn.execute(
                "UPDATE products SET product_no=?, unit_price=?, updated_at=? WHERE id=?",
                (m['costco_pno'], _price, now, _user_id)
            )
            price_updated_pnos.append(m['costco_pno'])
        else:
            conn.execute(
                "UPDATE products SET product_no=?, updated_at=? WHERE id=?",
                (m['costco_pno'], now, _user_id)
            )
            if _is_box_suspicion:
                skipped_box_price.append({
                    'user_id':      _user_id,
                    'pno':          m['costco_pno'],
                    'receipt_name': m.get('receipt_name', ''),
                    'rejected_price': _price,
                    'sale_price':   _sale,
                    'kept_price':   _old_p,
                })
            else:
                # 매입가 0이어도 product_no가 새로 등록됐으니 daily_orders 재계산 시도
                price_updated_pnos.append(m['costco_pno'])
        cnt += 1
    conn.commit()
    conn.close()

    # 박스 단가 의심으로 거부된 항목은 모듈 변수에 저장 (UI에서 표시 가능)
    global _last_skipped_box_prices
    _last_skipped_box_prices = skipped_box_price

    # 저장된 정산이력(daily_orders)에 매입가 변경 즉시 반영
    try:
        recalc_daily_orders_for_products(username, price_updated_pnos)
    except Exception:
        pass

    return cnt


_last_skipped_box_prices = []


def get_last_skipped_box_prices():
    """직전 apply_receipt_pno_updates에서 박스 단가 의심으로 거부된 항목 반환."""
    return list(_last_skipped_box_prices)


def apply_receipt_to_unmatched_daily_orders(username, unmatched_receipt_items, order_date):
    """영수증 항목 중 naver products에 매칭 안된 항목을 해당 날짜 daily_orders와 교차매칭.
    매칭 성공 시: products DB에 product_no + unit_price 등록, daily_orders.cost_price 갱신.

    Returns: list of {receipt_name, order_name, product_no, unit_price, status}
    """
    from db import get_user_db, get_daily_orders
    if not unmatched_receipt_items:
        return []

    daily_rows = get_daily_orders(username, order_date)
    if not daily_rows:
        return []

    conn = get_user_db(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        s_row = conn.execute("SELECT value FROM settings WHERE key='shipping_cost'").fetchone()
        b_row = conn.execute("SELECT value FROM settings WHERE key='box_cost'").fetchone()
        shipping_cost = int(s_row[0]) if s_row and s_row[0] else 1800
        box_cost      = int(b_row[0]) if b_row and b_row[0] else 300
    except Exception:
        shipping_cost, box_cost = 1800, 300

    results = []
    used_order_ids = set()

    for receipt_item in unmatched_receipt_items:
        receipt_name  = (receipt_item.get('상품명') or '').strip()
        receipt_pno   = str(receipt_item.get('상품번호') or '').strip()
        try:
            receipt_price = int(float(receipt_item.get('단가') or 0))
        except Exception:
            receipt_price = 0
        if not receipt_name or not receipt_pno or receipt_price <= 0:
            continue

        # daily_orders 중 이름 유사도 최고점 탐색 (cost_price=0인 미매칭 우선)
        best_order, best_score = None, 0.0
        for order_row in daily_rows:
            if order_row['id'] in used_order_ids:
                continue
            score = calc_match_score(receipt_name, order_row.get('product_name', ''))
            if score > best_score:
                best_score, best_order = score, order_row

        if best_order is None or best_score < MIN_MATCH_SCORE:
            results.append({'receipt_name': receipt_name, 'order_name': '', 'product_no': receipt_pno,
                             'unit_price': receipt_price, 'status': '미매칭'})
            continue

        order_pno  = str(best_order.get('product_no') or '').strip()
        order_name = best_order.get('product_name', '')

        # 박스 단가 체크
        existing = conn.execute(
            "SELECT id, sale_price, unit_price FROM products WHERE product_no=?", (order_pno,)
        ).fetchone() if order_pno else None
        _sale = int((existing['sale_price'] if existing else 0) or 0)
        is_box = (_sale > 0 and receipt_price > _sale * 5) or (_sale == 0 and receipt_price > 200000)

        if is_box:
            results.append({'receipt_name': receipt_name, 'order_name': order_name, 'product_no': order_pno,
                             'unit_price': receipt_price, 'status': '박스단가의심(스킵)'})
            continue

        # products DB upsert
        if existing:
            conn.execute("UPDATE products SET unit_price=?, updated_at=? WHERE id=?",
                         (receipt_price, now, existing['id']))
        else:
            conn.execute("""INSERT INTO products
                (product_no, store_product_name, costco_name, match_keyword, unit_price, split_qty, shipping_fee, updated_at)
                VALUES (?,?,?,?,?,1,0,?)""",
                (order_pno or receipt_pno, receipt_name, receipt_name, receipt_name, receipt_price, now))

        # daily_orders cost_price 재계산 (실정산배송비 = 네이버 수수료 차감 → 수익계산과 일치)
        settlement = int(best_order.get('settlement') or 0)
        ship_fee   = int(best_order.get('shipping_fee') or 0)
        from db_orders import _ship_settle_factor as _ssf
        profit     = (settlement + round(ship_fee * _ssf(conn))) - (receipt_price + shipping_cost + box_cost)
        conn.execute("UPDATE daily_orders SET cost_price=?, profit=?, matched=1 WHERE id=?",
                     (receipt_price, profit, best_order['id']))
        used_order_ids.add(best_order['id'])
        results.append({'receipt_name': receipt_name, 'order_name': order_name,
                         'product_no': order_pno or receipt_pno, 'unit_price': receipt_price, 'status': '등록완료'})

    conn.commit()
    conn.close()
    return results


def match_receipt_to_orders(receipt_items, order_product_names, pno_map=None):
    """
    receipt_items: 영수증 파싱 결과 (상품번호, 상품명, 단가 등)
    order_product_names: 주문 상품명 리스트
    pno_map: {product_no: [order_product_name, ...]} — 상품번호 우선 매칭용
    매칭 순서: 1) 상품번호 직접 매칭  2) 상품명 특징(ProductMatcher) 매칭
    """
    from utils import ProductMatcher
    matches = {}

    # Stage 1: 상품번호 직접 매칭 (정확도 최우선)
    if pno_map:
        rcpt_by_pno = {str(ri.get('상품번호', '') or ''): ri
                       for ri in receipt_items if ri.get('상품번호')}
        for pno, order_names in pno_map.items():
            rcpt_item = rcpt_by_pno.get(str(pno))
            if rcpt_item:
                for order_name in order_names:
                    if order_name not in matches:
                        matches[order_name] = rcpt_item

    # Stage 2: 상품명 특징 매칭 (상품번호 미매칭 항목만)
    remaining = [n for n in order_product_names if n not in matches]
    if remaining:
        # 영수증 항목의 특징을 미리 추출
        rcpt_features = [ProductMatcher.extract_features(item.get('상품명', '')) for item in receipt_items]
        
        for store_name in remaining:
            sf = ProductMatcher.extract_features(store_name)
            if not sf: continue
            
            best_idx, best_score = None, 0.0
            for i, rf in enumerate(rcpt_features):
                if not rf: continue
                si = ProductMatcher.get_score_from_features(sf, rf)
                if si['total'] > best_score:
                    best_score, best_idx = si['total'], i
                    
            if best_idx is not None and best_score >= 0.35: # 임계값 0.35
                matches[store_name] = receipt_items[best_idx]

    return matches


# ── 엑셀 처리 ─────────────────────────────────────────────
def decrypt_excel(uploaded_file, password):
    try:
        import msoffcrypto
    except ImportError:
        return None, "msoffcrypto 미설치 (pip install msoffcrypto-tool)"
    try:
        uploaded_file.seek(0)
        raw = io.BytesIO(uploaded_file.read())
        raw.seek(0)
        f = msoffcrypto.OfficeFile(raw)
        if not f.is_encrypted():
            raw.seek(0)
            return raw, None
        f.load_key(password=password)
        decrypted = io.BytesIO()
        f.decrypt(decrypted)
        decrypted.seek(0)
        return decrypted, None
    except Exception as e:
        uploaded_file.seek(0)
        return None, str(e)


def read_excel_auto(uploaded_file, password=""):
    decrypt_error = None
    if password:
        result, decrypt_error = decrypt_excel(uploaded_file, password)
        if result is None:
            return None, f"비밀번호 해제 실패: {decrypt_error}"
        uploaded_file = result

    # CSV 파일 처리 (CJ 파일접수 상세내역 등 EUC-KR CSV 지원)
    fname = getattr(uploaded_file, 'name', '') or ''
    if fname.lower().endswith('.csv'):
        for enc in ['euc-kr', 'cp949', 'utf-8-sig', 'utf-8']:
            try:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, encoding=enc, dtype=str,
                                 on_bad_lines='skip')
                if len(df) > 0 and len(df.columns) > 1:
                    df.columns = [str(c).strip() for c in df.columns]
                    return df, None
            except Exception:
                pass
        return None, "CSV 파일 읽기 실패 (EUC-KR/UTF-8 모두 실패)"

    # Excel 파일 처리
    last_error = ''
    for engine in ['openpyxl', 'xlrd']:
        for skip in [0, 1]:
            try:
                uploaded_file.seek(0)
                df = pd.read_excel(uploaded_file, engine=engine, header=skip)
                first_col = str(df.columns[0]) if len(df.columns) > 0 else ""
                if skip == 0 and len(first_col) > 50:
                    continue
                if len(df) > 0 and len(df.columns) > 3:
                    return df, None
            except Exception as e:
                last_error = str(e)
    for enc in ['utf-8', 'euc-kr']:
        try:
            uploaded_file.seek(0)
            dfs = pd.read_html(uploaded_file, encoding=enc)
            if dfs:
                return dfs[0], None
        except Exception:
            pass
    if decrypt_error:
        return None, f"비밀번호 해제 실패: {decrypt_error}"
    return None, f"파일 읽기 실패: {last_error or '알 수 없는 오류'}"


# ── 발송상태 동기화 ─────────────────────────────────────────
def sync_active_order_status(username, client_id, client_secret):
    """미발송(active)으로 잡힌 주문의 실제 네이버 상태를 조회해 로컬 order_history를 갱신.
    이미 발송/완료된 건은 상태가 갱신되어 미발송 목록에서 자동 제외된다.
    반환: dict(checked, updated, cleared, error)
      - checked: 네이버에서 조회된 건수
      - updated: 로컬 갱신된 행 수
      - cleared: 발송/완료 상태로 바뀌어 미발송에서 빠진 건수
    """
    from db import get_active_orders, update_order_status_bulk, get_user_db
    import naver_api as _na

    active = get_active_orders(username)
    if not active:
        return {'checked': 0, 'updated': 0, 'cleared': 0, 'error': None}

    _ACTIVE = {"PAYED", "INSTRUCT", "PRODUCT_READY", "결제완료", "발주확인", "발송대기"}
    status_map = {}
    cleared = 0
    checked = 0
    err = None

    # 플랫폼 분리: 쿠팡 주문번호는 '-' 포함(orderId-vendorItemId), 네이버는 미포함
    naver_active   = [r for r in active if r.get('order_no') and '-' not in str(r['order_no'])]
    coupang_active = [r for r in active if r.get('order_no') and '-' in str(r['order_no'])]

    # ── 네이버: 각 주문의 현재 상태 조회 → 미발송 아니면 제외 ──
    _dispatch_to_log = []  # 네이버에서 직접 발송된 건 → dispatch_log 자동 기록용
    nv_ids = [str(r['order_no']) for r in naver_active if r.get('order_no')]
    if nv_ids and client_id and client_secret:
        rows, _nerr = _na.fetch_order_details_by_ids(client_id, client_secret, nv_ids)
        if _nerr and not rows:
            err = _nerr
        for r in (rows or []):
            ono = str(r.get('상품주문번호', '') or '')
            stt = str(r.get('주문상태', '') or '')
            tno = str(r.get('송장번호', '') or '')
            if not ono or not stt:
                continue
            checked += 1
            status_map[ono] = {'status': stt, 'tracking_no': tno}
            if stt not in _ACTIVE:
                cleared += 1
                # 네이버에서 발송처리된 건 → 실제 발송일(sendDate)로 발송기록 자동 저장
                _sd = str(r.get('발송처리일', '') or r.get('발송일', ''))[:10]
                if _sd:
                    _dispatch_to_log.append({
                        'order_no': ono, 'dispatched_at': _sd,
                        'recipient': r.get('수취인명', '') or '',
                        'product_name': r.get('상품명', '') or '',
                        'tracking_no': tno or str(r.get('송장번호', '') or ''),
                        'courier': r.get('택배사', '') or '',
                    })

    # 네이버 직접 발송건을 dispatch_log에 기록 (발송일 그룹별, idempotent) → 정산 역추적 매칭에 잡힘
    if _dispatch_to_log:
        try:
            from db import log_dispatch_success as _lds
            from collections import defaultdict as _dd
            _by_date = _dd(list)
            for _row in _dispatch_to_log:
                _by_date[_row['dispatched_at']].append(_row)
            for _date, _rows in _by_date.items():
                try:
                    _lds(username, _rows, _date, platform='naver')
                except Exception:
                    pass
        except Exception:
            pass

    # ── 쿠팡: 현재 '대기'(ACCEPT+INSTRUCT) 목록에 없으면 = 발송됨 → 제외 ──
    if coupang_active:
        conn = get_user_db(username)
        _g = lambda k: (conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone() or [None])[0]
        _ak, _sk, _vid = _g('coupang_access_key'), _g('coupang_secret_key'), _g('coupang_vendor_id')
        conn.close()
        if _ak and _sk and _vid:
            import coupang_api as _cq
            from datetime import datetime as _dt, timedelta as _td
            _from = (_dt.now() - _td(days=30)).strftime("%Y-%m-%d")
            _to   = _dt.now().strftime("%Y-%m-%d")
            pending = set()
            pending_ok = False  # 조회 성공해야만 '미대기=발송' 판정 (API 실패 시 오제외 방지)
            for _stt in ("ACCEPT", "INSTRUCT"):
                try:
                    _res = _cq.get_orders(_ak, _sk, _vid, status=_stt, date_from=_from, date_to=_to)
                    _rows = _res[0] if isinstance(_res, tuple) else _res
                    _cerr = _res[1] if isinstance(_res, tuple) and len(_res) > 1 else None
                    if _cerr is None:
                        pending_ok = True
                    for _r in (_rows or []):
                        _ono = str(_r.get('상품주문번호', '') or _r.get('주문번호', '') or '')
                        if _ono:
                            pending.add(_ono.split('-')[0])
                except Exception:
                    pass
            if pending_ok:
                for r in coupang_active:
                    _full = str(r['order_no'])
                    _base = _full.split('-')[0]
                    checked += 1
                    if _base not in pending:
                        # 더 이상 대기상태가 아님 = 출고/배송 처리됨 → 미발송에서 제외
                        status_map[_full] = {'status': 'DEPARTURE', 'tracking_no': ''}
                        cleared += 1

    updated = update_order_status_bulk(username, status_map)
    return {'checked': checked, 'updated': updated, 'cleared': cleared, 'error': err}
