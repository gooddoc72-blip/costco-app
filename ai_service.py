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
                    max_tokens: int = 1200, model: str = DEFAULT_MODEL,
                    thinking: dict = None):
    """Claude 메시지 1회 호출. 반환: (text, error).
    thinking: {"type":"disabled"} 등 전달 시 요청에 포함 (단순작업은 사고 끄면 잘림 방지)."""
    if not api_key:
        return None, "Anthropic API 키 미설정 (설정 탭 > 🤖 AI 설정)"
    try:
        _body = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_msg}],
        }
        if thinking is not None:
            _body["thinking"] = thinking
        r = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key.strip(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=_body,
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


# ── Gemini (Google) ────────────────────────────────────────
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# ⚠️ 버전을 박으면 구글이 그 모델을 '신규 사용자에게만' 닫을 때 **새로 키를 발급한
#    사용자만** 404가 난다(2026-09 실측: gemini-2.5-flash가 그렇게 막혔고, 기존 키인
#    oxo·baremi542는 계속 됐다). 그래서 별칭을 기본으로 쓴다.
GEMINI_MODEL = "gemini-flash-latest"   # 현행 flash 별칭. thinking 끄고 사용(아래).
#: 모델이 막혔을 때(404/모델 없음) 순서대로 다시 시도할 후보.
GEMINI_FALLBACK_MODELS = ("gemini-flash-latest", "gemini-3.7-flash", "gemini-2.5-flash")
# 영수증처럼 깨알글씨·부호(-T)·검산이 필요한 판독은 flash가 자주 틀린다.
# → pro로 먼저 읽고 실패 시 flash로 폴백. (pro는 느리고 비싸지만 여전히 Claude보다 저렴)
# ⚠️ 'gemini-2.5-pro'는 신규 사용자에게 404다. 버전이 바뀌어도 안 깨지도록 별칭을 쓴다.
#    (flash도 같은 이유로 별칭으로 되돌렸다 — 'gemini-flash-latest' 400은 옛 이야기이고
#     2026-09 실측에서 thinkingBudget=0을 포함해도 200이다)
GEMINI_VISION_MODEL = "gemini-pro-latest"


def _gemini_post(api_key, model, body, timeout=60):
    """Gemini 호출 1회 + 자가복구 재시도. 반환: (text, error).

    두 가지 함정을 여기서 흡수한다.
      · 모델이 막힘(404) — 버전 박힌 이름이 신규 사용자에게 닫히는 일이 실제로 있었다.
        → GEMINI_FALLBACK_MODELS를 순서대로 다시 시도.
      · thinkingBudget=0 거부(400) — gemini-3.6-flash는 thinking 끄기를 안 받는다
        (실측: 3.6은 400, 3.7·flash-latest는 200).
        → thinkingConfig를 빼고 1회 재시도.
    """
    import copy as _copy
    tried, last_err = [], None
    queue = [model] + [m for m in GEMINI_FALLBACK_MODELS if m != model]
    for _m in queue:
        if _m in tried:
            continue
        tried.append(_m)
        for _drop_think in (False, True):
            _b = _copy.deepcopy(body)
            if _drop_think:
                _gc = _b.get("generationConfig") or {}
                if "thinkingConfig" not in _gc:
                    break                      # 뺄 게 없으면 재시도 무의미
                _gc.pop("thinkingConfig", None)
            try:
                r = requests.post(
                    GEMINI_URL.format(model=_m),
                    headers={"x-goog-api-key": str(api_key).strip(),
                             "content-type": "application/json"},
                    json=_b, timeout=timeout)
            except Exception as e:
                last_err = str(e)
                break
            if r.status_code == 200:
                data = r.json()
                cands = data.get("candidates") or []
                if not cands:
                    return None, "빈 응답(후보 없음 — 안전차단/키 확인)"
                parts = (cands[0].get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts)
                return (text.strip() or None), (None if text.strip() else "빈 응답")
            try:
                _msg = r.json().get("error", {}).get("message") or r.text[:200]
            except Exception:
                _msg = r.text[:200]
            last_err = f"[{r.status_code}] {_msg}"
            if r.status_code == 400 and not _drop_think:
                continue                        # thinking 빼고 한 번 더
            break                               # 그 외에는 다음 모델로
        if last_err and not str(last_err).startswith("[404]"):
            # 404(모델 없음)가 아니면 다른 모델로 바꿔도 소용없다 — 키·쿼터·요청 문제
            if not (str(last_err).startswith("[400]") and "model" in str(last_err).lower()):
                break
    return None, last_err or "빈 응답"


def gemini_complete(api_key: str, system: str, user_msg: str,
                    max_tokens: int = 1200, model: str = GEMINI_MODEL):
    """Gemini 메시지 1회 호출. 반환: (text, error).
    ⚠️ 2.5+ flash는 기본 thinking이 출력토큰을 소진해 빈 응답이 나므로 thinkingBudget=0로 끈다."""
    if not api_key:
        return None, "Gemini API 키 미설정 (설정 탭 > 🤖 AI 설정)"
    try:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0,
                                 "thinkingConfig": {"thinkingBudget": 0}},
        }
        return _gemini_post(api_key, model, body, timeout=60)
    except Exception as e:
        return None, str(e)


