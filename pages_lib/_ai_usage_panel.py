"""AI 사용량·비용 집계 패널 (관리자).

관리자 공용 AI 키로 나간 Claude·Gemini 호출을 사용자별로 보여 준다.
단가·환율·마진을 여기서 고칠 수 있게 둔 이유: 모델 가격과 환율은 바뀌는데
그때마다 코드를 고쳐 배포하면 이미 늦는다. 설정이 항상 기본값을 이긴다.

nested: 호출측이 이미 expander 안이면 True (Streamlit은 expander 중첩 거부).
"""
import streamlit as st
import pandas as pd

import db_ai_usage as U


def _fmt(n):
    return f"{int(n or 0):,}"


def render(key_prefix="aiu", nested=False):
    """집계 표 + 단가 설정 + 건별 로그. 반환: 집계 행 리스트."""
    _months = U.months_with_usage() or [U._ym()]
    _c1, _c2 = st.columns([1, 2])
    _ym = _c1.selectbox("월", _months, key=f"{key_prefix}_ym")

    _tot = U.month_totals(_ym)
    with _c2:
        st.write("")
        st.caption(f"**{_ym}** 전체 **{_fmt(_tot['cost'])}원** · "
                   f"호출 {_fmt(_tot['calls'])}건 · 사용자 {_tot['users']}명")

    _rows = U.usage_summary(_ym)
    if not _rows:
        st.info("아직 기록된 AI 사용이 없습니다. "
                "(기록은 이 기능 배포 시점부터 쌓입니다 — 이전 사용분은 알 수 없습니다)")
    else:
        _tbl = []
        for _r in _rows:
            _bp, _bf = _r['by_provider'], _r['by_feature']
            _tbl.append({
                "사용자": _r['username'] + (f" ({_r['display_name']})"
                                          if _r['display_name'] else ''),
                "비용(원)": _r['cost'],
                "호출": _r['calls'],
                "Claude": _bp.get('claude', 0),
                "Gemini": _bp.get('gemini', 0),
                "영수증": _bf.get('receipt', 0),
                "사진분석": _bf.get('photo', 0),
                "카테고리": _bf.get('category', 0),
                "상품명·설명": _bf.get('name', 0) + _bf.get('desc', 0),
                "월한도": _r['limit'] or '무제한',
                "남음": ('—' if _r['remaining'] is None
                        else ('🚫 소진' if _r['blocked'] else _r['remaining'])),
            })
        st.dataframe(pd.DataFrame(_tbl), use_container_width=True, hide_index=True)
        st.caption("금액은 **기록 시점 단가·환율로 굳힌 실비**입니다 — 나중에 환율이 "
                   "바뀌어도 이미 청구한 달의 금액은 흔들리지 않습니다. "
                   "용도 칸은 비용(원) 기준입니다.")

    # ── 단가·환율·마진 ────────────────────────────────────────────
    _prices = U.get_prices()
    _zero = [k for k, v in _prices.items()
             if not (float(v.get('in') or 0) or float(v.get('out') or 0))]
    if _zero:
        st.warning(f"⚠️ 단가가 0인 모델이 있습니다: **{', '.join(_zero)}** — "
                   "그 모델 호출은 토큰만 쌓이고 **금액이 0원으로 잡힙니다.** "
                   "아래에서 단가를 채우면 이후 호출부터 금액이 계산됩니다.")

    _setbox = st.container() if nested else st.expander("⚙️ 단가·환율·마진 설정",
                                                        expanded=bool(_zero))
    if nested:
        st.markdown("##### ⚙️ 단가·환율·마진 설정")
    with _setbox:
        _f1, _f2 = st.columns(2)
        _fx = _f1.number_input("환율 (USD → KRW)", min_value=0.0, max_value=100000.0,
                               step=10.0, value=float(U.get_usd_krw()),
                               key=f"{key_prefix}_fx",
                               help="AI 요금은 달러로 청구됩니다. 이 환율로 원화 "
                                    "실비를 계산합니다. 기본값은 임시값이므로 "
                                    "실제 환율로 맞춰 주세요.")
        _mg = _f2.number_input("청구 마진 %", min_value=0.0, max_value=200.0, step=5.0,
                               value=float(U.get_margin_pct()),
                               key=f"{key_prefix}_margin",
                               help="0이면 실비 그대로 청구합니다. 카드 수수료·환전 "
                                    "손실을 얹으려면 올리세요.")
        if abs(_fx - float(U.get_usd_krw())) > 1e-9:
            U.set_usd_krw(_fx)
            st.success(f"✅ 환율 {_fx:,.0f}원으로 저장 (이후 호출부터 적용)")
            st.rerun()
        if abs(_mg - float(U.get_margin_pct())) > 1e-9:
            U.set_margin_pct(_mg)
            st.success(f"✅ 마진 {_mg:g}%로 저장 (이후 호출부터 적용)")
            st.rerun()

        st.caption("모델별 단가 — **USD / 100만 토큰**. 공급사 가격표 그대로 넣으세요.")
        _pdf = pd.DataFrame([{"모델": k, "입력 $/1M": float(v.get('in') or 0),
                              "출력 $/1M": float(v.get('out') or 0)}
                             for k, v in sorted(_prices.items())])
        _ped = st.data_editor(_pdf, use_container_width=True, hide_index=True,
                              num_rows="dynamic", key=f"{key_prefix}_prices",
                              column_config={
                                  "입력 $/1M": st.column_config.NumberColumn(
                                      format="%.4f", min_value=0.0),
                                  "출력 $/1M": st.column_config.NumberColumn(
                                      format="%.4f", min_value=0.0)})
        if st.button("💾 단가 저장", key=f"{key_prefix}_saveprice"):
            _new = {}
            for _r in _ped.to_dict('records'):
                _k = str(_r.get('모델') or '').strip()
                if _k:
                    _new[_k] = {'in': float(_r.get('입력 $/1M') or 0),
                                'out': float(_r.get('출력 $/1M') or 0)}
            U.set_prices(_new)
            st.success(f"✅ 단가 {len(_new)}건 저장 — 이후 호출부터 이 단가로 계산됩니다.")
            st.rerun()
        st.caption("모델명은 **앞부분만 맞으면** 됩니다 — 'claude-haiku-4-5'로 넣어 두면 "
                   "'claude-haiku-4-5-20251001' 호출도 이 단가를 씁니다.")

    if not _rows:
        return _rows

    # ── 건별 로그 ────────────────────────────────────────────────
    if nested:
        if not st.checkbox("📋 건별 호출 로그 보기", key=f"{key_prefix}_showlog"):
            return _rows
        _logbox = st.container()
    else:
        _logbox = st.expander("📋 건별 호출 로그", expanded=False)

    with _logbox:
        _l1, _l2 = st.columns(2)
        _lu = _l1.selectbox("사용자", [''] + [r['username'] for r in _rows],
                            key=f"{key_prefix}_luser",
                            format_func=lambda v: v or "전체")
        _lf = _l2.selectbox("용도", [''] + list(U.FEATURE_LABELS),
                            key=f"{key_prefix}_lfeat",
                            format_func=lambda v: U.FEATURE_LABELS.get(v, v)
                            if v else "전체")
        _lg = U.list_usage(_lu, _ym, feature=_lf, limit=500)
        if not _lg:
            st.caption("해당 조건의 로그가 없습니다.")
        else:
            st.dataframe(pd.DataFrame([{
                "일시": _x['created_at'], "사용자": _x['username'],
                "공급자": _x['provider'], "모델": _x['model'],
                "용도": U.FEATURE_LABELS.get(_x['feature'], _x['feature'] or '기타'),
                "입력토큰": _x['in_tokens'], "출력토큰": _x['out_tokens'],
                "비용(원)": _x['cost_krw'],
            } for _x in _lg]), use_container_width=True, hide_index=True)
            st.caption(f"최근 {len(_lg)}건 (최대 500건)")
    return _rows
