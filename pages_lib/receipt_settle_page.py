"""🧾 영수증 정산 (관리자) — 코스트코 영수증을 각 사용자 주문에 자동배치하고
각 주문 구입가에 실단가를 반영 + 사용자별 정산표 생성."""
from datetime import date, datetime

import streamlit as st
import pandas as pd

from services import parse_costco_receipt_pdf, render_pdf_to_images
from receipt_settle import (
    allocate_receipt_to_orders, apply_receipt_settlement, cleanup_orphan_settlements,
    build_manual_rows, ai_match_receipt_orders, _summarize, compute_leftovers,
    build_stock_pool, get_settle_start_date, get_stock_status,
)
from db_receipt_settle import (
    save_settlement_batch, list_settlement_batches, get_settlement_items,
    get_settlement_shortages, get_settlement_leftovers, get_user_billing_basis,
    set_shortage_decision,
    get_user_settlement_summary, delete_settlement_batch,
)
from db import (
    get_all_users, get_all_settings, add_lot_units, find_lots_by_memo,
    set_global_setting,
)
from utils import fmt

invalidate_data_cache = None


def _set_cache_helpers(shared_fn=None, user_fn=None, merged_fn=None, invalidate_fn=None, **kwargs):
    global invalidate_data_cache
    invalidate_data_cache = invalidate_fn


def _disp_map():
    return {u['username']: (u.get('display_name') or u['username']) for u in get_all_users()}