def ai_complete(system: str, user_msg: str, *, gemini_key: str = '',
                anthropic_key: str = '', max_tokens: int = 1200,
                claude_model: str = None):
    """가용 키로 자동 선택 — Gemini 우선(있으면), 실패 시 Claude 폴백.
    claude_model: Claude 폴백 시 쓸 모델 (기본 DEFAULT_MODEL=haiku).
                  상품명 작문처럼 품질이 중요하면 VISION_MODEL을 넘긴다.
    반환: (text, error, provider)."""
    _cm = claude_model or DEFAULT_MODEL

    def _claude():
        return claude_complete(anthropic_key, system, user_msg, max_tokens=max_tokens,
                               model=_cm, thinking={"type": "disabled"})

    if gemini_key:
        txt, err = gemini_complete(gemini_key, system, user_msg, max_tokens=max_tokens)
        if txt:
            return txt, '', 'gemini'
        if anthropic_key:
            t2, e2 = _claude()
            if t2:
                return t2, '', 'claude'
            return None, f"Gemini 실패({err}) · Claude 실패({e2})", ''
        return None, err, 'gemini'
    if anthropic_key:
        t2, e2 = _claude()
        return (t2, '', 'claude') if t2 else (None, e2, 'claude')
    return None, "AI 키 없음 (설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키 등록)", ''


def resolve_gemini_key(gemini_key=None):
    """gemini_key=None → 설정에서 자동 해석 / ''(빈 문자열) → 명시적 미사용.

    호출부가 여러 파일에 흩어져 있어, 각 함수가 내부에서 이걸 호출해
    '키를 안 넘긴 기존 호출도 Gemini 폴백을 타도록' 한다.
    """
    if gemini_key is None:
        try:
            return get_ai_keys()[1]
        except Exception:
            return ''
    return gemini_key


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


def _shrink_for_ai(image_bytes, media_type, max_edge=1092):
    """AI 판독용 이미지 축소 — 긴 변을 max_edge로 제한(비율 유지, 크롭 없음).

    폰 원본(3000~4000px)을 그대로 보내면 이미지 토큰이 4~5배 → 등록 1건 80원.
    긴 변만 줄이면 글씨는 읽히면서 비용이 크게 준다.
    ⚠️ 정사각 크롭(resize_square_bytes)은 세로로 긴 식품라벨의 위·아래를 잘라
       원재료·영양성분이 사라지므로 AI 판독엔 절대 쓰지 않는다.
    반환: (bytes, media_type). PIL 없거나 실패·이미 작으면 원본 그대로.
    """
    try:
        from PIL import Image
        import io as _io
        with Image.open(_io.BytesIO(image_bytes)) as im:
            w, h = im.size
            if max(w, h) <= max_edge:
                return image_bytes, media_type   # 이미 충분히 작음
            im = im.convert("RGB")
            scale = max_edge / float(max(w, h))
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                           Image.LANCZOS)
            buf = _io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, media_type   # 실패 시 원본 유지 (절대 안 깨지게)


def claude_vision(api_key, image_bytes, media_type, system, user_text,
                  max_tokens=600, model=None, max_edge=1092):
    """이미지 1장 + 텍스트 → Claude 멀티모달 응답. 반환: (text, error).

    max_edge: 판독 전 긴 변 상한(px). 식품라벨 등 깨알글씨는 크게(1568) 넘겨준다.
    """
    if not api_key:
        return None, "Anthropic API 키 미설정"
    try:
        image_bytes, media_type = _shrink_for_ai(image_bytes, media_type, max_edge)
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


def gemini_vision(api_key, image_bytes, media_type, system, user_text,
                  max_tokens=600, model=GEMINI_MODEL, max_edge=1092, thinking=False):
    """이미지 1장 + 텍스트 → Gemini 멀티모달 응답. 반환: (text, error).

    claude_vision과 동일한 시그니처 — ai_vision에서 서로 바꿔 끼울 수 있게 맞췄다.
    ⚠️ flash는 기본 thinking이 출력토큰을 다 먹어 빈 응답이 나므로 thinkingBudget=0.
    """
    if not api_key:
        return None, "Gemini API 키 미설정"
    try:
        image_bytes, media_type = _shrink_for_ai(image_bytes, media_type, max_edge)
        _b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": media_type, "data": _b64}},
                {"text": user_text},
            ]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0},
        }
        # flash는 기본 thinking이 출력토큰을 다 먹어 빈 응답이 나므로 끈다.
        # pro(영수증 판독)는 thinking이 부호·검산 정확도를 올리므로 켜둔다.
        if not thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        return _gemini_post(api_key, model, body, timeout=180 if thinking else 90)
    except Exception as e:
        return None, str(e)


def ai_vision(system, user_text, image_bytes, media_type, *,
              gemini_key='', anthropic_key='', max_tokens=600, max_edge=1092):
    """사진 판독 — Gemini 우선(있으면), 실패 시 Claude 폴백. 반환: (text, error, provider).

    ai_complete(텍스트)의 이미지 버전. 판독 비용은 Gemini가 약 1/5.
    """
    if gemini_key:
        _t, _e = gemini_vision(gemini_key, image_bytes, media_type, system, user_text,
                               max_tokens=max_tokens, max_edge=max_edge)
        if _t:
            return _t, '', 'gemini'
        if anthropic_key:
            _t2, _e2 = claude_vision(anthropic_key, image_bytes, media_type, system,
                                     user_text, max_tokens=max_tokens, max_edge=max_edge)
            if _t2:
                return _t2, '', 'claude'
            return None, f"Gemini 실패({_e}) · Claude 실패({_e2})", ''
        return None, _e or "빈 응답", 'gemini'
    if anthropic_key:
        _t2, _e2 = claude_vision(anthropic_key, image_bytes, media_type, system,
                                 user_text, max_tokens=max_tokens, max_edge=max_edge)
        return (_t2, '', 'claude') if _t2 else (None, _e2 or "빈 응답", 'claude')
    return None, "AI 키 없음 (설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키 등록)", ''


