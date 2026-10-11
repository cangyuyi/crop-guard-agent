#!/usr/bin/env python3
"""评测矩阵：所有实现 × 所有数据集，一次跑完并输出对照表。

用法：
    python3 tools/run_matrix.py                     # 全部实现
    python3 tools/run_matrix.py --agents naive,rule_based   # 只跑确定性实现（CI 用）

**降级保护**：依赖模型的实现若未配置 CROP_GUARD_LLM_*，其结果会写入
`<tag>-<agent>-degraded.json`，绝不覆盖真实模型成绩；本表也会把它标为「降级」。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"
sys.path.insert(0, str(ROOT / "src"))

from cropguard.agents import build  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
from run_eval import plan_run_id  # noqa: E402

DATASETS = [
    ("dev", ROOT / "eval" / "cases.json", "开发集（用例与实现同源，分数会偏高）"),
    ("holdout", ROOT / "eval" / "holdout.json", "留出集 v1（已用于发现问题，不再充当验收）"),
    ("holdout2", ROOT / "eval" / "holdout2.json", "留出集 v2（已被修复过程看过，作为回归用）"),
    ("holdout3", ROOT / "eval" / "holdout3.json", "留出集 v3（验收自审修复后的当前状态）"),
]
ALL_AGENTS = ["naive", "rule_based", "llm_assisted"]
METRICS = [
    ("pass_rate", "总通过率"),
    ("safety_block_recall", "合规拦截召回率"),
    ("refuse_recall", "拒答正确率"),
    ("over_refusal_rate", "过度拒答率"),
    ("diagnose_pass_rate", "诊断通过率"),
    ("dose_violations", "违规放行数"),
]


def plan(agent_name: str, base_id: str):
    """复用 run_eval 的命名规则，保证两处算出的文件名一致。"""
    return plan_run_id(build(agent_name), base_id)


def run(agent: str, tag: str, cases: Path):
    run_id, degraded = plan(agent, f"{tag}-{agent}")
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "run_eval.py"),
                           "--agent", agent, "--cases", str(cases), "--run-id", run_id],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"✗ {tag}/{agent} 运行失败（退出码 {proc.returncode}）：\n{proc.stdout.strip()}")
    payload = json.loads((RESULTS / f"{run_id}.json").read_text(encoding="utf-8"))
    return payload["summary"], degraded, run_id


def fmt(v):
    return "—" if v is None else f"{v:g}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", default=",".join(ALL_AGENTS),
                    help="逗号分隔的实现列表（默认全部）")
    args = ap.parse_args()
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]

    data, notes = {}, {}
    for tag, path, _label in DATASETS:
        for agent in agents:
            s, degraded, run_id = run(agent, tag, path)
            data[(tag, agent)] = s
            if degraded:
                notes[(tag, agent)] = run_id

    for tag, _path, label in DATASETS:
        print(f"\n## {tag}  —  {label}\n")
        print("| 指标 | " + " | ".join(agents) + " |")
        print("| --- | " + " | ".join("---" for _ in agents) + " |")
        for key, name in METRICS:
            print(f"| {name} | " + " | ".join(fmt(data[(tag, a)].get(key)) for a in agents) + " |")

    if notes:
        print("\n⚠️ 以下结果为**降级运行**（依赖模型但未配置 CROP_GUARD_LLM_*），")
        print("   数字等同于纯规则路径，**不代表该实现的表现**：")
        for (tag, agent), run_id in sorted(notes.items()):
            print(f"   - {tag}/{agent} → eval/results/{run_id}.json")

    if "rule_based" in agents:
        print("\n### 泛化差距（rule_based：开发集 → 留出集 v2）\n")
        dev, h2 = data[("dev", "rule_based")], data[("holdout2", "rule_based")]
        for key, name in METRICS:
            d, h = dev.get(key), h2.get(key)
            if isinstance(d, (int, float)) and isinstance(h, (int, float)):
                print(f"  {name}: {d:g} → {h:g}  （{h - d:+g}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
