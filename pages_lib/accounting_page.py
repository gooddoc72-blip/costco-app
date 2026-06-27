"""📒 세무회계 (Phase 1) — 손익계산서 + 간편장부.

기존 정산·매입·경비 데이터를 회계 형식으로 집계. 사업자유형(일반/간이/법인)·
장부방식은 설정으로 보기 전환(Phase 2~에서 부가세/복식부기 확장).
"""
import io
from datetime import datetime, date

import streamlit as st
import pandas as pd

from db import (
    get_pl_summary, get_ledger_rows, get_monthly_pl,
    get_setting, set_setting,
)
from utils import fmt


_BIZ_TYPES = ["개인 일반과세자", "개인 간이과세자", "법인"]
_BOOK_TYPES = ["간편장부", "복식부기"]


def _period_range(label: str, y: int, q: int, m: int):
    if label == "연간":
        return f"{y}-01-01", f"{y}-12-31"
    if label == "분기":
        qm = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}[q]
        import calendar as _cal
        last = _cal.monthrange(y, qm[1])[1]
        return f"{y}-{qm[0]:02d}-01", f"{y}-{qm[1]:02d}-{last:02d}"
    import calendar as _cal
    last = _cal.monthrange(y, m)[1]
    return f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last:02d}"


def render(USERNAME: str):
    st.header("📒 세무회계")
    st.caption("정산·매입·경비 데이터를 회계 형식(손익계산서·간편장부)으로 집계합니다. "
               "부가세/복식부기/종소세는 단계적으로 확장됩니다.")

    # ── 사업자 설정 ──
    with st.expander("⚙️ 사업자 설정", expanded=False):
        _c1, _c2 = st.columns(2)
        _biz = _c1.selectbox("사업자 유형", _BIZ_TYPES,
                             index=_BIZ_TYPES.index(get_setting(USERNAME, "biz_type") or _BIZ_TYPES[0])
                             if (get_setting(USERNAME, "biz_type") in _BIZ_TYPES) else 0)
        _book = _c2.selectbox("장부 방식", _BOOK_TYPES,
                              index=_BOOK_TYPES.index(get_setting(USERNAME, "book_type") or _BOOK_TYPES[0])
                              if (get_setting(USERNAME, "book_type") in _BOOK_TYPES) else 0)
        if st.button("💾 사업자 설정 저장"):
            set_setting(USERNAME, "biz_type", _biz)
            set_setting(USERNAME, "book_type", _book)
            st.success("저장되었습니다.")

    # ── 기간 선택 ──
    _today = date.today()
    _p1, _p2, _p3 = st.columns([1.2, 1, 1])
    _ptype = _p1.radio("기간", ["월간", "분기", "연간"], horizontal=True, label_visibility="collapsed")
    _year = _p2.number_input("연도", value=_today.year, min_value=2020, max_value=2100, step=1)
    if _ptype == "월간":
        _mon = _p3.number_input("월", value=_today.month, min_value=1, max_value=12, step=1)
        _q = 1
    elif _ptype == "분기":
        _q = _p3.selectbox("분기", [1, 2, 3, 4], index=(_today.month - 1) // 3)
        _mon = 1
    else:
        _q, _mon = 1, 1
    _d_from, _d_to = _period_range(_ptype, int(_year), int(_q), int(_mon))
    st.caption(f"📅 집계 기간: {_d_from} ~ {_d_to}")

    pl = get_pl_summary(USERNAME, _d_from, _d_to)
    if pl['cnt'] == 0:
        st.info("이 기간에 저장된 정산(수익계산) 데이터가 없습니다. 수익 계산에서 정산 저장을 먼저 하세요.")
        return

    _tab1, _tab2, _tab3, _tab4 = st.tabs(
        ["📊 손익계산서", "📒 간편장부", "🧾 부가가치세", "📚 복식부기"])

    # ── 손익계산서 ──
    with _tab1:
        _ad_month = _d_to[:7]
        _adcost = int(get_setting(USERNAME, f"coupang_adcost_{_ad_month}") or 0)
        _net = pl['net_profit'] - _adcost
        _k1, _k2, _k3, _k4 = st.columns(4)
        _k1.metric("총매출(주문)", f"{fmt(pl['sales'])}원", delta=f"{pl['cnt']}건")
        _k2.metric("정산수령(실매출)", f"{fmt(pl['settle'])}원")
        _k3.metric("매출원가", f"{fmt(pl['cost'])}원")
        _k4.metric("💵 순이익", f"{fmt(_net)}원")

        _rows = [
            ("Ⅰ. 총매출액(주문금액)", pl['sales'], "고객 결제 상품금액(수수료 차감 전)"),
            ("　(−) 플랫폼 지급수수료", -pl['commission'], "총매출 − 정산수령(추정)"),
            ("　(=) 정산수령(실매출)", pl['settle'], "수수료 차감 후 실수령"),
            ("　(+) 배송비 수령", pl['ship'], "고객 결제 배송비(전액 정산)"),
            ("Ⅱ. 매출원가(매입)", -pl['cost'], "매입가"),
            ("Ⅲ. 운반비(택배원가)", -pl['delivery'], ""),
            ("Ⅳ. 포장비", -pl['box'], ""),
            ("Ⅴ. 광고선전비", -_adcost, f"{_ad_month} 광고비(수동)"),
            ("Ⅵ. 순이익", _net, "정산수령+배송비−원가−운반−포장−광고"),
        ]
        _html = '<table style="width:100%;border-collapse:collapse;font-size:14px">'
        for _name, _amt, _memo in _rows:
            _bold = _name.startswith(("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ"))
            _col = "#1D9E75" if _amt >= 0 else "#E74C3C"
            _html += (
                f'<tr style="border-bottom:1px solid #eee">'
                f'<td style="padding:7px 10px;{"font-weight:700" if _bold else "color:#666"}">{_name}</td>'
                f'<td style="padding:7px 10px;text-align:right;font-weight:{"700" if _bold else "500"};color:{_col}">{_amt:,}원</td>'
                f'<td style="padding:7px 10px;font-size:12px;color:#999">{_memo}</td></tr>'
            )
        _html += '</table>'
        st.markdown(_html, unsafe_allow_html=True)
        st.caption("✅ 순이익은 홈 달력/수익계산과 동일 기준(원가 0건 제외 재계산)입니다. "
                   "⚠️ 부가세 과세/면세 구분·매입세액공제는 Phase 2에서 반영. "
                   "원가>판매가인 행은 제품가격 DB 단가 오류일 수 있어 점검이 필요합니다.")

        # 월별 추이
        _my = get_monthly_pl(USERNAME, f"{int(_year)}-01-01", f"{int(_year)}-12-31")
        if _my:
            st.markdown("##### 월별 손익 추이 (" + str(int(_year)) + ")")
            _mdf = pd.DataFrame(_my)
            _mdf.columns = ['월', '매출', '매출원가', '순이익', '건수']
            st.dataframe(_mdf, use_container_width=True, hide_index=True)

    # ── 간편장부 ──
    with _tab2:
        _ledger = get_ledger_rows(USERNAME, _d_from, _d_to)
        st.caption(f"국세청 간편장부 형식 — {len(_ledger)}건 (수입=매출, 비용=원가+운반+포장)")
        _ldf = pd.DataFrame([{
            '일자': r['settlement_date'], '거래내용': (r.get('product_name') or '')[:24],
            '거래처/수취인': r.get('recipient', ''),
            '수입(매출)': int(r.get('order_amount') or 0),
            '비용(매입원가)': int(r.get('cost_price') or 0),
            '운반비': int(r.get('delivery_cost') or 0),
            '포장비': int(r.get('box_cost') or 0),
            '순이익': int(r.get('profit') or 0),
            '상품주문번호': r.get('order_no', ''),
        } for r in _ledger])
        st.dataframe(_ldf, use_container_width=True, hide_index=True)

        _LEDGER_DONE = True

    # ── 부가가치세 집계 ──
    with _tab3:
        _ad_month3 = _d_to[:7]
        _adcost3 = int(get_setting(USERNAME, f"coupang_adcost_{_ad_month3}") or 0)
        st.caption("매출세액 − 매입세액 = 납부세액. 플랫폼 수수료·택배비·광고비는 세금계산서 발행분(매입세액공제). "
                   "면세 매입은 공제 불가 → 면세 매입액을 입력해 과세분만 공제합니다.")
        _vat_exempt = int(st.number_input(
            "면세 매입액 (코스트코 면세품 등, 수동)", value=0, min_value=0, step=10000,
            key=f"vat_exempt_{_d_from}",
            help="영수증 과세/면세 자동구분은 파서 보강 후 적용. 현재는 면세 매입액을 직접 입력."))
        _taxable_purchase = max(0, pl['cost'] - _vat_exempt)  # 과세 매입(매입가)
        # 매출세액 (과세매출 가정 — 면세매출 구분은 추후)
        _vat_sales = round(pl['sales'] * 10 / 110)
        # 매입세액 (과세분 × 10/110)
        _vat_purchase = round(_taxable_purchase * 10 / 110)
        _vat_commission = round(pl['commission'] * 10 / 110)   # 플랫폼 수수료(세금계산서)
        _vat_delivery = round(pl['delivery'] * 10 / 110)        # 택배비(세금계산서/카드)
        _vat_ad = round(_adcost3 * 10 / 110)                    # 광고비
        _vat_in = _vat_purchase + _vat_commission + _vat_delivery + _vat_ad
        _vat_pay = _vat_sales - _vat_in
        _v1, _v2, _v3 = st.columns(3)
        _v1.metric("매출세액", f"{fmt(_vat_sales)}원")
        _v2.metric("매입세액(공제)", f"{fmt(_vat_in)}원")
        _v3.metric("💰 납부(환급)세액", f"{fmt(_vat_pay)}원",
                   delta=("납부" if _vat_pay >= 0 else "환급"))
        _vat_rows = [
            ("매출세액 (과세매출×10/110)", _vat_sales, f"과세매출 {fmt(pl['sales'])}"),
            ("(−) 매입세액 — 상품매입(과세)", -_vat_purchase, f"과세매입 {fmt(_taxable_purchase)}"),
            ("(−) 매입세액 — 플랫폼 수수료", -_vat_commission, f"수수료 {fmt(pl['commission'])} (네이버/쿠팡 세금계산서)"),
            ("(−) 매입세액 — 택배비", -_vat_delivery, f"택배 {fmt(pl['delivery'])}"),
            ("(−) 매입세액 — 광고비", -_vat_ad, f"광고 {fmt(_adcost3)}"),
            ("= 납부(환급)세액", _vat_pay, ""),
        ]
        _vhtml = '<table style="width:100%;border-collapse:collapse;font-size:14px">'
        for _n, _a, _m in _vat_rows:
            _b = _n.startswith(("매출", "= 납부"))
            _c = "#1D9E75" if _a >= 0 else "#E74C3C"
            _vhtml += (f'<tr style="border-bottom:1px solid #eee">'
                       f'<td style="padding:7px 10px;{"font-weight:700" if _b else "color:#666"}">{_n}</td>'
                       f'<td style="padding:7px 10px;text-align:right;font-weight:{"700" if _b else "500"};color:{_c}">{_a:,}원</td>'
                       f'<td style="padding:7px 10px;font-size:12px;color:#999">{_m}</td></tr>')
        _vhtml += '</table>'
        st.markdown(_vhtml, unsafe_allow_html=True)
        st.caption(f"⚠️ 추정치입니다 — 모든 매출을 과세로 가정(면세매출 구분 추후), 면세매입은 수동입력. "
                   f"사업자유형({_biz})에 따라 신고주기 다름(일반=분기, 간이=연). "
                   f"정확한 신고는 세무사/홈택스 확인 필요.")

    # ── 간편장부 엑셀 (탭2) ──
    with _tab2:
        if not _ldf.empty:
            _buf = io.BytesIO()
            with pd.ExcelWriter(_buf, engine='xlsxwriter') as _w:
                _ldf.to_excel(_w, index=False, sheet_name='간편장부')
            st.download_button("📥 간편장부 엑셀 다운로드 (세무사 제출용)",
                               data=_buf.getvalue(),
                               file_name=f"간편장부_{_d_from}_{_d_to}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ── 복식부기 (합계잔액시산표 + 손익계산서 + 집계 분개장) ──
    with _tab4:
        _adm = _d_to[:7]
        _adc = int(get_setting(USERNAME, f"coupang_adcost_{_adm}") or 0)
        _rev = pl['sales'] + pl['ship']          # 상품매출(총매출+배송비수령)
        _bank = pl['settle'] + pl['ship']         # 보통예금 실입금
        _comm, _cost, _dlv, _box = pl['commission'], pl['cost'], pl['delivery'], pl['box']
        _cash_out = _cost + _dlv + _box + _adc    # 현금 지출(비용 자산감소)
        _profit = _bank - _cash_out               # 당기순이익

        st.markdown("##### 합계잔액시산표")
        _tb = [
            # (계정, 구분, 차변, 대변)
            ("보통예금", "자산", _bank, 0),
            ("현금(지출)", "자산", 0, _cash_out),
            ("상품매출", "수익", 0, _rev),
            ("상품매출원가", "비용", _cost, 0),
            ("지급수수료", "비용", _comm, 0),
            ("운반비", "비용", _dlv, 0),
            ("포장비", "비용", _box, 0),
            ("광고선전비", "비용", _adc, 0),
        ]
        _tot_d = sum(x[2] for x in _tb)
        _tot_c = sum(x[3] for x in _tb)
        _thtml = ('<table style="width:100%;border-collapse:collapse;font-size:13px">'
                  '<tr style="background:#f8f9fa;font-weight:700">'
                  '<td style="padding:6px 10px">계정과목</td><td style="padding:6px 10px">구분</td>'
                  '<td style="padding:6px 10px;text-align:right">차변</td>'
                  '<td style="padding:6px 10px;text-align:right">대변</td></tr>')
        for _acc, _cls, _dr, _cr in _tb:
            _thtml += (f'<tr style="border-bottom:1px solid #eee">'
                       f'<td style="padding:6px 10px">{_acc}</td>'
                       f'<td style="padding:6px 10px;color:#888">{_cls}</td>'
                       f'<td style="padding:6px 10px;text-align:right">{_dr:,}</td>'
                       f'<td style="padding:6px 10px;text-align:right">{_cr:,}</td></tr>')
        _bal = "✅ 일치" if _tot_d == _tot_c else f"⚠️ 불일치({_tot_d-_tot_c:,})"
        _thtml += (f'<tr style="font-weight:700;border-top:2px solid #333">'
                   f'<td style="padding:6px 10px" colspan=2>합계 ({_bal})</td>'
                   f'<td style="padding:6px 10px;text-align:right">{_tot_d:,}</td>'
                   f'<td style="padding:6px 10px;text-align:right">{_tot_c:,}</td></tr></table>')
        st.markdown(_thtml, unsafe_allow_html=True)

        st.markdown("##### 손익계산서 (복식)")
        _pl_lines = [
            ("매출액 (상품매출)", _rev),
            ("(−) 매출원가", -_cost),
            ("(−) 지급수수료", -_comm),
            ("(−) 운반비", -_dlv),
            ("(−) 포장비", -_box),
            ("(−) 광고선전비", -_adc),
            ("당기순이익", _profit),
        ]
        _ph = '<table style="width:100%;border-collapse:collapse;font-size:14px">'
        for _n, _a in _pl_lines:
            _b = _n in ("매출액 (상품매출)", "당기순이익")
            _c = "#1D9E75" if _a >= 0 else "#E74C3C"
            _ph += (f'<tr style="border-bottom:1px solid #eee"><td style="padding:6px 10px;'
                    f'{"font-weight:700" if _b else "color:#666"}">{_n}</td>'
                    f'<td style="padding:6px 10px;text-align:right;font-weight:{"700" if _b else "500"};color:{_c}">{_a:,}원</td></tr>')
        _ph += '</table>'
        st.markdown(_ph, unsafe_allow_html=True)

        st.markdown("##### 집계 분개 (기간 요약 — 세무사 참고용)")
        _je = pd.DataFrame([
            {'차변계정': '보통예금', '차변금액': _bank, '대변계정': '상품매출', '대변금액': _rev,
             '적요': '기간 정산입금(매출)'},
            {'차변계정': '상품매출원가', '차변금액': _cost, '대변계정': '현금', '대변금액': _cost,
             '적요': '상품 매입원가'},
            {'차변계정': '지급수수료', '차변금액': _comm, '대변계정': '상품매출', '대변금액': _comm,
             '적요': '플랫폼 수수료(세금계산서)'},
            {'차변계정': '운반비', '차변금액': _dlv, '대변계정': '현금', '대변금액': _dlv, '적요': '택배비'},
            {'차변계정': '포장비', '차변금액': _box, '대변계정': '현금', '대변금액': _box, '적요': '포장비'},
            {'차변계정': '광고선전비', '차변금액': _adc, '대변계정': '현금', '대변금액': _adc, '적요': '광고비'},
        ])
        st.dataframe(_je, use_container_width=True, hide_index=True)
        st.caption("⚠️ 자산/부채/자본(자본금·재고·차입 등)은 별도 입력이 필요해 재무상태표는 추후 제공합니다. "
                   "현재는 거래 기반 손익·시산표·분개입니다. 복식부기 의무자/법인은 세무사 확인 권장.")
