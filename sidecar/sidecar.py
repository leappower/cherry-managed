#!/usr/bin/env python3
"""
CherryStudio 企业受管版 · Sidecar 常驻进程 (S-1 整合)
=====================================================
M2 批次 B S-1：将 8 个模块串成常驻主进程。
  注册 / 指令路由 / 采集 / 对账 / 自愈。

用法:
  python3 sidecar.py probe --machine <host>   # 探测机器能力(旧CLI)
  python3 sidecar.py run                       # 常驻主进程
  python3 sidecar.py first-run               # 首启：生成设备标识 + 落盘用户级配置 + 注册服务
  python3 sidecar.py install-service         # 注册/更新 NSSM 服务 CherrySidecar (Windows)
  python3 sidecar.py uninstall-service       # 停止并移除服务 (Windows)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# 批次 G (E-4)：用户级配置目录。
#   Windows: %APPDATA%\CherryManaged\
#   Linux/macOS: ~/.config/CherryManaged/  (降级用 machine-id / hostname)
USER_CONFIG_DIR_NAME = "CherryManaged"
USER_CONFIG_FILE = "config.json"
USER_DEVICE_FILE = "device.json"
SERVICE_NAME = "CherrySidecar"
# 内嵌模板在 PyInstaller onefile 下位于 _MEIPASS/config/sidecar.json
EMBEDDED_CONFIG_REL = "config/sidecar.json"

# 项目根目录 (sidecar/ 的上级)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

# 轻量：旧 CLI(probe/agents/models/deploy) 仅需 CherryClient。
# 其余运行时模块(ws_client/fork/dispatch/...)依赖外部包(websocket 等)，
# 在 SidecarRunner 内延后导入，使 --first-run/--install-service/--uninstall-service
# 即便无 websocket 环境也能独立运行（AC-E4 可测性）。
from cherry_client import CherryClient, CherryError  # noqa: E402

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
def _user_config_dir() -> Path:
    """用户级配置目录：Windows 用 %APPDATA%\\CherryManaged，其余用 ~/.config/CherryManaged。

    用 %APPDATA%（Roaming，普通用户可写）而非 %PROGRAMDATA%（全局受保护）：
    set-server / 首启配置在运行时由普通权限进程调用，写 ProgramData 会 PermissionError
    （实测「写入服务端地址失败，请检查权限后重试」）。改 APPDATA 根治权限，
    且卸载时由 NSIS 显式删除 %APPDATA%\\CherryManaged 实现「重装必重选服务端」。
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA", str(Path(os.environ.get("USERPROFILE", "C:")) / "AppData" / "Roaming"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / USER_CONFIG_DIR_NAME


def _embedded_config() -> Path:
    """内嵌配置模板路径：onefile 下 __file__→_MEIPASS 只读。"""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / EMBEDDED_CONFIG_REL


