"""
自愈模块 (S-9) — Sidecar 故障自恢复
=====================================
SDD §9 / 任务分解 S-9：Fork 升级失败回滚上一版；注入失败重跑；断线重连后补发未完成指令。

职责（构造注入 registry / fork，便于测试）：
  1. Fork 升级失败回滚上一版（备份恢复）
     - snapshot(scope)：对某目录/版本做快照（副本）
     - rollback(scope)：从最近快照恢复，回滚 Fork 升级失败
  2. 注入失败重试（injection retry）
     - track 记录派发结果；失败结果按重试策略重试（带最大次数 + 退避）
  3. 断线重连后补发未完成指令
     - 记录已下发未确认的 request_id（pending dispatch），重连后触发补发回调

对外入口：
  - check()         ：触发一次自愈检查，返回结构化结果
  - snapshot/rollback：备份与回滚
  - on_dispatch_result：派发结果登记（成功清 pending / 失败入重试）
  - on_reconnect() ：重连后补发 pending 指令
"""
from __future__ import annotations

import datetime
import logging
import shutil
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("sidecar.selfheal")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class SelfHealer:
    """Sidecar 自愈器。

    参数：
      registry : ManagedRegistry（受管旁路表，回滚后可能需重登受管态）
      fork     : ForkClient（读取 Fork 状态 / 升级探测）
      backup_root : 备份根目录（默认 <cwd>/data/backups）
      max_retry   : 注入失败最大重试次数
      retry_delay : 重试间隔（秒）
      resend_cb   : 回调，重连补发时调 resend_cb(list_of_messages) 由主循环重新发送
    """

    def __init__(self, registry=None, fork=None, backup_root=None,
                 max_retry: int = 3, retry_delay: float = 5.0,
                 resend_cb: Optional[Callable[[list[dict]], None]] = None):
        self.registry = registry
        self.fork = fork
        self.backup_root = Path(backup_root) if backup_root else Path("data/backups")
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.max_retry = max_retry
        self.retry_delay = retry_delay
        self.resend_cb = resend_cb

        # 已下发未确认的指令：request_id -> message
        self._pending: dict[str, dict] = {}
        # 注入失败重试记录：request_id -> {attempts, last_error, message, result}
        self._retries: dict[str, dict] = {}
        self._snapshots: dict[str, list[Path]] = {}  # scope -> [备份目录]

    # =========================================================
    # 3) 断线重连后补发未完成指令
    # =========================================================
    def track_pending(self, request_id: str, message: dict) -> None:
        """登记一条已下发待确认的指令（重连后据此补发）。"""
        self._pending[request_id] = message

    def on_dispatch_result(self, request_id: str, success: bool,
                           error: str | None = None) -> None:
        """派发结果登记。

        - success → 从 pending 移除（已确认）
        - 失败    → 进入重试队列（注入失败重跑），并保留 pending 待补发
        """
        if success:
            self._pending.pop(request_id, None)
            self._retries.pop(request_id, None)
            return
        # 失败：登记重试
        msg = self._pending.get(request_id)
        prev = self._retries.get(request_id, {})
        self._retries[request_id] = {
            "attempts": prev.get("attempts", 0) + 1,
            "last_error": error,
            "message": msg,
            "result": None,
        }

    def on_reconnect(self) -> dict:
        """重连后补发未完成指令。返回 {resent, requests}。"""
        if not self._pending:
            return {"resent": 0, "requests": []}
        msgs = list(self._pending.values())
        if self.resend_cb:
            try:
                self.resend_cb(msgs)
            except Exception as e:  # noqa: BLE001
                logger.exception("重连补发回调失败: %s", e)
        return {"resent": len(msgs), "requests": list(self._pending.keys())}

    # =========================================================
    # 2) 注入失败重试
    # =========================================================
    def retry_injections(self) -> dict:
        """重试注入失败项。返回 {retried, still_failed, succeeded}。"""
        retried = 0
        succeeded = 0
        still_failed = []
        for request_id, rec in list(self._retries.items()):
            if rec["attempts"] > self.max_retry:
                still_failed.append({
                    "request_id": request_id,
                    "attempts": rec["attempts"],
                    "last_error": rec["last_error"],
                    "gave_up": True,
                })
                continue
            # 重试：重新调用派发（由主循环注入重试执行器）
            if self.retry_cb:
                try:
                    result = self.retry_cb(request_id, rec.get("message"))
                    retried += 1
                    if result and result.get("success"):
                        succeeded += 1
                        self._retries.pop(request_id, None)
                        self._pending.pop(request_id, None)
                    else:
                        rec["attempts"] += 1
                        rec["last_error"] = (result or {}).get("error", "重试失败")
                except Exception as e:  # noqa: BLE001
                    logger.exception("重试注入 %s 异常", request_id)
                    rec["attempts"] += 1
                    rec["last_error"] = str(e)
            else:
                # 无重试执行器：仅登记，不实际重跑
                still_failed.append({
                    "request_id": request_id,
                    "attempts": rec["attempts"],
                    "last_error": rec["last_error"],
                })
        return {"retried": retried, "succeeded": succeeded,
                "still_failed": still_failed}

    # =========================================================
    # 1) Fork 升级失败回滚上一版（备份恢复）
    # =========================================================
    def snapshot(self, scope: str, target: Path | str) -> dict:
        """对某 scope（如 skills 目录 / fork 版本标记）做快照备份。

        target 为要备份的目录或文件。返回 {scope, backup, ok}。
        """
        target = Path(target)
        if not target.exists():
            return {"scope": scope, "backup": None, "ok": False,
                    "error": f"备份目标不存在: {target}"}
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backdir = self.backup_root / scope / ts
        backdir.mkdir(parents=True, exist_ok=True)
        try:
            if target.is_dir():
                # 仅备份子项（避免嵌套备份根目录自身）
                for child in target.iterdir():
                    dst = backdir / child.name
                    if child.is_dir():
                        shutil.copytree(child, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(child, dst)
            else:
                shutil.copy2(target, backdir / target.name)
        except Exception as e:  # noqa: BLE001
            logger.exception("快照失败 scope=%s", scope)
            return {"scope": scope, "backup": None, "ok": False, "error": str(e)}
        self._snapshots.setdefault(scope, []).append(backdir)
        logger.info("快照完成 scope=%s → %s", scope, backdir)
        return {"scope": scope, "backup": str(backdir), "ok": True}

    def rollback(self, scope: str, target: Path | str, index: int = -1) -> dict:
        """从最近快照（index=-1 为最近）恢复目标目录/文件。

        恢复前先做一次"坏版"快照，便于保留现场。返回 {scope, restored_from, ok}。
        """
        target = Path(target)
        snaps = self._snapshots.get(scope, [])
        if not snaps:
            return {"scope": scope, "restored_from": None, "ok": False,
                    "error": "无可用快照，无法回滚"}
        # 恢复前先备份当前（坏）版本
        self.snapshot(f"{scope}-bad", target)
        backup = snaps[index]
        if not backup.exists():
            return {"scope": scope, "restored_from": str(backup), "ok": False,
                    "error": f"快照缺失: {backup}"}
        try:
            # 清空目标后从快照复制
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            for child in backup.iterdir():
                dst = target / child.name
                if child.is_dir():
                    shutil.copytree(child, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(child, dst)
        except Exception as e:  # noqa: BLE001
            logger.exception("回滚失败 scope=%s", scope)
            return {"scope": scope, "restored_from": str(backup), "ok": False,
                    "error": str(e)}
        logger.info("回滚完成 scope=%s ← %s", scope, backup)
        return {"scope": scope, "restored_from": str(backup), "ok": True}

    # =========================================================
    # check() 入口
    # =========================================================
    def check(self, target: Path | str | None = None,
              scope: str = "default") -> dict:
        """触发一次自愈检查，返回结构化结果。

        - 检查待重试注入项 → 重试
        - 检查 pending 未确认指令 → 提示补发
        - 检查 Fork 升级态：若 fork 提供 probe_upgrade 且检测到"升级失败"，
          回滚上一版（target 需为可回滚目录）
        """
        result: dict = {
            "at": _now(),
            "retry": self.retry_injections(),
            "pending_resend": self.on_reconnect() if self._pending else
                              {"resent": 0, "requests": []},
            "rollback": None,
            "ok": True,
        }
        # Fork 升级失败回滚探测
        if self.fork is not None and hasattr(self.fork, "probe_upgrade"):
            try:
                up = self.fork.probe_upgrade()
            except Exception as e:  # noqa: BLE001
                up = {"error": str(e)}
            if up and up.get("upgrade_failed"):
                if target is None:
                    result["rollback"] = {
                        "skipped": True,
                        "reason": "检测到升级失败但未提供回滚 target",
                    }
                    result["ok"] = False
                else:
                    result["rollback"] = self.rollback(scope, target)
                    result["ok"] = bool(result["rollback"].get("ok"))
        return result

    # 供 retry_injections 使用的重试执行器（由主循环注入）
    retry_cb: Optional[Callable[[str, dict], dict]] = None

    def set_retry_cb(self, cb) -> None:
        """注入重试执行器：cb(request_id, message) -> dispatch_result。"""
        self.retry_cb = cb


def selfheal(registry=None, fork=None, **kw):
    """便捷工厂。"""
    return SelfHealer(registry, fork, **kw)
