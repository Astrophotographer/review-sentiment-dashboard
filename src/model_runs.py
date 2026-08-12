"""
모델 채점 스냅샷 헬퍼
---------------------
재분석 결과를 model_runs / model_run_results 에 남기고, 시드·라벨을 만든다.
"""
from __future__ import annotations

from typing import Optional

from .ai_client import AIClient
from .model_display import resolve_snapshot_model
from .utils import now_str


def run_label(provider: str, model: str) -> str:
    p = (provider or "unknown").lower()
    m = model or "-"
    names = {
        "spark": "Spark",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "fallback": "규칙 기반",
    }
    engine = names.get(p, p)
    if p == "fallback":
        return f"{engine} ({m}) · {now_str()}"
    return f"{engine} / {m} · {now_str()}"


def snapshot_after_analyze(db, config: dict, logger, ai_client: Optional[AIClient] = None) -> Optional[int]:
    """분석 결과가 1건 이상일 때 스냅샷을 저장한다. 전체 실패면 None."""
    stats = db.get_stats()
    if stats.get("analyzed", 0) <= 0:
        logger.warning("스냅샷 저장 생략: 분석된 리뷰가 없습니다.")
        return None

    ai = config.get("ai", {})
    provider = (ai.get("provider") or "anthropic").lower()
    if provider == "fallback":
        model_id = "규칙 기반"
    else:
        model_id = ai.get("sentiment_model") or ("qwen" if provider == "spark" else "unknown")

    temp_c = None
    health = None
    client = ai_client
    if provider == "spark":
        try:
            client = client or AIClient(config, logger)
            health = client.spark_device_status()
            if health.get("ok") and health.get("temp_c") is not None:
                temp_c = float(health["temp_c"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Spark 온도 기록 실패(스냅샷은 계속): {e}")

    display_model = resolve_snapshot_model(provider, model_id, health)

    run_id = db.save_model_run(
        provider=provider,
        model=display_model,
        label=run_label(provider, display_model),
        created_at=now_str(),
        temp_c=temp_c,
        notes=None,
    )
    logger.info(
        f"모델 스냅샷 저장: run_id={run_id} provider={provider} "
        f"model={display_model} (id={model_id})"
    )
    return run_id


def ensure_seed_snapshot(db, config: dict, logger) -> Optional[int]:
    ai = config.get("ai", {})
    provider = (ai.get("provider") or "fallback").lower()
    if provider == "fallback":
        model_id = "규칙 기반"
    else:
        model_id = ai.get("sentiment_model") or ("qwen" if provider == "spark" else "기존")
    display_model = resolve_snapshot_model(provider, model_id, None)
    names = {
        "spark": "Spark",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "fallback": "규칙 기반",
    }
    engine = names.get(provider, provider)
    label = f"{engine} / {display_model} · 시드(기존 분석)"
    run_id = db.seed_model_run_if_empty(provider, display_model, label, now_str())
    if run_id:
        logger.info(
            f"모델 스냅샷 시드 생성: run_id={run_id} provider={provider} model={display_model}"
        )
    return run_id
