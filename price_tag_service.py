"""가격표 사진 → 공유 매입가 — Next.js 새 화면(/m/pricetag)이 부른다.

매장에서 가격표를 여러 장 찍어 올리면 한 장씩 판독하고(ai_service.analyze_price_tag —
Streamlit 제품 DB의 '🏷 바코드·사진으로 매입가 수정'과 같은 판독기), 현재 매입가와
비교해 보여 준 뒤, 관리자가 고른 줄만 공유 매입가(shared_products)에 저장한다.

저장하는 값은 **정가**다. 가격표 최종가는 대개 기간 한정 할인가라, 그걸 매입가로
굳히면 다음 단계(판매가 반영)에서 할인이 끝난 날부터 역마진이 난다. 할인가·종료일은
기록(price_tag_log)에 따로 남긴다.

판독이 틀려도 되돌릴 수 있게 저장할 때마다 이전 값을 price_tag_log에 남기고,
한 번에 저장한 묶음(batch) 단위로 되돌린다.
"""
import base64
import io
import uuid
from datetime import datetime

from db_core import get_auth_db, retry_on_lock

#: 현재 매입가보다 이만큼 넘게 바뀌면 '확인 필요' — 판독 실수(자릿수·카톤가)를 거른다
SUSPECT_RATIO = 0.30


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_tag_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            batch        TEXT NOT NULL,
            product_no   TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            prev_price   INTEGER DEFAULT 0,   -- 저장 전 매입가(되돌릴 값)
            new_price    INTEGER DEFAULT 0,   -- 저장한 매입가(정가)
            final_price  INTEGER DEFAULT 0,   -- 가격표 최종가(할인 적용)
            discount     INTEGER DEFAULT 0,
            sale_end     TEXT DEFAULT '',     -- 할인 종료일
            barcode      TEXT DEFAULT '',
            photo        TEXT DEFAULT '',
            was_new      INTEGER DEFAULT 0,   -- 공유DB에 없던 상품을 새로 만든 줄
            created_by   TEXT DEFAULT '',
            created_at   TEXT DEFAULT '',
            undone_by    TEXT DEFAULT '',
            undone_at    TEXT DEFAULT ''
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ptlog_batch ON price_tag_log(batch)")


def _image(b64, name):
    """base64 → (bytes, media_type). 화면이 JPEG로 줄여 보내지만, 원본이 오면 HEIC도 바꾼다."""
    raw = base64.b64decode(b64 or '')
    if not raw:
        raise ValueError("빈 사진")
    if raw[:3] == b'\xff\xd8\xff':
        return raw, 'image/jpeg'
    if raw[:8] == b'\x89PNG\r\n\x1a\n':
        return raw, 'image/png'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return raw, 'image/webp'
    try:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=90)
            return buf.getvalue(), 'image/jpeg'
    except Exception:
        raise ValueError(f"{name or '사진'}: 읽을 수 없는 형식입니다 — JPG/PNG로 올려 주세요")


def _shared(pno, barcode=''):
    from db_products import find_shared_by_code
    sp = find_shared_by_code(pno) if pno else None
    if not sp and barcode:
        sp = find_shared_by_code(barcode)
    return sp


def classify(info):
    """판독 결과 → 비교 줄. status: change|same|suspect|new|no_pno|no_price."""
    pno = str(info.get('product_no') or '')
    bc = str(info.get('barcode') or '')
    sp = _shared(pno, bc)
    cur = int((sp or {}).get('store_price') or (sp or {}).get('unit_price') or 0)
    new = int(info.get('list_price') or info.get('price') or 0)
    if not pno and not sp:
        status = 'no_pno'
    elif new <= 0:
        status = 'no_price'
    elif not sp:
        status = 'new'
    elif cur == new:
        status = 'same'
    elif cur and abs(new - cur) / cur > SUSPECT_RATIO:
        status = 'suspect'
    else:
        status = 'change'
    return {
        'product_no': str((sp or {}).get('product_no') or pno),
        'barcode': bc,
        'name': str((sp or {}).get('costco_name') or info.get('product_name') or ''),
        'tag_name': str(info.get('product_name') or ''),
        'cur_price': cur,
        'online_price': int((sp or {}).get('online_price') or 0),
        'new_price': new,
        'final_price': int(info.get('price') or 0),
        'discount': int(info.get('discount') or 0),
        'sale_end': str(info.get('sale_end') or ''),
        'change_pct': round((new - cur) / cur * 100, 1) if (cur and new) else None,
        'status': status,
        'provider': str(info.get('_provider') or ''),
    }


def read(b64, name=''):
    """사진 1장 판독 → 비교 줄. 실패하면 {'ok': False, 'error'}."""
    import ai_service
    try:
        img, mt = _image(b64, name)
    except ValueError as e:
        return {'ok': False, 'error': str(e), 'photo': name}
    ak, gk = ai_service.get_ai_keys()
    if not (ak or gk):
        return {'ok': False, 'error': 'AI 키가 없습니다 (설정 › 🤖 AI 설정)', 'photo': name}
    info, err = ai_service.analyze_price_tag(ak, img, mt, gemini_key=gk)
    if err or not info:
        return {'ok': False, 'error': err or '판독 실패', 'photo': name}
    return dict(classify(info), ok=True, photo=name)


