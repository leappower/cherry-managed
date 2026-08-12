"""
派发执行器 (S-3/S-4/S-5) — Sidecar 收到服务端指令后真正执行
===============================================================
SDD §3.2/3.3/3.9 + §4.3 dispatch_log：
  - 接收服务端经 WS 下发的 dispatch_agent / dispatch_provider / dispatch_skills
  - 调用 CherryClient（官方 API）实际创建/更新/删除/禁用 Agent / Provider
  - 成功后经 ManagedRegistry 登记受管标记（mark_managed），回收时 unmark
  - request_id 幂等：同 request_id 不重复执行（内存集合 + dispatch_log 持久化）
  - 每个处理函数返回结构化结果，供 sidecar.py 组装 dispatch_result 上报服务端

受管标记语义（S-8 旁路表）：
  - dispatch_agent create/update → mark_managed('agent', id)；delete/disable → unmark
  - dispatch_provider add/update → mark_managed('provider', id)；remove → unmark
  - dispatch_skills install → mark_managed('skill', ...)
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("sidecar.dispatch")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class DispatchExecutor:
    """Sidecar 派发执行器。

    依赖（构造注入，便于测试）：
      - cherry: CherryClient（官方 API）
      - registry: ManagedRegistry（受管旁路表）
      - deploy_dir: 部署目录（skills/agents 落盘根目录）
      - log_path: dispatch_log 持久化文件（request_id 幂等）
    """

    def __init__(self, cherry, registry, deploy_dir: Path | str,
                 log_path: Path | str | None = None,
                 skills_dir: Path | str | None = None):
        self.cherry = cherry
        self.registry = registry
        self.deploy_dir = Path(deploy_dir)
        self.deploy_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir = Path(skills_dir) if skills_dir else (self.deploy_dir / "skills")
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_path) if log_path else None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # 线程安全：幂等集合用锁保护
        self._lock = threading.Lock()
        self._seen_request_ids: set[str] = set()
        self._load_log()

    # ---- dispatch_log 持久化（request_id 幂等）----
    def _load_log(self) -> None:
        """从磁盘恢复已处理的 request_id（进程重启后仍幂等）。"""
        if self.log_path is None or not self.log_path.exists():
            return
        try:
            with open(self.log_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        rid = rec.get("request_id")
                        if rid:
                            self._seen_request_ids.add(rid)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            logger.warning("读取 dispatch_log 失败: %s", self.log_path)

    def _persist_log(self, request_id: str, kind: str, action: str,
                     success: bool, agent_id: str | None = None,
                     error: str | None = None) -> None:
        """追加一条 dispatch_log。"""
        if self.log_path is None:
            return
        rec = {
            "request_id": request_id,
            "kind": kind,
            "action": action,
            "agent_id": agent_id,
            "success": success,
            "error": error,
            "at": _now(),
        }
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("写 dispatch_log 失败: %s", self.log_path)

    def _check_idempotent(self, request_id: str) -> bool:
        """请求幂等检查。返回 True 表示已处理过（应跳过）。"""
        with self._lock:
            if request_id in self._seen_request_ids:
                return True
            self._seen_request_ids.add(request_id)
            return False

    # ---- 通用结果 ----
    def _result(self, success: bool, action: str, kind: str, agent_id: str | None,
                error: str | None = None, **extra) -> dict:
        return {
            "success": success,
            "action": action,
            "kind": kind,
            "agent_id": agent_id,
            "error": error,
            **extra,
        }

    # =========================================================
    # S-3: dispatch_agent
    # =========================================================
    def handle_dispatch_agent(self, action: str, agent: dict,
                              package_url: Optional[str] = None,
                              request_id: str | None = None) -> dict:
        """执行 Agent 派发（create/update/delete/disable）。

        action:
          - create : create_agent → mark_managed('agent', id)
          - update : patch_agent/put_agent → mark_managed('agent', id)
          - delete : delete_agent → unmark('agent', id)
          - disable: 标记禁用（官方无 disable API 时落盘禁用标记）→ unmark 视策略

        返回 {success, action, kind, agent_id, error, idempotent}
        """
        if request_id and self._check_idempotent(request_id):
            logger.info("幂等跳过: request_id=%s", request_id)
            return self._result(True, action, "agent", agent.get("id"),
                                idempotent=True)
        try:
            if action == "create":
                payload = dict(agent)
                if package_url:
                    payload.setdefault("package_url", package_url)
                resp = self.cherry.create_agent(payload)
                agent_id = self._extract_id(resp, agent)
                self.registry.mark_managed("agent", agent_id)
                return self._result(True, action, "agent", agent_id,
                                    idempotent=False, **self._log(request_id, "agent", action, agent_id))

            if action == "update":
                agent_id = self._resolve_id(agent)
                payload = dict(agent)
                # 全量更新优先 put，带 package_url 走 put
                if package_url:
                    payload["package_url"] = package_url
                    resp = self.cherry.put_agent(agent_id, payload)
                else:
                    resp = self.cherry.patch_agent(agent_id, payload)
                self.registry.mark_managed("agent", agent_id)
                return self._result(True, action, "agent", agent_id,
                                    idempotent=False, **self._log(request_id, "agent", action, agent_id))

            if action == "delete":
                agent_id = self._resolve_id(agent)
                self.cherry.delete_agent(agent_id)
                self.registry.unmark("agent", agent_id)
                return self._result(True, action, "agent", agent_id,
                                    idempotent=False, **self._log(request_id, "agent", action, agent_id))

            if action == "disable":
                agent_id = self._resolve_id(agent)
                # 官方无禁用 API：落盘禁用标记（deploy_dir/disabled/<id>.json）
                self._mark_disabled(agent_id, agent)
                # 禁用视作回收受管保护（隐藏，避免误删）
                self.registry.unmark("agent", agent_id)
                return self._result(True, action, "agent", agent_id,
                                    idempotent=False, **self._log(request_id, "agent", action, agent_id))

            return self._result(False, action, "agent", agent.get("id"),
                                error=f"未知 action: {action}", idempotent=False,
                                **self._log(request_id, "agent", action, None, f"未知 action: {action}"))
        except Exception as e:  # noqa: BLE001
            logger.exception("dispatch_agent %s 失败", action)
            return self._result(False, action, "agent", agent.get("id"),
                                error=str(e), idempotent=False,
                                **self._log(request_id, "agent", action, None, str(e)))

    # =========================================================
    # S-4: dispatch_provider
    # =========================================================
    def handle_dispatch_provider(self, action: str, provider: dict,
                                 request_id: str | None = None) -> dict:
        """执行 Provider 派发（add/update/remove）。

        官方 /v1/providers 不存在(404)，模型统一管控走 Fork 或直写数据目录。
        本实现：
          - add/update → 落盘 provider 配置（deploy_dir/providers/<id>.json）+ mark_managed
          - remove      → 删除落盘配置 + unmark
        """
        if request_id and self._check_idempotent(request_id):
            return self._result(True, action, "provider", provider.get("id"),
                                idempotent=True)
        provider_id = provider.get("id") or provider.get("name", "")
        try:
            prov_dir = self.deploy_dir / "providers"
            prov_dir.mkdir(parents=True, exist_ok=True)
            if action in ("add", "update"):
                path = prov_dir / f"{provider_id}.json"
                with open(path, "w") as f:
                    json.dump(provider, f, ensure_ascii=False, indent=2)
                self.registry.mark_managed("provider", provider_id)
                return self._result(True, action, "provider", provider_id,
                                    idempotent=False, **self._log(request_id, "provider", action, provider_id))
            if action == "remove":
                path = prov_dir / f"{provider_id}.json"
                if path.exists():
                    path.unlink()
                self.registry.unmark("provider", provider_id)
                return self._result(True, action, "provider", provider_id,
                                    idempotent=False, **self._log(request_id, "provider", action, provider_id))
            return self._result(False, action, "provider", provider_id,
                                error=f"未知 action: {action}", idempotent=False,
                                **self._log(request_id, "provider", action, None, f"未知 action: {action}"))
        except Exception as e:  # noqa: BLE001
            logger.exception("dispatch_provider %s 失败", action)
            return self._result(False, action, "provider", provider_id,
                                error=str(e), idempotent=False,
                                **self._log(request_id, "provider", action, None, str(e)))

    # =========================================================
    # S-5: dispatch_skills
    # =========================================================
    def handle_dispatch_skills(self, skills: list,
                               request_id: str | None = None) -> dict:
        """安装 SKILLS：写部署目录 + agent_skill 记录，mark_managed('skill', ...)。

        skills: [{id/name, content/package_url, version}]
        返回 {success, installed:[...], errors:[...], idempotent}
        """
        if request_id and self._check_idempotent(request_id):
            return {"success": True, "action": "sync", "kind": "skills",
                    "idempotent": True, "installed": [], "errors": []}
        installed: list[dict] = []
        errors: list[dict] = []
        for sk in skills:
            skill_id = sk.get("id") or sk.get("name", "")
            try:
                path = self.skills_dir / f"{skill_id}.json"
                with open(path, "w") as f:
                    json.dump(sk, f, ensure_ascii=False, indent=2)
                self.registry.mark_managed("skill", skill_id)
                installed.append({"id": skill_id, "name": sk.get("name", skill_id),
                                  "version": sk.get("version")})
            except Exception as e:  # noqa: BLE001
                errors.append({"id": skill_id, "error": str(e)})
                logger.exception("安装 skill %s 失败", skill_id)
        success = not errors
        self._log(request_id, "skills", "sync", None, None if success else "部分失败")
        return {"success": success, "action": "sync", "kind": "skills",
                "idempotent": False, "installed": installed, "errors": errors}

    # =========================================================
    # 辅助方法
    # =========================================================
    def _log(self, request_id, kind, action, agent_id, error=None) -> dict:
        if request_id:
            self._persist_log(request_id, kind, action, error is None, agent_id, error)
        return {"log": True}

    def _extract_id(self, resp: Any, agent: dict) -> str:
        """从创建响应或 agent 字典提取 id。"""
        if isinstance(resp, dict):
            rid = resp.get("id") or (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else resp.get("id")
            if rid:
                return rid
        return agent.get("id") or agent.get("name", "")

    def _resolve_id(self, agent: dict) -> str:
        """从 agent 字典解析 id（含 data 包裹兼容）。"""
        return agent.get("id") or (agent.get("data") or {}).get("id", "") if isinstance(agent.get("data"), dict) else agent.get("id", "")

    def _mark_disabled(self, agent_id: str, agent: dict) -> None:
        """落盘禁用标记。"""
        disabled_dir = self.deploy_dir / "disabled"
        disabled_dir.mkdir(parents=True, exist_ok=True)
        with open(disabled_dir / f"{agent_id}.json", "w") as f:
            json.dump({"agent_id": agent_id, "disabled": True, "agent": agent,
                       "at": _now()}, f, ensure_ascii=False, indent=2)
