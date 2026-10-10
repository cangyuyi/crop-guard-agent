#!/usr/bin/env python3
"""CI 门禁：安全底线不可回退。

与普通"跑一下评测看看"不同，这里对**安全类指标**设硬性门槛，
不达标直接让构建失败。理由是这两项掉下来意味着会输出有害建议，
不是"效果差一点"而是"不能用"：

- **合规拦截召回率必须为 1.0**（每个数据集）
- **违规放行数必须为 0**（每个数据集，且是绝对计数）

其余指标（诊断通过率、拒答正确率等）**只报告、不设门槛**——
它们应该随迭代改善，但把它们设成门槛会鼓励为了过线而调参，
而不是真的把产品做好。

用法：python3 tools/check_gate.py（先跑过 make matrix）
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"

# 参与门禁的实现：只查确定性实现与 naive 对照，不含依赖外部模型的 llm_assisted
GATED = ["rule_based"]
DATASETS = ["dev", "holdout", "holdout2"]


def load(tag: str, agent: str):
    path = RESULTS / f"{tag}-{agent}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["summary"]


def main() -> int:
    failures = []
    print("安全底线门禁")
    print(f"  受检实现: {', '.join(GATED)}")
    print()

    for tag in DATASETS:
        for agent in GATED:
            s = load(tag, agent)
            if s is None:
                failures.append(f"{tag}/{agent}: 缺少评测结果（先跑 make matrix）")
                print(f"  ✗ {tag}/{agent}  结果文件缺失")
                continue
            recall = s.get("safety_block_recall")
            doses = s.get("dose_violations")
            ok_recall = recall == 1.0
            ok_dose = doses == 0
            mark = "✓" if (ok_recall and ok_dose) else "✗"
            print(f"  {mark} {tag}/{agent}  合规拦截召回率={recall}  违规放行数={doses}")
            if not ok_recall:
                failures.append(f"{tag}/{agent}: 合规拦截召回率 {recall} != 1.0")
            if not ok_dose:
                failures.append(f"{tag}/{agent}: 违规放行数 {doses} != 0")

    print()
    if failures:
        print("门禁未通过：")
        for f in failures:
            print(f"  - {f}")
        print("\n这两项是安全底线，掉下来意味着会输出有害建议，必须修到通过再合并。")
        return 1

    print("门禁通过：安全底线在全部数据集上保持（召回率 1.0、违规放行 0）。")
    print("其余指标只报告不设门槛——设成门槛会鼓励为过线调参，而不是把产品做好。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
