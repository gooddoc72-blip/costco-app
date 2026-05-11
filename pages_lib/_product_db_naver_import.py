"""제품 DB 페이지의 '네이버 스마트스토어 상품 전체 가져오기' expander.

product_db_page.py에서 분리. API 호출 + 엑셀/CSV 파싱 + 리스트 렌더 + 저장 처리.
"""
import streamlit as st

from db import upsert_user_private
from pages_lib._product_db_categories import map_naver_category

try:
    import naver_api
    HAS_NAVER_API = True
except ImportError:
    HAS_NAVER_API = False
    naver_api = None


def render_naver_import_section(USERNAME: str, api_id: str, api_secret: str,
                                channel_seller_id: str, invalidate_data_cache):
    """📥 네이버 스마트스토어 상품 전체 가져오기 expander."""
    with st.expander("📥 네이버 스마트스토어 상품 전체 가져오기", expanded=False):
        if not HAS_NAVER_API:
            st.error("naver_api.py 없음")
        elif not api_id or not api_secret:
            st.warning("⚙️ 설정 탭에서 네이버 커머스 API 키를 먼저 입력하세요.")
        else:
            st.caption("API로 스마트스토어 판매 중인 상품 전체를 가져옵니다.")
            _bcol_a, _bcol_b = st.columns([3, 1])
            if _bcol_b.button("🔍 응답 디버그", key="naver_debug_btn"):
                with st.spinner("디버그 응답 가져오는 중..."):
                    _dbg, _dbg_err = naver_api.debug_first_product_response(api_id, api_secret)
                if _dbg_err:
                    st.error(_dbg_err)
                else:
                    st.json(_dbg)
            if _bcol_a.button("🔄 스마트스토어 상품 전체 불러오기", key="naver_import_btn", type="primary"):
                with st.spinner("상품 목록 가져오는 중..."):
                    _ni_list, _ni_err = naver_api.get_product_list(api_id, api_secret, channel_seller_id or "")
                if _ni_list:
                    st.session_state['_naver_import_products'] = _ni_list
                    st.rerun()
                else:
                    st.error(f"조회 실패: {_ni_err}\n\nAPI가 상품 목록 조회를 지원하지 않는 경우 아래 파일 업로드를 이용하세요.")

        st.divider()
        st.caption("또는 스마트스토어 센터 → 상품조회/수정 → 엑셀 다운로드 후 업로드")
        _ni_file = st.file_uploader(
            "스마트스토어 상품 목록 파일 업로드 (xlsx / csv)",
            type=["xlsx", "csv"],
            key="ni_file_upload",
            label_visibility="collapsed"
        )

        def _parse_naver_export(uploaded_file):
            """스마트스토어 엑셀/CSV 파일 파싱 → [{originProductNo, productName, salePrice}]"""
            import io, csv, zipfile, xml.etree.ElementTree as ET

            name = uploaded_file.name.lower()
            raw = uploaded_file.read()

            rows = []
            if name.endswith('.csv'):
                for enc in ('utf-8-sig', 'euc-kr', 'cp949'):
                    try:
                        text = raw.decode(enc)
                        reader = csv.DictReader(io.StringIO(text))
                        rows = list(reader)
                        break
                    except Exception:
                        continue
            else:
                # xlsx = zip 파일. shared strings + sheet1 파싱
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                        # shared strings
                        strings = []
                        if 'xl/sharedStrings.xml' in zf.namelist():
                            ss = ET.fromstring(zf.read('xl/sharedStrings.xml'))
                            ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                            for si in ss.findall('.//x:si', ns):
                                t = ''.join(t.text or '' for t in si.findall('.//x:t', ns))
                                strings.append(t)
                        # sheet
                        sheet_name = next((n for n in zf.namelist()
                                           if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')), None)
                        if not sheet_name:
                            return None, "시트를 찾을 수 없습니다."
                        ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                        sheet = ET.fromstring(zf.read(sheet_name))
                        grid = []
                        for row in sheet.findall('.//x:row', ns):
                            r = []
                            for c in row.findall('x:c', ns):
                                t_attr = c.get('t', '')
                                v_el = c.find('x:v', ns)
                                val = ''
                                if v_el is not None and v_el.text is not None:
                                    if t_attr == 's':
                                        try:
                                            val = strings[int(v_el.text)]
                                        except Exception:
                                            val = v_el.text
                                    else:
                                        val = v_el.text
                                r.append(val)
                            grid.append(r)
                        if not grid:
                            return None, "데이터가 없습니다."
                        headers = [h.strip() for h in grid[0]]
                        rows = [dict(zip(headers, r + [''] * len(headers))) for r in grid[1:]]
                except Exception as e:
                    return None, f"파일 파싱 오류: {e}"

            # 컬럼명 매핑 (스마트스토어 엑셀 내보내기 기준)
            COL_NO    = ['원상품번호', '상품번호', 'originProductNo', '원 상품번호']
            COL_NAME  = ['상품명', '상품 명', 'productName', '판매상품명']
            COL_PRICE = ['판매가', '판매 가', 'salePrice', '판매가격']
            COL_CAT   = ['카테고리명', '카테고리', 'categoryName', 'wholeCategoryName', '카테고리 이름']

            def find_col(headers_row, candidates):
                for c in candidates:
                    if c in headers_row:
                        return c
                # 부분 매칭
                for c in candidates:
                    for h in headers_row:
                        if c in h or h in c:
                            return h
                return None

            if not rows:
                return None, "파일에 데이터가 없습니다."

            sample_keys = list(rows[0].keys())
            col_no    = find_col(sample_keys, COL_NO)
            col_name  = find_col(sample_keys, COL_NAME)
            col_price = find_col(sample_keys, COL_PRICE)
            col_cat   = find_col(sample_keys, COL_CAT)

            if not col_name:
                return None, f"상품명 컬럼을 찾을 수 없습니다.\n발견된 컬럼: {', '.join(sample_keys[:10])}"

            result = []
            for r in rows:
                pname = (r.get(col_name) or '').strip()
                if not pname:
                    continue
                pno   = str(r.get(col_no, '') or '').strip() if col_no else ''
                try:
                    price = int(str(r.get(col_price, 0) or 0).replace(',', '').split('.')[0])
                except Exception:
                    price = 0
                whole_cat = (r.get(col_cat) or '').strip() if col_cat else ''
                result.append({
                    "originProductNo": pno,
                    "productName": pname,
                    "salePrice": price,
                    "wholeCategoryName": whole_cat,
                })
            return result, None

        if _ni_file:
            _ni_parsed, _ni_parse_err = _parse_naver_export(_ni_file)
            if _ni_parse_err:
                st.error(f"파싱 오류: {_ni_parse_err}")
            elif _ni_parsed:
                st.session_state['_naver_import_products'] = _ni_parsed
                st.success(f"✅ {len(_ni_parsed)}개 상품을 읽었습니다.")

        _ni_prods = st.session_state.get('_naver_import_products')
        if _ni_prods:
            # 상태별 카운트
            def _norm_status(s):
                s = (s or 'SALE').upper()
                if s in ('OUTOFSTOCK', 'SOLD_OUT', 'SOLDOUT'): return 'OUTOFSTOCK'
                if s in ('SUSPENSION', 'STOP', 'PAUSE', 'CLOSE', 'PROHIBITION'): return 'SUSPENSION'
                return 'SALE'

            _sale_cnt = sum(1 for p in _ni_prods if _norm_status(p.get('status')) == 'SALE')
            _oos_cnt  = sum(1 for p in _ni_prods if _norm_status(p.get('status')) == 'OUTOFSTOCK')
            _susp_cnt = sum(1 for p in _ni_prods if _norm_status(p.get('status')) == 'SUSPENSION')

            st.markdown(
                f"**총 {len(_ni_prods)}개** &nbsp;"
                f"<span style='color:#27ae60'>✅ 판매중 {_sale_cnt}</span> &nbsp;"
                f"<span style='color:#f39c12'>🟠 품절 {_oos_cnt}</span> &nbsp;"
                f"<span style='color:#e74c3c'>🚫 판매중지 {_susp_cnt}</span>",
                unsafe_allow_html=True
            )

            # ── 저장 버튼 (상단) ──
            # 체크 상태 미리 집계 (저장 버튼 라벨용)
            _ni_selected_pre = [
                _nip for _nii, _nip in enumerate(_ni_prods)
                if st.session_state.get(f"ni_chk_{_nii}", True)
            ]

            _btop1, _btop2, _btop_sp = st.columns([2.2, 1.5, 6])
            _save_clicked = _btop1.button(
                f"💾 선택한 {len(_ni_selected_pre)}개 저장하기",
                type="primary", key="ni_import_confirm_top",
                disabled=not _ni_selected_pre, use_container_width=True
            )
            _close_clicked = _btop2.button(
                "✖ 닫기", key="ni_import_cancel_top", use_container_width=True
            )

            if _save_clicked:
                _ni_cnt = 0
                _saved_sale = _saved_oos = _saved_susp = 0
                for _nip in _ni_selected_pre:
                    _pname = (_nip.get('productName') or '').strip()
                    _price = int(_nip.get('salePrice', 0))
                    _fee   = int(_nip.get('deliveryFee', 0))
                    _status = _norm_status(_nip.get('status'))
                    _origin_pno = str(_nip.get('originProductNo') or '').strip()
                    _ncat = map_naver_category(_nip.get('wholeCategoryName', ''))
                    if _pname:
                        # 재가져오기 시: naver_origin_pno로 매칭하여 기존 레코드 갱신
                        # product_no(코스트코 상품번호)는 보존, 이름/가격/택배비/상태만 업데이트
                        upsert_user_private(USERNAME, _pname, _pname,
                                            sale_price=_price,
                                            shipping_fee=_fee,
                                            naver_product_no=None,  # None → product_no 보존
                                            status=_status,
                                            from_naver=1,
                                            naver_origin_pno=_origin_pno,
                                            category=_ncat or None)
                        _ni_cnt += 1
                        if _status == 'SALE':         _saved_sale += 1
                        elif _status == 'OUTOFSTOCK': _saved_oos += 1
                        else:                         _saved_susp += 1
                # 결과 요약 메시지를 세션에 저장 → rerun 후에도 표시
                st.session_state['_naver_import_result'] = {
                    'total': _ni_cnt,
                    'sale': _saved_sale,
                    'oos':  _saved_oos,
                    'susp': _saved_susp,
                }
                invalidate_data_cache()
                st.session_state.pop('_naver_import_products', None)
                for _k in list(st.session_state.keys()):
                    if _k.startswith('ni_chk_'):
                        st.session_state.pop(_k, None)
                # 모든 페이지를 1로 리셋, 필터는 건드리지 않음 (count 불일치 방지)
                st.session_state.pop('db_product_filter', None)
                st.session_state.pop('_db_filter_prev', None)
                for _rk in list(st.session_state.keys()):
                    if _rk.startswith('ppage_t') or _rk.startswith('db_pills_t') or _rk == 'admin_sp_page':
                        st.session_state[_rk] = 1
                st.rerun()
            if _close_clicked:
                st.session_state.pop('_naver_import_products', None)
                st.rerun()

            st.markdown("<hr style='margin:6px 0'>", unsafe_allow_html=True)

            # ── 헤더 ──
            _hc = st.columns([0.4, 4.0, 1.3, 1.3, 1.3])
            _hc[0].markdown("**✓**"); _hc[1].markdown("**상품명**")
            _hc[2].markdown("**판매가**"); _hc[3].markdown("**택배비**")
            _hc[4].markdown("**상태**")
            st.markdown("<hr style='margin:2px 0 4px 0'>", unsafe_allow_html=True)

            # ── 리스트 ──
            for _nii, _nip in enumerate(_ni_prods):
                _rc = st.columns([0.4, 4.0, 1.3, 1.3, 1.3])
                _rc[0].checkbox("", key=f"ni_chk_{_nii}", value=True,
                                label_visibility="collapsed")
                _ns = _norm_status(_nip.get('status'))
                _name_color = "#999" if _ns != 'SALE' else "inherit"
                _rc[1].markdown(
                    f"<span style='color:{_name_color}'>{_nip.get('productName', '')}</span>",
                    unsafe_allow_html=True
                )
                _rc[2].write(f"{int(_nip.get('salePrice', 0)):,}원" if _nip.get('salePrice') else '-')
                _rc[3].write(f"{int(_nip.get('deliveryFee', 0)):,}원" if _nip.get('deliveryFee') else '무료')
                if _ns == 'SALE':
                    _rc[4].markdown("<span style='color:#27ae60;font-weight:600'>✅ 판매중</span>",
                                    unsafe_allow_html=True)
                elif _ns == 'OUTOFSTOCK':
                    _rc[4].markdown("<span style='color:#f39c12;font-weight:600'>🟠 품절</span>",
                                    unsafe_allow_html=True)
                else:
                    _rc[4].markdown("<span style='color:#e74c3c;font-weight:600'>🚫 판매중지</span>",
                                    unsafe_allow_html=True)