def render(USERNAME: str, IS_ADMIN: bool, settings: dict):
    if not IS_ADMIN:
        st.error("관리자 전용 기능입니다.")
        return

    st.header("🧾 영수증 정산 — 사용자 주문 자동배치")
    # ── 정산 기준일 ────────────────────────────────────────────
    #   기준일 이전 구매는 재고 계산에서 제외한다. 과거에는 영수증 업로드가
    #   들쭉날쭉해 재고가 실제와 맞지 않는다. 데이터를 지우지는 않는다.
    _sd = get_settle_start_date()
    _sc1, _sc2 = st.columns([2, 3])
    _new_sd = _sc1.date_input(
        "정산 기준일 (이전 구매는 재고에서 제외)",
        value=(datetime.strptime(_sd, "%Y-%m-%d").date() if _sd else date.today()),
        key="rs_start_date")
    _sc2.write(""); _sc2.write("")
    if _sc2.button("기준일 저장", key="rs_save_start"):
        set_global_setting("settle_start_date", str(_new_sd))
        st.success(f"✅ 기준일 {_new_sd} 저장 — 이 날짜부터의 구매만 재고로 계산합니다.")
        st.rerun()
    if _sd:
        st.caption(f"📅 현재 기준일 **{_sd}** — 이전 영수증은 재고 이월에 쓰이지 않습니다 "
                   "(데이터는 보존되며 공유DB 매장 카탈로그에는 계속 반영됩니다).")
    else:
        st.warning("⚠️ 정산 기준일이 설정되지 않아 **모든 과거 영수증**이 재고로 계산됩니다. "
                   "업로드가 누락된 기간이 섞이면 재고가 실제와 어긋납니다.")

    st.caption(
        "코스트코 영수증 PDF를 올리면 **상품번호로 각 사용자 주문에 배치**하고, "
        "각 주문 구입가에 **영수증 실단가**를 반영합니다. 사용자별 구매금액 정산표도 만들어집니다."
    )

    # ── 1) 영수증 업로드 (선택) — 자동 인식 시도 후 표에 채움 ──
    files = st.file_uploader(
        "코스트코 영수증 PDF (여러 개 가능)", type=['pdf'],
        key="rs_pdf", accept_multiple_files=True
    )
    _fkey = tuple(sorted(f.name for f in files)) if files else ()
    if files and st.session_state.get('_rs_fkey') != _fkey:
        parsed, fails = [], []
        import ai_service as _ais
        _ak0, _gk0 = _ais.get_ai_keys(settings)
        with st.spinner("영수증 인식 중..."):
            for f in files:
                try:
                    items, err = parse_costco_receipt_pdf(f)
                except Exception as e:
                    items, err = None, f"파싱 예외: {e}"
                if items:
                    parsed.extend(items)
                    continue
                # ── 글자 없는 스캔 PDF → 페이지를 그림으로 렌더해 AI 비전으로 읽는다.
                #    코스트코 앱에서 받은 영수증이 대개 이 형태다.
                if not (_ak0 or _gk0):
                    fails.append((f.name, (err or '인식 실패')
                                  + " · AI 키가 없어 이미지 판독도 불가"))
                    continue
                _imgs, _rerr = render_pdf_to_images(f)
                if _rerr or not _imgs:
                    fails.append((f.name, f"{err or '인식 실패'} · 이미지 변환도 실패({_rerr})"))
                    continue
                _got = 0
                for _pi, (_ib, _mt) in enumerate(_imgs, 1):
                    _d, _de = _ais.parse_receipt_photo(_ak0, _ib, _mt, gemini_key=_gk0)
                    if _de or not _d:
                        continue
                    _rd = _d.get('purchase_date', '') or ''
                    for _it in (_d.get('items') or []):
                        if not str(_it.get('상품명', '') or '').strip():
                            continue
                        parsed.append({
                            '상품번호': str(_it.get('상품번호', '') or ''),
                            '상품명': _it.get('상품명', ''),
                            '수량': int(_it.get('수량') or 1),
                            '단가': int(_it.get('단가') or 0),
                            'receipt_date': _rd,
                        })
                        _got += 1
                    if not _d.get('_verified', True):
                        fails.append((f"{f.name} p{_pi}",
                                      "금액·수량 자가검증 불일치 — 아래 표에서 값을 확인하세요: "
                                      + " / ".join((_d.get('_check') or [])[:2])))
                if _got:
                    st.info(f"📄 {f.name} — 글자가 없는 스캔 PDF라 "
                            f"이미지 {len(_imgs)}쪽을 AI로 읽어 {_got}품목 인식했습니다. "
                            "값이 맞는지 아래 표에서 확인하세요.")
                else:
                    fails.append((f.name, f"{err or '인식 실패'} · AI 이미지 판독도 품목을 못 찾음"))
        merged = {}
        for p in parsed:
            k = _n(p.get('상품번호')) or _n(p.get('상품명'))
            ex = merged.get(k)
            if ex is None or (p.get('receipt_date', '') or '') >= (ex.get('receipt_date', '') or ''):
                merged[k] = p
        st.session_state['rs_receipt_items'] = list(merged.values())
        st.session_state['_rs_fkey'] = _fkey
        st.session_state['_rs_fails'] = fails
        st.session_state.pop('rs_alloc', None)   # 새 업로드 → 이전 미리보기 초기화
        st.session_state.pop('rs_day', None)     # 새 영수증 → 정산일을 새 영수증 날짜로 재설정

    # ── 1-b) 📱 영수증 사진 (휴대폰 촬영) — PDF가 없을 때 ──
    #   코스트코에서 장 본 직후 종이 영수증을 찍어 바로 정산할 수 있게 한다.
    #   판독 결과는 PDF와 같은 형태({상품번호,상품명,수량,단가})라 이후 배치 로직은 공용.
    _ph = st.file_uploader(
        "📷 또는 영수증 촬영 (누르면 카메라 · 여러 장 가능)",
        type=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'],
        key="rs_photo", accept_multiple_files=True)
    from pages_lib.receipt_page import inject_native_camera as _inject_cam
    _inject_cam("영수증 촬영")   # 탭 → 폰 기본 카메라 → 촬영 → 자동 업로드
    _pkey = tuple(sorted(f.name for f in _ph)) if _ph else ()
    if _ph and st.session_state.get('_rs_pkey') != _pkey:
        import ai_service
        from pages_lib.receipt_page import _image_for_ai
        _ak, _gk = ai_service.get_ai_keys(settings)
        if not (_ak or _gk):
            st.warning("⚠️ AI 키가 없어 사진 판독을 할 수 없습니다. 설정 탭 > 🤖 AI 설정에서 "
                       "Gemini 또는 Claude 키를 등록하세요.")
        else:
            _pparsed, _pfails = [], []
            _pbar = st.progress(0.0, text="영수증 사진 판독 중...")
            for _pi, _pf in enumerate(_ph, 1):
                _pbar.progress(_pi / len(_ph), text=f"영수증 사진 판독 중... ({_pi}/{len(_ph)})")
                _img, _ierr = _image_for_ai(_pf)
                if _ierr:
                    _pfails.append((_pf.name, _ierr)); continue
                _data, _perr = ai_service.parse_receipt_photo(
                    _ak, _img[0], _img[1], gemini_key=_gk)
                if _perr or not _data:
                    _pfails.append((_pf.name, _perr or '판독 실패')); continue
                _rdate = _data.get('purchase_date', '') or ''
                for _it in (_data.get('items') or []):
                    if not str(_it.get('상품명', '') or '').strip():
                        continue
                    _pparsed.append({
                        '상품번호': str(_it.get('상품번호', '') or ''),
                        '상품명': _it.get('상품명', ''),
                        '수량': int(_it.get('수량') or 1),
                        '단가': int(_it.get('단가') or 0),
                        'receipt_date': _rdate,
                    })
                if not _data.get('_verified', True):
                    _pfails.append((_pf.name,
                                    "금액·수량 자가검증 불일치 — 아래 표에서 값을 확인하세요: "
                                    + " / ".join((_data.get('_check') or [])[:2])))
            _pbar.empty()
            if _pparsed:
                _merged_p = {_n(x.get('상품번호')) or _n(x.get('상품명')): x for x in _pparsed}
                _prev = {_n(x.get('상품번호')) or _n(x.get('상품명')): x
                         for x in (st.session_state.get('rs_receipt_items') or [])}
                _prev.update(_merged_p)          # PDF로 올린 게 있으면 합친다
                st.session_state['rs_receipt_items'] = list(_prev.values())
                st.session_state.pop('rs_alloc', None)
                st.session_state.pop('rs_day', None)
                st.success(f"📱 사진 {len(_ph)}장에서 {len(_merged_p)}품목 인식")
            st.session_state['_rs_pkey'] = _pkey
            st.session_state['_rs_fails'] = (st.session_state.get('_rs_fails') or []) + _pfails

    for fn, em in st.session_state.get('_rs_fails', []):
        st.warning(f"⚠️ 자동 인식 실패: **{fn}** — {em}. 아래 표에 **직접 입력**해서 정산할 수 있습니다.")

    # ── 2) 영수증 품목 (자동 인식 + 직접 추가/수정) ──
    st.subheader("🧾 영수증 품목")
    st.caption("자동 인식되면 표에 채워집니다. 인식이 안 되거나 빠진 게 있으면 **코스트코 상품번호·상품명·단가를 직접 입력**하세요. (행 추가 가능)")
    _seed = st.session_state.get('rs_receipt_items') or []
    _rd_by_cno = {_n(p.get('상품번호')): (p.get('receipt_date', '') or '')
                  for p in _seed if _n(p.get('상품번호'))}
    _seed_rows = [{'상품번호': _n(p.get('상품번호')), '상품명': _n(p.get('상품명')),
                   '수량': int(p.get('수량') or 1), '단가': int(float(p.get('단가') or 0))}
                  for p in _seed] or [{'상품번호': '', '상품명': '', '수량': 1, '단가': 0}]
    edited = st.data_editor(
        pd.DataFrame(_seed_rows), num_rows='dynamic', use_container_width=True,
        key=f"rs_item_editor_{abs(hash(_fkey)) % 100000}",
        column_config={
            '상품번호': st.column_config.TextColumn('코스트코 상품번호'),
            '상품명': st.column_config.TextColumn('상품명'),
            '수량': st.column_config.NumberColumn('수량', min_value=1, step=1),
            '단가': st.column_config.NumberColumn('실단가(원)', min_value=0, step=100),
        },
    )
    receipt_items = []
    for r in edited.to_dict('records'):
        cno = _n(r.get('상품번호'))
        try:
            up = int(float(r.get('단가') or 0))
        except (TypeError, ValueError):
            up = 0
        if cno and up > 0:
            receipt_items.append({'상품번호': cno, '상품명': _n(r.get('상품명')),
                                  '수량': int(r.get('수량') or 1), '단가': up,
                                  'receipt_date': _rd_by_cno.get(cno, '')})
    if not receipt_items:
        st.info("정산하려면 표에 **코스트코 상품번호 + 실단가(>0)** 가 있는 항목이 최소 1개 필요합니다.")
        _render_stock_status()
        _render_history(_disp_map(), USERNAME)
        return
    st.caption(f"✅ 정산 대상 품목 {len(receipt_items)}종")

    # ── 2) 당일 배치 ── (당일 주문건만 매칭 — 매일 그날 주문에 대해 정산)
    st.divider()
    st.subheader("📅 당일 주문 배치")
    # 영수증에서 인식된 날짜를 기본 정산일로 (영수증일자 ↔ 주문일자 매칭)
    _rdates = sorted({(it.get('receipt_date') or '')[:10]
                      for it in receipt_items if (it.get('receipt_date') or '')})
    _def_day = date.today()
    if _rdates:
        try:
            _def_day = date.fromisoformat(_rdates[-1])
        except Exception:
            _def_day = date.today()
    d_day = st.date_input("정산 날짜 (당일 주문 기준)", value=_def_day, key="rs_day")
    if _rdates:
        st.caption(f"🧾 영수증 인식 날짜: **{', '.join(_rdates)}** → 기본 정산일로 설정됨. "
                   "여러 날짜면 각 날짜별로 나눠 배치하세요.")
    st.caption(f"**{d_day}** 에 결제된(주문일 기준) 모든 판매자 주문 중, 위 영수증 상품번호와 일치하는 건에 배치합니다.")
    d_from = d_to = d_day

    if st.button("🔎 당일 자동배치 미리보기", type="primary", key="rs_preview_btn"):
        with st.spinner("당일 주문을 조회해 배치 중..."):
            # 재고 이월 — 당일 영수증에 없는 주문을 과거 구매분(가용 재고)에서 찾는다.
            #   실측(8/15~19): 미매칭 159건 → 100건으로 59건 감소.
            _pool = build_stock_pool(str(d_to), exclude_dates=[str(d_day)])
            alloc = allocate_receipt_to_orders(
                receipt_items, str(d_from), str(d_to), stock_pool=_pool
            )
        st.session_state['rs_alloc'] = alloc

    alloc = st.session_state.get('rs_alloc')
    if not alloc:
        _render_stock_status()
        _render_history(_disp_map(), USERNAME)
        return

    dmap = _disp_map()
    rows = alloc['rows']
    summary = alloc['user_summary']
    unmatched = alloc['unmatched_receipt']

    st.divider()
    if not rows:
        st.warning(
            "이 기간에 영수증 상품번호와 일치하는 주문이 없습니다. "
            "기간을 넓히거나, 제품 DB에 코스트코 상품번호↔네이버 번호 매핑이 있는지 확인하세요."
        )
    else:
        # ── 3) 사용자별 정산표 ──
        st.subheader("💰 사용자별 정산표")
        srows = [{'사용자': dmap.get(u, u), '품목수': s['count'], '총수량': s['qty'],
                  '구매금액(정산)': fmt(s['amount'])} for u, s in
                 sorted(summary.items(), key=lambda kv: -kv[1]['amount'])]
        st.dataframe(pd.DataFrame(srows), use_container_width=True, hide_index=True)
        _tot = sum(s['amount'] for s in summary.values())
        st.markdown(f"### 합계 구매금액: **{fmt(_tot)}원**  ·  주문 {len(rows)}건  ·  사용자 {len(summary)}명")

        with st.expander(f"🔍 배치 상세 ({len(rows)}건) — 주문별 구입가 반영 내역", expanded=False):
            drows = [{'사용자': dmap.get(r['username'], r['username']),
                      '주문번호': r['order_no'], '주문일': r['order_date'],
                      '상품명': r['product_name'], '수량': r['qty'],
                      '코스트코번호': r['costco_no'], '실단가': fmt(r['unit_price']),
                      '기존구입가': fmt(r['prev_cost']), '→ 새구입가': fmt(r['amount'])}
                     for r in rows]
            st.dataframe(pd.DataFrame(drows), use_container_width=True, hide_index=True)

    if unmatched:
        with st.expander(f"⚠️ 주문을 못 찾은 영수증 품목 {len(unmatched)}건", expanded=False):
            st.caption("해당 상품의 주문이 당일 없거나, 제품 DB에 코스트코↔네이버 번호 매핑이 없어 배치 못 함.")
            st.dataframe(pd.DataFrame([{'상품번호': u['상품번호'], '상품명': u['상품명'],
                                        '단가': fmt(u['단가'])} for u in unmatched]),
                         use_container_width=True, hide_index=True)

    # ── 3.5) 미매칭 수동/AI 매칭 ──
    _render_match_section(alloc, dmap, settings, USERNAME)

    # ── 3.7) 남은 재고 확인·입고 ──
    _render_leftover_section(receipt_items, alloc, dmap, d_day, USERNAME)

    # ── 4) 적용 ──
    if rows:
        st.divider()
        st.warning("⚠️ 적용하면 각 주문의 구입가가 영수증 실단가로 **덮어써집니다**. (되돌리려면 정산 이력에서 삭제 후 재수집)")
        if st.button("✅ 정산 적용 (구입가 반영 + 정산표 저장)", type="primary", key="rs_apply_btn"):
            with st.spinner("적용 중..."):
                n = apply_receipt_settlement(rows)
                # 부족분(주문은 있는데 영수증에 없음)·재고분(사고 남은 것)도 함께 남긴다.
                #   화면에만 있고 저장이 안 돼, 나중에 "그날 뭐가 모자랐나"를 알 수 없었다.
                _short = (alloc.get('unmatched_orders') or [])
                try:
                    # receipt_items는 이 화면이 파싱해 들고 있는 그 영수증 품목이다
                    _left = compute_leftovers(receipt_items, rows)
                except Exception:
                    _left = []
                bid = save_settlement_batch(
                    label=f"당일 {d_day}", date_from=str(d_from), date_to=str(d_to),
                    receipt_dates=str(d_day), rows=rows, created_by=USERNAME,
                    shortages=_short, leftovers=_left,
                )
            try:
                if invalidate_data_cache:
                    invalidate_data_cache()
            except Exception:
                pass
            st.session_state.pop('rs_alloc', None)
            st.success(f"✅ 정산 적용 완료 — 주문 {n}건 구입가 반영, 정산 배치 #{bid} 저장. "
                       "각 사용자 수익계산에 즉시 반영됩니다.")
            st.rerun()

    _render_stock_status()
    _render_history(dmap, USERNAME)


