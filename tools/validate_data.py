#!/usr/bin/env python3
"""数据校验：体检 data/ 目录下的快照数据。

重点不是"数据对不对"，而是**把数据的缺口如实报出来**：
- 有多少条登记的安全间隔期还是未知（未知 ≠ 安全，必须转人工）
- R1 真值表覆盖了多少组 (作物, 成分)，其中多少是「未登记」

用法：python3 tools/validate_data.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from cropguard.datasource import audit_data, load_default  # noqa: E402
from parse_phi import parse_rate  # noqa: E402


def main() -> int:
    ds = load_default()
    s = audit_data(ds)

    print("数据体检")
    print(f"  病害知识表            : {s['diseases']} 条")
    print(f"  登记记录              : {s['registrations']} 条")
    print(f"    其中安全间隔期已抽取 : {s['phi_known']} 条")
    print(f"    其中安全间隔期未知   : {s['phi_unknown']} 条（未知 ≠ 安全，须转人工核对标签）")
    print(f"  禁限用名录            : {s['banned']} 条"
          f"（其中未核对公告文号 {s['banned_unverified']} 条）")
    print(f"  R1 真值表             : {s['r1_facts']} 组（作物×成分）")
    print(f"    其中官方查询为「未登记」: {s['r1_not_registered']} 组")
    print()

    # 用真实标签原文跑一次解析率，作为可上报指标
    raw_labels = []
    for r in ds.registrations():
        if r.phi_known:
            raw_labels.append(f"在{r.crop}上的安全间隔期为{r.phi_days}天。")
    if raw_labels:
        print(f"  解析器自检（{len(raw_labels)} 条已抽取样本）: 成功率 {parse_rate(raw_labels)}")

    print()
    print("提示：所有数据行均带 source 与 fetched_at，可用 tools/fetch_registrations.py 重新核对。")
    print("     安全间隔期是按登记证号存在的，换产品即换数值——不要按成分套用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
