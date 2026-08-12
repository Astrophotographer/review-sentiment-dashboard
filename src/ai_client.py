"""
AI API 클라이언트 모듈
----------------------
지원 provider:
  - anthropic : Anthropic Claude 공식 REST API
  - openai    : OpenAI 공식 API (OpenAI 호환 /v1/chat/completions)
  - spark     : DGX Spark vLLM (OpenAI 호환 /v1/chat/completions)
  - fallback  : 규칙 기반만 사용 (API 호출 없음)

- API 키/엔드포인트는 코드에 하드코딩하지 않고 config.json + 환경변수에서 읽는다.
- provider=fallback 이거나 키가 없으면 규칙 기반 폴백으로 동작한다.
- 키가 있는데 호출이 실패하면(크레딧 부족 등) 감정분석은 예외를 던져
  호출부(analyzer)가 해당 건을 스킵한다.
"""
import os
import re
import json
import requests
from typing import Optional

from .aspects import ASPECT_IDS, infer_aspects_from_text, normalize_aspects

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"

POSITIVE_HINTS = ["좋", "만족", "빠르", "편해", "훌륭", "추천", "예뻐", "친절", "가성비", "great", "good", "happy", "love"]
NEGATIVE_HINTS = ["불량", "늦", "실망", "안돼", "안됨", "불편", "느리", "나빠", "최악", "환불", "반품",
                  "disappoint", "defective", "bad", "slow", "broken"]


