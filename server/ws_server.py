"""WS 设备连接管理：长连接 + 心跳 + 断线重连 + 指令幂等队列。

SDD §3 / §9：
  - 首条消息带 token 鉴权，否则断开（§3 register 的 token 校验）
  - register → device_registry 登记设备
  - ping/pong 心跳，超时标记离线
  - 消息按 type 路由到 handler
  - dispatch_result/usage/status/agent_files 处理
  - 离线指令入队（OFFLINE_QUEUE），设备重连后补发（幂等）
  - sync_lock_rules/fetch_patch/install_gitbash 仅占位返回 not_implemented（归 M3）

服务端 → Sidecar 的 fetch_agent_files：
  {"type":"fetch_agent_files","device_id","agent_id","accessible_paths","request_id"}

对接 FastAPI 原生 WebSocket（uvicorn 的 WS 协议实现基于 websockets 库），
挂载在 /ws，端口 2334。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
from collections import deque
from typing import Callable

import db
from collect import CollectService
from device_registry import DeviceRegistry
from dispatch import DispatchService

logger = logging.getLogger("ws_server")

# 离线指令队列：(device_id, message)。设备重连后由 handle() 补发。
OFFLINE_QUEUE: deque = deque()

# 消息处理器注册表：type -> handler
_HANDLERS: dict[str, Callable] = {}


def register_handler(msg_type: str):
    """装饰器：注册某个 WS 消息类型的处理函数。"""
    def deco(fn):
        _HANDLERS[msg_type] = fn
        return fn
    return deco


def enqueue_offline(device_id: str, message: dict) -> None:
    """离线指令入队（供 dispatch.py 调用）。"""
    OFFLINE_QUEUE.append((device_id, message))


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class WSServer:
    def __init__(self, config: dict, db_path=None):
        self.config = config
        self.token = config.get("token", "")
        self.heartbeat_timeout = config.get("heartbeat_timeout", 60)
        self.db_path = db_path or db.db_path_from_config(config.get("db_path", "data/managed.db"))
        self.registry = DeviceRegistry(self.db_path)
        self.dispatch = DispatchService(self.db_path, self.registry)
        self.collect = CollectService(self.db_path)
        # 设备连接映射：device_id -> FastAPI WebSocket
        self._conns: dict[str, object] = {}

    # ---- FastAPI WebSocket 连接入口（挂载 /ws）----
    async def handle(self, websocket) -> None:
        """FastAPI /ws 端点处理逻辑。websocket 为 starlette WebSocket。"""
        await websocket.accept()
        device_id = None
        heartbeat_task = None
        try:
            # 首条消息必须为 register 且 token 正确，否则断开
            first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            if not self._authorize(first):
                logger.warning("WS 鉴权失败：%s", first.get("device_id"))
                await websocket.send_json({"type": "error", "error": "unauthorized"})
                await websocket.close(code=4001)
                return

            if first.get("type") != "register":
                await websocket.send_json({"type": "error", "error": "first_message_must_be_register"})
                await websocket.close(code=4002)
                return

            device_id = first["device_id"]
            self.registry.attach(device_id, websocket)
            self.registry.register(
                device_id=device_id,
                hostname=first.get("hostname", ""),
                os_=first.get("os", ""),
                cherry_version=first.get("cherry_version", ""),
                fork_version=first.get("fork_version"),
                group=first.get("group"),
                token=first.get("token", ""),
            )
            self._conns[device_id] = websocket
            await websocket.send_json({"type": "register_ack", "device_id": device_id})

            # 重连后补发离线指令（幂等）
            await self._flush_offline(device_id, websocket)

            # 心跳保活任务
            heartbeat_task = asyncio.create_task(self._heartbeat(device_id, websocket))

            # 循环处理消息
            while True:
                try:
                    msg = await websocket.receive_json()
                except Exception:  # 连接关闭/协议错误
                    break
                await self._route(websocket, device_id, msg)
        except asyncio.TimeoutError:
            logger.info("首条消息超时 device=%s", device_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("WS handler 异常: %s", e)
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
            if device_id:
                self.registry.detach(device_id)
                self.registry.set_offline(device_id)
                self._conns.pop(device_id, None)

    def _authorize(self, msg: dict) -> bool:
        """token 校验（timing-safe 风格）。"""
        if msg.get("type") != "register":
            return False
        supplied = msg.get("token", "")
        return self._timing_safe(supplied, self.token)

    @staticmethod
    def _timing_safe(a: str, b: str) -> bool:
        if not isinstance(a, str) or not isinstance(b, str):
            return False
        if len(a) != len(b):
            return False
        return sum(x != y for x, y in zip(a, b)) == 0

    async def _flush_offline(self, device_id: str, websocket) -> None:
        """重连后补发该设备的未确认离线指令（幂等）。"""
        to_send = [msg for (did, msg) in OFFLINE_QUEUE if did == device_id]
        for msg in to_send:
            await websocket.send_json(msg)
            for i, item in enumerate(OFFLINE_QUEUE):
                if item[0] == device_id and item[1].get("request_id") == msg.get("request_id"):
                    del OFFLINE_QUEUE[i]
                    break

    async def _heartbeat(self, device_id: str, websocket) -> None:
        """心跳保活：周期性发送 ping。"""
        interval = max(5, self.heartbeat_timeout // 3)
        try:
            while True:
                await asyncio.sleep(interval)
                self.registry.touch(device_id)
                await websocket.send_json({"type": "ping", "ts": _now()})
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            return

    async def _route(self, websocket, device_id: str, msg: dict) -> None:
        """按 type 分发到 handler。"""
        msg_type = msg.get("type")
        if msg_type == "ping":
            await websocket.send_json({"type": "pong", "ts": _now()})
            return
        if msg_type == "pong":
            self.registry.touch(device_id)
            return
        handler = _HANDLERS.get(msg_type)
        if handler is None:
            await websocket.send_json({"type": "error", "error": f"unknown_type:{msg_type}"})
            return
        try:
            await handler(self, websocket, device_id, msg)
        except Exception as e:  # noqa: BLE001
            logger.exception("handler %s 异常", msg_type)
            await websocket.send_json({"type": "error", "error": str(e), "request_id": msg.get("request_id")})


# ========================= 消息处理器 =========================

@register_handler("register")
async def _on_register(server, websocket, device_id, msg):
    server.registry.set_online(device_id)


@register_handler("dispatch_result")
async def _on_dispatch_result(server, websocket, device_id, msg):
    """SDD §3.6：派发回执。"""
    server.dispatch.confirm_result(
        msg.get("request_id", ""),
        bool(msg.get("success")),
        msg.get("error"),
    )
    await websocket.send_json({"type": "dispatch_result_ack", "request_id": msg.get("request_id")})


@register_handler("usage")
async def _on_usage(server, websocket, device_id, msg):
    """SDD §3.5：usage 上报写 usage_agg。"""
    n = server.collect.record_usage(
        device_id, msg.get("period", _now()), msg.get("records", []), msg.get("errors")
    )
    await websocket.send_json({"type": "usage_ack", "count": n, "request_id": msg.get("request_id")})


@register_handler("status")
async def _on_status(server, websocket, device_id, msg):
    """SDD §3.7：状态上报。"""
    server.registry.touch(device_id)
    await websocket.send_json({"type": "status_ack", "device_id": device_id})


@register_handler("agent_files")
async def _on_agent_files(server, websocket, device_id, msg):
    """工作目录采集上报：写 agent_files 表。"""
    n = server.collect.record_agent_files(
        device_id, msg.get("agent_id", ""), msg.get("files", [])
    )
    await websocket.send_json({"type": "agent_files_ack", "count": n,
                               "request_id": msg.get("request_id")})


# ---- M3 占位 handler（仅返回 not_implemented）----
@register_handler("sync_lock_rules")
async def _on_sync_lock_rules(server, websocket, device_id, msg):
    await websocket.send_json({"type": "not_implemented", "feature": "sync_lock_rules",
                               "request_id": msg.get("request_id")})


@register_handler("fetch_patch")
async def _on_fetch_patch(server, websocket, device_id, msg):
    await websocket.send_json({"type": "not_implemented", "feature": "fetch_patch",
                               "request_id": msg.get("request_id")})


@register_handler("install_gitbash")
async def _on_install_gitbash(server, websocket, device_id, msg):
    await websocket.send_json({"type": "not_implemented", "feature": "install_gitbash",
                               "request_id": msg.get("request_id")})


def create_ws_server(config: dict, db_path=None) -> WSServer:
    """工厂：创建 WSServer 实例（供 main.py 使用）。"""
    return WSServer(config, db_path)