def _render_leftover_section(receipt_items, alloc, dmap, d_day, USERNAME):
    """영수증 구매수량 중 판매되지 않고 남은 분을 재고로 잡는다 — 관리자 확인 필수.

    자동 입고하지 않는 이유: 영수증 수량 인식이 틀리거나 주문 매칭이 덜 되면
    있지도 않은 재고가 생기고, 그 유령 재고가 나중에 남의 판매에서 차감되며
    교차정산 웃돈까지 발생시킨다. 되돌리기 어려운 방향의 오류라 사람이 본다.
    """
    st.divider()
    st.subheader("📦 남은 재고 확인")

    lefts = compute_leftovers(receipt_items, alloc.get('rows') or [])
    if not lefts:
        st.success("남은 수량이 없습니다 — 영수증 구매분이 모두 주문에 배치됐습니다.")
        return

    _memo_tag = f"영수증정산 {d_day}"
    _already = {str(l.get('product_no') or '')
                for l in (find_lots_by_memo(_memo_tag, received_at=str(d_day)) or [])}
    if _already:
        st.warning(f"⚠️ 이 날짜({d_day})로 이미 입고된 품목이 {len(_already)}종 있습니다. "
                   "중복 입고를 막기 위해 아래 표에서 '입고됨'으로 표시합니다.")

    st.caption(
        f"영수증 구매수량에서 **배치된 주문 소비량**을 뺀 잔량입니다. "
        f"수량은 재고원장과 같은 **소분 단위**입니다(소분 상품은 1팩 = split개). "
        f"보유자를 지정하고 체크한 행만 입고됩니다.")

    _opts = sorted(dmap.keys(), key=lambda u: dmap.get(u, u))
    _labels = [dmap.get(u, u) for u in _opts]
    _lbl2user = {dmap.get(u, u): u for u in _opts}
    _def_lbl = dmap.get(USERNAME, USERNAME)
    if _def_lbl not in _labels:            # 관리자가 목록에 없으면(비활성 등) 첫 사용자
        _def_lbl = _labels[0] if _labels else _def_lbl

    _bulk = st.selectbox(
        "일괄 보유자 (실제로 물건을 산 사람 — 표에서 행별로 바꿀 수 있습니다)",
        _labels, index=_labels.index(_def_lbl) if _def_lbl in _labels else 0,
        key="rs_lf_bulk",
        help="여기 지정한 사람의 재고로 잡힙니다. 나중에 다른 사용자가 이 재고로 팔면 "
             "기존 교차정산(소분 1개당 웃돈)이 자동으로 걸립니다.")

    _rows = []
    for l in lefts:
        _dup = l['costco_no'] in _already
        _rows.append({
            '입고': not _dup,
            '상품번호': l['costco_no'],
            '상품명': l['name'][:34],
            '영수증수량(팩)': l['qty_receipt'],
            '판매소비(소분)': l['units_used'],
            '남은수량(소분)': l['units_left'],
            '남은(팩)': round(l['packs_left'], 2),
            '팩단가': l['unit_price'],
            '재고금액': int(l['unit_price'] / max(1, l['split_qty']) * l['units_left']),
            '보유자': _bulk,
            '상태': '이미 입고됨' if _dup else '',
        })

    _ed = st.data_editor(
        pd.DataFrame(_rows), use_container_width=True, hide_index=True,
        key=f"rs_leftover_editor_{d_day}",
        disabled=['상품번호', '상품명', '영수증수량(팩)', '판매소비(소분)',
                  '남은수량(소분)', '남은(팩)', '팩단가', '재고금액', '상태'],
        column_config={
            '입고': st.column_config.CheckboxColumn('입고', help='체크한 행만 재고로 잡습니다'),
            '보유자': st.column_config.SelectboxColumn('보유자', options=_labels, required=True),
            '팩단가': st.column_config.NumberColumn('팩단가', format='%d'),
            '재고금액': st.column_config.NumberColumn('재고금액', format='%d'),
        },
    )

    _picked = [r for r in _ed.to_dict('records') if r.get('입고')]
    _amt = sum(int(r.get('재고금액') or 0) for r in _picked)
    st.markdown(f"선택 **{len(_picked)}종** · 재고금액 합계 **{fmt(_amt)}원**")

    if not _picked:
        st.caption("입고할 행을 체크하세요.")
        return

    if st.button(f"📦 확인한 {len(_picked)}종 재고 입고", type="primary", key="rs_lf_apply"):
        _by_cno = {l['costco_no']: l for l in lefts}
        _ok, _skip, _fail = 0, 0, []
        for r in _picked:
            _cno = str(r.get('상품번호') or '')
            _l = _by_cno.get(_cno)
            if not _l:
                continue
            if _cno in _already:
                _skip += 1
                continue
            _owner = _lbl2user.get(str(r.get('보유자') or ''), USERNAME)
            try:
                _lid = add_lot_units(
                    product_no=_cno, product_name=_l['name'], owner=_owner,
                    pack_unit_cost=_l['unit_price'], qty_units=_l['units_left'],
                    split_qty=_l['split_qty'], received_at=str(d_day),
                    memo=f"{_memo_tag} · 영수증잔량")
                if _lid:
                    _ok += 1
                else:
                    _fail.append(f"{_l['name'][:20]} (수량 0)")
            except Exception as e:
                _fail.append(f"{_l['name'][:20]} — {str(e)[:60]}")
        _msg = f"✅ 재고 입고 {_ok}종"
        if _skip:
            _msg += f" · ⏭ 중복 {_skip}종"
        if _fail:
            st.error("❌ 실패: " + " / ".join(_fail))
        st.success(_msg + " — '재고 관리' 탭에서 확인하세요.")
        st.rerun()


