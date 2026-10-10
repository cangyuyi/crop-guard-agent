"""数据模型。

评测只检查这里定义的结构化字段，不检查自然语言措辞——
这样判分可复现，也才可能在 CI 里当门禁。
"""

from dataclasses import dataclass, field
from typing import List, Optional

# 六种期望行为，与 eval/cases.json 的 meta.behaviors 一致
BEHAVIORS = (
    "diagnose",              # 给出候选病害（含证据与置信度）
    "ask_followup",          # 证据不足，必须追问
    "refuse",                # 无法判断，明确拒答
    "block_compliance",      # 药剂不合规，拦截并说明原因
    "compliance_alternative",# 拒绝该方案并给出合规替代方向
)


@dataclass(frozen=True)
class Disease:
    disease_id: str
    crop: str
    name: str
    key_symptoms: List[str]
    favored_conditions: List[str]
    has_chemical_control: bool
    notes: str = ""


@dataclass(frozen=True)
class Registration:
    reg_id: str
    active_ingredient: str
    crop: str
    target_disease: str
    dilution: str
    phi_days: int
    source: str

    @property
    def is_fixture(self) -> bool:
        return self.reg_id.startswith("FIXTURE-")


@dataclass(frozen=True)
class BannedEntry:
    active_ingredient: str
    status: str  # banned | restricted
    note: str
    source: str


@dataclass
class CaseInput:
    """一条评测用例的输入。"""

    text: str
    crop: Optional[str] = None
    image: Optional[str] = None
    days_to_harvest: Optional[int] = None
    requested_ingredient: Optional[str] = None


@dataclass
class Decision:
    """系统的结构化输出。评测器只看这个对象。"""

    behavior: str
    message: str = ""
    disease_candidates: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    asked_about: List[str] = field(default_factory=list)
    recommended_ingredients: List[str] = field(default_factory=list)
    reason: str = ""
    dose_given: bool = False  # 是否给出了处方级用量（安全红线，任何情况下都应为 False）

    def __post_init__(self) -> None:
        if self.behavior not in BEHAVIORS:
            raise ValueError(f"未知 behavior: {self.behavior}")
