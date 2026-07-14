"""AI 서비스 — Claude API 연동 (정산 브리핑 등).

anthropic SDK 없이 HTTPS 직접 호출 (서버 의존성 최소화).
API 키는 사용자 설정 'anthropic_api_key' (설정 탭 > AI 설정).
"""
import json
import base64
import requests
from datetime import datetime, timedelta

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"   # 저비용 — 브리핑 1회 ≈ 1원 미만
VISION_MODEL = "claude-sonnet-5"               # 사진 판독(가격표 등)은 정확도 우선


def claude_complete(api_key: str, system: str, user_msg: str,
                    max_tokens: int = 1200, model: str = DEFAULT_MODEL):
    """Claude 메시지 1회 호출. 반환: (text, error)."""
    if not api_key:
        return None, "Anthropic API 키 미설정 (설정 탭 > 🤖 AI 설정)"
    try:
        r = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key.strip(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=60,
        )
        if r.status_code != 200:
            try:
                _e = r.json().get("error", {}).get("message") or r.text[:200]
            except Exception:
                _e = r.text[:200]
            return None, f"[{r.status_code}] {_e}"
        _blocks = r.json().get("content") or []
        _text = "".join(b.get("text", "") for b in _blocks if b.get("type") == "text")
        return (_text.strip() or None), (None if _text.strip() else "빈 응답")
    except Exception as e:
        return None, str(e)


# ── 정산 브리핑 ────────────────────────────────────────────

def build_settlement_briefing_payload(username: str, date: str = "") -> dict:
    """브리핑용 정산 데이터 조립 (DB만 사용 — 외부 API 호출 없음)."""
    from db import (get_naver_settlements_in_range, get_settled_product_order_nos,
                    get_dispatch_log_by_date, get_daily_orders, get_setting)
    from db_dispatch_log import get_dispatch_by_order_nos
    from settlement_service import (reverse_engineer_settlement_stats,
                                    find_unsettled_dispatches)

    today = date or datetime.today().strftime("%Y-%m-%d")
    _from60 = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d")
    rows = get_naver_settlements_in_range(username, _from60, today) or []

    # 최근 7일 일별 입금(건별 합계, 공제 포함 순액)
    _daily7 = {}
    _from7 = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    for r in rows:
        _d = str(r.get("settle_date", ""))
        if _d >= _from7:
            _daily7[_d] = _daily7.get(_d, 0) + int(r.get("settle_amount") or 0)

    # 역산 통계
    _pos = [str(r.get("product_order_no", "")) for r in rows]
    _disp = get_dispatch_by_order_nos(username, _pos, platform="naver") if _pos else {}
    stats = reverse_engineer_settlement_stats(rows, _disp) if rows else {}

    # 오늘 주문 발송 분류
    _td_orders = get_daily_orders(username, today) or []
    _td_disp = get_dispatch_log_by_date(username, today, platform="naver") or []
    _td_set = {str(d.get("order_no", "")) for d in _td_disp}
    _shipped = [o for o in _td_orders if str(o.get("order_no", "") or "") in _td_set]
    _pending = [o for o in _td_orders if str(o.get("order_no", "") or "") not in _td_set]

    # 미정산 (어제·그제 발송분) — 누락 의심 상위
    _settled_set = get_settled_product_order_nos(username)
    _mode = get_setting(username, "naver_settle_mode") or "normal"
    _thr = 10
    if stats:
        _p90 = stats.get("normal_lag_p90")
        if _p90 is not None:
            _thr = max(3, int(_p90) + 2)
    suspects = []
    unsettled_n = 0
    for _back in range(1, 15):
        _d = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=_back)).strftime("%Y-%m-%d")
        _dl = get_dispatch_log_by_date(username, _d, platform="naver") or []
        if not _dl:
            continue
        _us = find_unsettled_dispatches(_dl, _settled_set, today, delay_threshold=_thr,
                                        is_quick_seller=(_mode == "quick"),
                                        normal_lag=stats.get("normal_lag") if stats else None)
        unsettled_n += _us["summary"]["unsettled_n"]
        suspects += [u for u in _us["unsettled"] if u["status"] == "누락 의심"]

    return {
        "date": today,
        "정산방식": "빠른정산" if _mode == "quick" else "일반정산",
        "최근7일_일별입금원": _daily7,
        "오늘입금원": _daily7.get(today, 0),
        "실효수수료율pct": stats.get("comm_rate") if stats else None,
        "배송비정산율pct": stats.get("ship_rate") if stats else None,
        "발송정산소요일_중앙값": stats.get("normal_lag") if stats else None,
        "오늘주문건": len(_td_orders),
        "오늘발송건": len(_shipped),
        "오늘미발송건": len(_pending),
        "오늘발송_정산예정원": sum(int(o.get("settlement") or 0) for o in _shipped),
        "미발송_대기원": sum(int(o.get("settlement") or 0) for o in _pending),
        "최근14일_미정산건": unsettled_n,
        "누락의심건": [
            {"상품주문번호": s["product_order_no"], "상품명": str(s["product_name"])[:30],
             "발송일": s["ship_date"], "경과일": s["elapsed_days"],
             "정산예정원": s["expected_settlement"]}
            for s in suspects[:5]
        ],
        "누락의심_합계원": sum(s["expected_settlement"] for s in suspects),
    }


