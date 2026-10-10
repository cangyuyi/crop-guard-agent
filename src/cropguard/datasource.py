"""数据源适配器。

设计意图：**不把项目成败押在单一数据源上。**
登记数据可以从本地 CSV、官方接口或人工录入任一来源加载，
所以拿不到批量官方数据时，规则引擎与评测管线依然能完整跑通。

数据来源见 data/README.md：当前为**中国农药信息网官方查询的小快照**，
每行带登记证号与拉取日期，可用 tools/fetch_registrations.py 重新核对。
"""

import csv
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from .schema import BannedEntry, Disease, Registration, VerifiedQuery

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class DataSource(Protocol):
    def diseases(self) -> List[Disease]: ...
    def registrations(self) -> List[Registration]: ...
    def banned(self) -> List[BannedEntry]: ...
    def r1_facts(self) -> List[VerifiedQuery]: ...


def _split(cell: str) -> List[str]:
    return [p.strip() for p in (cell or "").split(";") if p.strip()]


def _int_or_none(cell: str) -> Optional[int]:
    cell = (cell or "").strip()
    return int(cell) if cell.isdigit() else None


class CsvDataSource:
    """从 data/ 目录下的 CSV 读取。适合快照数据与人工维护的登记台账。"""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.dir = Path(data_dir)
        self._cache: Dict[str, object] = {}

    def _rows(self, name: str) -> List[dict]:
        if name not in self._cache:
            path = self.dir / name
            if not path.exists():
                self._cache[name] = []
            else:
                with path.open(encoding="utf-8", newline="") as fh:
                    self._cache[name] = list(csv.DictReader(fh))
        return self._cache[name]  # type: ignore[return-value]

    def diseases(self) -> List[Disease]:
        return [
            Disease(
                disease_id=r["disease_id"], crop=r["crop"], name=r["name"],
                key_symptoms=_split(r["key_symptoms"]),
                favored_conditions=_split(r["favored_conditions"]),
                has_chemical_control=r["has_chemical_control"].strip() == "是",
                notes=r.get("notes", ""),
            )
            for r in self._rows("diseases.csv")
        ]

    def registrations(self) -> List[Registration]:
        return [
            Registration(
                reg_id=r["reg_id"], product_name=r["product_name"],
                active_ingredient=r["active_ingredient"], content=r.get("content", ""),
                crop=r["crop"], target_disease=r["target_disease"],
                phi_days=_int_or_none(r.get("phi_days", "")),
                phi_status=r.get("phi_status", ""),
                source=r.get("source", ""), fetched_at=r.get("fetched_at", ""),
                dose=r.get("dose", ""), method=r.get("method", ""),
            )
            for r in self._rows("registrations.csv")
        ]

    def banned(self) -> List[BannedEntry]:
        return [
            BannedEntry(active_ingredient=r["active_ingredient"], status=r["status"],
                        note=r["note"], source=r["source"])
            for r in self._rows("banned.csv")
        ]

    def r1_facts(self) -> List[VerifiedQuery]:
        """官方验证过的查询台账（作物级 + 防治对象级）。R1/R2 的权威依据。"""
        return [
            VerifiedQuery(crop=r["crop"], active_ingredient=r["active_ingredient"],
                          target_disease=r.get("target_disease", ""),
                          registered=r["registered"].strip() == "是",
                          hits=int(r["hits"]))
            for r in self._rows("verified_queries.csv")
        ]


def load_default() -> CsvDataSource:
    return CsvDataSource()


def audit_data(ds: DataSource) -> Dict[str, object]:
    """数据体检：多少间隔期还是未知的、多少 R1 事实是「未登记」。

    未知间隔期不是 0——它意味着**必须转人工核对标签**，不能当安全放行。
    """
    regs = ds.registrations()
    facts = ds.r1_facts()
    unknown_phi = [r for r in regs if not r.phi_known]
    return {
        "registrations": len(regs),
        "phi_known": len(regs) - len(unknown_phi),
        "phi_unknown": len(unknown_phi),
        "phi_known_rate": round((len(regs) - len(unknown_phi)) / len(regs), 4) if regs else 0.0,
        "r1_facts": len(facts),
        "r1_not_registered": sum(1 for f in facts if not f.registered),
        "diseases": len(ds.diseases()),
        "banned": len(ds.banned()),
    }
