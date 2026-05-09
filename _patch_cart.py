"""등록 → 장바구니 저장 방식으로 변경"""
APP = r"f:/1 코스트코/001 코스트코 자동화/코스트코 자동화 프로그램/app.py"
CART_MAX = 30

with open(APP, encoding="utf-8") as f:
    src = f.read()

# ─── 1. 상단 통계 아래 장바구니 뱃지 + 일괄 등록 버튼 추가 ──────────────
OLD1 = '''    _st1, _st2, _st3 = st.columns(3)
    _st1.metric("전체",    f"{len(_nr_all)}개")
    _st2.metric("등록완료", f"{len(_nr_reg)}개")
    _st3.metric("미등록",   f"{len(_nr_unreg)}개")
    st.divider()'''

NEW1 = f'''    _st1, _st2, _st3, _st4 = st.columns(4)
    _st1.metric("전체",    f"{{len(_nr_all)}}개")
    _st2.metric("등록완료", f"{{len(_nr_reg)}}개")
    _st3.metric("미등록",   f"{{len(_nr_unreg)}}개")
    _nr4_cart = st.session_state.get("nr4_cart", [])
    _st4.metric("장바구니", f"{{len(_nr4_cart)}}/{CART_MAX}개",
                delta="준비됨" if _nr4_cart else None)

    # 장바구니가 있을 때 일괄 등록 버튼
    if _nr4_cart:
        _cart_c1, _cart_c2, _cart_c3 = st.columns([3, 1, 1])
        _cart_c1.info(
            f"🛒 장바구니 {{len(_nr4_cart)}}개 준비 중 — "
            f"카테고리 {{len({{item['cat_name'].split(' > ')[-1] for item in _nr4_cart}})}}"
            "개에 분산"
        )
        if _cart_c2.button("🚀 일괄 등록", key="nr4_cart_reg", type="primary",
                            use_container_width=True):
            st.session_state["nr4_do_register"] = True
            st.rerun()
        if _cart_c3.button("🗑 장바구니 비우기", key="nr4_cart_clear",
                            use_container_width=True):
            st.session_state.pop("nr4_cart", None)
            st.rerun()
    st.divider()'''

assert OLD1 in src, "OLD1 not found"
src = src.replace(OLD1, NEW1, 1)
print("Patch 1 (통계+장바구니 뱃지) 완료")

# ─── 2. 일괄 등록 실행 블록 (divider 전에 삽입) ──────────────────────────
OLD2 = '''    if not _nr_unreg:
        st.success("🎉 모든 상품이 등록 완료되었습니다!")'''