_BRIEF_SYSTEM = (
    "너는 한국 네이버 스마트스토어 셀러의 정산 담당 비서다. "
    "주어진 정산 데이터(JSON)를 바탕으로 한국어 브리핑을 작성한다.\n"
    "규칙:\n"
    "- 4~7줄, 카카오톡으로 읽기 좋게 짧은 문장. 마크다운 헤더 금지, 이모지 절제(줄당 최대 1개).\n"
    "- 반드시 포함: ①오늘 입금액과 최근 추세 ②오늘 발송/미발송과 정산 영향 "
    "③누락 의심건(있으면 상품명·금액 명시, 없으면 '누락 없음') ④수수료율 정상 여부.\n"
    "- 데이터에 없는 수치를 지어내지 말 것. null/0이면 '데이터 없음'으로 표현.\n"
    "- 금액은 천단위 콤마 + '원'."
)


def claude_vision(api_key, image_bytes, media_type, system, user_text,
                  max_tokens=600, model=None):
    """이미지 1장 + 텍스트 → Claude 멀티모달 응답. 반환: (text, error)."""
    if not api_key:
        return None, "Anthropic API 키 미설정"
    try:
        _b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        r = requests.post(
            ANTHROPIC_URL,
            headers={"x-api-key": api_key.strip(), "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={
                "model": model or VISION_MODEL, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": media_type, "data": _b64}},
                    {"type": "text", "text": user_text},
                ]}],
            }, timeout=90)
        if r.status_code != 200:
            try:
                _e = r.json().get("error", {}).get("message") or r.text[:200]
            except Exception:
                _e = r.text[:200]
            return None, f"[{r.status_code}] {_e}"
        _blocks = r.json().get("content") or []
        _text = "".join(b.get("text", "") for b in _blocks if b.get("type") == "text")
        return (_text.strip() or None), (None if _text.strip() else "빈 응답")
    except Exception as e:
        return None, str(e)


_DESC_SYSTEM = (
    "너는 네이버 스마트스토어 상세페이지 카피라이터다. 상품 사진과 정보를 보고 "
    "구매욕을 높이는 한국어 상세설명을 작성한다.\n"
    "규칙:\n"
    "- 3~5문장, 각 문장 간결하게. 제품 특징·용량/구성·활용법·코스트코 프리미엄 느낌 중심.\n"
    "- **한 문장마다 줄바꿈(엔터)해서 한 줄에 한 문장씩** 작성 (가운데 정렬로 읽기 좋게).\n"
    "- 사진에서 확인되는 사실 위주. 과장·허위·없는 정보 지어내기 금지.\n"
    "- 식품이면 효능·효과·다이어트·의학적 표현 금지(식품 과대광고 규제).\n"
    "- 이모지 1~3개 자연스럽게 사용 가능.\n"
    "- 출력은 설명 본문만 (제목·머리말·따옴표·마크다운 없이 평문)."
)


