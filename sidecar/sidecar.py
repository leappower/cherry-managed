#!/usr/bin/env python3
"""
CherryStudio 企业受管版 · Sidecar 常驻进程 (S-1 整合)
=====================================================
M2 批次 B S-1：将 8 个模块串成常驻主进程。
  注册 / 指令路由 / 采集 / 对账 / 自愈。

用法:
  python3 sidecar.py probe --machine <host>   # 探测机器能力(旧CLI)
  python3 sidecar.py run                       # 常驻主进程
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

# 项目根目录 (sidecar/ 的上级)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from cherry_client import CherryClient, CherryError  # noqa: E402
from ws_client import WSClient  # noqa: E402
from fork_client import ForkClient  # noqa: E402
from managed_registry import ManagedRegistry  # noqa: E402
from dispatch import DispatchExecutor  # noqa: E402
from collect import Collector  # noqa: E402
from reconcile import ReconcileEngine  # noqa: E402
from selfheal import SelfHealer  # noqa: E402

logger = logging.getLogger("sidecar")


# ── 旧 CLI：机器清单加载 ─────────────────────────────
def _find_list_json() -> Path:
    candidates = [
        PROJECT_ROOT / "list.json",
        Path("/Volumes/Chee_2/Chee/OpenClaw_C/cherry-managed/list.json"),
        Path("/Volumes/Chee_2/OpenClaw/CherryStudio/list.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def load_machine(hostname: str) -> dict:
    list_path = _find_list_json()
    if not list_path.exists():
        raise SystemExit(f"list.json 未找到 ({list_path})")
    with open(list_path) as f:
        machines = json.load(f)["machines"]
    for m in machines:
        if m["hostname"] == hostname:
            return m
    raise SystemExit(f"未找到机器: {hostname}")


def get_client(hostname: str) -> CherryClient:
    return CherryClient.from_machine(load_machine(hostname))


def cmd_probe(args):
    c = get_client(args.machine)
    try:
        print(f"健康: {c.health()}")
    except CherryError as e:
        print(f"连接失败: {e}")
        return 1
    agents = c.list_agents()
    print(f"Agent 数: {len(agents)}")
    return 0


def cmd_agents(args):
    for a in get_client(args.machine).list_agents():
        print(f"  {a.get('name','?'):20s} {a.get('id','?')} model={a.get('model','?')}")
    return 0


def cmd_models(args):
    for m in get_client(args.machine).list_models():
        print(f"  {m.get('name','?'):30s} provider={m.get('provider_name','?')}")
    return 0


def cmd_deploy(args):
    c = get_client(args.machine)
    payload = {
        "type": "claude-code",
        "name": args.name,
        "description": args.description or args.name,
        "model": args.model,
        "instructions": args.instructions or f"你是 {args.name}。",
        "accessible_paths": [p.strip() for p in args.accessible_paths.split(",")] if args.accessible_paths else [],
        "configuration": {"permission_mode": "bypassPermissions", "max_turns": 100, "env_vars": {}},
    }
    existing = c.find_agent_by_name(args.name)
    if existing:
        print(f"更新: {existing['id']}")
        return c.patch_agent(existing["id"], payload)
    print("创建")
    return c.create_agent(payload)


# ── S-1 主进程 ────────────────────────────────────────
def _load_config() -> dict:
    cfg_path = Path(__file__).resolve().parent / "config" / "sidecar.json"
    with open(cfg_path) as f:
        return json.load(f)


class SidecarRunner:
    """常驻主进程：组装各模块 + 指令路由 + 定时采集/对账/自愈。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        srv = cfg["server"]
        fork_cfg = cfg["fork"]
        paths = cfg["paths"]
        self.device = cfg["device"]

        # 组装基础客户端
        self.cherry = CherryClient(host=fork_cfg.get("host", "127.0.0.1"),
                                   port=fork_cfg.get("port", 23333),
                                   api_key=fork_cfg.get("api_key", ""))
        self.fork = ForkClient(base_url=fork_cfg.get("base_url", "http://127.0.0.1:23333"),
                               api_key=fork_cfg.get("api_key", ""))
        self.registry = ManagedRegistry(paths["managed_registry_db"])

        # 执行/采集/对账/自愈
        self.dispatch = DispatchExecutor(cherry=self.cherry, registry=self.registry,
                                         deploy_dir=paths["agents_deploy"],
                                         skills_dir=paths["skills_dir"],
                                         log_path=paths.get("dispatch_log",
                                                            str(Path(paths["user_data"]) / "dispatch.log")))
        self.collector = Collector(fork=self.fork)
        self.reconciler = ReconcileEngine(registry=self.registry)
        self.healer = SelfHealer(registry=self.registry, fork=self.fork,
                                 backup_root=str(Path(paths["user_data"]) / "backups"))

        # WS 客户端
        hb = cfg.get("heartbeat", {})
        rc = cfg.get("reconnect", {})
        self.ws = WSClient(url=srv["url"],
                           on_message=self._on_message,
                           on_connected=self._on_connected,
                           on_disconnected=self._on_disconnected,
                           initial_delay=rc.get("initial_delay", 1.0),
                           max_delay=rc.get("max_delay", 60.0),
                           multiplier=rc.get("multiplier", 2.0),
                           heartbeat_interval=hb.get("interval", 20))

        self._stop = threading.Event()
        # 断线重连补发：把 pending 指令重新发给执行器
        self.healer.set_retry_cb(self._retry_dispatch)

    # ---- 发送 -------
    def _send(self, data: dict) -> None:
        self.ws.send(data)

    def _register(self) -> None:
        srv = self.cfg["server"]
        msg = {
            "type": "register",
            "device_id": self.device["device_id"],
            "hostname": self.device.get("hostname", ""),
            "os": self.device.get("os", ""),
            "cherry_version": self.cfg["cherry"].get("version", ""),
            "fork_version": self.cfg["cherry"].get("fork_version", ""),
            "group": self.device.get("group", ""),
            "token": srv.get("token", ""),
        }
        self._send(msg)

    # ---- WS 回调 ----
    def _on_connected(self) -> None:
        logger.info("已连接，发送 register")
        self._register()

    def _on_disconnected(self) -> None:
        logger.info("断线，触发自愈补发")
        try:
            self.healer.on_reconnect()
        except Exception as e:  # noqa: BLE001
            logger.exception("断线补发异常: %s", e)

    def _on_message(self, msg: dict) -> None:
        mtype = msg.get("type")
        # ack/pong 等回执类消息：静默忽略，不报错也不回发（防止 error 风暴）
        if mtype in ("register_ack", "usage_ack", "status_ack", "agent_files_ack",
                     "dispatch_result_ack", "pong", "not_implemented"):
            return
        handler = {
            "dispatch_agent": self._handle_dispatch_agent,
            "dispatch_provider": self._handle_dispatch_provider,
            "dispatch_skills": self._handle_dispatch_skills,
            "fetch_agent_files": self._handle_fetch_agent_files,
            "status": self._handle_status,
        }.get(mtype)
        if handler is None:
            # 未知指令（含服务端 error）：只打日志，不回发（防循环）
            logger.warning("忽略未知/错误指令 type=%s msg=%s",
                           mtype, json.dumps(msg, ensure_ascii=False)[:300])
            return
        try:
            handler(msg)
        except Exception as e:  # noqa: BLE001
            logger.exception("处理 %s 异常", mtype)
            self._send({"type": "dispatch_result", "request_id": msg.get("request_id"),
                        "success": False, "error": str(e)})

    # ---- 指令处理 ----
    def _handle_dispatch_agent(self, msg: dict) -> None:
        rid = msg.get("request_id")
        result = self.dispatch.handle_dispatch_agent(
            action=msg.get("action", "create"), agent=msg.get("agent", {}),
            package_url=msg.get("package_url"), request_id=rid)
        self.healer.track_pending(rid, msg)
        self.healer.on_dispatch_result(rid, result.get("success"), result.get("error"))
        self._send({"type": "dispatch_result", "request_id": rid,
                    "success": result.get("success"), "error": result.get("error"),
                    "result": result})

    def _handle_dispatch_provider(self, msg: dict) -> None:
        rid = msg.get("request_id")
        result = self.dispatch.handle_dispatch_provider(
            action=msg.get("action", "add"), provider=msg.get("provider", {}),
            request_id=rid)
        self.healer.track_pending(rid, msg)
        self.healer.on_dispatch_result(rid, result.get("success"), result.get("error"))
        self._send({"type": "dispatch_result", "request_id": rid,
                    "success": result.get("success"), "error": result.get("error"),
                    "result": result})

    def _handle_dispatch_skills(self, msg: dict) -> None:
        rid = msg.get("request_id")
        result = self.dispatch.handle_dispatch_skills(msg.get("skills", []), request_id=rid)
        self.healer.track_pending(rid, msg)
        self.healer.on_dispatch_result(rid, result.get("success"), None)
        self._send({"type": "dispatch_result", "request_id": rid,
                    "success": result.get("success"), "error": None, "result": result})

    def _handle_fetch_agent_files(self, msg: dict) -> None:
        rid = msg.get("request_id")
        result = self.collector.collect_agent_files(
            agent_id=msg.get("agent_id", ""),
            accessible_paths=msg.get("accessible_paths", []))
        self._send({"type": "agent_files", "request_id": rid,
                    "agent_id": msg.get("agent_id", ""), "files": result.get("files", []),
                    "skipped": result.get("skipped", []), "success": result.get("success")})

    def _handle_status(self, msg: dict) -> None:
        self._send({"type": "status", "device_id": self.device["device_id"],
                    "request_id": msg.get("request_id")})

    def _retry_dispatch(self, request_id: str, message: dict) -> dict:
        mtype = message.get("type")
        if mtype == "dispatch_agent":
            return self.dispatch.handle_dispatch_agent(
                action=message.get("action", "create"), agent=message.get("agent", {}),
                package_url=message.get("package_url"), request_id=request_id)
        if mtype == "dispatch_provider":
            return self.dispatch.handle_dispatch_provider(
                action=message.get("action", "add"),
                provider=message.get("provider", {}), request_id=request_id)
        if mtype == "dispatch_skills":
            return self.dispatch.handle_dispatch_skills(
                message.get("skills", []), request_id=request_id)
        return {"success": False, "error": f"未知重试类型 {mtype}"}

    # ---- 定时任务 ----
    def _run_periodic(self) -> None:
        col = self.cfg.get("collection", {})
        usage_iv = col.get("usage_interval", 60)
        status_iv = col.get("status_interval", 30)
        recon_iv = col.get("reconcile_interval", 120)
        last_usage = last_status = last_recon = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now - last_usage >= usage_iv:
                self._report_usage()
                last_usage = now
            if now - last_status >= status_iv:
                self._report_status()
                last_status = now
            if now - last_recon >= recon_iv:
                self._reconcile()
                last_recon = now
            time.sleep(5)

    def _report_usage(self) -> None:
        src = self.cfg.get("usage_source", {})
        result = self.collector.collect_usage(usage_source_url=src.get("url"))
        self._send({"type": "usage", "period": result.get("period"),
                    "records": result.get("records", []), "errors": result.get("errors")})

    def _report_status(self) -> None:
        self._send({"type": "status", "device_id": self.device["device_id"],
                    "managed_count": self.registry.count()})

    def _reconcile(self) -> None:
        try:
            expected = self.fork.list_agents()
            expected = [{"kind": "agent", "id": a.get("id", a.get("name", "")),
                         **a} for a in expected if isinstance(a, dict)]
        except Exception as e:  # noqa: BLE001
            logger.warning("对账基准拉取失败: %s", e)
            return
        diff = self.reconciler.reconcile(expected, self.registry)
        logger.info("对账: %s", json.dumps(diff.get("summary", {}), ensure_ascii=False))

    def run(self) -> None:
        logger.info("Sidecar 启动 device=%s", self.device["device_id"])
        self.ws.start()
        periodic = threading.Thread(target=self._run_periodic, name="periodic", daemon=True)
        periodic.start()
        try:
            while not self._stop.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("收到 Ctrl-C 退出")
        finally:
            self._stop.set()
            self.ws.stop()


def main():
    p = argparse.ArgumentParser(description="CherryStudio Sidecar")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_text in [("probe", "探测"), ("agents", "列出 Agent"),
                            ("models", "列出模型"), ("deploy", "派发")]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--machine", required=True)
        if name == "deploy":
            sp.add_argument("--name", required=True)
            sp.add_argument("--model", default="deepseek:deepseek-v4-flash")
            sp.add_argument("--instructions", default="")
            sp.add_argument("--description", default="")
            sp.add_argument("--accessible-paths", default="")
    sp_run = sub.add_parser("run", help="常驻主进程")
    sp_run.add_argument("--config", default="")

    args = p.parse_args()
    if args.cmd == "run":
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        cfg = _load_config()
        SidecarRunner(cfg).run()
        return 0
    fns = {"probe": cmd_probe, "agents": cmd_agents,
           "models": cmd_models, "deploy": cmd_deploy}
    return fns[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())




