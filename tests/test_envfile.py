"""
envfile.py 단위 테스트
----------------------
.env 파일 파싱/로딩/저장 로직을 검증한다. 실제 os.environ을 건드리지 않도록
임시 디렉터리와 격리된 키 이름을 사용한다.
실행: python -m unittest tests/test_envfile.py -v (프로젝트 루트에서)
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.envfile import parse_dotenv, load_dotenv, write_dotenv, ensure_gitignored

_TEST_KEY = "REVIEW_DASHBOARD_TEST_ENV_KEY_XYZ"


class TestEnvfile(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        os.environ.pop(_TEST_KEY, None)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        os.environ.pop(_TEST_KEY, None)

    def test_parse_dotenv_ignores_comments_and_blank_lines(self):
        text = f"# comment\n\n{_TEST_KEY}=hello\n# another comment\nFOO='quoted'\n"
        result = parse_dotenv(text)
        self.assertEqual(result[_TEST_KEY], "hello")
        self.assertEqual(result["FOO"], "quoted")

    def test_load_dotenv_sets_environ_when_missing_file_returns_zero(self):
        path = os.path.join(self.tmp_dir, "does_not_exist.env")
        self.assertEqual(load_dotenv(path), 0)

    def test_load_dotenv_applies_new_variable(self):
        path = os.path.join(self.tmp_dir, ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{_TEST_KEY}=from_file\n")
        applied = load_dotenv(path)
        self.assertEqual(applied, 1)
        self.assertEqual(os.environ.get(_TEST_KEY), "from_file")

    def test_load_dotenv_never_overrides_existing_real_env_var(self):
        os.environ[_TEST_KEY] = "real_env_wins"
        path = os.path.join(self.tmp_dir, ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{_TEST_KEY}=from_file_should_be_ignored\n")
        load_dotenv(path)
        self.assertEqual(os.environ.get(_TEST_KEY), "real_env_wins")

    def test_write_dotenv_creates_file_with_new_key(self):
        path = os.path.join(self.tmp_dir, ".env")
        write_dotenv(path, {_TEST_KEY: "written_value"})
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(f"{_TEST_KEY}=written_value", content)

    def test_write_dotenv_updates_existing_key_without_duplicating(self):
        path = os.path.join(self.tmp_dir, ".env")
        write_dotenv(path, {_TEST_KEY: "v1"})
        write_dotenv(path, {_TEST_KEY: "v2"})
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        matching = [l for l in lines if l.startswith(_TEST_KEY + "=")]
        self.assertEqual(len(matching), 1, "같은 키를 두 번 쓰면 한 줄만 남아야 한다")
        self.assertEqual(matching[0], f"{_TEST_KEY}=v2")

    def test_ensure_gitignored_adds_entry_once(self):
        gi_path = os.path.join(self.tmp_dir, ".gitignore")
        env_path = os.path.join(self.tmp_dir, ".env")
        added_first = ensure_gitignored(env_path, gi_path)
        added_second = ensure_gitignored(env_path, gi_path)
        self.assertTrue(added_first)
        self.assertFalse(added_second, "이미 등록되어 있으면 다시 추가하지 않아야 한다")
        with open(gi_path, encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content.count(".env"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
