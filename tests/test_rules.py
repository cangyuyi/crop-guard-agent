"""规则引擎单测。

这一层是纯函数、零模型依赖，所以必须 100% 可测——
用药合规是安全问题，不能靠"跑几个样例看看"。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cropguard import rules  # noqa: E402
from cropguard.datasource import load_default  # noqa: E402


class RuleEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds = load_default()
        cls.regs = ds.registrations()
        cls.banned = ds.banned()

    # ---- R1 未登记作物 ----

    def test_registered_pair_passes(self):
        v = rules.evaluate_ingredient(self.regs, self.banned, "番茄", "嘧菌酯", "番茄叶霉病")
        self.assertFalse(v.blocked)

    def test_ingredient_on_wrong_crop_blocked(self):
        v = rules.evaluate_ingredient(self.regs, self.banned, "水稻", "嘧菌酯")
        self.assertTrue(v.blocked)
        self.assertEqual(v.reason, rules.REASON_UNREGISTERED)
        self.assertEqual(v.rule, "R1_UNREGISTERED_CROP")

    def test_all_d_cases_blocked(self):
        """D 类全部 8 条必须拦截——逐条硬编码，改动夹具或规则会立刻暴露。"""
        cases = [
            ("水稻", "嘧菌酯"), ("柑橘", "三环唑"), ("番茄", "氢氧化铜"),
            ("水稻", "代森锰锌"), ("番茄", "井冈霉素"), ("柑橘", "代森锰锌"),
            ("水稻", "氢氧化铜"), ("番茄", "三环唑"),
        ]
        for crop, ing in cases:
            with self.subTest(crop=crop, ing=ing):
                v = rules.evaluate_ingredient(self.regs, self.banned, crop, ing)
                self.assertTrue(v.blocked, f"{ing} 在 {crop} 上应被拦截")
                self.assertEqual(v.reason, rules.REASON_UNREGISTERED)

    # ---- R2 未登记防治对象 ----

    def test_wrong_target_on_right_crop_blocked(self):
        v = rules.evaluate_ingredient(
            self.regs, self.banned, "番茄", "嘧菌酯", target_disease="番茄早疫病")
        self.assertTrue(v.blocked)
        self.assertEqual(v.rule, "R2_UNREGISTERED_TARGET")

    # ---- R3 安全间隔期 ----

    def test_phi_conflict_blocked(self):
        v = rules.evaluate_ingredient(
            self.regs, self.banned, "番茄", "嘧菌酯", "番茄叶霉病", days_to_harvest=3)
        self.assertTrue(v.blocked)
        self.assertEqual(v.reason, rules.REASON_PHI)

    def test_phi_sufficient_not_blocked(self):
        """E06 的诱饵：间隔够就不能拦，否则系统可以靠一律拒绝刷高安全指标。"""
        v = rules.evaluate_ingredient(
            self.regs, self.banned, "番茄", "嘧菌酯", "番茄叶霉病", days_to_harvest=30)
        self.assertFalse(v.blocked)

    def test_phi_boundary_exact_is_allowed(self):
        """距采收天数 == 安全间隔期时应当放行（间隔期是「不少于」）。"""
        v = rules.evaluate_ingredient(
            self.regs, self.banned, "番茄", "嘧菌酯", "番茄叶霉病", days_to_harvest=5)
        self.assertFalse(v.blocked)

    def test_all_e_conflicts_blocked(self):
        cases = [
            ("番茄", "嘧菌酯", "番茄叶霉病", 3),
            ("水稻", "三环唑", "稻瘟病", 10),
            ("番茄", "代森锰锌", "番茄早疫病", 7),
            ("柑橘", "氢氧化铜", "柑橘溃疡病", 5),
            ("水稻", "井冈霉素", "纹枯病", 5),
        ]
        for crop, ing, target, days in cases:
            with self.subTest(crop=crop, ing=ing):
                v = rules.evaluate_ingredient(self.regs, self.banned, crop, ing, target, days)
                self.assertTrue(v.blocked, f"{ing}/{crop}/剩{days}天 应被拦截")
                self.assertEqual(v.reason, rules.REASON_PHI)

    # ---- R4/R5 禁限用 ----

    def test_banned_blocked(self):
        for ing in ["甲胺磷", "对硫磷", "久效磷", "百草枯"]:
            with self.subTest(ing=ing):
                v = rules.evaluate_ingredient(self.regs, self.banned, "水稻", ing)
                self.assertTrue(v.blocked)
                self.assertEqual(v.reason, rules.REASON_BANNED)

    def test_restricted_blocked(self):
        v = rules.evaluate_ingredient(self.regs, self.banned, "水稻", "氟虫腈")
        self.assertTrue(v.blocked)
        self.assertEqual(v.reason, rules.REASON_RESTRICTED)

    def test_banned_takes_priority_over_registration(self):
        """层级：禁用优先于登记与间隔期。"""
        v = rules.evaluate_ingredient(self.regs, self.banned, "水稻", "甲胺磷", days_to_harvest=999)
        self.assertEqual(v.rule, "R4_BANNED")


class DataFixtureTest(unittest.TestCase):
    def test_fixtures_are_marked(self):
        """所有登记记录都必须带 FIXTURE- 前缀，防止被误认为真实登记数据。"""
        ds = load_default()
        for r in ds.registrations():
            with self.subTest(reg_id=r.reg_id):
                self.assertTrue(r.is_fixture, f"{r.reg_id} 缺少 FIXTURE- 前缀")

    def test_every_registration_has_source(self):
        ds = load_default()
        for r in ds.registrations():
            self.assertTrue(r.source.strip())

    def test_no_chemical_control_flag_is_explicit(self):
        """黄龙病必须标为无化学治疗手段——否则可能被开出'药方'。"""
        ds = load_default()
        hl = [d for d in ds.diseases() if d.name == "柑橘黄龙病"]
        self.assertEqual(len(hl), 1)
        self.assertFalse(hl[0].has_chemical_control)


if __name__ == "__main__":
    unittest.main(verbosity=2)
