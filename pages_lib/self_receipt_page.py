"""🧾 내 영수증 정산 (직접구매자) — 자기 영수증을 올려 주문 구입가·재고를 직접 정리한다.

직접구매 계정은 자기 돈으로 매장에서 산다. 그런데 영수증 매칭은 관리자 화면
(영수증 정산)에만 있어서, 구입가·재고를 맞추려면 관리자가 대신 올려 줘야 했다.
관리자가 계정별로 열어 주면(관리자 › 회원 관리 › '🧾 영수증 직접 정산 허용')
이 화면이 '운영' 메뉴에 보인다.

판독·매칭 엔진은 관리자 화면과 같다. **저장소만 따로다** — 관리자 영수증과
섞이면 그 물건이 관리자 창고 재고로 잡힌다(db_self_receipt 머리말 참고).
  판독    services.parse_costco_receipt_pdf / ai_service.parse_receipt_photo
  저장    db_self_receipt.save_items          — self_receipt_items (관리자 receipt_items와 별개)
  가격    db_self_receipt.update_latest_prices — 더 최근 영수증일 때만 공유 매장가 갱신
  매칭    receipt_settle.allocate_*(users=[나]) — 내 주문에만 붙는다
  구입가  settle_core.finalize, via='self'     — settle_item source='self', 청구서 없음
  재고    add_lot_units(owner=나)              — memo '직접구매영수증 …'
source='self'와 memo '직접구매영수증'은 공용 재고 계산(build_stock_pool 등)이
빼고 센다. 관리자 영수증 입고에 없는 물건을 관리자 재고에서 빼면 안 되기 때문이다.
"""
import hashlib
from datetime import date, datetime

import streamlit as st
import pandas as pd

import receipt_settle as _rs
import settle_core as _sc
import db_settle as _ds
import db_self_receipt as _sr
from db import get_setting
from utils import fmt

#: 관리자가 켜 주는 사용자 설정 키. 직접구매(self_purchase) 계정일 때만 의미가 있다.
SELF_RECEIPT_KEY = 'self_receipt_open'


def is_open(username):
    """이 사용자에게 화면을 보여 줄지. 직접구매 + 관리자 허용 둘 다 켜져야 한다.

    구매대행 계정에 열면 관리자가 청구할 물건값을 사용자가 스스로 바꾸게 된다
    (구입가 = 청구 근거). 그래서 직접구매 계정으로 묶는다.
    """
    try:
        return (str(get_setting(username, 'self_purchase') or '') == '1'
                and str(get_setting(username, SELF_RECEIPT_KEY) or '') == '1')
    except Exception:
        return False


def _n(v):
    return str(v or '').strip()


