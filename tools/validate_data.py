#!/usr/bin/env python3
"""数据校验：检查夹具是否还有未替换的记录。

接入真实登记数据前，`registrations_still_fixture` 必须归零——
否则评测分数没有意义（见 data/README.md）。

用法：python3 tools/validate_data.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cropguard.datasource import audit_fixtures, load_default  # noqa: E402


def main() -> int:
    ds = load_default()
    stats = audit_fixtures(ds)

    print("数据校验")
    print(f"  病害知识表        : {stats['diseases']} 条")
    print(f"  登记记录          : {stats['registrations_total']} 条")
    print(f"  其中仍为夹具      : {stats['registrations_still_fixture']} 条")
    print(f"  禁限用名录        : {stats['banned_rows']} 条")
    print()

    if stats["registrations_still_fixture"]:
        print("⚠️  仍有夹具记录。规则引擎与评测管线可正常跑通，")
        print("    但评测分数只反映「引擎是否正确」，不代表农艺正确性。")
        print("    接入官方登记数据后必须用同一套 eval/cases.json 重跑基线。")
        return 0

    print("✓ 未发现夹具记录。请确认 source 字段已记录真实来源与日期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