NEW2 = '''    # ── 장바구니 일괄 등록 실행 ─────────────────────────────────────
    if st.session_state.pop("nr4_do_register", False):
        _cart_items = st.session_state.get("nr4_cart", [])
        if _cart_items:
            _do_prog = st.progress(0)
            _do_txt  = st.empty()
            _do_res  = []
            _do_as   = _gs("naver_as_tel") or "1588-1234"
            _do_stk  = 10  # 기본 재고

            for _di, _ditem in enumerate(_cart_items):
                _dp   = _ditem["product"]
                _dcat = _ditem["cat_id"]
                _dp_img   = _dp.get("local_image") or _dp.get("image_url") or ""
                _dp_price = int(_dp.get("sale_price") or 0) or int(_dp.get("unit_price") or 0)
                _dp_name  = _dp["costco_name"]
                _do_txt.text(f"등록 중 ({_di+1}/{len(_cart_items)}): {_dp_name[:30]}")

                if not _dp_img:
                    _do_res.append({"상품명": _dp_name, "카테고리": _ditem["cat_name"].split(" > ")[-1],
                                    "결과": "❌", "내용": "이미지 없음"})
                    _do_prog.progress((_di+1)/len(_cart_items)); continue
                if not _dp_price:
                    _do_res.append({"상품명": _dp_name, "카테고리": _ditem["cat_name"].split(" > ")[-1],
                                    "결과": "❌", "내용": "가격 없음"})
                    _do_prog.progress((_di+1)/len(_cart_items)); continue

                _do_cdn, _do_e1 = naver_api.upload_product_image(api_id, api_secret, _dp_img)
                if _do_e1 or not _do_cdn:
                    _do_res.append({"상품명": _dp_name, "카테고리": _ditem["cat_name"].split(" > ")[-1],
                                    "결과": "❌", "내용": f"이미지 실패: {_do_e1}"})
                    _do_prog.progress((_di+1)/len(_cart_items)); continue

                _do_xraw = _dp.get("extra_images") or ""
                _do_ximgs = []
                if _do_xraw:
                    try: _do_ximgs = _nr_json.loads(_do_xraw)
                    except Exception: pass
                _do_xcdn = []
                if _do_ximgs:
                    _do_xcdn, _ = naver_api.upload_images_batch(api_id, api_secret, _do_ximgs)
                _do_det = ""
                if _dp.get("has_detail") and _dp.get("shared_id"):
                    _, _do_det = get_product_detail(_dp["shared_id"])

                _do_api, _do_e2 = naver_api.register_product(api_id, api_secret, {
                    "name":             _dp_name,
                    "sale_price":       _dp_price,
                    "image_url":        _do_cdn,
                    "category_id":      _dcat,
                    "stock":            _do_stk,
                    "shipping_fee":     int(_dp.get("shipping_fee") or 0),
                    "after_service_tel": _do_as,
                    "extra_image_urls": _do_xcdn,
                    "detail_html":      _do_det,
                })

                if _do_e2 or not _do_api:
                    _do_res.append({"상품명": _dp_name, "카테고리": _ditem["cat_name"].split(" > ")[-1],
                                    "결과": "❌", "내용": str(_do_e2)[:80]})
                else:
                    _do_npno = _do_api.get("origin_product_no", "")
                    upsert_user_private(USERNAME, _dp["match_keyword"], _dp_name,
                                        naver_product_no=_do_npno)
                    if _dp.get("shared_id"):
                        try:
                            _do_ca = sqlite3.connect(AUTH_DB)
                            _do_ca.execute("UPDATE shared_products SET naver_category_id=? WHERE id=?",
                                           (_dcat, _dp["shared_id"]))
                            _do_ca.commit(); _do_ca.close()
                        except Exception: pass
                    _do_res.append({"상품명": _dp_name, "카테고리": _ditem["cat_name"].split(" > ")[-1],
                                    "결과": "✅", "내용": f"상품번호 {_do_npno}"})
                _do_prog.progress((_di+1)/len(_cart_items))

            _do_ok = sum(1 for r in _do_res if r["결과"] == "✅")
            _do_txt.empty()
            # 성공한 항목만 장바구니에서 제거
            _ok_mks_do = {
                _ditem["product"]["match_keyword"]
                for _ditem in _cart_items
                if any(r["상품명"] == _ditem["product"]["costco_name"] and r["결과"] == "✅"
                       for r in _do_res)
            }
            st.session_state["nr4_cart"] = [
                i for i in _cart_items if i["product"]["match_keyword"] not in _ok_mks_do
            ]
            st.session_state["nr4_reg_results"] = _do_res
            st.rerun()

    # 등록 결과 표시
    if st.session_state.get("nr4_reg_results"):
        _r4r_top = st.session_state["nr4_reg_results"]
        _r4_ok_top = sum(1 for r in _r4r_top if r["결과"] == "✅")
        if _r4_ok_top == len(_r4r_top):
            st.success(f"✅ {_r4_ok_top}개 모두 등록 완료!")
        else:
            st.warning(f"성공 {_r4_ok_top}개 / 실패 {len(_r4r_top)-_r4_ok_top}개")
        st.dataframe(pd.DataFrame(_r4r_top), use_container_width=True, hide_index=True)
        if st.button("결과 닫기", key="nr4_top_res_clr"):
            st.session_state.pop("nr4_reg_results", None); st.rerun()
        st.divider()

    if not _nr_unreg:
        st.success("🎉 모든 상품이 등록 완료되었습니다!")'''

assert OLD2 in src, "OLD2 not found"
src = src.replace(OLD2, NEW2, 1)
print("Patch 2 (일괄 등록 실행 블록) 완료")

# ─── 3. 장바구니 뷰어 (STEP 3 전에) ─────────────────────────────────────
OLD3 = '''        # ── STEP 2: 네이버 카테고리 선택 ─────────────────────────────'''

