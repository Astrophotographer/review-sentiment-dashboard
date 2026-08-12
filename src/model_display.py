"""모델 id → 화면 표시명 정리."""
from __future__ import annotations

import re
from typing import Optional


_DATE_TOKEN = re.compile(r"^20\d{6}$")  # YYYYMMDD
_YEAR_TOKEN = re.compile(r"^20\d{2}$")  # 2024–2099


def format_model_display(name: Optional[str]) -> str:
    """호출용 model id를 사람이 읽기 쉬운 표시명으로 바꾼다.

    - '-' / '_' 를 공백으로 분리
    - 끝의 연도·YYYYMMDD 토큰 제거
    - 연속된 단일 숫자 버전 조각은 '.' 으로 합침 (4 5 → 4.5)
    """
    raw = (name or "").strip()
    if not raw:
        return "-"

    spaced = raw.replace("_", " ").replace("-", " ")
    parts = [p for p in spaced.split() if p]
    cleaned = []
    for p in parts:
        if _DATE_TOKEN.match(p) or _YEAR_TOKEN.match(p):
            continue
        cleaned.append(p)

    if not cleaned:
        return raw

    merged: list[str] = []
    i = 0
    while i < len(cleaned):
        if (
            i + 1 < len(cleaned)
            and re.fullmatch(r"\d+", cleaned[i])
            and re.fullmatch(r"\d+", cleaned[i + 1])
        ):
            ver = [cleaned[i], cleaned[i + 1]]
            j = i + 2
            while j < len(cleaned) and re.fullmatch(r"\d+", cleaned[j]):
                ver.append(cleaned[j])
                j += 1
            if len(ver) <= 3:
                merged.append(".".join(ver))
                i = j
                continue
        merged.append(cleaned[i])
        i += 1

    return " ".join(merged)


def resolve_snapshot_model(
    provider: str,
    model_id: str,
    spark_health: Optional[dict] = None,
) -> str:
    """스냅샷/UI에 넣을 표시용 모델명."""
    p = (provider or "").lower()
    mid = (model_id or "").strip()
    if p == "spark" and spark_health:
        health_model = (spark_health.get("model") or "").strip()
        if health_model:
            return format_model_display(health_model)
    if p == "fallback":
        return mid or "규칙 기반"
    return format_model_display(mid or "-")