def _merge_matches(alloc, new_rows, matched_order_indices):
    alloc['rows'].extend(new_rows)
    idxset = set(matched_order_indices)
    alloc['unmatched_orders'] = [o for i, o in enumerate(alloc.get('unmatched_orders', []))
                                 if i not in idxset]
    matched_costco = {str(r['costco_no']) for r in alloc['rows']}
    alloc['unmatched_receipt'] = [u for u in alloc.get('unmatched_receipt', [])
                                  if str(u['상품번호']) not in matched_costco]
    alloc['user_summary'] = _summarize(alloc['rows'])
    st.session_state['rs_alloc'] = alloc


def _render_match_section(alloc, dmap, settings, USERNAME):
    u_ords = alloc.get('unmatched_orders') or []
    u_rcpt = alloc.get('unmatched_receipt') or []
    if not u_ords or not u_rcpt:
        return
    st.divider()
    st.subheader(f"🔗 미매칭 매칭 — 주문 {len(u_ords)}건 · 영수증 {len(u_rcpt)}종")
    st.caption("자동으로 못 붙은 주문을 영수증 품목과 AI 또는 수동으로 연결합니다.")

    # AI 키는 공유 인프라 — 관리자가 다른 계정 설정에 저장했어도 찾도록 폴백 스캔.
    def _resolve_ai_key(name):
        v = (settings.get(name) if settings else '') or ''
        if v:
            return v
        try:
            for _u in get_all_users():
                vv = (get_all_settings(_u['username']) or {}).get(name) or ''
                if vv:
                    return vv
        except Exception:
            pass
        return ''
    _anthropic_key = _resolve_ai_key('anthropic_api_key')
    _gemini_key = _resolve_ai_key('gemini_api_key')
    _has_ai = bool(_anthropic_key or _gemini_key)
    _ai_label = "🤖 AI 자동매칭" + (" (Gemini)" if _gemini_key else "")
    if st.button(_ai_label, key="rs_ai_match", disabled=not _has_ai,
                 help=None if _has_ai else "설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키를 먼저 등록하세요."):
        with st.spinner("AI가 상품명을 비교해 매칭 중..."):
            pairs, ai_err = ai_match_receipt_orders(
                u_rcpt, u_ords, anthropic_key=_anthropic_key, gemini_key=_gemini_key)
        if pairs:
            new = build_manual_rows([
                {'order': u_ords[p['order_index']], 'costco_no': p['costco_no'],
                 'unit_price': p['unit_price'], 'via': 'ai'} for p in pairs])
            _merge_matches(alloc, new, [p['order_index'] for p in pairs])
            st.success(f"🤖 AI가 {len(new)}건 매칭했습니다.")
            st.rerun()
        elif ai_err:
            # 실제 API 오류(크레딧 부족 등)를 그대로 노출 — '못 찾음'으로 오인 방지
            _low = ('credit' in ai_err.lower() or '크레딧' in ai_err or 'balance' in ai_err.lower())
            st.error(f"⚠️ AI 매칭을 실행하지 못했습니다: {ai_err}"
                     + ("\n\n👉 Anthropic 계정의 **크레딧이 소진**됐습니다. Plans & Billing에서 "
                        "크레딧을 충전하면 AI 매칭이 동작합니다. 그동안은 아래 **수동 매칭**을 이용하세요."
                        if _low else "\n\n아래 수동 매칭을 이용하세요."))
        else:
            st.info("AI가 자신 있게 매칭할 항목을 못 찾았습니다. 아래 수동 매칭을 이용하세요.")

    with st.expander("✋ 수동 매칭", expanded=False):
        _ri_opts = {i: f"[{it['상품번호']}] {it['상품명']} ({fmt(it['단가'])}원)"
                    for i, it in enumerate(u_rcpt)}
        ri = st.selectbox("영수증 품목", options=list(_ri_opts),
                          format_func=lambda i: _ri_opts[i], key="rs_mm_ri")
        _oi_opts = {i: f"{dmap.get(o['username'], o['username'])} · {o['recipient']} · "
                       f"{o['product_name'][:24]} ×{o['qty']}"
                    for i, o in enumerate(u_ords)}
        ois = st.multiselect("이 품목에 해당하는 주문 선택", options=list(_oi_opts),
                             format_func=lambda i: _oi_opts[i], key="rs_mm_ois")
        if st.button("➕ 매칭 추가", key="rs_mm_add", disabled=not ois):
            it = u_rcpt[ri]
            new = build_manual_rows([
                {'order': u_ords[i], 'costco_no': it['상품번호'],
                 'unit_price': it['단가'], 'via': 'manual'} for i in ois])
            _merge_matches(alloc, new, list(ois))
            st.success(f"✋ {len(new)}건 매칭 추가")
            st.rerun()


