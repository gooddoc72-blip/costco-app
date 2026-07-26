"""수익계산 — DB 기반 매칭 보강 입력 빌더 (세션 비의존, 순수).

page.py는 st.session_state에서 receipt_items·kw_overrides·surcharge_map을 만들어
compute_rows()에 넘긴다. FastAPI(api/main.py)는 세션이 없으므로 같은 값을 **DB에서 직접**
만든다. compute_rows()는 순수함수이므로 동일 입력이면 동일 구입가(cost)를 낸다.

⚠️ 범위 = 구입가(매칭) 보강만. 실정산확정(actual/coupang)·포장배정 박스비 등
   profit 축은 여기서 다루지 않는다(page.py 후처리, 별도 과제).

세션-의존이라 API에서 재현 불가한 것(의도적으로 제외):
  · receipt_pick — 영수증 picker의 라이브 수동선택(세션 한정, DB 미영속).
  · 세션에 남은 임시 cost 편집 — ps 정산저장으로만 영속.
page.py가 kw_overrides에 심는 settlement_overrides.override_keyword는 현재 page.py에서
_sk 키로 저장돼 compute의 복합키 조회와 어긋나 사실상 no-op이다. 여기서는 compute가
실제로 조회하는 **복합키**로 올바르게 만들어, override_keyword가 설정된 경우 정확히 반영한다.
(page.py와의 미세 차이 가능 — override_keyword 미사용 계정에선 차이 0.)
"""
from db import get_recent_receipt_items, get_settlement_overrides_map
from db_inventory import get_surcharge_map


def build_receipt_maps(username, unique_products, match_fn, *, days=90):
    """(receipt_by_pno, receipt_matches) — page.py 320~345 로직의 DB판.

    receipt_by_pno: {코스트코상품번호: 영수증항목}  (같은 번호 중복 시 최신 우선)
    receipt_matches: {주문상품명: 영수증항목}       (상품번호→이름특징 매칭)

    match_fn(상품명, 상품번호) -> 제품 dict|None : compute_rows에 쓰는 것과 동일 주입식.
    """
    from services import match_receipt_to_orders
    receipt_items = get_recent_receipt_items(username, days=days)
    if not receipt_items:
        return {}, {}
    # get_recent_receipt_items는 receipt_date DESC 정렬 → 첫 등장이 최신. 최신 우선 유지.
    receipt_by_pno = {}
    for ri in receipt_items:
        pno = str(ri.get('상품번호', '') or '')
        if pno and pno not in receipt_by_pno:
            receipt_by_pno[pno] = ri
    # pno_map: {코스트코번호: [주문상품명...]} — 상품번호 우선 이름매칭용 (page.py _pno_map)
    pno_map = {}
    if receipt_by_pno:
        for un in unique_products:
            p0 = match_fn(un, '')
            pno0 = str((p0 or {}).get('product_no', '') or '').strip()
            if pno0:
                pno_map.setdefault(pno0, []).append(un)
    receipt_matches = match_receipt_to_orders(
        receipt_items, unique_products, pno_map=pno_map or None)
    return receipt_by_pno, receipt_matches


def build_kw_overrides(username, rows, calc_date_str):
    """{복합키: override_keyword} — settlement_overrides(영구 수동 키워드 매핑)의 DB판.

    복합키 = f"{수취인명}_{상품명}_{idx}_{calc_date_str}" (compute_row.row_key와 동일 포맷).
    rows: compute_rows에 넘기는 것과 동일 구조의 dict 리스트([{'idx','수취인명','상품명'}...]).
    override_keyword만 반영(단가 마스킹 없음 — products DB 단가가 진실원천, page.py 방침 일치).
    """
    so_map = get_settlement_overrides_map(username)
    if not so_map:
        return {}
    kw = {}
    for row in rows:
        rk = (str(row.get('수취인명', '') or ''), str(row.get('상품명', '') or ''))
        so = so_map.get(rk)
        if so and so.get('override_keyword'):
            key = (f"{row.get('수취인명', '')}_{row.get('상품명', '')}"
                   f"_{row.get('idx', '')}_{calc_date_str}")
            kw[key] = so['override_keyword']
    return kw


def load_surcharge_map(username, ids):
    """{idx: 웃돈} — 타인재고 웃돈(+원가). compute_rows/후처리 addback 공용.

    ids는 compute_rows에 넣는 각 행의 'idx'(= _sk)와 동일해야 한다.
    """
    try:
        return get_surcharge_map(username, [str(i) for i in ids]) or {}
    except Exception:
        return {}
