"""model_runs 스냅샷 비교 단위 테스트"""
import os
import tempfile
import unittest
import shutil

from src.db import Database
from src.utils import now_str


class TestModelRunCompare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "t.db"))
        # 최소 clean 리뷰 3건
        for i, (sent, conf) in enumerate([("positive", 0.9), ("negative", 0.8), ("neutral", 0.5)], start=1):
            rid = self.db.insert_clean(
                raw_id=None,
                review_text=f"리뷰 {i} 본문입니다",
                rating=5 if sent == "positive" else 1 if sent == "negative" else 3,
                review_date="2026-06-01",
                product="테스트제품",
                language="ko",
                dedup_hash=f"h{i}",
                cleaned_at=now_str(),
                category="테스트",
            )
            self.db.update_analysis(rid, sent, conf, now_str())

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seed_and_compare(self):
        seed = self.db.seed_model_run_if_empty("fallback", "규칙 기반", "시드", now_str())
        self.assertIsNotNone(seed)
        self.assertEqual(self.db.count_model_runs(), 1)
        # 두 번째 시드는 무시
        self.assertIsNone(self.db.seed_model_run_if_empty("fallback", "규칙 기반", "시드2", now_str()))

        # B 런: 일부 감정을 바꿔 저장하려면 clean을 바꾼 뒤 save
        self.db.update_analysis(1, "negative", 0.7, now_str())  # was positive
        run_b = self.db.save_model_run("spark", "qwen", "spark/qwen", now_str(), temp_c=55.0)
        cmp = self.db.compare_model_runs(seed, run_b)
        self.assertEqual(cmp["common_review_count"], 3)
        self.assertEqual(cmp["disagreement_total"], 1)
        self.assertLess(cmp["agreement_rate"], 100)
        self.assertEqual(cmp["disagreements"][0]["review_id"], 1)
        self.assertEqual(cmp["run_b"]["temp_c"], 55.0)

    def test_delete_model_run_removes_results(self):
        seed = self.db.seed_model_run_if_empty("fallback", "규칙 기반", "시드", now_str())
        run_b = self.db.save_model_run("spark", "qwen", "spark/qwen", now_str(), temp_c=55.0)
        self.assertTrue(self.db.delete_model_run(run_b))
        self.assertIsNone(self.db.get_model_run(run_b))
        leftover = self.db.conn.execute(
            "SELECT COUNT(*) c FROM model_run_results WHERE run_id=?", (run_b,)
        ).fetchone()["c"]
        self.assertEqual(leftover, 0)
        self.assertEqual(self.db.count_model_runs(), 1)
        self.assertIsNotNone(self.db.get_model_run(seed))
        self.assertFalse(self.db.delete_model_run(run_b))


if __name__ == "__main__":
    unittest.main()