def _load_config(explicit: str | None = None) -> dict:
    """读取配置，优先级：显式 --config 参数 > 用户级 config.json > 内嵌模板。

    不存在用户级配置时，用内嵌模板生成并落盘（AC-E4-4 非 _MEIPASS）。
    """
    if explicit:
        with open(explicit, encoding="utf-8") as f:
            return json.load(f)
    user_cfg = _user_config_dir() / USER_CONFIG_FILE
    embedded = _embedded_config()
    if user_cfg.exists():
        with open(user_cfg, encoding="utf-8") as f:
            return json.load(f)
    # 落盘用户级（用内嵌作模板）
    if embedded.exists():
        _user_config_dir().mkdir(parents=True, exist_ok=True)
        with open(embedded, encoding="utf-8") as src, open(user_cfg, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        with open(user_cfg, encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError("无显式配置、用户级配置或内嵌配置模板")


def _device_fingerprint() -> str:
    """机器指纹：Windows MachineGuid+hostname hash；Linux 降级 machine-id/hostname hash。"""
    host = socket.gethostname()
    h = hashlib.sha256()
    if os.name == "nt":
        guid = ""
        try:
            import winreg  # noqa: PLC0415
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Microsoft\\Cryptography") as k:
                guid, _ = winreg.QueryValueEx(k, "MachineGuid")
        except OSError:
            guid = ""
        h.update((guid or host).encode("utf-8", "replace"))
        h.update(host.encode("utf-8", "replace"))
    else:
        mid = ""
        machine_id = Path("/etc/machine-id")
        if machine_id.exists():
            try:
                mid = machine_id.read_text(encoding="utf-8").strip()
            except OSError:
                mid = ""
        h.update((mid or host).encode("utf-8", "replace"))
        h.update(host.encode("utf-8", "replace"))
    return "managed-" + h.hexdigest()[:16]


def _load_or_create_device(cfg: dict) -> dict:
    """读取/生成 device_id：优先用户级 device.json，否则用机器指纹生成并落盘。"""
    dev_dir = _user_config_dir()
    dev_file = dev_dir / USER_DEVICE_FILE
    if dev_file.exists():
        with open(dev_file, encoding="utf-8") as f:
            d = json.load(f)
            if d.get("device_id"):
                return d
    d = {"device_id": _device_fingerprint(),
         "hostname": socket.gethostname(),
         "os": "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")}
    dev_dir.mkdir(parents=True, exist_ok=True)
    with open(dev_file, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


class SidecarRunner:
    """常驻主进程：组装各模块 + 指令路由 + 定时采集/对账/自愈。"""

    def __init__(self, cfg: dict):
        # 延后导入运行时依赖（仅在 run 主进程需要），使子命令独立可测
        from ws_client import WSClient  # noqa: PLC0415
        from fork_client import ForkClient  # noqa: PLC0415
        from managed_registry import ManagedRegistry  # noqa: PLC0415
        from dispatch import DispatchExecutor  # noqa: PLC0415
        from collect import Collector  # noqa: PLC0415
        from reconcile import ReconcileEngine  # noqa: PLC0415
        from selfheal import SelfHealer  # noqa: PLC0415

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
        # JJC-20260818-001：从 Fork loopback 路由拉取的 managed_key（SC-001 让
        # /v1/admin/usage、/v1/admin/agents 不再 401）。空串表示尚未拉取到
        # （如 Cherry 未启动），由周期任务重试，不崩溃。
        self._managed_key = ""
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

    # ---- JJC-20260818-001：managed_key 拉取 ----
    def _fetch_managed_key(self) -> str:
        """从 Fork loopback 只读路由拉取 managed_key。

        GET http://127.0.0.1:23333/v1/admin/managed-key，带 X-Device-Id 自证
        设备身份（服务端回环校验 + 405 非回环拒绝）。Cherry 未启动 / 路由不可达
        时返回空串并记 warning，由调用方在下一个 reconcile/heartbeat 周期重试，
        不崩溃。
        """
        import urllib.request
        import urllib.error

        fork = self.cfg.get("fork", {})
        base = fork.get("base_url", "http://127.0.0.1:23333").rstrip("/")
        url = base + "/v1/admin/managed-key"
        req = urllib.request.Request(
            url, headers={"X-Device-Id": self.device["device_id"]}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    logger.warning("拉取 managed_key 非 200: %s", resp.status)
                    return ""
                data = json.loads(resp.read().decode("utf-8"))
                key = (data or {}).get("managed_key", "")
                return key if isinstance(key, str) else ""
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                json.JSONDecodeError) as e:
            logger.warning("拉取 managed_key 失败(下次周期重试): %s", e)
            return ""

    def _ensure_managed_key(self) -> str:
        """确保已持有 managed_key：拉取缓存(幂等)，空则拉一次并同步到 ForkClient。

        拉到的 key 同时填入 ``fork_cfg["api_key"]`` 供 ForkClient 作 Bearer，
        使 /v1/admin/usage(collect)、/v1/admin/agents(reconcile) 不再 401(SC-001)。
        返回当前 managed_key（可能为空串）。
        """
        if self._managed_key:
            return self._managed_key
        key = self._fetch_managed_key()
        if key:
            self._managed_key = key
            # 同步到 ForkClient 的 Bearer(默认空则首次写入；即便有 static api_key
            # 也覆盖为受管 key，确保管理路由鉴权一致)。
            self.fork.api_key = key
            fork = self.cfg.get("fork", {})
            fork["api_key"] = key
        return self._managed_key

    # ---- 发送 -------
    def _send(self, data: dict) -> None:
        self.ws.send(data)

    def _register(self) -> None:
        srv = self.cfg["server"]
        # 注册时一并拉取 managed_key(幂等缓存；未拉到则为空，由周期任务重试)。
        managed_key = self._ensure_managed_key()
        msg = {
            "type": "register",
            "device_id": self.device["device_id"],
            "hostname": self.device.get("hostname", ""),
            "os": self.device.get("os", ""),
            "cherry_version": self.cfg["cherry"].get("version", ""),
            "fork_version": self.cfg["cherry"].get("fork_version", ""),
            "group": self.device.get("group", ""),
            "token": srv.get("token", ""),
            "managed_key": managed_key,
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
                # JJC-20260818-001：managed_key 未拉到(如 Cherry 后启动)时在此
                # reconcile 周期重试拉取，成功后同步给 ForkClient 供对账/采集鉴权。
                self._ensure_managed_key()
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


def _run(cmd: list[str], *, check: bool = False, text: bool = True) -> subprocess.CompletedProcess:
    """封装 subprocess.run：text=True 时统一 errors='replace'。

    Windows 上 nssm/sc 的输出可能含非 gbk 可解码字节（中文错误信息、UTF-8 等），
    默认 gbk 解码会抛 UnicodeDecodeError 并炸 _readerthread（实测 first-run 崩溃）。
    errors='replace' 保证任何字节都能安全解码，不中断主流程。
    """
    return subprocess.run(cmd, capture_output=True, text=text, check=check, errors="replace")


def _windows_service_exists() -> bool:
    try:
        out = _run(["sc", "query", SERVICE_NAME], check=False)
        return "RUNNING" in out.stdout or "STOPPED" in out.stdout or "SERVICE_NAME" in out.stdout
    except OSError:
        return False


def _install_service_windows() -> bool:
    """Windows: 用 NSSM 注册/更新服务并启动；已存在则 stop/remove 后重建指向新 exe。"""
    exe = Path(sys.executable)
    nssm = _find_nssm()
    if nssm is None:
        raise RuntimeError("NSSM 未内置（extraResources 应含 nssm.exe），中止注册")
    # 幂等更新：若已存在先停掉移除重建（对齐方案 §1.2 升级场景）
    if _windows_service_exists():
        _run([str(nssm), "stop", SERVICE_NAME], check=False)
        _run([str(nssm), "remove", SERVICE_NAME, "confirm"], check=False)
    _run([str(nssm), "install", SERVICE_NAME, str(exe), "run"], check=True)
    _run([str(nssm), "set", SERVICE_NAME, "Start", "SERVICE_AUTO_START"], check=True)
    # ⚠️ nssm 的 AppExit 需要「退出码+动作」两个子值，语法：
    #   nssm set <service> AppExit <exit-code> <action>
    #   单给动作 AppExit Restart 会返回 exit 1（缺退出码参数）→ 服务注册在最后阶段失败。
    #   Default = 对所有未显式列出的退出码生效；Restart = 自动重启（崩溃自愈）。
    _run([str(nssm), "set", SERVICE_NAME, "AppExit", "Default", "Restart"], check=True)
    _run([str(nssm), "set", SERVICE_NAME, "AppRestartDelay", "5000"], check=True)
    log_dir = _user_config_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _run([str(nssm), "set", SERVICE_NAME, "AppStdout", str(log_dir / "sidecar.log")], check=True)
    _run([str(nssm), "set", SERVICE_NAME, "AppStderr", str(log_dir / "sidecar.err.log")], check=True)
    _run([str(nssm), "start", SERVICE_NAME], check=True)
    return True


def _find_nssm() -> Path | None:
    """定位 nssm.exe：优先 _MEIPASS 同级/extraResources 内置，其次 PATH。

    安装器（NSIS）把 nssm.exe 放到 $INSTDIR\\resources\\sidecar\\nssm.exe；
    PyInstaller onefile 下 sys.executable 即该目录下的 sidecar.exe，
    故 sys.executable.parent / "nssm.exe" 天然覆盖此位置。此处再补一条
    resources/sidecar 相对路径兜底，保证与 NSIS 卸载脚本的定位一致。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = []
    if meipass:
        candidates.append(Path(meipass) / "nssm.exe")
        candidates.append(Path(meipass) / "bin" / "nssm.exe")
    candidates.append(Path(sys.executable).parent / "nssm.exe")
    candidates.append(Path(sys.executable).parent / "resources" / "sidecar" / "nssm.exe")
    for c in candidates:
        if c.exists():
            return c
    which = _run(["where", "nssm"], check=False)
    if which.returncode == 0 and which.stdout.strip():
        return Path(which.stdout.strip().splitlines()[0])
    return None


def _uninstall_service_windows() -> bool:
    """Windows: 停止并移除 NSSM 服务（卸载先停服务再删文件，防占用 exe）。"""
    nssm = _find_nssm()
    if _windows_service_exists():
        if nssm is not None:
            _run([str(nssm), "stop", SERVICE_NAME], check=False)
            _run([str(nssm), "remove", SERVICE_NAME, "confirm"], check=False)
        else:
            _run(["sc", "stop", SERVICE_NAME], check=False)
            _run(["sc", "delete", SERVICE_NAME], check=False)
    return not _windows_service_exists()


def cmd_install_service(args) -> int:
    """注册/更新 CherrySidecar 服务。Linux 无 NSSM，打印安装说明（可测分支逻辑）。"""
    if os.name == "nt":
        try:
            _install_service_windows()
        except RuntimeError as e:
            logger.error("注册失败: %s", e)
            return 2
        print(f"服务 {SERVICE_NAME} 已注册并启动")
        return 0
    # Linux/macOS：打印 systemd 说明（E-4 跨平台分支；本机仅验证逻辑）
    print("当前平台无 NSSM，跳过服务注册。")
    print(f"Linux 可手动安装 systemd unit：CherrySidecar 运行 `{sys.executable} run`")
    return 0


def cmd_uninstall_service(args) -> int:
    """停止并移除 CherrySidecar 服务。"""
    if os.name == "nt":
        ok = _uninstall_service_windows()
        print(f"服务 {SERVICE_NAME} 已{'移除' if ok else '移除失败'}")
        return 0 if ok else 1
    print("当前平台无 NSSM，无服务可卸载")
    return 0


def cmd_discover(args) -> int:
    """扫描局域网找受管服务端（批次H E）——UDP 广播 + 收集 ACK，打印候选。

    供 NSIS 安装弹窗 / F 配置工具调用。匹配 ACK 后逐行打印 `IP:port`。
    """
    cfg = _load_config(getattr(args, "config", None) or None)
    token = cfg.get("server", {}).get("token", "")
    dport = int(cfg.get("discovery", {}).get("port", 2335))
    timeout_ms = int(cfg.get("discovery", {}).get("timeout_ms", 2000))
    broadcast = getattr(args, "broadcast", "255.255.255.255")

    req = json.dumps({"type": "cherry-managed-discovery", "token": token}).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout_ms / 1000.0)
    found = []
    try:
        sock.sendto(req, (broadcast, dport))
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            try:
                msg = json.loads(data.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if msg.get("type") != "cherry-managed-discovery-ack":
                continue
            ip = msg.get("server_ip") or (addr[0] if addr else "")
            port = msg.get("port", 2334)
            found.append((ip, port, msg))
    finally:
        sock.close()

    if found:
        for ip, port, msg in found:
            print(f"{ip}:{port}")
            print(json.dumps(msg, ensure_ascii=False))
        return 0
    print("no-server-found")
    return 1


def cmd_set_server(args) -> int:
    """把服务端地址写进用户级 config（批次H F）——`sidecar.exe set-server <ip> [port]`。"""
    ip = getattr(args, "ip", None)
    port = int(getattr(args, "port", 2334) or 2334)
    if not ip:
        logger.error("set-server 需要 --ip")
        return 2
    # 用户级 config：存在则读，不存在则用内嵌生成
    user_cfg = _user_config_dir() / USER_CONFIG_FILE
    if not user_cfg.exists():
        embedded = _embedded_config()
        if not embedded.exists():
            logger.error("内嵌配置模板缺失: %s", embedded)
            return 2
        _user_config_dir().mkdir(parents=True, exist_ok=True)
        with open(embedded, encoding="utf-8") as src, open(user_cfg, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    with open(user_cfg, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("server", {})["url"] = f"ws://{ip}:{port}/ws"
    with open(user_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"server.url -> {ip}:{port}  ({user_cfg})")
    return 0


def cmd_first_run(args) -> int:
    """首启：生成/读 device_id + 落盘用户级 config + 注册服务。"""
    embedded = _embedded_config()
    if not embedded.exists():
        logger.error("内嵌配置模板缺失: %s", embedded)
        return 2
    # 用户级 config 始终作为落盘目标（非 _MEIPASS）；显式 --config 仅作模板优先源
    user_cfg = _user_config_dir() / USER_CONFIG_FILE
    explicit = getattr(args, "config", None) or None
    if explicit:
        # 显式配置作为落盘源：读取后原样落盘到用户级
        _user_config_dir().mkdir(parents=True, exist_ok=True)
        with open(explicit, encoding="utf-8") as src, open(user_cfg, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    elif not user_cfg.exists():
        # 无显式：用内嵌模板生成落盘（AC-E4-1/AC-E4-4 非 _MEIPASS）
        _user_config_dir().mkdir(parents=True, exist_ok=True)
        with open(embedded, encoding="utf-8") as src, open(user_cfg, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    # 生成/读 device_id 并写回用户级 config 的 device 段
    with open(user_cfg, encoding="utf-8") as f:
        cfg = json.load(f)
    dev = _load_or_create_device(cfg)
    cfg.setdefault("device", {}).update(dev)
    with open(user_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"device_id={dev['device_id']}")
    print(f"config    ={user_cfg}")
    print(f"device    ={_user_config_dir() / USER_DEVICE_FILE}")
    # 触发安装服务（跨平台分支）
    cmd_install_service(args)
    return 0


def main(argv: list[str] | None = None) -> int:
    """入口。argv 提供时 override sys.argv 以便测试直调。"""
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
    sp_first = sub.add_parser("first-run", help="首启：生成设备标识+落盘配置+注册服务")
    sp_first.add_argument("--config", default="")
    sub.add_parser("install-service", help="注册/更新服务")
    sub.add_parser("uninstall-service", help="停止并移除服务")
    sp_discover = sub.add_parser("discover", help="扫描局域网找受管服务端(批次H E)")
    sp_discover.add_argument("--config", default="")
    sp_discover.add_argument("--broadcast", default="255.255.255.255")
    sp_set = sub.add_parser("set-server", help="设置服务端地址并写用户级config(批次H F)")
    sp_set.add_argument("--ip", required=True)
    sp_set.add_argument("--port", default=2334)

    args = p.parse_args(argv)
    if args.cmd == "first-run":
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        return cmd_first_run(args)
    if args.cmd == "install-service":
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        return cmd_install_service(args)
    if args.cmd == "uninstall-service":
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        return cmd_uninstall_service(args)
    if args.cmd == "discover":
        return cmd_discover(args)
    if args.cmd == "set-server":
        return cmd_set_server(args)
    if args.cmd == "run":
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        cfg = _load_config(args.config or None)
        SidecarRunner(cfg).run()
        return 0
    fns = {"probe": cmd_probe, "agents": cmd_agents,
           "models": cmd_models, "deploy": cmd_deploy}
    return fns[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())