def _i(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


# ── 1) 판독 ─────────────────────────────────────────────────
def _parse_uploads(pdfs, photos, settings):
    """PDF·사진 → 영수증 줄 목록. 반환: (items, fails)

    관리자 화면과 같은 판독 경로다. 글자 없는 스캔 PDF는 그림으로 바꿔 AI로 읽는다.
    """
    from services import parse_costco_receipt_pdf, render_pdf_to_images
    import ai_service as _ais
    from pages_lib.receipt_page import _image_for_ai

    _ak, _gk = _ais.get_ai_keys(settings)
    items, fails = [], []

    def _from_ai(data):
        _rd = data.get('purchase_date', '') or ''
        for _it in (data.get('items') or []):
            if not _n(_it.get('상품명')):
                continue
            items.append({'상품번호': _n(_it.get('상품번호')), '상품명': _it.get('상품명', ''),
                          '수량': _i(_it.get('수량')) or 1, '단가': _i(_it.get('단가')),
                          '금액': _i(_it.get('금액')), '할인': _i(_it.get('할인')),
                          'receipt_date': _rd})

    for f in (pdfs or []):
        try:
            _got, err = parse_costco_receipt_pdf(f)
        except Exception as e:
            _got, err = None, f"파싱 예외: {e}"
        if _got:
            items.extend(_got)
            continue
        if not (_ak or _gk):
            fails.append((f.name, (err or '인식 실패') + " · AI 키가 없어 이미지 판독 불가"))
            continue
        _imgs, _rerr = render_pdf_to_images(f)
        if _rerr or not _imgs:
            fails.append((f.name, f"{err or '인식 실패'} · 이미지 변환 실패({_rerr})"))
            continue
        for _ib, _mt in _imgs:
            _d, _de = _ais.parse_receipt_photo(_ak, _ib, _mt, gemini_key=_gk)
            if _d and not _de:
                _from_ai(_d)
                if not _d.get('_verified', True):
                    fails.append((f.name, "금액·수량 자가검증 불일치 — 표에서 값을 확인하세요"))

    if photos and not (_ak or _gk):
        fails.append(('사진', 'AI 키가 없어 사진 판독을 할 수 없습니다 — 관리자에게 문의하세요'))
    elif photos:
        for _pf in photos:
            _img, _ierr = _image_for_ai(_pf)
            if _ierr:
                fails.append((_pf.name, _ierr))
                continue
            _d, _de = _ais.parse_receipt_photo(_ak, _img[0], _img[1], gemini_key=_gk)
            if _de or not _d:
                fails.append((_pf.name, _de or '판독 실패'))
                continue
            _from_ai(_d)
            if not _d.get('_verified', True):
                fails.append((_pf.name, "금액·수량 자가검증 불일치 — 표에서 값을 확인하세요"))

    items, _snap = _rs.snap_items_to_catalog(items)
    # 같은 상품이 두 줄로 찍히는 일이 흔하다 — 관리자 화면과 같은 규칙으로 합친다
    from pages_lib.receipt_settle_page import _merge_receipt_lines
    return list(_merge_receipt_lines(items).values()), fails


# ── 화면 ────────────────────────────────────────────────────
def render(USERNAME, IS_ADMIN, settings):
    st.title("🧾 내 영수증 정산")
    if not is_open(USERNAME):
        st.error("이 화면은 관리자가 허용한 **직접구매 계정**만 쓸 수 있습니다. "
                 "필요하면 관리자에게 요청하세요.")
        return

    st.caption("매장에서 직접 산 영수증을 올리면 **내 주문의 구입가**를 영수증 실단가로 "
               "맞추고, 주문에 안 붙은 **남은 물건은 내 재고**로 넣을 수 있습니다. "
               "직접구매 계정이라 청구서는 만들어지지 않습니다.")

    # ── 1. 업로드 ──
    st.subheader("1. 영수증 올리기")
    _pdfs = st.file_uploader("코스트코 영수증 PDF (여러 개 가능)", type=['pdf'],
                             key="sr_pdf", accept_multiple_files=True)
    _photos = st.file_uploader("📷 또는 영수증 사진 (여러 장 가능)",
                               type=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'],
                               key="sr_photo", accept_multiple_files=True)
    try:
        from pages_lib.receipt_page import inject_native_camera
        inject_native_camera("영수증 사진")
    except Exception:
        pass
    _fkey = (tuple(sorted((f.name, getattr(f, 'size', 0)) for f in (_pdfs or []))),
             tuple(sorted((f.name, getattr(f, 'size', 0)) for f in (_photos or []))))
    if (_pdfs or _photos) and st.session_state.get('_sr_fkey') != _fkey:
        with st.spinner("영수증 판독 중..."):
            _items, _fails = _parse_uploads(_pdfs, _photos, settings)
        st.session_state['sr_items'] = _items
        st.session_state['_sr_fails'] = _fails
        st.session_state['_sr_fkey'] = _fkey
        st.session_state['_sr_unsaved'] = True
        st.session_state.pop('sr_alloc', None)
        _rds = sorted({_n(x.get('receipt_date')) for x in _items if _n(x.get('receipt_date'))})
        if _rds:
            st.session_state['sr_date_val'] = _rds[-1]
            # key 있는 date_input은 value를 바꿔도 옛 값을 쥐고 있다 — 위젯 생성 전에 비운다
            st.session_state.pop('sr_date', None); st.session_state.pop('sr_odate', None)
    for _fn, _em in st.session_state.get('_sr_fails') or []:
        st.warning(f"⚠️ {_fn} — {_em}")

    # 저장해 둔 영수증 다시 열기 — 내가 올린 것만 보인다
    if not st.session_state.get('sr_items'):
        try:
            _mine = _sr.dates(USERNAME, limit=30)
        except Exception:
            _mine = []
        if _mine:
            _opts = [f"{d} ({c}종)" for d, c in _mine]
            _c1, _c2 = st.columns([2, 1])
            _pick = _c1.selectbox("저장된 내 영수증 불러오기", ['(선택)'] + _opts, key="sr_load")
            _c2.write("")
            if _pick != '(선택)' and _c2.button("📂 불러오기", key="sr_load_btn",
                                               use_container_width=True):
                _d = _mine[_opts.index(_pick)][0]
                st.session_state['sr_items'] = _sr.items_by_date(USERNAME, _d)
                st.session_state['sr_date_val'] = _d
                st.session_state.pop('sr_date', None); st.session_state.pop('sr_odate', None)
                st.session_state['_sr_unsaved'] = False
                st.session_state.pop('sr_alloc', None)
                st.rerun()

    # ── 2. 품목 확인·저장 ──
    st.subheader("2. 품목 확인 · 저장")
    _seed = st.session_state.get('sr_items') or []
    try:
        _dv = datetime.strptime(st.session_state.get('sr_date_val') or '', "%Y-%m-%d").date()
    except ValueError:
        _dv = date.today()
    _rdate = str(st.date_input("영수증 날짜 (구매일)", value=_dv, key="sr_date"))
    _rows0 = [{'상품번호': _n(p.get('상품번호')), '상품명': _n(p.get('상품명')),
               '수량': _i(p.get('수량')) or 1,
               '정가': _i(p.get('정가단가') or p.get('단가')),
               '할인': _i(p.get('할인')), '단가': _i(p.get('단가'))}
              for p in _seed] or [{'상품번호': '', '상품명': '', '수량': 1,
                                   '정가': 0, '할인': 0, '단가': 0}]
    _ed = st.data_editor(
        pd.DataFrame(_rows0), num_rows='dynamic', use_container_width=True,
        key=f"sr_editor_{hashlib.md5(str(_rows0).encode()).hexdigest()[:8]}",
        column_config={
            '상품번호': st.column_config.TextColumn('코스트코 상품번호'),
            '상품명': st.column_config.TextColumn('상품명'),
            '수량': st.column_config.NumberColumn('수량', min_value=1, step=1),
            '정가': st.column_config.NumberColumn('정가(원)', min_value=0, step=100),
            '할인': st.column_config.NumberColumn('할인(원)', min_value=0, step=100,
                                                help='그 품목 쿠폰 합계(수량 전체)'),
            '단가': st.column_config.NumberColumn('실단가(원)', min_value=0, step=100,
                                                help='(정가×수량 − 할인) ÷ 수량'),
        })
    items = []
    for r in _ed.to_dict('records'):
        cno, up = _n(r.get('상품번호')), _i(r.get('단가'))
        _lp, _dc, _qy = _i(r.get('정가')), _i(r.get('할인')), max(1, _i(r.get('수량')) or 1)
        # 정가·할인을 고쳤으면 실단가를 다시 계산한다(관리자 화면과 같은 규칙)
        if _lp > 0 and (_dc or _lp != up):
            _calc = max(0, _lp * _qy - _dc) // _qy
            if _calc > 0:
                up = _calc
        if cno and up > 0:
            items.append({'상품번호': cno, '상품명': _n(r.get('상품명')), '수량': _qy,
                          '단가': up, '정가단가': _lp or up, '할인': _dc,
                          'receipt_date': _rdate})
    if not items:
        st.info("표에 **코스트코 상품번호 + 실단가(>0)** 가 있는 줄이 1개 이상 있어야 합니다.")
        return

    from pages_lib.receipt_settle_page import _receipt_outliers
    _odd = _receipt_outliers(items)
    if _odd:
        st.error("🚨 판독이 의심스러운 줄 — 저장 전에 확인하세요.\n\n" + "\n".join(
            f"- **{o['상품명']}** 수량 {o['수량']} · {fmt(o['단가'])}원 ({o['이유']})"
            for o in _odd[:6]))
    _pay = sum(x['단가'] * x['수량'] for x in items)
    st.markdown(f"🧾 **{len(items)}종 · 총수량 {sum(x['수량'] for x in items)}개 · "
                f"실지불 {fmt(_pay)}원**")
    if st.session_state.get('_sr_unsaved'):
        st.warning("⚠️ 아직 저장되지 않았습니다. 표를 확인한 뒤 **영수증 저장**을 누르세요.")
    if st.button("💾 영수증 저장", key="sr_save",
                 type="primary" if st.session_state.get('_sr_unsaved') else "secondary"):
        _s, _rm = _sr.save_items(USERNAME, items)
        _pr = _sr.update_latest_prices(USERNAME, items)
        st.session_state['sr_items'] = list(items)
        st.session_state['sr_date_val'] = _rdate
        st.session_state['_sr_unsaved'] = False
        st.session_state.pop('sr_alloc', None)
        st.success(f"💾 {_rdate} 영수증 {_s}종 저장"
                   + (f" (빠진 옛 줄 {_rm} 삭제)" if _rm else "")
                   + f"\n\n💲 가격DB: 변경 {_pr['changed']} · 신규 {_pr['new']} · "
                     f"동일 {_pr['same']}"
                   + (f" · 더 최근 가격이 있어 유지 {_pr['older']}" if _pr['older'] else ""))
    st.caption("저장은 **그 날짜의 내 영수증을 통째로 교체**합니다(관리자 영수증과는 따로 "
               "보관). 하루에 여러 장이면 한 번에 같이 올리세요. 가격DB 매장가는 "
               "**이 영수증이 더 최근일 때만** 정가로 갱신됩니다.")
    if st.session_state.get('_sr_unsaved'):
        return   # 저장 안 한 값으로 구입가·재고를 바꾸면 영수증 기록과 어긋난다

    # ── 3. 내 주문 구입가 반영 ──
    st.divider()
    st.subheader("3. 내 주문 구입가 반영")
    _basis = st.radio("어떤 주문에 붙일까요?",
                      ["발송(송장등록)한 날 기준", "주문 수집일 기준"],
                      horizontal=True, key="sr_basis",
                      help="송장을 등록한 주문이면 발송일 기준이 정확합니다. "
                           "아직 송장 전이면 주문 수집일 기준을 고르세요.")
    _odate = str(st.date_input("주문 날짜", value=datetime.strptime(_rdate, "%Y-%m-%d").date(),
                               key="sr_odate"))
    if st.button("🔍 매칭 미리보기", key="sr_preview"):
        # 이미 정산된 주문은 뺀다. 단 '이 날짜에 내가 내 영수증으로 한 것'은 다시
        # 돌릴 수 있게 남긴다. 관리자가 대리구매로 정산한 주문은 날짜와 무관하게
        # 빼야 한다 — 같은 주문을 여기서 덮으면 대리구매 청구액이 조용히 줄어든다.
        _c = _ds._conn(); _ds.ensure(_c)
        try:
            _excl = {(str(r[0]), str(r[1])) for r in _c.execute(
                "SELECT username, order_no FROM settle_item WHERE order_no<>'' "
                "AND NOT (settle_date=? AND username=? AND COALESCE(source,'')='self')",
                (_odate, USERNAME))}
        finally:
            _c.close()
        if _basis.startswith("발송"):
            _al = _rs.allocate_dispatched_to_receipt(items, _odate, users=[USERNAME],
                                                     exclude_orders=_excl)
        else:
            _al = _rs.allocate_receipt_to_orders(items, _odate, _odate, users=[USERNAME],
                                                 exclude_orders=_excl)
        # 엔진은 users로 걸러 주지만, 남의 주문이 한 줄이라도 섞이면 남의 구입가를
        # 바꾸게 된다. 화면에서 한 번 더 막는다.
        _al['rows'] = [r for r in (_al.get('rows') or []) if r.get('username') == USERNAME]
        # 정산 기록을 source='self'로 남긴다 — 공용 재고 계산이 이걸 보고 뺀다
        for r in _al['rows']:
            r['via'] = 'self'
        _al['unmatched_orders'] = [r for r in (_al.get('unmatched_orders') or [])
                                   if r.get('username') == USERNAME]
        st.session_state['sr_alloc'] = {'date': _odate, 'alloc': _al}

    _pv = st.session_state.get('sr_alloc')
    _rows = []
    if _pv:
        _al = _pv['alloc']
        _rows = _al.get('rows') or []
        if _rows:
            st.dataframe(pd.DataFrame([{
                '주문번호': r['order_no'], '상품': _n(r.get('product_name'))[:30],
                '코스트코번호': r.get('costco_no'), '수량': r.get('qty'),
                '기존 구입가': fmt(_i(r.get('prev_cost'))), '새 구입가': fmt(_i(r.get('amount'))),
            } for r in _rows]), use_container_width=True, hide_index=True)
        else:
            st.info("영수증에 붙는 내 주문이 없습니다. 날짜나 기준을 바꿔 보세요.")
        _uo = _al.get('unmatched_orders') or []
        if _uo:
            with st.expander(f"영수증에서 못 찾은 내 주문 {len(_uo)}건"):
                st.dataframe(pd.DataFrame([{'주문번호': o['order_no'],
                                            '상품': _n(o.get('product_name'))[:30],
                                            '수량': o.get('qty')} for o in _uo]),
                             use_container_width=True, hide_index=True)
        if _rows and st.button(f"✅ 구입가 {len(_rows)}건 반영", key="sr_apply", type="primary"):
            _res = _sc.finalize(_pv['date'], _rows, created_by=USERNAME)
            st.success(f"✅ 주문 {_res.get('applied', 0)}건 구입가 반영 · 정산기록 "
                       f"{_res.get('saved', 0)}줄 (수익계산에 바로 반영됩니다)")

    # ── 4. 남은 물건 → 내 재고 ──
    st.divider()
    st.subheader("4. 남은 물건 내 재고로 넣기")
    st.caption("영수증에서 주문에 안 붙은 수량입니다. 실제로 가지고 있는 것만 체크해 넣으세요 "
               "— 넣은 재고는 이후 내 판매에서 먼저 차감됩니다.")
    try:
        _left = _sc.leftovers(items, _rows, _rdate, users=[USERNAME])
    except Exception as e:
        _left = []
        st.caption(f"⚠️ 잔량 계산 실패: {e}")
    # 같은 날 이미 넣은 만큼은 뺀다 — 버튼을 두 번 누르면 재고가 두 배가 된다
    _have = {}
    try:
        from db_inventory import find_lots
        for _l in find_lots(date_from=_rdate, date_to=_rdate, owner=USERNAME) or []:
            if str(_l.get('memo') or '').startswith(_sr.LOT_MEMO_PREFIX):
                _k = _n(_l.get('product_no'))
                _have[_k] = _have.get(_k, 0) + _i(_l.get('qty_in'))
    except Exception:
        pass
    _cand = []
    for l in _left:
        _u = _i(l.get('units_left')) - _have.get(_n(l.get('costco_no')), 0)
        if _u > 0:
            _cand.append({**l, 'units_left': _u})
    if not _cand:
        st.caption("넣을 잔량이 없습니다" + (" (이미 넣은 만큼은 뺐습니다)." if _have else "."))
    else:
        _df = pd.DataFrame([{'입고': True, '코스트코번호': c['costco_no'],
                             '상품': _n(c.get('name'))[:30], '소분': c.get('split_qty'),
                             '남은 수량(소분)': c['units_left'],
                             '단가': fmt(_i(c.get('unit_price')))} for c in _cand])
        _pk = st.data_editor(_df, hide_index=True, use_container_width=True, key="sr_left",
                             disabled=['코스트코번호', '상품', '소분', '단가'],
                             column_config={'남은 수량(소분)': st.column_config.NumberColumn(
                                 min_value=0, step=1)})
        _picks = []
        for c, r in zip(_cand, _pk.to_dict('records')):
            _q = min(_i(r.get('남은 수량(소분)')), c['units_left'])
            if r.get('입고') and _q > 0:
                _picks.append({**c, 'units_left': _q, 'owner': USERNAME})
        if _picks and st.button(f"📦 {len(_picks)}종 내 재고로 넣기", key="sr_recv"):
            # settle_core.receive_leftovers는 쓰지 않는다 — memo가 '영수증정산'으로
            # 고정이라 공용 재고 계산이 이 lot을 관리자 영수증 배정분으로 빼 버린다.
            from db_inventory import add_lot_units
            _ok, _bad = 0, []
            for p in _picks:
                try:
                    if add_lot_units(
                            product_no=p['costco_no'], product_name=_n(p.get('name')),
                            owner=USERNAME, pack_unit_cost=_i(p.get('unit_price')),
                            qty_units=_i(p['units_left']),
                            split_qty=max(1, _i(p.get('split_qty')) or 1),
                            received_at=_rdate,
                            memo=f"{_sr.LOT_MEMO_PREFIX} {_rdate} · 잔량"):
                        _ok += 1
                except Exception as e:
                    _bad.append(f"{_n(p.get('name'))[:20]} — {str(e)[:60]}")
            st.toast(f"📦 {_ok}종 내 재고로 입고")
            for _m in _bad:
                st.warning(f"입고 실패: {_m}")
            if not _bad:
                st.rerun()

    # ── 되돌리기 — 잘못 넣은 재고 ──
    try:
        from db_inventory import find_lots, delete_lots
        _mylots = [l for l in (find_lots(date_from=_rdate, date_to=_rdate, owner=USERNAME) or [])
                   if str(l.get('memo') or '').startswith(_sr.LOT_MEMO_PREFIX)]
    except Exception:
        _mylots = []
    if _mylots:
        with st.expander(f"↩️ {_rdate}에 넣은 내 재고 {len(_mylots)}건 — 잘못 넣었으면 취소"):
            for l in _mylots:
                _c1, _c2 = st.columns([4, 1])
                _c1.caption(f"{_n(l.get('product_name'))[:30]} · {_i(l.get('qty_in'))}개 입고 "
                            f"· 남음 {_i(l.get('qty_left'))}"
                            + (" · 판매에 사용됨(취소 불가)" if _i(l.get('used')) else ""))
                if not _i(l.get('used')) and _c2.button("취소", key=f"sr_undo_{l['id']}"):
                    delete_lots([l['id']])
                    st.rerun()
