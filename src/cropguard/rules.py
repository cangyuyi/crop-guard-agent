"""确定性合规规则引擎。

**这一层不含任何模型判断。** 模型可以引用它的结论，但不能绕过它。

设计理由：用药是否合规是**可判定**的问题（药剂是否登记在该作物/防治对象、
安全间隔期是否满足采收计划、是否在禁限用名录内），把它交给概率模型去"判断"
既无必要也不可接受——这类错误在真实农业场景中代价很高。

三条来自真实数据的规则（不是凭空设计的）：
- **R1 用官方登记查询的验证结果**（data/verified_queries.csv），而不是自建的假设
- **R3 按登记证号判间隔期**：实测同一成分（甲基硫菌灵）在番茄上有 3 天与 7 天两值，
  所以间隔期是产品属性，不是成分属性
- **R3-UNKNOWN：间隔期未知 ≠ 安全**。标签里抽不到就必须转人工核对，
  绝不能因为"没查到问题"就放行
"""

from dataclasses import dataclass
from typing import List, Optional

from .schema import BannedEntry, Registration, VerifiedQuery

# 结论类型：ok 放行 / block 拦截 / confirm 需人工确认
KIND_OK, KIND_BLOCK, KIND_CONFIRM = "ok", "block", "confirm"

# 评测通过 reason 关键词判定的约定值
REASON_UNREGISTERED = "未登记"
REASON_PHI = "安全间隔期"
REASON_PHI_UNKNOWN = "间隔期未知"
REASON_PHI_MULTI = "以产品标签为准"
REASON_BANNED = "禁用"
REASON_RESTRICTED = "限用"


@dataclass(frozen=True)
class Verdict:
    kind: str
    rule: str = ""
    reason: str = ""
    detail: str = ""

    @property
    def blocked(self) -> bool:
        return self.kind == KIND_BLOCK

    @property
    def needs_confirmation(self) -> bool:
        return self.kind == KIND_CONFIRM

    @staticmethod
    def ok() -> "Verdict":
        return Verdict(kind=KIND_OK)


def _block(rule: str, reason: str, detail: str) -> Verdict:
    return Verdict(kind=KIND_BLOCK, rule=rule, reason=reason, detail=detail)


def _confirm(rule: str, reason: str, detail: str) -> Verdict:
    return Verdict(kind=KIND_CONFIRM, rule=rule, reason=reason, detail=detail)


def regs_for(regs: List[Registration], crop: str, ingredient: str) -> List[Registration]:
    return [r for r in regs if r.crop == crop and r.active_ingredient == ingredient]


def check_ingredient(queries: List[VerifiedQuery], crop: str, ingredient: str) -> Verdict:
    """R1：该成分是否登记在该作物上（作物级查询）。

    依据来自官方登记查询的台账（data/verified_queries.csv），每行带查询日期。
    查不到该组合的记录时**转人工**，而不是当作通过——没有记录 ≠ 没有风险。
    """
    fact = next((q for q in queries
                 if q.is_crop_level and q.crop == crop and q.active_ingredient == ingredient), None)
    if fact is None:
        return _confirm("R1_NO_RECORD", REASON_UNREGISTERED,
                        f"本地台账里没有「{ingredient} × {crop}」的登记查询记录，"
                        f"请先在官方登记查询系统核对，不要据此下结论。")
    if not fact.registered:
        return _block("R1_UNREGISTERED_CROP", REASON_UNREGISTERED,
                      f"经官方登记查询核对，{ingredient}未登记用于{crop}（查询结果 0 条）。"
                      f"建议咨询当地植保站选择合规药剂。")
    return Verdict.ok()


def check_target_disease(queries: List[VerifiedQuery], regs: List[Registration],
                         crop: str, ingredient: str,
                         target_disease: Optional[str]) -> Verdict:
    """R2：是否登记在该防治对象上。

    证据来源有两条，优先级不同：
    1. **防治对象级查询台账**——官方系统返回 0 条即为「未登记」，可据此拦截
    2. **登记快照里的正向记录**——只要快照里存在 (作物, 成分, 病害) 这条登记，
       就是「已登记」的正面证据，可据此放行

    两条都没有 → 转人工。**「快照里没有」不构成未登记的证据**，因为快照是抽样的。
    """
    if not target_disease:
        return Verdict.ok()

    fact = next((q for q in queries
                 if not q.is_crop_level and q.crop == crop
                 and q.active_ingredient == ingredient
                 and (target_disease in q.target_disease or q.target_disease in target_disease)), None)
    if fact is not None:
        if fact.registered:
            return Verdict.ok()
        return _block("R2_UNREGISTERED_TARGET", REASON_UNREGISTERED,
                      f"官方查询显示「{crop} × {ingredient} × {target_disease}」为 0 条，"
                      f"{ingredient}未登记用于防治{crop}的{target_disease}。")

    # 没有查询记录时，看快照里有没有正向登记证据
    positive = [r for r in regs_for(regs, crop, ingredient)
                if target_disease in r.target_disease or r.target_disease in target_disease]
    if positive:
        return Verdict.ok()

    return _confirm("R2_NO_RECORD", REASON_UNREGISTERED,
                    f"本地台账与快照里都没有「{crop} × {ingredient} × {target_disease}」的记录，"
                    f"无法确认是否登记该防治对象，请先核对官方查询。")


