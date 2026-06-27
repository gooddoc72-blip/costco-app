"""국세청 사업자등록 상태조회 (공공데이터포털 API).

사업자번호로 납세상태(계속/휴업/폐업)·과세유형(일반/간이/면세)을 조회.
serviceKey는 data.go.kr '국세청_사업자등록정보 진위확인 및 상태조회'에서 무료 발급.
"""
import requests

_URL = "https://api.odcloud.kr/api/nts-businessman/v1/status"


def check_business_status(service_key: str, b_no: str) -> dict:
    """사업자 상태조회. b_no: 사업자번호(하이픈 무관).
    Returns: dict(b_no, b_stt, tax_type, ...) 또는 {'_error': msg}
    """
    bno = "".join(c for c in str(b_no or "") if c.isdigit())
    if len(bno) != 10:
        return {"_error": "사업자등록번호 10자리를 입력하세요."}
    if not service_key:
        return {"_error": "공공데이터포털 서비스키가 없습니다. (data.go.kr 발급)"}
    try:
        r = requests.post(_URL, params={"serviceKey": service_key},
                          json={"b_no": [bno]}, timeout=20)
    except Exception as e:
        return {"_error": f"호출 실패: {str(e)[:150]}"}
    if r.status_code != 200:
        return {"_error": f"오류 {r.status_code}: {r.text[:200]}"}
    try:
        data = r.json().get("data") or []
    except Exception:
        return {"_error": "응답 파싱 실패"}
    if not data:
        return {"_error": "조회 결과 없음 (번호 확인)"}
    return data[0]


def map_tax_type(tax_type: str) -> str:
    """국세청 tax_type 문자열 → 앱의 사업자유형."""
    t = str(tax_type or "")
    if "법인" in t:
        return "법인"
    if "간이" in t:
        return "개인 간이과세자"
    if "면세" in t:
        return "개인 일반과세자"  # 면세는 별도 옵션 없어 일반으로(표기는 상태로 안내)
    if "일반" in t:
        return "개인 일반과세자"
    return ""