NEW3 = f'''        # ── 장바구니 뷰어 ──────────────────────────────────────────────
        _nr4_cart = st.session_state.get("nr4_cart", [])
        if _nr4_cart:
            with st.expander(f"🛒 장바구니 {{len(_nr4_cart)}}/{CART_MAX}개", expanded=False):
                _cart_by_cat = {{}}
                for _ci_item in _nr4_cart:
                    _ckey = _ci_item["cat_name"].split(" > ")[-1]
                    _cart_by_cat.setdefault(_ckey, []).append(_ci_item)
                for _ccat_name, _citems in _cart_by_cat.items():
                    st.markdown(f"**{{_ccat_name}}** ({{len(_citems)}}개)")
                    for _cii, _ci_item in enumerate(_citems):
                        _cic1, _cic2 = st.columns([5, 1])
                        _cic1.caption(f"  • {{_ci_item['product']['costco_name']}}")
                        if _cic2.button("✖", key=f"cart_rm_{{_ci_item['product']['match_keyword']}}",
                                        use_container_width=True):
                            st.session_state["nr4_cart"] = [
                                i for i in _nr4_cart
                                if i["product"]["match_keyword"] != _ci_item["product"]["match_keyword"]
                            ]
                            st.rerun()
            st.divider()

        # ── STEP 2: 네이버 카테고리 선택 ─────────────────────────────'''

assert OLD3 in src, "OLD3 not found"
src = src.replace(OLD3, NEW3, 1)
print("Patch 3 (장바구니 뷰어) 완료")

# ─── 4. "🚀 선택한 N개 등록" → "➕ 장바구니에 추가" 로 교체 ────────────
OLD4 = '''            if st.button(
                f"🚀 선택한 {len(_checked4)}개를 [{_nr4_ncat_name.split(' > ')[-1]}]에 등록",
                key="nr4_reg_btn", type="primary",
                disabled=(not _checked4 or not _nr4_ncat_id),
            ):
                _r4_prog = st.progress(0)
                _r4_txt  = st.empty()
                _r4_res  = []

                for _ri4, _rp4 in enumerate(_checked4):
                    _rp4_img   = _rp4.get("local_image") or _rp4.get("image_url") or ""
                    _rp4_price = int(_rp4.get("sale_price") or 0) or int(_rp4.get("unit_price") or 0)
                    _rp4_name  = _rp4["costco_name"]
                    _r4_txt.text(f"등록 중 ({_ri4+1}/{len(_checked4)}): {_rp4_name[:30]}")

                    if not _rp4_img:
                        _r4_res.append({"상품명": _rp4_name, "결과": "❌", "내용": "이미지 없음"})
                        _r4_prog.progress((_ri4+1)/len(_checked4)); continue
                    if not _rp4_price:
                        _r4_res.append({"상품명": _rp4_name, "결과": "❌", "내용": "가격 없음"})
                        _r4_prog.progress((_ri4+1)/len(_checked4)); continue

                    _r4_cdn, _r4_e1 = naver_api.upload_product_image(api_id, api_secret, _rp4_img)
                    if _r4_e1 or not _r4_cdn:
                        _r4_res.append({"상품명": _rp4_name, "결과": "❌", "내용": f"이미지 실패: {_r4_e1}"})
                        _r4_prog.progress((_ri4+1)/len(_checked4)); continue

                    _r4_xraw = _rp4.get("extra_images") or ""
                    _r4_ximgs = []
                    if _r4_xraw:
                        try: _r4_ximgs = _nr_json.loads(_r4_xraw)
                        except Exception: pass
                    _r4_xcdn = []
                    if _r4_ximgs:
                        _r4_xcdn, _ = naver_api.upload_images_batch(api_id, api_secret, _r4_ximgs)
                    _r4_det = ""
                    if _rp4.get("has_detail") and _rp4.get("shared_id"):
                        _, _r4_det = get_product_detail(_rp4["shared_id"])

                    _r4_api, _r4_e2 = naver_api.register_product(api_id, api_secret, {
                        "name":             _rp4_name,
                        "sale_price":       _rp4_price,
                        "image_url":        _r4_cdn,
                        "category_id":      _nr4_ncat_id,
                        "stock":            int(_nr4_stk),
                        "shipping_fee":     int(_rp4.get("shipping_fee") or 0),
                        "after_service_tel": _nr4_as or "1588-1234",
                        "extra_image_urls": _r4_xcdn,
                        "detail_html":      _r4_det,
                    })

                    if _r4_e2 or not _r4_api:
                        _r4_res.append({"상품명": _rp4_name, "결과": "❌", "내용": str(_r4_e2)[:80]})
                    else:
                        _r4_npno = _r4_api.get("origin_product_no", "")
                        upsert_user_private(USERNAME, _rp4["match_keyword"], _rp4_name,
                                            naver_product_no=_r4_npno)
                        if _rp4.get("shared_id"):
                            try:
                                _r4_ca = sqlite3.connect(AUTH_DB)
                                _r4_ca.execute(
                                    "UPDATE shared_products SET naver_category_id=? WHERE id=?",
                                    (_nr4_ncat_id, _rp4["shared_id"])
                                )
                                _r4_ca.commit(); _r4_ca.close()
                            except Exception: pass
                        _r4_res.append({"상품명": _rp4_name, "결과": "✅",
                                         "내용": f"상품번호 {_r4_npno}"})
                    _r4_prog.progress((_ri4+1)/len(_checked4))

                _r4_ok = sum(1 for r in _r4_res if r["결과"] == "✅")
                _r4_txt.empty()
                if _nr4_as: set_setting(USERNAME, "naver_as_tel", _nr4_as)

                # 등록 완료 상품 결과에서 제거
                _ok_mks4 = {
                    _rp4["match_keyword"]
                    for _rp4 in _checked4
                    if any(r["상품명"] == _rp4["costco_name"] and r["결과"] == "✅" for r in _r4_res)
                }
                _rem4 = [p for p in _nr4_results if p["match_keyword"] not in _ok_mks4]
                if _rem4:
                    st.session_state["nr4_ai_results"] = _rem4
                else:
                    st.session_state.pop("nr4_ai_results", None)

                st.session_state["nr4_reg_results"] = _r4_res
                st.rerun()

            # 등록 결과 표시
            if st.session_state.get("nr4_reg_results"):
                _r4r = st.session_state["nr4_reg_results"]
                _r4_ok_n = sum(1 for r in _r4r if r["결과"] == "✅")
                if _r4_ok_n == len(_r4r):
                    st.success(f"✅ {_r4_ok_n}개 등록 완료!")
                else:
                    st.warning(f"성공 {_r4_ok_n}개 / 실패 {len(_r4r)-_r4_ok_n}개")
                st.dataframe(pd.DataFrame(_r4r), use_container_width=True, hide_index=True)
                if st.button("결과 닫기", key="nr4_res_clr"):
                    st.session_state.pop("nr4_reg_results", None); st.rerun()'''

