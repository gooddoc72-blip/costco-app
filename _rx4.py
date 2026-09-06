# -*- coding: utf-8 -*-
import io
p = 'pages_lib/receipt_settle_page.py'
s = io.open(p, encoding='utf-8').read()

# ── 1) 사진 판독: 조각판독·보정 알림 + 상품DB 대조 ─────────────
old = """                if not _data.get('_verified', True):
                    _pfails.append((_pf.name,
                                    "금액·수량 자가검증 불일치 — 아래 표에서 값을 확인하세요: "
                                    + " / ".join((_data.get('_check') or [])[:2])))
            _pbar.empty()
            if _pparsed:"""
new = """                _note = []
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
            if _pparsed:"""
assert old in s, 'photo anchor'
s = s.replace(old, new, 1)

# ── 2) 스캔 PDF 판독: 상품DB 대조 ─────────────────────────────
old2 = """        merged = {}
        for p in parsed:"""
new2 = """        parsed, _snap0 = _rs.snap_items_to_catalog(parsed)
        if _snap0:
            with st.expander(f"🔧 상품번호 자동 교정 {len(_snap0)}건", expanded=False):
                for _l in _snap0:
                    st.caption("· " + _l)
        merged = {}
        for p in parsed:"""
assert old2 in s, 'pdf anchor'
s = s.replace(old2, new2, 1)
io.open(p, 'w', encoding='utf-8', newline='').write(s)
print('receipt_settle_page: 교정 연결')
