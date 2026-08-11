"""aspect (상품/배송/응대) 유틸·폴백 추론 테스트."""
import unittest

from src.aspects import aspects_from_json, aspects_to_json, infer_aspects_from_text, normalize_aspects


class AspectTests(unittest.TestCase):
    def test_infer_delivery_positive(self):
        a = infer_aspects_from_text("배송이 정말 빨라서 좋아요")
        self.assertEqual(a["delivery"], "positive")

    def test_infer_service_negative(self):
        a = infer_aspects_from_text("고객센터 응대가 불친절하고 답이 없어요")
        self.assertEqual(a["service"], "negative")

    def test_infer_product_positive(self):
        a = infer_aspects_from_text("상품 품질이 기대 이상이라 만족합니다")
        self.assertEqual(a["product"], "positive")

    def test_normalize_and_roundtrip(self):
        raw = {"product": "positive", "delivery": "bogus", "extra": 1}
        n = normalize_aspects(raw)
        self.assertEqual(n["product"], "positive")
        self.assertEqual(n["delivery"], "not_mentioned")
        self.assertEqual(n["service"], "not_mentioned")
        again = aspects_from_json(aspects_to_json(n))
        self.assertEqual(again, n)


if __name__ == "__main__":
    unittest.main()
