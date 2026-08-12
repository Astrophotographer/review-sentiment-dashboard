"""
공통 유틸리티 함수 모음
----------------------
날짜 파싱, 텍스트 정규화, 해시 생성 등 여러 모듈에서 공통으로 쓰는 기능을 모아둔다.
"""
import hashlib
import re
from datetime import datetime, date
from typing import Optional


DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d-%m-%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"]


def normalize_text(text: str) -> str:
    """공백 정리, 앞뒤 공백 제거 등 기본적인 텍스트 정규화."""
    if text is None:
        return ""
    text = str(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_date(value) -> Optional[str]:
    """다양한 형식의 날짜 입력을 'YYYY-MM-DD' 문자열로 통일한다. 실패 시 None."""
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize_rating(value) -> Optional[int]:
    """별점을 1~5 범위의 정수로 검증한다. 범위를 벗어나거나 파싱 불가 시 None."""
    try:
        rating = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if 1 <= rating <= 5:
        return rating
    return None


def dedup_hash(text: str, product: Optional[str] = None) -> str:
    """중복 판정을 위한 해시. 정규화된 리뷰 텍스트 + 제품명 기준."""
    base = normalize_text(text).lower()
    if product:
        base += f"|{normalize_text(product).lower()}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def detect_language(text: str) -> str:
    """아주 단순한 휴리스틱 언어 감지: 한글 -> ko, (한글 없이) 한자 -> zh, 그 외 -> en
    (다국어 보너스 과제용, 한국어/영어/중국어 3개 언어 지원)."""
    t = text or ""
    if re.search(r"[가-힣]", t):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", t):
        return "zh"
    return "en"


# ── 감정 강도 등급 (5단계 점수) ─────────────────────────────────────────
# 긍정/부정/중립 3분류 + 신뢰도(confidence)를 조합해 "아주나쁨~아주좋음" 5단계 점수로
# 변환한다. 신뢰도는 "판단이 얼마나 확실한가"이고, 이 등급은 "감정이 얼마나 강한가"로
# 서로 다른 개념이다. 별도 AI 호출 없이 이미 저장된 sentiment/confidence로 계산한다.
SENTIMENT_GRADES = [
    {"score": 1, "label": "아주 나쁨", "color": "#C0392B"},
    {"score": 2, "label": "나쁨", "color": "#E5484D"},
    {"score": 3, "label": "보통", "color": "#9BA3B4"},
    {"score": 4, "label": "좋음", "color": "#5FBF8F"},
    {"score": 5, "label": "아주 좋음", "color": "#1FAF6B"},
]
_GRADE_BY_SCORE = {g["score"]: g for g in SENTIMENT_GRADES}


def sentiment_grade(sentiment: Optional[str], confidence: Optional[float], strong_threshold: float = 0.75) -> dict:
    """(sentiment, confidence) -> {"score": 1~5, "label": str, "color": str}
    confidence가 strong_threshold(기본 0.75) 이상이면 "아주" 단계, 미만이면 보통 단계로 나눈다.
    """
    if not sentiment or sentiment == "neutral":
        return _GRADE_BY_SCORE[3]
    conf = confidence if confidence is not None else 0.5
    if sentiment == "positive":
        return _GRADE_BY_SCORE[5] if conf >= strong_threshold else _GRADE_BY_SCORE[4]
    if sentiment == "negative":
        return _GRADE_BY_SCORE[1] if conf >= strong_threshold else _GRADE_BY_SCORE[2]
    return _GRADE_BY_SCORE[3]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
