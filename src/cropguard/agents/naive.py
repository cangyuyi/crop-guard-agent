"""naive 基线：一个**故意做错**的实现。

它存在的唯一目的是证明评测集抓得住问题。它的做法是很多第一版 Demo 的做法：
关键词命中就直接下结论，其它一概不管。

预期它在这些地方翻车（这正是评测要证明的）：
  B 类 从不追问      → 追问正确数 0
  C 类 从不拒答      → 拒答正确率 0
  D/E/F 类 没有合规校验 → 合规拦截召回率 0
  G 类 顺着用户走    → 安全红线被突破，违规放行数 > 0

如果它拿到高分，说明评测集是坏的，不是它做得好。
"""

import re
from typing import List, Optional

from ..datasource import DataSource, load_default
from ..schema import CaseInput, Decision, Disease


class NaiveAgent:
    name = "naive"

    def __init__(self, ds: Optional[DataSource] = None) -> None:
        self.ds = ds or load_default()
        self._diseases: List[Disease] = self.ds.diseases()

    def respond(self, case_input: CaseInput) -> Decision:
        text = case_input.text or ""
        crop = case_input.crop
        pool = [d for d in self._diseases if (crop is None or d.crop == crop)] or self._diseases

        # 关键词命中就下结论（不追问、不校验）
        scored = sorted(pool, key=lambda d: self._overlap(text, d), reverse=True)
        top = [d.name for d in scored[:3] if self._overlap(text, d) > 0]
        if not top:
            top = [scored[0].name] if scored else []
        evidence = [s for s in (scored[0].key_symptoms[:1] if scored else [])]

        # 用户问什么就答什么，包括禁用农药的用法
        if case_input.requested_ingredient:
            return Decision(
                behavior="diagnose",
                message=f"{case_input.requested_ingredient}可以用，具体用量请按包装说明。",
                disease_candidates=top,
                evidence=evidence,
                recommended_ingredients=[case_input.requested_ingredient],
                dose_given=True,  # 安全红线：naive 基线的典型失败
            )

        return Decision(
            behavior="diagnose",
            message=f"很可能是{top[0] if top else '某种病害'}。",
            disease_candidates=top,
            evidence=evidence,
        )

    @staticmethod
    def _overlap(text: str, disease: Disease) -> int:
        """粗糙的症状关键词重合度。"""
        score = 0
        for phrase in disease.key_symptoms + disease.favored_conditions:
            for token in re.split(r"[;；]", phrase):
                token = token.strip()
                if len(token) >= 2 and token in text:
                    score += 2 if len(token) >= 4 else 1
        return score
