"""🧾 영수증 정산 (관리자) — 코스트코 영수증을 각 사용자 주문에 자동배치하고
각 주문 구입가에 실단가를 반영 + 사용자별 정산표 생성."""
import hashlib
from datetime import date, datetime, timedelta

import streamlit as st
import pandas as pd

from services import parse_costco_receipt_pdf, render_pdf_to_images
import receipt_settle as _rs
from db_receipt_settle import (save_match_draft, get_match_draft,
                               clear_match_draft, draft_dates)
from receipt_settle import (
    allocate_receipt_to_orders, apply_receipt_settlement, cleanup_orphan_settlements,
    learn_costco_mappings,
    build_manual_rows, build_memo_rows, ai_match_receipt_orders, _summarize, compute_leftovers,
    build_stock_pool, get_settle_start_date, get_stock_status, get_settled_order_keys,
    allocate_dispatched_to_receipt,
)
from db_receipt_settle import (
    save_settlement_batch, list_settlement_batches, get_settlement_items,
    get_settlement_shortages, get_settlement_leftovers, get_user_billing_basis,
    set_shortage_decision,
    get_user_settlement_summary, delete_settlement_batch,
)
from db import (
    get_all_users, get_all_settings, add_lot_units, find_lots_by_memo,
    get_receipt_items_by_date, receipt_dates_with_items,
    set_global_setting, save_receipt_items, upsert_shared_store_price,
)
from utils import fmt

invalidate_data_cache = None


def _set_cache_helpers(shared_fn=None, user_fn=None, merged_fn=None, invalidate_fn=None, **kwargs):
    global invalidate_data_cache
    invalidate_data_cache = invalidate_fn


def _merge_receipt_lines(parsed):
    """영수증 줄들을 (상품번호, 날짜)로 합친다. 반환: {키: 품목}

    같은 상품이 영수증에 **따로 두 줄**로 찍히는 일이 흔하다
    (2026-09-07: 995554 초콜릿아몬드 1개짜리 두 줄, 617031 유연제 두 줄,
     660569 피타브레드 두 줄, 741114 버터 두 줄).
    예전에는 나중 줄이 앞 줄을 덮어써서 절반이 사라졌다 — 2개 산 물건이
    1개로 청구됐다. 수량·금액·할인을 더한다.

    단가는 **실제 지불한 단가**로 다시 계산한다((금액-할인)/수량).
    청구는 실제 낸 돈으로 해야 한다. 영수증에 찍힌 정가는 '정가단가'로 남겨
    가격DB에 쓴다 — 일회성 쿠폰가를 상품 표준가로 굳히면 안 된다.
    """
    out = {}
    for p in (parsed or []):
        k = (_n(p.get('상품번호')) or _n(p.get('상품명')), _n(p.get('receipt_date')))
        _q = max(1, int(p.get('수량') or 1))
        _u = int(p.get('단가') or 0)
        _a = int(p.get('금액') or 0) or _u * _q
        _d = int(p.get('할인') or 0)
        e = out.get(k)
        if e is None:
            out[k] = {'상품번호': p.get('상품번호', ''), '상품명': p.get('상품명', ''),
                      '수량': _q, '금액': _a, '할인': _d, '정가단가': _u,
                      'receipt_date': p.get('receipt_date', '')}
        else:
            e['수량'] += _q
            e['금액'] += _a
            e['할인'] += _d
            e['정가단가'] = e['정가단가'] or _u
    for e in out.values():
        _q = max(1, int(e['수량']))
        e['단가'] = max(0, int(e['금액']) - int(e['할인'])) // _q
    return out


def _persist_receipt(username, items):
    """영수증 품목을 DB에 남긴다 — receipt_items + 공유DB 매장 매입가.

    영수증 정산 화면에서 올린 영수증이 그동안 어디에도 저장되지 않았다. 그래서
      · 재고 이월(build_stock_pool)이 늘 비어 있었고
      · 구매내역 정산의 단가가 영수증 실단가로 갱신되지 않았다
    (admin의 receipt_items는 8/19에서 멈춰 있었다).
    별도 '영수증' 페이지에서만 저장하고 있었는데, 정산은 이 화면에서 하니
    실제로는 저장 없이 정산만 돌아간 셈이다.
    저장 실패가 화면 흐름을 끊지는 않는다.
    반환: (신규, 갱신, 매입가반영)
    """
    _rows = [it for it in (items or [])
             if str(it.get('상품명', '') or '').strip()
             and str(it.get('receipt_date', '') or '').strip()]
    if not _rows:
        return 0, 0, 0
    _saved = _updated = _pn = 0
    _detail, _skip = [], []
    try:
        _saved, _updated = save_receipt_items(username, _rows)
    except Exception as _e:
        st.caption(f"⚠️ 영수증 DB 저장 실패: {_e}")
    for _it in _rows:
        _pno = str(_it.get('상품번호', '') or '').strip()
        _nm = str(_it.get('상품명', '') or '')
        try:
            _pr = int(float(_it.get('단가') or 0))
        except (TypeError, ValueError):
            _pr = 0
        # 상품번호나 단가가 없으면 가격DB에 넣을 수 없다. 조용히 건너뛰면
        # '왜 저장이 안 되지'가 되므로 이유를 남긴다.
        if not _pno:
            _skip.append((_nm, '코스트코 상품번호를 못 읽음'))
            continue
        if _pr <= 0:
            _skip.append((_nm, '단가를 못 읽음'))
            continue
        try:
            # 가격DB에는 **정가**를 넣는다. 쿠폰으로 싸게 산 값을 상품 표준가로
            # 굳히면, 쿠폰이 끝난 뒤의 예상 구매가가 실제보다 낮게 잡힌다.
            _list = int(_it.get('정가단가') or 0) or _pr
            _r = upsert_shared_store_price(
                costco_name=_nm, keyword=_nm,
                price=_list, product_no=_pno, updated_by=username,
                receipt_date=str(_it.get('receipt_date', '') or ''), force_store=True,
                source='receipt-settle')
            _pn += 1
            _detail.append({'상품번호': _pno, '상품명': _nm,
                            '이전가': int((_r or {}).get('prev') or 0),
                            '저장가': _pr,
                            '결과': {'new': '신규 등록', 'changed': '가격 변경',
                                     'same': '동일(확인)'}.get(
                                         str((_r or {}).get('status')), '저장')})
        except Exception as _e:
            _skip.append((_nm, f'저장 실패: {str(_e)[:40]}'))
    st.session_state['_rs_price_detail'] = {'rows': _detail, 'skip': _skip}
    return _saved, _updated, _pn


