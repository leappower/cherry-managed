"""
WS 客户端 — Sidecar 与服务端 (2334) 的长连接管理
==================================================
SDD §3 + §5：
  - 基于 websocket-client（Sidecar 侧技术选型，SDD §5）
  - 指数退避重连（SDD §3：断线指数退避重连）
  - 心跳保活（服务端 ping → 回 pong；主动周期 ping 探测连通）
  - register 首条消息 + token 鉴权
  - 断线重连后重新 register

消息类型（Sidecar → 服务端）：
  register / dispatch_result / usage / status / agent_files / pong

服务端 → Sidecar：
  register_ack / ping / dispatch_agent / dispatch_provider / dispatch_skills
  fetch_agent_files / dispatch_result_ack / usage_ack / status_ack / agent_files_ack
  not_implemented / error

本模块只负责传输层（连接 + 收发 + 心跳 + 重连），
业务派发/采集由 dispatch.py / collect.py / reconcile.py 处理，
sidecar.py 主循环负责调度。
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Callable, Optional

import websocket  # websocket-client

logger = logging.getLogger("sidecar.ws")


class WSClient:
    """websocket-client 封装：连接、收发、心跳、指数退避重连。

    on_message(msg: dict) 回调由 sidecar.py 注入处理业务指令。
    """

    def __init__(
        self,
        url: str,
        on_message: Optional[Callable[[dict], None]] = None,
        on_connected: Optional[Callable[[], None]] = None,
        on_disconnected: Optional[Callable[[], None]] = None,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0,
        heartbeat_interval: float = 20.0,
    ):
        self.url = url
        self.on_message = on_message
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.heartbeat_interval = heartbeat_interval

        self.ws: Optional[websocket.WebSocket] = None
        self._stop = threading.Event()
        self._connected = False
        self._reconnect_delay = initial_delay
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

    # ---- 生命周期 ----
    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        """后台线程启动连接循环（阻塞直到 stop()）。"""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ws-client", daemon=True)
        self._thread.start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="ws-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    def stop(self) -> None:
        """停止连接并关闭 socket。"""
        self._stop.set()
        ws = self.ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._connected = False

    def _run(self) -> None:
        """主连接循环：连接 → 收消息 → 断线退避重连。"""
        while not self._stop.is_set():
            try:
                self._connect_once()
            except Exception as e:  # noqa: BLE001
                logger.warning("连接失败: %s，%s 秒后重连", e, self._reconnect_delay)
                self._set_disconnected()
                self._sleep_backoff()
        self._set_disconnected()

    def _connect_once(self) -> None:
        """建立一次连接并进入收发循环。异常抛出由 _run 处理退避。"""
        if self._stop.is_set():
            return
        ws = websocket.create_connection(self.url, timeout=30)
        self.ws = ws
        self._connected = True
        # 首条消息 register 由 sidecar.py 通过 on_connected 发送
        if self.on_connected:
            self.on_connected()
        self._reconnect_delay = self.initial_delay  # 连接成功重置退避
        logger.info("WS 已连接: %s", self.url)
        try:
            while not self._stop.is_set():
                raw = ws.recv()
                if raw is None or raw == "":
                    break
                self._dispatch(raw)
        finally:
            self._set_disconnected()
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _dispatch(self, raw: str) -> None:
        """解析并派发服务端消息。"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("非 JSON 消息: %r", raw[:200])
            return
        if msg.get("type") == "ping":
            # 服务端心跳 ping → 回 pong（服务端 handle 的 _route 处理）
            self.send({"type": "pong", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            return
        if self.on_message:
            try:
                self.on_message(msg)
            except Exception:  # noqa: BLE001
                logger.exception("on_message 回调异常")

    def _set_disconnected(self) -> None:
        if self._connected:
            self._connected = False
            if self.on_disconnected:
                try:
                    self.on_disconnected()
                except Exception:  # noqa: BLE001
                    logger.exception("on_disconnected 回调异常")

    def _sleep_backoff(self) -> None:
        """指数退避睡眠（可被 stop 中断）。"""
        delay = self._reconnect_delay
        self._reconnect_delay = min(self._reconnect_delay * self.multiplier, self.max_delay)
        # 分片睡眠以响应 stop
        slept = 0.0
        while slept < delay and not self._stop.is_set():
            time.sleep(min(0.5, delay - slept))
            slept += 0.5

    def _heartbeat_loop(self) -> None:
        """周期发送主动心跳 ping，探测对端是否存活。"""
        while not self._stop.is_set():
            time.sleep(self.heartbeat_interval)
            if self._connected:
                self.send({"type": "ping", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    # ---- 发送 ----
    def send(self, data: dict) -> bool:
        """发送 JSON 消息。返回是否成功。"""
        if not self._connected or self.ws is None:
            return False
        try:
            with self._lock:
                self.ws.send(json.dumps(data, ensure_ascii=False))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("发送失败: %s", e)
            self._connected = False
            return False

    def send_json_raw(self, obj) -> bool:
        return self.send(obj)
