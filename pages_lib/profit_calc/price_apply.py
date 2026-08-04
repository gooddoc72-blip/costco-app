"""수익계산 → 제품가격 DB 반영 (순수 함수).

두 갈래를 다룬다.
  A. 영수증 단가 반영  — 영수증에 찍힌 코스트코 실단가를 그대로 원가로.
  B. 정산표 단가 반영  — 화면의 구입가를 1주문 단가로 환산해 원가로.

제품 레코드 식별 순서(중요):
  ① 코스트코 상품번호
  ② 네이버 채널 상품번호 (주문의 product_no)
  ③ 네이버 원상품번호 (소분 판매 — 코스트코번호가 없는 경우가 많다)
  ④ 매칭 키워드
소분(split_qty>1)·코스트코번호 없는 상품은 ①이 비어서 예전엔 저장 대상이
못 되거나 엉뚱한 레코드에 붙었다. ②③으로 정확히 집는 게 이 모듈의 핵심.

⚠️ 돈 계산이라 '무엇을 왜 바꾸는지'를 항상 목록으로 돌려준다(미리보기용).
"""

_MAX_JUMP = 5   # 기존 단가 대비 N배 초과 인상은 박스/카톤가 의심 → 기본 제외


def _int(v, d=0):
    try:
        return int(float(str(v).replace(',', '').strip() or d))
    except (TypeError, ValueError):
        return d


def find_product(products, *, costco_no='', naver_channel='', naver_origin='', keyword=''):
    """제품 레코드 1건 식별. 반환: (product|None, 식별근거)."""
    costco_no = str(costco_no or '').strip()
    naver_channel = str(naver_channel or '').strip()
    naver_origin = str(naver_origin or '').strip()
    keyword = str(keyword or '').strip()

    if costco_no:
        for p in products:
            if str(p.get('product_no', '') or '').strip() == costco_no:
                return p, '코스트코번호'
    if naver_channel:
        for p in products:
            if str(p.get('naver_channel_pno', '') or '').strip() == naver_channel:
                return p, '네이버채널번호'
        # 채널번호를 origin칸에 넣어 쓰는 계정도 있어 교차 조회
        for p in products:
            if str(p.get('naver_origin_pno', '') or '').strip() == naver_channel:
                return p, '네이버원번호'
    if naver_origin:
        for p in products:
            if str(p.get('naver_origin_pno', '') or '').strip() == naver_origin:
                return p, '네이버원번호'
    if keyword:
        for p in products:
            if str(p.get('match_keyword', '') or '').strip() == keyword:
                return p, '키워드'
    return None, ''


def build_receipt_updates(rows, receipt_by_pno, products, *, max_jump=_MAX_JUMP):
    """영수증 실단가 → 제품가격 DB 반영 목록.

    rows: 정산표 행 [{상품명, 매칭상품번호, 매칭출처, product_no(주문 네이버번호), ...}]
    receipt_by_pno: {코스트코번호: 영수증항목} — 영수증 단가의 출처
    반환: (updates, skipped)
      updates = [{keyword, costco_no, split_qty, naver_origin, old_price, new_price,
                  product_name, receipt_name, by}]
      skipped = [{product_name, reason}]
    """
    updates, skipped, seen = [], [], set()
    for r in rows or []:
        _name = str(r.get('상품명', '') or '')
        _cno = str(r.get('매칭상품번호', '') or '').strip()
        if not _cno:
            skipped.append({'product_name': _name, 'reason': '코스트코 상품번호 없음(영수증 매칭 불가)'})
            continue
        _item = (receipt_by_pno or {}).get(_cno)
        if not _item:
            continue                      # 이 행은 영수증에 없는 상품
        _new = _int(_item.get('단가'))
        if _new <= 0:
            skipped.append({'product_name': _name, 'reason': '영수증 단가 0원'})
            continue
        if _cno in seen:
            continue                      # 같은 상품번호는 1번만
        seen.add(_cno)

        _p, _by = find_product(
            products, costco_no=_cno,
            naver_channel=str(r.get('product_no', '') or ''),
            naver_origin=str(r.get('naver_origin_pno', '') or ''),
            keyword=str(r.get('매칭제품', '') or ''))
        _old = _int((_p or {}).get('unit_price'))
        if _old > 0 and _new > _old * max_jump:
            skipped.append({'product_name': _name,
                            'reason': f'기존 {_old:,}원 → {_new:,}원 ({max_jump}배 초과, 박스가 의심)'})
            continue
        if _old == _new:
            continue                      # 이미 같은 값
        _kw = (str((_p or {}).get('match_keyword', '') or '').strip()
               or str(r.get('매칭제품', '') or '').strip()
               or str(_item.get('상품명', '') or '').strip())
        if not _kw:
            skipped.append({'product_name': _name, 'reason': '매칭 키워드 없음'})
            continue
        updates.append({
            'keyword': _kw,
            'costco_no': _cno,
            'split_qty': max(1, _int((_p or {}).get('split_qty'), 1)),
            'naver_origin': str((_p or {}).get('naver_origin_pno', '') or ''),
            'old_price': _old,
            'new_price': _new,
            'product_name': _name,
            'receipt_name': str(_item.get('상품명', '') or ''),
            'by': _by or '신규',
        })
    return updates, skipped


