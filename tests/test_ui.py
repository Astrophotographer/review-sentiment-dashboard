"""
ui.py 단위 테스트
-----------------
특히 한글(동아시아 넓은 문자) 폭 계산이 깨지지 않는지 집중적으로 검증한다.
실행: python -m unittest tests/test_ui.py -v (프로젝트 루트에서)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui import _display_width, _pad, _truncate


class TestUiWidth(unittest.TestCase):
    def test_ascii_width_equals_len(self):
        self.assertEqual(_display_width("hello"), 5)

    def test_korean_counts_as_double_width(self):
        # "가" 한 글자는 터미널에서 2칸을 차지해야 한다
        self.assertEqual(_display_width("가"), 2)
        self.assertEqual(_display_width("안녕"), 4)

    def test_mixed_korean_and_ascii(self):
        self.assertEqual(_display_width("A안녕B"), 1 + 2 + 2 + 1)

    def test_pad_produces_equal_display_width_for_mixed_strings(self):
        # "이어폰"(폭6)과 "Earphone X100"(폭13)을 같은 목표 폭으로 패딩하면
        # 최종 문자열의 표시 폭이 동일해야 표가 정렬된다.
        target = 20
        padded_ko = _pad("이어폰", target)
        padded_en = _pad("Earphone X100", target)
        self.assertEqual(_display_width(padded_ko), target)
        self.assertEqual(_display_width(padded_en), target)

    def test_truncate_does_not_break_mid_character_and_respects_width(self):
        text = "배송이 정말 빨라서 놀랐어요"
        truncated = _truncate(text, 10)
        self.assertTrue(truncated.endswith("…"))
        self.assertLessEqual(_display_width(truncated), 10)
        # 잘린 결과가 원본 텍스트의 접두사 + 말줄임표여야 한다 (문자 중간에 깨지지 않음)
        self.assertTrue(text.startswith(truncated[:-1]))

    def test_truncate_noop_when_within_limit(self):
        self.assertEqual(_truncate("짧음", 20), "짧음")


if __name__ == "__main__":
    unittest.main()
