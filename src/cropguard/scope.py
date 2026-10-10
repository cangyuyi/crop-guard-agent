"""作用域判定：这条输入该不该由本系统处理。

## 为什么从"黑名单"改成"允许列表"

第一版用一张关键词黑名单判断"是否与植保无关"（"天气""股票""餐馆"…）。
留出集把这种写法的天花板量了出来：**拒答正确率 87.5% → 0%**——
黑名单永远列不完，换一种说法就漏。

改成**允许列表**：输入里必须出现植保相关信号（作物名 / 病害名 / 症状词 / 用药词），
否则判为域外。这类"没有信号就拒绝"的写法比"命中黑名单才拒绝"更稳——
漏网的代价从"放过域外问题"变成"多问一句"，后者可接受得多。

## 这个模块也如实承认自己的边界

允许列表同样解决不了「话题对但意图不对」的情况：
「柑橘怎么储存不容易坏」里有作物名，会被判为域内，但它问的是采后储运。
这类需要理解意图而不是匹配词汇——**属于模型该做的部分**，见 `agents/llm_assisted.py`。
这里不假装能靠规则解决。
"""

import re
from typing import Iterable, List, Set

from .schema import Disease

# 用药与植保操作词汇
MEDICATION_TERMS = [
    "农药", "药剂", "打药", "用药", "施药", "喷雾", "喷施", "拌种", "灌根",
    "稀释", "倍液", "克/亩", "安全间隔期", "间隔期", "禁限用", "登记证",
    "杀虫", "杀菌", "除草", "防治", "植保", "病害", "虫害", "药害",
]

# 症状与观察词汇
SYMPTOM_TERMS = [
    "病斑", "斑块", "斑点", "霉层", "霉", "腐烂", "软腐", "枯", "萎蔫", "黄化",
    "褪绿", "卷叶", "畸形", "落叶", "落果", "裂果", "轮纹", "云纹", "梭形",
    "水浸状", "火山口", "菌脓", "锈色", "白粉", "灰霉", "黑斑", "叶背", "叶鞘",
    "叶缘", "叶脉", "根腐", "茎基", "穗颈",
]

# 农事与田间条件词汇
FIELD_TERMS = [
    "大棚", "棚内", "田间", "田里", "果园", "温室", "露地", "连作", "密植",
    "通风", "湿度", "阴天", "多雨", "台风", "浇水", "施肥", "氮肥", "苗期",
    "花期", "坐果", "采收", "收割", "采摘", "挂果", "抽穗", "分蘖",
]

# 指令覆盖 / 提示注入：**次要信号**，不是主判据。
# 单靠它不够（留出集证明过），但它是零成本的一道补充防线。
INJECTION_PATTERNS = [
    r"忽略(以上|之前|前面|所有|全部)?[^，。]{0,6}(指令|规则|限制|设定)",
    r"无视[^，。]{0,6}(指令|规则|限制)",
    r"(不要|不用)(再)?(管|遵守|理)[^，。]{0,6}(规则|规定|限制)",
    r"你现在是[^，。]{0,12}(没有限制|不受限制)",
    r"(系统提示|system\s*prompt|你的设定)",
    r"(完整|全部)的?(农药)?配比表",
]


def build_vocabulary(diseases: Iterable[Disease]) -> Set[str]:
    """从病害知识表里自动长出作物名与病害名，避免手写清单过期。"""
    vocab: Set[str] = set()
    for d in diseases:
        vocab.add(d.crop)
        vocab.add(d.name)
        # 「柑橘溃疡病」→「溃疡病」；「番茄叶霉病」→「叶霉病」
        short = d.name.replace(d.crop, "") if d.crop in d.name else d.name
        if len(short) >= 3:
            vocab.add(short)
    return {v for v in vocab if v}


def signals(text: str, diseases: Iterable[Disease]) -> List[str]:
    """返回命中的植保信号词，便于归因与调试。"""
    hits: List[str] = []
    hits += [w for w in build_vocabulary(diseases) if w in text]
    for group in (MEDICATION_TERMS, SYMPTOM_TERMS, FIELD_TERMS):
        hits += [w for w in group if w in text]
    # 去重保序
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def has_injection(text: str) -> bool:
    return any(re.search(p, text) for p in INJECTION_PATTERNS)


def in_scope(text: str, diseases: Iterable[Disease], crop: str = "") -> bool:
    """有植保信号、或调用方已指明作物，即视为域内。

    **传入 crop 会直接判域内**：前端既然已经知道这是哪个作物的问题，
    就不该因为描述太短（「叶子有斑」）而被当成域外问题拒绝——
    那种情况正确行为是追问，不是拒答。这个区分是开发集上的回归暴露出来的：
    早先只看文本，导致 4 条"信息不足但确属植保"的用例被误拒。
    """
    if crop:
        return True
    return bool(signals(text, diseases))