def check_phi(regs: List[Registration], crop: str, ingredient: str,
              days_to_harvest: Optional[int],
              reg_id: Optional[str] = None) -> Verdict:
    """R3：安全间隔期。**按登记证号判定，不按成分判定。**

    三种情况分别处理：
    1. 多登记且间隔期不一致 → 不得给单一数字，要求以手中产品标签为准
    2. 间隔期未知（标签未抽取到）→ 转人工核对，**不放行也不拦截**
    3. 有值且距采收不足 → 拦截
    """
    if days_to_harvest is None:
        return Verdict.ok()

    hits = regs_for(regs, crop, ingredient)
    if not hits:
        return Verdict.ok()

    if reg_id:
        hits = [r for r in hits if r.reg_id == reg_id] or hits

    known = [r for r in hits if r.phi_known]
    if not known:
        return _confirm("R3_PHI_UNKNOWN", REASON_PHI_UNKNOWN,
                        f"{ingredient}在{crop}上的安全间隔期未从标签抽取到，"
                        f"无法确认是否满足距采收 {days_to_harvest} 天。请核对手中产品标签后再决定。")

    values = {r.phi_days for r in known}
    if len(values) > 1 and not reg_id:
        pairs = "、".join(f"{r.reg_id}={r.phi_days}天" for r in known)
        return _confirm("R3_PHI_MULTI", REASON_PHI_MULTI,
                        f"同一成分在{crop}上有多个登记，安全间隔期并不相同（{pairs}）。"
                        f"间隔期是产品属性，请按**手中产品的登记证号**核对标签，本系统不给单一数值。")

    phi = min(values)
    if days_to_harvest < phi:
        return _block("R3_PHI_CONFLICT", REASON_PHI,
                      f"{ingredient}在{crop}上的安全间隔期为 {phi} 天，"
                      f"距采收仅 {days_to_harvest} 天，不满足要求，不可使用。"
                      f"可考虑延后采收，或改用非化学措施。")
    return Verdict.ok()


def check_banned(banned: List[BannedEntry], ingredient: str) -> Verdict:
    """R4/R5：是否命中禁限用名录。"""
    for entry in banned:
        if entry.active_ingredient != ingredient:
            continue
        # 名录条目若尚未核对公告文号，把这份不确定性直接告诉用户，
        # 而不是替它打包票——这是本项目对"可追责"的最低要求。
        caveat = "" if entry.verified else "（该名录条目尚未逐条核对公告文号，请以农业农村部最新公告为准）"
        if entry.status == "banned":
            return _block("R4_BANNED", REASON_BANNED,
                          f"{ingredient}属于禁用农药（{entry.note}），不得使用。"
                          f"请选择已登记的合规替代药剂，或采用非化学防治。{caveat}")
        return _block("R5_RESTRICTED", REASON_RESTRICTED,
                      f"{ingredient}属于限用农药（{entry.note}），仅限特定登记范围与用途，当前场景不适用。{caveat}")
    return Verdict.ok()


# 台账"查不到记录"归到最低层：它确实需要人工核对，但不该盖住
# 更具体、更可行动的结论（比如"间隔期未知"或"间隔期不足"）。
_NOTE_RULES = {"R1_NO_RECORD", "R2_NO_RECORD"}


def evaluate_all(queries: List[VerifiedQuery], regs: List[Registration],
                 banned: List[BannedEntry], crop: str, ingredient: str,
                 target_disease: Optional[str] = None,
                 days_to_harvest: Optional[int] = None,
                 reg_id: Optional[str] = None) -> List[Verdict]:
    """返回**全部**非 ok 的发现，按安全层级排序。

    分三层，从高到低：
      1. 拦截（禁用/限用、未登记作物、未登记防治对象、间隔期不足）
      2. 需确认——间隔期相关（未知 / 多登记不一致）
      3. 需确认——台账缺记录

    调用方应把全部发现一起呈现；判"行为"时取第一条即可。
    """
    found = [
        check_banned(banned, ingredient),
        check_ingredient(queries, crop, ingredient),
        check_target_disease(queries, regs, crop, ingredient, target_disease),
        check_phi(regs, crop, ingredient, days_to_harvest, reg_id),
    ]
    found = [v for v in found if v.kind != KIND_OK]

    def tier(v: Verdict) -> int:
        if v.blocked:
            return 0
        return 2 if v.rule in _NOTE_RULES else 1

    return sorted(found, key=tier)


def evaluate_ingredient(queries: List[VerifiedQuery], regs: List[Registration],
                        banned: List[BannedEntry], crop: str, ingredient: str,
                        target_disease: Optional[str] = None,
                        days_to_harvest: Optional[int] = None,
                        reg_id: Optional[str] = None) -> Verdict:
    """取优先级最高的那条结论；没有任何发现则返回 ok。

    落进 confirm（而非 block）的那些不会放行——调用方必须转人工，
    这也让「数据缺失」不会被当成「安全」。
    """
    found = evaluate_all(queries, regs, banned, crop, ingredient,
                         target_disease, days_to_harvest, reg_id)
    return found[0] if found else Verdict.ok()