def _render_stock_status():
    """📦 현재 구입재고 — 영수증 입고분에서 주문 사용분을 뺀 잔량.

    정산 시점에만 계산되던 값을 상시 조회 가능하게 한다. 실물 재고와 대조하고
    묶여 있는 자금을 파악하려면 필요하다.
    """
    st.divider()
    st.subheader("📦 현재 구입재고")
    _sd = get_settle_start_date()
    st.caption(f"영수증 입고 − 주문 사용 = 잔량"
               + (f" · 기준일 **{_sd}** 이후" if _sd else " · 전체 기간"))
    try:
        rows = get_stock_status()
    except Exception as e:
        st.error(f"재고 조회 실패: {e}")
        return
    if not rows:
        st.info("현재 재고가 없습니다. 영수증을 업로드하면 구입분이 재고로 잡힙니다.")
        return

    _left = [r for r in rows if r['units_left'] > 0]
    _neg = [r for r in rows if r['units_left'] < 0]
    _amt = sum(r['amount'] for r in _left)
    _m1, _m2, _m3 = st.columns(3)
    _m1.metric("재고 품목", f"{len(_left)}종")
    _m2.metric("재고 금액", f"{fmt(_amt)}원")
    _m3.metric("소진 품목", f"{len(rows) - len(_left) - len(_neg)}종")

    if _neg:
        st.warning(f"⚠️ 사용량이 입고량을 넘은 품목 {len(_neg)}종 — 영수증 누락이 의심됩니다. "
                   "그날 구매한 영수증이 업로드됐는지 확인하세요.")

    _only = st.checkbox("잔량 있는 것만", value=True, key="rs_stock_only")
    _view = _left if _only else rows
    st.dataframe(pd.DataFrame([
        {'코스트코번호': r['costco_no'], '상품명': str(r['name'])[:34],
         '입고': r['units_in'], '사용': r['units_used'], '남음': r['units_left'],
         '단가': fmt(r['price']), '재고금액': fmt(r['amount'])}
        for r in _view
    ]), use_container_width=True, hide_index=True)
    st.caption("단위는 소분 단위입니다 — 1팩을 N개로 나눠 파는 상품은 팩이 아니라 낱개 기준입니다.")


