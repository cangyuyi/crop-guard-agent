"""数据源适配器。

设计意图：**不把项目成败押在单一数据源上。**
登记数据可以从本地 CSV、官方接口或人工录入任一来源加载，
所以拿不到批量官方数据时，规则引擎与评测管线依然能完整跑通。

当前实现只有 CSV 一种来源（夹具数据）。接入官方接口时新增一个
实现同一接口的类即可，上层代码不需要改。
"""

import csv
from pathlib import Path
from typing import Dict, List, Protocol

from .schema import BannedEntry, Disease, Registration

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class DataSource(Protocol):
    def diseases(self) -> List[Disease]: ...
    def registrations(self) -> List[Registration]: ...
    def banned(self) -> List[BannedEntry]: ...


def _split(cell: str) -> List[str]:
    return [p.strip() for p in (cell or "").split(";") if p.strip()]


class CsvDataSource:
    """从 data/ 目录下的 CSV 读取。适合夹具数据与人工维护的登记台账。"""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.dir = Path(data_dir)
        self._cache: Dict[str, object] = {}

    def _rows(self, name: str) -> List[dict]:
        if name not in self._cache:
            with (self.dir / name).open(encoding="utf-8", newline="") as fh:
                self._cache[name] = list(csv.DictReader(fh))
        return self._cache[name]  # type: ignore[return-value]

    def diseases(self) -> List[Disease]:
        return [
            Disease(
                disease_id=r["disease_id"],
                crop=r["crop"],
                name=r["name"],
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
                reg_id=r["reg_id"],
                active_ingredient=r["active_ingredient"],
                crop=r["crop"],
                target_disease=r["target_disease"],
                dilution=r["dilution"],
                phi_days=int(r["phi_days"]),
                source=r["source"],
            )
            for r in self._rows("registrations.csv")
        ]

    def banned(self) -> List[BannedEntry]:
        return [
            BannedEntry(
                active_ingredient=r["active_ingredient"],
                status=r["status"],
                note=r["note"],
                source=r["source"],
            )
            for r in self._rows("banned.csv")
        ]


def load_default() -> CsvDataSource:
    return CsvDataSource()


def audit_fixtures(ds: DataSource) -> Dict[str, int]:
    """统计还有多少条夹具记录未替换。

    接入真实数据前必须让 fixture 计数归零——否则评测分数没有意义。
    """
    regs = ds.registrations()
    return {
        "registrations_total": len(regs),
        "registrations_still_fixture": sum(1 for r in regs if r.is_fixture),
        "banned_rows": len(ds.banned()),
        "diseases": len(ds.diseases()),
    }
