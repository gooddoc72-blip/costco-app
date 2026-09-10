"""🧾 영수증 정산 (관리자) — 코스트코 영수증을 각 사용자 주문에 자동배치하고
각 주문 구입가에 실단가를 반영 + 사용자별 정산표 생성."""
import hashlib
from datetime import date, datetime, timedelta

import streamlit as st
import pandas as pd

from services import parse_costco_receipt_pdf, render_pdf_to_images
import receipt_settle as _rs
import db_settle as _ds
import settle_core as _sc
from receipt_settle import (
    allocate_receipt_to_orders, cleanup_orphan_settlements,
    build_manual_rows, build_memo_rows, ai_match_receipt_orders, _summarize,
    build_stock_pool, get_settle_start_date, get_stock_status,
    allocate_dispatched_to_receipt,
)
from db import (
    get_all_users, get_all_settings,
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
    #   매일 쓰는 값이 아니라 한 번 정하는 값이라 화면 맨 위에서 접어 둔다 —
    #   맨 앞에 펼쳐 두니 매번 만져야 하는 설정으로 읽혔다.
    _sd = get_settle_start_date()
    with st.expander(f"⚙️ 재고 계산 시작일 — 현재 **{_sd or '미설정'}**", expanded=not _sd):
        st.caption(
            "이 날짜 **이전**에 산 영수증은 재고로 치지 않습니다. 옛날에는 영수증 업로드가 "
            "들쭉날쭉해서 전부 세면 있지도 않은 재고가 잡히기 때문입니다. "
            "한 번 정하면 계속 쓰는 값이라 평소에는 건드릴 일이 없습니다. "
            "(영수증 데이터는 지워지지 않고, 공유DB 매장 카탈로그에는 계속 반영됩니다.)")
        _sc1, _sc2 = st.columns([2, 3])
        _new_sd = _sc1.date_input(
            "재고 계산 시작일", value=(datetime.strptime(_sd, "%Y-%m-%d").date()
                                 if _sd else date.today()),
            key="rs_start_date")
        _sc2.write(""); _sc2.write("")
        if _sc2.button("기준일 저장", key="rs_save_start"):
            set_global_setting("settle_start_date", str(_new_sd))
            st.success(f"✅ 기준일 {_new_sd} 저장 — 이 날짜부터의 구매만 재고로 계산합니다.")
            st.rerun()
        # 입력칸을 고쳐 놓고 저장을 안 하면 위 칸과 '현재 기준일'이 달라 보인다.
        # 화면이 스스로 모순돼 보이는 상태라 반드시 짚어 준다.
        if _sd and str(_new_sd) != _sd:
            st.warning(f"✏️ 입력한 **{_new_sd}** 는 아직 저장되지 않았습니다 — "
                       f"실제로 적용 중인 기준일은 **{_sd}** 입니다. "
                       "바꾸려면 '기준일 저장'을 누르세요.")
        if not _sd:
            st.warning("⚠️ 시작일이 없어 **모든 과거 영수증**이 재고로 계산됩니다. "
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
    # 기준일보다 이른 날을 정산하면 그날 영수증이 재고에 잡히지 않는다. 조용히
    # 무시돼 "산 물건이 어디로 갔는지 모르겠다"가 된다 — 반드시 알려 준다.
    if _sd and str(d_day) < _sd:
        st.error(
            f"🚫 **정산일 {d_day} 이 재고 계산 시작일 {_sd} 보다 앞섭니다.** "
            f"이 날 영수증으로 산 물건은 **재고에 잡히지 않아**, 주문에 안 붙고 남은 금액이 "
            "어디에도 나타나지 않습니다.\n\n"
            f"이 날짜를 제대로 정산하려면 위 **⚙️ 재고 계산 시작일**을 **{d_day} 이전**으로 "
            "바꿔 저장하세요. (배치·청구 자체는 지금도 되지만 남은 재고가 유실됩니다.)")
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

    # 표시이름 대응표 — 아래 발송 현황부터 쓴다. (예전엔 훨씬 뒤에서 만들어
    # 'dmap referenced before assignment' 로 페이지가 죽었다.)
    dmap = _disp_map()
    # 영수증↔발송 매칭은 발송 기록이 있어야 성립한다. 없으면 매칭이 아니라
    # 관리자 수작업이 되므로, 어느 사용자가 비어 있는지 먼저 보여준다.
    try:
        _cov = _rs.dispatch_coverage(str(d_day))
    except Exception:
        _cov = []
    if _cov:
        _none = [c for c in _cov if c['orders'] and not c['dispatched']]
        _c_tot_o = sum(c['orders'] for c in _cov)
        _c_tot_d = sum(c['dispatched'] for c in _cov)
        st.markdown(f"**🚚 {d_day} 발송 기록** — 주문 {_c_tot_o}건 중 "
                    f"발송 **{_c_tot_d}건**")
        st.dataframe(pd.DataFrame([{
            '사용자': dmap.get(c['username'], c['username']),
            '주문': c['orders'], '발송': c['dispatched'],
            '미발송': max(0, c['orders'] - c['dispatched']),
            '상태': '✅' if c['dispatched'] else ('⚠️ 발송 기록 없음' if c['orders'] else '-'),
        } for c in _cov]), use_container_width=True, hide_index=True)
        # 주문은 있는데 송장등록이 안 된 건 — **보여주기만** 한다.
        # 송장등록은 사용자가 자기 스토어에서 하는 일이라 관리자가 대신 누르면
        # 실제 상태와 어긋난다. 관리자에게 필요한 건 '누가 아직 안 했나'다.
        _pend = [c for c in _cov if c['orders'] > c['dispatched']]
        if _pend:
            _pend_n = sum(c['orders'] - c['dispatched'] for c in _pend)
            with st.expander(f"📮 송장등록 안 된 주문 {_pend_n}건 — 어느 건인지 보기",
                             expanded=False):
                st.caption("송장등록은 **각 사용자가 자기 스토어에서** 하는 일입니다. "
                           "여기서는 아직 안 된 주문이 무엇인지 확인해 해당 사용자에게 "
                           "알려주세요. 등록이 끝나면 자동으로 발송으로 잡혀 "
                           "영수증과 매칭됩니다.")
                _plabels = [f"{dmap.get(c['username'], c['username'])} "
                            f"— 미등록 {c['orders'] - c['dispatched']}건" for c in _pend]
                _pk = st.selectbox("사용자", _plabels, key=f"rs_md_u_{d_day}")
                _pu = _pend[_plabels.index(_pk)]['username']
                try:
                    _ulist = _rs.undispatched_orders(_pu, str(d_day))
                except Exception as _e:
                    _ulist = []
                    st.error(f"목록 조회 실패: {_e}")
                if not _ulist:
                    st.caption("미등록 주문이 없습니다.")
                else:
                    st.dataframe(pd.DataFrame([{
                        '주문번호': o['order_no'], '수취인': o['recipient'],
                        '상품명': o['product_name'][:44], '수량': o['qty'],
                        '코스트코번호': o['costco_no'] or '',
                    } for o in _ulist]), use_container_width=True, hide_index=True)
                    st.caption(f"{dmap.get(_pu, _pu)} · {len(_ulist)}건 미등록")
                    try:
                        _csv = pd.DataFrame(_ulist).to_csv(index=False).encode('utf-8-sig')
                        st.download_button("📥 미등록 목록 CSV", data=_csv,
                                           file_name=f"미등록_{_pu}_{d_day}.csv",
                                           mime="text/csv", key=f"rs_md_dl_{d_day}_{_pu}")
                    except Exception:
                        pass

        if _none:
            # 사용자들은 대개 오후 6~7시에 송장을 등록한다. 그 전에 정산을 돌리면
            # 발송이 비어 매칭이 안 되는데, 이건 고장이 아니라 아직 이른 것이다.
            from datetime import datetime as _dtn
            _now = _dtn.now()
            _early = (str(d_day) == _now.strftime('%Y-%m-%d') and _now.hour < 19)
            st.warning(
                "⚠️ **발송 기록이 없어 영수증과 매칭할 수 없는 사용자** — "
                + " · ".join(f"{dmap.get(c['username'], c['username'])} "
                             f"(주문 {c['orders']}건)" for c in _none[:8])
                + ("  ·  🕕 사용자들은 보통 **오후 6~7시**에 송장을 등록합니다. "
                   "지금은 그 전이라 비어 있는 것이 정상입니다 — "
                   "등록이 끝난 뒤 다시 미리보기를 누르세요."
                   if _early else
                   "  ·  위 **🚚 발송 파일 업로드**에 송장 파일을 올리거나 "
                   "각 사용자가 송장 등록으로 발송처리하면 매칭 대상이 됩니다. "
                   "그 전에는 아래에서 손으로 배정할 수밖에 없습니다."))

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
            _settled = _ds.settled_order_keys(exclude_date=str(d_day))
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
            _sticky += [r for r in (_ds.get_draft(str(d_day)) or [])
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

    # ── 3.3) 배정 — 주문 못 찾은 품목 + 팔고 남은 품목을 한 곳에서 ──
    #   예전엔 '주문 못 찾은 품목'(청구)과 '남은 재고 확인'(입고)이 따로 있었다.
    #   같은 물건을 두 화면에서 나눠 다루니 어디서 뭘 해야 하는지 매번 헷갈렸다.
    #   이제 한 표에서 사람을 고르고 **청구할지 재고로 넘길지**만 정한다.
    _assign_src = _build_assign_rows(unmatched, receipt_items, alloc, d_day)
    if _assign_src:
        # 배정하고 rerun하면 접혀 버려 매번 다시 열어야 했다. 한 번 열면
        # 남은 게 없어질 때까지 열어 둔다 — 여러 사용자에게 나눠 배정하는 화면이다.
        _um_open = bool(st.session_state.get('_rs_um_open'))
        with st.expander(f"⚠️ 배정할 영수증 품목 {len(_assign_src)}건 "
                         "— 주문 못 찾음 · 팔고 남음", expanded=_um_open):
            st.caption("주문이 당일 없거나 코스트코↔네이버 번호 매핑이 없어 배치 못 한 품목과, "
                       "주문에 붙고 **남은 수량**입니다. 사용자를 고른 뒤 "
                       "**청구**(이전 주문의 교환·추가 발송분)할지 "
                       "**재고로 입고**(안 팔려서 남은 것)할지 고르세요.")
            # 사용자는 표 안에서 고르지 않는다. 표 안 SelectboxColumn은
            #   · 기본값을 바꾸면 표 전체가 다시 그려져 체크와 행별 입력이 날아가고
            #   · 옵션에 없는 값(빈 문자열)은 None으로 렌더돼 아예 못 고른다.
            # 대신 '체크한 행을 이 사람에게'로 흐름을 단순화한다.
            # 받는 사람이 서로 다르면 나눠서 두 번 하면 된다 —
            # 배정한 품목은 목록에서 바로 빠지므로 자연스럽게 이어진다.
            _um_opts = sorted(dmap.keys(), key=lambda u: dmap.get(u, u))
            _um_labels = [dmap.get(u, u) for u in _um_opts] or [USERNAME]
            _um_l2u = {dmap.get(u, u): u for u in _um_opts} or {USERNAME: USERNAME}
            # 장보기 목록에서 그 상품을 요청한 사람이 있으면 그 사람이 기본값이다.
            # '누가 사 달라고 했나'가 '누구 물건인가'에 가장 가까운 답이다.
            _hint_cnt = sum(1 for r in _assign_src if r.get('요청자'))
            _um_def = dmap.get(USERNAME, USERNAME)
            if _um_def not in _um_labels:
                _um_def = _um_labels[0]
            _um_bulk = st.selectbox(
                "배정할 사용자 — 아래에서 체크한 품목이 이 사람에게 갑니다",
                _um_labels, index=_um_labels.index(_um_def),
                key=f"rs_memo_bulk_{d_day}",
                help="교환·추가 발송분을 실제로 받은 사용자, 또는 남은 물건을 가질 사용자를 "
                     "고르세요. 받는 사람이 서로 다르면 나눠서 두 번 하면 됩니다.")
            if _hint_cnt:
                st.caption(f"🛒 {_hint_cnt}건은 그날 **장보기 목록 요청자**가 있습니다 "
                           "— 표의 '요청자' 열을 참고하세요.")

            # 표는 체크만 받는다. 수량을 표 안에서 고치면 편집이 되돌아가는 일이
            # 있어(셀 수정 → rerun → 표 재생성) 수량·메모는 아래에서 따로 받는다.
            _um_rows = [{'배정': False,
                         '상품번호': u['상품번호'], '상품명': u['상품명'],
                         '구분': u['구분'], '요청자': dmap.get(u.get('요청자') or '', ''),
                         '영수증수량': int(u.get('영수증수량') or 1),
                         '남은수량': int(u.get('남은수량') or 1),
                         '정가': _list_by.get(_n(u['상품번호']), 0),
                         '팩단가': int(u['단가'] or 0)} for u in _assign_src]
            _um_sig = hashlib.md5("|".join(
                f"{u['상품번호']}:{u.get('남은수량')}" for u in _assign_src
            ).encode()).hexdigest()[:8]
            # 편집기 key에 사용자를 넣지 않는다 — 사용자를 바꿀 때마다 표가
            # 초기화되면 체크해 둔 것이 사라진다.
            _um_ed = st.data_editor(
                pd.DataFrame(_um_rows), use_container_width=True, hide_index=True,
                key=f"rs_memo_editor_{d_day}_{_um_sig}",
                disabled=['상품번호', '상품명', '구분', '요청자', '팩단가',
                          '영수증수량', '남은수량', '정가'],
                column_config={
                    '배정': st.column_config.CheckboxColumn(
                        '배정', help='체크하면 아래에 수량·메모 입력칸이 생깁니다'),
                    '구분': st.column_config.TextColumn(
                        '구분', help='주문없음 = 당일 주문에 못 붙은 품목 · '
                                    '팔고남음 = 주문에 붙고 남은 수량'),
                    '요청자': st.column_config.TextColumn(
                        '요청자', help='그날 장보기 목록에서 이 상품을 요청한 사용자'),
                    '정가': st.column_config.NumberColumn(
                        '정가', format='%d', help='영수증에 찍힌 단가(할인 전)'),
                    '팩단가': st.column_config.NumberColumn(
                        '팩단가', format='%d',
                        help='쿠폰 할인을 뺀 실제 지불 단가. 이 값으로 청구·입고됩니다.'),
                    '영수증수량': st.column_config.NumberColumn(
                        '영수증수량', format='%d', help='영수증에 찍힌 구매 팩 수'),
                    '남은수량': st.column_config.NumberColumn(
                        '남은수량', format='%d',
                        help='아직 아무에게도 주지 않은 팩 수. 이만큼까지 배정할 수 있습니다.'),
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
                st.markdown(f"**{_um_bulk}** 에게 **{len(_um_pick)}종** · "
                            f"금액 **{fmt(_um_amt)}원**")
                st.caption(" · ".join(
                    f"{r['상품명']} {r['수량(팩)']}/{r['남은수량']}팩"
                    + (f" ({r['메모']})" if str(r.get('메모') or '').strip() else "")
                    for r in _um_pick))

            _lf_msg = st.session_state.pop('_rs_lf_msg', None)
            if _lf_msg:
                (st.success if _lf_msg.get('ok') else st.warning)(_lf_msg.get('text', ''))
                if _lf_msg.get('err'):
                    st.error(_lf_msg['err'])

            _b1, _b2 = st.columns(2)
            if _b1.button(f"🧑‍💼 {len(_um_pick)}종을 {_um_bulk}에게 **청구**",
                          key="rs_memo_apply", type="primary", disabled=not _um_pick,
                          use_container_width=True,
                          help="이전 주문의 교환·추가 발송분처럼 물건이 이미 그 사람에게 "
                               "나간 경우입니다. 재고로 잡지 않고 바로 청구합니다."):
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
                    _clear_assign_inputs(d_day, _um_pick)
                    st.session_state['_rs_um_open'] = True   # 이어서 배정하도록 열어 둔다
                    st.success(f"✅ {len(_new)}종을 {_um_bulk}에게 청구 추가했습니다 — "
                               "정산표에 반영됐습니다. '정산 요청'을 눌러 저장하세요.")
                    st.rerun()
                else:
                    st.error("배정할 항목을 만들지 못했습니다 (사용자·상품번호 확인).")

            if _b2.button(f"📦 {len(_um_pick)}종을 {_um_bulk} **재고로 입고**",
                          key="rs_stock_apply", disabled=not _um_pick,
                          use_container_width=True,
                          help="안 팔려서 창고에 남은 물건입니다. 그 사용자 재고로 잡히고 "
                               "청구되지 않습니다. 나중에 그 재고로 팔면 자동 차감됩니다."):
                _uname = _um_l2u.get(_um_bulk, '')
                _split = {str(u['상품번호']): max(1, int(u.get('split_qty') or 1))
                          for u in _assign_src}
                _picks = [{'costco_no': str(r.get('상품번호') or ''),
                           'name': str(r.get('상품명') or ''),
                           'unit_price': int(r.get('팩단가') or 0),
                           'split_qty': _split.get(str(r.get('상품번호') or ''), 1),
                           # 재고원장은 소분 단위다 — 팩 수 × split
                           'units_left': int(r.get('수량(팩)') or 1)
                                         * _split.get(str(r.get('상품번호') or ''), 1),
                           'owner': _uname} for r in _um_pick]
                _res = _sc.receive_leftovers(str(d_day), _picks)
                if _res['ok']:
                    _text = (f"📦 {_um_bulk} 재고로 {_res['ok']}종 입고했습니다 — "
                             "아래 **재고 현황 › 사용자별 재고**에서 확인하세요.")
                    if _res['skipped']:
                        _text += f" (이미 입고돼 건너뜀 {_res['skipped']}종)"
                else:
                    _text = (f"입고된 항목이 없습니다 — 선택한 {_res['skipped']}종은 "
                             f"이 날짜({d_day})로 이미 입고돼 있습니다. "
                             "'재고 관리' 탭에서 확인하세요.")
                _clear_assign_inputs(d_day, _um_pick)
                st.session_state['_rs_um_open'] = True
                st.session_state['_rs_lf_msg'] = {
                    'ok': bool(_res['ok']), 'text': _text,
                    'err': ("❌ 실패: " + " / ".join(_res['failed']))
                           if _res['failed'] else '',
                }
                st.rerun()

    # ── 3.4) 잘못 붙은 매칭 끊기 (수동 매칭 바로 위) ──
    _render_unmatch_panel(alloc, dmap, receipt_items)

    # ── 4) 저장 / 정산 요청 ──
    #   둘은 다른 결정이다. 저장은 '여기까지 했다', 정산은 '이 금액으로 청구한다'.
    #   중간에 저장할 데가 없어서 창을 닫으면 배정이 통째로 날아갔다.
    if rows:
        st.divider()
        st.subheader("💾 저장 · ✅ 정산 요청")
        _sv1, _sv2 = st.columns([1, 2])
        if _sv1.button("💾 매칭 저장 (정산 안 함)", key="rs_save_draft",
                       use_container_width=True):
            try:
                # 변수명에 _n을 쓰면 모듈 함수 _n()이 render() 전체에서 가려진다
                # (파이썬은 함수 안 대입만 봐도 그 이름을 지역변수로 확정한다).
                _saved_n = _ds.save_draft(str(d_day), rows, created_by=USERNAME)
                st.success(f"💾 매칭 {_saved_n}건을 저장했습니다 — 창을 닫아도 남습니다. "
                           "아직 사용자에게 청구되지 않았습니다.")
            except Exception as _e:
                st.error(f"저장 실패: {_e}")
        _sv2.caption("**저장**은 여기까지 한 매칭을 붙들어 둘 뿐 사용자에게 아무것도 "
                     "보내지 않습니다. 나중에 이어서 하거나 다른 사람이 검수할 때 씁니다.")
        try:
            _dd = [(_d, _c) for _d, _c in (_ds.draft_dates() or []) if _d != str(d_day)]
        except Exception:
            _dd = []
        if _dd:
            st.caption("📌 저장만 하고 정산하지 않은 날 — "
                       + " · ".join(f"**{_d}** {_c}건" for _d, _c in _dd[:6]))

        # ── 정산 미리보기 — 물건값 + 그날 택배·포장비 ──
        _goods = {u: v['amount'] for u, v in (summary or {}).items()}
        _fees = _sc.fees_for_users(sorted(_goods), str(d_day))
        _prev = []
        for _u in sorted(_goods, key=lambda k: -_goods[k]):
            _f = _fees.get(_u) or {}
            _prev.append({
                '판매자': dmap.get(_u, _u),
                '물건값': int(_goods[_u]),
                '택배비': int(_f.get('ship_fee') or 0),
                '포장비': int(_f.get('pack_fee') or 0),
                '청구액': int(_goods[_u]) + int(_f.get('ship_fee') or 0)
                          + int(_f.get('pack_fee') or 0),
                '발송': int(_f.get('ship_count') or 0),
            })
        _total = sum(r['청구액'] for r in _prev)
        st.dataframe(pd.DataFrame(_prev), use_container_width=True, hide_index=True,
                     column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                                    for _k in ('물건값', '택배비', '포장비', '청구액')})
        st.caption("청구액 = 물건값 + 그날 택배비(발송건수 × 설정) + 그날 포장비. "
                   "월말에 몰아 붙이지 않고 발생한 날에 싣습니다.")

        _render_reconcile(receipt_items, alloc, sum(r['물건값'] for r in _prev), d_day)

        st.warning("⚠️ 정산하면 각 주문의 구입가가 영수증 실단가로 **덮어써지고** "
                   "각 사용자에게 청구금액으로 보입니다. "
                   "청구는 다음 단계입니다 — 관리자 › 정산·청구에서 누르세요.")
        if st.button(f"✅ 정산 요청 ({len(_goods)}명 · {fmt(_total)}원)",
                     type="primary", key="rs_apply_btn"):
            with st.spinner("정산 중..."):
                res = _sc.finalize(str(d_day), rows, created_by=USERNAME)
            try:
                if invalidate_data_cache:
                    invalidate_data_cache()
            except Exception:
                pass
            st.session_state.pop('rs_alloc', None)
            _st = st.session_state.get('rs_sticky') or {}
            _st.pop(str(d_day), None)      # 정산됐으니 더 붙들 이유가 없다
            st.session_state['rs_sticky'] = _st
            try:
                _ds.clear_draft(str(d_day))
            except Exception:
                pass
            _lmsg = ""
            _learn = res.get('learned') or {}
            if _learn.get('filled'):
                _lu = ", ".join(f"{k} {v}건" for k, v in (_learn.get('by_user') or {}).items())
                _lmsg = (f" 🧠 코스트코번호 매핑 {_learn['filled']}건 학습({_lu})"
                         + (f" · 주문 {_learn['orders']}건에 번호 기입"
                            if _learn.get('orders') else "")
                         + " — 다음 정산부터 자동 매칭됩니다.")
            _dropped = res.get('dropped') or []
            if _dropped:
                st.warning(f"⚠️ 단가를 못 찾은 {len(_dropped)}건은 청구서에서 뺐습니다 — "
                           "0원으로 청구하면 그만큼이 그대로 손실입니다. "
                           "제품DB에 단가를 채운 뒤 다시 정산하세요.")
            st.success(
                f"✅ 정산 완료 — 품목 {res['saved']}건 원장 기록, "
                f"주문 {res['applied']}건 구입가 반영, "
                f"사용자 {len(res['totals'])}명 청구서 생성 "
                f"(합계 {fmt(sum(res['totals'].values()))}원). "
                "관리자 › 정산·청구에서 청구하세요." + _lmsg)
            st.rerun()

    _render_stock_status()
    _render_history(dmap, USERNAME)


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




def _render_reconcile(receipt_items, alloc, goods_total, d_day):
    """영수증 합계와 배치 금액을 맞춰 보여준다 — 차액이 어디로 갔는지.

    "영수증 금액과 배치 금액이 안 맞는다"는 말이 계속 나왔다. 실제로는 안 맞는 게
    정상이다 — 그날 산 것 전부가 그날 나가지는 않으니까. 문제는 **남은 돈이
    어디로 갔는지 화면에 없었다**는 것이다. 숫자 둘만 보이면 틀린 것으로 읽힌다.

      영수증 합계 = 배치(청구할 물건값) + 배정 대기(아직 안 나간 것)
    """
    _r_total = 0
    for it in (receipt_items or []):
        try:
            _r_total += int(float(it.get('단가') or 0)) * max(1, int(it.get('수량') or 1))
        except (TypeError, ValueError):
            continue
    if _r_total <= 0:
        return

    _goods = int(goods_total or 0)
    _rest = _r_total - _goods

    st.markdown("##### 🧮 영수증 ↔ 배치 대조")
    m1, m2, m3 = st.columns(3)
    m1.metric("영수증 합계", f"{fmt(_r_total)}원", f"{len(receipt_items or [])}종")
    m2.metric("이번 정산 물건값", f"{fmt(_goods)}원")
    m3.metric("배정 대기", f"{fmt(_rest)}원", delta_color="off")

    if _rest > 0:
        # 왜 남았는지까지 말해 준다. '남았다'만으로는 실수인지 정상인지 알 수 없다.
        _lefts = _sc.leftovers(receipt_items, alloc.get('rows') or [], str(d_day))
        _undisp = len(alloc.get('unmatched_orders') or [])
        _msg = (f"영수증 {fmt(_r_total)}원 중 **{fmt(_goods)}원**만 이번 정산에 들어갑니다. "
                f"나머지 **{fmt(_rest)}원**은 아직 주문에 안 붙은 물건입니다 — "
                "**금액이 틀린 게 아니라** 그날 산 것이 전부 그날 나가지 않아서입니다.")
        if _lefts:
            _msg += (f"\n\n남은 품목 {len(_lefts)}종은 위 **배정할 영수증 품목**에서 "
                     "사용자에게 청구하거나 재고로 넘기세요.")
        if _undisp:
            _msg += (f"\n\n미매칭 주문 {_undisp}건 — 송장이 등록됐는데 영수증에서 상품을 "
                     "못 찾은 건입니다.")
        st.info(_msg)
    elif _rest < 0:
        st.warning(
            f"⚠️ 배치 금액이 영수증보다 **{fmt(-_rest)}원 많습니다**. "
            "이전 구입분(재고)에서 나간 주문이 섞였거나, 그날 영수증이 일부만 "
            "업로드된 것입니다. 위 **매칭 경로**에서 '재고 이월'이 몇 건인지 확인하세요.")
    else:
        st.success("영수증 금액이 전부 이번 정산에 들어갑니다 — 남은 물건이 없습니다.")


def _build_assign_rows(unmatched, receipt_items, alloc, d_day):
    """배정할 영수증 품목 — 주문 못 찾은 것 + 주문에 붙고 남은 것을 한 목록으로.

    예전엔 이 둘이 다른 화면이었다. '주문 못 찾은 품목'은 청구만, '남은 재고
    확인'은 입고만 할 수 있어서, 같은 물건을 놓고 어느 화면으로 가야 하는지
    매번 판단해야 했다. 실제로는 **누구에게 줄 것인가**와 **청구인가 재고인가**
    두 가지만 정하면 되는 일이다.

    남은수량은 팩 단위다(영수증에 찍힌 단위 그대로 읽을 수 있어야 한다).
    이미 그날 재고로 입고한 만큼은 빼서, 나눠 배정해도 수량이 어긋나지 않는다.
    팩에 못 미치는 자투리(1팩을 4소분해 2개만 남은 경우)는 여기 안 나온다 —
    '재고 관리' 탭에서 직접 넣는다.
    """
    try:
        lefts = _sc.leftovers(receipt_items, alloc.get('rows') or [], str(d_day))
    except Exception:
        lefts = []
    if not lefts:
        return []

    # 그날 이미 재고로 넘긴 수량 — 안 빼면 나눠 배정할 때 같은 수량이 또 보인다
    try:
        _lots = _rs.receipt_lot_units(str(d_day), start=str(d_day))
    except Exception:
        _lots = {}

    _price_by = {}
    for it in (receipt_items or []):
        _c = _n(it.get('상품번호'))
        if _c:
            try:
                _price_by[_c] = int(float(it.get('단가') or 0))
            except (TypeError, ValueError):
                _price_by[_c] = 0

    out = []
    for l in lefts:
        _c = str(l['costco_no'])
        _sq = max(1, int(l.get('split_qty') or 1))
        _units = int(l.get('units_left') or 0) - int(_lots.get(_c, 0) or 0)
        _packs = _units // _sq
        if _packs <= 0:
            continue
        out.append({
            '상품번호': _c,
            '상품명': str(l.get('name') or ''),
            '단가': _price_by.get(_c, int(l.get('unit_price') or 0)),
            '영수증수량': int(l.get('qty_receipt') or 0),
            '남은수량': _packs,
            '구분': '주문없음' if int(l.get('units_used') or 0) <= 0 else '팔고남음',
            '요청자': str(l.get('owner') or ''),
            'split_qty': _sq,
        })
    out.sort(key=lambda r: (-r['남은수량'] * r['단가'], r['상품명']))
    return out


def _clear_assign_inputs(d_day, picks):
    """배정 뒤 수량·메모 위젯 값을 지운다.

    배정하면 남은 수량이 줄어드는데 수량칸에 옛 값(예: 3)이 남아 있으면
    새 최대치(1)를 넘어 위젯이 오류를 낸다.
    """
    for _r in (picks or []):
        for _sfx in ('_q', '_m'):
            st.session_state.pop(f"rs_asg_{d_day}_{_r['상품번호']}{_sfx}", None)


def _render_stock_status():
    """📦 재고 — '아직 임자 없는 구입잔량'과 '사용자별 재고'를 갈라서 본다.

    예전엔 이 화면이 둘을 한 숫자로 섞어 보여 줬다. 영수증으로 산 것에서 정산에
    배치된 것만 빼서 남긴 값이라, **사용자 재고로 배정한 물건이 그대로 남아 있었다**.
    배정을 해도 숫자가 안 줄어드니 "이게 왜 이렇게 되어 있나"가 될 수밖에 없다.

    이제 갈라 놓는다:
      배정 대기 = 영수증 입고 − 정산 배치 − 사용자 재고로 배정한 수량
      사용자별 재고 = inventory_lots (배정한 순간 여기로 옮겨 온다)
    """
    st.divider()
    st.subheader("📦 재고 현황")
    _sd = get_settle_start_date()
    st.caption("**배정 대기** = 영수증으로 샀는데 아직 주문에도 안 붙고 "
               "누구 재고로도 안 넘긴 물건 · **사용자별 재고** = 배정을 마쳐 그 사람 것이 된 물건"
               + (f"  ·  기준일 **{_sd}** 이후" if _sd else "  ·  전체 기간"))

    _t_wait, _t_user = st.tabs(["⏳ 배정 대기", "👥 사용자별 재고"])

    with _t_wait:
        try:
            rows = get_stock_status()
        except Exception as e:
            st.error(f"재고 조회 실패: {e}")
            rows = []
        if not rows:
            st.info("영수증을 업로드하면 구입분이 여기 잡힙니다.")
        else:
            # 재고가 부풀어 보이는 원인 1위 — 미리보기에서 매칭만 하고 '정산 요청'을
            # 누르지 않으면 사용량이 0이라 산 것이 통째로 남는다. 화면에는
            # '사용 0'만 보여 매칭이 안 된 것처럼 읽힌다 — 매칭은 됐고 저장이 안 된 것이다.
            try:
                _unap = _rs.unapplied_receipt_dates()
            except Exception:
                _unap = []
            if _unap:
                _msg = ["🚨 **정산하지 않은 영수증이 있습니다** — 그날 산 것이 "
                        "통째로 배정 대기로 잡혀 있습니다.", ""]
                _msg += [f"- **{d}** 영수증 {n}종 · 정산 0건" for d, n in _unap[:6]]
                _msg += ["", "미리보기에서 매칭만 하고 **'정산 요청'을 누르지 않으면** "
                         "사용량이 0으로 남습니다. 위 날짜로 영수증 정산을 다시 열어 "
                         "매칭 후 **정산 요청**까지 누르면 여기서 빠집니다."]
                st.error("\n".join(_msg))

            _left = [r for r in rows if r['units_left'] > 0]
            _neg = [r for r in rows if r['units_left'] < 0]
            _amt = sum(r['amount'] for r in _left)
            _m1, _m2, _m3 = st.columns(3)
            _m1.metric("배정 대기", f"{len(_left)}종")
            _m2.metric("묶인 금액", f"{fmt(_amt)}원")
            _m3.metric("소진 품목", f"{len(rows) - len(_left) - len(_neg)}종")

            if _neg:
                st.warning(f"⚠️ 사용량이 입고량을 넘은 품목 {len(_neg)}종 — 영수증 누락이 "
                           "의심됩니다. 그날 구매한 영수증이 업로드됐는지 확인하세요.")

            _only = st.checkbox("잔량 있는 것만", value=True, key="rs_stock_only")
            _view = _left if _only else rows
            st.dataframe(pd.DataFrame([
                {'코스트코번호': r['costco_no'], '상품명': str(r['name'])[:34],
                 '입고': r['units_in'], '주문사용': r['units_used'],
                 '재고배정': r.get('units_assigned', 0), '배정대기': r['units_left'],
                 '단가': r['price'], '묶인금액': r['amount']}
                for r in _view
            ]), use_container_width=True, hide_index=True,
                column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                               for _k in ('입고', '주문사용', '재고배정', '배정대기',
                                          '단가', '묶인금액')})
            st.caption("**입고** = 영수증 구매 · **주문사용** = 정산에서 주문에 붙은 양 · "
                       "**재고배정** = 사용자 재고로 넘긴 양 · **배정대기** = 남은 것. "
                       "단위는 소분 단위입니다 — 1팩을 N개로 나눠 파는 상품은 낱개 기준입니다.")

    with _t_user:
        try:
            from db_inventory import get_stock_summary
            _lots = get_stock_summary() or []
        except Exception as e:
            st.error(f"사용자 재고 조회 실패: {e}")
            _lots = []
        if not _lots:
            st.info("사용자 재고가 없습니다. 위 **배정할 영수증 품목**에서 "
                    "'재고로 입고'를 누르면 그 사용자 재고로 잡힙니다.")
            _render_lot_undo()
            return

        _dm = _disp_map()
        _by_owner = {}
        for r in _lots:
            _by_owner.setdefault(str(r['owner']), []).append(r)

        st.caption(f"보유자 {len(_by_owner)}명 · {len(_lots)}종 — "
                   "이 재고로 판매가 일어나면 자동 차감되고, 남의 재고에서 빠지면 "
                   "보유자에게 교차정산 웃돈이 붙습니다.")
        st.dataframe(pd.DataFrame([
            {'보유자': _dm.get(_o, _o), '품목': len(_rs_),
             '수량(소분)': sum(int(x['qty_left'] or 0) for x in _rs_),
             '가장 오래된 입고': min(str(x['oldest_at'] or '') for x in _rs_)}
            for _o, _rs_ in sorted(_by_owner.items(),
                                   key=lambda kv: -sum(int(x['qty_left'] or 0)
                                                       for x in kv[1]))
        ]), use_container_width=True, hide_index=True,
            column_config={'수량(소분)': st.column_config.NumberColumn(
                '수량(소분)', format='%d')})

        _sel = st.selectbox("보유자별 상세", sorted(_by_owner),
                            format_func=lambda u: _dm.get(u, u), key="rs_stock_owner")
        st.dataframe(pd.DataFrame([
            {'코스트코번호': r['product_no'], '상품명': str(r['product_name'])[:34],
             '입고': int(r['qty_in'] or 0), '남음': int(r['qty_left'] or 0),
             '입고일': str(r['oldest_at'] or ''), '경과일': int(r['age_days'] or 0)}
            for r in _by_owner[_sel]
        ]), use_container_width=True, hide_index=True,
            column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                           for _k in ('입고', '남음', '경과일')})
        st.caption("수량은 소분 단위입니다.")
        _render_lot_undo()


def _render_lot_undo():
    """영수증 정산에서 넣은 재고 입고를 되돌린다.

    입고는 사람이 판단해 넣는 값이라 틀릴 수 있는데, 되돌릴 방법이 화면에 없어서
    잘못 넣으면 그대로 남았다. 지운 만큼 '배정 대기'로 돌아오므로 다시 배정하면 된다.

    판매에 쓰인 lot은 지우지 않는다 — inventory_moves가 그 lot을 가리키고 있어
    지우면 '누구 재고에서 나갔는지'와 교차정산 웃돈의 근거를 잃는다.
    """
    _msg = st.session_state.pop('_rs_lot_msg', None)
    if _msg:
        (st.success if _msg.get('ok') else st.warning)(_msg.get('text', ''))

    with st.expander("↩️ 재고 입고 되돌리기 — 잘못 넣은 입고 취소", expanded=False):
        try:
            from db_inventory import find_receipt_lots, delete_lots
            lots = find_receipt_lots() or []
        except Exception as e:
            st.error(f"입고 이력 조회 실패: {e}")
            return
        if not lots:
            st.caption("영수증 정산으로 입고한 재고가 없습니다.")
            return

        _dm = _disp_map()
        _dates = sorted({str(l['received_at']) for l in lots}, reverse=True)
        _pick_d = st.selectbox("입고일", ["(전체)"] + _dates, key="rs_lot_date")
        _view = [l for l in lots
                 if _pick_d == "(전체)" or str(l['received_at']) == _pick_d]

        st.caption("지운 만큼 **배정 대기**로 돌아옵니다 — 다시 배정하면 됩니다. "
                   "이미 판매에 쓰인 입고는 지울 수 없습니다(근거가 사라지므로).")
        _ed = st.data_editor(
            pd.DataFrame([{
                '취소': False,
                '입고일': str(l['received_at']),
                '보유자': _dm.get(str(l['owner']), str(l['owner'])),
                '상품명': str(l['product_name'])[:30],
                '코스트코번호': str(l['product_no']),
                '입고': int(l['qty_in'] or 0),
                '남음': int(l['qty_left'] or 0),
                '판매사용': int(l.get('used') or 0),
                '_id': int(l['id']),
            } for l in _view]),
            use_container_width=True, hide_index=True,
            key=f"rs_lot_ed_{_pick_d}",
            disabled=['입고일', '보유자', '상품명', '코스트코번호', '입고', '남음',
                      '판매사용', '_id'],
            column_config={
                '취소': st.column_config.CheckboxColumn('취소', help='체크한 입고를 지웁니다'),
                '판매사용': st.column_config.NumberColumn(
                    '판매사용', format='%d', help='0보다 크면 지울 수 없습니다'),
                '_id': None,
            })
        _picked = [r for r in _ed.to_dict('records') if r.get('취소')]
        if not _picked:
            st.caption("되돌릴 입고를 체크하세요.")
            return

        _blocked = [r for r in _picked if int(r.get('판매사용') or 0) > 0
                    or int(r.get('남음') or 0) != int(r.get('입고') or 0)]
        if _blocked:
            st.warning(f"⚠️ {len(_blocked)}건은 이미 판매에 쓰였거나 일부 차감돼 "
                       "건너뜁니다 — **재고 관리** 탭에서 수량을 고치세요.")
        st.markdown(f"취소할 입고 **{len(_picked) - len(_blocked)}건** · "
                    f"수량 {sum(int(r['입고']) for r in _picked if r not in _blocked)}소분")

        if st.button(f"↩️ 선택한 {len(_picked)}건 입고 취소", key="rs_lot_del"):
            _res = delete_lots([int(r['_id']) for r in _picked])
            if _res['deleted']:
                _text = (f"↩️ 입고 {_res['deleted']}건을 취소했습니다 — "
                         "그만큼 **배정 대기**로 돌아왔습니다.")
                if _res['skipped']:
                    _text += f" (건너뜀 {len(_res['skipped'])}건)"
            else:
                _text = ("취소된 입고가 없습니다 — "
                         + " / ".join(f"#{x['id']} {x['reason']}"
                                      for x in _res['skipped'][:5]))
            st.session_state['_rs_lot_msg'] = {'ok': bool(_res['deleted']), 'text': _text}
            st.rerun()


def _render_history(dmap, USERNAME=''):
    """정산 이력 — 원장에 남은 것 그대로.

    예전에는 '배치'라는 별도 개념이 있었고, 배치 안에 부족분·재고분·근거분해가
    따로 저장됐다. 그러다 보니 배치 합계와 실제 청구액이 어긋났다(하루에 두 번
    돌리면 마지막 회차만 남는 식). 이제 정산 결과는 날짜×사용자 청구서 하나뿐이라
    여기 보이는 값이 곧 청구되는 값이다.
    """
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

    dates = _ds.settled_dates(limit=30)
    if not dates:
        st.caption("아직 정산한 날짜가 없습니다.")
        return

    for b in dates:
        _d = str(b['settle_date'])
        _tag = ""
        if b['paid']:
            _tag += f" · 🟢 입금 {b['paid']}명"
        if b['billed']:
            _tag += f" · 🟡 미입금 {b['billed']}명"
        with st.expander(
            f"{_d} · 사용자 {b['users']}명 · 총 {fmt(int(b['total'] or 0))}원{_tag}",
            expanded=False
        ):
            invs = _ds.list_invoices(_d)
            st.dataframe(pd.DataFrame([{
                '상태': _ds.STATUS_LABEL.get(i['status'], i['status']),
                '사용자': dmap.get(i['username'], i['username']),
                '품목수': int(i['item_count'] or 0),
                '물건값': int(i['goods_amount'] or 0),
                '택배·포장': int(i['ship_fee'] or 0) + int(i['pack_fee'] or 0),
                '청구액': int(i['total_amount'] or 0),
            } for i in invs]), use_container_width=True, hide_index=True,
                column_config={_k: st.column_config.NumberColumn(_k, format='%d')
                               for _k in ('물건값', '택배·포장', '청구액')})

            # 근거 분해 — 이 돈이 영수증 실단가인지 재고 단가인지 수동인지
            _items = _ds.get_items(_d)
            if _items:
                _basis = {}
                for _it in _items:
                    _e = _basis.setdefault(_it['username'], {})
                    _s = _ds.SOURCE_LABEL.get(_it['source'], _it['source'])
                    _c = _e.setdefault(_s, [0, 0])
                    _c[0] += 1
                    _c[1] += int(_it['amount'] or 0)
                st.markdown("**🧾 청구 근거 분해**")
                _keys = sorted({k for v in _basis.values() for k in v})
                st.dataframe(pd.DataFrame([
                    {'사용자': dmap.get(_u, _u),
                     **{_k: (f"{_v[_k][0]}건 · {fmt(_v[_k][1])}원" if _k in _v else "-")
                        for _k in _keys}}
                    for _u, _v in sorted(_basis.items())
                ]), use_container_width=True, hide_index=True)
                st.caption("**영수증** = 그날 코스트코 영수증 실단가 · **재고** = 이전 구입분 "
                           "lot 단가 · **수동/직접청구** = 관리자가 지정한 단가")

            _c1, _c2 = st.columns([3, 1])
            _c1.caption("정산을 취소하면 그날 품목과 청구서가 지워집니다. "
                        "**입금완료된 사용자는 남습니다** — 받은 돈의 근거를 지울 수 없으니까요. "
                        "취소 후 다시 매칭해 정산하면 됩니다.")
            if _c2.button("🗑 이 날짜 정산 취소", key=f"rs_del_{_d}"):
                _n_del, _kept = _ds.delete_settlement(_d)
                _msg = f"🗑 {_d} 정산 취소 — 사용자 {_n_del}명"
                if _kept:
                    _msg += (" · 입금완료라 남긴 사용자: "
                             + ", ".join(dmap.get(_u, _u) for _u in _kept))
                st.session_state['_rs_hist_msg'] = _msg
                st.rerun()

    _hm = st.session_state.pop('_rs_hist_msg', None)
    if _hm:
        st.info(_hm)

    _render_reset_panel(dmap, USERNAME)


def _render_reset_panel(dmap, USERNAME=''):
    """정산을 처음부터 다시 쌓기 위한 전체 초기화.

    날짜별 취소는 위에 있지만, 며칠치가 엉킨 상태에서는 하나씩 지우는 것이
    더 위험하다(어디까지 지웠는지 모른다). 한 번에 비우고 다시 쌓는 길을 둔다.
    되돌릴 수 없는 동작이라 이름을 직접 입력해 확인받는다.
    """
    _rm = st.session_state.pop('_rs_reset_msg', None)
    if _rm:
        (st.success if _rm.get('ok') else st.warning)(_rm.get('text', ''))

    with st.expander("🧨 정산 전체 초기화 — 처음부터 다시 쌓기", expanded=False):
        st.warning(
            "**되돌릴 수 없습니다.** 정산 품목·청구서를 비우고 각 주문의 구입가를 "
            "정산 전 값으로 되돌립니다.\n\n"
            "**남는 것** — 영수증 품목 · 공유상품 · 코스트코번호 매핑 · 발송 기록. "
            "영수증과 매핑은 정산의 *입력*이지 결과가 아니라 그대로 둡니다. "
            "비운 뒤 같은 영수증으로 다시 정산하면 됩니다.")

        try:
            _inv = _ds.list_invoices('2000-01-01', '2099-12-31')
        except Exception as _e:
            st.error(f"조회 실패: {_e}")
            return
        _paid = [i for i in _inv if i['status'] == 'paid']
        if not _inv:
            st.caption("지울 정산이 없습니다.")
        else:
            st.markdown(
                f"대상 — 청구서 **{len(_inv)}건** · 합계 "
                f"**{fmt(sum(int(i['total_amount'] or 0) for i in _inv))}원** · "
                f"날짜 {len({i['settle_date'] for i in _inv})}일")
        if _paid:
            st.info(f"🟢 입금완료 {len(_paid)}건은 기본적으로 **남깁니다** — "
                    "받은 돈의 근거를 지우면 그 입금이 무엇에 대한 것이었는지 "
                    "설명할 수 없습니다.")

        _c1, _c2 = st.columns(2)
        _drop_lots = _c1.checkbox(
            "재고 입고도 되돌리기", value=True, key="rs_reset_lots",
            help="영수증 정산으로 넣은 재고 입고를 함께 취소합니다. "
                 "판매에 쓰인 입고는 건너뜁니다.")
        _inc_paid = _c2.checkbox(
            "입금완료분까지 전부 삭제", value=False, key="rs_reset_paid",
            help="받은 돈의 근거까지 지웁니다. 정말 전부 다시 쌓을 때만 켜세요.")

        _need = "초기화"
        _typed = st.text_input(
            f"확인 — 아래 칸에 **{_need}** 라고 입력하세요", key="rs_reset_confirm",
            placeholder=_need)
        if st.button("🧨 전체 초기화 실행", key="rs_reset_go",
                     disabled=(str(_typed).strip() != _need)):
            try:
                _res = _ds.reset_all(include_paid=_inc_paid, restore_cost=True,
                                     drop_lots=_drop_lots)
            except Exception as _e:
                st.session_state['_rs_reset_msg'] = {
                    'ok': False, 'text': f"❌ 초기화 실패: {_e}"}
                st.rerun()
            _t = (f"🧨 초기화 완료 — 품목 {_res['items']}건 · 청구서 {_res['invoices']}건 삭제 · "
                  f"구입가 {_res['restored']}건 복원")
            if _res.get('lots'):
                _t += f" · 재고 입고 {_res['lots']}건 취소"
            if _res.get('kept_paid'):
                _t += (" · 입금완료라 남긴 것: "
                       + ", ".join(f"{d} {dmap.get(u, u)}"
                                   for d, u in _res['kept_paid'][:5]))
            _t += "\n\n같은 영수증으로 다시 정산하면 됩니다."
            st.session_state['_rs_reset_msg'] = {'ok': True, 'text': _t}
            st.session_state.pop('rs_alloc', None)
            st.session_state.pop('rs_sticky', None)
            st.rerun()
        if str(_typed).strip() != _need:
            st.caption(f"안전을 위해 **{_need}** 를 입력해야 버튼이 켜집니다.")


def _n(s):
    return str(s or '').strip()
