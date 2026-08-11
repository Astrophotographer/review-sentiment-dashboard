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
from . import analyzer, extractor, alerts, visualizer, reporter, model_runs, ingest, cleaner
from .db import Database


PROVIDER_OPTIONS = [
    {"id": "spark", "label": "Spark (vLLM / qwen)", "needs_model": True},
    {"id": "anthropic", "label": "Anthropic Claude", "needs_model": True},
    {"id": "fallback", "label": "규칙 기반 폴백", "needs_model": False},
]

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
        if m and m not in models:
            models.append(m)
    # 자주 쓰는 후보도 선택지에 노출
    for m in ("claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-sonnet-4-20250514"):
        if m not in models:
            models.append(m)
    return models


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
    models = []
    spark = None
    if provider == "spark":
        models = _spark_models(config, logger)
        spark_client = AIClient(config, logger)
        spark = spark_client.spark_device_status()
    elif provider == "anthropic":
        models = _anthropic_models(config)
    else:
        models = ["규칙 기반"]

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

    return {
        "provider": provider,
        "sentiment_model": ai.get("sentiment_model"),
        "extract_model": ai.get("extract_model"),
        "providers": PROVIDER_OPTIONS,
        "models": models,
        "anthropic_key_set": bool(os.environ.get(ai.get("api_key_env", "ANTHROPIC_API_KEY"), "").strip()),
        "spark": spark,
        "spark_tunnel_ok": tunnel_ok,
        "base_url": ai.get("base_url"),
    }


def apply_provider_config(config_path: str, provider: str, model: Optional[str], logger) -> dict:
    config = _read_config(config_path)
    ai = config.setdefault("ai", {})
    provider = (provider or "").strip().lower()
    if provider not in ("spark", "anthropic", "fallback"):
        raise ValueError(f"지원하지 않는 provider: {provider}")

    ai["provider"] = provider
    if provider == "spark":
        ai.setdefault("base_url", "http://127.0.0.1:8000/v1")
        ai.setdefault("spark_health_url", "http://100.114.218.1:8080/health")
        ai["enable_thinking"] = False
        ai.setdefault("request_timeout_sec", 90)
        if model:
            ai["sentiment_model"] = model
            ai["extract_model"] = model
        else:
            ai.setdefault("sentiment_model", "qwen")
            ai.setdefault("extract_model", "qwen")
    elif provider == "anthropic":
        if model:
            ai["sentiment_model"] = model
            # extract 는 더 큰 모델을 유지할 수 있게, 감정모델만 강제하지 않음
            # 다만 UI에서 하나로 고르면 둘 다 맞춤
            ai["extract_model"] = model
    else:
        # fallback
        pass

    _write_config(config_path, config)
    logger.info(f"AI provider 설정 저장: provider={provider}, model={ai.get('sentiment_model')}")
    return build_status(config_path, logger)


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
