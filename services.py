"""
비즈니스 로직 레이어 — UI 의존성 없음
DB 읽기/쓰기는 db.py를 통해, 유틸은 utils.py를 통해 처리
"""
import re
import io
from datetime import datetime

import pandas as pd

from utils import calc_match_score, MIN_MATCH_SCORE, extract_pack_qty
from db import (
    get_shared_products, get_all_products, get_all_products_merged,
    upsert_user_private,
)


# ── 상품 매칭 ─────────────────────────────────────────────
def _token_score(a: str, b: str) -> float:
    ta = set(re.findall(r'[가-힣a-zA-Z0-9]+', a.lower()))
    tb = set(re.findall(r'[가-힣a-zA-Z0-9]+', b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _find_db_product(products, order_name: str, order_pno: str = '', pno_map: dict = None):
    if order_pno and pno_map:
        p = pno_map.get(str(order_pno).strip())
        if p:
            return p
    best_score, best_p = 0.0, None
    for p in products:
        for field in ('store_product_name', 'match_keyword', 'costco_name'):
            score = _token_score(order_name, p.get(field) or '')
            if score > best_score:
                best_score, best_p = score, p
    return best_p if best_score >= 0.5 else None


def match_product_to_db(username, store_product_name, product_no=None):
    """제품 매칭: shared_products 우선, 없으면 사용자 DB 폴백."""
    sp = match_shared_product(store_product_name, product_no=product_no)
    if sp:
        user_prods = get_all_products(username)
        up = next((p for p in user_prods if p['match_keyword'] == sp['match_keyword']), {})
        return {
            **sp,
            'sale_price':       int(up.get('sale_price',   0) or 0),
            'shipping_fee':     int(up.get('shipping_fee', 0) or 0),
            'naver_product_no': up.get('product_no', ''),
        }
    products = get_all_products(username)
    if not products:
        return None
    if product_no:
        for p in products:
            if p.get('product_no') == str(product_no):
                return p
    candidates = []
    for p in products:
        s1 = calc_match_score(p.get('costco_name', ''), store_product_name)
        s2 = calc_match_score(p['match_keyword'], store_product_name)
        score = max(s1, s2)
        if score >= MIN_MATCH_SCORE:
            candidates.append((p, score))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def match_shared_product(product_name, product_no=None):
    """이름·번호로 공유 제품 검색."""
    products = get_shared_products()
    if not products:
        return None
    if product_no:
        for p in products:
            if str(p.get('product_no', '')) == str(product_no):
                return p
    best_score, best_p = 0.0, None
    for p in products:
        for field in ('match_keyword', 'costco_name'):
            score = _token_score(product_name, p.get(field) or '')
            if score > best_score:
                best_score, best_p = score, p
    return best_p if best_score >= 0.5 else None


# ── 주문에서 제품 정보 자동 업데이트 ─────────────────────
def update_product_info_from_orders(username, orders_df):
    """주문 목록 → 사용자 개인 DB의 shipping_fee·sale_price 동시 갱신."""
    if '상품명' not in orders_df.columns:
        return 0, 0
    merged = get_all_products_merged(username)
    if not merged:
        return 0, 0
    pno_map    = {str(p['product_no']): p for p in merged if p.get('product_no')}
    has_pno    = '상품번호' in orders_df.columns
    has_fee    = '배송비 합계' in orders_df.columns
    has_sprice = '상품가격' in orders_df.columns
    has_total  = '최종 상품별 총 주문금액' in orders_df.columns
    has_qty    = '수량' in orders_df.columns
    group_key  = ['상품명'] + (['상품번호'] if has_pno else [])
    agg_map    = {}
    for keys, grp in orders_df.groupby(group_key):
        if isinstance(keys, str):
            name, pno = keys, ''
        else:
            name = keys[0]
            pno  = str(keys[1]) if len(keys) > 1 else ''
        if has_fee:
            fees    = pd.to_numeric(grp['배송비 합계'], errors='coerce').fillna(0).astype(int)
            nz      = fees[fees > 0]
            fee_val = int(nz.max()) if len(nz) > 0 else 0
        else:
            fee_val = -1
        if has_sprice:
            prices = pd.to_numeric(grp['상품가격'], errors='coerce').fillna(0).astype(int)
        elif has_total and has_qty:
            totals = pd.to_numeric(grp['최종 상품별 총 주문금액'], errors='coerce').fillna(0).astype(int)
            qtys   = pd.to_numeric(grp['수량'], errors='coerce').fillna(1).astype(int).replace(0, 1)
            prices = totals // qtys
        else:
            prices = pd.Series([], dtype=int)
        nz_p     = prices[prices > 0] if len(prices) > 0 else pd.Series([], dtype=int)
        sale_val = int(nz_p.max()) if len(nz_p) > 0 else 0
        agg_map[(str(name), pno)] = {'fee': fee_val, 'sale': sale_val}
    fee_cnt = sale_cnt = 0
    for (name, pno), vals in agg_map.items():
        matched = _find_db_product(merged, name, pno, pno_map)
        if not matched:
            continue
        kw          = matched.get('match_keyword', '')
        costco_name = matched.get('costco_name', name)
        fee_val     = vals['fee']
        sale_val    = vals['sale']
        if fee_val >= 0 or sale_val > 0:
            upsert_user_private(
                username, kw, costco_name,
                sale_price=sale_val if sale_val > 0 else None,
                shipping_fee=fee_val if fee_val >= 0 else None,
            )
            if fee_val >= 0:  fee_cnt  += 1
            if sale_val > 0:  sale_cnt += 1
    return fee_cnt, sale_cnt


def update_product_shipping_fees(username, orders_df):
    fee_cnt, _ = update_product_info_from_orders(username, orders_df)
    return fee_cnt


def update_product_sale_price(username, orders_df):
    _, sale_cnt = update_product_info_from_orders(username, orders_df)
    return sale_cnt


# ── 가격 변동 감지 ────────────────────────────────────────
def detect_price_changes(username, parsed_items):
    shared     = get_shared_products()
    user_prods = get_all_products(username)
    user_fee_map = {up['match_keyword']: int(up.get('shipping_fee', 0) or 0) for up in user_prods}
    changes = []
    for item in parsed_items:
        receipt_name  = item.get('상품명', '')
        receipt_price = int(item.get('단가', 0))
        receipt_no    = str(item.get('상품번호', ''))
        if not receipt_name or receipt_price <= 0:
            continue
        sp = match_shared_product(receipt_name, product_no=receipt_no if receipt_no else None)
        if sp is None:
            continue
        old_price = int(sp.get('unit_price', 0) or 0)
        if old_price <= 0 or old_price == receipt_price:
            continue
        diff     = receipt_price - old_price
        diff_pct = round(diff / old_price * 100, 1)
        changes.append({
            'costco_name': sp.get('costco_name') or receipt_name,
            'old_cost': old_price, 'new_cost': receipt_price,
            'diff': diff, 'diff_pct': diff_pct,
            'product_no': sp.get('product_no', ''),
            'split_qty': int(sp.get('split_qty', 1) or 1),
            'shipping_fee': user_fee_map.get(sp['match_keyword'], 0),
            'shared_id': sp.get('id'),
        })
    return changes


def build_price_alert_msg(changes, today_str=None):
    if not today_str:
        today_str = datetime.now().strftime("%Y-%m-%d")
    up   = [c for c in changes if c['diff'] > 0]
    down = [c for c in changes if c['diff'] < 0]
    lines = [f"[코스트코 가격 변동 알림] {today_str}", ""]
    def fee_str(f):
        return "무료" if f == 0 else f"{f:,}원"
    if up:
        lines.append(f"🔺 가격 인상 ({len(up)}건)")
        for c in up:
            lines.append(f"• {c['costco_name']}\n"
                         f"  {c['old_cost']:,} → {c['new_cost']:,}원 (+{c['diff']:,}원, +{c['diff_pct']}%)\n"
                         f"  고객 배송비: {fee_str(c['shipping_fee'])}")
        lines.append("")
    if down:
        lines.append(f"🔻 가격 인하 ({len(down)}건)")
        for c in down:
            lines.append(f"• {c['costco_name']}\n"
                         f"  {c['old_cost']:,} → {c['new_cost']:,}원 ({c['diff']:,}원, {c['diff_pct']}%)\n"
                         f"  고객 배송비: {fee_str(c['shipping_fee'])}")
        lines.append("")
    lines.append("※ 앱 접속 후 네이버 판매가를 검토하고 적용해주세요.")
    return "\n".join(lines)


# ── 영수증 PDF 파싱 ───────────────────────────────────────
def parse_costco_receipt_pdf(uploaded_pdf):
    try:
        import pdfplumber
    except ImportError:
        return None, "pdfplumber 미설치 (pip install pdfplumber)"
    try:
        if hasattr(uploaded_pdf, 'read'):
            uploaded_pdf.seek(0)
            raw = io.BytesIO(uploaded_pdf.read())
        else:
            raw = uploaded_pdf
            raw.seek(0)
    except Exception as e:
        return None, f"파일 읽기 오류: {e}"
    try:
        with pdfplumber.open(raw) as pdf:
            full_text = "".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        return None, f"PDF 열기 오류: {e}"
    if not full_text.strip():
        return None, "텍스트 추출 실패 (스캔 이미지 PDF이거나 암호화된 파일)"
    items, lines, skip_next = [], full_text.split('\n'), False
    for i in range(len(lines) - 1):
        if skip_next:
            skip_next = False
            continue
        line, next_line = lines[i].strip(), lines[i + 1].strip()
        if line == '*** CPN':
            skip_next = True
            continue
        if 'CPN' in line or 'IRC' in line:
            continue
        m = re.match(r'^(\d{4,7})\s+(\d+)\s+([\d,]+)\s+([\d,\-\s]+)\s*[TFN]?\s*$', next_line)
        if m:
            name = line
            if any(x in name for x in ['코스트코코리아', '대표자', '부산시', '판매', '닫기', 'costco', 'http']):
                continue
            if not name or len(name) < 2:
                continue
            items.append({
                '상품번호': m.group(1),
                '상품명': name,
                '수량': int(m.group(2)),
                '단가': int(m.group(3).replace(',', '')),
            })
            skip_next = True
    if items:
        return items, None
    preview = full_text[:800].strip()
    return None, f"상품 패턴 미인식 (텍스트 {len(full_text)}자 추출)\n\n--- 추출 원문 ---\n{preview}"


def match_receipt_to_orders(receipt_items, order_product_names):
    matches = {}
    for store_name in order_product_names:
        best_idx, best_score = None, 0
        for i, item in enumerate(receipt_items):
            score = calc_match_score(item['상품명'], store_name)
            if score > best_score:
                best_score, best_idx = score, i
        if best_idx is not None and best_score >= MIN_MATCH_SCORE:
            matches[store_name] = receipt_items[best_idx]
    return matches


# ── 엑셀 처리 ─────────────────────────────────────────────
def decrypt_excel(uploaded_file, password):
    try:
        import msoffcrypto
    except ImportError:
        return None, "msoffcrypto 미설치 (pip install msoffcrypto-tool)"
    try:
        uploaded_file.seek(0)
        raw = io.BytesIO(uploaded_file.read())
        raw.seek(0)
        f = msoffcrypto.OfficeFile(raw)
        if not f.is_encrypted():
            raw.seek(0)
            return raw, None
        f.load_key(password=password)
        decrypted = io.BytesIO()
        f.decrypt(decrypted)
        decrypted.seek(0)
        return decrypted, None
    except Exception as e:
        uploaded_file.seek(0)
        return None, str(e)


def read_excel_auto(uploaded_file, password=""):
    decrypt_error = None
    if password:
        result, decrypt_error = decrypt_excel(uploaded_file, password)
        if result is None:
            return None, f"비밀번호 해제 실패: {decrypt_error}"
        uploaded_file = result
    for engine in ['openpyxl', 'xlrd']:
        for skip in [0, 1]:
            try:
                uploaded_file.seek(0)
                df = pd.read_excel(uploaded_file, engine=engine, header=skip)
                first_col = str(df.columns[0]) if len(df.columns) > 0 else ""
                if skip == 0 and len(first_col) > 50:
                    continue
                if len(df) > 0 and len(df.columns) > 3:
                    return df, None
            except Exception as e:
                last_error = str(e)
    for enc in ['utf-8', 'euc-kr']:
        try:
            uploaded_file.seek(0)
            dfs = pd.read_html(uploaded_file, encoding=enc)
            if dfs:
                return dfs[0], None
        except Exception:
            pass
    if decrypt_error:
        return None, f"비밀번호 해제 실패: {decrypt_error}"
    return None, f"파일 읽기 실패: {'last_error' in dir() and last_error or '알 수 없는 오류'}"
