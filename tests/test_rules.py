"""规则引擎单测。

这一层是纯函数、零模型依赖，所以必须 100% 可测——
用药合规是安全问题，不能靠"跑几个样例看看"。

**所有断言基于 data/ 下的真实快照数据**（中国农药信息网官方查询，
拉取于 2026-10-10）。数据一变，这些测试就会失败——这是有意为之。
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
        cls.facts = ds.r1_facts()

    def evaluate(self, crop, ing, **kw):
        return rules.evaluate_ingredient(self.facts, self.regs, self.banned, crop, ing, **kw)

    # ---- R1 未登记作物（真实未登记组合，已对官方查询核对）----

    def test_r1_not_registered_real_pairs(self):
        """这 5 组是官方登记查询返回 0 条的组合，必须拦截。"""
        for crop, ing in [
            ("水稻", "代森锌"), ("水稻", "啶酰菌胺"), ("柑橘树", "啶酰菌胺"),
            ("水稻", "木霉菌"), ("柑橘树", "木霉菌"),
        ]:
            with self.subTest(crop=crop, ing=ing):
                v = self.evaluate(crop, ing)
                self.assertTrue(v.blocked, f"{ing} 在 {crop} 上应被拦截")
                self.assertEqual(v.reason, rules.REASON_UNREGISTERED)

    def test_r1_registered_pairs_pass_r1(self):
        """官方查询有结果的组合，不应被 R1 拦截。

        注意这**不等于**整体合规——后面还有 R2/R3。
        """
        for crop, ing in [("番茄", "氢氧化铜"), ("水稻", "甲基硫菌灵"), ("番茄", "多菌灵")]:
            with self.subTest(crop=crop, ing=ing):
                self.assertFalse(rules.check_ingredient(self.facts, crop, ing).blocked)

    def test_r1_missing_fact_requires_confirmation_not_pass(self):
        """本地没有该组合的查询记录时，**必须转人工**，不能当成通过。"""
        v = rules.check_ingredient(self.facts, "番茄", "某个没查过的成分")
        self.assertTrue(v.needs_confirmation)

    # ---- R2 未登记防治对象 ----

    def test_r2_insecticide_on_disease_blocked(self):
        """吡虫啉登记在番茄上（9 条），但它是杀虫剂，不登记用于叶霉病。"""
        v = self.evaluate("番茄", "吡虫啉", target_disease="叶霉病")
        self.assertTrue(v.blocked)
        self.assertEqual(v.rule, "R2_UNREGISTERED_TARGET")

    # ---- R3 安全间隔期：按登记证号判定 ----

    def test_r3_conflict_blocked(self):
        """中生菌素在水稻上 3 天；距采收 2 天 → 拦截。"""
        v = self.evaluate("水稻", "中生菌素", target_disease="白叶枯病", days_to_harvest=2)
        self.assertTrue(v.blocked)
        self.assertEqual(v.reason, rules.REASON_PHI)

    def test_r3_14_days_blocked(self):
        """噻森铜在水稻上 14 天；距采收 10 天 → 拦截。"""
        v = self.evaluate("水稻", "噻森铜", target_disease="白叶枯病", days_to_harvest=10)
        self.assertTrue(v.blocked)
        self.assertEqual(v.reason, rules.REASON_PHI)

    def test_r3_sufficient_not_blocked(self):
        """E 类诱饵：间隔够就**不能**拦，否则系统可以靠一律拒绝刷高安全指标。"""
        v = self.evaluate("水稻", "中生菌素", target_disease="白叶枯病", days_to_harvest=30)
        self.assertFalse(v.blocked)
        self.assertFalse(v.needs_confirmation)

    def test_r3_boundary_exact_allowed(self):
        """距采收天数 == 间隔期时应放行（间隔期是「不少于」）。"""
        v = self.evaluate("水稻", "中生菌素", target_disease="白叶枯病", days_to_harvest=3)
        self.assertFalse(v.blocked)

    def test_r3_multi_registration_requires_product(self):
        """★ 真实数据发现：甲基硫菌灵在番茄上 PD20111255=3天、PD20161283=7天。

        间隔期不一致时**不得给单一数值**，必须提示按手中产品的登记证号核对。
        """
        v = self.evaluate("番茄", "甲基硫菌灵", target_disease="叶霉病", days_to_harvest=5)
        self.assertTrue(v.needs_confirmation)
        self.assertEqual(v.rule, "R3_PHI_MULTI")
        self.assertEqual(v.reason, rules.REASON_PHI_MULTI)

    def test_r3_multi_registration_with_regid_resolves(self):
        """指定了登记证号就能给出确定结论：PD20111255 是 3 天，距采收 5 天 → 放行。"""
        v = self.evaluate("番茄", "甲基硫菌灵", target_disease="叶霉病",
                          days_to_harvest=5, reg_id="PD20111255")
        self.assertFalse(v.blocked)
        self.assertFalse(v.needs_confirmation)

    def test_r3_with_regid_7_days_blocks(self):
        """同一作物、同一成分，换 PD20161283（7 天），距采收 5 天 → 拦截。

        这一对用例证明「按登记证号判定」不是设计洁癖，而是会改变结论的。
        """
        v = self.evaluate("番茄", "甲基硫菌灵", target_disease="叶霉病",
                          days_to_harvest=5, reg_id="PD20161283")
        self.assertTrue(v.blocked)
        self.assertEqual(v.reason, rules.REASON_PHI)

    def test_r3_unknown_phi_requires_confirmation(self):
        """★ 间隔期未知 ≠ 安全。标签没抽到就必须转人工，不能放行。"""
        v = self.evaluate("番茄", "百菌清", target_disease="早疫病", days_to_harvest=1)
        self.assertTrue(v.needs_confirmation)
        self.assertEqual(v.rule, "R3_PHI_UNKNOWN")
        self.assertFalse(v.blocked)  # 是"需确认"，不是"已拦截"

    # ---- R4/R5 禁限用 ----

    def test_banned_blocked(self):
        for ing in ["甲胺磷", "对硫磷", "久效磷", "百草枯"]:
            with self.subTest(ing=ing):
                v = self.evaluate("水稻", ing)
                self.assertTrue(v.blocked)
                self.assertEqual(v.reason, rules.REASON_BANNED)

    def test_unverified_banned_entry_discloses_uncertainty(self):
        """名录条目未核对公告文号时，说明里必须带提醒。

        这条钉住的是"可追责"的底线：依据本身不确定时，必须把不确定性
        告诉用户，而不是替它打包票。数据核对完成后把 verified 改成「是」，
        本测试会失败——那时应当同时删掉这条断言。
        """
        v = self.evaluate("水稻", "甲胺磷")
        self.assertTrue(v.blocked)
        self.assertIn("未", v.detail)
        self.assertIn("公告", v.detail)

    def test_restricted_blocked(self):
        v = self.evaluate("水稻", "氟虫腈")
        self.assertTrue(v.blocked)
        self.assertEqual(v.reason, rules.REASON_RESTRICTED)

    def test_banned_priority_over_everything(self):
        """层级：禁用优先于登记与间隔期。"""
        v = self.evaluate("水稻", "甲胺磷", days_to_harvest=999)
        self.assertEqual(v.rule, "R4_BANNED")

    def test_block_priority_over_confirm(self):
        """既命中拦截又命中需确认时，返回拦截（安全层级更高）。"""
        v = self.evaluate("水稻", "代森锌", target_disease="稻瘟病", days_to_harvest=1)
        self.assertTrue(v.blocked)
        self.assertEqual(v.reason, rules.REASON_UNREGISTERED)


class DataIntegrityTest(unittest.TestCase):
    def test_every_registration_has_provenance(self):
        """每条登记都必须能追溯到来源与拉取日期。"""
        for r in load_default().registrations():
            with self.subTest(reg_id=r.reg_id):
                self.assertTrue(r.source.strip())
                self.assertTrue(r.fetched_at.strip())

    def test_phi_status_consistent_with_value(self):
        """phi_status 必须与 phi_days 一致，不允许"状态说已抽取但值是空"。"""
        for r in load_default().registrations():
            with self.subTest(reg_id=r.reg_id):
                if r.phi_known:
                    self.assertEqual(r.phi_status, "已抽取")
                else:
                    self.assertEqual(r.phi_status, "未抽取")

    def test_r1_facts_cover_both_polarities(self):
        """R1 真值表必须同时含「已登记」与「未登记」——否则 D 类用例无从判定。"""
        facts = load_default().r1_facts()
        self.assertTrue(any(f.registered for f in facts))
        self.assertTrue(any(not f.registered for f in facts))

    def test_huanglongbing_has_no_chemical_control(self):
        """黄龙病必须标为无化学治疗手段——否则可能被开出"药方"。"""
        hl = [d for d in load_default().diseases() if d.name == "柑橘黄龙病"]
        self.assertEqual(len(hl), 1)
        self.assertFalse(hl[0].has_chemical_control)


if __name__ == "__main__":
    unittest.main(verbosity=2)
