"""모델 표시명 포맷 테스트"""
import unittest

from src.model_display import format_model_display, resolve_snapshot_model


class TestModelDisplay(unittest.TestCase):
    def test_strips_date_suffix(self):
        self.assertEqual(
            format_model_display("claude-haiku-4-5-20251001"),
            "claude haiku 4.5",
        )

    def test_strips_year_token(self):
        self.assertNotIn("2026", format_model_display("foo-bar-2026"))
        self.assertEqual(format_model_display("foo-bar-2026"), "foo bar")

    def test_keeps_readable_health_name(self):
        self.assertEqual(
            format_model_display("Qwen3.5-122B"),
            "Qwen3.5 122B",
        )

    def test_spark_prefers_health_model(self):
        self.assertEqual(
            resolve_snapshot_model("spark", "qwen", {"ok": True, "model": "Qwen3.5-122B"}),
            "Qwen3.5 122B",
        )

    def test_openai_formats_id(self):
        self.assertEqual(resolve_snapshot_model("openai", "gpt-4o-mini"), "gpt 4o mini")


if __name__ == "__main__":
    unittest.main()
