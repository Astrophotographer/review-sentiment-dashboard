"""
main.py CLI 통합 테스트
------------------------
실제로 `python main.py ...` 를 서브프로세스로 실행해서, 이번에 추가한
사용자 편의성 기능(환영 화면, --yes 플래그 위치, quickstart)이 실제로
동작하는지 검증한다. 격리된 임시 디렉터리에서 실행하므로 실제
data/output 폴더를 건드리지 않는다.

실행: python -m unittest tests/test_main_cli.py -v (프로젝트 루트에서)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestMainCli(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        # main.py, src/, config.json, sample_data/ 를 임시 폴더로 복사해 격리 실행
        for name in ("main.py", "config.json", "src", "sample_data"):
            src_path = os.path.join(ROOT, name)
            dst_path = os.path.join(self.tmp_dir, name)
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(src_path, dst_path)
        os.makedirs(os.path.join(self.tmp_dir, "data"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp_dir, "logs"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp_dir, "output"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run(self, args, input_text=None, timeout=60):
        # input_text가 없을 때는 stdin을 명시적으로 /dev/null 로 돌려서 "완전히 비대화형"
        # 상태를 보장한다. 그냥 input=None만 넘기면 subprocess가 stdin을 리다이렉트하지
        # 않고 부모 프로세스(=이 테스트를 실행 중인 실제 터미널)의 stdin을 그대로 물려받는데,
        # 이 테스트를 실제 대화형 터미널(예: Terminal.app, iTerm)에서 직접 실행하면 하위
        # main.py 프로세스가 진짜 tty를 물려받아 confirm() 프롬프트가 응답을 못 받고 타임아웃날
        # 수 있다. DEVNULL로 고정하면 어떤 환경(CI든 로컬 터미널이든)에서 실행해도 항상
        # "비대화형"으로 동일하게 동작한다.
        kwargs = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
        return subprocess.run(
            [sys.executable, "main.py"] + args,
            cwd=self.tmp_dir, capture_output=True, text=True, timeout=timeout, **kwargs,
        )

    def test_no_args_shows_welcome_and_exits_cleanly(self):
        result = self._run([])
        self.assertEqual(result.returncode, 0)
        self.assertIn("menu", result.stdout)
        self.assertIn("quickstart", result.stdout)

    def test_help_still_works(self):
        result = self._run(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("quickstart", result.stdout)

    def test_import_without_file_auto_detects_sample_csv(self):
        result = self._run(["import"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("자동으로 찾았습니다", result.stdout)
        self.assertIn("가져오기 완료", result.stdout)

    def test_failed_import_does_not_show_misleading_next_step_hint(self):
        # 존재하지 않는 파일을 지정하면 가져오기가 실패해야 하고, 그 경우
        # "clean을 실행하세요" 같은 다음 단계 힌트를 보여주면 안 된다 (아무것도
        # 가져오지 못했으므로 오해를 줄 수 있다). 로그(ERROR)는 stderr로, 힌트는
        # stdout으로 나가므로 각각 확인한다.
        result = self._run(["import", "--file", "no_such_file.csv"])
        self.assertNotIn("다음 단계", result.stdout)
        self.assertIn("찾을 수 없습니다", result.stderr)

    def test_quickstart_runs_full_pipeline_and_creates_outputs(self):
        result = self._run(["quickstart", "--no-html"])
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("퀵스타트 완료", result.stdout)
        output_dir = os.path.join(self.tmp_dir, "output")
        files = os.listdir(output_dir)
        self.assertIn("dashboard_report.md", files)
        self.assertTrue(any(f.endswith(".png") for f in files), "차트 PNG가 생성되어야 한다")

    def test_yes_flag_works_before_and_after_subcommand(self):
        self._run(["quickstart"])  # 먼저 데이터를 채워둔다 (분석까지 완료된 상태)
        # -y 를 서브커맨드 뒤에 붙여도 확인 프롬프트 없이 진행되어야 한다
        result_after = self._run(["analyze", "--all", "-y"])
        self.assertEqual(result_after.returncode, 0)
        self.assertNotIn("취소되었습니다", result_after.stdout)
        self.assertIn("분석 완료", result_after.stdout)
        # -y 를 서브커맨드 앞에 붙여도 동일하게 동작해야 한다
        result_before = self._run(["-y", "analyze", "--all"])
        self.assertEqual(result_before.returncode, 0)
        self.assertNotIn("취소되었습니다", result_before.stdout)

    def test_analyze_all_without_yes_declines_safely_when_noninteractive(self):
        self._run(["quickstart"])
        result = self._run(["analyze", "--all"])  # -y 없음, stdin 없음(비대화형) -> 안전하게 취소
        self.assertEqual(result.returncode, 0)
        self.assertIn("취소되었습니다", result.stdout)

    def test_menu_list_pagination_navigates_between_pages(self):
        # 대화형 메뉴 안에서 목록 조회(6번)를 골라 감정필터 없이(엔터), 다음 페이지(n)로
        # 두 번 넘어간 뒤 그만(엔터) → 메뉴로 복귀(엔터) → 종료(0) 흐름을 검증한다.
        self._run(["quickstart"])
        result = self._run(["menu"], input_text="6\n\nn\nn\n\n\n0\n", timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertIn("1/", result.stdout)
        self.assertIn("2/", result.stdout)
        self.assertIn("3/", result.stdout)
        self.assertRegex(result.stdout, r"1/\d+ 페이지")

    def test_search_finds_matching_reviews_by_keyword(self):
        self._run(["quickstart"])
        result = self._run(["search", "배송"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("검색 결과", result.stdout)
        self.assertIn("배송", result.stdout)

    def test_search_with_no_matches_shows_friendly_message(self):
        self._run(["quickstart"])
        result = self._run(["search", "존재하지않는단어그자체"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("검색 결과가 없습니다", result.stdout)

    def test_setup_writes_env_file_and_gitignore_entry(self):
        result = self._run(["setup"], input_text="sk-ant-faketestkey\nn\n")
        self.assertEqual(result.returncode, 0)
        env_path = os.path.join(self.tmp_dir, ".env")
        self.assertTrue(os.path.exists(env_path))
        with open(env_path, encoding="utf-8") as f:
            self.assertIn("ANTHROPIC_API_KEY=sk-ant-faketestkey", f.read())
        gitignore_path = os.path.join(self.tmp_dir, ".gitignore")
        with open(gitignore_path, encoding="utf-8") as f:
            self.assertIn(".env", f.read())

    def test_env_file_is_auto_loaded_on_next_run(self):
        # setup으로 저장한 키가, 별도로 export 하지 않아도 다음 실행에서 자동 적용되는지 확인.
        self._run(["setup"], input_text="sk-ant-autoloadtest\nn\n")
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)  # 실제 환경변수는 없는 상태를 시뮬레이션
        result = subprocess.run(
            [sys.executable, "main.py", "import"],
            cwd=self.tmp_dir, capture_output=True, text=True, timeout=30, env=env,
        )
        # .env의 키가 적용되었다면 "환경변수가 설정되지 않았습니다" 경고가 뜨지 않아야 한다
        self.assertNotIn("환경변수가 설정되지 않았습니다", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
