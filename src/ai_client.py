"""
AI API 클라이언트 모듈
----------------------
Anthropic Claude API(공식 REST 엔드포인트, requests 직접 호출)를 사용하여
1) 리뷰 감정 분석  2) 키워드/요약/개선제안 추출 을 수행한다.

- API 키는 코드에 하드코딩하지 않고 환경변수(config.json의 ai.api_key_env 로 지정)에서 읽는다.
- API 키가 없거나 호출에 실패하면, 실습/데모가 끊기지 않도록 규칙 기반 폴백(fallback) 분석기로
  자동 전환한다 (경고 로그 남김). 실제 채점/운영 환경에서는 환경변수에 유효한 키를 설정하면
  자동으로 실제 AI 분석으로 전환된다.
"""
import os
import re
import json
import time
import requests
from typing import Optional

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

POSITIVE_HINTS = ["좋", "만족", "빠르", "편해", "훌륭", "추천", "예뻐", "친절", "가성비", "great", "good", "happy", "love"]
NEGATIVE_HINTS = ["불량", "늦", "실망", "안돼", "안됨", "불편", "느리", "나빠", "최악", "환불", "반품",
                  "disappoint", "defective", "bad", "slow", "broken"]


class AIClient:
    def __init__(self, config: dict, logger):
        self.logger = logger
        ai_cfg = config.get("ai", {})
        self.api_key = os.environ.get(ai_cfg.get("api_key_env", "ANTHROPIC_API_KEY"), "").strip()
        self.sentiment_model = ai_cfg.get("sentiment_model", "claude-haiku-4-5-20251001")
        self.extract_model = ai_cfg.get("extract_model", "claude-sonnet-5")
        self.max_tokens = ai_cfg.get("max_tokens", 1024)
        # extract는 키워드/요약/유형별집계 등 훨씬 긴 JSON 응답이 필요해서, analyze용
        # max_tokens를 그대로 쓰면 응답이 중간에 잘려 파싱 실패 -> 조용히 폴백되는
        # 문제가 있었다. 그래서 extract 전용으로 더 넉넉한 값을 따로 둔다.
        self.extract_max_tokens = ai_cfg.get("extract_max_tokens", max(self.max_tokens * 2, 2048))
        self.timeout = ai_cfg.get("request_timeout_sec", 30)
        self.available = bool(self.api_key)
        if not self.available:
            self.logger.warning(
                "ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다. "
                "실제 AI 호출 대신 규칙 기반 폴백 분석기를 사용합니다. "
                "실제 AI 분석을 사용하려면: export ANTHROPIC_API_KEY=sk-ant-xxxx"
            )

    # ---------------- 내부: 실제 API 호출 ----------------
    def _call_claude(self, model: str, system: str, user_prompt: str, max_tokens: Optional[int] = None) -> Optional[str]:
        if not self.available:
            return None
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        try:
            resp = requests.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=self.timeout)
            if resp.status_code != 200:
                self.logger.error(f"AI API 호출 실패 (status={resp.status_code}): {resp.text[:200]}")
                return None
            data = resp.json()
            if data.get("stop_reason") == "max_tokens":
                self.logger.warning(
                    f"AI 응답이 max_tokens({max_tokens or self.max_tokens}) 제한에 걸려 중간에 잘렸습니다. "
                    "JSON 파싱이 실패할 수 있습니다 (config.json의 max_tokens/extract_max_tokens를 늘려보세요)."
                )
            text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            return "\n".join(text_blocks).strip()
        except requests.RequestException as e:
            self.logger.error(f"AI API 요청 중 네트워크 오류: {e}")
            return None

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        if not text:
            return None
        text = text.strip()
        text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
        return None

    # ---------------- 감정 분석 ----------------
    def analyze_sentiment(self, review_text: str, language: str = "ko") -> dict:
        """리뷰 1건에 대해 {'sentiment': 'positive|negative|neutral', 'confidence': 0.0~1.0} 반환.

        - API 키가 아예 설정되지 않은 경우: 의도된 폴백(데모/개발용 안전장치)으로 규칙 기반 결과를 반환한다.
        - API 키는 있는데 호출 자체가 실패한 경우(크레딧 부족, 인증 오류, 네트워크 오류 등):
          과제 요구사항 "API 실패 시 로깅 후 스킵"을 그대로 따르기 위해 예외를 발생시킨다.
          (호출부인 analyzer.py 가 이를 잡아서 로깅하고 해당 리뷰를 건너뛴다.)
        """
        if not self.available:
            return self._fallback_sentiment(review_text)

        system = (
            "너는 전자상거래 고객 리뷰 감정 분석 전문가다. 주어진 리뷰(한국어 또는 영어)를 읽고 "
            "감정을 positive, negative, neutral 중 하나로 분류하고 0.0~1.0 사이의 신뢰도 점수를 매겨라. "
            "반드시 다른 설명 없이 JSON만 출력하라: {\"sentiment\": \"positive|negative|neutral\", \"confidence\": 0.0}"
        )
        result = self._call_claude(self.sentiment_model, system, f"리뷰: {review_text}")
        parsed = self._extract_json(result) if result else None
        if parsed and parsed.get("sentiment") in ("positive", "negative", "neutral"):
            confidence = parsed.get("confidence", 0.75)
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.75
            return {"sentiment": parsed["sentiment"], "confidence": round(max(0.0, min(1.0, confidence)), 2)}

        if result and not parsed:
            # HTTP 호출 자체는 성공했지만(200 OK) 응답을 JSON으로 못 읽은 경우.
            # 이전에는 아무 로그도 안 남기고 조용히 실패 처리되어 원인 파악이 불가능했다.
            self.logger.error(f"AI 응답을 JSON으로 파싱하지 못했습니다. 원문 일부: {result[:200]!r}")

        # API 키는 설정되어 있지만 호출/파싱이 실패한 경우 -> 조용히 넘어가지 않고 진짜 실패로 처리한다.
        raise RuntimeError("AI 감정분석 API 호출에 실패했습니다 (크레딧 부족/인증오류/네트워크 오류 등 - logs/app.log 확인)")

    @staticmethod
    def _fallback_sentiment(text: str) -> dict:
        t = (text or "").lower()
        pos = sum(1 for w in POSITIVE_HINTS if w.lower() in t)
        neg = sum(1 for w in NEGATIVE_HINTS if w.lower() in t)
        if pos > neg:
            return {"sentiment": "positive", "confidence": round(min(0.6 + 0.1 * (pos - neg), 0.95), 2)}
        if neg > pos:
            return {"sentiment": "negative", "confidence": round(min(0.6 + 0.1 * (neg - pos), 0.95), 2)}
        return {"sentiment": "neutral", "confidence": 0.55}

    # ---------------- 키워드/요약 추출 ----------------
    def extract_insights(self, reviews: list, condition_desc: str) -> dict:
        """리뷰 목록을 종합하여 긍정/부정 키워드, 요약, 개선 제안을 생성.
        감정분석(analyze)과 달리 extract는 "실패 시 스킵" 요구사항이 명시되어 있지 않아,
        실패해도 결과가 아예 비지 않도록 규칙 기반 요약으로 대체한다. 다만 그 사실을
        숨기지 않고 명확한 WARNING 로그로 남긴다."""
        if not self.available:
            self.logger.warning("ANTHROPIC_API_KEY가 없어 규칙 기반 키워드 추출로 대체합니다.")
            return self._fallback_extract(reviews)

        system = (
            "너는 커머스 VOC(고객의 소리) 분석가다. 주어진 리뷰 목록을 종합 분석하여 아래 JSON 스키마로만 답하라. "
            "다른 설명 문장은 절대 포함하지 마라.\n"
            "{\n"
            '  "positive_keywords": [{"keyword": "빠른 배송", "count": 23}, ...],\n'
            '  "negative_keywords": [{"keyword": "배송 지연", "count": 8}, ...],\n'
            '  "summary": "전체 리뷰에 대한 2~4문장 요약",\n'
            '  "suggestions": ["개선 제안1", "개선 제안2"],\n'
            '  "topic_breakdown": [{"topic": "배송", "count": 9, "examples": ["배송 지연", "오배송"]}]\n'
            "}\n"
            "positive_keywords/negative_keywords 의 keyword는 \"배송 지연\", \"품질 불량\"처럼 "
            "단어 하나가 아니라 의미가 통하는 2~3어절 구(句)로 만들고, count는 해당 키워드가 "
            "리뷰들에서 실제로 언급된(또는 그와 같은 취지의) 횟수를 세어 넣어라. 키워드는 count 내림차순으로 정렬하라.\n"
            "topic_breakdown 은 부정/긍정 리뷰를 유형별로 묶어 건수와 대표 키워드를 제공하는 항목이다."
        )
        joined = "\n".join(f"- ({r.get('sentiment','?')}, {r.get('rating','?')}점) {r.get('review_text','')}" for r in reviews[:200])
        user_prompt = f"[분석 조건: {condition_desc}]\n리뷰 목록:\n{joined}"
        result = self._call_claude(self.extract_model, system, user_prompt, max_tokens=self.extract_max_tokens)
        parsed = self._extract_json(result) if result else None
        if parsed:
            return parsed

        if result and not parsed:
            # HTTP 호출 자체는 성공했지만(200 OK) 응답을 JSON으로 못 읽은 경우
            # (흔한 원인: max_tokens 제한에 걸려 응답이 중간에 잘림). 원인 파악이 되도록
            # 원문 일부를 남긴다.
            self.logger.error(f"AI 추출 응답을 JSON으로 파싱하지 못했습니다. 원문 일부: {result[:300]!r}")

        self.logger.warning(
            "AI 키워드/요약 추출 호출이 실패해 규칙 기반 결과로 대체합니다 "
            "(크레딧 부족/인증오류/네트워크 오류/응답 잘림 등 - logs/app.log 확인)."
        )
        return self._fallback_extract(reviews)

    @staticmethod
    def _fallback_extract(reviews: list) -> dict:
        pos_words, neg_words = {}, {}
        for r in reviews:
            text = (r.get("review_text") or "")
            bucket = pos_words if r.get("sentiment") == "positive" else neg_words if r.get("sentiment") == "negative" else None
            if bucket is None:
                continue
            for hint in (POSITIVE_HINTS if bucket is pos_words else NEGATIVE_HINTS):
                if hint.lower() in text.lower():
                    bucket[hint] = bucket.get(hint, 0) + 1
        pos_sorted = sorted(pos_words.items(), key=lambda x: -x[1])[:5]
        neg_sorted = sorted(neg_words.items(), key=lambda x: -x[1])[:5]

        # 간단한 유형(topic) 집계: 부정 키워드를 대략적인 카테고리로 묶는다 (규칙 기반 데모용)
        topic_map = {
            "배송": ["늦", "배송"],
            "품질": ["불량", "나빠", "최악"],
            "서비스": ["불편", "안돼", "안됨"],
            "가격/기타": ["실망", "환불", "반품"],
        }
        topic_breakdown = []
        for topic, hints in topic_map.items():
            count = sum(1 for r in reviews if r.get("sentiment") == "negative" and
                        any(h in (r.get("review_text") or "") for h in hints))
            if count:
                topic_breakdown.append({"topic": topic, "count": count, "examples": hints})

        return {
            "positive_keywords": [{"keyword": w, "count": c} for w, c in pos_sorted] or [{"keyword": "데이터 부족", "count": 0}],
            "negative_keywords": [{"keyword": w, "count": c} for w, c in neg_sorted] or [{"keyword": "데이터 부족", "count": 0}],
            "summary": f"총 {len(reviews)}건의 리뷰를 규칙 기반으로 요약했습니다. "
                       f"(실제 AI 요약을 원하면 ANTHROPIC_API_KEY를 설정하세요.)",
            "suggestions": ["ANTHROPIC_API_KEY 설정 후 재실행하면 더 정교한 AI 인사이트를 받을 수 있습니다."],
            "topic_breakdown": topic_breakdown,
        }
