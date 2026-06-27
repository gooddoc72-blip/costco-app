"""AI 자동분류 (세무회계 ②) — Claude API로 통장/카드 거래의 계정과목을 분류.

거래 적요+입출금을 보고 계정과목/매입세액공제/사업용 여부를 분류한다.
개인정보 최소화: 적요·금액만 전송(계좌번호 등은 저장/전송 안 함).
"""
import json
import re

import requests

# 표준 계정과목 (소규모 쇼핑몰 기준)
ACCOUNTS = [
    "상품매출", "상품매출원가", "지급수수료", "운반비", "포장비", "광고선전비",
    "소모품비", "통신비", "임차료", "접대비", "세금과공과", "보험료",
    "수도광열비", "차량유지비", "이자비용", "급여", "복리후생비", "잡비",
    "사업무관/개인",
]

_SYSTEM = (
    "너는 한국 소규모 쇼핑몰 사업자의 회계 분류 비서다. 각 거래의 적요와 입출금 금액을 보고 "
    "가장 적절한 계정과목 하나를 고른다.\n"
    f"계정과목은 반드시 다음 중 하나만 사용: {', '.join(ACCOUNTS)}\n"
    "규칙:\n"
    "- 입금이고 정산/매출/스마트스토어/쿠팡 정산이면 '상품매출'\n"
    "- 광고/마케팅 → '광고선전비', 택배/배송/CJ/로젠/한진 → '운반비', 박스/포장 → '포장비'\n"
    "- 통신/인터넷/휴대폰 → '통신비', 임대/월세 → '임차료', 보험 → '보험료'\n"
    "- 마트/도매/사입/상품매입 → '상품매출원가', 플랫폼 수수료 → '지급수수료'\n"
    "- 세금/4대보험/공과금 → '세금과공과', 식대/회식 → '복리후생비' 또는 '접대비'\n"
    "- 사업과 무관해 보이면 '사업무관/개인'\n"
    "vat_deductible: 세금계산서·사업용카드 매입으로 매입세액공제가 가능하면 1, 아니면 0(면세·개인·인건비 등).\n"
    "biz_use: 사업용이면 1, 개인용이면 0.\n"
    "반드시 JSON 배열만 출력(설명 금지): "
    '[{"id":번호,"category":"계정과목","vat_deductible":0,"biz_use":1}]'
)


def classify_transactions(api_key: str, txs: list,
                          model: str = "claude-haiku-4-5-20251001") -> dict:
    """txs: [{id, description, amount_in, amount_out, source_type}, ...]
    Returns: {str(id): {category, vat_deductible, biz_use}} 또는 {'_error': msg}
    """
    if not api_key:
        return {"_error": "Anthropic API 키가 없습니다. 설정에서 입력하세요."}
    if not txs:
        return {}
    items = [{
        "id": t["id"],
        "적요": (t.get("description") or "")[:60],
        "입금": int(t.get("amount_in") or 0),
        "출금": int(t.get("amount_out") or 0),
        "구분": "카드" if t.get("source_type") == "card" else "통장",
    } for t in txs]
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 8000,
                "system": _SYSTEM,
                "messages": [{"role": "user",
                              "content": json.dumps(items, ensure_ascii=False)}],
            },
            timeout=90,
        )
    except Exception as e:
        return {"_error": f"API 호출 실패: {str(e)[:150]}"}
    if resp.status_code != 200:
        return {"_error": f"API 오류 {resp.status_code}: {resp.text[:200]}"}
    try:
        text = resp.json()["content"][0]["text"]
        m = re.search(r"\[.*\]", text, re.DOTALL)
        arr = json.loads(m.group(0)) if m else []
    except Exception as e:
        return {"_error": f"응답 파싱 실패: {str(e)[:150]}"}
    out = {}
    for x in arr:
        try:
            cat = str(x.get("category", "") or "")
            if cat not in ACCOUNTS:
                cat = "잡비"
            out[str(x["id"])] = {
                "category": cat,
                "vat_deductible": int(x.get("vat_deductible", 0) or 0),
                "biz_use": int(x.get("biz_use", 1) or 0),
            }
        except Exception:
            continue
    return out