class AIClient:
    def __init__(self, config: dict, logger):
        self.logger = logger
        ai_cfg = config.get("ai", {})
        self.provider = (ai_cfg.get("provider") or "anthropic").strip().lower()
        self.api_key_env = ai_cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        self.api_key = os.environ.get(self.api_key_env, "").strip()
        self.spark_api_key_env = ai_cfg.get("spark_api_key_env", "SPARK_API_KEY")
        self.spark_api_key = os.environ.get(self.spark_api_key_env, "").strip()
        self.openai_api_key_env = ai_cfg.get("openai_api_key_env", "OPENAI_API_KEY")
        self.openai_api_key = os.environ.get(self.openai_api_key_env, "").strip()
        self.sentiment_model = ai_cfg.get("sentiment_model", "claude-haiku-4-5-20251001")
        self.extract_model = ai_cfg.get("extract_model", "claude-sonnet-5")
        self.max_tokens = ai_cfg.get("max_tokens", 1024)
        # extract는 긴 JSON 응답이 필요해서 analyze용 max_tokens를 그대로 쓰면
        # 응답이 중간에 잘려 파싱 실패 -> 조용히 폴백되는 문제가 있었다.
        self.extract_max_tokens = ai_cfg.get("extract_max_tokens", max(self.max_tokens * 4, 4096))
        self.timeout = ai_cfg.get("request_timeout_sec", 30)
        # extract는 리뷰 최대 200건을 한 프롬프트에 넣으므로 analyze용 타임아웃보다 넉넉히.
        self.extract_timeout = ai_cfg.get("extract_timeout_sec", max(self.timeout * 3, 120))
        self.base_url = (ai_cfg.get("base_url") or "http://127.0.0.1:8000/v1").rstrip("/")
        self.openai_base_url = (ai_cfg.get("openai_base_url") or OPENAI_DEFAULT_BASE).rstrip("/")
        self.enable_thinking = bool(ai_cfg.get("enable_thinking", False))
        self.spark_health_url = ai_cfg.get("spark_health_url", "http://100.114.218.1:8080/health")

        if self.provider == "fallback":
            self.available = False
            self.logger.warning("AI provider=fallback — 규칙 기반 분석만 사용합니다.")
        elif self.provider == "spark":
            self.available = bool(self.spark_api_key)
            if not self.available:
                self.logger.warning(
                    f"{self.spark_api_key_env} 환경변수가 설정되지 않았습니다. "
                    "Spark 호출 대신 규칙 기반 폴백 분석기를 사용합니다. "
                    f"Spark를 쓰려면 .env 에 {self.spark_api_key_env}=... 를 넣거나 "
                    f"export {self.spark_api_key_env}=... 후 다시 실행하세요."
                )
            else:
                self.logger.info(
                    f"AI provider=spark (OpenAI 호환) base_url={self.base_url} "
                    f"model={self.sentiment_model}"
                )
        elif self.provider == "openai":
            self.available = bool(self.openai_api_key)
            if not self.available:
                self.logger.warning(
                    f"{self.openai_api_key_env} 환경변수가 설정되지 않았습니다. "
                    "OpenAI 호출 대신 규칙 기반 폴백 분석기를 사용합니다. "
                    f"OpenAI를 쓰려면 .env 에 {self.openai_api_key_env}=... 를 넣으세요."
                )
            else:
                self.logger.info(
                    f"AI provider=openai base_url={self.openai_base_url} "
                    f"model={self.sentiment_model}"
                )
        else:
            self.provider = "anthropic"
            self.available = bool(self.api_key)
            if not self.available:
                self.logger.warning(
                    f"{self.api_key_env} 환경변수가 설정되지 않았습니다. "
                    "실제 AI 호출 대신 규칙 기반 폴백 분석기를 사용합니다. "
                    f"실제 AI 분석을 사용하려면: export {self.api_key_env}=sk-ant-xxxx "
                    "또는 config.json 에서 provider 를 spark / openai / fallback 으로 바꾸세요."
                )

    def _openai_compat_base_and_key(self):
        """(base_url, api_key, env_name) for OpenAI-compatible providers."""
        if self.provider == "openai":
            return self.openai_base_url, self.openai_api_key, self.openai_api_key_env
        return self.base_url, self.spark_api_key, self.spark_api_key_env

    # ---------------- 내부: LLM 호출 ----------------
    def _call_llm(self, model: str, system: str, user_prompt: str,
                   max_tokens: Optional[int] = None, timeout: Optional[float] = None) -> Optional[str]:
        if not self.available:
            return None
        if self.provider in ("spark", "openai"):
            return self._call_openai(model, system, user_prompt, max_tokens=max_tokens, timeout=timeout)
        return self._call_claude(model, system, user_prompt, max_tokens=max_tokens, timeout=timeout)

    def _call_claude(self, model: str, system: str, user_prompt: str,
                      max_tokens: Optional[int] = None, timeout: Optional[float] = None) -> Optional[str]:
        if not self.available:
            return None
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        used_max = max_tokens or self.max_tokens
        used_timeout = timeout or self.timeout
        payload = {
            "model": model,
            "max_tokens": used_max,
            "system": system,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        try:
            resp = requests.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=used_timeout)
            if resp.status_code != 200:
                self.logger.error(f"AI API 호출 실패 (status={resp.status_code}): {resp.text[:200]}")
                return None
            data = resp.json()
            if data.get("stop_reason") == "max_tokens":
                self.logger.warning(
                    f"AI 응답이 max_tokens({used_max}) 제한에 걸려 중간에 잘렸습니다. "
                    "JSON 파싱이 실패할 수 있습니다 (config.json의 max_tokens/extract_max_tokens를 늘려보세요)."
                )
            text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            return "\n".join(text_blocks).strip()
        except requests.exceptions.Timeout:
            self.logger.error(
                f"AI API 요청이 {used_timeout}초 안에 끝나지 않아 타임아웃되었습니다 "
                "(요청이 크거나 서버가 느린 경우 흔함 - config.json의 "
                "request_timeout_sec/extract_timeout_sec를 늘려보세요)."
            )
            return None
        except requests.RequestException as e:
            self.logger.error(f"AI API 요청 중 네트워크 오류: {e}")
            return None

    def _call_openai(self, model: str, system: str, user_prompt: str,
                      max_tokens: Optional[int] = None, timeout: Optional[float] = None) -> Optional[str]:
        """OpenAI / vLLM OpenAI 호환 chat completions."""
        base_url, key, env_name = self._openai_compat_base_and_key()
        headers = {"content-type": "application/json"}
        if not key:
            key = os.environ.get(env_name, "").strip()
        if not key:
            self.logger.error(f"{self.provider} 호출에 {env_name} 가 필요합니다.")
            return None
        headers["authorization"] = f"Bearer {key}"
        used_max = max_tokens or self.max_tokens
        used_timeout = timeout or self.timeout
        payload = {
            "model": model,
            "max_tokens": used_max,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.provider == "spark":
            payload["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        url = f"{base_url}/chat/completions"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=used_timeout)
            if resp.status_code != 200:
                self.logger.error(
                    f"{self.provider} API 호출 실패 (status={resp.status_code}): {resp.text[:200]}"
                )
                return None
            data = resp.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            content = msg.get("content")
            if content:
                return str(content).strip()
            # thinking 모델이 content 대신 reasoning 만 주는 경우 JSON 조각을 회수
            reasoning = msg.get("reasoning") or msg.get("reasoning_content")
            if reasoning:
                return str(reasoning).strip()
            return None
        except requests.RequestException as e:
            self.logger.error(f"{self.provider} API 요청 중 네트워크 오류: {e}")
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
        """리뷰 1건에 대해 overall sentiment + 측면(상품/배송/응대) 감정을 반환.

        반환: {
          'sentiment': 'positive|negative|neutral',
          'confidence': 0.0~1.0,
          'aspects': {'product'|'delivery'|'service': 'positive|negative|neutral|not_mentioned'}
        }

        - API 키가 아예 설정되지 않은 경우(또는 provider=fallback): 규칙 기반 폴백.
        - provider 가 활성화되어 있는데 호출이 실패한 경우: 예외를 발생시켜 스킵한다.
        """
        if not self.available:
            return self._fallback_sentiment(review_text)

        system = (
            "너는 전자상거래 고객 리뷰 감정 분석 전문가다. 주어진 리뷰(한국어 또는 영어)를 읽고 "
            "(1) 전체 감정을 positive, negative, neutral 중 하나로 분류하고 0.0~1.0 신뢰도를 매기며, "
            "(2) 만족도 측면별로 감정을 분류하라: product(상품), delivery(배송), service(응대/CS). "
            "해당 측면이 리뷰에 언급되지 않으면 not_mentioned. "
            "반드시 다른 설명 없이 JSON만 출력하라: "
            '{"sentiment":"positive|negative|neutral","confidence":0.0,'
            '"aspects":{"product":"positive|negative|neutral|not_mentioned",'
            '"delivery":"positive|negative|neutral|not_mentioned",'
            '"service":"positive|negative|neutral|not_mentioned"}}'
        )
        result = self._call_llm(self.sentiment_model, system, f"리뷰: {review_text}")
        parsed = self._extract_json(result) if result else None
        if parsed and parsed.get("sentiment") in ("positive", "negative", "neutral"):
            confidence = parsed.get("confidence", 0.75)
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.75
            aspects = normalize_aspects(parsed.get("aspects"))
            # LLM이 aspects를 빠뜨리면 규칙으로 보완
            if all(aspects[a] == "not_mentioned" for a in ASPECT_IDS):
                aspects = infer_aspects_from_text(review_text)
            return {
                "sentiment": parsed["sentiment"],
                "confidence": round(max(0.0, min(1.0, confidence)), 2),
                "aspects": aspects,
            }

        if result and not parsed:
            self.logger.error(f"AI 응답을 JSON으로 파싱하지 못했습니다. 원문 일부: {result[:200]!r}")

        raise RuntimeError(
            f"AI 감정분석 API 호출에 실패했습니다 (provider={self.provider} — "
            "크레딧 부족/인증오류/네트워크/터널 끊김 등 - logs/app.log 확인)"
        )

    @staticmethod
    def _fallback_sentiment(text: str) -> dict:
        t = (text or "").lower()
        pos = sum(1 for w in POSITIVE_HINTS if w.lower() in t)
        neg = sum(1 for w in NEGATIVE_HINTS if w.lower() in t)
        if pos > neg:
            overall = {"sentiment": "positive", "confidence": round(min(0.6 + 0.1 * (pos - neg), 0.95), 2)}
        elif neg > pos:
            overall = {"sentiment": "negative", "confidence": round(min(0.6 + 0.1 * (neg - pos), 0.95), 2)}
        else:
            overall = {"sentiment": "neutral", "confidence": 0.55}
        overall["aspects"] = infer_aspects_from_text(text)
        return overall

    # ---------------- 키워드/요약 추출 ----------------
    def extract_insights(self, reviews: list, condition_desc: str) -> dict:
        """리뷰 목록을 종합하여 긍정/부정 키워드, 요약, 개선 제안을 생성.
        실패해도 결과가 비지 않도록 규칙 기반 요약으로 대체한다."""
        if not self.available:
            self.logger.warning("AI provider 비가용 — 규칙 기반 키워드 추출로 대체합니다.")
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
            "topic_breakdown 은 부정/긍정 리뷰를 유형별로 묶어 건수와 대표 키워드를 제공하는 항목이다.\n"
            "응답이 너무 길어지지 않도록 반드시 지켜라: positive_keywords/negative_keywords는 "
            "각각 최대 5개, topic_breakdown은 최대 5개 유형까지만, 각 유형의 examples는 최대 3개까지만 "
            "포함하라. summary는 4문장을 넘기지 마라."
        )
        joined = "\n".join(
            f"- ({r.get('sentiment','?')}, {r.get('rating','?')}점) {r.get('review_text','')}"
            for r in reviews[:200]
        )
        user_prompt = f"[분석 조건: {condition_desc}]\n리뷰 목록:\n{joined}"

        parsed = None
        result = None
        for attempt in range(2):
            result = self._call_llm(
                self.extract_model, system, user_prompt,
                max_tokens=self.extract_max_tokens, timeout=self.extract_timeout,
            )
            parsed = self._extract_json(result) if result else None
            if parsed:
                return parsed
            if attempt == 0:
                self.logger.warning("AI 추출 첫 시도가 실패해 한 번 더 재시도합니다...")

        if result and not parsed:
            self.logger.error(f"AI 추출 응답을 JSON으로 파싱하지 못했습니다. 원문 일부: {result[:300]!r}")

        self.logger.warning(
            f"AI 키워드/요약 추출 호출이 실패해 규칙 기반 결과로 대체합니다 "
            f"(provider={self.provider} — logs/app.log 확인)."
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

        topic_map = {
            "배송": ["늦", "배송"],
            "품질": ["불량", "나빠", "최악"],
            "서비스": ["불편", "안돼", "안됨"],
            "가격/기타": ["실망", "환불", "반품"],
        }
        topic_breakdown = []
        for topic, hints in topic_map.items():
            count = sum(
                1 for r in reviews
                if r.get("sentiment") == "negative" and any(h in (r.get("review_text") or "") for h in hints)
            )
            if count:
                topic_breakdown.append({"topic": topic, "count": count, "examples": hints})

        return {
            "positive_keywords": [{"keyword": w, "count": c} for w, c in pos_sorted] or [{"keyword": "데이터 부족", "count": 0}],
            "negative_keywords": [{"keyword": w, "count": c} for w, c in neg_sorted] or [{"keyword": "데이터 부족", "count": 0}],
            "summary": f"총 {len(reviews)}건의 리뷰를 규칙 기반으로 요약했습니다. "
                       f"(Spark/OpenAI/Anthropic provider 를 선택하면 AI 요약을 받을 수 있습니다.)",
            "suggestions": ["대시보드에서 채점 모델을 Spark(qwen) 또는 OpenAI 등으로 바꾼 뒤 재분석해 보세요."],
            "topic_breakdown": topic_breakdown,
        }

    def list_remote_models(self) -> list:
        """OpenAI 호환 /models 에서 사용 가능한 모델 id 목록을 가져온다."""
        if self.provider not in ("spark", "openai"):
            return [self.sentiment_model]
        base_url, key, _env = self._openai_compat_base_and_key()
        headers = {}
        if key:
            headers["authorization"] = f"Bearer {key}"
        try:
            resp = requests.get(f"{base_url}/models", headers=headers or None, timeout=5)
            if resp.status_code != 200:
                return [self.sentiment_model]
            data = resp.json()
            ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
            return ids or [self.sentiment_model]
        except requests.RequestException:
            return [self.sentiment_model]

    def spark_device_status(self) -> dict:
        """Spark 봇 health 엔드포인트에서 기기 온도 등을 조회한다."""
        try:
            resp = requests.get(self.spark_health_url, timeout=5)
            if resp.status_code != 200:
                return {"ok": False, "error": f"HTTP {resp.status_code}"}
            data = resp.json()
            temp = data.get("temp_c")
            if temp is None:
                temp = data.get("temperature")
            if temp is None and isinstance(data.get("gpu"), dict):
                temp = data["gpu"].get("temp_c") or data["gpu"].get("temperature")
            try:
                temp = float(temp) if temp is not None and temp != "" else None
            except (TypeError, ValueError):
                temp = None
            return {
                "ok": bool(data.get("ok", True)),
                "temp_c": temp,
                "model": data.get("model"),
                "raw": data,
            }
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}
