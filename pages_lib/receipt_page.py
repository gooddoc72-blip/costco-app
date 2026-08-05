"""🧾 영수증 등록 페이지 — pages_lib 자동 추출."""
import os
import io
import sys
import json
import subprocess
import sqlite3
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
try:
    import plotly.express as px
except ImportError:
    px = None

from db import (
    init_auth_db, hash_pw, check_login, get_global_setting, set_global_setting,
    register_user, get_pending_users, approve_user, reject_user, get_all_users,
    add_user, delete_user, change_password, get_user_info,
    create_session, get_session_user, delete_session,
    get_shared_products, upsert_shared_product, delete_shared_product, upsert_shared_store_price,
    get_user_db, init_user_db, get_setting, set_setting, get_all_settings, get_all_products,
    upsert_user_private, get_all_products_merged, upsert_product,
    get_product_detail,
    save_daily_orders, get_daily_orders, save_order_history, search_order_history,
    save_receipt_items, get_recent_receipt_items, delete_receipt_items_by_date, get_receipt_dates,
    get_date_range_stats, get_monthly_stats, get_product_ranking, get_saved_dates,
    get_dashboard_kpi, get_daily_profit_trend, get_week_best_products,
    get_price_history_monthly, save_price_changes_to_history, get_price_change_history,
    add_keyword_tracking, get_keyword_trackings, delete_keyword_tracking,
    save_rank_result, get_rank_history, get_latest_ranks,
    get_daily_ranks_in_month, get_yearly_rank_history, delete_trackings_bulk,
    get_rank_drops,
    AUTH_DB,
)
from services import (
    match_product_to_db, match_shared_product,
    update_product_info_from_orders, update_product_shipping_fees, update_product_sale_price,
    detect_price_changes, build_price_alert_msg,
    parse_costco_receipt_pdf, match_receipt_to_orders,
    match_receipt_to_naver_products, apply_receipt_pno_updates,
    apply_receipt_to_unmatched_daily_orders,
    get_last_skipped_box_prices,
    decrypt_excel, read_excel_auto,
    _token_score,
)
from utils import (
    fmt, to_id_str, extract_pack_qty, clean_name, has_meaningful_char,
    get_ngrams, calc_match_score, MIN_MATCH_SCORE, get_week_range, get_month_range,
)
from ui_theme import (
    COLORS, CHART_COLORS, hero_section, section_header,
    kpi_card, chart_card_open, chart_card_close, quick_action_buttons,
)

try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False
    naver_api = None

# app.py 라우터에서 주입되는 cached wrapper들
cached_shared_products = None
cached_user_products = None
cached_merged = None
invalidate_data_cache = None


def _set_cache_helpers(shared_fn, user_fn, merged_fn, invalidate_fn, **kwargs):
    global cached_shared_products, cached_user_products, cached_merged, invalidate_data_cache
    cached_shared_products = shared_fn
    cached_user_products = user_fn
    cached_merged = merged_fn
    invalidate_data_cache = invalidate_fn


_IMG_TYPES = ['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif']


def _image_for_ai(upload) -> tuple:
    """업로드 이미지 → (bytes, media_type). HEIC(아이폰)은 JPEG로 변환.
    반환 실패 시 (None, 오류메시지)."""
    try:
        upload.seek(0)
        raw = upload.read()
    except Exception as e:
        return None, f"파일 읽기 오류: {e}"
    if not raw:
        return None, "빈 파일"
    _ext = (upload.name.rsplit('.', 1)[-1] if '.' in upload.name else '').lower()
    _mt = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
           'webp': 'image/webp'}.get(_ext, '')
    if _mt:
        return (raw, _mt), None
    # HEIC/HEIF 등 Claude 미지원 포맷 → PIL로 JPEG 변환 시도
    try:
        try:
            import pillow_heif   # noqa: F401  (설치돼 있으면 HEIC 디코딩 등록)
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=90)
            return (buf.getvalue(), "image/jpeg"), None
    except Exception:
        return None, (f"{_ext.upper()} 형식은 읽을 수 없습니다. "
                      "휴대폰 설정에서 '호환성(JPEG)' 촬영으로 바꾸거나 JPG/PNG로 저장 후 올려주세요.")


