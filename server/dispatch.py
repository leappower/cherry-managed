"""派发调度：dispatch_agent / dispatch_provider / dispatch_skills。

SDD §3.2/3.3/3.9 + §4.3 dispatch_log：
  request_id 幂等（同 request_id 不重复创建 dispatch_log）
  在线设备：直接向 WS 下发
  离线设备：指令入队（offline_queue），重连后由 ws_server 补发

dispatch_skills 在本阶段（M2）与 dispatch_agent 共用同一下发通道（type 区分），
SKILLS 完整仓库归 M3。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import db


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class DispatchService:
    def __init__(self, db_path: Path | str, registry):
        self.db_path = Path(db_path)
        self.registry = registry  # DeviceRegistry

    # ---- 幂等创建派发记录 ----
    def _create_log(self, request_id: str, device_id: str, type_: str,
                    action: str) -> bool:
        """创建派发记录。若 request_id 已存在返回 False（幂等，不重复创建）。"""
        conn = db.get_conn(self.db_path)
        exists = conn.execute(
            "SELECT 1 FROM dispatch_log WHERE request_id=?", (request_id,)
        ).fetchone()
        if exists:
            return False
        conn.execute(
            "INSERT INTO dispatch_log(request_id, device_id, type, action, status, created_at) "
            "VALUES (?,?,?,?, 'pending', ?)",
            (request_id, device_id, type_, action, _now()),
        )
        conn.commit()
        return True

    def _mark_status(self, request_id: str, status: str) -> None:
        conn = db.get_conn(self.db_path)
        conn.execute(
            "UPDATE dispatch_log SET status=? WHERE request_id=?", (status, request_id)
        )
        conn.commit()

    async def _dispatch(self, request_id: str, device_id: str, message: dict) -> bool:
        """向设备下发消息。设备在线则发；离线则入队待补发。返回是否在线下发。"""
        from ws_server import enqueue_offline  # 延迟导入避免环

        ws = self.registry.get_connection(device_id)
        if ws is not None:
            try:
                # starlette WebSocket 用 send_json（ASGI 消息需带 "type" 键，send(str) 会因
                # string indices 异常被误判为离线）。FakeWS（单测）兼容 send_json。
                if hasattr(ws, "send_json"):
                    await ws.send_json(message)
                else:
                    await ws.send(json.dumps(message, ensure_ascii=False))
                return True
            except Exception:
                # 发送失败视为离线，走入队
                self.registry.set_offline(device_id)
                self.registry.detach(device_id)
        enqueue_offline(device_id, message)
        return False

    # ---- 对外派发入口（async） ----
    async def dispatch_agent(self, device_id: str, action: str, agent: dict,
                             package_url: str | None, request_id: str) -> dict:
        """派发 Agent。SDD §3.2。返回 {created, online, request_id}。"""
        created = self._create_log(request_id, device_id, "dispatch_agent", action)
        msg = {
            "type": "dispatch_agent",
            "action": action,
            "agent": agent,
            "package_url": package_url,
            "request_id": request_id,
        }
        online = await self._dispatch(request_id, device_id, msg)
        db.audit(self.db_path, "server", "dispatch_agent", device_id, request_id)
        return {"created": created, "online": online, "request_id": request_id}

    async def dispatch_provider(self, device_id: str, action: str, provider: dict,
                                request_id: str) -> dict:
        """派发 Provider。SDD §3.3。"""
        created = self._create_log(request_id, device_id, "dispatch_provider", action)
        msg = {
            "type": "dispatch_provider",
            "action": action,
            "provider": provider,
            "request_id": request_id,
        }
        online = await self._dispatch(request_id, device_id, msg)
        db.audit(self.db_path, "server", "dispatch_provider", device_id, request_id)
        return {"created": created, "online": online, "request_id": request_id}

    async def dispatch_skills(self, device_id: str, skills: list,
                              request_id: str) -> dict:
        """派发 SKILLS。M2 用 dispatch_agent 同通道，type=dispatch_skills。"""
        created = self._create_log(request_id, device_id, "dispatch_skills", "sync")
        msg = {
            "type": "dispatch_skills",
            "skills": skills,
            "request_id": request_id,
        }
        online = await self._dispatch(request_id, device_id, msg)
        db.audit(self.db_path, "server", "dispatch_skills", device_id, request_id)
        return {"created": created, "online": online, "request_id": request_id}

    # ---- 回执处理 ----
    def confirm_result(self, request_id: str, success: bool,
                       error: str | None = None) -> None:
        """SDD §3.6 dispatch_result 回执：更新派发状态。"""
        status = "success" if success else "fail"
        self._mark_status(request_id, status)
        db.audit(self.db_path, "sidecar", "dispatch_result", request_id, request_id)

    async def fetch_agent_files(self, device_id: str, agent_id: str,
                                accessible_paths: list[str],
                                request_id: str) -> dict:
        """S-6b 工作目录采集：服务端 → Sidecar 发起 fetch_agent_files。

        幂等（同 request_id 不重复创建 dispatch_log）；
        在线设备直接下发，离线入队待重连补发。
        Sidecar 回 dispatch_result 带采集内容，由 confirm_result 更新状态。
        """
        created = self._create_log(request_id, device_id, "fetch_agent_files", "collect")
        msg = {
            "type": "fetch_agent_files",
            "device_id": device_id,
            "agent_id": agent_id,
            "accessible_paths": accessible_paths,
            "request_id": request_id,
        }
        online = await self._dispatch(request_id, device_id, msg)
        db.audit(self.db_path, "server", "fetch_agent_files", f"{device_id}:{agent_id}", request_id)
        return {"created": created, "online": online, "request_id": request_id}

    def get_log(self, request_id: str) -> dict | None:
        conn = db.get_conn(self.db_path)
        row = conn.execute(
            "SELECT request_id, device_id, type, action, status, created_at "
            "FROM dispatch_log WHERE request_id=?",
            (request_id,),
        ).fetchone()
        return db._row_to_dict(row)

    def pending_for_device(self, device_id: str) -> list[dict]:
        """查询某设备所有 pending 状态派发（用于重连补发）。"""
        conn = db.get_conn(self.db_path)
        rows = conn.execute(
            "SELECT request_id, device_id, type, action, status, created_at "
            "FROM dispatch_log WHERE device_id=? AND status='pending' ORDER BY created_at",
            (device_id,),
        ).fetchall()
        return [dict(r) for r in rows]
