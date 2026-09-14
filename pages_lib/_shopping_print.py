"""장보기 목록 합본 인쇄 — 관리자 탭과 일일 주문 수집이 공유.

사용자마다 프린트 버튼이 따로 있어 3종짜리 목록도 한 장을 통째로 썼다.
사용자가 10명이면 종이 10장이 나가는데 실제 내용은 두어 장 분량이다.
작은 목록끼리 같은 장에 이어 붙여 종이를 아낀다.

get_items(sub) : 그 제출의 품목 리스트를 돌려주는 함수. 화면마다 품목을 들고
                 있는 방식이 달라(관리자는 items_json, 일일 주문 수집은 별도
                 조회) 접근 방법만 주입받는다.
key_prefix     : Streamlit 위젯 키 접두사. 두 화면이 같은 세션에서 충돌하지 않게.
"""
import hashlib
import json          # noqa: F401  (호출측 호환)

import pandas as pd
import streamlit as st

from utils import fmt


def render(_subs, _period_label, get_items, name_map=None, key_prefix="shop_combo"):
    """여러 사용자의 장보기 목록을 한 문서로 묶어 인쇄한다.

    사용자마다 프린트 버튼이 따로 있어 3종짜리 목록도 한 장을 통째로 썼다.
    사용자가 10명이면 종이 10장이 나가는데 실제 내용은 두어 장 분량이다.

    핵심은 사용자 블록에 break-inside:avoid만 걸고 page-break-after는 걸지
    않는 것이다. 그러면 블록이 페이지 중간에서 잘리지는 않으면서 작은
    블록끼리는 같은 장에 이어 붙는다. 장을 나눠야 할 때만 강제 개행한다.
    """
    import html as _h
    import streamlit.components.v1 as _components

    st.markdown("**🖨 합본 인쇄 — 여러 사용자를 한 장에 모아 찍기**")
    st.caption("사용자마다 따로 찍으면 3종짜리 목록도 한 장을 통째로 씁니다. "
               "여기서는 작은 목록끼리 같은 장에 이어 붙여 종이를 아낍니다.")

    # 체크박스로 고른다 — 주문이 적은 사용자만 골라 한 장에 몰아 찍는 게
    # 실제 쓰임이라, 목록에서 바로 보고 체크하는 편이 빠르다.
    # 표시이름 대응표 — 이 화면은 dmap을 따로 갖고 있지 않다
    if name_map:
        _dn = dict(name_map)
    else:
        try:
            from db import get_all_users as _gau
            _dn = {u['username']: (u.get('display_name') or u['username'])
                   for u in _gau()}
        except Exception:
            _dn = {}
    _cnt_by = []
    for _s in _subs:
        try:
            _nn = len(json.loads(_s.get('items_json') or '[]'))
        except Exception:
            _nn = int(_s.get('total_items') or 0)
        _cnt_by.append(_nn)
    _small = st.number_input(
        "이 종수 이하만 자동 체크 (0 = 전체 체크)", min_value=0, max_value=200,
        value=0, step=1, key=f"{key_prefix}_small",
        help="예: 5를 넣으면 5종 이하인 사용자만 체크됩니다. "
             "주문이 적은 사용자끼리 한 장에 몰아 찍을 때 씁니다.")
    _pick_rows = [{'인쇄': (True if not _small else _cnt_by[i] <= _small),
                   '날짜': _s['order_date'],
                   '사용자': _dn.get(_s['username'], _s['username']),
                   '종수': _cnt_by[i],
                   '매장금액': int(_s.get('total_amount') or 0)}
                  for i, _s in enumerate(_subs)]
    # .encode()가 join에만 걸려 str+bytes로 터졌다 — 합친 뒤에 인코딩한다.
    _pk_sig = hashlib.md5(
        (f"{_small}|" + "|".join(f"{r['날짜']}{r['사용자']}{r['종수']}"
                                 for r in _pick_rows)).encode()).hexdigest()[:8]
    _pk_ed = st.data_editor(
        pd.DataFrame(_pick_rows), use_container_width=True, hide_index=True,
        key=f"{key_prefix}_tbl_{_pk_sig}",
        disabled=['날짜', '사용자', '종수', '매장금액'],
        column_config={
            '인쇄': st.column_config.CheckboxColumn('인쇄', help='체크한 사용자만 찍습니다'),
            '종수': st.column_config.NumberColumn('종수', format='%d'),
            '매장금액': st.column_config.NumberColumn('매장금액', format='%d'),
        })
    _sel = [_subs[i] for i, r in enumerate(_pk_ed.to_dict('records')) if r.get('인쇄')]
    if not _sel:
        st.caption("인쇄할 사용자를 체크하세요.")
        return

    _cc1, _cc2, _cc3 = st.columns([1.3, 1.3, 1.4])
    _per_page = _cc1.checkbox("사용자마다 새 장", value=False, key=f"{key_prefix}_break",
                              help="체크하면 예전처럼 사용자당 한 장씩 나옵니다.")
    _compact = _cc2.checkbox("작게(2단)", value=False, key=f"{key_prefix}_2col",
                             help="목록을 두 단으로 흘려 더 많이 담습니다. "
                                  "종수가 적은 날에 유리합니다.")
    _hide_money = _cc3.checkbox("금액 숨기기", value=False, key=f"{key_prefix}_nomoney",
                                help="매장에서 담기만 할 때는 금액이 없는 편이 읽기 쉽습니다.")

    _blocks, _tot_items, _tot_amt = [], 0, 0
    for _s in _sel:
        try:
            _its = list(get_items(_s) or [])
        except Exception:
            _its = []
        if not _its:
            continue
        _amt = int(_s.get('total_amount') or 0)
        _set = sum(int(_i.get('정산금액') or 0) for _i in _its)
        _tot_items += len(_its)
        _tot_amt += _amt
        _rs = []
        for _it in _its:
            _pno = str(_it.get('코스트코상품번호') or '') or '—'
            _nm = _h.escape(str(_it.get('상품명', '')))
            _qty = int(_it.get('코스트코구매수량') or _it.get('주문수량') or 0)
            _cnt = int(_it.get('주문건수') or 0)
            _money = ('' if _hide_money else
                      f'<td class="r">{fmt(int(_it.get("정산금액") or 0))}</td>')
            _rs.append(f'<tr><td class="chk"></td><td class="no">{_pno}</td>'
                       f'<td>{_nm}</td><td class="r">{_qty}개'
                       + (f'<span class="sub">({_cnt}건)</span>' if _cnt else '')
                       + f'</td>{_money}</tr>')
        _money_h = '' if _hide_money else '<th class="r">정산금액</th>'
        _money_t = '' if _hide_money else f' · 정산 {fmt(_set)}원'
        _blocks.append(
            '<section class="blk">'
            f'<h2>👤 {_h.escape(str(_s["username"]))} '
            f'<span class="d">{_s["order_date"]}</span></h2>'
            f'<div class="meta">{len(_its)}종 · 매장금액 {fmt(_amt)}원{_money_t}</div>'
            '<table><thead><tr><th class="chk">✓</th><th class="no">상품번호</th>'
            f'<th>상품명</th><th class="r">수량</th>{_money_h}</tr></thead>'
            '<tbody>' + ''.join(_rs) + '</tbody></table></section>')

    if not _blocks:
        st.caption("인쇄할 항목이 없습니다.")
        return

    _brk = 'section.blk{break-after:page;page-break-after:always}' if _per_page else ''
    _col = ('body{column-count:2;column-gap:18px}'
            'section.blk{break-inside:avoid-column}') if _compact else ''
    _html = (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
        f'<title>장보기 합본 {_period_label}</title><style>'
        '@page{size:A4;margin:10mm}'
        'body{font-family:"맑은 고딕",sans-serif;padding:0;margin:0;font-size:12px}'
        'h1{font-size:17px;margin:0 0 2px}'
        '.top{color:#666;font-size:12px;margin:0 0 10px;'
        'border-bottom:2px solid #333;padding-bottom:6px}'
        'section.blk{break-inside:avoid;page-break-inside:avoid;margin:0 0 12px}'
        'h2{font-size:14px;margin:0 0 2px;background:#f0f0f0;padding:4px 6px;'
        'border-left:4px solid #333}'
        'h2 .d{font-weight:400;color:#666;font-size:12px;margin-left:6px}'
        '.meta{color:#666;font-size:11px;margin:0 0 4px}'
        'table{width:100%;border-collapse:collapse;font-size:12px}'
        'th,td{border-bottom:1px solid #ddd;padding:3px 5px;text-align:left;'
        'vertical-align:top}'
        'th{background:#fafafa;font-weight:600;font-size:11px}'
        '.r{text-align:right;white-space:nowrap}'
        '.no{white-space:nowrap;color:#555;font-variant-numeric:tabular-nums}'
        '.chk{width:16px}'
        'td.chk::before{content:"";display:inline-block;width:10px;height:10px;'
        'border:1px solid #999;border-radius:2px}'
        '.sub{color:#888;font-size:10px;margin-left:2px}'
        '@media print{.noprint{display:none}}'
        + _brk + _col +
        '</style></head><body>'
        f'<h1>🛒 장보기 합본 — {_h.escape(str(_period_label))}</h1>'
        f'<div class="top">{len(_blocks)}명 · {_tot_items}종 · '
        f'매장금액 합계 {fmt(_tot_amt)}원</div>'
        + ''.join(_blocks) +
        '<button class="noprint" onclick="window.print()" '
        'style="margin-top:16px;padding:8px 20px;font-size:13px;cursor:pointer">'
        '🖨 인쇄</button></body></html>')
    _esc = _h.escape(_html, quote=True)

    st.caption(f"📄 {len(_blocks)}명 · {_tot_items}종 · "
               f"매장금액 합계 {fmt(_tot_amt)}원"
               + ("  ·  사용자마다 새 장" if _per_page
                  else "  ·  작은 목록은 같은 장에 이어 붙습니다"))
    _pc1, _pc2 = st.columns([1.2, 3])
    with _pc1:
        _components.html(
            f'''<button onclick="(function(){{
                var f=document.getElementById('pf_combo');
                if(f&&f.contentWindow){{f.contentWindow.focus();f.contentWindow.print();}}
            }})()" style="width:100%;padding:7px 0;background:#ff4b4b;color:white;
                border:none;border-radius:8px;cursor:pointer;
                font-family:'Source Sans Pro',sans-serif;font-size:14px;font-weight:600">
                🖨 합본 인쇄
            </button>
            <iframe id="pf_combo" srcdoc="{_esc}" style="display:none"></iframe>''',
            height=44,
        )
    _pc2.download_button("📥 합본 HTML 저장", data=_html.encode('utf-8'),
                         file_name=f"shopping_combo_{_period_label}.html",
                         mime="text/html", key=f"{key_prefix}_dl")
