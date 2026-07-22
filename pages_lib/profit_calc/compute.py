"""수익계산 — 매칭·원가 계산의 순수 로직 (Streamlit/세션/DB쓰기 없음).

page.py의 거대한 렌더-중-계산 루프를 여기로 추출한다. 목적:
  · 입력 → 출력이 결정적 (테스트 가능, 페이지네이션/재실행과 무관)
  · DB 쓰기(자동링크)는 실행하지 않고 '적용 목록'으로 반환만 → 호출자가 저장 시점에 1회 실행
  · React+API로 이전 시 이 함수를 그대로 재사용

match_fn(product_name, product_no) -> product dict | None
  : 제품 매칭 함수(services.match_product_to_db를 미리 로드된 상품으로 감싼 것). 주입식.
"""
import re

from services import resolve_pack_factor

_PACK_RE = re.compile(r'x\s*(\d+)\s*개', re.IGNORECASE)


def _int(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


# 고객배송비 정산 비율 — db_orders._ship_settle_factor와 일치(전액 정산 1.0).
#   네이버 수수료 5.5%는 판매가에만 적용돼 정산예정금액에 이미 반영됨.
SHIP_SETTLE_FACTOR = 1.0


def settled_shipping(shipping_fee, factor=SHIP_SETTLE_FACTOR):
    """실정산배송비 = round(배송비합계 × factor). page.py: df['배송비 합계'].round().astype(int)."""
    try:
        return int(round(float(shipping_fee or 0) * factor))
    except (TypeError, ValueError):
        return 0


def compute_profit(settlement_amount, shipping_fee, cost_price, delivery_cost, box_cost,
                   factor=SHIP_SETTLE_FACTOR, only_when_costed=False):
    """행별 수입(순수). page.py 라인 585 · db_stats._PS_PROFIT_EXPR과 동일 공식:
        수입 = (정산예정금액 + 실정산배송비) − (구입가격 + 택배원가 + 박스원가)
    only_when_costed=True면 구입가<=0 행은 0으로(집계용 db_stats 규칙)."""
    cp = _int(cost_price)
    if only_when_costed and cp <= 0:
        return 0
    return (_int(settlement_amount) + settled_shipping(shipping_fee, factor)) - (
        cp + _int(delivery_cost) + _int(box_cost))


def compute_row(row, *, match_fn, receipt_by_pno, receipt_matches,
                kw_overrides, receipt_pick, surcharge_map, calc_date_str):
    """한 주문행 매칭·원가. Returns dict(cost, source, matched_name, matched_pno, sqty, auto_links)."""
    product = str(row.get('상품명', '') or '')
    qty = max(1, _int(row.get('수량', 1), 1))
    idx = str(row.get('idx', ''))
    saved_cost = _int(row.get('구입가격', 0))
    row_sur = _int(surcharge_map.get(idx, 0))
    if row_sur and saved_cost > row_sur:
        saved_cost -= row_sur
    row_key = f"{row.get('수취인명', '')}_{product}_{idx}_{calc_date_str}"
    p_no = str(row.get('product_no', '') or '')
    links = []

    def out(cost, source, name, pno, sqty):
        return {'cost': int(cost), 'source': source, 'matched_name': name,
                'matched_pno': pno, 'sqty': int(sqty), 'auto_links': links}

    # 1. 수동 오버라이드 (최우선)
    if row_key in kw_overrides:
        manual_kw = kw_overrides[row_key]
        p = match_fn(manual_kw, '')
        if p:
            sq = max(1, _int(p.get('split_qty', 1), 1))
            sf = resolve_pack_factor(p, product)
            cost = (_int(p.get('unit_price')) // sq) * qty * sf
        else:
            cost = saved_cost if saved_cost > 0 else 0
        picked = receipt_pick.get(row_key, '')
        return out(cost, '수동입력', manual_kw,
                   picked or (p.get('product_no', '') if p else ''),
                   max(1, _int((p or {}).get('split_qty', 1), 1)))

    # 2. 상품번호 매칭
    p = match_fn(product, p_no) if p_no else None
    if not p:
        p = match_fn(product, '')
    matched_by_naver = bool(p) and bool(p_no) and (
        str(p.get('naver_channel_pno', '') or '') == p_no
        or str(p.get('naver_origin_pno', '') or '') == p_no)
    if p and (str(p.get('product_no', '') or '').strip() or matched_by_naver):
        pno1 = str(p.get('product_no', '') or '').strip()
        sq = max(1, _int(p.get('split_qty', 1), 1))
        sf = resolve_pack_factor(p, product)
        aq = qty * sf
        if receipt_by_pno and pno1 and pno1 in receipt_by_pno:
            ri = receipt_by_pno[pno1]
            eff_sq = sq if sq > 1 else sf
            computed = (_int(ri.get('단가')) // max(1, eff_sq)) * aq
            return out(computed if computed > 0 else saved_cost, '영수증',
                       ri.get('상품명', ''), pno1, sq)
        computed = (_int(p.get('unit_price')) // sq) * aq
        return out(computed if computed > 0 else saved_cost, 'DB-번호',
                   p.get('costco_name') or p.get('store_product_name') or product,
                   pno1 or p_no, sq)

    # 3. 영수증 이름매칭 (자동링크는 반환만 — 실행 안 함)
    if product in receipt_matches:
        item = receipt_matches[product]
        rcpt_pno = str(item.get('상품번호', '') or '')
        rsq = max(1, _int((p or {}).get('split_qty', 1), 1))
        if rsq == 1:
            m2 = _PACK_RE.search(str(item.get('상품명', '')))
            if m2:
                rsq = max(1, int(m2.group(1)))
        sf = resolve_pack_factor(p, product)
        cost = (_int(item.get('단가')) // rsq) * qty * sf
        if rcpt_pno and p and p.get('match_keyword'):
            links.append({'type': 'upsert_costco',
                          'costco_name': p.get('costco_name') or p['match_keyword'],
                          'keyword': p['match_keyword'],
                          'unit_price': _int(p.get('unit_price')),
                          'product_no': rcpt_pno,
                          'split_qty': _int(p.get('split_qty', 1), 1)})
        if rcpt_pno and p_no:
            links.append({'type': 'link_costco_naver', 'naver_no': p_no, 'costco_no': rcpt_pno})
        return out(cost, '영수증', item.get('상품명', ''), rcpt_pno, rsq)

    # 4. 키워드 토큰 매칭
    if p:
        sq = max(1, _int(p.get('split_qty', 1), 1))
        sf = resolve_pack_factor(p, product)
        cost = (_int(p.get('unit_price')) // sq) * qty * sf
        return out(cost, 'DB-키워드', p.get('costco_name', ''), '', sq)

    # 5. 미매칭
    if saved_cost > 0:
        return out(saved_cost, 'DB-키워드', product, '', 1)
    return out(0, '미매칭', '', '', 1)


def compute_rows(rows, *, match_fn, receipt_by_pno=None, receipt_matches=None,
                 kw_overrides=None, receipt_pick=None, surcharge_map=None, calc_date_str=''):
    """전체 행 계산. match_fn은 (상품명, 상품번호)->제품|None. 메모이제이션은 호출자 권장.

    Returns: (results, auto_links)
      results: [{cost, source, matched_name, matched_pno, sqty}, ...] (rows와 동일 순서)
      auto_links: [{type, ...}, ...] — 저장 시점에 호출자가 실행할 DB 반영 목록.
    """
    receipt_by_pno = receipt_by_pno or {}
    receipt_matches = receipt_matches or {}
    kw_overrides = kw_overrides or {}
    receipt_pick = receipt_pick or {}
    surcharge_map = surcharge_map or {}
    results, all_links, _seen = [], [], set()
    for row in rows:
        r = compute_row(row, match_fn=match_fn, receipt_by_pno=receipt_by_pno,
                        receipt_matches=receipt_matches, kw_overrides=kw_overrides,
                        receipt_pick=receipt_pick, surcharge_map=surcharge_map,
                        calc_date_str=calc_date_str)
        for lk in r.pop('auto_links'):
            _k = (lk.get('type'), lk.get('product_no') or lk.get('costco_no'),
                  lk.get('keyword') or lk.get('naver_no'))
            if _k not in _seen:
                _seen.add(_k)
                all_links.append(lk)
        results.append(r)
    return results, all_links


def apply_auto_links(username, auto_links):
    """compute_rows가 반환한 자동링크를 실제 DB에 반영 (저장 시점에 1회 호출).
    렌더 중이 아니라 명시적 저장 액션에서만 실행 → 렌더-중-DB쓰기 제거."""
    from db import upsert_product, link_costco_to_naver
    n = 0
    for lk in auto_links or []:
        try:
            if lk['type'] == 'upsert_costco':
                upsert_product(username, lk['costco_name'], lk['keyword'],
                               lk['unit_price'], product_no=lk['product_no'],
                               split_qty=lk['split_qty'])
                n += 1
            elif lk['type'] == 'link_costco_naver':
                link_costco_to_naver(username, lk['naver_no'], lk['costco_no'])
                n += 1
        except Exception:
            pass
    return n
