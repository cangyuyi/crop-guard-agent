"""Agent 接口与注册表。

评测器只依赖 `Agent.respond(case_input) -> Decision` 这一个方法，
所以任何实现（naive 基线 / 规则实现 / LLM 实现）都能用同一套评测跑。
"""

from typing import Dict, List, Protocol

from ..schema import CaseInput, Decision


class Agent(Protocol):
    name: str

    def respond(self, case_input: CaseInput) -> Decision: ...


_REGISTRY: Dict[str, str] = {
    "naive": "cropguard.agents.naive:NaiveAgent",
}


def available() -> List[str]:
    return sorted(_REGISTRY)


def build(name: str) -> Agent:
    """按名称构建 Agent。用延迟导入，避免评测器依赖具体实现。"""
    if name not in _REGISTRY:
        raise ValueError(f"未知 agent: {name}（可选：{', '.join(available())}）")
    module_path, _, attr = _REGISTRY[name].partition(":")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, attr)()
