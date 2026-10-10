"""安全间隔期解析器单测。

样本全部取自真实标签原文（中国农药信息网核准标签，2026-10-10 拉取），
保留原始表述差异，因为解析器要面对的正是这些差异。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from parse_phi import multi_registration_conflict, parse_phi, parse_rate  # noqa: E402


class ParsePhiTest(unittest.TestCase):
    def test_multi_crop_real_label(self):
        """真实标签：PD20111255 甲基硫菌灵，三个作物各有间隔期。"""
        text = ("3.在苹果树上的安全间隔期为21天，每个作物周期最多使用3次。"
                "在番茄上的安全间隔期为3天，每个作物周期最多使用2次。"
                "在柑橘树上的安全间隔期为21天，每个作物周期最多使用3次。")
        res = parse_phi(text)
        self.assertEqual(res.for_crop("番茄").days, 3)
        self.assertEqual(res.for_crop("柑橘树").days, 21)
        self.assertEqual(res.for_crop("苹果树").days, 21)
        self.assertEqual(res.for_crop("水稻"), None)

    def test_single_crop_real_label(self):
        """真实标签：PD20161283 只写了番茄一种。"""
        res = parse_phi("在番茄上的安全间隔期为7天。")
        self.assertEqual(res.for_crop("番茄").days, 7)

    def test_bare_phi_without_crop(self):
        """真实标签存在无作物限定的写法：只能得到数值，不能确定归属作物。"""
        res = parse_phi("安全间隔期14天。")
        self.assertFalse(res.per_crop)
        self.assertEqual(res.unscoped[0].days, 14)

    def test_no_phi_returns_empty(self):
        """抽不到就返回空——调用方必须降级为人工确认，**不得猜**。"""
        res = parse_phi("本品用于防治番茄叶霉病，注意轮换用药。")
        self.assertFalse(res.ok)

    def test_matched_text_is_preserved_for_audit(self):
        """每条命中都要留原文片段，否则人工无法复核。"""
        res = parse_phi("在番茄上的安全间隔期为3天。")
        self.assertIn("安全间隔期", res.per_crop[0].matched_text)

    def test_for_crop_does_not_cross_crops(self):
        """取不到该作物时必须返回 None，**绝不能拿别的作物的数字兜底**。"""
        text = "在苹果树上的安全间隔期为21天。在柑橘树上的安全间隔期为21天。"
        res = parse_phi(text)
        self.assertIsNone(res.for_crop("番茄"))

    def test_parse_rate_is_reported_honestly(self):
        """成功率就是成功率，低于 1 不能被粉饰。"""
        labels = ["在番茄上的安全间隔期为3天。", "本品无相关表述。"]
        self.assertEqual(parse_rate(labels), 0.5)

    def test_parse_rate_empty(self):
        self.assertEqual(parse_rate([]), 0.0)


class MultiRegistrationTest(unittest.TestCase):
    def test_conflict_detected(self):
        """真实情况：甲基硫菌灵在番茄上 3 天与 7 天并存 → 冲突。"""
        self.assertTrue(multi_registration_conflict(
            [("PD20111255", 3), ("PD20161283", 7)]))

    def test_no_conflict_when_same(self):
        self.assertFalse(multi_registration_conflict(
            [("PD1", 7), ("PD2", 7)]))

    def test_unknown_values_ignored(self):
        """未知值不参与冲突判定，但也不代表一致。"""
        self.assertFalse(multi_registration_conflict([("PD1", 7), ("PD2", None)]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
