"""模型辅助实现：模型负责「理解」，规则层负责「判定」并能否决模型。

## 与 rule_based 的区别

| | rule_based | llm_assisted |
| --- | --- | --- |
| 作用域判定 | 允许列表（`scope.py`） | 模型判断 |
| 症状解读 | 字符二元组重合 | 模型输出候选 + 证据 |
| **合规判定** | **确定性规则** | **确定性规则（完全相同）** |
| 无模型时 | 就是它本身 | 自动降级为 rule_based |

## 两道模型过不去的闸门

1. **合规闸门**：先跑规则层。模型说「可以用甲胺磷」也没用——
   禁用名录命中就拦截。模型无权放行。
2. **知识表闸门**：模型给出的病害候选必须能在 `data/diseases.csv` 里找到，
   找不到就丢弃。**模型不能凭空造出一个病名**（这类幻觉在植保场景里会直接害人）。

这两条在 `tests/test_llm_guard.py` 里各有一条测试。
"""

import os
import re
from typing import List, Optional

from ..datasource import DataSource, load_default
from ..llm import SYSTEM_PROMPT, LlmClient, ModelReading, OpenAiCompatibleClient, read
from ..schema import CaseInput, Decision, Disease
from ..scope import has_injection, in_scope
from .rule_based import RuleBasedAgent


class LlmAssistedAgent:
    name = "llm_assisted"
    # 声明「依赖模型」：未配置 key 时 run_eval 会把结果标为 -degraded，
    # 避免降级数字被误当成该实现的成绩。
    requires_llm = True

    def __init__(self, ds: Optional[DataSource] = None,
                 client: Optional[LlmClient] = None,
                 allow_env_client: bool = True) -> None:
        self.ds = ds or load_default()
        if client is not None:
            self.client: Optional[LlmClient] = client
        elif allow_env_client:
            self.client = OpenAiCompatibleClient.from_env()
        else:
            self.client = None
        # 确定性层：合规判定与降级路径都用它，保证两条路径口径一致
        self.base = RuleBasedAgent(self.ds)
        self.diseases: List[Disease] = self.ds.diseases()

    @property
    def llm_enabled(self) -> bool:
        return self.client is not None

    def health_check(self) -> "tuple[bool, str]":
        """真实发一次请求，确认模型端可用。

        **为什么必须有这道检查**：`_read()` 会吞掉模型异常并返回空解读，
        于是「API 全程报错」与「模型正常但没读懂」在结果上无法区分——
        但前者仍会被标成「真模型」并写进真成绩文件名。宁可让运行直接失败，
        也不要产出一份标签是假的评测报告。
        """
        if not self.client:
            return False, "未配置模型客户端（缺 CROP_GUARD_LLM_BASE/KEY/MODEL）"
        try:
            raw = self.client.complete(SYSTEM_PROMPT, "连通性测试：水稻叶片有梭形病斑")
        except Exception as exc:  # noqa: BLE001
            return False, f"调用失败：{type(exc).__name__}: {exc}"
        if not re.search(r"\{.*\}", raw or "", re.S):
            return False, f"返回内容不含 JSON：{(raw or '')[:80]!r}"
        return True, "ok"

    # ---------- 入口 ----------

    def respond(self, case_input: CaseInput) -> Decision:
        text = case_input.text or ""

        # 闸门 1：提示注入 → 直接拒答，不进模型
        if has_injection(text):
            return Decision(
                behavior="refuse",
                message="这条输入试图改变系统设定。本系统只处理作物病害与用药合规问题，不会因此改变口径。",
            )

        reading = self._read(text, case_input)

        # 闸门 2（核心）：合规永远先于模型结论，模型无权放行
        if case_input.requested_ingredient or reading.ingredient:
            ci = case_input
            if not ci.requested_ingredient and reading.ingredient:
                ci = CaseInput(text=text, crop=ci.crop, days_to_harvest=ci.days_to_harvest,
                               requested_ingredient=reading.ingredient,
                               requested_reg_id=ci.requested_reg_id,
                               target_disease=ci.target_disease)
            verdict = self.base.check_compliance(ci)
            if verdict is not None:
                return verdict

        # 作用域：模型判域外则拒答；无模型时用允许列表
        if self.llm_enabled:
            if reading.scope == "out":
                return Decision(behavior="refuse",
                                message="本系统只处理作物病害的诊断与用药合规问题，这个问题不在范围内。")
        elif not in_scope(text, self.diseases, case_input.crop or ""):
            return Decision(behavior="refuse",
                            message="本系统只处理作物病害的诊断与用药合规问题，这个问题不在范围内。")

        # 无模型 → 完全交给确定性路径
        if not self.llm_enabled:
            return self.base.respond(case_input)

        # 闸门 3：模型候选必须能在知识表里找到，否则丢弃
        grounded = self._ground(reading, case_input.crop)

        # 有可用候选就诊断；**不能因为模型顺手给了追问维度就改问**。
        # （早先写成 `if reading.asked_about or not grounded`，导致模型同时给出
        #  候选与追问维度时永远不诊断——这是开发集暴露出来的集成 bug。）
        if grounded:
            return Decision(
                behavior="diagnose",
                message=f"最可能是候选首位：{grounded[0]}。请结合田间情况核对。",
                disease_candidates=grounded,
                evidence=reading.evidence[:3] or [f"{grounded[0]}：按模型解读的症状证据"],
            )

        # 没有可用候选 → 追问；维度用模型给的，为空则回落到固定词表
        return Decision(behavior="ask_followup",
                        message="现有信息不足以区分病害，请补充以下维度。",
                        asked_about=self._normalize_asked(reading.asked_about))

    # 评测与前端约定的判别维度词表。模型输出的自由措辞需归一到这套词，
    # 否则下游无法判定"到底问清了没有"。
    _DIM_VOCAB = {
        "部位": ("部位", "位置", "哪个部位", "叶片", "果实", "茎", "根", "穗", "叶鞘", "枝梢"),
        "颜色": ("颜色", "色泽", "色彩", "什么色"),
        "形状": ("形状", "形态", "病斑", "斑形", "轮纹", "云纹"),
        "叶背": ("叶背", "背面", "叶背面"),
        "条件": ("条件", "环境", "湿度", "温度", "天气", "棚内", "管理", "栽培", "生育期"),
    }

    @classmethod
    def _normalize_asked(cls, asked: List[str]) -> List[str]:
        """把模型的自由措辞归一成约定词表。

        归一到固定词表不是"迁就评测"，而是**接口契约**：下游要据它判断
        "是否问清了判别所必需的信息"，自由措辞无法判定。
        """
        out: List[str] = []
        for raw in asked:
            text = str(raw)
            for dim, keys in cls._DIM_VOCAB.items():
                if any(k in text for k in keys) and dim not in out:
                    out.append(dim)
        return out or ["部位", "颜色", "形状", "叶背"]

    # ---------- 内部 ----------

    def _read(self, text: str, ci: CaseInput) -> ModelReading:
        if not self.client:
            return ModelReading()
        try:
            return read(self.client, text)
        except Exception:  # noqa: BLE001 — 模型不可用不应让整个请求失败
            return ModelReading()

    def _ground(self, reading: ModelReading, crop: Optional[str]) -> List[str]:
        """把模型候选收窄到知识表里真实存在的病害名，并保持优先级顺序。"""
        known = [d.name for d in self.diseases if (crop is None or d.crop == crop)] or \
                [d.name for d in self.diseases]
        out: List[str] = []
        for cand in reading.candidates:
            for name in known:
                if cand in name or name in cand:
                    if name not in out:
                        out.append(name)
                    break
        return out[:3]