def _receipt_ledger_excel(rows: list, items: list) -> bytes:
    """매입 장부 → 엑셀 bytes (시트: 영수증 / 품목내역)."""
    _hdr = pd.DataFrame([{
        "구매일자": r.get('purchase_date', ''),
        "구매시각": r.get('purchase_time', ''),
        "구매매장명": r.get('store_type', '') or r.get('store_name', ''),
        "매장(지점)": r.get('store_name', ''),
        "구매수량": int(r.get('total_qty') or 0),
        "품목종수": int(r.get('item_kinds') or 0),
        "구매금액": int(r.get('total_amount') or 0),
        "할인금액": int(r.get('discount_amount') or 0),
        "사용카드 끝4자리": r.get('card_last4', ''),
        "현금영수증 승인번호": r.get('cash_receipt_no', ''),
        "메모": r.get('memo', ''),
    } for r in rows])
    _itm = pd.DataFrame([{
        "구매일자": it.get('purchase_date', ''),
        "구매매장명": it.get('store_type', '') or it.get('store_name', ''),
        "상품번호": it.get('product_no', ''),
        "상품명": it.get('product_name', ''),
        "수량": int(it.get('qty') or 0),
        "단가": int(it.get('unit_price') or 0),
        "금액": int(it.get('amount') or 0),
        "할인": int(it.get('discount') or 0),
    } for it in items])
    _buf = io.BytesIO()
    with pd.ExcelWriter(_buf, engine='openpyxl') as _w:
        (_hdr if not _hdr.empty else pd.DataFrame(
            columns=["구매일자", "구매매장명", "구매수량", "구매금액", "할인금액",
                     "사용카드 끝4자리", "현금영수증 승인번호"])).to_excel(
            _w, sheet_name="영수증", index=False)
        if not _itm.empty:
            _itm.to_excel(_w, sheet_name="품목내역", index=False)
    return _buf.getvalue()


def inject_native_camera(marker: str):
    """업로더의 <input type=file>에 capture 속성을 붙여 '누르면 폰 기본 카메라'가 열리게 한다.

    Streamlit의 st.file_uploader는 capture 속성을 노출하지 않아, 폰에서 누르면
    파일선택 메뉴가 먼저 뜨고 거기서 '사진 찍기'를 또 골라야 했다.
    capture="environment"를 붙이면 탭 한 번에 후면 카메라가 바로 열리고,
    촬영을 마치면 input change가 발생해 그대로 자동 업로드된다.
    (st.camera_input은 웹캠 위젯이라 화질이 낮아 영수증 판독에 부적합)

    marker: 그 업로더 라벨에 들어 있는 문구 — 이 문구를 가진 업로더에만 적용.
    Streamlit이 리렌더할 때마다 DOM이 갈리므로 주기적으로 다시 적용한다.
    """
    import streamlit.components.v1 as _components
    _components.html(
        """
<script>
(function () {
  var MARK = %s;
  var doc = window.parent.document;
  function apply() {
    var boxes = doc.querySelectorAll('[data-testid="stFileUploader"]');
    for (var i = 0; i < boxes.length; i++) {
      var el = boxes[i];
      if (!el.innerText || el.innerText.indexOf(MARK) === -1) continue;
      var inp = el.querySelector('input[type=file]');
      if (inp && inp.getAttribute('capture') !== 'environment') {
        inp.setAttribute('capture', 'environment');
        inp.setAttribute('accept', 'image/*');
      }
    }
  }
  apply();
  setInterval(apply, 700);
})();
</script>
""" % (repr(str(marker)),),
        height=0,
    )


def _ai_error_hint(msg: str) -> str:
    """AI 판독 오류 → 사용자가 바로 조치할 수 있는 한국어 안내.

    영문 원문만 보여주면 뭘 해야 할지 알 수 없다. 특히 크레딧 소진은
    사진을 다시 찍어도 절대 안 되므로 명확히 구분해 준다.
    """
    _m = str(msg or '')
    _low = _m.lower()
    if 'credit balance is too low' in _low or 'insufficient' in _low:
        return ("💳 **Claude(Anthropic) 크레딧이 소진**됐습니다. 사진을 다시 찍어도 안 됩니다.\n\n"
                "· 가장 빠른 해결: **설정 탭 > 🤖 AI 설정에 Gemini 키 등록** "
                "(aistudio.google.com/apikey · 무료 등급 있음). 등록 즉시 판독이 다시 됩니다.\n"
                "· 또는 console.anthropic.com > Plans & Billing에서 크레딧 충전.")
    if 'rate limit' in _low or '429' in _m:
        return "⏳ 요청이 몰렸습니다(rate limit). 30초쯤 뒤 **다시 판독**을 눌러주세요."
    if 'api 키 미설정' in _m or 'AI 키 없음' in _m:
        return "🔑 설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키를 등록하세요."
    if 'timeout' in _low or 'timed out' in _low:
        return "⏱ 판독이 시간 초과됐습니다. 사진 용량이 크면 조금 기다렸다 다시 시도해주세요."
    return ""


def _persist_receipt_items(username: str, items: list):
    """영수증 품목을 DB(receipt_items)에 저장 — 수익계산의 영수증 매칭 입력.

    이 테이블이 비어 있으면 수익계산은 영수증을 하나도 못 쓴다
    (compute는 receipt_by_pno/receipt_matches를 이 데이터로 만든다).
    실패해도 화면 흐름은 끊지 않는다 — 저장은 부가 작업.
    """
    _rows = [it for it in (items or [])
             if str(it.get('상품명', '') or '').strip()
             and str(it.get('receipt_date', '') or '').strip()]
    if not _rows:
        return 0, 0
    try:
        return save_receipt_items(username, _rows)
    except Exception as _e:
        st.caption(f"⚠️ 영수증 DB 저장 실패(화면 표시는 정상): {_e}")
        return 0, 0


