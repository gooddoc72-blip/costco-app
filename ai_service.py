"""AI 서비스 — Claude API 연동 (정산 브리핑 등).

anthropic SDK 없이 HTTPS 직접 호출 (서버 의존성 최소화).
API 키는 사용자 설정 'anthropic_api_key' (설정 탭 > AI 설정).
"""
import json
import requests
from datetime import datetime, timedelta

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"   # 저비용 — 브리핑 1회 ≈ 1원 미만


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