def _extract_json(text):
    """모델 응답에서 JSON 본문만 추출 → dict. 실패 시 None.
    코드블록/설명이 앞뒤에 붙어 나와도 첫 '{' ~ 마지막 '}'만 잘라 파싱한다."""
    _s = (text or "").strip()
    _i, _j = _s.find("{"), _s.rfind("}")
    if _i >= 0 and _j > _i:
        _s = _s[_i:_j + 1]
    try:
        _d = json.loads(_s)
        return _d if isinstance(_d, dict) else None
    except Exception:
        return None


_KEYS_CACHE = {"t": 0.0, "v": None}   # 전역 키 조회 캐시 (DB 스캔 반복 방지)


def get_ai_keys(settings=None, ttl=20):
    """설정에서 (anthropic_key, gemini_key) 해석. 전역(관리자 공용키) 우선.

    AI 키는 공유 인프라 — 관리자가 다른 계정 설정에 저장했어도 찾도록 폴백 스캔한다.
    settings 없이 호출한 결과만 ttl초 캐시한다(설정 저장 직후 반영을 막지 않게 짧게).
    """
    import time as _time
    if settings is None and _KEYS_CACHE["v"] is not None \
            and (_time.time() - _KEYS_CACHE["t"]) < ttl:
        return _KEYS_CACHE["v"]
    _auto = settings is None
    settings = settings or {}

    def _one(name):
        try:
            from db import get_global_setting
            v = (get_global_setting(name) or '').strip()
            if v:
                return v
        except Exception:
            pass
        v = str(settings.get(name) or '').strip()
        if v:
            return v
        try:
            from db import get_all_users, get_all_settings
            for _u in get_all_users():
                vv = str((get_all_settings(_u['username']) or {}).get(name) or '').strip()
                if vv:
                    return vv
        except Exception:
            pass
        return ''

    _out = (_one('anthropic_api_key'), _one('gemini_api_key'))
    if _auto:
        import time as _time2
        _KEYS_CACHE.update(t=_time2.time(), v=_out)
    return _out


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


def generate_product_description(api_key, image_bytes, media_type, name="", category="",
                                 *, gemini_key=''):
    """상품 사진 + 상품명/카테고리로 상세페이지 설명 초안 생성 (문장별 줄바꿈). 반환: (text, error)."""
    if not (api_key or gemini_key):
        return None, "AI 키 미설정 (설정 탭 > 🤖 AI 설정)"
    _u = (f"상품명: {name or '(미상)'}\n카테고리: {category or '(미상)'}\n"
          "이 상품 사진을 보고 상세페이지에 넣을 상세설명을 작성해줘. 문장마다 줄바꿈해서.")
    _txt, _err, _prov = ai_vision(_DESC_SYSTEM, _u, image_bytes, media_type,
                                  gemini_key=gemini_key, anthropic_key=api_key,
                                  max_tokens=500)
    if _txt:
        return _desc_to_lines(_txt), None
    return None, _err


_PHOTO_SYSTEM = (
    "너는 네이버 스마트스토어 상품등록 전문가다. 상품 사진(가격표·라벨 포함 가능)을 분석해 "
    "등록용 정보를 JSON으로만 출력한다.\n"
    "출력 형식(JSON만, 설명 금지):\n"
    '{"name":"상품명","volume":"용량/수량","price":정수,"category":"카테고리키워드","origin":"원산지",'
    '"brand":"브랜드","manufacturer":"제조사","model_name":"모델명"}\n'
    "규칙:\n"
    "- volume: 포장에 표기된 용량·중량·수량을 반드시 읽어서 넣는다 "
    "(예: '260mL x 3개입', '1.5kg', '30개입', '500g x 2입', '1L x 6'). 정말 안 보이면 ''.\n"
    "- name: 브랜드+제품명+용량/수량을 모두 포함한 실제 판매용 상품명. **용량/수량을 절대 빠뜨리지 말 것.**\n"
    "- price: 사진 속 가격표/라벨에서 읽은 판매가(숫자만, 원 단위). 할인가가 있으면 할인가. 가격 안 보이면 0.\n"
    "- category: 상품 분류 키워드(예: 어묵, 키친타월, 견과류).\n"
    "- origin: 원산지(모르면 '국산'). brand: 브랜드(모르면 '').\n"
    "- manufacturer: 제조사·판매원·수입원(포장에 적힌 회사명. 모르면 브랜드와 같게, 그것도 모르면 '').\n"
    "- model_name: 용량/수량을 뺀 순수 제품명(예: '커클랜드 그릭요거트 플레인 무지방'). 모르면 ''.\n"
    "가격을 지어내지 말 것 — 안 보이면 반드시 0."
)