def _render_history(dmap, USERNAME=''):
    st.divider()
    _h1, _h2 = st.columns([3, 1.3])
    _h1.subheader("📚 정산 이력")
    if _h2.button("🧹 삭제된 주문 정리", key="rs_cleanup_btn",
                  help="사용자가 삭제한(더 이상 존재하지 않는) 주문을 구매 정산 내역에서 일괄 제거합니다."):
        res = cleanup_orphan_settlements()
        if res.get('removed'):
            st.success(f"✅ 삭제된 주문 {res['removed']}건을 구매 정산 내역에서 정리했습니다. "
                       f"(검사 {res['checked']}건)")
        else:
            st.info(f"정리할 항목이 없습니다. (검사 {res.get('checked', 0)}건 — 모두 유효)")
    batches = list_settlement_batches(limit=30)
    if not batches:
        st.caption("아직 저장된 정산 배치가 없습니다.")
        return
    for b in batches:
        with st.expander(
            f"#{b['id']} · {b['label']} · 주문 {b['order_count']}건 · "
            f"총 {fmt(b['total_amount'])}원 · {b['created_at']}",
            expanded=False
        ):
            usum = get_user_settlement_summary(b['id'])
            if usum:
                st.dataframe(pd.DataFrame([
                    {'사용자': dmap.get(u['username'], u['username']),
                     '품목수': u['item_count'], '총수량': u['qty'],
                     '구매금액': fmt(u['amount'])} for u in usum
                ]), use_container_width=True, hide_index=True)
            # ── 근거 분해 · 부족분 · 재고분 (저장된 값 그대로) ──
            try:
                _basis = get_user_billing_basis(b['id']) or {}
            except Exception:
                _basis = {}
            if _basis:
                st.markdown("**🧾 청구 근거 분해**")
                st.dataframe(pd.DataFrame([
                    {'사용자': dmap.get(_u, _u),
                     '확정(번호)': f"{_v['확정'][0]}건 · {fmt(_v['확정'][1])}원",
                     '추정(이름)': f"{_v['추정'][0]}건 · {fmt(_v['추정'][1])}원",
                     '수동': f"{_v['수동'][0]}건 · {fmt(_v['수동'][1])}원"}
                    for _u, _v in sorted(_basis.items())
                ]), use_container_width=True, hide_index=True)

            try:
                _sh = get_settlement_shortages(b['id']) or []
            except Exception:
                _sh = []
            if _sh:
                _undec = [x for x in _sh if not str(x.get('decision') or '').strip()]
                st.markdown(f"**⚠️ 부족분 {len(_sh)}건** — 주문은 있는데 영수증에서 못 찾은 건"
                            + (f" · 미확인 {len(_undec)}건" if _undec else " · 전부 확인됨"))
                st.caption("실제로 **샀는데 매칭만 실패**한 건은 청구에 포함하고, "
                           "**정말 못 산 건**은 제외하세요. 제외한 건은 청구서에서 빠집니다.")
                _DEC_LABEL = {'bill': '✅ 청구포함', 'exclude': '🚫 청구제외', '': '⬜ 미확인'}
                _pick = []
                for x in _sh:
                    _k = f"rs_sh_{b['id']}_{x['id']}"
                    _c1, _c2 = st.columns([0.5, 9])
                    if _c1.checkbox("선택", key=_k, label_visibility="collapsed"):
                        _pick.append(x['id'])
                    _cur = str(x.get('decision') or '')
                    _c2.markdown(
                        f"{_DEC_LABEL.get(_cur, '⬜ 미확인')} · **{dmap.get(x['username'], x['username'])}** "
                        f"· {str(x.get('recipient') or '')} · {str(x.get('product_name') or '')[:40]} "
                        f"· {x.get('qty', 0)}개 <span style='color:#999'>({x.get('order_no', '')})</span>",
                        unsafe_allow_html=True)
                _b1, _b2, _b3 = st.columns(3)
                if _b1.button(f"✅ 선택 청구포함 ({len(_pick)})", key=f"rs_shb_{b['id']}",
                              disabled=not _pick, use_container_width=True):
                    set_shortage_decision(_pick, 'bill', USERNAME); st.rerun()
                if _b2.button(f"🚫 선택 청구제외 ({len(_pick)})", key=f"rs_shx_{b['id']}",
                              disabled=not _pick, use_container_width=True):
                    set_shortage_decision(_pick, 'exclude', USERNAME); st.rerun()
                if _b3.button(f"↩ 선택 미확인으로 ({len(_pick)})", key=f"rs_shr_{b['id']}",
                              disabled=not _pick, use_container_width=True):
                    set_shortage_decision(_pick, '', USERNAME); st.rerun()

            try:
                _lf = get_settlement_leftovers(b['id']) or []
            except Exception:
                _lf = []
            if _lf:
                _amt = sum(int(x['unit_price'] or 0) * int(x['units_left'] or 0)
                           // max(1, int(x['split_qty'] or 1)) for x in _lf)
                st.markdown(f"**📦 재고분 {len(_lf)}종** — 사고 남은 수량 (추정 {fmt(_amt)}원)")
                st.dataframe(pd.DataFrame([
                    {'코스트코번호': x['costco_no'], '상품명': str(x['name'])[:34],
                     '영수증수량': x['qty_receipt'], '사용': x['units_used'],
                     '남음': x['units_left'], '단가': fmt(x['unit_price'])} for x in _lf
                ]), use_container_width=True, hide_index=True)

            _c1, _c2 = st.columns([3, 1])
            if _c2.button("🗑 이 배치 삭제", key=f"rs_del_{b['id']}"):
                delete_settlement_batch(b['id'])
                st.rerun()


def _n(s):
    return str(s or '').strip()
