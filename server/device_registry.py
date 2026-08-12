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
                 group: str | None, token: str) -> dict:
        """注册/更新设备元数据，写 devices 表。幂等（UPSERT）。"""
        now = _now()
        conn = db.get_conn(self.db_path)
        conn.execute(
            """
            INSERT INTO devices(device_id, hostname, os, cherry_version, fork_version,
                                online, last_seen, "group", token)
            VALUES (?,?,?,?,?,1,?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET
                hostname=excluded.hostname,
                os=excluded.os,
                cherry_version=excluded.cherry_version,
                fork_version=excluded.fork_version,
                online=1,
                last_seen=excluded.last_seen,
                "group"=excluded."group",
                token=excluded.token
            """,
            (device_id, hostname, os_, cherry_version, fork_version, now, group, token),
        )
        conn.commit()
        return self.get(device_id)

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
            "last_seen, \"group\", token FROM devices WHERE device_id=?",
            (device_id,),
        ).fetchone()
        return db._row_to_dict(row)

    def get_all(self) -> list[dict]:
        conn = db.get_conn(self.db_path)
        rows = conn.execute(
            "SELECT device_id, hostname, os, cherry_version, fork_version, online, "
            "last_seen, \"group\", token FROM devices ORDER BY device_id"
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