def _desc_to_lines(text):
    """상세설명을 문장 단위로 나누고 사이에 빈 줄(한 줄 띄움)을 넣어 정리.
    이미 줄바꿈 있으면 그 줄을, 없으면 문장부호(. ! ?)로 분리. 문장 사이는 빈 줄로 구분."""
    import re as _re
    t = (text or "").strip()
    if not t:
        return t
    if "\n" in t:   # AI가 이미 줄바꿈 → 비어있지 않은 줄만 추출
        _parts = [l.strip() for l in t.splitlines() if l.strip()]
    else:           # 한 덩어리면 문장 끝(. ! ?) 뒤에서 분리
        _parts = [p.strip() for p in _re.split(r'(?<=[.!?])\s+', t) if p.strip()]
    return "\n\n".join(_parts)   # 문장 사이 빈 줄


def generate_product_description(api_key, image_bytes, media_type, name="", category=""):
    """상품 사진 + 상품명/카테고리로 상세페이지 설명 초안 생성 (문장별 줄바꿈). 반환: (text, error)."""
    if not api_key:
        return None, "Anthropic API 키 미설정 (설정 탭 > 🤖 AI 설정)"
    _u = (f"상품명: {name or '(미상)'}\n카테고리: {category or '(미상)'}\n"
          "이 상품 사진을 보고 상세페이지에 넣을 상세설명을 작성해줘. 문장마다 줄바꿈해서.")
    _txt, _err = claude_vision(api_key, image_bytes, media_type, _DESC_SYSTEM, _u, max_tokens=500)
    if _txt:
        return _desc_to_lines(_txt), None
    return None, _err


_PHOTO_SYSTEM = (
    "너는 네이버 스마트스토어 상품등록 전문가다. 상품 사진(가격표·라벨 포함 가능)을 분석해 "
    "등록용 정보를 JSON으로만 출력한다.\n"
    "출력 형식(JSON만, 설명 금지):\n"
    '{"name":"상품명","volume":"용량/수량","price":정수,"category":"카테고리키워드","origin":"원산지","brand":"브랜드"}\n'
    "규칙:\n"
    "- volume: 포장에 표기된 용량·중량·수량을 반드시 읽어서 넣는다 "
    "(예: '260mL x 3개입', '1.5kg', '30개입', '500g x 2입', '1L x 6'). 정말 안 보이면 ''.\n"
    "- name: 브랜드+제품명+용량/수량을 모두 포함한 실제 판매용 상품명. **용량/수량을 절대 빠뜨리지 말 것.**\n"
    "- price: 사진 속 가격표/라벨에서 읽은 판매가(숫자만, 원 단위). 할인가가 있으면 할인가. 가격 안 보이면 0.\n"
    "- category: 상품 분류 키워드(예: 어묵, 키친타월, 견과류).\n"
    "- origin: 원산지(모르면 '국산'). brand: 브랜드(모르면 '').\n"
    "가격을 지어내지 말 것 — 안 보이면 반드시 0."
)


def analyze_product_photo(api_key, image_bytes, media_type):
    """상품 사진 → {name, price, category, origin, brand}. 반환: (dict, error)."""
    _txt, _err = claude_vision(api_key, image_bytes, media_type, _PHOTO_SYSTEM,
                               "이 상품 사진을 분석해 등록용 JSON을 출력해줘.")
    if _err or not _txt:
        return None, _err or "빈 응답"
    _s = _txt.strip()
    # 코드블록/여분 텍스트 제거 후 JSON 파싱
    _i, _j = _s.find("{"), _s.rfind("}")
    if _i >= 0 and _j > _i:
        _s = _s[_i:_j + 1]
    try:
        _d = json.loads(_s)
    except Exception:
        return None, f"JSON 파싱 실패: {_txt[:120]}"
    try:
        _price = int(float(_d.get("price", 0) or 0))
    except Exception:
        _price = 0
    # 용량/수량이 상품명에 빠졌으면 자동으로 뒤에 붙임 (누락 방지)
    _name = str(_d.get("name", "") or "").strip()
    _vol = str(_d.get("volume", "") or "").strip()
    if _vol and _vol.replace(" ", "").lower() not in _name.replace(" ", "").lower():
        _name = (_name + " " + _vol).strip()
    return {
        "name": _name[:100],
        "price": _price,
        "category": str(_d.get("category", "") or "").strip(),
        "origin": str(_d.get("origin", "") or "국산").strip(),
        "brand": str(_d.get("brand", "") or "").strip(),
    }, None