def _render_photo_receipt(USERNAME: str, settings: dict, compact: bool = False):
    """📱 휴대폰 영수증 사진 → AI 파싱 → 매입 장부 저장 → 엑셀(재고관리용).

    수집 항목: 구매일자 · 구매매장명(코스트코/트레이더스) · 구매수량 · 구매금액 ·
              할인금액 · 사용카드 끝4자리 · 현금영수증 승인번호 (+ 품목 내역)
    """
    from db import (get_global_setting, save_purchase, list_purchases,
                    get_purchase_items_range, delete_purchase, STORE_TYPES)
    import ai_service

    if compact:
        st.markdown("##### 📱 영수증 사진 촬영/업로드 (휴대폰)")
    else:
        st.subheader("📱 영수증 사진 업로드 (휴대폰 촬영)")
    st.caption("구매일자 · 매장(코스트코/트레이더스) · 구매수량 · 구매금액 · 할인금액 · "
               "카드 끝4자리 · 현금영수증 승인번호를 AI가 읽어 매입 장부에 저장합니다.")

    # 판독 전략: Gemini로 먼저 읽고 영수증 내장 체크섬(합계·수량)으로 자가검증 →
    #            검증 실패한 사진만 Claude로 재판독. (ai_service.parse_receipt_photo)
    _ai_key = (get_global_setting('anthropic_api_key')
               or settings.get('anthropic_api_key') or '')
    _g_key = (get_global_setting('gemini_api_key')
              or settings.get('gemini_api_key') or '')
    if not (_ai_key or _g_key):
        st.warning("⚠️ AI 키가 없어 사진 판독을 할 수 없습니다. "
                   "설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키를 먼저 등록하세요.")
    elif _g_key and _ai_key:
        st.caption("🤖 판독: Gemini 1차 → 금액·수량 자가검증 → 불일치 시 Claude 재판독")
    elif _g_key:
        st.caption("🤖 판독: Gemini (Claude 키를 함께 넣으면 검증 실패 사진을 재판독합니다)")

    _cc1, _cc2 = st.columns(2)
    with _cc1:
        _shots = st.file_uploader(
            "📷 영수증 촬영 (누르면 카메라)",
            type=_IMG_TYPES, key="receipt_shot", accept_multiple_files=True)
    with _cc2:
        _photos = st.file_uploader(
            "🖼 갤러리·파일에서 선택",
            type=_IMG_TYPES, key="receipt_photo", accept_multiple_files=True)
    # 왼쪽 업로더만 '탭 → 기본 카메라 → 촬영 → 자동 업로드'가 되게 capture 부여
    inject_native_camera("영수증 촬영")
    st.caption("📱 왼쪽을 누르면 폰 **기본 카메라가 바로 열리고**, 촬영을 마치면 자동으로 올라갑니다. "
               "영수증이 화면에 꽉 차게, 세로로 곧게 찍어주세요. (여러 장 가능)")
    # 구형 브라우저 등 capture가 막힌 환경용 최후 수단 — 웹캠이라 화질이 낮다.
    with st.expander("📷 웹 카메라로 찍기 (카메라가 안 열릴 때만)", expanded=False):
        st.caption("화질이 낮아 깨알글씨 판독이 실패할 수 있습니다.")
        _cam = st.camera_input("영수증을 화면에 꽉 차게 찍어주세요", key="receipt_cam")
    _uploads = list(_shots or []) + list(_photos or [])
    if _cam is not None:
        _uploads.append(_cam)

    _cache = st.session_state.setdefault('_rcpt_photo_parsed', {})

    if _uploads and (_ai_key or _g_key):
        _todo = []
        for _up in _uploads:
            _sig = f"{getattr(_up, 'name', 'cam')}|{getattr(_up, 'size', 0)}"
            if _sig not in _cache:
                _todo.append((_sig, _up))
        if _todo:
            _prog = st.progress(0.0, text="영수증 판독 중...")
            for _n, (_sig, _up) in enumerate(_todo, 1):
                _prog.progress(_n / len(_todo), text=f"영수증 판독 중... ({_n}/{len(_todo)})")
                _img, _ierr = _image_for_ai(_up)
                if _ierr:
                    _cache[_sig] = {'_error': _ierr, '_name': getattr(_up, 'name', '사진')}
                    continue
                _data, _perr = ai_service.parse_receipt_photo(_ai_key, _img[0], _img[1],
                                                              gemini_key=_g_key)
                if _perr or not _data:
                    _cache[_sig] = {'_error': _perr or '판독 실패',
                                    '_name': getattr(_up, 'name', '사진')}
                else:
                    _data['_name'] = getattr(_up, 'name', '사진')
                    _cache[_sig] = _data
            _prog.empty()

    # ── 판독 결과 확인·수정·저장 ──
    #  저장/버린 사진도 sig를 캐시에 남긴다 — 업로더에 파일이 남아 있어도 재판독(=재과금) 방지.
    _parsed = [(_s, _d) for _s, _d in _cache.items()
               if not _d.get('_error') and not _d.get('_done')]
    _failed = [(_s, _d) for _s, _d in _cache.items() if _d.get('_error')]

    for _fi, (_sig, _d) in enumerate(_failed):
        _fc1, _fc2 = st.columns([4, 1])
        _fc1.error(f"⚠️ {_d.get('_name', '사진')} 판독 실패 — {_d['_error']}")
        _hint = _ai_error_hint(_d['_error'])
        if _hint:
            _fc1.warning(_hint)
        if _fc2.button("🔄 다시 판독", key=f"rp_retry_{_fi}"):
            _cache.pop(_sig, None)
            st.rerun()

    if _parsed:
        _nok = sum(1 for _, _d in _parsed if _d.get('_verified', True))
        st.success(f"✅ {len(_parsed)}장 판독 완료 (자가검증 통과 {_nok}장) — "
                   "값을 확인·수정한 뒤 저장하세요.")
    for _sig, _d in _parsed:
        _vok = bool(_d.get('_verified', True))
        _prov = {'gemini': 'Gemini', 'claude': 'Claude'}.get(_d.get('_provider'), '')
        _label = (("🧾 " if _vok else "⚠️ ")
                  + f"{_d.get('purchase_date') or '날짜?'} · "
                  + f"{_d.get('store_type') or _d.get('store_name') or '매장?'} · "
                  + f"{int(_d.get('total_amount') or 0):,}원"
                  + (f"  ·  {_prov}" if _prov else ""))
        with st.expander(_label, expanded=(len(_parsed) == 1 or not _vok)):
            # 영수증 자가검증 결과 — 금액/수량이 안 맞으면 AI가 숫자를 잘못 읽었다는 신호
            _iss = _d.get('_check') or []
            if not _vok:
                st.error("❗ 영수증 합계와 품목이 맞지 않습니다 — 저장 전 값을 꼭 확인하세요.\n"
                         + "\n".join(f"- {x}" for x in _iss[:6]))
            elif _iss:
                st.warning("⚠️ 판독 참고사항\n" + "\n".join(f"- {x}" for x in _iss[:6]))
            else:
                st.caption("✔ 자가검증 통과 — 품목 합계 = 결제금액, 품목 수량 = 총수량")
            # 위젯 키는 파일 시그니처 기준 — 목록 순서가 바뀌어도 입력값이 섞이지 않게
            _k = "rp_" + "".join(ch if ch.isalnum() else "_" for ch in _sig)[-40:]
            _c1, _c2, _c3 = st.columns(3)
            _date = _c1.text_input("구매일자 (YYYY-MM-DD)", value=_d.get('purchase_date', ''),
                                   key=f"{_k}_date")
            _stype = _c2.selectbox(
                "구매매장명", STORE_TYPES,
                index=STORE_TYPES.index(_d['store_type']) if _d.get('store_type') in STORE_TYPES else 0,
                key=f"{_k}_stype")
            _sname = _c3.text_input("매장(지점)", value=_d.get('store_name', ''), key=f"{_k}_sname")

            _c4, _c5, _c6 = st.columns(3)
            _qty = _c4.number_input("구매수량", min_value=0, step=1,
                                    value=int(_d.get('total_qty') or 0), key=f"{_k}_qty")
            _amt = _c5.number_input("구매금액", min_value=0, step=100,
                                    value=int(_d.get('total_amount') or 0), key=f"{_k}_amt")
            _disc = _c6.number_input("할인금액", min_value=0, step=100,
                                     value=int(_d.get('discount_amount') or 0), key=f"{_k}_disc")

            # 사용자가 수정한 값 기준으로 재검증 (수정으로 맞춰졌는지 즉시 확인)
            if _d.get('items'):
                _lok, _liss = ai_service.validate_receipt(
                    {**_d, 'total_qty': _qty, 'total_amount': _amt, 'discount_amount': _disc})
                if _lok:
                    st.caption("✔ 현재 입력값 기준 검증 통과")
                else:
                    st.warning("⚠️ 현재 입력값으로도 안 맞습니다 — " + " / ".join(_liss[:2]))

            _c7, _c8, _c9 = st.columns(3)
            _card = _c7.text_input("사용카드 끝 4자리", value=_d.get('card_last4', ''),
                                   max_chars=4, key=f"{_k}_card")
            _cash = _c8.text_input("현금영수증 승인번호", value=_d.get('cash_receipt_no', ''),
                                   key=f"{_k}_cash")
            _memo = _c9.text_input("메모", value="", key=f"{_k}_memo")

            _items = _d.get('items') or []
            if _items:
                st.caption(f"📦 품목 {len(_items)}종 (재고 입고 수량으로 저장됩니다)")
                st.dataframe(pd.DataFrame(_items)[['상품번호', '상품명', '수량', '단가', '금액']],
                             use_container_width=True, hide_index=True)
            else:
                st.caption("📦 품목 내역 미인식 — 영수증 합계만 저장됩니다.")

            _b1, _b2 = st.columns([1, 1])
            if _b1.button("💾 매입 장부 저장", type="primary", key=f"{_k}_save"):
                if not _date:
                    st.error("구매일자가 비어 있습니다. 직접 입력해주세요.")
                else:
                    _pid, _new = save_purchase(USERNAME, {
                        'purchase_date': _date, 'purchase_time': _d.get('purchase_time', ''),
                        'store_type': _stype, 'store_name': _sname or _stype,
                        'total_qty': _qty, 'item_kinds': len(_items),
                        'total_amount': _amt, 'discount_amount': _disc,
                        'card_last4': _card, 'cash_receipt_no': _cash,
                        'memo': _memo, 'source': 'photo',
                    }, _items)
                    if _pid:
                        # 품목이 있으면 기존 영수증 흐름(공유DB 매입가 반영)에도 그대로 태운다
                        if _items:
                            st.session_state['receipt_items'] = [
                                {"상품명": it['상품명'], "수량": it['수량'], "단가": it['단가'],
                                 "상품번호": it.get('상품번호', ''), "receipt_date": _date}
                                for it in _items if it.get('단가')]
                            # 사진 영수증도 수익계산이 쓸 수 있게 DB에 남긴다
                            _persist_receipt_items(USERNAME, st.session_state['receipt_items'])
                        _cache[_sig] = {'_done': True, '_name': _d.get('_name', '')}
                        st.success(f"✅ {'저장' if _new else '갱신'} 완료 — {_date} {_stype} "
                                   f"{_amt:,}원 (품목 {len(_items)}종)")
                        st.rerun()
                    else:
                        st.error("저장 실패 — 구매일자를 확인해주세요.")
            if _b2.button("🗑 이 영수증 버리기", key=f"{_k}_drop"):
                _cache[_sig] = {'_done': True, '_name': _d.get('_name', '')}
                st.rerun()

    # ── 매입 장부 (저장된 영수증) + 엑셀 ──
    if compact:
        st.caption("📒 매입 장부·엑셀은 좌측 '영수증' 탭에서 확인할 수 있습니다.")
        return
    st.markdown("##### 📒 매입 영수증 장부")
    _t = datetime.now()
    _lc1, _lc2, _lc3 = st.columns([1, 1, 1.4])
    _lf = _lc1.date_input("조회 시작", value=(_t - timedelta(days=30)).date(),
                          key="rp_ledger_from")
    _lt = _lc2.date_input("조회 종료", value=_t.date(), key="rp_ledger_to")
    _rows = list_purchases(USERNAME, str(_lf), str(_lt))
    if not _rows:
        st.info("해당 기간에 저장된 매입 영수증이 없습니다.")
        return

    _sum_amt = sum(int(r.get('total_amount') or 0) for r in _rows)
    _sum_disc = sum(int(r.get('discount_amount') or 0) for r in _rows)
    _sum_qty = sum(int(r.get('total_qty') or 0) for r in _rows)
    _lc3.metric("기간 합계", f"{_sum_amt:,}원",
                f"영수증 {len(_rows)}장 · 수량 {_sum_qty:,} · 할인 {_sum_disc:,}원")

    _disp = pd.DataFrame([{
        "구매일자": r.get('purchase_date', ''),
        "구매매장명": r.get('store_type', '') or r.get('store_name', ''),
        "구매수량": int(r.get('total_qty') or 0),
        "구매금액": int(r.get('total_amount') or 0),
        "할인금액": int(r.get('discount_amount') or 0),
        "카드 끝4자리": r.get('card_last4', ''),
        "현금영수증 승인번호": r.get('cash_receipt_no', ''),
        "품목종수": int(r.get('item_kinds') or 0),
        "ID": r.get('id'),
    } for r in _rows])
    st.dataframe(_disp, use_container_width=True, hide_index=True)

    _items_all = get_purchase_items_range(USERNAME, str(_lf), str(_lt))
    _dc1, _dc2 = st.columns([1.6, 1])
    _dc1.download_button(
        "📥 매입 장부 엑셀 다운로드 (재고관리용)",
        data=_receipt_ledger_excel(_rows, _items_all),
        file_name=f"매입영수증_{_lf}_{_lt}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="rp_ledger_dl", use_container_width=True)
    _del_id = _dc2.number_input("삭제할 ID", min_value=0, step=1, value=0, key="rp_del_id")
    if _dc2.button("🗑 영수증 삭제", key="rp_del_btn") and _del_id:
        if delete_purchase(USERNAME, int(_del_id)):
            st.success(f"🗑 ID {int(_del_id)} 삭제 완료")
            st.rerun()
        else:
            st.error("해당 ID를 찾을 수 없습니다.")


def render(USERNAME: str, IS_ADMIN: bool, settings: dict, embedded: bool = False, order_date: str = ""):
    """🧾 영수증 등록 탭 렌더링.

    Args:
        embedded: True면 다른 페이지 내부에서 호출됨 → st.header() 생략
        order_date: 수익계산 선택 날짜 (embedded 시 미매칭 교차매칭에 사용)
    """
    def _gs(k, default=""):
        return settings.get(k) or default
    api_id = _gs("api_client_id")
    api_secret = _gs("api_client_secret")
    channel_seller_id = _gs("channel_seller_id")
    excel_pw = _gs("excel_password")

    if not embedded:
        st.header("🧾 코스트코 영수증 등록")
        _render_photo_receipt(USERNAME, settings)
        st.divider()
    else:
        # 수익계산 안에서도 휴대폰 촬영이 되게 (예전엔 PDF만 떠서
        #  폰으로 영수증을 올릴 방법이 없었다). 매입 장부 목록은 생략.
        _render_photo_receipt(USERNAME, settings, compact=True)
        st.divider()

    st.subheader("📄 영수증 PDF 업로드 (여러 파일 동시 등록 가능)")
    receipt_files = st.file_uploader(
        "코스트코 영수증 PDF (여러 파일 선택 가능)",
        type=['pdf'], key="receipt_pdf", accept_multiple_files=True
    )

    if receipt_files:
        all_parsed = []
        fail_files = []   # [(filename, error_msg)]
        for rf in receipt_files:
            items, err = parse_costco_receipt_pdf(rf)
            if items:
                for p in items:
                    p['_file'] = rf.name
                all_parsed.extend(items)
            else:
                fail_files.append((rf.name, err))

        if all_parsed:
            # 같은 상품번호/상품명이면 영수증 날짜가 최신인 항목 우선
            merged = {}
            for p in all_parsed:
                key = p.get('상품번호') or p['상품명']
                existing = merged.get(key)
                if existing is None:
                    merged[key] = p
                else:
                    # receipt_date가 있으면 최신 날짜 우선, 없으면 나중에 파싱된 것 우선
                    if (p.get('receipt_date', '') or '') >= (existing.get('receipt_date', '') or ''):
                        merged[key] = p
            deduped = list(merged.values())

            st.success(f"✅ {len(receipt_files) - len(fail_files)}개 파일 / {len(deduped)}종 상품 인식")
            if fail_files:
                for fname, emsg in fail_files:
                    with st.expander(f"⚠️ 인식 실패: {fname}", expanded=False):
                        st.warning(emsg)

            # 파일별 탭으로 결과 표시
            if len(receipt_files) > 1:
                file_names = sorted(set(p['_file'] for p in all_parsed))
                tabs = st.tabs([f"📄 {n}" for n in file_names] + ["📋 전체 합산"])
                for ti, fname in enumerate(file_names):
                    with tabs[ti]:
                        file_items = [p for p in all_parsed if p['_file'] == fname]
                        st.dataframe(
                            pd.DataFrame(file_items)[['상품번호', '상품명', '수량', '단가']],
                            use_container_width=True, hide_index=True
                        )
                with tabs[-1]:
                    st.dataframe(
                        pd.DataFrame(deduped)[['상품번호', '상품명', '수량', '단가']],
                        use_container_width=True, hide_index=True
                    )
            else:
                st.dataframe(
                    pd.DataFrame(deduped)[['상품번호', '상품명', '수량', '단가']],
                    use_container_width=True, hide_index=True
                )

            st.session_state['receipt_items'] = [
                {"상품명": p['상품명'], "수량": p['수량'], "단가": p['단가'],
                 "상품번호": p.get('상품번호', ''), "receipt_date": p.get('receipt_date', '')}
                for p in deduped
            ]
            # 💾 DB에도 영속화 — 예전엔 세션에만 담아서, 페이지를 다시 들어오면
            #    영수증이 사라지고 수익계산의 영수증 매칭이 통째로 안 됐다.
            _persist_receipt_items(USERNAME, st.session_state['receipt_items'])

            # 🌐 관리자 업로드 → 영수증 가격을 공유DB에 자동 저장 (모든 사용자 수익계산에 반영).
            #    코스트코 상품번호 있는 항목만. 소분(번호 없음)은 각자 DB로 매칭되므로 제외.
            if IS_ADMIN:
                _rc_sig = "|".join(f"{p.get('상품번호','')}:{p.get('단가',0)}" for p in deduped)
                if st.session_state.get('_rcpt_shared_saved_sig') != _rc_sig:
                    _saved_n = 0
                    for p in deduped:
                        _pno = str(p.get('상품번호', '') or '').strip()
                        try:
                            _pr = int(float(p.get('단가') or 0))
                        except (TypeError, ValueError):
                            _pr = 0
                        if _pno and _pr > 0:
                            try:
                                upsert_shared_store_price(
                                    costco_name=p['상품명'], keyword=p['상품명'],
                                    price=_pr, product_no=_pno, updated_by=USERNAME,
                                    receipt_date=p.get('receipt_date', ''),
                                    force_store=IS_ADMIN)
                                _saved_n += 1
                            except Exception:
                                pass
                    st.session_state['_rcpt_shared_saved_sig'] = _rc_sig
                    if _saved_n:
                        st.session_state['_shared_cache_dirty'] = True
                        try:
                            invalidate_data_cache()
                        except Exception:
                            pass
                        st.success(f"🌐 관리자 업로드 → {_saved_n}종 공유DB 자동 저장 "
                                   "(모든 사용자 수익계산에 반영됩니다)")

            # 인식된 영수증 날짜 표시
            _dates = sorted({p.get('receipt_date', '') for p in deduped if p.get('receipt_date')})
            if _dates:
                st.caption(f"📅 영수증 날짜: {', '.join(_dates)}")

            # 영수증 → 네이버 등록상품 자동 매칭 (확실/유력 자동 저장)
            # 매칭 결과 session_state 캐시 — receipt_items 변동 시에만 재계산 (화면 속도 개선)
            _rcm_key = hash(tuple((it.get('상품번호','') or '', str(it.get('단가',0)))
                                  for it in deduped))
            _rcm_state = '_receipt_match_cache'
            if st.session_state.get(_rcm_state + '_key') == _rcm_key:
                _match_result = st.session_state[_rcm_state]
            else:
                _match_result = match_receipt_to_naver_products(USERNAME, deduped, threshold=0.30)
                _auto_certain = [m for m in _match_result['matched'] if m['tier'] in ('확실', '유력')]
                if _auto_certain:
                    apply_receipt_pno_updates(USERNAME, _auto_certain)
                    invalidate_data_cache()
                    _match_result = match_receipt_to_naver_products(USERNAME, deduped, threshold=0.30)
                st.session_state[_rcm_state] = _match_result
                st.session_state[_rcm_state + '_key'] = _rcm_key

            # ── 미매칭 영수증 → 공유DB 등록 (영수증 매칭) ──
            #  퍼지 '주문 교차매칭'(이름 유사도로 주문에 억지 링크 → 오매칭 다발)을 폐기.
            #  영수증의 코스트코번호·상품명·매입가를 공유DB에 그대로 등록 → 주문은 번호로 자동 매칭.
            _unmatched_after = _match_result.get('unmatched_receipt', []) or []
            if embedded and order_date and _unmatched_after:
                st.divider()
                st.subheader("🧾 영수증 매칭 — 미등록 신규 상품 공유DB 등록")
                st.caption(
                    f"네이버 상품 DB에 없는 영수증 항목 **{len(_unmatched_after)}건**을 "
                    "영수증의 **코스트코 상품번호·상품명·매입가** 그대로 공유DB에 등록합니다. "
                    "이름 유사도로 주문에 억지로 링크하지 않으므로 오매칭이 없고, "
                    "이후 주문은 공유DB 상품번호로 자동 매칭됩니다."
                )
                if st.button("🧾 공유DB에 영수증 등록", key="receipt_to_shared_btn", type="primary"):
                    _reg_ok, _reg_skip = 0, []
                    for _um in _unmatched_after:
                        _um_name = (_um.get('상품명') or '').strip()
                        _um_pno  = str(_um.get('상품번호') or '').strip()
                        try:
                            _um_price = int(float(_um.get('단가') or 0))
                        except Exception:
                            _um_price = 0
                        # 공유DB는 코스트코 상품번호 기준 → 번호·단가 없으면 건너뜀
                        if not _um_pno or _um_price <= 0:
                            _reg_skip.append(_um_name or _um_pno or '?')
                            continue
                        upsert_shared_store_price(
                            costco_name=_um_name, keyword=_um_name,
                            price=_um_price, product_no=_um_pno,
                            updated_by=USERNAME,
                            receipt_date=_um.get('receipt_date', ''),
                            force_store=IS_ADMIN,
                        )
                        _reg_ok += 1
                    if _reg_ok:
                        st.session_state['_shared_cache_dirty'] = True
                        try:
                            invalidate_data_cache()
                        except Exception:
                            pass
                        st.success(
                            f"✅ {_reg_ok}종 공유DB 등록 완료 (코스트코번호·상품명·매입가). "
                            "주문은 상품번호로 자동 매칭됩니다."
                        )
                    if _reg_skip:
                        st.warning(
                            f"⚠️ 상품번호/단가 없음 {len(_reg_skip)}건 건너뜀: "
                            f"{', '.join(_reg_skip[:5])}"
                        )
                    st.rerun()

            # ── 가격 변동 감지 ──────────────────────────────────────
            price_changes = detect_price_changes(USERNAME, deduped)

            if price_changes:
                st.divider()
                up_cnt = sum(1 for c in price_changes if c['diff'] > 0)
                dn_cnt = sum(1 for c in price_changes if c['diff'] < 0)
                st.warning(f"⚠️ 가격 변동 감지: 🔺인상 {up_cnt}건 / 🔻인하 {dn_cnt}건")

                # 변동 내역 테이블
                def _fee_str(f):
                    return "무료" if f == 0 else f"{int(f):,}원"

                change_rows = []
                for c in price_changes:
                    arrow = "🔺" if c['diff'] > 0 else "🔻"
                    change_rows.append({
                        "": arrow,
                        "코스트코 상품명": c['costco_name'],
                        "기존 매입가": f"{c['old_cost']:,}원",
                        "새 매입가": f"{c['new_cost']:,}원",
                        "변동": f"{'+' if c['diff']>0 else ''}{c['diff']:,}원 ({'+' if c['diff']>0 else ''}{c['diff_pct']}%)",
                        "고객 배송비": _fee_str(c['shipping_fee']),
                    })
                st.dataframe(pd.DataFrame(change_rows), use_container_width=True, hide_index=True)

                # ── 카카오 알림 (텔레그램은 2026-07 삭제) ──
                kakao_token = _gs('kakao_access_token')

                col_notif, col_save = st.columns([1, 1])
                if col_notif.button("📲 가격변동 알림 카톡 발송", key="send_price_alert", use_container_width=True):
                    alert_msg = build_price_alert_msg(price_changes)
                    sent_ok = False
                    if HAS_NAVER_API and kakao_token:
                        kakao_key = _gs('kakao_api_key')
                        kakao_refresh = _gs('kakao_refresh_token')
                        kakao_secret = _gs('kakao_client_secret')
                        ok, kerr = naver_api.send_kakao(kakao_token, alert_msg, rest_api_key=kakao_key, refresh_token=kakao_refresh, client_secret=kakao_secret)
                        if ok:
                            sent_ok = True
                            if kerr and "__TOKEN_REFRESHED__" in str(kerr):
                                parts = str(kerr).replace("__TOKEN_REFRESHED__", "").split("||")
                                set_setting(USERNAME, 'kakao_access_token', parts[0])
                                if len(parts) > 1: set_setting(USERNAME, 'kakao_refresh_token', parts[1])
                        else:
                            st.error(f"카카오 실패: {kerr}")
                    if sent_ok:
                        # 알림 발송 이력 저장
                        save_price_changes_to_history(USERNAME, price_changes)
                        st.success("✅ 가격 변동 알림 발송 완료!")
                    elif not kakao_token:
                        st.warning("설정에서 카카오톡을 먼저 설정해주세요.")


            else:
                st.info("✅ 가격 변동 없음 — DB에 저장된 가격과 동일합니다.")

            st.divider()
            if st.button("💾 공유 DB 저장 (전체 판매자 매입가 업데이트)", type="primary", key="save_parsed"):
                cnt = 0
                skipped = 0
                for p in deduped:
                    _rd = p.get('receipt_date', '')
                    upsert_shared_store_price(
                        costco_name=p['상품명'],
                        keyword=p['상품명'],
                        price=p['단가'],
                        product_no=p.get('상품번호', ''),
                        updated_by=USERNAME,
                        receipt_date=_rd,
                        force_store=IS_ADMIN,
                    )
                    cnt += 1
                st.session_state['_shared_cache_dirty'] = True; invalidate_data_cache()
                st.success(f"✅ {cnt}종 공유 DB 저장 완료! 모든 판매자에게 반영됩니다.")
        else:
            st.warning("업로드한 파일 모두 인식 실패. 아래에서 직접 입력해주세요.")
            for fname, emsg in fail_files:
                with st.expander(f"⚠️ {fname} — 실패 원인", expanded=True):
                    st.code(emsg, language=None)

    # 새 파일 업로드 없이 기존 로드된 영수증 항목 표시
    elif st.session_state.get('receipt_items'):
        _existing = st.session_state['receipt_items']
        _dates_ex = sorted({it.get('receipt_date', '') for it in _existing if it.get('receipt_date')})
        if _dates_ex:
            st.caption(f"📅 영수증 날짜: {', '.join(_dates_ex)}")
        st.dataframe(
            pd.DataFrame(_existing)[['상품번호', '상품명', '수량', '단가']],
            use_container_width=True, hide_index=True
        )
        # 로드된 영수증 → 공유 DB 저장 (전체 판매자 매입가 반영). 새 업로드 시점(save_parsed)과 동일 동작.
        #   ⚠️ 아래 '정산 데이터 저장'은 수익표 정산 저장(별개). 영수증 가격 반영은 이 버튼으로 한다.
        _rc_c1, _rc_c2 = st.columns([2.7, 1])
        if _rc_c1.button("💾 공유 DB 가격 저장 (전체 판매자 매입가 업데이트)",
                         type="primary", key="save_loaded_receipt"):
            cnt = skipped = 0
            for p in _existing:
                _pno = str(p.get('상품번호', '') or '').strip()
                try:
                    _pr = int(float(p.get('단가') or 0))
                except (TypeError, ValueError):
                    _pr = 0
                if _pno and _pr > 0:
                    upsert_shared_store_price(
                        costco_name=p.get('상품명', ''), keyword=p.get('상품명', ''),
                        price=_pr, product_no=_pno, updated_by=USERNAME,
                        receipt_date=p.get('receipt_date', ''), force_store=IS_ADMIN)
                    cnt += 1
                else:
                    skipped += 1
            st.session_state['_shared_cache_dirty'] = True
            try:
                invalidate_data_cache()
            except Exception:
                pass
            _msg = f"✅ {cnt}종 공유 DB 저장 완료! 모든 판매자에게 반영됩니다."
            if skipped:
                _msg += f" (상품번호/가격 없는 {skipped}건 제외)"
            st.success(_msg)
        if _rc_c2.button("🗑 영수증 초기화", key="clear_receipt_items"):
            st.session_state['receipt_items'] = []
            st.rerun()



    # ═══════════════════════════════════════
    # 탭 3: 수익 계산
    # ═══════════════════════════════════════
