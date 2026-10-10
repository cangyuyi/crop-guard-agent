"""模型层：只负责“理解”，不负责“判定”。

## 架构上的一条硬边界

模型可以做：意图分类、症状解读、把用户口语映射到知识表里的病害候选。
模型**不可以**做：决定某种药剂是否可以用。

原因写在 `rules.py` 顶部：用药是否合规是可判定问题，把它交给概率模型去判断
既无必要也不可接受。所以 `LlmAssistedAgent` 的流程是：

    模型给出方案 → 规则层复核 → 规则可以否决模型

`tests/test_llm_guard.py` 里有一条测试专门验证这件事：
即使模型明确建议使用禁用农药，最终结论仍然是拦截。

## 可插拔 + 可降级

- `LlmClient` 是协议；`OpenAiCompatibleClient` 用标准库 urllib 打任意兼容端点
- 没有配置 API key 时，`LlmAssistedAgent` 自动降级为纯规则路径——
  评测因此**在没有 key 的环境里也能跑**，这是可复现性的前提
- 测试用 `ScriptedClient`（假客户端）验证接线，不依赖真实模型
"""

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Protocol

SYSTEM_PROMPT = """你是作物植保助手，只做「理解」不做「判定」。

给你一条农户描述，请输出 JSON：
{"scope": "in"|"out", "intent": "diagnose"|"compliance"|"other",
 "candidates": ["最多3个病害名"], "evidence": ["支撑候选的症状短语"],
 "asked_about": ["信息不足时需要追问的维度"], "ingredient": "提到的药剂名或空"}

字段约定（下游程序依赖，务必遵守）：
- `asked_about` 的每一项**必须且只能**从这五个词里选：部位 / 颜色 / 形状 / 叶背 / 条件。
  不要写"叶片的哪一部分"这类自由描述——下游无法判定。
- `candidates` 只在**信息足够**时给出；信息不足时**留空数组**，把所有要问的维度放进 asked_about。
  只要给了 candidates，就代表你认为已足以给出候选，此时 asked_about 应为空数组。

硬性约束：
- 不得给出任何用药剂量
- 不得判断某药剂是否合规（合规由规则引擎负责，你只负责指出用户提到了什么）
- 只输出 JSON，不要解释"""


class LlmClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAiCompatibleClient:
    """任意 OpenAI 兼容端点。零第三方依赖。

    只在配置了 CROP_GUARD_LLM_BASE / CROP_GUARD_LLM_KEY 时启用；
    未配置时上层会降级到纯规则路径。
    """

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> Optional["OpenAiCompatibleClient"]:
        base = os.environ.get("CROP_GUARD_LLM_BASE")
        key = os.environ.get("CROP_GUARD_LLM_KEY")
        model = os.environ.get("CROP_GUARD_LLM_MODEL")
        if not (base and key and model):
            return None
        return cls(base, key, model)

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"]


@dataclass
class ModelReading:
    """模型「理解」结果的规范化形式。**不含任何合规判断。**"""

    scope: str = "in"
    intent: str = "other"
    candidates: List[str] = None  # type: ignore[assignment]
    evidence: List[str] = None  # type: ignore[assignment]
    asked_about: List[str] = None  # type: ignore[assignment]
    ingredient: str = ""

    def __post_init__(self) -> None:
        self.candidates = self.candidates or []
        self.evidence = self.evidence or []
        self.asked_about = self.asked_about or []


def parse_reading(raw: str) -> ModelReading:
    """解析模型输出。**容错但严格**：解析不出来就返回空解读，
    由上层降级到规则路径，而不是拿半句话去猜。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return ModelReading(scope="in", intent="other")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return ModelReading(scope="in", intent="other")
    return ModelReading(
        scope=str(data.get("scope", "in")),
        intent=str(data.get("intent", "other")),
        candidates=[str(c) for c in (data.get("candidates") or [])][:3],
        evidence=[str(e) for e in (data.get("evidence") or [])][:3],
        asked_about=[str(a) for a in (data.get("asked_about") or [])][:6],
        ingredient=str(data.get("ingredient") or ""),
    )


def read(client: LlmClient, text: str) -> ModelReading:
    return parse_reading(client.complete(SYSTEM_PROMPT, text))
