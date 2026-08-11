"""
만족도 측면(Aspect) 정의
------------------------
고정 3축: 상품 / 배송 / 응대
값: positive | negative | neutral | not_mentioned
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

ASPECTS = (
    {"id": "product", "label": "상품 만족도"},
    {"id": "delivery", "label": "배송 만족도"},
    {"id": "service", "label": "응대 만족도"},
)
ASPECT_IDS = [a["id"] for a in ASPECTS]
ASPECT_LABEL = {a["id"]: a["label"] for a in ASPECTS}
VALID = {"positive", "negative", "neutral", "not_mentioned"}

# 측면 언급 단서 (부분 문자열)
ASPECT_CUES = {
    "product": [
        "상품", "제품", "기능", "사용감", "디자인", "성능", "품질", "완성도",
        "음질", "배터리", "착용", "결함", "사진과", "product", "feature", "item",
        "quality", "unfinished",
    ],
    "delivery": [
        "배송", "포장", "도착", "오배송", "택배", "박스", "delivery", "shipping",
        "package", "box was", "왔", "와서",
    ],
    "service": [
        "응대", "상담", "고객센터", "cs", "문의", "교환", "환불", "상담원",
        "채팅", "답변", "답도", "답이", "support", "service", "reply", "replied",
    ],
}

POS_CUES = [
    "좋", "만족", "훌륭", "완벽", "최고", "기대 이상", "기대이상",
    "친절해서", "친절하", "친절했", "빠르", "빨랐", "빨라", "빨리", "매끄럽", "꼼꼼", "믿음", "해결",
    "excellent", "great", "good", "happy", "fast", "kind", "promised",
]
NEG_CUES = [
    "늦", "실망", "불량", "최악", "안돼", "안 되", "불친절", "느리", "고장",
    "결함", "떨어", "바닥", "찌그러", "다르", "못 미", "회피", "끊겨", "없어요", "안 되고",
    "bad", "slow", "poor", "broken", "worst", "damaged", "disappointed",
    "unfinished", "week", "never replied",
]

# 긍정 단서가 부정 표현 안에 섞여 잡히는 경우 제외
POS_FALSE_FRIENDS = ("불친절", "안좋", "안 좋", "좋지 않", "좋지않")


def empty_aspects() -> Dict[str, str]:
    return {aid: "not_mentioned" for aid in ASPECT_IDS}


def normalize_aspects(raw: Any) -> Dict[str, str]:
    out = empty_aspects()
    if not isinstance(raw, dict):
        return out
    for aid in ASPECT_IDS:
        val = raw.get(aid)
        if isinstance(val, dict):
            val = val.get("sentiment") or val.get("label")
        if val in VALID:
            out[aid] = val
    return out


def _score_span(span: str) -> tuple[int, int]:
    t = span.lower()
    neg = sum(1 for w in NEG_CUES if w.lower() in t)
    pos = 0
    for w in POS_CUES:
        wl = w.lower()
        if wl not in t:
            continue
        if any(ff in t for ff in POS_FALSE_FRIENDS) and wl in ("친절해서", "친절하", "친절했", "좋"):
            # 불친절/안좋 등에 흡수된 긍정 단서는 무시
            if wl == "좋" and ("안좋" in t or "안 좋" in t or "좋지" in t):
                continue
            if wl.startswith("친절") and "불친절" in t:
                continue
        pos += 1
    return pos, neg


def _label_from_score(pos: int, neg: int) -> str:
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def infer_aspects_from_text(text: str) -> Dict[str, str]:
    """규칙 기반: 측면 단서가 있는 문장/구간의 긍정·부정 단서로 판정."""
    raw = text or ""
    t = raw.lower()
    aspects = empty_aspects()
    # 문장 단위로 잘라 측면별로 가까운 문장만 점수
    sentences = [s.strip() for s in re.split(r"[.!?。\n]+", raw) if s.strip()]
    if not sentences:
        sentences = [raw]

    for aid, cues in ASPECT_CUES.items():
        cue_l = [c.lower() for c in cues]
        if not any(c in t for c in cue_l):
            continue
        relevant = [s for s in sentences if any(c in s.lower() for c in cue_l)]
        if not relevant:
            relevant = sentences
        pos = neg = 0
        for span in relevant:
            p, n = _score_span(span)
            pos += p
            neg += n
        # 측면별 강한 단서 보정
        joined = " ".join(relevant).lower()
        if aid == "delivery":
            if any(w in joined for w in ["늦", "오배송", "찌그러", "week", "damaged", "최악"]):
                neg += 2
            if any(w in joined for w in ["빨랐", "빨라", "빨리", "완벽", "꼼꼼", "다음날"]):
                pos += 2
        if aid == "service":
            if any(w in joined for w in ["연결이 안", "답도 없", "불친절", "회피", "바닥", "최악", "never replied"]):
                neg += 2
            if any(w in joined for w in ["친절", "매끄럽", "바로 해결", "믿음"]):
                pos += 2
        if aid == "product":
            if any(w in joined for w in ["기대에 못", "결함", "실망", "unfinished", "다르", "떨어"]):
                neg += 2
            if any(w in joined for w in ["만족", "기대 이상", "완성도", "excellent", "훌륭", "최고"]):
                pos += 2
        aspects[aid] = _label_from_score(pos, neg)
    return aspects


def aspects_to_json(aspects: Dict[str, str]) -> str:
    return json.dumps(normalize_aspects(aspects), ensure_ascii=False)


def aspects_from_json(raw: Optional[str]) -> Dict[str, str]:
    if not raw:
        return empty_aspects()
    try:
        return normalize_aspects(json.loads(raw))
    except (TypeError, json.JSONDecodeError):
        return empty_aspects()
