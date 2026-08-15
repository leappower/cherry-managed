"""UDP 局域网发现服务端（批次H · E 扫描核心）。

服务端监听 UDP 2335 端口，收到带正确共享 token 的发现请求后，
单播回应自身 IP + 主服务端口 + 版本。供安装器/配置工具的 "扫描局域网" 使用。

协议：
  请求  {"type":"cherry-managed-discovery","token":"<共享token>"}
  回应  {"type":"cherry-managed-discovery-ack","server_ip":"...","port":2334,"version":"...","build":"batchH"}

安全：token 校验兜底 + 结果上屏用户确认（非自动连），对齐批次H方案 §3.1。
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket

log = logging.getLogger(__name__)

DISCOVERY_TYPE = "cherry-managed-discovery"
ACK_TYPE = "cherry-managed-discovery-ack"


class DiscoveryServer:
    """后台 UDP 发现监听器，随服务端同进程启动。"""

    def __init__(self, config: dict, host: str = "0.0.0.0", port: int | None = None):
        self.config = config
        self.token = config.get("token", "")
        self.host = host
        self.port = port or int(config.get("discovery", {}).get("port", 2335))
        self.manager_port = int(config.get("port", 2334))
        self.version = config.get("version", "4.0.0-rc.1")
        self._task: asyncio.Task | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._server_sock: socket.socket | None = None

    def _timing_safe(self, a: str, b: str) -> bool:
        import hmac

        return hmac.compare_digest(a.encode(), b.encode())

    async def _handle(self, data: bytes, addr) -> None:
        try:
            msg = json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") != DISCOVERY_TYPE:
            return
        supplied = msg.get("token", "")
        if not self._timing_safe(supplied, self.token):
            log.warning("discovery: token mismatch from %s", addr)
            return
        # 提取请求方看到的服务端地址：UDP 广播源不可靠，取本机局域网地址
        server_ip = self._primary_ip()
        ack = {
            "type": ACK_TYPE,
            "server_ip": server_ip,
            "port": self.manager_port,
            "version": self.version,
            "build": "batchH",
        }
        payload = json.dumps(ack).encode("utf-8")
        sock = self._server_sock
        if sock is not None:
            try:
                sock.sendto(payload, addr)
                log.info("discovery: ack to %s -> %s:%s", addr, server_ip, self.manager_port)
            except Exception as e:  # pragma: no cover
                log.warning("discovery: send fail %s", e)

    def _primary_ip(self) -> str:
        """取本机首个非回环 IPv4 地址，作为回应的 server_ip。"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # 连接外部不需要实际发包，仅用于让系统选出默认出口网卡 IP
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
            finally:
                s.close()
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
        # 兜底：枚举网卡
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127."):
                    return ip
        except Exception:
            pass
        return "127.0.0.1"

    async def start(self) -> None:
        """启动 UDP 监听（asyncio），幂等。"""
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()

        def _make_socket() -> socket.socket:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.setblocking(False)
            return sock

        self._server_sock = await loop.run_in_executor(None, _make_socket)
        sock = self._server_sock

        def _on_datagram(data: bytes, addr) -> None:
            # 在事件循环线程内创建子任务处理（发送可能阻塞，用 executor）
            asyncio.create_task(self._handle(data, addr))

        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(_on_datagram),
            sock=sock,
        )
        log.info("discovery UDP listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._server_sock is not None:
            self._server_sock.close()
            self._server_sock = None


class _UDPProtocol(asyncio.DatagramProtocol):
    """asyncio UDP 协议包装，把数据报回调到 on_datagram。"""

    def __init__(self, on_datagram):
        self._on_datagram = on_datagram

    def datagram_received(self, data: bytes, addr):
        self._on_datagram(data, addr)

    def error_received(self, exc):  # pragma: no cover
        log.warning("discovery UDP error: %s", exc)