def lookup(code):
    """번호·바코드를 직접 넣어 고칠 때 — 판독이 틀린 줄을 화면에서 바로잡는다."""
    sp = _shared(''.join(ch for ch in str(code or '') if ch.isdigit()))
    if not sp:
        return None
    return {'product_no': str(sp.get('product_no') or ''),
            'name': str(sp.get('costco_name') or ''),
            'cur_price': int(sp.get('store_price') or sp.get('unit_price') or 0),
            'online_price': int(sp.get('online_price') or 0)}


def save(username, rows):
    """고른 줄을 공유 매입가에 저장. 반환: {batch, saved, same, skipped[]}.

    같은 상품이 여러 장이면 마지막 줄만 쓴다(같은 가격표를 두 번 찍은 경우).
    """
    from db_products import upsert_shared_store_price
    last = {}
    skipped = []
    for r in rows or []:
        pno = ''.join(ch for ch in str(r.get('product_no') or '') if ch.isdigit())
        price = int(r.get('new_price') or 0)
        if not pno or price <= 0:
            skipped.append({'name': r.get('name') or r.get('photo') or '', 'why': '상품번호·가격 없음'})
            continue
        last[pno] = dict(r, product_no=pno, new_price=price)

    batch = datetime.now().strftime("%Y%m%d%H%M%S") + '-' + uuid.uuid4().hex[:4]
    logs, saved, same = [], 0, 0
    for pno, r in last.items():
        sp = _shared(pno)
        name = str((sp or {}).get('costco_name') or r.get('name') or pno)
        prev = int((sp or {}).get('store_price') or (sp or {}).get('unit_price') or 0)
        res = upsert_shared_store_price(
            costco_name=name, keyword=str((sp or {}).get('match_keyword') or name),
            price=int(r['new_price']), product_no=pno, updated_by=username,
            force_store=True, source='pricetag', barcode=(str(r.get('barcode') or '') or None))
        st = str((res or {}).get('status') or '')
        if st == 'same':
            same += 1
            continue
        saved += 1
        logs.append((batch, pno, name, prev, int(r['new_price']), int(r.get('final_price') or 0),
                     int(r.get('discount') or 0), str(r.get('sale_end') or ''),
                     str(r.get('barcode') or ''), str(r.get('photo') or '')[:120],
                     1 if (st == 'new' or not sp) else 0, username, _now()))

    if logs:
        def _w():
            conn = get_auth_db()
            try:
                _ensure(conn)
                conn.executemany(
                    "INSERT INTO price_tag_log (batch, product_no, product_name, prev_price, "
                    "new_price, final_price, discount, sale_end, barcode, photo, was_new, "
                    "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", logs)
                conn.commit()
            finally:
                conn.close()
        retry_on_lock(_w)
    return {'batch': batch if logs else '', 'saved': saved, 'same': same, 'skipped': skipped}


def history(limit=30):
    """최근 저장 묶음 — [{batch, created_at, created_by, count, undone, rows[]}]."""
    conn = get_auth_db()
    try:
        _ensure(conn)
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM price_tag_log WHERE batch IN ("
            "  SELECT batch FROM price_tag_log GROUP BY batch ORDER BY MAX(id) DESC LIMIT ?)"
            " ORDER BY id DESC", (int(limit),)).fetchall()
        keys = [d[0] for d in conn.execute("SELECT * FROM price_tag_log LIMIT 0").description]
    finally:
        conn.close()
    out, by = [], {}
    for r in rows:
        d = dict(zip(keys, r))
        b = by.get(d['batch'])
        if b is None:
            b = by[d['batch']] = {'batch': d['batch'], 'created_at': d['created_at'],
                                  'created_by': d['created_by'], 'undone': bool(d['undone_at']),
                                  'rows': []}
            out.append(b)
        b['rows'].append({k: d[k] for k in ('product_no', 'product_name', 'prev_price', 'new_price',
                                             'final_price', 'discount', 'sale_end', 'was_new')})
    for b in out:
        b['count'] = len(b['rows'])
    return out


def undo(username, batch):
    """묶음 되돌리기 — 이전 매입가로 다시 저장. 새로 만든 상품은 지우지 않고 그대로 둔다
    (주문·영수증이 이미 그 번호를 쓸 수 있다). 반환: {restored, kept_new}."""
    from db_products import upsert_shared_store_price
    conn = get_auth_db()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT id, product_no, product_name, prev_price, was_new FROM price_tag_log "
            " WHERE batch=? AND COALESCE(undone_at,'')=''", (str(batch),)).fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError("되돌릴 기록이 없습니다(이미 되돌렸거나 없는 묶음)")
    restored, kept = 0, 0
    for _id, pno, name, prev, was_new in rows:
        if was_new or not int(prev or 0):
            kept += 1
            continue
        sp = _shared(pno)
        upsert_shared_store_price(
            costco_name=str((sp or {}).get('costco_name') or name),
            keyword=str((sp or {}).get('match_keyword') or name),
            price=int(prev), product_no=str(pno), updated_by=username,
            force_store=True, source='pricetag-undo')
        restored += 1

    def _w():
        c = get_auth_db()
        try:
            c.execute("UPDATE price_tag_log SET undone_by=?, undone_at=? WHERE batch=? "
                      "AND COALESCE(undone_at,'')=''", (username, _now(), str(batch)))
            c.commit()
        finally:
            c.close()
    retry_on_lock(_w)
    return {'restored': restored, 'kept_new': kept}

