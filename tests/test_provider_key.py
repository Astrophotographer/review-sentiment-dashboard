"""provider API 키 저장/삭제 테스트"""
import json
import os
import tempfile
import unittest
import shutil
import logging

from src.dashboard_server import (
    save_provider_api_key,
    delete_provider_api_key,
    apply_provider_config,
    model_fits_provider,
)
from src import envfile


class TestProviderKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "ai": {
                        "provider": "fallback",
                        "api_key_env": "ANTHROPIC_API_KEY",
                        "spark_api_key_env": "SPARK_API_KEY",
                        "openai_api_key_env": "OPENAI_API_KEY",
                        "gemini_api_key_env": "GEMINI_API_KEY",
                        "base_url": "http://127.0.0.1:8000/v1",
                        "sentiment_model": "qwen",
                    },
                    "storage": {"db_path": os.path.join(self.tmp, "t.db")},
                },
                f,
            )
        self.logger = logging.getLogger("test")
        self._env_backup = {
            k: os.environ.get(k)
            for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SPARK_API_KEY", "GEMINI_API_KEY")
        }

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_openai_key_to_dotenv(self):
        status = save_provider_api_key(self.config_path, "openai", "sk-test-openai", self.logger)
        self.assertTrue(status["openai_key_set"])
        env_path = os.path.join(self.tmp, ".env")
        self.assertTrue(os.path.exists(env_path))
        with open(env_path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("OPENAI_API_KEY=sk-test-openai", text)
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), "sk-test-openai")

    def test_save_gemini_key_and_apply(self):
        status = save_provider_api_key(self.config_path, "gemini", "gem-test-key", self.logger)
        self.assertTrue(status["gemini_key_set"])
        status = apply_provider_config(self.config_path, "gemini", "gemini-2.0-flash", self.logger)
        self.assertEqual(status["provider"], "gemini")
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["ai"]["provider"], "gemini")
        self.assertEqual(cfg["ai"]["sentiment_model"], "gemini-2.0-flash")

    def test_delete_openai_key(self):
        save_provider_api_key(self.config_path, "openai", "sk-to-delete", self.logger)
        status = delete_provider_api_key(self.config_path, "openai", self.logger)
        self.assertFalse(status["openai_key_set"])
        self.assertNotIn("OPENAI_API_KEY", os.environ)
        env_path = os.path.join(self.tmp, ".env")
        with open(env_path, encoding="utf-8") as f:
            self.assertNotIn("OPENAI_API_KEY=", f.read())

    def test_cannot_delete_spark_key(self):
        save_provider_api_key(self.config_path, "spark", "spark-secret", self.logger)
        with self.assertRaises(ValueError):
            delete_provider_api_key(self.config_path, "spark", self.logger)
        self.assertEqual(os.environ.get("SPARK_API_KEY"), "spark-secret")

    def test_spark_rejects_claude_model_id(self):
        apply_provider_config(self.config_path, "anthropic", "claude-sonnet-5", self.logger)
        status = apply_provider_config(self.config_path, "spark", "claude-sonnet-5", self.logger)
        self.assertEqual(status["provider"], "spark")
        self.assertEqual(status["sentiment_model"], "qwen")
        self.assertTrue(all(model_fits_provider("spark", m) for m in status["models"]))
        self.assertNotIn("claude-sonnet-5", status["models"])
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["ai"]["sentiment_model"], "qwen")
        self.assertEqual(cfg["ai"]["last_models"]["anthropic"], "claude-sonnet-5")

    def test_model_fits_provider(self):
        self.assertFalse(model_fits_provider("spark", "claude-sonnet-5"))
        self.assertFalse(model_fits_provider("spark", "gpt-4o-mini"))
        self.assertFalse(model_fits_provider("spark", "gemini-2.0-flash"))
        self.assertTrue(model_fits_provider("spark", "qwen"))
        self.assertTrue(model_fits_provider("anthropic", "claude-sonnet-5"))
        self.assertFalse(model_fits_provider("openai", "claude-sonnet-5"))

    def test_apply_openai_provider(self):
        status = apply_provider_config(self.config_path, "openai", "gpt-4o-mini", self.logger)
        self.assertEqual(status["provider"], "openai")
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["ai"]["provider"], "openai")
        self.assertEqual(cfg["ai"]["sentiment_model"], "gpt-4o-mini")

    def test_remove_dotenv_keys(self):
        env_path = os.path.join(self.tmp, ".env")
        envfile.write_dotenv(env_path, {"A": "1", "B": "2"})
        n = envfile.remove_dotenv_keys(env_path, ["A"])
        self.assertEqual(n, 1)
        pairs = envfile.parse_dotenv(open(env_path, encoding="utf-8").read())
        self.assertNotIn("A", pairs)
        self.assertEqual(pairs.get("B"), "2")


if __name__ == "__main__":
    unittest.main()
