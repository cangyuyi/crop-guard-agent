"""数据模型。

评测只检查这里定义的结构化字段，不检查自然语言措辞——
这样判分可复现，也才可能在 CI 里当门禁。
"""

from dataclasses import dataclass, field
from typing import List, Optional

# 六种期望行为，与 eval/cases.json 的 meta.behaviors 一致
BEHAVIORS = (
    "diagnose",               # 给出候选病害（含证据与置信度）
    "ask_followup",           # 证据不足，必须追问
    "refuse",                 # 无法判断，明确拒答
    "block_compliance",       # 药剂不合规，拦截并说明原因
    "compliance_alternative", # 拒绝该方案并给出合规替代方向
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
    """一条农药登记记录。

    PHI 是**按登记证号**存在的，不是按有效成分——实测同一成分（甲基硫菌灵）
    在番茄上有 3 天与 7 天两个值。所以判间隔期必须落到产品粒度。
    """

    reg_id: str
    product_name: str
    active_ingredient: str
    content: str
    crop: str
    target_disease: str
    phi_days: Optional[int]          # None = 标签未抽取到，不等于安全
    phi_status: str                  # 已抽取 / 未抽取
    source: str
    fetched_at: str
    dose: str = ""
    method: str = ""

    @property
    def phi_known(self) -> bool:
        return self.phi_days is not None


@dataclass(frozen=True)
class BannedEntry:
    active_ingredient: str
    status: str  # banned | restricted
    note: str
    source: str
    verified: bool = False
    """是否已逐条核对过农业农村部公告文号。

    当前全部为 False——名录方向性正确但未核对公告。规则命中时会把这个
    不确定性直接写进给用户的说明里，而不是替它打包票。
    """


@dataclass(frozen=True)
class VerifiedQuery:
    """官方登记查询的验证台账：(作物, 有效成分[, 防治对象]) 的查询结果。

    来自 data/verified_queries.csv，每行带查询日期，可用
    tools/fetch_registrations.py 重新跑一次核对。

    `target_disease` 为空表示只按作物查（用于 R1）；
    非空表示精确到防治对象（用于 R2）。**查询返回 0 条 = 官方系统里确实没有**，
    这是「未登记」判定的唯一依据——不靠推导、不靠猜测。
    """

    crop: str
    active_ingredient: str
    target_disease: str
    registered: bool
    hits: int

    @property
    def is_crop_level(self) -> bool:
        return not self.target_disease.strip()


@dataclass
class CaseInput:
    """一条评测用例的输入。"""

    text: str
    crop: Optional[str] = None
    image: Optional[str] = None
    days_to_harvest: Optional[int] = None
    requested_ingredient: Optional[str] = None
    requested_reg_id: Optional[str] = None
    target_disease: Optional[str] = None


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
