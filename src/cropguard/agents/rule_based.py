"""规则驱动的实现。

这是**第一个真正用上合规引擎**的实现，用来对照 naive 基线，
证明评测集在两个方向上都有效：既抓得住错，也认得出对。

行为决策顺序（安全优先）：

1. 点名了药剂 → 先跑合规引擎
   - 拦截（禁用/未登记/间隔期不足）→ block_compliance / compliance_alternative
   - 需确认（间隔期未知或需指定产品）→ ask_followup，问清产品信息
2. 没点名药剂 → 走诊断流程
   - 症状信息不足 → ask_followup，追问判别所必需的维度
   - 与作物/病害无关 → refuse
   - 信息充分 → diagnose，带证据引用

**不说剂量**：本实现从不输出处方级用量（dose_given 恒为 False）。
"""

import re
from typing import List, Optional, Tuple

from ..datasource import DataSource, load_default
from ..rules import KIND_BLOCK, evaluate_all
from ..schema import CaseInput, Decision, Disease
from ..scope import has_injection, in_scope

# 症状判别最依赖的几个维度——追问时按此顺序问
ASK_DIMENSIONS = ["部位", "颜色", "形状", "叶背", "霉层", "条件", "部位"]


class RuleBasedAgent:
    name = "rule_based"

    def __init__(self, ds: Optional[DataSource] = None) -> None:
        self.ds = ds or load_default()
        self.queries = self.ds.r1_facts()
        self.regs = self.ds.registrations()
        self.banned = self.ds.banned()
        self.diseases: List[Disease] = self.ds.diseases()

    # ---------- 入口 ----------

    def respond(self, case_input: CaseInput) -> Decision:
        text = case_input.text or ""

        # ① 先处理用药诉求：合规永远优先于诊断
        if case_input.requested_ingredient:
            decision = self.check_compliance(case_input)
            if decision is not None:
                return decision

        # ② 提示注入 → 拒答（次要防线，见 scope.INJECTION_PATTERNS 的说明）
        if has_injection(text):
            return Decision(
                behavior="refuse",
                message="这条输入试图改变系统设定。本系统只处理作物病害与用药合规问题，不会因此改变口径。",
            )

        # ②b 域外 → 拒答。**允许列表**：没有植保信号且未指明作物才拒绝
        #     （原来用关键词黑名单，泛化差；改用允许列表后留出集拒答正确率 0 → 0.75）
        if not in_scope(text, self.diseases, case_input.crop or ""):
            return Decision(
                behavior="refuse",
                message="本系统只处理作物病害的诊断与用药合规问题，这个问题不在范围内。",
            )

        # ③ 无化学治疗手段的病害 + 索要用药方案 → 拒答
        #    （黄龙病一类不存在"治疗方案"，给方案比不给更危险）
        named = self._mentioned_disease(text, case_input.crop)
        if named is not None and not named.has_chemical_control and self._wants_medication(text):
            return Decision(
                behavior="refuse",
                message=(f"{named.name}没有化学治疗手段，不存在可开出的药方。"
                         f"正确处置是清除病株并防控媒介昆虫，具体请咨询当地植保站。"),
                disease_candidates=[named.name],
                evidence=[f"{named.name}：{named.notes}"],
            )

        # ④ 用户已点名病害 → 按其开展，不必再追问症状
        if named is not None:
            return self._diagnose(text, case_input.crop, forced=named)

        # ⑤ 症状信息不足 → 追问（不得直接下结论）
        if self._is_underspecified(text, case_input.crop):
            return Decision(
                behavior="ask_followup",
                message="现有描述不足以区分是哪一种病害，请补充以下信息。",
                asked_about=self._missing_dimensions(text),
            )

        # ⑥ 诊断
        return self._diagnose(text, case_input.crop)

    # ---------- 病害点名 ----------

    def _mentioned_disease(self, text: str, crop: Optional[str]) -> Optional[Disease]:
        pool = [d for d in self.diseases if (crop is None or d.crop == crop)] or self.diseases
        for d in pool:
            short = d.name.replace(d.crop, "") if d.crop in d.name else d.name
            if d.name in text or (len(short) >= 3 and short in text):
                return d
        return None

    @staticmethod
    def _wants_medication(text: str) -> bool:
        return bool(re.search(r"药|打药|用药|喷|配方|药方|克|剂量", text))

    # ---------- 合规 ----------

    def check_compliance(self, ci: CaseInput) -> Optional[Decision]:
        """返回拦截/需确认的结论；**全部通过时返回 None**，让流程继续走诊断。

        公开方法：`LlmAssistedAgent` 复用它作为不可绕过的合规层。

        注意这里刻意不返回"核对通过"的 Decision——早先那样写会导致
        用户既问了用药又描述了症状时拿不到诊断结果（评测暴露出来的）。
        """
        crop = ci.crop
        if not crop:
            return None
        verdicts = evaluate_all(
            self.queries, self.regs, self.banned, crop, ci.requested_ingredient,
            target_disease=ci.target_disease,
            days_to_harvest=ci.days_to_harvest, reg_id=ci.requested_reg_id,
        )
        if not verdicts:
            return None

        top = verdicts[0]
        if top.kind == KIND_BLOCK:
            behavior = ("compliance_alternative"
                        if top.rule in ("R4_BANNED", "R5_RESTRICTED") else "block_compliance")
            return Decision(behavior=behavior, reason=top.reason, message=top.detail,
                            evidence=[f"依据规则 {top.rule}"])

        # 需确认：把要问的信息说清楚
        asked = ["登记证号", "产品", "标签"] if top.rule == "R3_PHI_MULTI" else ["标签", "间隔期"]
        return Decision(behavior="ask_followup", reason=top.reason, message=top.detail,
                        asked_about=asked)

    # ---------- 输入分类 ----------

    # ---------- 症状匹配 ----------

    _PUNCT = re.compile(r"[\s，。、；：（）()\-—的]")

    @classmethod
    def _bigrams(cls, s: str) -> set:
        s = cls._PUNCT.sub("", s or "")
        if len(s) < 2:
            return {s} if s else set()
        return {s[i:i + 2] for i in range(len(s) - 1)}

    @classmethod
    def _symptom_hits(cls, text: str, disease: Disease) -> List[str]:
        """字符二元组重合度。

        用整短语子串匹配会在真实表述上失效——用户写「叶片正面**有**边界不清的
        淡黄色斑块」，而知识表写「叶正面边界不清的淡黄色斑块」，插一个字就匹配不上。
        二元组对插入与语序变动更宽容。
        """
        tb = cls._bigrams(text)
        hits = []
        for phrase in disease.key_symptoms + disease.favored_conditions:
            pb = cls._bigrams(phrase)
            if not pb:
                continue
            ratio = len(tb & pb) / len(pb)
            if ratio >= 0.5:
                hits.append(phrase)
        return hits

    def _is_underspecified(self, text: str, crop: Optional[str]) -> bool:
        """判据：所有候选病害都没有一条症状被充分命中，说明描述不足以区分。"""
        pool = [d for d in self.diseases if (crop is None or d.crop == crop)] or self.diseases
        best = max((len(self._symptom_hits(text, d)) for d in pool), default=0)
        return best < 1

    @staticmethod
    def _missing_dimensions(text: str) -> List[str]:
        dims = []
        if not re.search(r"叶|果|茎|根|穗|鞘|梢|枝", text):
            dims.append("部位")
        if not re.search(r"黄|褐|灰|白|黑|绿|红|银", text):
            dims.append("颜色")
        if not re.search(r"斑|霉|腐|枯|萎|点|粉|轮纹|云纹|梭形|火山口", text):
            dims.append("形状")
        if "叶背" not in text:
            dims.append("叶背")
        if not re.search(r"湿度|阴天|雨|温|棚|密植|通风", text):
            dims.append("条件")
        return dims or ["症状细节"]

    # ---------- 诊断 ----------

    def _diagnose(self, text: str, crop: Optional[str],
                  forced: Optional[Disease] = None) -> Decision:
        pool = [d for d in self.diseases if (crop is None or d.crop == crop)] or self.diseases
        scored: List[Tuple[int, Disease, List[str]]] = []
        if forced is not None:
            hits = self._symptom_hits(text, forced)
            scored.append((max(len(hits), 1) + 10, forced, hits or forced.key_symptoms[:1]))
        for d in pool:
            if forced is not None and d.name == forced.name:
                continue
            hits = self._symptom_hits(text, d)
            if hits:
                scored.append((len(hits), d, hits))
        scored.sort(key=lambda x: (-x[0], x[1].name))

        if not scored:
            return Decision(
                behavior="ask_followup",
                message="没有识别到可用于判别的症状特征，请描述病斑部位、颜色、形状以及叶背情况。",
                asked_about=["部位", "颜色", "形状", "叶背"],
            )

        top = scored[:3]
        # 无化学治疗手段的病害（如黄龙病）不得给出药剂方向
        candidates = [d.name for _, d, _ in top]
        no_chem = [d for _, d, _ in top if not d.has_chemical_control]
        message = f"最可能是候选首位：{candidates[0]}。请结合田间情况核对。"
        if no_chem:
            message += (f"注意：{no_chem[0].name}无化学治疗手段，须清除病株并防控媒介昆虫，"
                        f"不要按病害打药。")
        return Decision(
            behavior="diagnose",
            message=message,
            disease_candidates=candidates,
            evidence=[f"{d.name}：{'、'.join(h)}" for _, d, h in top][:3],
            dose_given=False,
        )