NEW4 = f'''            # 장바구니 추가 버튼
            _cart_now  = st.session_state.get("nr4_cart", [])
            _cart_mks  = {{i["product"]["match_keyword"] for i in _cart_now}}
            _new_items = [p for p in _checked4 if p["match_keyword"] not in _cart_mks]
            _slots_left = {CART_MAX} - len(_cart_now)
            _can_add   = bool(_new_items) and bool(_nr4_ncat_id) and _slots_left > 0

            _add_c1, _add_c2 = st.columns([2, 3])
            if _add_c1.button(
                f"➕ 선택한 {{min(len(_new_items), _slots_left)}}개 장바구니에 추가",
                key="nr4_add_cart", type="primary",
                disabled=not _can_add,
            ):
                _to_add = _new_items[:_slots_left]
                for _ap in _to_add:
                    _cart_now.append({{
                        "product":  _ap,
                        "cat_id":   _nr4_ncat_id,
                        "cat_name": _nr4_ncat_name,
                    }})
                st.session_state["nr4_cart"] = _cart_now
                # 추가된 항목 AI 결과에서 제거
                _added_mks = {{p["match_keyword"] for p in _to_add}}
                st.session_state["nr4_ai_results"] = [
                    p for p in _nr4_results if p["match_keyword"] not in _added_mks
                ]
                _added_n = len(_to_add)
                _remain  = {CART_MAX} - len(_cart_now)
                st.rerun()

            if len(_cart_now) >= {CART_MAX}:
                _add_c2.warning(f"장바구니가 꽉 찼습니다 ({CART_MAX}개). 먼저 일괄 등록하세요.")
            elif _new_items:
                _add_c2.caption(
                    f"장바구니 {{len(_cart_now)}}/{CART_MAX}개 · "
                    f"추가 가능 {{min(len(_new_items), _slots_left)}}개"
                )
            if not _new_items and _checked4:
                _add_c2.info("선택한 상품이 이미 모두 장바구니에 있습니다.")'''

assert OLD4 in src, "OLD4 not found"
src = src.replace(OLD4, NEW4, 1)
print("Patch 4 (등록→장바구니 추가 버튼) 완료")

with open(APP, "w", encoding="utf-8") as f:
    f.write(src)
print("모든 패치 완료")
