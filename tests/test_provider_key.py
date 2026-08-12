"""provider API 키 저장 테스트"""
import json
import os
import tempfile
import unittest
import shutil
import logging

from src.dashboard_server import save_provider_api_key, apply_provider_config
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
            for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SPARK_API_KEY")
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

    def test_apply_openai_provider(self):
        status = apply_provider_config(self.config_path, "openai", "gpt-4o-mini", self.logger)
        self.assertEqual(status["provider"], "openai")
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["ai"]["provider"], "openai")
        self.assertEqual(cfg["ai"]["sentiment_model"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