def build_row_updates(rows, products, *, only_keys=None, row_key_fn=None,
                      sell_factor_fn=None, max_jump=_MAX_JUMP):
    """정산표의 구입가 → 제품가격 DB 반영 목록 (소분·코스트코번호 없는 건 포함).

    화면의 구입가는 '그 주문 전체 금액'이므로 1팩 단가로 되돌린다:
        unit_price = 구입가 × split_qty ÷ (수량 × sell_factor)
    only_keys/row_key_fn: 사용자가 직접 고친 행만 대상으로 좁히고 싶을 때.
    """
    updates, skipped, seen = [], [], set()
    for r in rows or []:
        _name = str(r.get('상품명', '') or '')
        if only_keys is not None and row_key_fn is not None:
            if row_key_fn(r) not in only_keys:
                continue
        _cost = _int(r.get('구입가격'))
        if _cost <= 0:
            skipped.append({'product_name': _name, 'reason': '구입가 0원'})
            continue
        _cno = str(r.get('매칭상품번호', '') or '').strip()
        _nch = str(r.get('product_no', '') or '').strip()
        _p, _by = find_product(products, costco_no=_cno, naver_channel=_nch,
                              naver_origin=str(r.get('naver_origin_pno', '') or ''),
                              keyword=str(r.get('매칭제품', '') or ''))
        if not _p and not (_cno or _nch):
            skipped.append({'product_name': _name, 'reason': '코스트코·네이버 번호가 모두 없음'})
            continue
        _sq = max(1, _int((_p or {}).get('split_qty'), 1))
        _qty = max(1, _int(r.get('수량'), 1))
        _sf = max(1, sell_factor_fn(_name) if sell_factor_fn else 1)
        _new = (_cost * _sq) // max(1, _qty * _sf)
        if _new <= 0:
            skipped.append({'product_name': _name, 'reason': '환산 단가 0원'})
            continue
        _dedup = _cno or f"n:{_nch}" or _name
        if _dedup in seen:
            continue
        seen.add(_dedup)
        _old = _int((_p or {}).get('unit_price'))
        if _old > 0 and _new > _old * max_jump:
            skipped.append({'product_name': _name,
                            'reason': f'기존 {_old:,}원 → {_new:,}원 ({max_jump}배 초과, 박스가 의심)'})
            continue
        if _old == _new:
            continue
        _kw = (str((_p or {}).get('match_keyword', '') or '').strip()
               or str(r.get('매칭제품', '') or '').strip() or _name)
        updates.append({
            'keyword': _kw,
            'costco_no': _cno or str((_p or {}).get('product_no', '') or ''),
            'split_qty': _sq,
            'naver_origin': (str((_p or {}).get('naver_origin_pno', '') or '')
                             or (_nch if not _cno else '')),
            'old_price': _old,
            'new_price': _new,
            'product_name': _name,
            'receipt_name': '',
            'by': _by or ('네이버채널번호' if _nch else '신규'),
        })
    return updates, skipped
