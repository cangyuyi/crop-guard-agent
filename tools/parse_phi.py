#!/usr/bin/env python3
"""从农药核准标签全文里抽取安全间隔期（PHI）。

**为什么需要一个专门的解析器**：官方标签接口返回的是自然语言全文，
安全间隔期埋在「使用技术要求」段落里，没有结构化字段。实测抽取成功率
不高，所以这个模块的设计目标是**可测量、可复核**，而不是假装 100% 准确：

1. 每条抽取结果都带 `matched_text`（命中的原文片段），便于人工核对
2. 抽取不到时返回 None，调用方必须降级为人工确认——**绝不猜**
3. `parse_rate()` 输出成功率，作为可上报的指标

实测（2026-10-10，24 条真实标签）：标准句式命中约 25%，
其余多为无 PHI 表述或表述变体 —— 这个数字本身就是要写进 README 的事实。
"""

import re
from dataclasses import dataclass
from typing import List, Optional

# 「在<作物>上的安全间隔期为<N>天」——最常见句式
PHI_PER_CROP = re.compile(r"在([^，。；、\n]{1,15}?)上的安全间隔期为?\s*(\d+)\s*天")
# 「安全间隔期<N>天」——无作物限定
PHI_BARE = re.compile(r"安全间隔期\s*(?:为)?\s*(\d+)\s*天")
# 「<作物>安全间隔期<N>天」
PHI_CROP_FIRST = re.compile(r"([^，。；、\n]{1,15}?)\s*安全间隔期\s*(?:为)?\s*(\d+)\s*天")


@dataclass(frozen=True)
class PhiHit:
    days: int
    crop: Optional[str]   # None 表示标签未指明作物
    matched_text: str
    pattern: str


@dataclass
class PhiResult:
    per_crop: List[PhiHit]
    unscoped: List[PhiHit]

    @property
    def ok(self) -> bool:
        return bool(self.per_crop or self.unscoped)

    def for_crop(self, crop: str) -> Optional[PhiHit]:
        """取指定作物的间隔期。**不做跨作物兜底**——拿别的作物的数字是危险的。"""
        for hit in self.per_crop:
            if crop in hit.crop or hit.crop in crop:
                return hit
        return None

    @property
    def crops(self) -> List[str]:
        return [h.crop for h in self.per_crop if h.crop]


def parse_phi(label_text: str) -> PhiResult:
    """从标签全文抽取安全间隔期。抽取不到就返回空结果，由调用方降级为人工确认。"""
    text = (label_text or "").replace("\u3000", " ")

    per_crop: List[PhiHit] = []
    seen = set()
    for m in PHI_PER_CROP.finditer(text):
        crop, days = m.group(1).strip(), int(m.group(2))
        key = (crop, days)
        if key in seen:
            continue
        seen.add(key)
        per_crop.append(PhiHit(days=days, crop=crop, matched_text=m.group(0), pattern="phi_per_crop"))

    unscoped: List[PhiHit] = []
    for m in PHI_BARE.finditer(text):
        hit = PhiHit(days=int(m.group(1)), crop=None, matched_text=m.group(0), pattern="phi_bare")
        # 若该片段已被「按作物」句式覆盖，则不重复计入
        if any(hit.matched_text in h.matched_text for h in per_crop):
            continue
        unscoped.append(hit)

    return PhiResult(per_crop=per_crop, unscoped=unscoped)


def parse_rate(labels: List[str]) -> float:
    """成功率：能抽出至少一条间隔期的标签占比。

    这个数字必须如实上报。它低于 100% 是**正常且预期的**——
    因为不是所有标签都写间隔期，且表述方式多样。
    """
    if not labels:
        return 0.0
    hit = sum(1 for t in labels if parse_phi(t).ok)
    return round(hit / len(labels), 4)


if __name__ == "__main__":
    import sys

    sample = sys.stdin.read()
    res = parse_phi(sample)
    print(f"按作物命中 {len(res.per_crop)} 条，无作物限定 {len(res.unscoped)} 条")
    for h in res.per_crop:
        print(f"  {h.crop}: {h.days} 天   ← {h.matched_text}")
    for h in res.unscoped:
        print(f"  (未指明作物): {h.days} 天   ← {h.matched_text}")