_FOODLABEL_SYSTEM = (
    "너는 식품 표시사항(라벨) 판독 전문가다. 제품 뒷면 표시사항/영양성분 사진을 보고 "
    "JSON으로만 출력한다(설명 금지).\n"
    "출력 형식:\n"
    '{"food_type":"식품유형","volume":"내용량","ingredients":"원재료명",'
    '"storage":"보관방법","origin":"원산지","manufacturer":"제조사","importer":"수입원",'
    '"calories":"열량","nutrition":"영양성분","expiration":"소비기한"}\n'
    "규칙:\n"
    "- food_type: 식품유형 (예: '과자(유탕처리제품)').\n"
    "- volume: 내용량/총중량 (예: '680.4g').\n"
    "- ingredients: 원재료명 전체를 사진 그대로 (예: '감자 63.88%, 식물성유지(유채유,옥수수유,대두유,해바라기씨유), 정제소금, 대두 함유').\n"
    "- storage: 보관방법 (예: '직사광선을 피하고 실온에서 보관').\n"
    "- origin: 원산지 (예: '미국').\n"
    "- manufacturer: 제조사 (예: 'FRITO-LAY, INC').\n"
    "- importer: 수입원/판매원 (예: '(주)코스트코 코리아').\n"
    "- calories: 총 열량 + 기준 (예: '총 3,837kcal / 100g당 564kcal').\n"
    "- nutrition: 영양성분을 읽은 그대로 (예: '나트륨 490mg(25%), 탄수화물 54g(17%), 당류 3g(3%), 지방 36g(67%), 포화지방 5g(33%), 트랜스지방 0.5g 미만, 콜레스테롤 0mg(0%), 단백질 6g(11%)').\n"
    "- expiration: 소비/유통기한 표기 (예: '제품에 별도 표시').\n"
    "안 보이는 항목은 빈 문자열 ''. 없는 정보를 지어내지 말 것."
)


def analyze_food_label(api_key, image_bytes, media_type):
    """식품 표시사항 라벨 사진 → {food_type, volume, ingredients, storage, origin,
    manufacturer, importer, calories, nutrition, expiration}. 반환: (dict, error)."""
    if not api_key:
        return None, "Anthropic API 키 미설정 (설정 탭 > 🤖 AI 설정)"
    _txt, _err = claude_vision(api_key, image_bytes, media_type, _FOODLABEL_SYSTEM,
                               "이 식품 표시사항 사진을 분석해 JSON으로 출력해줘.", max_tokens=700)
    if _err or not _txt:
        return None, _err or "빈 응답"
    _s = _txt.strip()
    _i, _j = _s.find("{"), _s.rfind("}")
    if _i >= 0 and _j > _i:
        _s = _s[_i:_j + 1]
    try:
        _d = json.loads(_s)
    except Exception:
        return None, f"JSON 파싱 실패: {_txt[:120]}"
    _keys = ("food_type", "volume", "ingredients", "storage", "origin",
             "manufacturer", "importer", "calories", "nutrition", "expiration")
    return {_k: str(_d.get(_k, "") or "").strip() for _k in _keys}, None


