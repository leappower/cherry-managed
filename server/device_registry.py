"""设备注册表：devices 表 + 分组 + 在线状态。

SDD §4.3 devices 表：
  device_id(PK), hostname, os, cherry_version, fork_version,
  online, last_seen, group, token

内存中维护在线连接映射（device_id -> 活跃 WebSocket），SQLite 持久化设备元数据。
"""
from __future__ import annotations

import datetime
import threading
import time as _time
from pathlib import Path

import db


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_iso(s: str) -> float | None:
    """解析 ISO8601 时间戳为 epoch 秒；失败返回 None。"""
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _fmt_duration(secs: int | None) -> str | None:
    """秒数格式化为人类可读时长（如 "2小时前"）。"""
    if secs is None:
        return None
    s = max(int(secs), 0)
    if s < 60:
        return f"{s}秒前"
    if s < 3600:
        return f"{s // 60}分钟前"
    if s < 86400:
        return f"{s // 3600}小时前"
    return f"{s // 86400}天前"


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
                 managed_key: str | None = None, ip: str | None = None) -> dict:
        """注册/更新设备元数据，写 devices 表。幂等（UPSERT）。

        managed_key 为 JJC-20260818-001 设备级受管密钥（可空）；空值不覆盖旧值
        （周期/重连上报空 managed_key 时保留已登记 key）。
        ip 为设备内网地址（由 WS 连接对端获取），仅非空时覆盖旧值。
        """
        now = _now()
        conn = db.get_conn(self.db_path)
        # managed_key：仅当本次上报非空才覆盖（CASE），避免周期/重连空值冲掉已登记 key。
        conn.execute(
            """
            INSERT INTO devices(device_id, hostname, os, cherry_version, fork_version,
                                online, last_seen, "group", token, managed_key, ip)
            VALUES (?,?,?,?,?,1,?,?,?,?,?)
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
                                 ELSE devices.managed_key END,
                ip=CASE WHEN excluded.ip IS NOT NULL AND excluded.ip != ''
                        THEN excluded.ip ELSE devices.ip END
            """,
            (device_id, hostname, os_, cherry_version, fork_version, now, group,
             token, managed_key, ip),
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
            "last_seen, \"group\", token, managed_key, ip, remark FROM devices WHERE device_id=?",
            (device_id,),
        ).fetchone()
        return db._row_to_dict(row)

    def get_all(self) -> list[dict]:
        conn = db.get_conn(self.db_path)
        rows = conn.execute(
            "SELECT device_id, hostname, os, cherry_version, fork_version, online, "
            "last_seen, \"group\", token, managed_key, ip, remark FROM devices ORDER BY device_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_devices_deliverable(self) -> list[dict]:
        """可交付视图：online 转为语义化状态字符串，供管理端 UI/API 直接展示。

        状态取值：
          - "在线"     WS 长连接活跃（registry.online=1）
          - "离线"     曾在线，当前 WS 断开（last_seen 距今超过 OFFLINE_THRESHOLD）
          - "未连接"   注册过但从未在线（last_seen 为空/极旧，无连接历史）
        附 online_raw（0/1）供程序逻辑（派发判断）继续使用。
        """
        import time as _time
        rows = self.get_all()
        now = _time.time()
        out = []
        for r in rows:
            d = dict(r)
            raw = 1 if d.get("online") else 0
            ls = d.get("last_seen")
            offline_secs = None
            if ls:
                try:
                    ls_ts = _parse_iso(ls)
                    offline_secs = int(now - ls_ts) if ls_ts else None
                except Exception:
                    offline_secs = None
            if raw == 1:
                status = "在线"
            elif ls and offline_secs is not None and offline_secs < 0:
                status = "在线"  # last_seen 在未来（时钟偏差），仍视为在线
            elif ls:
                status = "离线"
            else:
                status = "未连接"
            d["online"] = status
            d["online_raw"] = raw
            d["offline_since"] = _fmt_duration(offline_secs) if (status == "离线" and offline_secs is not None) else None
            out.append(d)
        return out

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

    def set_remark(self, device_id: str, remark: str) -> None:
        """设置设备备注（问题3：设备ID解析调优）。空串即清除备注。"""
        conn = db.get_conn(self.db_path)
        conn.execute("UPDATE devices SET remark=? WHERE device_id=?", (remark, device_id))
        conn.commit()


    def device_exists(self, device_id: str) -> bool:
        conn = db.get_conn(self.db_path)
        return conn.execute(
            "SELECT 1 FROM devices WHERE device_id=?", (device_id,)
        ).fetchone() is not None
