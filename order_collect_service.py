"""일일 주문 수집 로직 — /m/orders(API)가 부른다. Streamlit '일일 주문 수집'과 같은 함수를 쓴다.

수집기(naver_api.get_new_orders · coupang_api.get_orders · cafe24_api.get_orders)와 저장
(services.process_and_save_orders · db_orders)은 그대로 부른다. 화면 코드에만 있던 흐름
(결제일로 기간 자르기 · 기간 이전 미발송 포함 · 마감시각 · 제외 목록 · 주소 보완 · 발송상태
자동 동기화)만 여기로 옮겼다. 옛 /m 수집은 이걸 TypeScript로 다시 짜서 네이버 조회 이름이
틀리고 쿠팡 수량이 늘 1이었다.

Streamlit과 다른 점 두 가지(의도):
  · 수집 결과는 세션이 아니라 DB(미발송 주문)에서 바로 읽어 보여 준다 — 새로고침해도 같다.
  · 네이버 수집 때 화면이 하던 upsert_product(product_no=네이버 상품번호)는 하지 않는다.
    코스트코 상품번호 칸에 네이버 번호가 들어가는 원인이었다(제품 DB '⚠ 9자리 이상').
"""
import re
import threading
from datetime import datetime, timedelta

import pandas as pd

#: naver_api가 경고·상태 분포를 모듈 전역에 남긴다 — 두 사람이 동시에 수집하면 섞이므로 한 줄로 세운다
_NAVER_LOCK = threading.Lock()

NUM_COLS = ('수량', '최종 상품별 총 주문금액', '배송비 합계', '제주/도서 추가배송비', '정산예정금액')


def _settings(username):
    from db import get_all_settings
    return get_all_settings(username) or {}


def _costs(username):
    s = _settings(username)
    try:
        ship = int(s.get('shipping_cost') or 2000)
    except (TypeError, ValueError):
        ship = 2000
    try:
        box = int(s.get('box_cost') or 300)
    except (TypeError, ValueError):
        box = 300
    return ship, box


def _today():
    return datetime.today().strftime("%Y-%m-%d")


def _num(df):
    for c in NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
    return df


def _drop_excluded(username, df):
    from db import get_excluded_order_nos
    if df is None or df.empty or '상품주문번호' not in df.columns:
        return df, 0
    ex = get_excluded_order_nos(username)
    if not ex:
        return df, 0
    m = df['상품주문번호'].astype(str).isin(ex)
    return df[~m].reset_index(drop=True), int(m.sum())


def status(username):
    """어느 수집 경로가 열려 있는지 + 마감시각·마지막 수집 — 화면 첫 안내용."""
    from services import get_order_cutoff_hour
    s = _settings(username)
    ship, box = _costs(username)
    return {
        'naver': bool(s.get('api_client_id') and s.get('api_client_secret')),
        'coupang': bool(s.get('coupang_access_key') and s.get('coupang_secret_key') and s.get('coupang_vendor_id')),
        'cafe24': bool(s.get('cafe24_mall_id') and s.get('cafe24_client_id')),
        'cafe24_auth': bool(s.get('cafe24_access_token')),
        'cutoff_hour': get_order_cutoff_hour(username),
        'last_sync': str(s.get('last_order_sync') or '')[:16].replace('T', ' '),
        'shipping_cost': ship, 'box_cost': box,
        'excel_password_set': bool(s.get('excel_password')),
    }


