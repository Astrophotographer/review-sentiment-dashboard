"""
전체 파이프라인 통합(스모크) 테스트
------------------------------------
실제 SQLite DB와 샘플 CSV를 이용해 import -> clean -> analyze -> stats 까지
한 번에 실행해보고, 예외 없이 끝나는지 + 숫자가 말이 되는지 검증한다.
ANTHROPIC_API_KEY 없이도 규칙 기반 폴백으로 동작하므로 네트워크 연결이 필요 없다.

실행 방법: python -m unittest tests/test_pipeline_smoke.py -v  (프로젝트 루트에서)
"""
import os
import sys
import shutil
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

    def test_analyze_sentiment_falls_back_silently_when_no_key_at_all(self):
        client = self._client_without_key()
        self.assertFalse(client.available)
        result = client.analyze_sentiment("배송이 빨라서 좋아요")
        self.assertIn(result["sentiment"], ("positive", "negative", "neutral"))

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


if __name__ == "__main__":
    unittest.main()