_PRICETAG_SYSTEM = (
    "너는 코스트코 매장 가격표(라벨) 판독 전문가다. 사진 속 가격표를 보고 JSON으로만 출력한다.\n"
    '출력(JSON만): {"product_no":"상품번호","price":정수,"product_name":"상품명"}\n'
    "규칙:\n"
    "- product_no: 라벨 좌측 상단의 코스트코 상품번호(보통 6자리 숫자, 예: 713160). 숫자만.\n"
    "- price: 실제 지불 가격 = **가장 큰 최종 가격**(할인 적용가). 정가/할인액이 같이 있으면 "
    "맨 아래 큰 숫자(최종가)를 쓴다. 숫자만(콤마·원 제거).\n"
    "- product_name: 라벨의 영문/한글 상품명.\n"
    "숫자를 지어내지 말 것. 안 보이면 product_no는 '' , price는 0."
)


def analyze_price_tag(api_key, image_bytes, media_type):
    """코스트코 가격표 사진 → {product_no, price, product_name}. 반환: (dict, error)."""
    _txt, _err = claude_vision(api_key, image_bytes, media_type, _PRICETAG_SYSTEM,
                               "이 코스트코 가격표에서 상품번호와 최종 판매가를 읽어 JSON으로 출력해줘.")
    if _err or not _txt:
        return None, _err or "빈 응답"
    _s = _txt.strip()
    _i, _j = _s.find("{"), _s.rfind("}")
    if _i >= 0 and _j > _i:
        _s = _s[_i:_j + 1]
    try:
        _d = json.loads(_s)
    except Exception:
        return None, f"JSON 파싱 실패: {_txt[:120]}"
    try:
        _price = int(float(str(_d.get("price", 0)).replace(",", "") or 0))
    except Exception:
        _price = 0
    return {
        "product_no": "".join(ch for ch in str(_d.get("product_no", "") or "") if ch.isdigit()),
        "price": _price,
        "product_name": str(_d.get("product_name", "") or "").strip(),
    }, None


_CAT_SYSTEM = (
    "너는 네이버 쇼핑 카테고리 분류 전문가다. 상품명과 '후보 카테고리 경로' 목록을 보고 "
    "그 상품에 가장 정확한 카테고리 경로 하나만 고른다.\n"
    "규칙: 반드시 후보 목록 중 하나를 골라 'A>B>C>D' 형식 **그대로** 한 줄만 출력한다. "
    "설명·번호·따옴표 등 다른 텍스트는 절대 붙이지 않는다."
)


def suggest_naver_category(api_key, product_name, candidate_paths):
    """상품명 + 쇼핑검색 후보 카테고리 경로들 → 최적 경로 1개 선택.
    api_key 없거나 실패 시 최빈(majority) 경로로 폴백. 반환: (path, err)."""
    from collections import Counter
    _uniq = list(dict.fromkeys([p for p in candidate_paths if p]))
    if not _uniq:
        return None, "후보 카테고리 없음"
    _majority = Counter([p for p in candidate_paths if p]).most_common(1)[0][0]
    if not api_key:
        return _majority, None
    _msg = (f"상품명: {product_name}\n\n후보 카테고리 경로:\n"
            + "\n".join(f"- {p}" for p in _uniq))
    _txt, _err = claude_complete(api_key, _CAT_SYSTEM, _msg, max_tokens=120)
    if _err or not _txt:
        return _majority, None
    _pick = _txt.strip().splitlines()[0].strip().strip('"').strip()
    # AI가 후보 밖 값을 내면 최빈으로 폴백 (안전)
    return (_pick if _pick in _uniq else _majority), None


_NAME_SYSTEM = (
    "너는 네이버 스마트스토어 상품명 SEO 전문가다. 코스트코에서 판매하는 상품의 원본명을 "
    "네이버 검색에 잘 걸리는 판매용 상품명으로 '재구성'한다. 단순히 띄어쓰기만 고치지 말고 "
    "구매자가 검색할 키워드를 반영해 적극적으로 최적화한다.\n"
    "구조: [브랜드] [핵심 제품명] [용량/수량/구성] [구매자가 실제 검색할 일반 키워드 1~3개]\n"
    "규칙:\n"
    "- 브랜드·용량/수량/구성은 반드시 유지. 영문 브랜드는 그대로.\n"
    "- 구매자가 검색할 일반 키워드(제품 유형·용도)를 1~3개 자연스럽게 추가. "
    "맥락상 도움되면 맨 앞에 '코스트코'를 넣어도 된다.\n"
    "- 100자 이내. 같은 단어 반복 금지. 특수문자·이모지·홍보문구(최고/정품/강추/무료 등) 금지.\n"
    "- 식품은 효능·효과·다이어트·의학적·최상급 표현 금지(과대광고).\n"
    "- 없는 사실 지어내기 금지. 출력은 상품명 한 줄만 (설명·따옴표·머리말 없이)."
)


