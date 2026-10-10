#!/usr/bin/env python3
"""评测矩阵：所有实现 × 所有数据集，一次跑完并输出对照表。

用法：python3 tools/run_matrix.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"

DATASETS = [
    ("dev", ROOT / "eval" / "cases.json", "开发集（用例与实现同源，分数会偏高）"),
    ("holdout", ROOT / "eval" / "holdout.json", "留出集 v1（已用于发现问题，不再充当验收）"),
    ("holdout2", ROOT / "eval" / "holdout2.json", "留出集 v2（用于验收 W3 修复）"),
]
AGENTS = ["naive", "rule_based", "llm_assisted"]
METRICS = [
    ("pass_rate", "总通过率"),
    ("safety_block_recall", "合规拦截召回率"),
    ("refuse_recall", "拒答正确率"),
    ("over_refusal_rate", "过度拒答率"),
    ("diagnose_pass_rate", "诊断通过率"),
    ("dose_violations", "违规放行数"),
]


def run(agent: str, tag: str, cases: Path):
    run_id = f"{tag}-{agent}"
    subprocess.run([sys.executable, str(ROOT / "tools" / "run_eval.py"),
                    "--agent", agent, "--cases", str(cases), "--run-id", run_id],
                   check=True, capture_output=True, text=True)
    return json.loads((RESULTS / f"{run_id}.json").read_text(encoding="utf-8"))


def fmt(v):
    if v is None:
        return "—"
    return f"{v:g}"


def main() -> int:
    data = {}
    for tag, path, _label in DATASETS:
        for agent in AGENTS:
            data[(tag, agent)] = run(agent, tag, path)["summary"]

    for tag, _path, label in DATASETS:
        print(f"\n## {tag}  —  {label}\n")
        header = "| 指标 | " + " | ".join(AGENTS) + " |"
        print(header)
        print("| --- | " + " | ".join("---" for _ in AGENTS) + " |")
        for key, name in METRICS:
            row = " | ".join(fmt(data[(tag, a)].get(key)) for a in AGENTS)
            print(f"| {name} | {row} |")

    print("\n### 泛化差距（rule_based：开发集 → 留出集 v2）\n")
    dev = data[("dev", "rule_based")]
    h2 = data[("holdout2", "rule_based")]
    for key, name in METRICS:
        d, h = dev.get(key), h2.get(key)
        if isinstance(d, (int, float)) and isinstance(h, (int, float)):
            print(f"  {name}: {d:g} → {h:g}  （{h - d:+g}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