def _pay_day(o):
    """주문 → 결제일(YYYY-MM-DD). 결제일이 비면 주문일시."""
    m = re.search(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", str(o.get('결제일') or o.get('주문일시') or ''))
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else ''


def collect_naver(username, date_from, date_to, status_type='PAYED', incl_prev=True,
                  incl_after_cutoff=False, log=lambda m: None):
    """네이버 주문 수집 — order_upload_page 465-671과 같은 순서.

    결제일이 [date_from, date_to]인 주문(+ 체크 시 기간 이전 7일 미발송)을 주문 이력에 저장하고,
    주소 빠진 주문을 보완하고, 이미 발송된 건을 미발송에서 뺀다. daily_orders는 건드리지 않는다
    (💾 저장을 눌러야 들어간다). 반환: 건수·경고·이번에 수집된 주문번호.
    """
    import naver_api
    from db import save_order_history, set_setting, get_active_orders
    from services import get_order_cutoff_hour, split_orders_by_cutoff, sync_active_order_status, \
        process_and_save_orders
    s = _settings(username)
    cid, csec = s.get('api_client_id'), s.get('api_client_secret')
    if not (cid and csec):
        raise ValueError('설정에서 네이버 API 키를 먼저 등록하세요.')
    out = {'warnings': [], 'errors': []}
    d_from = datetime.strptime(str(date_from)[:10], "%Y-%m-%d")
    # 기간 이전 미발송을 세려면 시작일보다 7일 더 앞까지 조회한다(변경시각 ≥ 결제시각)
    hours = max(48, int((datetime.now() - (d_from - timedelta(days=7))).total_seconds() / 3600))
    types = ["READY", "PAYED"] if status_type == "ALL" else [status_type]
    all_orders = []
    log(f"네이버 커머스 API 조회 중… ({hours}시간 범위)")
    with _NAVER_LOCK:
        for t in types:
            orders, err = naver_api.get_new_orders(cid, csec, hours_back=hours, status_type=t)
            fw = naver_api.get_last_fetch_warning()
            if fw:
                out['warnings'].append(fw)       # 일부 구간을 호출 한도로 못 읽었다 — 건수가 모자란다
            if orders:
                all_orders.extend(orders)
            elif err and not str(err).startswith("DEBUG_RESP:"):
                out['errors'].append(f"{t} 조회: {err}")
        try:
            dist = naver_api.get_last_status_dist() or {}
        except Exception:
            dist = {}
    out['status_dist'] = dist
    out['fetched'] = len(all_orders)

    # 결제일로 기간 자르기 — 이력에는 전부 남긴다(상태 추적은 기간과 무관)
    nf, nt = str(date_from)[:10], str(date_to)[:10]
    _in, _prev, _after = [], [], []
    for o in all_orders:
        pd_ = _pay_day(o)
        if not pd_ or nf <= pd_ <= nt:
            _in.append(o)                  # 날짜를 모르면 빼지 않는다(누락보다 낫다)
        elif pd_ < nf:
            _prev.append(o)
        else:
            _after.append(o)
    picked = _in + (_prev if incl_prev else [])
    out.update({'in_range': len(_in), 'prev': len(_prev), 'prev_included': bool(incl_prev),
                'after': len(_after), 'deferred_cut': 0})

    cut = get_order_cutoff_hour(username)
    if cut is not None and not incl_after_cutoff and picked:
        picked, deferred = split_orders_by_cutoff(picked, cut)
        out['deferred_cut'] = len(deferred)

    fetched_df = None
    if picked:
        fetched_df = pd.DataFrame(picked).drop_duplicates(subset=['상품주문번호'], keep='last')
        fetched_df, out['excluded'] = _drop_excluded(username, fetched_df)
        save_order_history(username, fetched_df)
    outside = [o for o in (_prev + _after) if o not in picked]
    if outside:
        try:
            save_order_history(username, pd.DataFrame(outside).drop_duplicates(subset=['상품주문번호'], keep='last'))
        except Exception:
            pass
    try:
        set_setting(username, 'last_order_sync', datetime.now().isoformat())
    except Exception:
        pass

    # 주소·연락처 빠진 미발송 주문 보완
    no_addr = [r['order_no'] for r in get_active_orders(username)
               if not r.get('raw_json') or '통합배송지' not in (r.get('raw_json') or '')]
    if no_addr:
        log(f"주소/연락처 보완 중… ({len(no_addr)}건)")
        rows, _ = naver_api.fetch_order_details_by_ids(cid, csec, no_addr)
        if rows:
            save_order_history(username, pd.DataFrame(rows))
        out['address_filled'] = len(rows or [])

    # 이미 발송·완료된 건 자동 제외(네이버에서 직접 보낸 건 포함)
    log("발송상태 확인 중…")
    try:
        sy = sync_active_order_status(username, cid, csec)
        out['sync_cleared'] = int((sy or {}).get('cleared') or 0) if not (sy or {}).get('error') else 0
    except Exception:
        out['sync_cleared'] = 0

    # 제품 DB 배송비·판매가 갱신(매입가 계산만 — daily_orders 저장 안 함)
    if fetched_df is not None and not fetched_df.empty:
        ship, box = _costs(username)
        try:
            r = process_and_save_orders(username, fetched_df, _today(), ship, box,
                                        save_history=False, save_daily=False)
            out['fee_updates'], out['sale_updates'] = r.get('fee_updates', 0), r.get('sale_updates', 0)
            if r.get('error_orders'):
                out['errors'].append(f"매입가 계산 일부 실패: {r['error_orders']}")
        except Exception as e:
            out['errors'].append(f"매입가 계산 실패: {e}")
    out['order_nos'] = [] if fetched_df is None else fetched_df['상품주문번호'].astype(str).tolist()
    log("완료")
    return out


def collect_coupang(username, date_from, date_to, status_type='ALL', incl_after_cutoff=False,
                    log=lambda m: None):
    """쿠팡 주문 수집 — order_upload_page 711-800. 이력 저장 + 제품 DB 갱신."""
    import coupang_api
    from services import get_order_cutoff_hour, split_orders_by_cutoff, process_and_save_orders
    s = _settings(username)
    ak, sk, vid = s.get('coupang_access_key'), s.get('coupang_secret_key'), s.get('coupang_vendor_id')
    if not (ak and sk and vid):
        raise ValueError('설정에서 쿠팡 Wing API 키를 먼저 등록하세요.')
    log(f"쿠팡 Wing API 조회 중… ({date_from} ~ {date_to})")
    res = coupang_api.get_orders(ak, sk, vid, status=status_type, date_from=str(date_from)[:10],
                                 date_to=str(date_to)[:10])
    rows, err = res[0], res[1]                  # 키가 없을 때는 값 2개만 돌려준다
    debug = res[2] if len(res) > 2 else {}
    out = {'warnings': [], 'errors': [err] if err else [], 'fetched': len(rows or []), 'deferred_cut': 0,
           'debug': [{'status': k, 'count': (v or {}).get('count', 0), 'error': (v or {}).get('error') or '',
                      'from': (v or {}).get('from', ''), 'to': (v or {}).get('to', '')}
                     for k, v in (debug or {}).items()]}
    cut = get_order_cutoff_hour(username)
    if rows and cut is not None and not incl_after_cutoff:
        rows, deferred = split_orders_by_cutoff(rows, cut)
        out['deferred_cut'] = len(deferred)
    if err or not rows:
        out['order_nos'] = []
        return out
    df = _num(pd.DataFrame(rows))
    df, out['excluded'] = _drop_excluded(username, df)
    ship, box = _costs(username)
    r = process_and_save_orders(username, df, _today(), ship, box, save_history=True, save_daily=False)
    out.update({'history': r.get('history', 0), 'fee_updates': r.get('fee_updates', 0),
                'sale_updates': r.get('sale_updates', 0),
                'order_nos': df['상품주문번호'].astype(str).tolist() if '상품주문번호' in df.columns else []})
    log("완료")
    return out


def collect_cafe24(username, date_from, date_to, log=lambda m: None):
    """카페24 주문 수집 — order_upload_page 853-896. 토큰이 갱신되면 설정에 저장한다."""
    import cafe24_api
    from db import set_setting
    from services import process_and_save_orders
    s = _settings(username)
    if not (s.get('cafe24_mall_id') and s.get('cafe24_client_id')):
        raise ValueError('설정에서 카페24 연동을 먼저 등록하세요.')
    if not s.get('cafe24_access_token'):
        raise ValueError('카페24 인증 전입니다 — 설정 › 카페24 연동 › 카페24 인증하기를 먼저 하세요.')
    creds = {'mall_id': s.get('cafe24_mall_id'), 'client_id': s.get('cafe24_client_id'),
             'client_secret': s.get('cafe24_client_secret'), 'access_token': s.get('cafe24_access_token'),
             'refresh_token': s.get('cafe24_refresh_token'), 'expires_at': s.get('cafe24_token_expires_at')}

    def _save_tokens(t):
        set_setting(username, 'cafe24_access_token', t.get('access_token', ''))
        set_setting(username, 'cafe24_refresh_token', t.get('refresh_token', ''))
        set_setting(username, 'cafe24_token_expires_at', t.get('expires_at', ''))
    log(f"카페24 주문 조회 중… ({date_from} ~ {date_to})")
    rows, err = cafe24_api.get_orders(creds, str(date_from)[:10], str(date_to)[:10], save_tokens=_save_tokens)
    out = {'warnings': [], 'errors': [f"카페24 조회 실패: {err}"] if err else [], 'fetched': len(rows or [])}
    if err or not rows:
        out['order_nos'] = []
        return out
    df = _num(pd.DataFrame(rows))
    if '상품가격' in df.columns:
        df['상품가격'] = pd.to_numeric(df['상품가격'], errors='coerce').fillna(0).astype(int)
    ship, box = _costs(username)
    r = process_and_save_orders(username, df, _today(), ship, box, save_history=True, save_daily=False)
    out.update({'history': r.get('history', 0),
                'order_nos': df['상품주문번호'].astype(str).tolist() if '상품주문번호' in df.columns else []})
    log("완료")
    return out


#: 엑셀 필수 열 — order_upload_page.EXTRACT_COLS(결제일은 옛 엑셀 호환으로 선택)
EXTRACT_COLS = ['수취인명', '상품명', '옵션정보', '수량', '최종 상품별 총 주문금액', '배송비 합계',
                '제주/도서 추가배송비', '정산예정금액']


def collect_excel(username, files, password='', order_date='', incl_after_cutoff=False, log=lambda m: None):
    """네이버 발주발송관리 엑셀 — order_upload_page 907-976. 이력 저장 + 제품 DB 갱신."""
    import io
    from services import read_excel_auto, get_order_cutoff_hour, split_df_by_cutoff, process_and_save_orders
    pw = password or _settings(username).get('excel_password') or ''
    out = {'warnings': [], 'errors': [], 'fetched': 0, 'order_nos': []}
    frames = []
    for name, data in files:
        bio = io.BytesIO(data)
        bio.name = name
        df, err = read_excel_auto(bio, pw)
        if df is None:
            out['errors'].append(f"{name}: {err}")
            continue
        missing = [c for c in EXTRACT_COLS if c not in df.columns]
        if missing:
            out['errors'].append(f"{name}: 필요한 열이 없습니다 {missing}")
            continue
        frames.append(df)
    if not frames:
        return out
    df = pd.concat(frames, ignore_index=True)
    df, out['excluded'] = _drop_excluded(username, df)
    cut = get_order_cutoff_hour(username)
    if cut is not None and not incl_after_cutoff:
        df, out['deferred_cut'] = split_df_by_cutoff(df, cut)
    keep = EXTRACT_COLS + [c for c in ('결제일', '상품번호', '상품주문번호', '주문일시') if c in df.columns]
    df = _num(df[keep].copy())
    out['fetched'] = len(df)
    ship, box = _costs(username)
    r = process_and_save_orders(username, df, str(order_date or _today())[:10], ship, box,
                                save_history=True, save_daily=False)
    out.update({'history': r.get('history', 0), 'fee_updates': r.get('fee_updates', 0),
                'sale_updates': r.get('sale_updates', 0),
                'order_nos': df['상품주문번호'].astype(str).tolist() if '상품주문번호' in df.columns else []})
    if r.get('error_orders'):
        out['errors'].append(f"매입가 계산 일부 실패: {r['error_orders']}")
    return out


# ── 미발송 주문 ─────────────────────────────────────────────
def _platform(ono):
    o = str(ono or '')
    return 'coupang' if '-' in o else 'naver'


def active(username, incl_after_cutoff=False):
    """지금 미발송 주문 — 구입가는 저장할 때와 같은 계산(compute_costs_for_df).

    미발송이 0건이면 오늘 발송한 건으로 보여 준다(송장 업로드로 오늘 일을 끝낸 뒤에도
    목록이 비지 않게 — order_upload_page 980-995와 같다).
    """
    from db import get_active_orders, db_rows_to_orders_df, get_user_db
    from services import get_order_cutoff_hour, split_df_by_cutoff, compute_costs_for_df
    rows = get_active_orders(username)
    shipped_today = False
    if not rows:
        c = get_user_db(username)
        try:
            rows = [dict(r) for r in c.execute(
                "SELECT oh.* FROM order_history oh JOIN dispatch_log dl ON dl.order_no = oh.order_no "
                "WHERE substr(dl.dispatched_at,1,10) = date('now','localtime')")]
        except Exception:
            rows = []
        finally:
            c.close()
        shipped_today = bool(rows)
    cut = get_order_cutoff_hour(username)
    out = {'rows': [], 'deferred_cut': 0, 'cutoff_hour': cut, 'shipped_today': shipped_today}
    if not rows:
        return out
    df = _num(db_rows_to_orders_df(rows))
    if cut is not None and not incl_after_cutoff:
        df, out['deferred_cut'] = split_df_by_cutoff(df, cut)
    if df.empty:
        return out
    df = compute_costs_for_df(username, df)
    df = df.sort_values('상품명', kind='stable')

    def g(r, k, d=''):
        v = r.get(k, d)
        return d if v is None or (isinstance(v, float) and v != v) else v
    for r in df.to_dict('records'):
        ono = str(g(r, '상품주문번호'))
        out['rows'].append({
            'order_no': ono, 'platform': _platform(ono), 'recipient': str(g(r, '수취인명')),
            'buyer': str(g(r, '구매자명')), 'product_name': str(g(r, '상품명')),
            'product_no': str(g(r, '상품번호')), 'option_info': str(g(r, '옵션정보')),
            'qty': int(g(r, '수량', 1) or 1), 'amount': int(g(r, '최종 상품별 총 주문금액', 0) or 0),
            'shipping_fee': int(g(r, '배송비 합계', 0) or 0), 'settlement': int(g(r, '정산예정금액', 0) or 0),
            'pay_day': str(g(r, '결제일'))[:10], 'ordered_at': str(g(r, '주문일시'))[:16],
            'status': str(g(r, '주문상태')), 'cost': int(g(r, '구입가격', 0) or 0),
            'split_qty': int(g(r, '소분단위', 1) or 1),
        })
    return out


def save_daily(username, order_date, order_nos=None, incl_after_cutoff=False):
    """💾 저장 — 지금 미발송 주문(고른 것만)을 그 날짜 daily_orders에 저장한다.

    이미 다른 날짜에 저장된 주문(이월)은 다시 쌓지 않는다(process_and_save_orders).
    Streamlit은 저장하면서 그 날짜 정산저장(profit_settlements)을 지웠는데, 수익계산이
    발송 기준으로 바뀐 뒤로는 그날 발송 수익을 지우는 일이 돼 여기서는 하지 않는다.
    """
    from db import get_active_orders, db_rows_to_orders_df
    from services import get_order_cutoff_hour, split_df_by_cutoff, process_and_save_orders
    rows = get_active_orders(username)
    if order_nos:
        want = {str(o) for o in order_nos}
        rows = [r for r in rows if str(r.get('order_no')) in want]
    if not rows:
        return {'orders': 0, 'carried': 0}
    df = _num(db_rows_to_orders_df(rows))
    cut = get_order_cutoff_hour(username)
    if cut is not None and not incl_after_cutoff and not order_nos:
        df, _n = split_df_by_cutoff(df, cut)
    ship, box = _costs(username)
    r = process_and_save_orders(username, df, str(order_date)[:10], ship, box,
                                save_history=True, save_daily=True)
    if r.get('error_orders'):
        raise ValueError(f"저장 실패: {r['error_orders']}")
    return {'orders': int(r.get('orders') or 0), 'carried': int(r.get('carried') or 0)}


def sync(username):
    """🚚 발송상태 동기화 — 이미 발송된 건을 미발송에서 뺀다."""
    from services import sync_active_order_status
    s = _settings(username)
    if not (s.get('api_client_id') and s.get('api_client_secret')):
        raise ValueError('설정에서 네이버 API 키를 먼저 등록하세요.')
    r = sync_active_order_status(username, s['api_client_id'], s['api_client_secret']) or {}
    if r.get('error'):
        raise ValueError(f"동기화 실패: {r['error']}")
    return {k: int(r.get(k) or 0) for k in ('checked', 'updated', 'cleared')}


def exclude(username, order_nos, also_daily=False):
    """🗑 영구 삭제 — 주문 이력에서 지우고 제외 목록에 올린다(다시 수집해도 안 들어온다).
    also_daily: 저장된 수집분도 지운다(취소 주문처럼 수익계산에서도 빼야 할 때)."""
    from db import delete_orders_from_history, delete_daily_orders
    onos = [str(o) for o in (order_nos or []) if str(o).strip()]
    n = delete_orders_from_history(username, onos)
    d = delete_daily_orders(username, order_nos=onos) if (also_daily and onos) else 0
    return {'deleted': int(n or 0), 'daily_deleted': int(d or 0)}
