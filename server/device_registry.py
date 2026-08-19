"""设备注册表：devices 表 + 分组 + 在线状态。

SDD §4.3 devices 表：
  device_id(PK), hostname, os, cherry_version, fork_version,
  online, last_seen, group, token

内存中维护在线连接映射（device_id -> 活跃 WebSocket），SQLite 持久化设备元数据。
"""
from __future__ import annotations

import datetime
import threading
from pathlib import Path

import db


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class DeviceRegistry:
    """设备注册表。

    - ``_connections``: device_id -> active WebSocket（内存，在线态）
    - SQLite ``devices`` 表: 设备元数据持久化
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._connections: dict[str, object] = {}

    # ---- 内存在线连接管理 ----
    def attach(self, device_id: str, ws) -> None:
        with self._lock:
            self._connections[device_id] = ws

    def detach(self, device_id: str) -> None:
        with self._lock:
            self._connections.pop(device_id, None)

    def get_connection(self, device_id: str):
        with self._lock:
            return self._connections.get(device_id)

    def online_ids(self) -> list[str]:
        with self._lock:
            return list(self._connections.keys())

    # ---- SQLite 持久化 ----
    def register(self, device_id: str, hostname: str, os_: str,
                 cherry_version: str, fork_version: str | None,
                 group: str | None, token: str,
                 managed_key: str | None = None) -> dict:
        """注册/更新设备元数据，写 devices 表。幂等（UPSERT）。

        managed_key 为 JJC-20260818-001 设备级受管密钥（可空）；空值不覆盖旧值
        （周期/重连上报空 managed_key 时保留已登记 key）。
        """
        now = _now()
        conn = db.get_conn(self.db_path)
        # managed_key：仅当本次上报非空才覆盖（CASE），避免周期/重连空值冲掉已登记 key。
        conn.execute(
            """
            INSERT INTO devices(device_id, hostname, os, cherry_version, fork_version,
                                online, last_seen, "group", token, managed_key)
            VALUES (?,?,?,?,?,1,?,?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET
                hostname=excluded.hostname,
                os=excluded.os,
                cherry_version=excluded.cherry_version,
                fork_version=excluded.fork_version,
                online=1,
                last_seen=excluded.last_seen,
                "group"=excluded."group",
                token=excluded.token,
                managed_key=CASE WHEN excluded.managed_key IS NOT NULL
                                 AND excluded.managed_key != ''
                                 THEN excluded.managed_key
                                 ELSE devices.managed_key END
            """,
            (device_id, hostname, os_, cherry_version, fork_version, now, group,
             token, managed_key),
        )
        conn.commit()
        rec = self.get(device_id)
        if rec is None:  # 刚插入必存在（防御性，满足类型标注）
            raise RuntimeError(f"register 后取回设备失败: {device_id}")
        return rec

    def set_managed_key(self, device_id: str, managed_key: str) -> None:
        """更新设备的 managed_key（JJC-20260818-001）。非空才写（幂等）。"""
        if not managed_key:
            return
        conn = db.get_conn(self.db_path)
        conn.execute(
            "UPDATE devices SET managed_key=? WHERE device_id=?",
            (managed_key, device_id),
        )
        conn.commit()

    def set_online(self, device_id: str) -> None:
        now = _now()
        conn = db.get_conn(self.db_path)
        conn.execute(
            "UPDATE devices SET online=1, last_seen=? WHERE device_id=?",
            (now, device_id),
        )
        conn.commit()

    def set_offline(self, device_id: str) -> None:
        now = _now()
        conn = db.get_conn(self.db_path)
        conn.execute(
            "UPDATE devices SET online=0, last_seen=? WHERE device_id=?",
            (now, device_id),
        )
        conn.commit()

    def touch(self, device_id: str) -> None:
        """心跳续活：更新 last_seen，保持 online。"""
        now = _now()
        conn = db.get_conn(self.db_path)
        conn.execute(
            "UPDATE devices SET online=1, last_seen=? WHERE device_id=?",
            (now, device_id),
        )
        conn.commit()

    def get(self, device_id: str) -> dict | None:
        conn = db.get_conn(self.db_path)
        row = conn.execute(
            "SELECT device_id, hostname, os, cherry_version, fork_version, online, "
            "last_seen, \"group\", token, managed_key FROM devices WHERE device_id=?",
            (device_id,),
        ).fetchone()
        return db._row_to_dict(row)

    def get_all(self) -> list[dict]:
        conn = db.get_conn(self.db_path)
        rows = conn.execute(
            "SELECT device_id, hostname, os, cherry_version, fork_version, online, "
            "last_seen, \"group\", token, managed_key FROM devices ORDER BY device_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def by_group(self, group: str) -> list[dict]:
        conn = db.get_conn(self.db_path)
        rows = conn.execute(
            "SELECT * FROM devices WHERE \"group\"=? ORDER BY device_id", (group,)
        ).fetchall()
        return [dict(r) for r in rows]

    def groups(self) -> list[str]:
        conn = db.get_conn(self.db_path)
        rows = conn.execute('SELECT DISTINCT "group" FROM devices WHERE "group" IS NOT NULL').fetchall()
        return [r["group"] for r in rows]

    def set_group(self, device_id: str, group: str) -> None:
        conn = db.get_conn(self.db_path)
        conn.execute('UPDATE devices SET "group"=? WHERE device_id=?', (group, device_id))
        conn.commit()

    def device_exists(self, device_id: str) -> bool:
        conn = db.get_conn(self.db_path)
        return conn.execute(
            "SELECT 1 FROM devices WHERE device_id=?", (device_id,)
        ).fetchone() is not None