def analyze_product_photo(api_key, image_bytes, media_type, *, gemini_key=''):
    """상품 사진 → {name, price, category, origin, brand}. 반환: (dict, error).

    Gemini 키가 있으면 Gemini 우선(비용 약 1/5), 실패 시 Claude 폴백.
    등록 직전 화면에서 사람이 확인·수정하는 값이라 판독 오류 비용이 낮다.
    """
    _txt, _err, _prov = ai_vision(_PHOTO_SYSTEM, "이 상품 사진을 분석해 등록용 JSON을 출력해줘.",
                                  image_bytes, media_type,
                                  gemini_key=gemini_key, anthropic_key=api_key)
    if _err or not _txt:
        return None, _err or "빈 응답"
    _d = _extract_json(_txt)
    if _d is None:
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
    # 네이버 '속성'(브랜드·제조사·모델명)과 용량 — 등록 payload의
    # detailAttribute.naverShoppingSearchInfo 에 들어간다. 예전엔 volume을
    # 상품명에만 합치고 버려서 속성 칸이 비어 있었다.
    _brand = str(_d.get("brand", "") or "").strip()
    return {
        "name": _name[:100],
        "price": _price,
        "category": str(_d.get("category", "") or "").strip(),
        "origin": str(_d.get("origin", "") or "국산").strip(),
        "brand": _brand,
        "manufacturer": (str(_d.get("manufacturer", "") or "").strip() or _brand),
        "model_name": str(_d.get("model_name", "") or "").strip(),
        "volume": _vol,
        "_provider": _prov,
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


def analyze_food_label(api_key, image_bytes, media_type, *, gemini_key=''):
    """식품 표시사항 라벨 사진 → {food_type, volume, ingredients, storage, origin,
    manufacturer, importer, calories, nutrition, expiration}. 반환: (dict, error)."""
    if not (api_key or gemini_key):
        return None, "AI 키 미설정 (설정 탭 > 🤖 AI 설정)"
    # 식품 표시사항은 원재료·영양성분이 깨알글씨 → 축소 상한을 크게(1568) 잡아 가독성 보존
    _txt, _err, _prov = ai_vision(_FOODLABEL_SYSTEM,
                                  "이 식품 표시사항 사진을 분석해 JSON으로 출력해줘.",
                                  image_bytes, media_type,
                                  gemini_key=gemini_key, anthropic_key=api_key,
                                  max_tokens=700, max_edge=1568)
    if _err or not _txt:
        return None, _err or "빈 응답"
    _d = _extract_json(_txt)
    if _d is None:
        return None, f"JSON 파싱 실패: {_txt[:120]}"
    _keys = ("food_type", "volume", "ingredients", "storage", "origin",
             "manufacturer", "importer", "calories", "nutrition", "expiration")
    # ⚠️ 반환 dict는 상세페이지 제품정보 표(_food_info_html)로 그대로 흘러가므로
    #    _provider 같은 부가 키를 넣지 않는다 (표에 노출됨).
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


_PRICETAG_USER = "이 코스트코 가격표에서 상품번호와 최종 판매가를 읽어 JSON으로 출력해줘."


def _parse_price_tag_json(txt):
    """가격표 응답 → dict|None."""
    _d = _extract_json(txt)
    if _d is None:
        return None
    try:
        _price = int(float(str(_d.get("price", 0)).replace(",", "") or 0))
    except Exception:
        _price = 0
    return {
        "product_no": "".join(ch for ch in str(_d.get("product_no", "") or "") if ch.isdigit()),
        "price": _price,
        "product_name": str(_d.get("product_name", "") or "").strip(),
    }


def analyze_price_tag(api_key, image_bytes, media_type, *, gemini_key=''):
    """코스트코 가격표 사진 → {product_no, price, product_name}. 반환: (dict, error).

    가격표 값은 제품 원가로 저장되므로 판독 실수 비용이 크다.
    → Gemini로 먼저 읽고, 결과가 수상하면(상품번호 6~7자리 아님 / 가격 0) Claude로 재판독.
    """
    def _suspicious(d):
        return (not d) or d.get("price", 0) <= 0 or not (6 <= len(d.get("product_no", "")) <= 7)

    _out, _g_err = None, ''
    if gemini_key:
        # 가격표는 영수증보다 단순해 flash로 대부분 끝난다.
        #   수상할 때만(번호 6~7자리 아님 / 가격 0) pro로 한 번 더 — Claude 없이도 자력 해결.
        for _gm, _think in ((GEMINI_MODEL, False), (GEMINI_VISION_MODEL, True)):
            _t, _e = gemini_vision(gemini_key, image_bytes, media_type,
                                   _PRICETAG_SYSTEM, _PRICETAG_USER,
                                   model=_gm, thinking=_think,
                                   max_tokens=800 if _think else 600)
            if not _t:
                _g_err = _g_err or f"{_gm}: {_e}"
                continue
            _cand = _parse_price_tag_json(_t)
            if not _cand:
                _g_err = _g_err or f"{_gm}: 응답이 JSON이 아님"
                continue
            if not _suspicious(_cand):
                _cand["_provider"] = "gemini"
                return _cand, None
            _out = _out or _cand          # 수상하지만 유일한 후보로 보관
        if not api_key:
            if _out:
                _out["_provider"] = "gemini"
                return _out, None
            return None, _g_err or "판독 실패"

    _txt, _err = claude_vision(api_key, image_bytes, media_type,
                               _PRICETAG_SYSTEM, _PRICETAG_USER)
    if _err or not _txt:
        # Claude 실패 시 Gemini 결과라도 있으면 그걸 반환 (완전 실패보다 낫다)
        if _out:
            _out["_provider"] = "gemini"
            return _out, None
        return None, _err or "빈 응답"
    _c = _parse_price_tag_json(_txt)
    if _c is None:
        if _out:
            _out["_provider"] = "gemini"
            return _out, None
        return None, f"JSON 파싱 실패: {_txt[:120]}"
    _c["_provider"] = "claude"
    return _c, None


_RECEIPT_SYSTEM = (
    "너는 한국 대형마트(코스트코 / 이마트 트레이더스) 영수증 판독 전문가다. "
    "휴대폰으로 찍은 영수증 사진을 보고 JSON으로만 출력한다(설명·마크다운 금지).\n"
    "출력 형식:\n"
    '{"purchase_date":"YYYY-MM-DD","purchase_time":"HH:MM","store_type":"코스트코|트레이더스",'
    '"store_name":"지점 포함 매장명","total_qty":정수,"item_kinds":정수,"total_amount":정수,'
    '"discount_amount":정수,"coupon_total":정수,"card_last4":"1234","cash_receipt_no":"승인번호",'
    '"items":[{"product_no":"상품번호","name":"상품명","qty":정수,"unit_price":정수,'
    '"amount":정수,"discount":정수}]}\n'
    "규칙:\n"
    "- purchase_date: **영수증 하단/상단의 '거래일시'(결제한 날짜)**. 2자리 연도는 20xx로. 안 보이면 ''.\n"
    "  ⚠️ 회원카드 만료일(유효기간·만료일·EXPIRE·MEMBER EXP 등)을 구매일자로 쓰면 절대 안 된다. "
    "미래 날짜는 거래일자가 아니다. 거래일시와 만료일이 함께 보이면 **과거/오늘 날짜 쪽**을 쓴다.\n"
    "  ⚠️ 코스트코 영수증 날짜는 **MM/DD/YYYY** 형식이다. 예 `08/05/2026` → 2026-08-05 "
    "(8월 5일, 2026년). **연도 4자리를 그대로** 읽을 것 — 2026을 2023/2020 등으로 바꾸지 말 것.\n"
    "- ⚠️ **할인 줄 판별 — 가장 중요**: 코스트코 영수증은 금액 끝에 과세표시 'T'가 붙는데, "
    "**금액과 T 사이에 '-'가 있으면 그 줄은 할인**이다.\n"
    "  · 정상 품목:  `488431  A&H베이킹소다  2x 15,490  30,980 T`\n"
    "  · 할인 줄:    `1286    A&H베이킹소다IRC 2x 3,500   7,000-T`  ← 7,000원 할인\n"
    "  · 할인 줄은 **바로 위 품목과 상품명이 거의 같다**(뒤에 IRC 등이 붙기도 함). "
    "이름만 보고 새 품목으로 착각하지 말고 **금액 뒤의 '-' 부호로 판단**할 것.\n"
    "  · 줄 근처에 'CPN'(쿠폰) 표시가 있으면 그것도 할인 줄이다.\n"
    "  · 할인 줄은 **items에 넣지 말고** 바로 위 품목의 discount에 양수로 넣는다. "
    "어느 품목인지 모르면 discount_amount에 합산한다.\n"
    "  · 그래서 item_kinds(품목 종수)와 total_qty(총수량)에도 할인 줄은 세지 않는다.\n"
    "- coupon_total: 영수증 하단 'COUPON TOTAL' 옆의 **할인 합계 금액**(개수 아님). "
    "예 `COUPON TOTAL  8  23,500` → 23500. 없으면 0.\n"
    "- item_kinds: 영수증에 '총 품목 수 : 20'처럼 적혀 있으면 그 숫자를 그대로.\n"
    "- 검산: (품목 금액 합계) − (할인 합계) = total_amount(합계/VAT 포함) 가 되어야 한다. "
    "안 맞으면 할인 줄을 품목으로 잘못 센 것이니 다시 확인할 것.\n"
    "- store_type: 로고/상호에 COSTCO·코스트코면 '코스트코', TRADERS·트레이더스면 '트레이더스'. "
    "판단 불가면 ''.\n"
    "- store_name: '코스트코 상봉점'처럼 매장명+지점. 지점 안 보이면 매장명만.\n"
    "- total_qty: 구매수량 합계(영수증의 '총수량'/'수량합계'. 없으면 품목 수량의 합).\n"
    "- item_kinds: 품목 종수(줄 수).\n"
    "- total_amount: 실제 결제한 최종 합계금액(할인 반영 후). 숫자만.\n"
    "- discount_amount: 할인·쿠폰·즉시할인 합계. 없으면 0. 양수로.\n"
    "- card_last4: 카드번호 마스킹의 **끝 4자리 숫자만**. 현금결제면 ''.\n"
    "- cash_receipt_no: 현금영수증 승인번호(숫자). 없으면 ''.\n"
    "- items: 품목 줄마다 1개. 상품번호(보통 6~7자리)가 있으면 넣고 없으면 ''. "
    "unit_price=단가, amount=금액. 안 보이는 값은 0.\n"
    "- 흐리거나 잘려서 안 보이는 값은 절대 지어내지 말고 빈 문자열/0으로 둔다."
)


_RECEIPT_USER = (
    "이 영수증에서 구매일자·매장(코스트코/트레이더스)·구매수량·구매금액·할인금액·"
    "카드 끝 4자리·현금영수증 승인번호와 품목 내역을 읽어 JSON으로 출력해줘."
)


def _parse_receipt_json(txt):
    """영수증 판독 응답(JSON 문자열) → 정규화 dict. 실패 시 None.

    dict = {purchase_date, purchase_time, store_type, store_name, total_qty,
            item_kinds, total_amount, discount_amount, card_last4,
            cash_receipt_no, items:[{상품번호,상품명,수량,단가,금액,할인}]}
    """
    _d = _extract_json(txt)
    if _d is None:
        return None

    def _num(v):
        try:
            return int(float(str(v).replace(",", "").replace("원", "").strip() or 0))
        except (TypeError, ValueError):
            return 0

    _store = str(_d.get("store_type", "") or "").strip()
    if "트레이더스" in _store or "TRADERS" in _store.upper():
        _store = "트레이더스"
    elif "코스트코" in _store or "COSTCO" in _store.upper():
        _store = "코스트코"

    # 할인 줄을 품목으로 잘못 읽는 경우가 잦다(코스트코 영수증은 '-3,000' 형태).
    #   → 품목에서 빼고 할인으로 돌린다. 안 그러면 품목합·수량·종수가 전부 부풀어
    #     자가검증이 매번 불일치로 뜬다.
    _DISCOUNT_WORDS = ('할인', '쿠폰', '세일', 'DISCOUNT', 'COUPON', 'SALE', 'SAVING', '즉시')
    _items = []
    _extra_discount = 0
    for _it in (_d.get("items") or []):
        _name = str(_it.get("name", "") or "").strip()
        if not _name:
            continue
        _q = _num(_it.get("qty")) or 1
        _up = _num(_it.get("unit_price"))
        _amt = _num(_it.get("amount")) or _q * _up
        _upper = _name.upper()
        _is_disc = (any(_w in _name or _w in _upper for _w in _DISCOUNT_WORDS)
                    or _amt < 0 or _up < 0)
        if _is_disc:
            _extra_discount += abs(_amt) or abs(_up) * max(1, _q)
            continue
        _items.append({
            "상품번호": "".join(ch for ch in str(_it.get("product_no", "") or "") if ch.isdigit()),
            "상품명": _name,
            "수량": _q,
            "단가": _up,
            "금액": _amt,
            "할인": abs(_num(_it.get("discount"))),
        })

    _date = str(_d.get("purchase_date", "") or "").strip()[:10]
    # 날짜 오독이 두 갈래로 나온다:
    #   ① 회원카드 만료일을 구매일자로 (예: 2027-02-01) → 미래
    #   ② 연도만 틀리게 (예: 08/05/2026을 2023-08-05로) → 아주 먼 과거
    # 영수증 사진은 보통 당일~며칠 내라, 미래거나 400일 넘게 과거면 오독으로 본다.
    # ②는 월·일은 맞는 경우가 대부분이라 **월·일은 두고 연도만** 가장 가까운 과거로 당긴다.
    _date_note = ''
    try:
        if _date:
            _dt = datetime.strptime(_date, "%Y-%m-%d")
            _now = datetime.now()
            if _dt > _now + timedelta(days=1):
                _date_note = _date
                _date = _now.strftime("%Y-%m-%d")
            elif (_now - _dt).days > 400:
                _date_note = _date
                _y = _now.year
                try:
                    _cand = _dt.replace(year=_y)
                except ValueError:            # 2/29 등
                    _cand = _dt.replace(year=_y, day=28)
                if _cand > _now + timedelta(days=1):
                    _cand = _cand.replace(year=_y - 1)
                _date = _cand.strftime("%Y-%m-%d")
    except ValueError:
        pass
    return {
        "purchase_date": _date,
        "_date_fixed_from": _date_note,   # 만료일을 읽었던 원본값 (있으면 검증에서 알림)
        "purchase_time": str(_d.get("purchase_time", "") or "").strip()[:5],
        "store_type": _store,
        "store_name": str(_d.get("store_name", "") or "").strip(),
        "total_qty": _num(_d.get("total_qty")) or sum(i["수량"] for i in _items),
        "item_kinds": _num(_d.get("item_kinds")) or len(_items),
        "total_amount": _num(_d.get("total_amount")),
        "discount_amount": abs(_num(_d.get("discount_amount"))) + _extra_discount,
        "card_last4": "".join(ch for ch in str(_d.get("card_last4", "") or "")
                              if ch.isdigit())[-4:],
        "cash_receipt_no": "".join(ch for ch in str(_d.get("cash_receipt_no", "") or "")
                                   if ch.isdigit()),
        "items": _items,
    }


def validate_receipt(data, tolerance=1):
    """영수증 판독 결과 자가검증 — 영수증에 내장된 체크섬으로 판독 오류를 잡는다.

    영수증은 (품목 금액 합계 − 할인 = 결제금액), (품목 수량 합계 = 총수량),
    (품목 줄 수 = 품목 종수)라는 관계가 항상 성립한다. 이게 깨지면 AI가 숫자를
    잘못 읽었거나 지어냈다는 신호다.

    반환: (ok, issues). ok=False = 금액/수량이 안 맞는 '심각' 오류 → 재판독 대상.
    """
    import re as _re
    if not data:
        return False, ["판독 결과 없음"]
    _issues, _critical = [], False
    _items = data.get("items") or []
    if not _items:
        return False, ["품목 내역을 하나도 못 읽음"]

    _sum_amt = sum(int(i.get("금액") or 0) for i in _items)
    _sum_disc = sum(int(i.get("할인") or 0) for i in _items)
    _sum_qty = sum(int(i.get("수량") or 0) for i in _items)
    _total = int(data.get("total_amount") or 0)
    _disc = int(data.get("discount_amount") or 0)

    if _total > 0:
        # 할인이 품목별로 찍히는 영수증 / 합계로만 찍히는 영수증이 섞여 있어 둘 다 허용
        _cands = (_sum_amt, _sum_amt - _disc, _sum_amt - _sum_disc,
                  _sum_amt - _disc - _sum_disc)
        if not any(abs(_total - c) <= tolerance for c in _cands):
            _issues.append(f"금액 불일치 — 품목합 {_sum_amt:,}원"
                           f"(할인 {max(_disc, _sum_disc):,}원) vs 결제금액 {_total:,}원")
            _critical = True
    else:
        _issues.append("결제금액(합계)을 못 읽음")
        _critical = True

    _tq = int(data.get("total_qty") or 0)
    if _tq and _sum_qty and _tq != _sum_qty:
        _issues.append(f"수량 불일치 — 품목합 {_sum_qty}개 vs 총수량 {_tq}개")
        _critical = True

    _kinds = int(data.get("item_kinds") or 0)
    if _kinds and _kinds != len(_items):
        _issues.append(f"품목 수 불일치 — 읽은 {len(_items)}종 vs 표기 {_kinds}종")

    for _i in _items:
        _q = int(_i.get("수량") or 0)
        _u = int(_i.get("단가") or 0)
        _a = int(_i.get("금액") or 0)
        if _q and _u and _a and abs(_q * _u - _a) > tolerance:
            _issues.append(f"'{str(_i.get('상품명', ''))[:16]}' "
                           f"단가×수량({_q * _u:,}) ≠ 금액({_a:,})")

    _fixed_from = str(data.get("_date_fixed_from") or "")
    if _fixed_from:
        _issues.append(f"구매일자를 미래({_fixed_from})로 읽어 오늘로 바로잡음 "
                       f"— 회원카드 만료일을 읽었을 수 있으니 날짜를 확인하세요")

    _dt_s = str(data.get("purchase_date") or "")
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", _dt_s):
        _issues.append("구매일자를 못 읽음")
    else:
        try:
            if datetime.strptime(_dt_s, "%Y-%m-%d") > datetime.now() + timedelta(days=1):
                _issues.append(f"미래 날짜({_dt_s})")
        except Exception:
            _issues.append(f"구매일자 이상({_dt_s})")

    return (not _critical), _issues


def parse_receipt_photo(api_key, image_bytes, media_type, max_tokens=4000, *, gemini_key=''):
    """코스트코/트레이더스 영수증 사진 → 매입 정보 dict. 반환: (dict, error).

    비용/정확도 전략: Gemini로 먼저 읽고 validate_receipt로 자가검증 →
      · 검증 통과 → 그대로 채택 (판독 비용 약 1/5)
      · 검증 실패 → Claude로 재판독해 더 나은 쪽 채택
    선명한 사진은 대부분 Gemini에서 끝나고, 흐린 사진만 Claude가 받는다.

    결과 dict 부가 키: _provider('gemini'|'claude') · _check(경고 목록) · _verified(bool)
    영수증은 글씨가 작고 세로로 길어 축소 상한을 1568px로 둔다(가격표보다 크게).
    """
    def _tag(d, prov, issues, ok):
        d["_provider"] = prov
        d["_check"] = issues
        d["_verified"] = ok
        return d

    _g_data, _g_err = None, ''
    if gemini_key:
        # pro(정확·thinking) → flash(빠름) 순으로 시도. 검산을 통과하면 거기서 끝낸다.
        # 둘 다 검산을 못 넘기면 '경고가 더 적은 쪽'을 후보로 남긴다.
        _g_iss = None
        for _gm, _think in ((GEMINI_VISION_MODEL, True), (GEMINI_MODEL, False)):
            _gt, _e = gemini_vision(gemini_key, image_bytes, media_type, _RECEIPT_SYSTEM,
                                    _RECEIPT_USER, max_tokens=max_tokens, max_edge=1568,
                                    model=_gm, thinking=_think)
            if not _gt:
                _g_err = _g_err or f"{_gm}: {_e}"
                continue
            _cand = _parse_receipt_json(_gt)
            if not _cand:
                _g_err = _g_err or f"{_gm}: 응답이 JSON이 아님"
                continue
            _cok, _ciss = validate_receipt(_cand)
            if _cok:
                return _tag(_cand, 'gemini', _ciss, True), None
            if _g_data is None or len(_ciss) < len(_g_iss or []):
                _g_data, _g_iss = _cand, _ciss
        if not api_key:
            if _g_data:
                return _tag(_g_data, 'gemini', _g_iss or [], False), None
            return None, _g_err or "판독 실패"

    if not api_key:
        return None, "AI 키 없음 (설정 탭 > 🤖 AI 설정에서 Gemini 또는 Claude 키 등록)"

    _txt, _err = claude_vision(api_key, image_bytes, media_type, _RECEIPT_SYSTEM,
                               _RECEIPT_USER, max_tokens=max_tokens, max_edge=1568)
    _c_data = _parse_receipt_json(_txt) if _txt else None
    if _c_data is None:
        if _g_data is not None:   # Claude 실패 → 검증 못 넘긴 Gemini 결과라도 돌려준다
            _gok, _giss = validate_receipt(_g_data)
            return _tag(_g_data, 'gemini', _giss, _gok), None
        # Gemini도 실패했다면 두 오류를 함께 보여준다 — Claude 오류만 뜨면
        # "Gemini 키를 넣었는데 왜 Claude 오류가?"를 진단할 수 없다.
        _c_msg = _err or (f"JSON 파싱 실패: {_txt[:200]}" if _txt else "빈 응답")
        if gemini_key and _g_err:
            return None, f"Gemini 실패({_g_err}) · Claude 실패({_c_msg})"
        return None, _c_msg

    _cok, _ciss = validate_receipt(_c_data)
    if _g_data is not None and not _cok:
        # 둘 다 검증 실패 → 경고가 적은 쪽 채택 (동률이면 Claude)
        _gok, _giss = validate_receipt(_g_data)
        if _gok or len(_giss) < len(_ciss):
            return _tag(_g_data, 'gemini', _giss, _gok), None
    return _tag(_c_data, 'claude', _ciss, _cok), None


_CAT_SYSTEM = (
    "너는 네이버 쇼핑 카테고리 분류 전문가다. 상품명과 '후보 카테고리 경로' 목록을 보고 "
    "그 상품에 가장 정확한 카테고리 경로 하나만 고른다.\n"
    "규칙: 반드시 후보 목록 중 하나를 골라 'A>B>C>D' 형식 **그대로** 한 줄만 출력한다. "
    "설명·번호·따옴표 등 다른 텍스트는 절대 붙이지 않는다."
)


_CAT_TERM_SYSTEM = (
    "너는 네이버 쇼핑 카테고리 분류 전문가다. 상품명을 보고 네이버 쇼핑 카테고리 트리에서 "
    "그 상품이 속할 만한 **카테고리 이름 후보**를 3개까지 추정한다.\n"
    "규칙: 브랜드·용량·수량은 무시하고 품목의 일반명으로 답한다. "
    "네이버 카테고리에 실제로 쓰이는 일반적인 단어를 쓴다(예: 참치액→액젓, 소갈비찜→갈비, "
    "프레스앤씰→랩, 콩국→두유/콩국). 쉼표로 구분해 한 줄만 출력하고 다른 텍스트는 붙이지 않는다."
)


def suggest_category_terms(product_name, api_key=None, *, gemini_key=None):
    """상품명 → 네이버 카테고리 검색어 후보(최대 3개). 반환: ([term], err).
    쇼핑검색 API 폐지 후 카테고리 후보를 만들기 위한 1단계."""
    _gk = resolve_gemini_key(gemini_key)
    if not (api_key or _gk):
        return [], "AI 키 없음"
    _txt, _err, _ = ai_complete(_CAT_TERM_SYSTEM, f"상품명: {product_name}",
                                gemini_key=_gk, anthropic_key=api_key, max_tokens=60)
    if _err or not _txt:
        return [], _err or "응답 없음"
    line = _txt.strip().splitlines()[0]
    terms = [t.strip().strip('"').strip() for t in line.split(",")]
    return [t for t in terms if 1 < len(t) <= 20][:3], None


def suggest_naver_category(api_key, product_name, candidate_paths, *, gemini_key=None):
    """상품명 + 쇼핑검색 후보 카테고리 경로들 → 최적 경로 1개 선택.
    키 없거나 실패 시 최빈(majority) 경로로 폴백. 반환: (path, err)."""
    from collections import Counter
    _uniq = list(dict.fromkeys([p for p in candidate_paths if p]))
    if not _uniq:
        return None, "후보 카테고리 없음"
    _majority = Counter([p for p in candidate_paths if p]).most_common(1)[0][0]
    _gk = resolve_gemini_key(gemini_key)
    if not (api_key or _gk):
        return _majority, None
    _msg = (f"상품명: {product_name}\n\n후보 카테고리 경로:\n"
            + "\n".join(f"- {p}" for p in _uniq))
    _txt, _err, _ = ai_complete(_CAT_SYSTEM, _msg, gemini_key=_gk,
                                anthropic_key=api_key, max_tokens=120)
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
    # 상품명은 품질 중요 → Sonnet, 단 사고(thinking) 끔(잘림/비용 방지)
    _txt, _err = claude_complete(api_key, _NAME_SYSTEM, _msg, max_tokens=200,
                                 model=VISION_MODEL, thinking={"type": "disabled"})
    if _err or not _txt:
        return _orig, _err
    # 줄바꿈을 공백으로 합침 (상품명은 한 줄) + 따옴표 제거. 너무 짧으면 원본 유지.
    _name = " ".join(str(_txt).split()).strip().strip('"').strip()
    if len(_name) < 4:
        return _orig, None
    return _name[:100], None


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
    _t, _e = claude_complete(api_key, _DESC_TEXT_SYSTEM, _msg, max_tokens=700,
                             model=VISION_MODEL, thinking={"type": "disabled"})
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