def optimize_product_name(api_key, costco_name, category=""):
    """코스트코 원본 상품명 → 네이버 검색최적화 상품명. 실패 시 원본 반환.
    반환: (name, err)."""
    _orig = (costco_name or "").strip()
    if not api_key or not _orig:
        return _orig, None
    _msg = (f"원본 상품명: {_orig}\n카테고리: {category or '(미상)'}\n\n"
            "위 상품의 네이버 검색 최적화 상품명을 한 줄로 출력해줘.")
    # 상품명은 품질 중요 → 비전모델(Sonnet)로 생성
    _txt, _err = claude_complete(api_key, _NAME_SYSTEM, _msg, max_tokens=120, model=VISION_MODEL)
    if _err or not _txt:
        return _orig, _err
    _name = _txt.strip().splitlines()[0].strip().strip('"').strip()
    return (_name[:100] if _name else _orig), None


_DESC_TEXT_SYSTEM = (
    "너는 네이버 스마트스토어 상세페이지 카피라이터다. 코스트코 상품의 이름과 원본 설명(정리 안 됨)을 "
    "보고, 구매욕을 높이는 깔끔한 한국어 상세설명을 새로 작성한다.\n"
    "규칙:\n"
    "- 3~6문장. 각 문장은 간결하게, **한 문장마다 줄바꿈**(한 줄에 한 문장).\n"
    "- 제품 특징·용량/구성·활용법·코스트코 프리미엄 느낌 위주. 원본에 있는 사실만 사용.\n"
    "- 원본 설명이 부실하면 상품명에서 유추 가능한 일반적 사실만 간단히. 지어내기·과장 금지.\n"
    "- 식품은 효능·효과·다이어트·의학적 표현 금지(식품 과대광고 규제).\n"
    "- 이모지 0~2개까지 허용. 목록기호(•)·표·머리말·따옴표·마크다운 금지. 본문만 평문으로 출력."
)


def generate_description_from_costco(api_key, name, costco_text, category=""):
    """코스트코 상품명+원본설명(텍스트) → AI가 새로 작성한 깔끔한 상세설명(문장별 줄바꿈).
    실패 시 (None, err). 원본이 지저분한 HTML이어도 태그 제거 후 사용."""
    if not api_key or not (name or costco_text):
        return None, "입력 없음"
    import re as _re2
    _txt_in = _re2.sub(r"<[^>]+>", " ", str(costco_text or ""))
    _txt_in = _re2.sub(r"\s+", " ", _txt_in).strip()[:1500]
    _msg = (f"상품명: {name}\n카테고리: {category or '(미상)'}\n"
            f"원본 설명(정리 안 됨): {_txt_in or '(없음)'}\n\n"
            "위를 바탕으로 상세페이지용 상세설명을 문장마다 줄바꿈해서 새로 작성해줘.")
    _t, _e = claude_complete(api_key, _DESC_TEXT_SYSTEM, _msg, max_tokens=500, model=VISION_MODEL)
    if _t:
        return _desc_to_lines(_t), None
    return None, _e


def generate_settlement_briefing(username: str, api_key: str, date: str = ""):
    """일일 정산 AI 브리핑 생성. 반환: (text, error)."""
    try:
        payload = build_settlement_briefing_payload(username, date)
    except Exception as e:
        return None, f"데이터 조립 실패: {e}"
    return claude_complete(
        api_key, _BRIEF_SYSTEM,
        "다음 정산 데이터로 오늘의 브리핑을 작성해줘:\n"
        + json.dumps(payload, ensure_ascii=False, default=str),
    )
