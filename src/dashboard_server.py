"""
로컬 대시보드 서버
------------------
정적 HTML(output/dashboard.html)을 띄우면서, 채점 모델 선택·Spark 온도·재분석 API를 제공한다.

사용:
  python main.py serve --port 8765
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests

from .ai_client import AIClient
from . import analyzer, extractor, alerts, visualizer, reporter, model_runs, ingest, cleaner, envfile
from .db import Database
from .model_display import resolve_snapshot_model


PROVIDER_OPTIONS = [
    {"id": "spark", "label": "Spark (vLLM)", "needs_model": True},
    {"id": "openai", "label": "OpenAI", "needs_model": True},
    {"id": "gemini", "label": "Google Gemini", "needs_model": True},
    {"id": "anthropic", "label": "Anthropic Claude", "needs_model": True},
    {"id": "fallback", "label": "규칙 기반 폴백", "needs_model": False},
]

PROVIDER_KEY_ENV = {
    "spark": ("spark_api_key_env", "SPARK_API_KEY"),
    "openai": ("openai_api_key_env", "OPENAI_API_KEY"),
    "gemini": ("gemini_api_key_env", "GEMINI_API_KEY"),
    "anthropic": ("api_key_env", "ANTHROPIC_API_KEY"),
}

# Spark 는 대시보드에서 키 삭제/수정 바를 열지 않음 (없을 때만 입력)
EDITABLE_KEY_PROVIDERS = {"openai", "gemini", "anthropic"}

DEFAULT_MODELS = {
    "spark": "qwen",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "anthropic": "claude-sonnet-5",
    "fallback": "규칙 기반",
}


def model_fits_provider(provider: str, model: Optional[str]) -> bool:
    """다른 엔진의 모델 id가 섞여 저장되지 않게 한다."""
    m = (model or "").strip().lower()
    p = (provider or "").strip().lower()
    if not m:
        return False
    if p == "fallback":
        return m in ("규칙 기반", "fallback", "rule")
    if p == "anthropic":
        return m.startswith("claude")
    if p == "openai":
        return m.startswith("gpt-") or m.startswith(("o1", "o3", "o4", "chatgpt"))
    if p == "gemini":
        return "gemini" in m
    if p == "spark":
        if m.startswith(("claude", "gpt-", "o1", "o3", "o4", "chatgpt")) or "gemini" in m:
            return False
        return True
    return True


def resolve_provider_model(provider: str, model: Optional[str], last_models: Optional[dict] = None) -> str:
    if model_fits_provider(provider, model):
        return (model or "").strip()
    remembered = (last_models or {}).get(provider)
    if model_fits_provider(provider, remembered):
        return remembered
    return DEFAULT_MODELS.get(provider, "qwen")

UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # 20MB
UPLOAD_ALLOWED_EXT = {".csv", ".xlsx", ".xls"}


class _JobState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.done = False
        self.error: Optional[str] = None
        self.message = ""
        self.success = 0
        self.failed = 0
        self.kind = ""  # analyze | upload
        self.imported = 0
        self.skipped = 0

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "done": self.done,
                "error": self.error,
                "message": self.message,
                "success": self.success,
                "failed": self.failed,
                "kind": self.kind,
                "imported": self.imported,
                "skipped": self.skipped,
            }


def _read_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_config(path: str, config: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _anthropic_models(config: dict) -> list:
    ai = config.get("ai", {})
    models = []
    for key in ("sentiment_model", "extract_model"):
        m = ai.get(key)
        if m and model_fits_provider("anthropic", m) and m not in models:
            models.append(m)
    for m in ("claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-sonnet-4-20250514"):
        if m not in models:
            models.append(m)
    return models


def _openai_models(config: dict, logger) -> list:
    client = AIClient(config, logger)
    client.provider = "openai"
    client.available = True
    client.openai_base_url = (
        config.get("ai", {}).get("openai_base_url") or "https://api.openai.com/v1"
    ).rstrip("/")
    client.openai_api_key = os.environ.get(
        config.get("ai", {}).get("openai_api_key_env", "OPENAI_API_KEY"), ""
    ).strip()
    return client.list_remote_models()


def _gemini_models(config: dict, logger) -> list:
    client = AIClient(config, logger)
    client.provider = "gemini"
    client.available = True
    client.gemini_base_url = (
        config.get("ai", {}).get("gemini_base_url")
        or "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")
    client.gemini_api_key = os.environ.get(
        config.get("ai", {}).get("gemini_api_key_env", "GEMINI_API_KEY"), ""
    ).strip()
    return client.list_remote_models()


def _spark_models(config: dict, logger) -> list:
    client = AIClient(config, logger)
    # 목록 조회를 위해 잠깐 provider 를 spark 로 맞춘다
    client.provider = "spark"
    client.available = True
    client.base_url = (config.get("ai", {}).get("base_url") or "http://127.0.0.1:8000/v1").rstrip("/")
    return client.list_remote_models()


def build_status(config_path: str, logger) -> dict:
    config = _read_config(config_path)
    ai = config.get("ai", {})
    provider = (ai.get("provider") or "anthropic").lower()
    model_id = ai.get("sentiment_model")
    if provider != "fallback" and not model_fits_provider(provider, model_id):
        model_id = resolve_provider_model(provider, None, ai.get("last_models") or {})
        ai["sentiment_model"] = model_id
        ai["extract_model"] = model_id
        last = ai.setdefault("last_models", {})
        last[provider] = model_id
        _write_config(config_path, config)

    models = []
    spark = None
    if provider == "spark":
        models = _spark_models(config, logger)
        spark_client = AIClient(config, logger)
        spark = spark_client.spark_device_status()
    elif provider == "openai":
        models = _openai_models(config, logger)
    elif provider == "gemini":
        models = _gemini_models(config, logger)
    elif provider == "anthropic":
        models = _anthropic_models(config)
    else:
        models = ["규칙 기반"]

    models = [m for m in models if model_fits_provider(provider, m)]
    if model_id and model_id not in models and model_fits_provider(provider, model_id):
        models.insert(0, model_id)
    if not models:
        models = [DEFAULT_MODELS.get(provider, "qwen")]

    # Spark 미선택이어도 온도 위젯용으로 도달 여부만 확인 (선택 시 UI에서 강조)
    if spark is None:
        probe = AIClient({**config, "ai": {**ai, "provider": "spark"}}, logger)
        spark = probe.spark_device_status()

    tunnel_ok = False
    try:
        base = (ai.get("base_url") or "http://127.0.0.1:8000/v1").rstrip("/")
        r = requests.get(f"{base}/models", timeout=3)
        tunnel_ok = r.status_code == 200
    except requests.RequestException:
        tunnel_ok = False

    anthropic_env = ai.get("api_key_env", "ANTHROPIC_API_KEY")
    spark_env = ai.get("spark_api_key_env", "SPARK_API_KEY")
    openai_env = ai.get("openai_api_key_env", "OPENAI_API_KEY")
    gemini_env = ai.get("gemini_api_key_env", "GEMINI_API_KEY")
    display = resolve_snapshot_model(provider, model_id or "", spark if provider == "spark" else None)

    return {
        "provider": provider,
        "sentiment_model": model_id,
        "extract_model": ai.get("extract_model"),
        "display_model": display,
        "providers": PROVIDER_OPTIONS,
        "models": models,
        "anthropic_key_set": bool(os.environ.get(anthropic_env, "").strip()),
        "spark_key_set": bool(os.environ.get(spark_env, "").strip()),
        "openai_key_set": bool(os.environ.get(openai_env, "").strip()),
        "gemini_key_set": bool(os.environ.get(gemini_env, "").strip()),
        "editable_key_providers": sorted(EDITABLE_KEY_PROVIDERS),
        "spark": spark,
        "spark_tunnel_ok": tunnel_ok,
        "base_url": ai.get("base_url"),
    }


def apply_provider_config(config_path: str, provider: str, model: Optional[str], logger) -> dict:
    config = _read_config(config_path)
    ai = config.setdefault("ai", {})
    provider = (provider or "").strip().lower()
    if provider not in ("spark", "openai", "gemini", "anthropic", "fallback"):
        raise ValueError(f"지원하지 않는 provider: {provider}")

    last_models = ai.setdefault("last_models", {})
    prev_provider = (ai.get("provider") or "").strip().lower()
    prev_model = ai.get("sentiment_model")
    if prev_provider and model_fits_provider(prev_provider, prev_model):
        last_models[prev_provider] = prev_model

    resolved = resolve_provider_model(provider, model, last_models)
    ai["provider"] = provider
    if provider == "spark":
        ai.setdefault("base_url", "http://127.0.0.1:8000/v1")
        ai.setdefault("spark_health_url", "http://100.114.218.1:8080/health")
        ai["enable_thinking"] = False
        ai.setdefault("request_timeout_sec", 90)
        ai["sentiment_model"] = resolved
        ai["extract_model"] = resolved
    elif provider == "openai":
        ai.setdefault("openai_base_url", "https://api.openai.com/v1")
        ai.setdefault("openai_api_key_env", "OPENAI_API_KEY")
        ai.setdefault("request_timeout_sec", 60)
        ai["sentiment_model"] = resolved
        ai["extract_model"] = resolved
    elif provider == "gemini":
        ai.setdefault("gemini_base_url", "https://generativelanguage.googleapis.com/v1beta")
        ai.setdefault("gemini_api_key_env", "GEMINI_API_KEY")
        ai.setdefault("request_timeout_sec", 60)
        ai["sentiment_model"] = resolved
        ai["extract_model"] = resolved
    elif provider == "anthropic":
        ai["sentiment_model"] = resolved
        ai["extract_model"] = resolved
    last_models[provider] = ai.get("sentiment_model")

    _write_config(config_path, config)
    logger.info(f"AI provider 설정 저장: provider={provider}, model={ai.get('sentiment_model')}")
    return build_status(config_path, logger)


def save_provider_api_key(config_path: str, provider: str, key: str, logger) -> dict:
    """대시보드에서 입력한 provider API 키를 .env 에 저장하고 프로세스 환경변수에도 반영한다."""
    provider = (provider or "").strip().lower()
    key = (key or "").strip()
    if provider not in PROVIDER_KEY_ENV:
        raise ValueError(f"키를 저장할 수 없는 provider: {provider}")
    if not key:
        raise ValueError("API 키가 비어 있습니다.")
    if len(key) > 512:
        raise ValueError("키가 너무 깁니다.")
    config = _read_config(config_path)
    cfg_key, default_env = PROVIDER_KEY_ENV[provider]
    env_name = config.get("ai", {}).get(cfg_key, default_env)
    env_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), ".env")
    envfile.write_dotenv(env_path, {env_name: key})
    envfile.ensure_gitignored(env_path)
    os.environ[env_name] = key
    logger.info(f"{env_name} 을 .env 에 저장했습니다 (provider={provider}).")
    return build_status(config_path, logger)


def delete_provider_api_key(config_path: str, provider: str, logger) -> dict:
    """OpenAI/Anthropic/Gemini 키를 .env·프로세스에서 제거한다. Spark는 지원하지 않는다."""
    provider = (provider or "").strip().lower()
    if provider == "spark":
        raise ValueError("Spark API 키는 대시보드에서 삭제할 수 없습니다.")
    if provider not in EDITABLE_KEY_PROVIDERS:
        raise ValueError(f"키를 삭제할 수 없는 provider: {provider}")
    config = _read_config(config_path)
    cfg_key, default_env = PROVIDER_KEY_ENV[provider]
    env_name = config.get("ai", {}).get(cfg_key, default_env)
    env_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), ".env")
    envfile.remove_dotenv_keys(env_path, [env_name])
    os.environ.pop(env_name, None)
    logger.info(f"{env_name} 을 .env 에서 삭제했습니다 (provider={provider}).")
    return build_status(config_path, logger)


def save_spark_api_key(config_path: str, key: str, logger) -> dict:
    """호환용: Spark 키 저장 → save_provider_api_key 위임."""
    return save_provider_api_key(config_path, "spark", key, logger)


def _rebuild_dashboard(db, config, logger, job: _JobState):
    with job.lock:
        job.message = "차트/HTML 대시보드 생성 중"
    output_dir = config.get("visualization", {}).get("output_dir", "output")
    chart_paths = visualizer.generate_all_charts(db, config, logger)
    threshold = config.get("sentiment_grade", {}).get("strong_threshold", 0.75)
    days = config.get("alert", {}).get("recent_days", 7)
    alert_result = alerts.check_negative_spike(db, config, logger, days=days)
    text_report = reporter.build_report_text(db, chart_paths, alert_result, threshold=threshold)
    reporter.save_report(text_report, output_dir, "md")
    reporter.build_html_dashboard(db, chart_paths, alert_result, output_dir, threshold=threshold)
    reporter.build_compare_html(output_dir)


def run_reanalyze_job(config_path: str, logger, job: _JobState, rebuild_charts: bool = True) -> None:
    with job.lock:
        if job.running:
            return
        job.running = True
        job.done = False
        job.error = None
        job.kind = "analyze"
        job.message = "재분석 시작"
        job.success = 0
        job.failed = 0
        job.imported = 0
        job.skipped = 0

    try:
        config = _read_config(config_path)
        db = Database(config["storage"]["db_path"])
        ai_client = AIClient(config, logger)
        try:
            with job.lock:
                job.message = "감정 분석 중 (전체 재채점)"
            result = analyzer.analyze_reviews(
                db, ai_client, logger, target="all", show_progress=False
            )
            with job.lock:
                job.success = result.get("success", 0)
                job.failed = result.get("failed", 0)
                job.message = "스냅샷 저장 중"
            if result.get("success", 0) > 0:
                model_runs.snapshot_after_analyze(db, config, logger, ai_client)
            with job.lock:
                job.message = "키워드/요약 추출 중"
            extractor.extract_insights(db, ai_client, logger, sentiment=None)

            if rebuild_charts:
                _rebuild_dashboard(db, config, logger, job)

            with job.lock:
                job.message = "완료"
                job.done = True
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.error(f"재분석 작업 실패: {e}\n{traceback.format_exc()}")
        with job.lock:
            job.error = str(e)
            job.message = "실패"
            job.done = True
    finally:
        with job.lock:
            job.running = False


def run_upload_job(config_path: str, logger, job: _JobState, filepath: str) -> None:
    """CSV/Excel 업로드 → import → clean → analyze(미분석) → 대시보드 재생성."""
    with job.lock:
        if job.running:
            return
        job.running = True
        job.done = False
        job.error = None
        job.kind = "upload"
        job.message = "업로드 처리 시작"
        job.success = 0
        job.failed = 0
        job.imported = 0
        job.skipped = 0

    try:
        config = _read_config(config_path)
        db = Database(config["storage"]["db_path"])
        ai_client = AIClient(config, logger)
        try:
            with job.lock:
                job.message = "파일 가져오는 중 (import)"
            total, valid, skipped = ingest.import_file(db, config, logger, filepath)
            with job.lock:
                job.imported = valid
                job.skipped = skipped
                job.message = f"가져오기 완료 (유효 {valid}/스킵 {skipped}) — 정제 중"

            clean_result = cleaner.clean_all(db, config, logger)
            with job.lock:
                job.message = (
                    f"정제 완료 (신규 {clean_result.get('inserted', 0)}) — 감정 분석 중"
                )

            result = analyzer.analyze_reviews(
                db, ai_client, logger, target="unanalyzed", show_progress=False
            )
            with job.lock:
                job.success = result.get("success", 0)
                job.failed = result.get("failed", 0)
                job.message = "스냅샷 저장 중"
            if result.get("success", 0) > 0:
                model_runs.snapshot_after_analyze(db, config, logger, ai_client)

            with job.lock:
                job.message = "키워드/요약 추출 중"
            extractor.extract_insights(db, ai_client, logger, sentiment=None)
            _rebuild_dashboard(db, config, logger, job)

            with job.lock:
                job.message = (
                    f"완료 · 가져오기 {valid}건 · 분석 {result.get('success', 0)}건"
                )
                job.done = True
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.error(f"업로드 작업 실패: {e}\n{traceback.format_exc()}")
        with job.lock:
            job.error = str(e)
            job.message = "실패"
            job.done = True
    finally:
        with job.lock:
            job.running = False
        try:
            if filepath and os.path.isfile(filepath):
                os.remove(filepath)
        except OSError:
            pass


def _save_upload_file(headers, rfile, upload_dir: str) -> tuple[str, str]:
    """multipart 업로드를 받아 디스크에 저장. (저장경로, 원본파일명) 반환."""
    import cgi
    import re
    from datetime import datetime

    content_type = headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("multipart/form-data 형식으로 업로드하세요.")

    length = int(headers.get("Content-Length") or 0)
    if length <= 0:
        raise ValueError("업로드 본문이 비어 있습니다.")
    if length > UPLOAD_MAX_BYTES:
        raise ValueError(f"파일 크기 제한({UPLOAD_MAX_BYTES // (1024 * 1024)}MB)을 초과했습니다.")

    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(length),
    }
    form = cgi.FieldStorage(fp=rfile, headers=headers, environ=environ, keep_blank_values=True)
    item = form["file"] if "file" in form else None
    if item is None:
        # 첫 번째 파일 필드 사용
        for key in form.keys():
            candidate = form[key]
            if getattr(candidate, "filename", None) and getattr(candidate, "file", None):
                item = candidate
                break
    if item is None or not getattr(item, "filename", None):
        raise ValueError("파일 필드(file)가 없습니다.")

    original = os.path.basename(item.filename)
    ext = os.path.splitext(original)[1].lower()
    if ext not in UPLOAD_ALLOWED_EXT:
        raise ValueError(f"지원 형식: {', '.join(sorted(UPLOAD_ALLOWED_EXT))} (받은 확장자: {ext or '없음'})")

    os.makedirs(upload_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", original) or "upload.csv"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(upload_dir, f"{stamp}_{safe}")
    data = item.file.read()
    if not data:
        raise ValueError("빈 파일입니다.")
    if len(data) > UPLOAD_MAX_BYTES:
        raise ValueError(f"파일 크기 제한({UPLOAD_MAX_BYTES // (1024 * 1024)}MB)을 초과했습니다.")
    with open(dest, "wb") as f:
        f.write(data)
    return dest, original


def make_handler(
    output_dir: str,
    config_path: str,
    logger,
    job: _JobState,
) -> type:
    root = Path(output_dir).resolve()

    class DashboardHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, fmt, *args):
            logger.debug("HTTP " + fmt % args)

        def _send_json(self, code: int, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                try:
                    self._send_json(200, build_status(config_path, logger))
                except Exception as e:  # noqa: BLE001
                    self._send_json(500, {"error": str(e)})
                return
            if parsed.path == "/api/job":
                self._send_json(200, job.snapshot())
                return
            if parsed.path == "/api/runs":
                try:
                    config = _read_config(config_path)
                    db = Database(config["storage"]["db_path"])
                    try:
                        model_runs.ensure_seed_snapshot(db, config, logger)
                        runs = db.list_model_runs()
                    finally:
                        db.close()
                    self._send_json(200, {"runs": runs})
                except Exception as e:  # noqa: BLE001
                    self._send_json(500, {"error": str(e)})
                return
            if parsed.path == "/api/compare":
                try:
                    qs = parse_qs(parsed.query)
                    a = int((qs.get("a") or [""])[0])
                    b = int((qs.get("b") or [""])[0])
                    config = _read_config(config_path)
                    db = Database(config["storage"]["db_path"])
                    try:
                        payload = db.compare_model_runs(a, b)
                    finally:
                        db.close()
                    self._send_json(200, payload)
                except ValueError as e:
                    self._send_json(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    self._send_json(500, {"error": str(e)})
                return
            if parsed.path in ("/", "/index.html"):
                self.path = "/dashboard.html"
            return super().do_GET()

        def do_DELETE(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/runs":
                try:
                    qs = parse_qs(parsed.query)
                    run_id = int((qs.get("id") or [""])[0])
                    config = _read_config(config_path)
                    db = Database(config["storage"]["db_path"])
                    try:
                        ok = db.delete_model_run(run_id)
                        runs = db.list_model_runs()
                    finally:
                        db.close()
                    if not ok:
                        self._send_json(404, {"error": "없는 스냅샷입니다.", "runs": runs})
                        return
                    logger.info(f"모델 스냅샷 삭제: id={run_id}")
                    self._send_json(200, {"ok": True, "deleted": run_id, "runs": runs})
                except ValueError:
                    self._send_json(400, {"error": "스냅샷 id가 필요합니다."})
                except Exception as e:  # noqa: BLE001
                    self._send_json(500, {"error": str(e)})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/config":
                try:
                    data = self._read_json()
                    status = apply_provider_config(
                        config_path,
                        provider=data.get("provider"),
                        model=data.get("model"),
                        logger=logger,
                    )
                    self._send_json(200, status)
                except Exception as e:  # noqa: BLE001
                    self._send_json(400, {"error": str(e)})
                return
            if parsed.path == "/api/provider-key":
                try:
                    data = self._read_json()
                    if data.get("delete") or data.get("action") == "delete":
                        status = delete_provider_api_key(
                            config_path,
                            data.get("provider") or "",
                            logger,
                        )
                    else:
                        status = save_provider_api_key(
                            config_path,
                            data.get("provider") or "",
                            data.get("key") or "",
                            logger,
                        )
                    self._send_json(200, {"ok": True, **status})
                except Exception as e:  # noqa: BLE001
                    self._send_json(400, {"error": str(e)})
                return
            if parsed.path == "/api/spark-key":
                try:
                    data = self._read_json()
                    status = save_spark_api_key(config_path, data.get("key") or "", logger)
                    self._send_json(200, {"ok": True, **status})
                except Exception as e:  # noqa: BLE001
                    self._send_json(400, {"error": str(e)})
                return
            if parsed.path == "/api/analyze":
                snap = job.snapshot()
                if snap["running"]:
                    self._send_json(409, {"error": "이미 재분석이 진행 중입니다.", **snap})
                    return
                # 먼저 provider/model 반영
                try:
                    data = self._read_json()
                    if data.get("provider"):
                        apply_provider_config(
                            config_path,
                            provider=data.get("provider"),
                            model=data.get("model"),
                            logger=logger,
                        )
                except Exception as e:  # noqa: BLE001
                    self._send_json(400, {"error": str(e)})
                    return
                t = threading.Thread(
                    target=run_reanalyze_job,
                    args=(config_path, logger, job),
                    daemon=True,
                )
                t.start()
                self._send_json(202, {"ok": True, "message": "재분석 시작", **job.snapshot()})
                return
            if parsed.path == "/api/upload":
                snap = job.snapshot()
                if snap["running"]:
                    self._send_json(409, {"error": "다른 작업이 진행 중입니다. 끝날 때까지 기다려 주세요.", **snap})
                    return
                try:
                    upload_dir = os.path.join(os.path.dirname(config_path) or ".", "uploads")
                    dest, original = _save_upload_file(self.headers, self.rfile, upload_dir)
                except Exception as e:  # noqa: BLE001
                    self._send_json(400, {"error": str(e)})
                    return
                t = threading.Thread(
                    target=run_upload_job,
                    args=(config_path, logger, job, dest),
                    daemon=True,
                )
                t.start()
                self._send_json(
                    202,
                    {
                        "ok": True,
                        "message": f"업로드 접수: {original}",
                        "filename": original,
                        **job.snapshot(),
                    },
                )
                return
            self._send_json(404, {"error": "not found"})

    return DashboardHandler


def serve(
    output_dir: str,
    config_path: str,
    logger,
    host: str = "127.0.0.1",
    port: int = 8765,
):
    os.makedirs(output_dir, exist_ok=True)
    config = _read_config(config_path)
    db = Database(config["storage"]["db_path"])
    try:
        model_runs.ensure_seed_snapshot(db, config, logger)
    finally:
        db.close()

    dash = Path(output_dir) / "dashboard.html"
    if not dash.exists():
        logger.warning("dashboard.html 이 없습니다. 먼저 `python main.py dashboard --html` 을 실행하세요.")
    reporter.build_compare_html(output_dir)

    job = _JobState()
    handler = make_handler(output_dir, config_path, logger, job)
    httpd = ThreadingHTTPServer((host, port), handler)
    logger.info(f"대시보드 서버: http://{host}:{port}/dashboard.html")
    print(f"✔ 대시보드 서버 실행 중 → http://{host}:{port}/dashboard.html")
    print(f"  모델 비교 → http://{host}:{port}/compare.html")
    print("  채점 모델 선택 / Spark 온도 / 재분석 / 스냅샷 비교 API 포함 (Ctrl+C 종료)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] 서버를 종료합니다.")
    finally:
        httpd.server_close()