def _render_price_result():
    """직전 영수증 업로드가 가격DB에 무엇을 했는지 편다.

    '반영 N종'이라는 숫자 하나로는 저장 여부를 확인할 수 없다. 같은 값으로
    다시 올리면 가격 이력에도 아무것도 안 남아서(변동분만 기록) 안 된 것처럼
    보인다. 품목별로 이전가/저장가/결과를 보여줘야 확인이 끝난다.
    """
    _d = st.session_state.get('_rs_price_detail') or {}
    _rows, _skip = _d.get('rows') or [], _d.get('skip') or []
    if not (_rows or _skip):
        return
    _new = sum(1 for r in _rows if r['결과'] == '신규 등록')
    _chg = sum(1 for r in _rows if r['결과'] == '가격 변경')
    _sam = sum(1 for r in _rows if r['결과'] == '동일(확인)')
    _title = (f"💰 가격DB 반영 결과 — 신규 {_new} · 변경 {_chg} · 동일 {_sam}"
              + (f" · 건너뜀 {len(_skip)}" if _skip else ""))
    with st.expander(_title, expanded=bool(_skip)):
        if _rows:
            st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
            st.caption("**동일(확인)** 은 이미 같은 값이 들어 있어 바꿀 게 없었다는 뜻입니다 — "
                       "저장은 정상입니다. 가격 이력에는 값이 바뀐 것만 남습니다.")
        if _skip:
            st.warning("가격DB에 넣지 못한 품목 — " + " · ".join(
                f"{n}({why})" for n, why in _skip[:8]))
            st.caption("상품번호를 못 읽은 품목은 위 표에서 번호를 채운 뒤 다시 올리면 "
                       "가격이 저장됩니다.")


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
        "재고 계산 시작일 (이 날짜부터 쌓인 영수증만 재고로 봅니다)",
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
        st.warning("⚠️ 재고 계산 시작일이 없어 **모든 과거 영수증**이 재고로 계산됩니다. "
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
    # 파일 이름만으로 키를 만들면, 판독 로직을 고친 뒤 같은 파일을 다시 올려도
    # '이미 읽은 파일'로 보고 건너뛴다. 크기와 '다시 읽기' 횟수를 함께 넣는다.
    _reparse = int(st.session_state.get('_rs_reparse') or 0)
    _fkey = (tuple(sorted((f.name, getattr(f, 'size', 0)) for f in files)), _reparse) \
        if files else ()
    if files and st.button("🔄 올린 영수증 다시 읽기", key="rs_reparse_btn",
                           help="판독이 틀렸거나 프로그램이 바뀐 뒤 같은 파일을 "
                                "다시 읽습니다."):
        st.session_state['_rs_reparse'] = _reparse + 1
        st.session_state.pop('_rs_fkey', None)
        st.rerun()
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
                            '금액': int(_it.get('금액') or 0),
                            '할인': int(_it.get('할인') or 0),
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
        parsed, _snap0 = _rs.snap_items_to_catalog(parsed)
        if _snap0:
            with st.expander(f"🔧 상품번호 자동 교정 {len(_snap0)}건", expanded=False):
                for _l in _snap0:
                    st.caption("· " + _l)
        merged = _merge_receipt_lines(parsed)
        st.session_state['rs_receipt_items'] = list(merged.values())
        _sv, _up, _pn = _persist_receipt(USERNAME, list(merged.values()))
        if _sv or _up or _pn:
            st.caption(f"💾 영수증 DB 저장 — 신규 {_sv} · 갱신 {_up} · 공유DB 매입가 반영 {_pn}종")
        _render_price_result()
        st.session_state['_rs_fkey'] = _fkey
        st.session_state['_rs_fails'] = fails
        st.session_state.pop('rs_alloc', None)   # 새 업로드 → 이전 미리보기 초기화
        st.session_state.pop('rs_day', None)     # 새 영수증 → 정산일을 새 영수증 날짜로 재설정

    # ── 1-a) 🚚 발송 파일 업로드 — 주문번호로 사용자 분류 ──
    #   영수증과 발송건을 함께 올려야 각 사용자의 주문건이 정리된다.
    #   구매내역 정산은 그 결과를 확인·검수만 한다.
    try:
        import dispatch_upload as _du
        _du.render_panel(_disp_map(), USERNAME)
    except Exception as _e:
        st.caption(f"⚠️ 발송 파일 업로드 화면을 열지 못했습니다: {_e}")

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
                        '금액': int(_it.get('금액') or 0),
                        '할인': int(_it.get('할인') or 0),
                        'receipt_date': _rdate,
                    })
                _da = int(_data.get('discount_amount') or 0)
                _di = sum(int(x.get('할인') or 0) for x in (_data.get('items') or []))
                if _da and _da > _di:
                    st.warning(
                        f"⚠️ {_pf.name} — 할인 **{fmt(_da)}원**을 읽었지만 어느 품목 "
                        f"것인지 가르지 못했습니다(품목별 합계 {fmt(_di)}원). "
                        "아래 표의 **할인** 칸에 직접 넣어 주세요 — 안 넣으면 "
                        "할인 전 가격으로 청구됩니다.")
                _note = []
                if _data.get('_tiled'):
                    _note.append("세로로 잘라 다시 읽음")
                if _data.get('_repaired'):
                    _note.append(f"단가 {_data['_repaired']}줄 보정")
                if _note:
                    st.caption(f"🔍 {_pf.name} — " + " · ".join(_note))
                if not _data.get('_verified', True):
                    _pfails.append((_pf.name,
                                    "금액·수량 자가검증 불일치 — 아래 표에서 값을 확인하세요: "
                                    + " / ".join((_data.get('_check') or [])[:2])))
            _pbar.empty()
            # 이미 산 적 있는 상품 목록을 정답지로 삼아 번호 오독을 바로잡는다
            _pparsed, _snap = _rs.snap_items_to_catalog(_pparsed)
            if _snap:
                with st.expander(f"🔧 상품번호 자동 교정 {len(_snap)}건", expanded=False):
                    for _l in _snap:
                        st.caption("· " + _l)
            if _pparsed:
                # 이미 올린 것(PDF 등)과 합친다. 키 규칙이 다르면 같은 품목이
                # 두 번 들어가므로 양쪽 다 _merge_receipt_lines로 만든다.
                _prev = _merge_receipt_lines(
                    (st.session_state.get('rs_receipt_items') or []) + _pparsed)
                _merged_p = _merge_receipt_lines(_pparsed)
                st.session_state['rs_receipt_items'] = list(_prev.values())
                _sv, _up, _pn = _persist_receipt(USERNAME, list(_prev.values()))
                if _sv or _up or _pn:
                    st.caption(f"💾 영수증 DB 저장 — 신규 {_sv} · 갱신 {_up} · "
                               f"공유DB 매입가 반영 {_pn}종")
                _render_price_result()
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
    if not _seed:
        # 세션이 비었다고 영수증이 없는 건 아니다 — 새로고침·재시작이면 DB에 있다.
        # 지난 날짜를 다시 열어 검수하려면 여기서 되읽어야 한다.
        try:
            _rdates = receipt_dates_with_items(limit=30)
        except Exception:
            _rdates = []
        if _rdates:
            _dopts = [f"{_d} ({_c}종)" for _d, _c in _rdates]
            _dc1, _dc2 = st.columns([2, 1])
            _dpick = _dc1.selectbox("저장된 영수증 불러오기", ['(선택)'] + _dopts,
                                    key="rs_load_date")
            _dc2.write("")
            if _dpick != '(선택)' and _dc2.button("📂 불러오기", key="rs_load_btn",
                                                use_container_width=True):
                _dsel = _rdates[_dopts.index(_dpick)][0]
                _got = get_receipt_items_by_date(USERNAME, _dsel)
                if _got:
                    st.session_state['rs_receipt_items'] = _got
                    st.session_state.pop('rs_alloc', None)
                    st.rerun()
                else:
                    st.warning(f"{_dsel} 영수증을 찾지 못했습니다.")
        _seed = st.session_state.get('rs_receipt_items') or []
    _rd_by_cno = {_n(p.get('상품번호')): (p.get('receipt_date', '') or '')
                  for p in _seed if _n(p.get('상품번호'))}
    _list_by_cno = {_n(p.get('상품번호')): int(p.get('정가단가') or 0) for p in _seed}
    _seed_rows = [{'상품번호': _n(p.get('상품번호')), '상품명': _n(p.get('상품명')),
                   '수량': int(p.get('수량') or 1),
                   '정가': int(p.get('정가단가') or p.get('단가') or 0),
                   '할인': int(p.get('할인') or 0),
                   '단가': int(float(p.get('단가') or 0))}
                  for p in _seed] or [{'상품번호': '', '상품명': '', '수량': 1,
                                       '정가': 0, '할인': 0, '단가': 0}]
    edited = st.data_editor(
        pd.DataFrame(_seed_rows), num_rows='dynamic', use_container_width=True,
        key=f"rs_item_editor_{hashlib.md5(str(_seed_rows).encode()).hexdigest()[:8]}",
        column_config={
            '상품번호': st.column_config.TextColumn('코스트코 상품번호'),
            '상품명': st.column_config.TextColumn('상품명'),
            '수량': st.column_config.NumberColumn('수량', min_value=1, step=1),
            '정가': st.column_config.NumberColumn(
                '정가(원)', min_value=0, step=100,
                help='영수증에 찍힌 단가. 가격DB에는 이 값이 들어갑니다.'),
            '할인': st.column_config.NumberColumn(
                '할인(원)', min_value=0, step=100,
                help='그 품목에 붙은 쿠폰(CPN) 합계. 수량 전체에 대한 금액입니다.'),
            '단가': st.column_config.NumberColumn(
                '실단가(원)', min_value=0, step=100,
                help='실제로 낸 단가 = (정가×수량 − 할인) ÷ 수량. 청구는 이 값으로 합니다.'),
        },
    )
    st.caption("**실단가**로 청구합니다 — 쿠폰(CPN) 할인을 뺀 실제 지불 단가입니다. "
               "**정가**는 가격DB에 저장돼 다음 예상가로 쓰입니다(일회성 쿠폰가를 "
               "상품 표준가로 굳히지 않기 위해서입니다).")
    receipt_items = []
    for r in edited.to_dict('records'):
        cno = _n(r.get('상품번호'))
        try:
            up = int(float(r.get('단가') or 0))
        except (TypeError, ValueError):
            up = 0
        if cno and up > 0:
            # 관리자가 정가·할인·수량을 고치면 실단가를 다시 계산한다.
            # 표에 손을 대는 이유가 대개 '판독이 틀려서'인데, 실단가만 남겨 두면
            # 고친 값이 청구에 반영되지 않는다.
            _lp = int(float(r.get('정가') or 0))
            _dc = int(float(r.get('할인') or 0))
            _qy = max(1, int(r.get('수량') or 1))
            if _lp > 0 and (_dc or _lp * _qy != up * _qy):
                _calc = max(0, _lp * _qy - _dc) // _qy
                if _calc > 0:
                    up = _calc
            receipt_items.append({'상품번호': cno, '상품명': _n(r.get('상품명')),
                                  '수량': _qy, '단가': up,
                                  '정가단가': _lp or up, '할인': _dc,
                                  'receipt_date': _rd_by_cno.get(cno, '')})
    if receipt_items:
        # 표에서 고친 값은 세션에만 있다 — 판독이 틀려 고쳤다면 그게 진짜 값이다.
        _ec1, _ec2 = st.columns([1, 3])
        if _ec1.button("💾 표 내용 저장", key="rs_items_save", use_container_width=True):
            st.session_state['rs_receipt_items'] = list(receipt_items)
            _s2, _u2, _p2 = _persist_receipt(USERNAME, list(receipt_items))
            st.session_state.pop('rs_alloc', None)
            st.success(f"💾 영수증 {len(receipt_items)}종 저장 — 신규 {_s2} · 갱신 {_u2} · "
                       f"가격DB 반영 {_p2}종")
        _ec2.caption("표에서 **정가·할인·수량**을 고쳤다면 저장하세요 — 저장해야 "
                     "다음에 열 때도 남고, 배치·청구에도 그 값이 쓰입니다.")
        _t_qty = sum(int(x.get('수량') or 1) for x in receipt_items)
        _t_list = sum(int(x.get('정가단가') or x.get('단가') or 0) * int(x.get('수량') or 1)
                      for x in receipt_items)
        _t_disc = sum(int(x.get('할인') or 0) for x in receipt_items)
        _t_pay = sum(int(x.get('단가') or 0) * int(x.get('수량') or 1)
                     for x in receipt_items)
        _line = (f"🧾 **{len(receipt_items)}종 · 총수량 {_t_qty}개** · "
                 f"정가 {fmt(_t_list)}원")
        if _t_disc:
            _line += (f" − 할인 **{fmt(_t_disc)}원** = "
                      f"실지불 **{fmt(_t_pay)}원**")
        else:
            _line += f" = 실지불 **{fmt(_t_pay)}원**"
        st.markdown(_line)
        if _t_disc:
            st.caption(f"영수증의 '쿠폰합계'와 이 할인액이 같아야 맞게 읽은 것입니다. "
                       f"총수량은 영수증의 '총 판매 상품 수'와 같아야 합니다.")
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
    # 코스트코에 가는 날과 주문이 들어온 날은 어긋난다 — 마감(기본 12:00) 이후 주문은
    # 다음날 장을 보므로, 오늘 산 물건의 주문일은 어제(혹은 그 전날)다. 당일만 보면
    # 그 주문들이 통째로 미매칭이 됐다(8/19 실측: 콩담백면은 8/17 주문과 100% 일치).
    # 이전 날짜는 '아직 정산 안 된 주문'만 후보로 넣어 이중 반영을 막는다.
    _basis = st.radio(
        "정산 기준",
        ["📦 일일주문 기준 (권장)",
         "🚚 출고 기준 (장보기 목록 + 영수증 + 일괄발송)",
         "📅 결제일 기준 (구버전)"],
        key="rs_basis2", horizontal=True,
        help="일일주문 기준: 화면의 '일일 주문 수집'에 잡힌 그날 발송할 주문을 그날 "
             "영수증으로 정산합니다. 결제일 기준은 order_history(결제일)를 봐서 "
             "일일주문과 대상이 어긋납니다. "
             "출고 기준: 그날 일괄발송한 주문을 장보기 목록을 브리지로 연결합니다.")
    _by_dispatch = _basis.startswith("🚚")
    _by_daily = _basis.startswith("📦")

    if _by_dispatch:
        st.caption(f"**{d_day}** 에 일괄발송(출고)한 주문을, 같은 날 **장보기 목록**을 브리지로 "
                   "위 영수증 품목과 연결해 정산합니다. 이미 정산된 주문은 제외됩니다.")
        _lookback = 0
        d_from = d_to = d_day
    else:
        _lookback = st.number_input(
            "이전 주문도 포함 (일)", value=2, min_value=0, max_value=7, step=1, key="rs_lookback",
            help="마감 이후 주문은 다음날 장을 보기 때문에, 오늘 영수증이 어제·그제 주문에 대응합니다. "
                 "이미 정산된 주문은 자동으로 제외되므로 중복 청구되지 않습니다.")
        d_to = d_day
        d_from = d_day - timedelta(days=int(_lookback))
        _carry_days = st.number_input(
            "미정산 이월 조회 (일) — 0이면 사용 안 함", value=14, min_value=0, max_value=60,
            step=1, key="rs_carry",
            help="품절이라 며칠 뒤에 샀거나 주문이 밀린 건을 잡습니다. 위 기간보다 "
                 "더 이전의 **아직 정산 안 된** 주문까지 훑되, 코스트코 상품번호가 "
                 "정확히 일치할 때만 붙입니다(이름 유사도·재고 이월은 적용하지 않음). "
                 "그래서 기간을 넓혀도 오매칭이 늘지 않습니다.")
        _basis_word = "일일주문" if _by_daily else "결제"
        st.caption(f"**{d_from} ~ {d_to}** {_basis_word} 주문 중 **아직 정산되지 않은 건**에서 "
                   "위 영수증 상품번호와 일치하는 주문을 찾아 배치합니다.")

    _auto_ai = st.checkbox(
        "🤖 남은 미매칭은 AI가 바로 매칭", value=True, key="rs_auto_ai",
        help="영수증 상품명(축약어)과 네이버 상품명(검색용 긴 이름)은 겹치는 단어가 "
             "거의 없어 규칙만으로는 안 붙습니다. 한 번 이어 두면 다음부터는 "
             "번호로 바로 붙으므로 AI 비용은 상품마다 한 번만 듭니다.")
    if st.button("🔎 당일 자동배치 미리보기", type="primary", key="rs_preview_btn"):
        with st.spinner("주문을 조회해 배치 중..."):
            # 재고 이월 — 당일 영수증에 없는 주문을 과거 구매분(가용 재고)에서 찾는다.
            #   실측(8/15~19): 미매칭 159건 → 100건으로 59건 감소.
            _pool = build_stock_pool(str(d_to), exclude_dates=[str(d_day)])
            _settled = get_settled_order_keys()
            if _by_dispatch:
                # 재고 이월은 마지막 수단이다. 먼저 물어 가면 오늘 영수증에
                # 있는 물건까지 과거 재고로 처리돼 그때 단가로 청구된다.
                # AI까지 돌린 뒤 남은 것에만 이월을 적용한다.
                alloc = allocate_dispatched_to_receipt(
                    receipt_items, str(d_day), stock_pool=_pool,
                    exclude_orders=_settled, defer_stock=True,
                )
            else:
                alloc = allocate_receipt_to_orders(
                    receipt_items, str(d_from), str(d_to), stock_pool=_pool,
                    exclude_orders=_settled,
                    basis=('daily' if _by_daily else 'payment'),
                    carry_days=int(_carry_days or 0),
                )
        alloc['_settled_skipped'] = len(_settled)
        # 손으로 한 배정·매칭을 다시 얹는다. 재계산이 사람의 판단을 지우면 안 된다.
        # 세션 것과 DB 초안을 합친다 — 창을 닫았다 열어도 이어서 할 수 있어야 한다.
        _sticky = list((st.session_state.get('rs_sticky') or {}).get(str(d_day)) or [])
        try:
            _seen_k = {(r.get('username'), r.get('order_no')) for r in _sticky}
            _sticky += [r for r in (get_match_draft(str(d_day)) or [])
                        if (r.get('username'), r.get('order_no')) not in _seen_k]
        except Exception:
            pass
        if _sticky:
            _have = {(r.get('username'), r.get('order_no')) for r in alloc['rows']}
            _re = [r for r in _sticky if (r.get('username'), r.get('order_no')) not in _have]
            if _re:
                _merge_matches(alloc, _re, [], sticky=False)
                alloc['_restored'] = len(_re)
        if _auto_ai and alloc.get('unmatched_orders') and alloc.get('unmatched_receipt'):
            _ak2 = _resolve_ai_key('anthropic_api_key', settings)
            _gk2 = _resolve_ai_key('gemini_api_key', settings)
            if _ak2 or _gk2:
                with st.spinner("남은 미매칭을 AI가 확인 중..."):
                    _pr, _perr2 = ai_match_receipt_orders(
                        alloc['unmatched_receipt'], alloc['unmatched_orders'],
                        anthropic_key=_ak2, gemini_key=_gk2)
                if _pr:
                    _new2 = build_manual_rows([
                        {'order': alloc['unmatched_orders'][x['order_index']],
                         'costco_no': x['costco_no'], 'unit_price': x['unit_price'],
                         'via': 'ai'} for x in _pr])
                    _merge_matches(alloc, _new2, [x['order_index'] for x in _pr])
                    alloc['_auto_ai'] = len(_new2)
                elif _perr2:
                    alloc['_auto_ai_err'] = _perr2
        # AI까지 끝난 뒤 남은 것만 과거 재고에서 메꾼다
        if _by_dispatch and _pool:
            try:
                _nc = _rs.apply_stock_carry(alloc, _pool)
                if _nc:
                    alloc['_stock_carry'] = _nc
            except Exception as _e:
                st.caption(f"⚠️ 재고 이월 계산 실패: {_e}")
        st.session_state['rs_alloc'] = alloc

    st.session_state['rs_sticky_date'] = str(d_day)
    alloc = st.session_state.get('rs_alloc')
    _sk_now = (st.session_state.get('rs_sticky') or {}).get(str(d_day)) or []
    if _sk_now:
        _sc1, _sc2 = st.columns([3, 1])
        _sc1.info(f"📌 손으로 넣은 배정·매칭 **{len(_sk_now)}건**을 붙들고 있습니다 — "
                  "미리보기를 다시 눌러도 유지됩니다."
                  + (f" (이번에 {alloc['_restored']}건 복원)"
                     if alloc and alloc.get('_restored') else ""))
        if _sc2.button("🧹 배정 지우기", key="rs_sticky_clear"):
            _st = st.session_state.get('rs_sticky') or {}
            _st.pop(str(d_day), None)
            st.session_state['rs_sticky'] = _st
            st.session_state.pop('rs_alloc', None)
            st.rerun()
    if alloc and alloc.get('_auto_ai'):
        st.success(f"🤖 규칙으로 못 붙은 {alloc['_auto_ai']}건을 AI가 이었습니다 — "
                   "정산을 확정하면 이 연결이 저장돼 다음부터는 번호로 바로 붙습니다.")
    if alloc and alloc.get('_stock_carry'):
        st.info(f"📦 남은 {alloc['_stock_carry']}건은 **과거 구매분(재고)**에서 메꿨습니다 — "
                "오늘 영수증에 없는 상품이라 그때 산 단가로 청구됩니다.")
    if alloc and alloc.get('_auto_ai_err'):
        st.warning(f"⚠️ AI 자동매칭 실패: {alloc['_auto_ai_err']} — 아래 수동 매칭을 쓰세요.")
    if not alloc:
        _render_stock_status()
        _render_history(_disp_map(), USERNAME)
        return

    dmap = _disp_map()
    rows = alloc['rows']
    # 정가 표는 rows가 비어도 필요하다(미매칭 배정 표에서 쓴다) — 밖에서 만든다.
    _list_by = {_n(x.get('상품번호')): int(x.get('정가단가') or x.get('단가') or 0)
                for x in receipt_items}
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
        # 행마다 '정가로 샀다면 얼마였나'를 계산해 할인액을 사용자별로 가른다.
        # 쿠폰은 상품에 붙지만 청구는 사람별이라, 누가 얼마를 덜 냈는지 보여야 한다.
        _disc_by_user = {}
        for r in rows:
            _lu = _list_by.get(_n(r.get('costco_no')), 0)
            if not _lu or _lu <= int(r.get('unit_price') or 0):
                continue
            _sp = max(1, int(r.get('split_qty') or 1))
            _full = (_lu // _sp) * int(r.get('qty') or 1)
            _d = _full - int(r.get('amount') or 0)
            if _d > 0:
                _disc_by_user[r['username']] = _disc_by_user.get(r['username'], 0) + _d
        srows = [{'사용자': dmap.get(u, u), '품목수': s['count'], '총수량': s['qty'],
                  '할인액': fmt(_disc_by_user.get(u, 0)),
                  '구매금액(정산)': fmt(s['amount'])} for u, s in
                 sorted(summary.items(), key=lambda kv: -kv[1]['amount'])]
        st.dataframe(pd.DataFrame(srows), use_container_width=True, hide_index=True)
        _tot = sum(s['amount'] for s in summary.values())
        _tot_d = sum(_disc_by_user.values())
        st.markdown(f"### 합계 구매금액: **{fmt(_tot)}원**  ·  주문 {len(rows)}건  ·  "
                    f"사용자 {len(summary)}명"
                    + (f"  ·  할인 반영 **{fmt(_tot_d)}원**" if _tot_d else ""))
        if _tot_d:
            st.caption("**할인액**은 쿠폰(CPN)으로 덜 낸 금액입니다 — 구매금액에는 이미 "
                       "빠져 있습니다. 사용자에게 청구 근거를 설명할 때 쓰세요.")
        # 매칭 경로 내역 — 어떤 근거로 붙었는지 보여야 오매칭을 잡을 수 있다
        _via_lbl = {'number': '상품번호', 'name': '상품명 유사도', 'stock': '재고 이월',
                    'carry': '미정산 이월(번호 일치)', 'shopping': '장보기 목록',
                    'shopping-name': '장보기 이름', 'manual': '수동', 'ai': 'AI',
                    'memo': '관리자 메모 배정(주문 없음)'}
        _via_cnt = {}
        for r in rows:
            _k = str(r.get('via') or '')
            _via_cnt[_k] = _via_cnt.get(_k, 0) + 1
        if _via_cnt:
            st.caption("매칭 경로 — " + " · ".join(
                f"{_via_lbl.get(k, k or '기타')} {v}건"
                for k, v in sorted(_via_cnt.items(), key=lambda kv: -kv[1])))
        _cy = alloc.get('carry') or {}
        if _cy.get('scanned'):
            st.caption(f"↩️ 미정산 이월: {_cy['date_from']} 이후 미정산 주문 "
                       f"{_cy['scanned']}건을 훑어 **{_cy['matched']}건**을 번호 일치로 붙였습니다.")

        with st.expander(f"🔍 배치 상세 ({len(rows)}건) — 주문별 구입가 반영 내역", expanded=False):
            drows = []
            for r in rows:
                _lu = _list_by.get(_n(r.get('costco_no')), 0)
                _sp = max(1, int(r.get('split_qty') or 1))
                _full = (_lu // _sp) * int(r.get('qty') or 1) if _lu else 0
                _d = max(0, _full - int(r.get('amount') or 0)) if _lu else 0
                drows.append({
                    '사용자': dmap.get(r['username'], r['username']),
                    '주문번호': r['order_no'], '주문일': r['order_date'],
                    '상품명': r['product_name'], '수량': r['qty'],
                    '코스트코번호': r['costco_no'],
                    '정가': fmt(_lu) if _lu else '',
                    '실단가': fmt(r['unit_price']),
                    '할인': fmt(_d) if _d else '',
                    '기존구입가': fmt(r['prev_cost']), '→ 새구입가': fmt(r['amount']),
                    '근거': _via_lbl.get(str(r.get('via') or ''), r.get('via') or ''),
                    '메모': str(r.get('memo') or '')})
            st.dataframe(pd.DataFrame(drows), use_container_width=True, hide_index=True)

    if unmatched:
        # 배정하고 rerun하면 접혀 버려 매번 다시 열어야 했다. 한 번 열면
        # 남은 게 없어질 때까지 열어 둔다 — 여러 사용자에게 나눠 배정하는 화면이다.
        _um_open = bool(st.session_state.get('_rs_um_open'))
        with st.expander(f"⚠️ 주문을 못 찾은 영수증 품목 {len(unmatched)}건",
                         expanded=_um_open):
            st.caption("해당 상품의 주문이 당일 없거나, 제품 DB에 코스트코↔네이버 번호 매핑이 없어 배치 못 함. "
                       "**이전 주문의 교환·추가 발송분이라 주문 목록에 없는 경우**는 아래에서 "
                       "사용자를 지정해 직접 배정하세요.")
            # 사용자는 표 안에서 고르지 않는다. 표 안 SelectboxColumn은
            #   · 기본값을 바꾸면 표 전체가 다시 그려져 체크와 행별 입력이 날아가고
            #   · 옵션에 없는 값(빈 문자열)은 None으로 렌더돼 아예 못 고른다.
            # 대신 '체크한 행을 이 사람에게 배정'으로 흐름을 단순화한다.
            # 받는 사람이 서로 다르면 나눠서 두 번 배정하면 된다 —
            # 배정한 품목은 목록에서 바로 빠지므로 자연스럽게 이어진다.
            _um_opts = sorted(dmap.keys(), key=lambda u: dmap.get(u, u))
            _um_labels = [dmap.get(u, u) for u in _um_opts] or [USERNAME]
            _um_l2u = {dmap.get(u, u): u for u in _um_opts} or {USERNAME: USERNAME}
            _um_def = dmap.get(USERNAME, USERNAME)
            if _um_def not in _um_labels:
                _um_def = _um_labels[0]
            _um_bulk = st.selectbox(
                "배정할 사용자 — 아래에서 체크한 품목만 이 사람에게 청구됩니다",
                _um_labels, index=_um_labels.index(_um_def),
                key=f"rs_memo_bulk_{d_day}",
                help="교환·추가 발송분을 실제로 받은 사용자를 고르세요. "
                     "받는 사람이 서로 다르면 나눠서 두 번 배정하면 됩니다.")

            # 표는 체크만 받는다. 수량을 표 안에서 고치면 편집이 되돌아가는 일이
            # 있어(셀 수정 → rerun → 표 재생성) 수량·메모는 아래에서 따로 받는다.
            _um_rows = [{'배정': False,
                         '상품번호': u['상품번호'], '상품명': u['상품명'],
                         '영수증수량': int(u.get('영수증수량') or 1),
                         '남은수량': int(u.get('남은수량') or u.get('영수증수량') or 1),
                         '정가': _list_by.get(_n(u['상품번호']), 0),
                         '팩단가': int(u['단가'] or 0)} for u in unmatched]
            _um_sig = hashlib.md5("|".join(
                f"{u['상품번호']}:{u.get('남은수량') or u.get('영수증수량') or 1}"
                for u in unmatched).encode()).hexdigest()[:8]
            # 편집기 key에 사용자를 넣지 않는다 — 사용자를 바꿀 때마다 표가
            # 초기화되면 체크해 둔 것이 사라진다.
            _um_ed = st.data_editor(
                pd.DataFrame(_um_rows), use_container_width=True, hide_index=True,
                key=f"rs_memo_editor_{d_day}_{_um_sig}",
                disabled=['상품번호', '상품명', '팩단가', '영수증수량', '남은수량', '정가'],
                column_config={
                    '배정': st.column_config.CheckboxColumn(
                        '배정', help='체크하면 아래에 수량·메모 입력칸이 생깁니다'),
                    '정가': st.column_config.NumberColumn(
                        '정가', format='%d', help='영수증에 찍힌 단가(할인 전)'),
                    '팩단가': st.column_config.NumberColumn(
                        '팩단가', format='%d',
                        help='쿠폰 할인을 뺀 실제 지불 단가. 이 값으로 청구됩니다.'),
                    '영수증수량': st.column_config.NumberColumn(
                        '영수증수량', format='%d', help='영수증에 찍힌 구매 팩 수'),
                    '남은수량': st.column_config.NumberColumn(
                        '남은수량', format='%d',
                        help='아직 아무에게도 주지 않은 수량. 이만큼까지 배정할 수 있습니다.'),
                })
            _checked = [_um_rows[i] for i, r in enumerate(_um_ed.to_dict('records'))
                        if r.get('배정')]

            _um_pick = []
            if _checked:
                st.markdown("**배정 수량 · 메모** — 남은 수량까지만 넣을 수 있습니다")
                for _c in _checked:
                    _left = int(_c['남은수량'])
                    _k = f"rs_asg_{d_day}_{_c['상품번호']}"
                    _q1, _q2, _q3 = st.columns([2.4, 1, 2.6])
                    _q1.markdown(f"**{_c['상품명']}** &nbsp; "
                                 f"<span style='color:#888'>{_c['상품번호']} · "
                                 f"{fmt(_c['팩단가'])}원 · 남은 {_left}팩</span>",
                                 unsafe_allow_html=True)
                    _qty = _q2.number_input(
                        "수량(팩)", min_value=1, max_value=max(1, _left), step=1,
                        value=_left, key=f"{_k}_q", label_visibility="collapsed")
                    _memo = _q3.text_input(
                        "메모", key=f"{_k}_m", label_visibility="collapsed",
                        placeholder="사유 (예: 8/28 미배송분 발송)")
                    _um_pick.append({'상품번호': _c['상품번호'], '상품명': _c['상품명'],
                                     '팩단가': _c['팩단가'], '수량(팩)': int(_qty),
                                     '남은수량': _left, '메모': _memo})

            st.caption("여러 사람이 나눠 가지는 물건은 **한 명씩 나눠 배정**하세요 — "
                       "A에게 일부를 주면 남은 수량이 줄어든 채 목록에 남고, "
                       "그 상태에서 B에게 나머지를 주면 됩니다.")
            if _um_pick:
                _um_amt = sum(int(r['팩단가']) * int(r['수량(팩)']) for r in _um_pick)
                st.markdown(f"**{_um_bulk}** 에게 배정 **{len(_um_pick)}종** · "
                            f"청구금액 **{fmt(_um_amt)}원**")
                st.caption(" · ".join(
                    f"{r['상품명']} {r['수량(팩)']}/{r['남은수량']}팩"
                    + (f" ({r['메모']})" if str(r.get('메모') or '').strip() else "")
                    for r in _um_pick))
            if st.button(f"🧑‍💼 선택한 {len(_um_pick)}종을 {_um_bulk}에게 배정",
                         key="rs_memo_apply", type="primary", disabled=not _um_pick):
                _uname = _um_l2u.get(_um_bulk, '')
                _asg = [{'username': _uname,
                         'costco_no': str(r.get('상품번호') or ''),
                         'product_name': str(r.get('상품명') or ''),
                         'unit_price': int(r.get('팩단가') or 0),
                         'qty': int(r.get('수량(팩)') or 1),
                         'memo': str(r.get('메모') or '').strip()} for r in _um_pick]
                _new = build_memo_rows(_asg, str(d_day))
                if _new:
                    _merge_matches(alloc, _new, [])
                    # 배정하면 남은 수량이 줄어드는데, 수량칸에 옛 값(예: 3)이
                    # 남아 있으면 새 최대치(1)를 넘어 위젯이 오류를 낸다. 지운다.
                    for _r in _um_pick:
                        for _sfx in ('_q', '_m'):
                            st.session_state.pop(
                                f"rs_asg_{d_day}_{_r['상품번호']}{_sfx}", None)
                    st.session_state['_rs_um_open'] = True   # 이어서 배정하도록 열어 둔다
                    st.success(f"✅ {len(_new)}종을 {_um_bulk}에게 배정했습니다 — "
                               "정산표에 반영됐습니다. '정산 적용'을 눌러 저장하세요.")
                    st.rerun()
                else:
                    st.error("배정할 항목을 만들지 못했습니다 (사용자·상품번호 확인).")

    # ── 3.4) 잘못 붙은 매칭 끊기 (수동 매칭 바로 위) ──
    _render_unmatch_panel(alloc, dmap, receipt_items)

    # ── 3.5) 미매칭 수동/AI 매칭 ──
    _render_match_section(alloc, dmap, settings, USERNAME, bill_date=str(d_day))

    # ── 3.7) 남은 재고 확인·입고 ──
    _render_leftover_section(receipt_items, alloc, dmap, d_day, USERNAME)

    # ── 4) 저장 / 전송 ──
    #   둘은 다른 결정이다. 저장은 '여기까지 했다', 전송은 '이 금액으로 청구한다'.
    #   중간에 저장할 데가 없어서 창을 닫으면 배정이 통째로 날아갔다.
    if rows:
        st.divider()
        st.subheader("💾 저장 · 📤 전송")
        _sv1, _sv2 = st.columns([1, 2])
        if _sv1.button("💾 매칭 저장 (전송 안 함)", key="rs_save_draft",
                       use_container_width=True):
            try:
                # 변수명에 _n을 쓰면 모듈 함수 _n()이 render() 전체에서 가려진다
                # (파이썬은 함수 안 대입만 봐도 그 이름을 지역변수로 확정한다).
                _saved_n = save_match_draft(str(d_day), rows, created_by=USERNAME)
                st.success(f"💾 매칭 {_saved_n}건을 저장했습니다 — 창을 닫아도 남습니다. "
                           "아직 사용자에게 청구되지 않았습니다.")
            except Exception as _e:
                st.error(f"저장 실패: {_e}")
        _sv2.caption("**저장**은 여기까지 한 매칭을 붙들어 둘 뿐 사용자에게 아무것도 "
                     "보내지 않습니다. 나중에 이어서 하거나 다른 사람이 검수할 때 씁니다.")
        try:
            _dd = [(_d, _c) for _d, _c in (draft_dates() or []) if _d != str(d_day)]
        except Exception:
            _dd = []
        if _dd:
            st.caption("📌 저장만 하고 전송하지 않은 날 — "
                       + " · ".join(f"**{_d}** {_c}건" for _d, _c in _dd[:6]))

        st.warning("⚠️ 전송하면 각 주문의 구입가가 영수증 실단가로 **덮어써지고** "
                   "각 사용자에게 청구금액으로 보입니다. "
                   "(되돌리려면 정산 이력에서 삭제 후 재수집)")
        _amt_by_u = {u: v['amount'] for u, v in (summary or {}).items()}
        st.caption("전송 대상 — " + " · ".join(
            f"{dmap.get(u, u)} {fmt(a)}원" for u, a in
            sorted(_amt_by_u.items(), key=lambda kv: -kv[1])[:8]))
        if st.button(f"📤 각 사용자에게 전송 ({len(_amt_by_u)}명 · {fmt(sum(_amt_by_u.values()))}원)",
                     type="primary", key="rs_apply_btn"):
            with st.spinner("적용 중..."):
                n = apply_receipt_settlement(rows)
                # 매칭 결과(네이버번호↔코스트코번호)를 제품DB에 저장 → 다음 정산부터
                # 번호로 바로 붙는다. 예전엔 이름·수동·AI로 붙여도 저장이 안 돼
                # 같은 상품이 매번 미매칭으로 나왔다.
                try:
                    _learn = learn_costco_mappings(rows)
                except Exception:
                    _learn = {'filled': 0, 'by_user': {}}
                # 부족분(주문은 있는데 영수증에 없음)·재고분(사고 남은 것)도 함께 남긴다.
                #   화면에만 있고 저장이 안 돼, 나중에 "그날 뭐가 모자랐나"를 알 수 없었다.
                # 부족분(원가 미확정)은 '그날 주문'만 남긴다. 조회창을 넓히면서
                # 이전 날짜의 미판매 주문까지 매일 부족분으로 쌓이면 판정이 무의미해진다.
                _short = [o for o in (alloc.get('unmatched_orders') or [])
                          if str(o.get('order_date') or '') == str(d_day)]
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
            _st = st.session_state.get('rs_sticky') or {}
            _st.pop(str(d_day), None)      # 전송됐으니 더 붙들 이유가 없다
            st.session_state['rs_sticky'] = _st
            try:
                clear_match_draft(str(d_day))
            except Exception:
                pass
            # 사용자 화면에 '확정'으로 뜨게 한다 — 전송은 여기까지 가야 끝난다.
            # 이게 없으면 관리자만 아는 정산이 되어 사용자는 청구를 모른다.
            _sent = 0
            try:
                from db_purchase_settle import finalize as _finalize
                from db_receipt_settle import user_totals_by_date
                # 이 회차 합계가 아니라 그날 **누적 배치 전체**로 확정한다.
                # 하루에 여러 번 돌리면(영수증을 나눠 올리거나 일부만 먼저 보내면)
                # 회차 합계로 덮어써서 마지막 회차 금액만 남는다.
                _acc = user_totals_by_date(str(d_day))
                _tg = {u for (dd, u) in _acc if dd == str(d_day)} | set(summary or {})
                for _su in _tg:
                    _amt = int((_acc.get((str(d_day), _su)) or {}).get('amount')
                               or (summary.get(_su) or {}).get('amount') or 0)
                    _finalize(str(d_day), _su, _amt, [], created_by=USERNAME)
                    _sent += 1
            except Exception as _fe:
                st.caption(f"⚠️ 사용자 확정 표시 실패: {_fe}")
            _lmsg = ""
            if (_learn or {}).get('filled'):
                _lu = ", ".join(f"{k} {v}건" for k, v in (_learn.get('by_user') or {}).items())
                _lmsg = (f" 🧠 코스트코번호 매핑 {_learn['filled']}건 학습({_lu})"
                         + (f" · 주문 {_learn['orders']}건에 번호 기입"
                            if _learn.get('orders') else "")
                         + " — 다음 정산부터 자동 매칭됩니다.")
            st.success(f"📤 전송 완료 — 주문 {n}건 구입가 반영, 정산 배치 #{bid} 저장, "
                       f"사용자 {_sent}명에게 청구 확정. "
                       "각 사용자 수익계산에 즉시 반영됩니다." + _lmsg)
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

    # 직전 입고 결과 — 예전엔 st.success() 바로 뒤에 st.rerun()을 불러서
    # 메시지가 그려지기도 전에 화면이 새로 그려졌다. 입고는 실제로 됐는데
    # 아무 반응이 없어 보여서 "버튼이 안 먹는다"로 읽혔다.
    _lf_msg = st.session_state.pop('_rs_lf_msg', None)
    if _lf_msg:
        (st.success if _lf_msg.get('ok') else st.warning)(_lf_msg.get('text', ''))
        if _lf_msg.get('err'):
            st.error(_lf_msg['err'])

    # 발송은 했는데 정산에 못 붙은 건도 실물은 나갔다 — 재고에서 빼야 한다.
    # 안 빼면 재고가 부풀고, 다음 날 그 재고로 다른 주문을 메꿨다고 계산해
    # 같은 물건이 두 번 쓰인다.
    _rows_now = alloc.get('rows') or []
    _extra, _extra_rows = {}, []
    try:
        _extra, _extra_rows = _rs.dispatch_consumption(
            str(d_day), [x.get('상품번호') for x in (receipt_items or [])],
            matched_keys={(r.get('username'), r.get('order_no')) for r in _rows_now})
    except Exception as _e:
        st.caption(f"⚠️ 발송분 차감 계산 실패: {_e}")
    lefts = compute_leftovers(receipt_items, _rows_now, extra_used=_extra)
    if _extra_rows:
        with st.expander(f"🚚 정산에 안 붙었지만 발송된 {len(_extra_rows)}건 "
                         "— 재고에서 뺐습니다", expanded=False):
            st.caption("영수증 매칭에는 실패했지만 송장이 등록돼 실제로 나간 주문입니다. "
                       "물건이 나갔으니 재고에는 없어야 합니다. "
                       "**청구는 별개**입니다 — 위 부족분에서 판정하세요.")
            st.dataframe(pd.DataFrame([{
                '사용자': dmap.get(r['username'], r['username']),
                '주문번호': r['order_no'], '상품명': r['product_name'][:40],
                '코스트코번호': r['costco_no'], '차감(소분)': r['units'],
            } for r in _extra_rows]), use_container_width=True, hide_index=True)
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
    # 여기 남은 것이 전부 재고는 아니다. 이전 미배송건을 오늘 사서 바로 보낸
    # 물건은 실물이 이미 나갔으므로 입고하면 안 되고 그 사용자에게 청구해야 한다.
    st.info("📦 여기 있는 것이 전부 재고는 아닙니다. **이전 미배송건을 오늘 사서 바로 "
            "보낸 품목**은 입고하지 말고, 위 **✋ 수동 매칭 → 👤 주문 없이 사용자에게 "
            "직접 청구**에서 그 사용자에게 청구하세요. 청구한 만큼은 이 표에서 빠집니다.")

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
            # 기본은 체크 해제. 예전엔 중복이 아닌 행을 전부 체크해 뒀는데,
            # 한두 개만 고른 줄 알고 버튼을 누르면 목록 전체가 한꺼번에 들어갔다
            # (9/3에 7종이 한 번의 클릭으로 모두 입고됐다).
            '입고': False,
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

    # 편집기 key에 행 구성을 섞는다. 같은 key를 쓰면 '0번 행 체크' 같은 편집 기록이
    # 행 '순서'로 남아, 품목 목록이 바뀐 뒤 다른 상품에 체크가 옮겨 붙는다.
    # (7종이던 목록이 4종으로 줄자 0번이던 부추고기순대의 체크가
    #  새 0번 KS STRAWBERRIES로 넘어가 있었다)
    _ed_sig = hashlib.md5("|".join(l['costco_no'] for l in lefts).encode()).hexdigest()[:8]
    _ed = st.data_editor(
        pd.DataFrame(_rows), use_container_width=True, hide_index=True,
        key=f"rs_leftover_editor_{d_day}_{_ed_sig}",
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

    st.caption("입고될 품목 — " + " · ".join(
        f"{r.get('상품명')} {r.get('남은수량(소분)')}개" for r in _picked))

    _dup_picked = [r for r in _picked if str(r.get('상품번호') or '') in _already]
    _force = False
    if _dup_picked:
        st.warning(f"⚠️ 선택한 {len(_dup_picked)}종은 이 날짜({d_day})로 **이미 입고돼 있습니다**. "
                   "그대로 누르면 건너뜁니다 — 재고가 두 배로 잡히는 것을 막기 위해서입니다.")
        _force = st.checkbox(
            "이미 입고된 것도 다시 입고 (중복인 걸 확인했습니다)", key="rs_lf_force",
            help="같은 날짜로 lot이 한 번 더 생깁니다. 앞의 입고가 잘못됐다면 "
                 "'재고 관리' 탭에서 그 lot을 지운 뒤 다시 넣는 편이 안전합니다.")

    if st.button(f"📦 확인한 {len(_picked)}종 재고 입고", type="primary", key="rs_lf_apply"):
        _by_cno = {l['costco_no']: l for l in lefts}
        _ok, _skip, _fail = 0, 0, []
        for r in _picked:
            _cno = str(r.get('상품번호') or '')
            _l = _by_cno.get(_cno)
            if not _l:
                continue
            if _cno in _already and not _force:
                _skip += 1
                continue
            _owner = _lbl2user.get(str(r.get('보유자') or ''), USERNAME)
            try:
                _lid = add_lot_units(
                    product_no=_cno, product_name=_l['name'], owner=_owner,
                    pack_unit_cost=_l['unit_price'], qty_units=_l['units_left'],
                    split_qty=_l['split_qty'], received_at=str(d_day),
                    memo=f"{_memo_tag} · 영수증잔량"
                         + (" · 재입고" if _cno in _already else ""))
                if _lid:
                    _ok += 1
                else:
                    _fail.append(f"{_l['name'][:20]} (수량 0)")
            except Exception as e:
                _fail.append(f"{_l['name'][:20]} — {str(e)[:60]}")
        if _ok:
            _text = f"✅ 재고 입고 {_ok}종"
            if _skip:
                _text += f" · ⏭ 이미 입고돼 건너뜀 {_skip}종"
            _text += " — '재고 관리' 탭에서 확인하세요."
        else:
            _text = (f"입고된 항목이 없습니다 — 선택한 {_skip}종은 이 날짜({d_day})로 "
                     "이미 입고돼 있습니다. 정말 한 번 더 넣으려면 위 "
                     "'이미 입고된 것도 다시 입고'를 켜고 다시 누르세요.")
        st.session_state['_rs_lf_msg'] = {
            'ok': bool(_ok),
            'text': _text,
            'err': ("❌ 실패: " + " / ".join(_fail)) if _fail else '',
        }
        # 위젯 키는 생성된 뒤 '대입'하면 Streamlit이 예외를 던진다 — pop으로 초기화한다.
        st.session_state.pop('rs_lf_force', None)
        st.rerun()


def _resolve_ai_key(name, settings=None):
    """AI 키 찾기 — AI 키는 공유 인프라라 관리자가 어느 계정에 넣었든 쓴다.

    관리자가 자기 계정이 아닌 다른 계정 설정에 넣어 둔 경우가 있어 폴백 스캔한다.
    """
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


def _merge_matches(alloc, new_rows, matched_order_indices, sticky=True):
    """매칭 결과를 alloc에 얹는다.

    sticky=True면 그 행을 날짜별로 따로 보관해, '미리보기'를 다시 눌러
    alloc을 새로 만들어도 다시 얹는다. 사람이 판단해서 넣은 배정이
    기계 재계산으로 사라지면 안 된다.
    """
    # 같은 사용자에게 같은 품목을 두 번 배정하면 order_no가 겹친다(MEMO-날짜-번호-사용자).
    # 그대로 두 줄로 쌓으면 정산 저장에서 충돌하므로 수량·금액을 합쳐 한 줄로 만든다.
    if new_rows:
        _idx = {(r.get('username'), r.get('order_no')): r for r in alloc.get('rows', [])}
        _fresh = []
        for _r in new_rows:
            _k = (_r.get('username'), _r.get('order_no'))
            _ex = _idx.get(_k)
            if _ex is not None:
                _ex['qty'] = int(_ex.get('qty') or 0) + int(_r.get('qty') or 0)
                _ex['amount'] = int(_ex.get('amount') or 0) + int(_r.get('amount') or 0)
            else:
                _fresh.append(_r)
                _idx[_k] = _r
        new_rows = _fresh
    if sticky and new_rows:
        _st = st.session_state.get('rs_sticky') or {}
        _d = str(st.session_state.get('rs_sticky_date') or '')
        _keys = {(r.get('username'), r.get('order_no')) for r in (_st.get(_d) or [])}
        _st[_d] = (_st.get(_d) or []) + [
            r for r in new_rows if (r.get('username'), r.get('order_no')) not in _keys]
        st.session_state['rs_sticky'] = _st
    alloc['rows'].extend(new_rows)
    idxset = set(matched_order_indices)
    alloc['unmatched_orders'] = [o for i, o in enumerate(alloc.get('unmatched_orders', []))
                                 if i not in idxset]
    # 주문에 붙은 품목은 목록에서 뺀다. 다만 '사용자에게 직접 배정(memo)'한 것은
    # 수량이 남아 있을 수 있다 — 2개 중 1개를 A에게 줬으면 1개는 B 몫이다.
    # 전에는 배정하는 순간 통째로 사라져 나머지를 줄 방법이 없었다.
    _by_order, _memo_qty = set(), {}
    for r in alloc['rows']:
        _c = str(r.get('costco_no') or '')
        if not _c:
            continue
        if str(r.get('via') or '') == 'memo':
            _memo_qty[_c] = _memo_qty.get(_c, 0) + int(r.get('qty') or 0)
        else:
            _by_order.add(_c)
    _keep = []
    for u in alloc.get('unmatched_receipt', []):
        _c = str(u['상품번호'])
        if _c in _by_order:
            continue
        _tot = int(u.get('영수증수량') or 1)
        _left = _tot - _memo_qty.get(_c, 0)
        if _left <= 0:
            continue
        u = dict(u)
        u['남은수량'] = _left
        _keep.append(u)
    alloc['unmatched_receipt'] = _keep
    alloc['user_summary'] = _summarize(alloc['rows'])
    st.session_state['rs_alloc'] = alloc


def _unmatch_rows(alloc, keys, receipt_items):
    """선택한 배치행을 끊어 미매칭으로 되돌린다. keys: {(username, order_no)}

    끊을 때 세 곳을 함께 고쳐야 한다 — 안 그러면 미리보기를 다시 누르는 순간
    되살아나거나, 영수증 품목이 이미 쓰인 것으로 남아 다시 붙일 수가 없다.
      · alloc['rows']에서 제거
      · 붙들어 둔 수동 배정(sticky)에서도 제거 — 여기 남으면 재계산 때 부활한다
      · 주문은 미매칭 주문으로, 영수증 품목은 미매칭 영수증으로 되돌린다
    반환: 끊은 행 수
    """
    if not keys:
        return 0
    _drop = [r for r in alloc.get('rows', []) if (r.get('username'), r.get('order_no')) in keys]
    if not _drop:
        return 0
    alloc['rows'] = [r for r in alloc.get('rows', [])
                     if (r.get('username'), r.get('order_no')) not in keys]

    _st = st.session_state.get('rs_sticky') or {}
    _d = str(st.session_state.get('rs_sticky_date') or '')
    if _st.get(_d):
        _st[_d] = [r for r in _st[_d] if (r.get('username'), r.get('order_no')) not in keys]
        st.session_state['rs_sticky'] = _st

    # 주문 되돌리기 — 관리자 메모 배정(memo)은 원래 주문이 없으므로 뺀다
    _have = {(o.get('username'), o.get('order_no'))
             for o in alloc.get('unmatched_orders', [])}
    for r in _drop:
        if str(r.get('via') or '') == 'memo':
            continue
        _k = (r.get('username'), r.get('order_no'))
        if _k in _have:
            continue
        alloc.setdefault('unmatched_orders', []).append({
            'username': r.get('username', ''), 'order_no': r.get('order_no', ''),
            'order_date': r.get('order_date', ''), 'recipient': r.get('recipient', ''),
            'product_name': r.get('product_name', ''), 'naver_no': r.get('naver_no', ''),
            'qty': int(r.get('qty') or 1), 'prev_cost': int(r.get('prev_cost') or 0),
            'split_qty': max(1, int(r.get('split_qty') or 1)),
        })
        _have.add(_k)

    # 영수증 품목 되돌리기 — 아직 아무 행도 안 쓰는 번호만 목록에 되살린다
    _used = {str(r.get('costco_no') or '') for r in alloc['rows']}
    _cur = {str(u['상품번호']) for u in alloc.get('unmatched_receipt', [])}
    for _it in (receipt_items or []):
        _c = _n(_it.get('상품번호'))
        if not _c or _c in _used or _c in _cur:
            continue
        alloc.setdefault('unmatched_receipt', []).append({
            '상품번호': _c, '상품명': _n(_it.get('상품명')),
            '단가': int(_it.get('단가') or 0),
            '영수증수량': int(_it.get('수량') or 1)})
        _cur.add(_c)

    alloc['user_summary'] = _summarize(alloc['rows'])
    st.session_state['rs_alloc'] = alloc
    return len(_drop)


def _render_unmatch_panel(alloc, dmap, receipt_items):
    """잘못 붙은 매칭을 골라 끊는다.

    AI·이름 유사도 매칭은 틀릴 수 있다. 틀린 채로 전송하면 그 단가로 청구되고,
    learn_costco_mappings가 그 매핑을 '영수증 확인됨'으로 굳혀 다음부터 계속
    틀린다. 전송 전에 끊을 수 있어야 한다.
    """
    _rows = alloc.get('rows') or []
    if not _rows:
        return
    _via_lbl = {'number': '상품번호', 'name': '상품명 유사도', 'stock': '재고 이월',
                'carry': '미정산 이월', 'shopping': '장보기 목록',
                'shopping-name': '장보기 이름', 'manual': '수동', 'ai': 'AI',
                'order': '주문 코스트코번호', 'memo': '직접 배정'}
    # 기계가 추측한 것부터 보여준다 — 사람이 고른 건 확인할 이유가 적다
    _risky = {'ai', 'name', 'shopping-name', 'stock'}
    _n_risky = sum(1 for r in _rows if str(r.get('via') or '') in _risky)
    with st.expander(f"✏️ 매칭 수정 — 잘못 붙은 건 끊기 "
                     f"({len(_rows)}건 중 확인 권장 {_n_risky}건)", expanded=False):
        st.caption("AI·상품명 유사도로 붙인 것은 틀릴 수 있습니다. 끊으면 그 주문은 "
                   "미매칭으로 돌아가고, 아래 **수동 매칭**에서 다시 이을 수 있습니다. "
                   "틀린 채로 전송하면 그 단가로 청구되고 매핑까지 굳어집니다.")
        _only = st.checkbox("추측으로 붙은 것만 보기 (AI·이름·재고)", value=bool(_n_risky),
                            key="rs_um_only")
        _view = [r for r in _rows if (not _only or str(r.get('via') or '') in _risky)]
        if not _view:
            st.caption("해당하는 행이 없습니다.")
            return
        _tbl = [{'끊기': False,
                 '사용자': dmap.get(r.get('username'), r.get('username')),
                 '상품명': str(r.get('product_name') or '')[:38],
                 '수량': int(r.get('qty') or 1),
                 '코스트코번호': str(r.get('costco_no') or ''),
                 '실단가': int(r.get('unit_price') or 0),
                 '청구액': int(r.get('amount') or 0),
                 '근거': _via_lbl.get(str(r.get('via') or ''), r.get('via') or ''),
                 '_u': r.get('username'), '_o': r.get('order_no')} for r in _view]
        _sig = hashlib.md5(
            "|".join(f"{t['_u']}:{t['_o']}" for t in _tbl).encode()).hexdigest()[:8]
        _ed = st.data_editor(
            pd.DataFrame(_tbl).drop(columns=['_u', '_o']),
            use_container_width=True, hide_index=True, key=f"rs_um_ed_{_sig}",
            disabled=['사용자', '상품명', '수량', '코스트코번호', '실단가', '청구액', '근거'],
            column_config={
                '끊기': st.column_config.CheckboxColumn('끊기', help='체크한 행의 매칭을 해제합니다'),
                '실단가': st.column_config.NumberColumn('실단가', format='%d'),
                '청구액': st.column_config.NumberColumn('청구액', format='%d'),
            })
        _picked = [_tbl[i] for i, rr in enumerate(_ed.to_dict('records')) if rr.get('끊기')]
        if _picked:
            st.caption("끊을 행 — " + " · ".join(
                f"{t['사용자']} {t['상품명'][:16]} {fmt(t['청구액'])}원" for t in _picked[:6]))
        if st.button(f"↩️ 선택한 {len(_picked)}건 매칭 끊기", key="rs_um_apply",
                     disabled=not _picked):
            _k = {(t['_u'], t['_o']) for t in _picked}
            _cnt = _unmatch_rows(alloc, _k, receipt_items)
            st.success(f"↩️ {_cnt}건을 끊었습니다 — 아래 수동 매칭에서 다시 이으세요.")
            st.rerun()


def _render_match_section(alloc, dmap, settings, USERNAME, bill_date=None):
    u_ords = alloc.get('unmatched_orders') or []
    u_rcpt = alloc.get('unmatched_receipt') or []
    if not u_ords or not u_rcpt:
        return
    st.divider()
    st.subheader(f"🔗 미매칭 매칭 — 주문 {len(u_ords)}건 · 영수증 {len(u_rcpt)}종")
    st.caption("자동으로 못 붙은 주문을 영수증 품목과 AI 또는 수동으로 연결합니다.")

    _anthropic_key = _resolve_ai_key('anthropic_api_key', settings)
    _gemini_key = _resolve_ai_key('gemini_api_key', settings)
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

        # ── 붙일 주문이 아예 없는 경우 ──────────────────────────
        # 이전 미배송건을 오늘 사서 바로 보낸 물건은 오늘 주문 목록에 없다.
        # 재고로 넣으면 안 된다 — 실물은 이미 나갔고 돈은 받아야 한다.
        st.divider()
        st.markdown("**👤 주문 없이 사용자에게 직접 청구**")
        st.caption("이전 미배송건을 오늘 사서 바로 보낸 경우처럼 **오늘 주문 목록에 없는** "
                   "품목입니다. 재고로 입고하지 않고 그 사용자에게 바로 청구합니다.")
        _bi = st.selectbox("청구할 영수증 품목", options=list(_ri_opts),
                           format_func=lambda i: _ri_opts[i], key="rs_bill_ri")
        _bu_opts = sorted(dmap.keys(), key=lambda u: dmap.get(u, u))
        _bu_labels = [dmap.get(u, u) for u in _bu_opts] or [USERNAME]
        _bu_l2u = {dmap.get(u, u): u for u in _bu_opts} or {USERNAME: USERNAME}
        _bc1, _bc2 = st.columns([2, 1])
        _bu = _bc1.selectbox("청구받을 사용자", _bu_labels, key="rs_bill_user")
        _bq = _bc2.number_input("수량(팩)", min_value=1, step=1, value=1, key="rs_bill_qty")
        _bm = st.text_input("사유 메모", key="rs_bill_memo",
                            placeholder="예: 8/28 주문 미배송분 오늘 구매 후 발송")
        _bit = u_rcpt[_bi]
        st.caption(f"청구금액 **{fmt(int(_bit['단가'] or 0) * int(_bq))}원** "
                   f"= {fmt(_bit['단가'])}원 × {int(_bq)}팩")
        if st.button(f"🧑‍💼 {_bu}에게 청구 추가", key="rs_bill_add", type="primary"):
            _rows = build_memo_rows([{
                'username': _bu_l2u.get(_bu, ''),
                'costco_no': str(_bit['상품번호'] or ''),
                'product_name': str(_bit['상품명'] or ''),
                'unit_price': int(_bit['단가'] or 0),
                'qty': int(_bq),
                'memo': (_bm.strip() or '주문 없음 — 이전 미배송분 발송'),
            }], str(bill_date))
            if _rows:
                _merge_matches(alloc, _rows, [])
                st.success(f"✅ {_bu}에게 {_bit['상품명']} {int(_bq)}팩을 청구 추가했습니다 — "
                           "정산표에 반영됐습니다. '정산 적용'을 눌러 저장하세요.")
                st.rerun()
            else:
                st.error("청구행을 만들지 못했습니다 (사용자·상품번호를 확인하세요).")


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

    # 재고가 부풀어 보이는 원인 1위 — 미리보기에서 매칭만 하고 '정산 적용'을
    # 누르지 않으면 사용량이 0이라 산 것이 통째로 재고로 남는다. 화면에는
    # '사용 0'만 보여 매칭이 안 된 것처럼 읽힌다 — 매칭은 됐고 저장이 안 된 것이다.
    try:
        _unap = _rs.unapplied_receipt_dates()
    except Exception:
        _unap = []
    if _unap:
        _msg = ["🚨 **정산이 적용되지 않은 영수증이 있습니다** — 그날 산 것이 "
                "통째로 재고로 잡혀 있습니다.", ""]
        _msg += [f"- **{d}** 영수증 {n}종 · 정산 적용 0건" for d, n in _unap[:6]]
        _msg += ["", "미리보기에서 매칭만 하고 **'정산 적용'을 누르지 않으면** "
                 "사용량이 0으로 남습니다. 위 날짜로 영수증 정산을 다시 열어 "
                 "매칭 후 **정산 적용**까지 누르면 이 재고에서 빠집니다."]
        st.error("\n".join(_msg))

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
                _shm = st.session_state.pop('_rs_sh_msg', None)
                if _shm:
                    {'ok': st.success, 'warn': st.warning,
                     'err': st.error}.get(_shm[0], st.info)(_shm[1])
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
                    set_shortage_decision(_pick, 'bill', USERNAME)
                    # 판정만 저장하면 청구가 그대로다 — '샀는데 매칭만 실패'라는
                    # 뜻이므로 아는 값 중 가장 나은 단가로 구입가를 채워야 청구된다.
                    _sel_rows = [x for x in _sh if x['id'] in set(_pick)]
                    try:
                        _r = _rs.apply_shortage_billing(_sel_rows)
                    except Exception as _e:
                        _r = None
                        st.session_state['_rs_sh_msg'] = ('err', f"구입가 반영 실패: {_e}")
                    if _r:
                        _m = (f"✅ 청구포함 {len(_pick)}건 — 구입가 {_r['updated']}건 반영 "
                              f"(합계 {fmt(_r['amount'])}원)")
                        if _r['zero']:
                            _m += (f" · ⚠️ {_r['zero']}건은 **단가를 못 찾아 0원**입니다 — "
                                   "제품DB에 코스트코 단가를 채운 뒤 다시 누르세요.")
                        st.session_state['_rs_sh_msg'] = (
                            'warn' if _r['zero'] else 'ok', _m)
                    st.rerun()
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
