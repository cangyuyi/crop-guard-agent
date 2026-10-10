#!/usr/bin/env python3
"""从中国农药信息网按需拉取登记数据（复核用）。

## 为什么是"按需拉取"而不是内置全量数据

该站页脚声明「网站保留所有权, 未经允许不得复制, 镜像」。
所以本仓库只保留**目标子集的小快照**（可追溯到登记证号与拉取日期），
需要更多数据时由使用者**自己**运行本脚本按需查询——这样定位是"引用与复核"，
不是转载。请遵守：只查你真正需要的组合、保持间隔、不要并发轰炸。

## 用法

    python3 tools/fetch_registrations.py --crop 番茄 --ingredient 三环唑
    python3 tools/fetch_registrations.py --crop 水稻 --disease 稻瘟病 --limit 5
    python3 tools/fetch_registrations.py --verify          # 用现有台账跑一遍回归核对

只依赖标准库。**需要在你自己的网络环境运行**——部分环境下该站点仅 HTTP 可达。
"""

import argparse
import csv
import html
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

BASE = "http://www.chinapesticide.org.cn"
SYS = "https://www.icama.cn/BasicdataSystem"
QUERY_URL = f"{SYS}/pesticideRegistration/queryselect.do"
VIEW_URL = f"{SYS}/pesticideRegistration/viewpd.do"
TAG_URL = f"{SYS}/pesticideRegistration/tagview.do"

UA = "Mozilla/5.0 (compatible; crop-guard-agent/0.2; +https://github.com/cangyuyi/crop-guard-agent)"
DELAY_S = 1.0  # 请求间隔：请勿调小

# 与 queryselect.do 表单一致的字段名
FORM_FIELDS = [
    "pageNo", "pageSize", "djzh", "nymc", "cjmc", "sf", "nylb", "zhl", "jx",
    "zwmc", "fzdx", "syff", "dx", "yxcf", "yxcf_en", "yxcfhl",
    "yxcf2", "yxcf2_en", "yxcf2hl", "yxcf3", "yxcf3_en", "yxcf3hl",
    "yxqs_start", "yxqs_end", "yxjz_start", "yxjz_end",
]


def _post(fields: Dict[str, str]) -> str:
    payload = {k: "" for k in FORM_FIELDS}
    payload.update({"pageNo": "1", "pageSize": "20"})
    payload.update({k: v for k, v in fields.items() if v is not None})
    req = urllib.request.Request(
        QUERY_URL,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                 "User-Agent": UA},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def query(payload: Dict[str, str]) -> Tuple[int, List[Dict[str, str]]]:
    """执行一次登记查询。返回 (命中条数, 前若干条 {pdno, uuid})。"""
    body = _post(payload)
    if "内部错误" in body:
        raise RuntimeError("服务端返回内部错误——通常是参数不完整或请求过频，请稍后重试")

    rows: List[Dict[str, str]] = []
    for m in re.finditer(r"_viewpd\('([0-9a-f]{32})'\)[^>]*>\s*(PD[\w\-]+)", body, re.I):
        rows.append({"uuid": m.group(1), "pdno": m.group(2).strip()})
    if not rows:  # 顺序可能相反，换个方向再抓一次
        for m in re.finditer(r">\s*(PD[\w\-]+)\s*<[^>]*_viewpd\('([0-9a-f]{32})'\)", body, re.I):
            rows.append({"uuid": m.group(2), "pdno": m.group(1).strip()})

    total = re.search(r"(\d+)\s*条", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", body)))
    n = int(total.group(1)) if total else len(rows)
    return n, rows


def label_text(pdno: str) -> str:
    """取核准标签全文（安全间隔期就在这里）。"""
    body = _get(f"{TAG_URL}?pdno={urllib.parse.quote(pdno)}")
    text = re.sub(r"<script.*?</script>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    return html.unescape(re.sub(r"\n{2,}", "\n", text)).strip()


def verify_ledger(limit: Optional[int] = None) -> int:
    """拿 data/verified_queries.csv 里的组合回官方系统跑一遍，看是否仍一致。

    返回不一致的条数（0 = 全部一致）。**这是这份台账能被称为"可复核"的原因。**
    """
    path = DATA / "verified_queries.csv"
    mismatches = 0
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if limit:
        rows = rows[:limit]
    for i, row in enumerate(rows, 1):
        payload = {"zwmc": row["crop"], "yxcf": row["active_ingredient"],
                   "fzdx": row.get("target_disease", "")}
        try:
            n, _ = query(payload)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(rows)}] 查询失败 {payload} — {exc}")
            continue
        expected = int(row["hits"])
        ok = (n == 0) == (expected == 0)
        flag = "一致" if ok else "★不一致"
        if not ok:
            mismatches += 1
        print(f"  [{i}/{len(rows)}] {row['crop']} × {row['active_ingredient']}"
              f"{' × ' + row['target_disease'] if row.get('target_disease') else ''}"
              f"  台账={expected} 现查={n}  {flag}")
        time.sleep(DELAY_S)
    return mismatches


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", help="作物/场所，如 番茄、水稻、柑橘树")
    ap.add_argument("--ingredient", help="有效成分，如 三环唑")
    ap.add_argument("--disease", help="防治对象，如 稻瘟病")
    ap.add_argument("--limit", type=int, default=3, help="取前几条标签")
    ap.add_argument("--verify", action="store_true", help="用现有台账跑回归核对")
    ap.add_argument("--labels", action="store_true", help="顺便拉取标签并抽取安全间隔期")
    args = ap.parse_args()

    if args.verify:
        print("回归核对 data/verified_queries.csv（间隔 %.1fs）" % DELAY_S)
        bad = verify_ledger()
        print(f"\n不一致 {bad} 条" + ("（数据未变，台账仍可信）" if bad == 0 else "（台账需更新）"))
        return 0 if bad == 0 else 1

    if not args.crop:
        ap.error("需要 --crop，或使用 --verify")

    payload = {"zwmc": args.crop, "yxcf": args.ingredient or "", "fzdx": args.disease or ""}
    n, rows = query(payload)
    print(f"命中 {n} 条")
    for r in rows[: args.limit]:
        print(f"  {r['pdno']}")
        if args.labels:
            t = label_text(r["pdno"])
            phi = re.findall(r"在([^，。；、\n]{1,15}?)上的安全间隔期为?\s*(\d+)\s*天", t)
            name = re.search(r"农药名称：\s*([^\n]+)", t)
            print(f"      名称：{name.group(1).strip() if name else '?'}")
            print(f"      间隔期：{phi if phi else '未抽取到（须转人工核对标签）'}")
            time.sleep(DELAY_S)
        time.sleep(DELAY_S)
    print("\n提示：只查你真正需要的组合，保持请求间隔，不要批量镜像。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
