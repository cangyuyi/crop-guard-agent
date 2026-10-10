"""确定性合规规则引擎。

**这一层不含任何模型判断。** 模型可以引用它的结论，但不能绕过它。

设计理由：用药是否合规是**可判定**的问题（药剂是否登记在该作物/防治对象、
安全间隔期是否满足采收计划、是否在禁限用名录内），把它交给概率模型去"判断"
既无必要也不可接受——这类错误在真实农业场景中代价很高。
"""

from dataclasses import dataclass
from typing import List, Optional

from .schema import BannedEntry, Registration


@dataclass(frozen=True)
class Verdict:
    blocked: bool
    rule: str        # 触发的规则名，便于归因
    reason: str      # 供评测做关键词匹配的规范化原因
    detail: str      # 给人看的说明

    @staticmethod
    def ok() -> "Verdict":
        return Verdict(blocked=False, rule="", reason="", detail="")


# 评测通过 reason 关键词判定的约定值
REASON_UNREGISTERED = "未登记"
REASON_PHI = "安全间隔期"
REASON_BANNED = "禁用"
REASON_RESTRICTED = "限用"


def registered_for(regs: List[Registration], crop: str, ingredient: str) -> List[Registration]:
    """取出某药剂在某作物上的全部登记记录（不看防治对象）。"""
    return [r for r in regs if r.crop == crop and r.active_ingredient == ingredient]


def check_ingredient(
    regs: List[Registration],
    crop: str,
    ingredient: str,
    target_disease: Optional[str] = None,
) -> Verdict:
    """三元校验第一步：药剂是否登记在该作物上（可选进一步比对防治对象）。"""
    hits = registered_for(regs, crop, ingredient)
    if not hits:
        crops = sorted({r.crop for r in regs if r.active_ingredient == ingredient})
        where = "、".join(crops) if crops else "任何作物"
        return Verdict(
            blocked=True,
            rule="R1_UNREGISTERED_CROP",
            reason=REASON_UNREGISTERED,
            detail=f"{ingredient}未登记用于{crop}（现有登记：{where}）。建议咨询当地植保站选择合规药剂。",
        )
    if target_disease:
        targets = [r.target_disease for r in hits]
        if not any(target_disease == t or target_disease in t or t in target_disease for t in targets):
            return Verdict(
                blocked=True,
                rule="R2_UNREGISTERED_TARGET",
                reason=REASON_UNREGISTERED,
                detail=f"{ingredient}在{crop}上的登记防治对象为「{'、'.join(targets)}」，不含「{target_disease}」。",
            )
    return Verdict.ok()


def check_phi(
    regs: List[Registration],
    crop: str,
    ingredient: str,
    days_to_harvest: Optional[int],
) -> Verdict:
    """三元校验第二步：安全间隔期是否满足采收计划。

    安全间隔期 = 末次施药到采收的最短间隔天数。距采收天数小于它即不可用。
    """
    if days_to_harvest is None:
        return Verdict.ok()
    hits = registered_for(regs, crop, ingredient)
    if not hits:
        return Verdict.ok()  # 未登记由 R1 负责，不在这里重复报
    phi = min(r.phi_days for r in hits)
    if days_to_harvest < phi:
        return Verdict(
            blocked=True,
            rule="R3_PHI_CONFLICT",
            reason=REASON_PHI,
            detail=(
                f"{ingredient}在{crop}上的安全间隔期为 {phi} 天，"
                f"距采收仅 {days_to_harvest} 天，不满足要求，不可使用。"
                f"可考虑延后采收，或改用非化学措施。"
            ),
        )
    return Verdict.ok()


def check_banned(banned: List[BannedEntry], ingredient: str) -> Verdict:
    """三元校验第三步：是否命中禁限用名录。"""
    for entry in banned:
        if entry.active_ingredient == ingredient:
            if entry.status == "banned":
                return Verdict(
                    blocked=True,
                    rule="R4_BANNED",
                    reason=REASON_BANNED,
                    detail=f"{ingredient}属于禁用农药（{entry.note}），不得使用。请选择已登记的合规替代药剂，或采用非化学防治。",
                )
            return Verdict(
                blocked=True,
                rule="R5_RESTRICTED",
                reason=REASON_RESTRICTED,
                detail=f"{ingredient}属于限用农药（{entry.note}），仅限特定登记范围与用途，当前场景不适用。",
            )
    return Verdict.ok()


def evaluate_ingredient(
    regs: List[Registration],
    banned: List[BannedEntry],
    crop: str,
    ingredient: str,
    target_disease: Optional[str] = None,
    days_to_harvest: Optional[int] = None,
) -> Verdict:
    """按顺序跑全部规则，返回第一个拦截结论。

    顺序有意为之：禁用/限用优先于登记与间隔期——安全层级从高到低。
    """
    for verdict in (
        check_banned(banned, ingredient),
        check_ingredient(regs, crop, ingredient, target_disease),
        check_phi(regs, crop, ingredient, days_to_harvest),
    ):
        if verdict.blocked:
            return verdict
    return Verdict.ok()
