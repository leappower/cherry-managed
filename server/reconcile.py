"""对账入口。

SDD §8.5 / §9 数据一致性：Sidecar 周期性对账（缺的补/受管保护/非受管忽略）。
本模块为 M2 对账入口（reconcile），返回对账所需的服务端期望清单：
  - 期望在线设备
  - 各设备已派发（成功）的 Agent/Provider
供 Sidecar 比对本地实际清单，缺的补、受管保护、非受管忽略。

完整对账修复逻辑随 M3 深化。
"""
from __future__ import annotations

from pathlib import Path

import db


class ReconcileService:
    def __init__(self, db_path: Path | str, registry=None, dispatch=None):
        self.db_path = Path(db_path)
        self.registry = registry
        self.dispatch = dispatch

    def expected_devices(self) -> list[dict]:
        """期望清单：全部注册设备及在线状态。"""
        if self.registry is None:
            return []
        return self.registry.get_all()

    def expected_dispatches(self) -> list[dict]:
        """期望派发清单：所有成功派发的 dispatch_log。"""
        conn = db.get_conn(self.db_path)
        rows = conn.execute(
            "SELECT request_id, device_id, type, action, status, created_at "
            "FROM dispatch_log ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def reconcile_summary(self) -> dict:
        """汇总对账所需的期望状态（供 Sidecar 拉取比对）。"""
        return {
            "expected_devices": self.expected_devices(),
            "expected_dispatches": self.expected_dispatches(),
            "generated_at": _now(),
        }


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()
