"""AI 키워드/인사이트: 폴백이 성공한 추출을 덮어쓰지 않는지"""
import json
import os
import tempfile
import unittest
import shutil

from src.db import Database
from src.reporter import _top_keywords
from src.utils import now_str


class TestKeywordInsights(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_top_keywords_prefers_ai_over_later_fallback(self):
        ai = {
            "positive_keywords": [{"keyword": "배송 빨라", "count": 10}],
            "negative_keywords": [{"keyword": "배송 지연", "count": 4}],
            "summary": "배송은 빠르다는 칭찬과 지연 불만이 함께 있습니다.",
            "suggestions": ["물류 점검"],
            "topic_breakdown": [{"topic": "배송", "count": 4, "examples": ["지연"]}],
        }
        fallback = {
            "fallback": True,
            "positive_keywords": [{"keyword": "좋", "count": 28}],
            "negative_keywords": [{"keyword": "늦", "count": 9}],
            "summary": "총 183건의 리뷰를 규칙 기반으로 요약했습니다.",
            "suggestions": ["재분석해 보세요."],
            "topic_breakdown": [],
        }
        self.db.insert_extraction("keyword_summary", "감정=전체", json.dumps(ai, ensure_ascii=False), now_str())
        self.db.insert_extraction("keyword_summary", "감정=전체", json.dumps(fallback, ensure_ascii=False), now_str())
        kw = _top_keywords(self.db)
        self.assertEqual(kw["positive"][0]["keyword"], "배송 빨라")
        self.assertIn("배송은 빠르다", kw["summary"])
        self.assertIn("AI", kw["source"])


if __name__ == "__main__":
    unittest.main()
