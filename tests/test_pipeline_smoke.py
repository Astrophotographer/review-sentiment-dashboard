"""
전체 파이프라인 통합(스모크) 테스트
------------------------------------
실제 SQLite DB와 샘플 CSV를 이용해 import -> clean -> analyze -> stats 까지
한 번에 실행해보고, 예외 없이 끝나는지 + 숫자가 말이 되는지 검증한다.
ANTHROPIC_API_KEY 없이도 규칙 기반 폴백으로 동작하므로 네트워크 연결이 필요 없다.

실행 방법: python -m unittest tests/test_pipeline_smoke.py -v  (프로젝트 루트에서)
"""
import json
import os
import sys
import shutil
import logging
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import Database
from src.ai_client import AIClient
from src import ingest, cleaner, analyzer
from src.logger_setup import setup_logger


class TestPipelineSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.config = {
            "ai": {"provider": "anthropic", "api_key_env": "ANTHROPIC_API_KEY_NOT_SET_FOR_TEST",
                   "sentiment_model": "x", "extract_model": "x", "max_tokens": 100, "request_timeout_sec": 5},
            "dedup_policy": "skip",
            "cleaning": {"min_review_length": 5},
            "storage": {"db_path": os.path.join(self.tmp_dir, "test.db")},
            "logging": {"log_dir": os.path.join(self.tmp_dir, "logs"), "level": "INFO"},
        }
        self.logger = setup_logger(self.config)
        self.db = Database(self.config["storage"]["db_path"])
        self.ai_client = AIClient(self.config, self.logger)
        self.csv_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "sample_data", "reviews_sample.csv",
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_full_pipeline_runs_without_error(self):
        total, valid, skipped = ingest.import_file(self.db, self.config, self.logger, self.csv_path)
        self.assertGreaterEqual(valid, 30, "샘플 데이터는 최소 30건 이상이어야 한다")

        clean_result = cleaner.clean_all(self.db, self.config, self.logger)
        self.assertEqual(self.db.count_raw(), valid)

        analyze_result = analyzer.analyze_reviews(self.db, self.ai_client, self.logger, target="unanalyzed")
        self.assertEqual(analyze_result["failed"], 0, "폴백 분석기는 실패 없이 전건 처리되어야 한다")

        stats = self.db.get_stats()
        self.assertEqual(stats["total"], stats["analyzed"], "analyze 이후에는 전건 분석완료 상태여야 한다")
        self.assertGreater(stats["total"], 0)
        # 감정 분포 비율의 합이 총합과 같아야 한다 (데이터 무결성 체크)
        self.assertEqual(sum(stats["sentiment_dist"].values()), stats["analyzed"])

    def test_dedup_skip_prevents_duplicates_on_reimport(self):
        ingest.import_file(self.db, self.config, self.logger, self.csv_path, dedup_policy="skip")
        first_count = self.db.count_raw()
        ingest.import_file(self.db, self.config, self.logger, self.csv_path, dedup_policy="skip")
        second_count = self.db.count_raw()
        self.assertEqual(first_count, second_count, "skip 정책에서는 재수입해도 건수가 늘지 않아야 한다")


class TestInteractiveHtmlDashboard(unittest.TestCase):
    """[회귀 테스트] 카테고리/제품 필터가 있는 대화형 HTML 대시보드가 실제로
    필요한 요소(필터 select, 임베드된 리뷰 데이터, 내장 Chart.js)를 전부 포함해서
    생성되는지 검증한다. 실제 브라우저 없이 생성된 HTML 텍스트만 검사한다
    (브라우저 상호작용 검증은 개발 중 Playwright로 별도 수동 확인함)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.config = {
            "ai": {"provider": "anthropic", "api_key_env": "ANTHROPIC_API_KEY_NOT_SET_FOR_TEST",
                   "sentiment_model": "x", "extract_model": "x", "max_tokens": 100, "request_timeout_sec": 5},
            "dedup_policy": "skip",
            "cleaning": {"min_review_length": 5},
            "storage": {"db_path": os.path.join(self.tmp_dir, "test.db")},
            "logging": {"log_dir": os.path.join(self.tmp_dir, "logs"), "level": "INFO"},
            "sentiment_grade": {"strong_threshold": 0.75},
        }
        self.logger = setup_logger(self.config)
        self.db = Database(self.config["storage"]["db_path"])
        self.ai_client = AIClient(self.config, self.logger)
        csv_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data", "reviews_sample.csv",
        )
        ingest.import_file(self.db, self.config, self.logger, csv_path)
        cleaner.clean_all(self.db, self.config, self.logger)
        analyzer.analyze_reviews(self.db, self.ai_client, self.logger, target="unanalyzed", show_progress=False)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_rating_sentiment_png_chart_still_generated_despite_removal_from_html(self):
        # HTML 대시보드에서는 "별점-감정 상관관계" 카드를 뺐지만, 이건 과제 필수
        # 차트 3종 중 하나라서 matplotlib PNG는 그대로 생성되어야 한다.
        from src import visualizer
        cfg = dict(self.config)
        cfg["visualization"] = {"output_dir": self.tmp_dir, "dpi": 80, "font_candidates": []}
        paths = visualizer.generate_all_charts(self.db, cfg, self.logger)
        self.assertTrue(any("rating_sentiment_matrix.png" in p for p in paths))

    def test_html_dashboard_contains_filter_elements_and_embedded_data(self):
        from src import reporter
        path = reporter.build_html_dashboard(self.db, [], None, self.tmp_dir)
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            html = f.read()

        # 카테고리/제품 필터 UI가 있어야 한다
        self.assertIn('id="catFilter"', html)
        self.assertIn('id="prodFilter"', html)
        # 리뷰 데이터가 브라우저에서 다시 집계할 수 있게 통째로 임베드되어야 한다
        self.assertIn("const ALL_REVIEWS", html)
        # Chart.js가 CDN이 아니라 파일 안에 그대로 내장되어 오프라인에서도 동작해야 한다
        self.assertNotIn("cdn.jsdelivr.net/npm/chart.js", html)
        self.assertNotIn("cdnjs.cloudflare.com", html)
        self.assertIn("Chart.js v4.4.1", html, "Chart.js 본체가 인라인으로 삽입되어 있어야 한다")
        # 제품이 하나로 좁혀졌을 때 비교 차트를 숨기는 로직이 포함되어 있어야 한다
        self.assertIn("toggleComparisonCharts", html)
        # 별점-감정 상관관계 차트는 HTML 대시보드에서는 제거되었다 (matplotlib PNG로는
        # 여전히 별도 생성되어 요구사항은 충족한다 - test_visualizer 쪽에서 확인).
        self.assertNotIn('id="chartRating"', html)
        # 시간별 감정 추이는 이동평균으로 표시된다
        self.assertIn("movingAverage", html)
        self.assertIn("이동평균", html)
        # 제품별 차트는 제품 수에 비례해 높이가 늘어나야 12개 제품이 안 잘리고 다 보인다
        self.assertIn("_sizeWrapForCategories", html)
        # 다국어 지원: 한국어/영어 + 중국어
        self.assertIn('"zh"', html)

    def test_embedded_review_payload_matches_db_row_count(self):
        from src import reporter
        import json as _json
        path = reporter.build_html_dashboard(self.db, [], None, self.tmp_dir)
        with open(path, encoding="utf-8") as f:
            html = f.read()
        start = html.index("const ALL_REVIEWS = ") + len("const ALL_REVIEWS = ")
        end = html.index(";\n", start)
        payload = _json.loads(html[start:end])
        self.assertEqual(len(payload), len(self.db.get_all_clean()))
        self.assertIn("product", payload[0])
        self.assertIn("category", payload[0])
        self.assertIn("sentiment", payload[0])


class TestAIFailureVsFallback(unittest.TestCase):
    """[회귀 테스트] API 키가 아예 없을 때(의도된 폴백)와, 키는 있는데 호출 자체가
    실패할 때(크레딧 부족/인증오류 등, 진짜 실패)를 구분하는지 검증한다.
    과제 요구사항 "API 실패 시 로깅 후 스킵"을 지키려면, 키가 있는데 실패한 경우는
    조용히 폴백으로 넘어가지 말고 실제 실패로 처리(예외)해야 한다.
    네트워크 호출 없이 _call_claude만 모킹해서 결정적으로 테스트한다."""

    def setUp(self):
        os.environ["TEST_FAKE_ANTHROPIC_KEY"] = "sk-ant-pretend-key-for-test"
        import logging
        self.logger = logging.getLogger("test_ai_failure_vs_fallback")
        self.logger.handlers = [logging.NullHandler()]
        self.logger.propagate = False

    def tearDown(self):
        os.environ.pop("TEST_FAKE_ANTHROPIC_KEY", None)
        os.environ.pop("TEST_FAKE_SPARK_KEY", None)

    def _client_with_key(self):
        config = {"ai": {"provider": "anthropic", "api_key_env": "TEST_FAKE_ANTHROPIC_KEY",
                          "sentiment_model": "x", "extract_model": "x",
                          "max_tokens": 100, "request_timeout_sec": 5}}
        return AIClient(config, self.logger)

    def _client_without_key(self):
        config = {"ai": {"provider": "anthropic", "api_key_env": "TEST_KEY_DEFINITELY_NOT_SET_ANYWHERE",
                          "sentiment_model": "x", "extract_model": "x",
                          "max_tokens": 100, "request_timeout_sec": 5}}
        return AIClient(config, self.logger)

    def test_spark_requires_api_key(self):
        config = {"ai": {"provider": "spark", "spark_api_key_env": "TEST_SPARK_KEY_NOT_SET",
                         "sentiment_model": "qwen", "extract_model": "qwen",
                         "max_tokens": 100, "request_timeout_sec": 5}}
        client = AIClient(config, self.logger)
        self.assertFalse(client.available)
        result = client.analyze_sentiment("배송이 빨라서 좋아요")
        self.assertIn(result["sentiment"], ("positive", "negative", "neutral"))

    def test_spark_available_when_api_key_set(self):
        os.environ["TEST_FAKE_SPARK_KEY"] = "spark-test-key"
        config = {"ai": {"provider": "spark", "spark_api_key_env": "TEST_FAKE_SPARK_KEY",
                         "sentiment_model": "qwen", "extract_model": "qwen",
                         "max_tokens": 100, "request_timeout_sec": 5}}
        client = AIClient(config, self.logger)
        self.assertTrue(client.available)

    def test_analyze_sentiment_falls_back_silently_when_no_key_at_all(self):
        client = self._client_without_key()
        self.assertFalse(client.available)
        result = client.analyze_sentiment("배송이 빨라서 좋아요")
        self.assertIn(result["sentiment"], ("positive", "negative", "neutral"))
        self.assertIn("aspects", result)
        self.assertEqual(result["aspects"]["delivery"], "positive")

    def test_analyze_sentiment_raises_when_key_present_but_call_fails(self):
        client = self._client_with_key()
        self.assertTrue(client.available, "키가 설정되어 있으면 available은 True여야 한다")
        with patch.object(client, "_call_claude", return_value=None):
            with self.assertRaises(RuntimeError):
                client.analyze_sentiment("아무 리뷰 텍스트")

    def test_extract_insights_falls_back_when_no_key_at_all(self):
        client = self._client_without_key()
        result = client.extract_insights(
            [{"review_text": "좋아요", "sentiment": "positive", "rating": 5}], "감정=전체"
        )
        self.assertIn("positive_keywords", result)

    def test_fallback_keywords_include_occurrence_counts(self):
        # 과제 문서 예시("1. 빠른 배송 (23회)")처럼 키워드가 단순 문자열이 아니라
        # {keyword, count} 형태로 나와서 TOP N 리포트에 실제 등장 횟수를 붙일 수 있어야 한다.
        client = self._client_without_key()
        reviews = [
            {"review_text": "정말 좋아요 만족합니다", "sentiment": "positive", "rating": 5},
            {"review_text": "이것도 좋아요", "sentiment": "positive", "rating": 4},
            {"review_text": "배송이 늦어서 불편했어요", "sentiment": "negative", "rating": 2},
        ]
        result = client.extract_insights(reviews, "감정=전체")
        pos = result["positive_keywords"]
        self.assertTrue(all(isinstance(k, dict) and "keyword" in k and "count" in k for k in pos))
        good = next(k for k in pos if k["keyword"] == "좋")
        self.assertEqual(good["count"], 2, "'좋'이 두 리뷰에 등장했으므로 count=2 여야 한다")

    def test_extract_uses_longer_timeout_than_analyze(self):
        # extract는 리뷰를 최대 200건까지 한 프롬프트에 넣고 최대 4096 토큰까지
        # 생성하게 하므로, analyze(리뷰 1건)용 짧은 타임아웃으로는 실제로 자주
        # ReadTimeout이 났다. 전용 타임아웃이 더 길어야 한다.
        client = self._client_with_key()
        self.assertGreater(client.extract_timeout, client.timeout)
        self.assertGreaterEqual(client.extract_timeout, 120)

    def test_extract_falls_back_with_clear_message_on_timeout(self):
        # 회귀 테스트: 응답 시간 초과(ReadTimeout)가 나도 폴백 결과는 정상 반환되고,
        # 원인이 "타임아웃"이라는 걸 명확히 알 수 있는 로그가 남아야 한다
        # (예전엔 "네트워크 오류: Read timed out..." 처럼 뭉뚱그려져 원인 파악이 어려웠다).
        import requests as _requests
        client = self._client_with_key()
        logged = []

        class _Capture(logging.Handler):
            def emit(self, record):
                logged.append((record.levelname, record.getMessage()))

        self.logger.addHandler(_Capture())
        with patch("requests.post", side_effect=_requests.exceptions.ReadTimeout("Read timed out.")):
            result = client.extract_insights(
                [{"review_text": "좋아요", "sentiment": "positive", "rating": 5}], "감정=전체"
            )
        self.assertIn("positive_keywords", result)
        timeout_logs = [m for lvl, m in logged if lvl == "ERROR" and "타임아웃" in m]
        # extract는 첫 시도가 실패하면 한 번 더 재시도하므로(총 2회 시도),
        # 매번 타임아웃나면 원인 로그도 2번 남는 게 맞다.
        self.assertEqual(len(timeout_logs), 2, "재시도 포함 시도마다 타임아웃 원인이 ERROR 로그로 남아야 한다")

    def test_extract_uses_larger_max_tokens_than_analyze(self):
        # extract는 analyze보다 훨씬 긴 JSON을 요구하므로, 같은 max_tokens를 쓰면
        # 응답이 중간에 잘려 파싱 실패로 이어질 수 있다. 전용 예산이 더 커야 한다.
        # (실제로 리뷰 180건을 요약할 때 2048로도 부족했던 적이 있어 4096 이상을 요구한다.)
        client = self._client_with_key()
        self.assertGreater(client.extract_max_tokens, client.max_tokens)
        self.assertGreaterEqual(client.extract_max_tokens, 4096)

    def test_extract_logs_error_when_response_is_truncated_and_unparseable(self):
        # 회귀 테스트: 응답이 200 OK로 오긴 했지만 JSON이 중간에 잘려 파싱이 실패하는
        # 경우, 예전에는 아무 로그도 안 남기고 조용히 폴백으로 넘어갔다. 이제는 원문
        # 일부를 담은 ERROR 로그를 남기고, 그래도 폴백 결과 자체는 정상 반환해야 한다.
        client = self._client_with_key()
        logged = []

        class _Capture(logging.Handler):
            def emit(self, record):
                logged.append((record.levelname, record.getMessage()))

        self.logger.addHandler(_Capture())
        truncated_json = '{"positive_keywords": [{"keyword": "좋아요", "count": 3}], "negative_keyw'
        with patch.object(client, "_call_claude", return_value=truncated_json):
            result = client.extract_insights(
                [{"review_text": "좋아요", "sentiment": "positive", "rating": 5}], "감정=전체"
            )
        self.assertIn("positive_keywords", result, "파싱 실패해도 폴백 결과는 정상 반환되어야 한다")
        error_logs = [m for lvl, m in logged if lvl == "ERROR" and "파싱하지 못했습니다" in m]
        self.assertEqual(len(error_logs), 1, "파싱 실패 원인이 ERROR 로그로 남아야 한다")

    def test_extract_insights_falls_back_without_crashing_when_key_present_but_call_fails(self):
        # extract는 analyze와 달리 "스킵" 요구사항이 없으므로, 실패해도 대시보드가
        # 텅 비지 않도록 규칙 기반 결과로 대체한다 (다만 WARNING 로그를 남긴다).
        client = self._client_with_key()
        with patch.object(client, "_call_claude", return_value=None):
            result = client.extract_insights(
                [{"review_text": "배송 늦어요", "sentiment": "negative", "rating": 1}], "감정=negative"
            )
        self.assertIn("positive_keywords", result)
        self.assertIn("negative_keywords", result)


class TestSparkKeySave(unittest.TestCase):
    def test_save_spark_api_key_writes_dotenv(self):
        import logging
        from src.dashboard_server import save_spark_api_key
        from src.envfile import parse_dotenv

        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        cfg_path = os.path.join(tmp, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"ai": {"provider": "spark", "spark_api_key_env": "SPARK_API_KEY"}}, f)
        logger = logging.getLogger("test_spark_key")
        logger.addHandler(logging.NullHandler())
        os.environ.pop("SPARK_API_KEY", None)
        save_spark_api_key(cfg_path, "test-spark-secret", logger)
        env_path = os.path.join(tmp, ".env")
        self.assertTrue(os.path.exists(env_path))
        with open(env_path, encoding="utf-8") as f:
            pairs = parse_dotenv(f.read())
        self.assertEqual(pairs.get("SPARK_API_KEY"), "test-spark-secret")
        self.assertEqual(os.environ.get("SPARK_API_KEY"), "test-spark-secret")
        os.environ.pop("SPARK_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
